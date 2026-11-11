import pytest
import pytest_asyncio

from ipmon.engine import ProbeResult
from ipmon.probe import Target
from ipmon.runner import Monitor

GOOD = ProbeResult(20.0, 0.0)
DEAD = ProbeResult(None, 100.0)
CANARY = Target(None, "1.1.1.1", "tcp", 443)


class FakeNotifier:
    def __init__(self, ok=True):
        self.sent, self.buttons, self.ok = [], [], ok

    async def send(self, text, buttons=None):
        self.sent.append(text)
        self.buttons.append(buttons)
        return (True, None) if self.ok else (False, "boom")


class Script:
    """Probe stand-in: ``state[key]`` gives the result each target should return."""

    def __init__(self):
        self.state = {}

    async def __call__(self, targets, count, timeout):
        return {t.key: self.state.get(t.key, GOOD) for t in targets}


@pytest_asyncio.fixture
async def setup(db):
    n, script = FakeNotifier(), Script()
    m = Monitor(db, n, confirm=3, canary=CANARY, probe_fn=script)
    a = await db.upsert_branch("HQ", "10.0.0.1")
    return m, n, script, db, a


async def cycles(m, k):
    out = []
    for _ in range(k):
        out += await m.run_cycle()
    return out


async def test_outage_opens_incident_and_alerts_once_then_recovers(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)  # baseline UP
    script.state[a] = DEAD
    await cycles(m, 2)
    assert n.sent == [] and await db.open_incident_id(a) is None  # debounced
    await cycles(m, 1)
    assert len(n.sent) == 1 and n.sent[0].startswith("🔴 DOWN: HQ")
    assert await db.open_incident_id(a) is not None
    await cycles(m, 5)
    assert len(n.sent) == 1  # no repeat alerts while still down
    script.state[a] = GOOD
    await cycles(m, 3)
    assert len(n.sent) == 2 and n.sent[1].startswith("🟢 RECOVERED: HQ")
    assert await db.open_incident_id(a) is None


async def test_checks_and_notifications_are_stored(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    script.state[a] = DEAD
    await cycles(m, 3)
    rows = await db.pool.fetch("SELECT status FROM checks WHERE branch_id=$1 ORDER BY id", a)
    assert [r["status"] for r in rows] == ["UP"] * 3 + ["DOWN"] * 3
    assert await db.pool.fetchval("SELECT count(*) FROM checks WHERE branch_id IS NULL") == 6  # canary
    notes = await db.pool.fetch("SELECT kind, success FROM notifications")
    assert [(r["kind"], r["success"]) for r in notes] == [("DOWN", True)]


async def test_failed_notification_is_recorded(db):
    n, script = FakeNotifier(ok=False), Script()
    m = Monitor(db, n, confirm=1, probe_fn=script)
    a = await db.upsert_branch("HQ", "10.0.0.1")
    await cycles(m, 1)
    script.state[a] = DEAD
    await cycles(m, 1)
    row = await db.pool.fetchrow("SELECT success, error FROM notifications")
    assert (row["success"], row["error"]) == (False, "boom")


async def test_mass_failure_sends_single_monitor_alert(setup):
    m, n, script, db, a = setup
    b = await db.upsert_branch("Depot", "10.0.0.2")
    await cycles(m, 3)
    script.state.update({a: DEAD, b: DEAD, None: DEAD})
    await cycles(m, 3)
    assert len(n.sent) == 1 and "MONITOR MAY BE OFFLINE" in n.sent[0]
    assert await db.open_incident_id(a) is None and await db.open_incident_id(b) is None


async def test_new_branch_is_probed_without_restart(setup):
    m, n, script, db, a = setup
    await m.run_cycle()
    b = await db.upsert_branch("Depot", "10.0.0.2")
    await m.run_cycle()
    assert await db.pool.fetchval("SELECT count(*) FROM checks WHERE branch_id=$1", b) == 1


async def test_disabled_branch_stops_being_probed(setup):
    m, n, script, db, a = setup
    await m.run_cycle()
    await db.upsert_branch("HQ", "10.0.0.1", enabled=False)
    await m.run_cycle()
    assert await db.pool.fetchval("SELECT count(*) FROM checks WHERE branch_id=$1", a) == 1
    assert a not in m.latest


async def test_restart_does_not_forget_open_incident(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    script.state[a] = DEAD
    await cycles(m, 3)
    n2 = FakeNotifier()
    m2 = Monitor(db, n2, confirm=3, canary=CANARY, probe_fn=script)  # simulated restart
    await m2.restore_state()
    await cycles(m2, 3)
    assert n2.sent == []  # still down: no duplicate DOWN alert
    script.state[a] = GOOD
    await cycles(m2, 3)
    assert len(n2.sent) == 1 and n2.sent[0].startswith("🟢 RECOVERED")


async def test_subscribers_receive_cycle_messages(setup):
    m, n, script, db, a = setup
    q = m.subscribe()
    await m.run_cycle()
    msg = q.get_nowait()
    assert msg["type"] == "cycle" and msg["branches"][0]["name"] == "HQ"
    m.unsubscribe(q)
    await m.run_cycle()
    assert q.empty()


async def test_slow_subscriber_never_blocks(setup):
    m, n, script, db, a = setup
    q = m.subscribe()
    for _ in range(60):
        await m.run_cycle()  # queue holds 50; overflow drops the oldest
    assert q.qsize() == 50


# --- buttons, mutes, acknowledgement, reminders -------------------------------------


async def go_down(m, script, a):
    await cycles(m, 3)
    script.state[a] = DEAD
    await cycles(m, 3)


async def backdate(db, minutes):
    await db.pool.execute("UPDATE notifications SET sent_at = now() - make_interval(mins => $1)", minutes)
    await db.pool.execute("UPDATE incidents SET started_at = now() - make_interval(mins => $1)", minutes)


async def test_down_alert_has_ack_and_mute_buttons(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    iid = await db.open_incident_id(a)
    assert n.buttons[0] == [[("✅ Acknowledge", f"ack:{iid}"), ("🔕 Mute 1h", f"mute:{a}:60")]]


async def test_recovery_has_no_buttons(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    script.state[a] = GOOD
    await cycles(m, 3)
    assert n.buttons[-1] is None


async def test_mute_suppresses_alerts_but_probing_and_incidents_continue(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    await db.mute_branch(a, 60, "tester")
    script.state[a] = DEAD
    await cycles(m, 3)
    assert n.sent == []
    assert await db.open_incident_id(a) is not None
    assert await db.pool.fetchval("SELECT count(*) FROM checks WHERE branch_id=$1 AND status='DOWN'", a) == 3
    row = await db.pool.fetchrow("SELECT suppressed, success FROM notifications")
    assert (row["suppressed"], row["success"]) == (True, True)


async def test_alerts_resume_after_unmute(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    await db.mute_branch(a, 60)
    assert await db.unmute_branch(a) is True
    script.state[a] = DEAD
    await cycles(m, 3)
    assert len(n.sent) == 1


async def test_reminder_sent_after_interval_then_not_again(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    await cycles(m, 2)
    assert len(n.sent) == 1  # too soon
    await backdate(db, 31)
    await cycles(m, 1)
    assert len(n.sent) == 2 and n.sent[1].startswith("⏰ STILL DOWN: HQ") and "31m" in n.sent[1]
    assert n.buttons[1] is not None  # reminders can be acknowledged too
    await cycles(m, 3)
    assert len(n.sent) == 2  # next one is another 30 minutes away


async def test_acknowledged_incident_gets_no_reminders(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    iid = await db.open_incident_id(a)
    assert await db.acknowledge_incident(iid, "Alex") == "acknowledged"
    await backdate(db, 45)
    await cycles(m, 2)
    assert len(n.sent) == 1


async def test_muted_incident_gets_no_reminders(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    await db.mute_branch(a, 120)
    await backdate(db, 45)
    await cycles(m, 2)
    assert len(n.sent) == 1


async def test_reminders_can_be_disabled(db):
    n, script = FakeNotifier(), Script()
    m = Monitor(db, n, confirm=3, probe_fn=script, reminder_minutes=None)
    a = await db.upsert_branch("HQ", "10.0.0.1")
    await go_down(m, script, a)
    await backdate(db, 600)
    await cycles(m, 2)
    assert len(n.sent) == 1


async def test_recovered_incident_gets_no_reminder(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    script.state[a] = GOOD
    await cycles(m, 3)
    await backdate(db, 90)
    await cycles(m, 1)
    assert not any(t.startswith("⏰") for t in n.sent)


async def test_acknowledge_states(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    iid = await db.open_incident_id(a)
    assert await db.acknowledge_incident(iid, "Alex") == "acknowledged"
    assert await db.acknowledge_incident(iid, "Sam") == "already"
    row = await db.pool.fetchrow("SELECT acknowledged_by, acknowledged_at FROM incidents WHERE id=$1", iid)
    assert row["acknowledged_by"] == "Alex" and row["acknowledged_at"] is not None  # first one wins
    assert await db.acknowledge_incident(99999, "Alex") == "missing"
    script.state[a] = GOOD
    await cycles(m, 3)
    assert await db.acknowledge_incident(iid, "Alex") == "closed"  # recovered since


async def test_mute_replaces_previous_and_expires(setup):
    m, n, script, db, a = setup
    first = await db.mute_branch(a, 10)
    second = await db.mute_branch(a, 120)
    assert second > first
    assert await db.pool.fetchval("SELECT count(*) FROM mutes WHERE until > now()") == 1
    await db.pool.execute("UPDATE mutes SET until = now() - interval '1 second'")
    assert await db.muted_until(a) is None
    assert await db.unmute_branch(a) is False
