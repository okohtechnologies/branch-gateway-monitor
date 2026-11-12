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


# --- retention ---------------------------------------------------------------------------


async def seed_checks(db, branch_id, ages_days):
    from datetime import datetime, timedelta, timezone

    from ipmon.engine import Status

    now = datetime.now(timezone.utc)
    for d in ages_days:
        await db.insert_check(branch_id, GOOD, Status.UP, now - timedelta(days=d))
        await db.insert_check(None, GOOD, Status.UP, now - timedelta(days=d))  # canary row


async def test_prune_deletes_only_expired_checks(setup):
    m, n, script, db, a = setup
    await seed_checks(db, a, [1, 10, 29, 31, 45, 90])
    removed = await db.prune_checks(30)
    assert removed == 6  # 3 expired ages x (branch row + canary row)
    ages = await db.pool.fetchval("select count(*) from checks")
    assert ages == 6


async def test_prune_handles_backlog_larger_than_one_batch(setup):
    m, n, script, db, a = setup
    await seed_checks(db, a, [60] * 25)
    assert await db.prune_checks(30, batch=10) == 50
    assert await db.pool.fetchval("select count(*) from checks") == 0


async def test_prune_keeps_incidents_and_notifications(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    await db.pool.execute("UPDATE checks SET ts = now() - interval '90 days'")
    await db.prune_checks(30)
    assert await db.pool.fetchval("select count(*) from checks") == 0
    assert await db.pool.fetchval("select count(*) from incidents") == 1
    assert await db.pool.fetchval("select count(*) from notifications") == 1


async def test_monitor_prunes_at_start_then_waits(setup):
    m, n, script, db, a = setup
    await seed_checks(db, a, [60])
    assert await m.maybe_prune() == 2
    await seed_checks(db, a, [60])
    assert await m.maybe_prune() == 0  # too soon: not run again for a few hours
    m._last_prune -= m.PRUNE_EVERY_S + 1
    assert await m.maybe_prune() == 2


async def test_retention_zero_keeps_everything(db):
    m = Monitor(db, FakeNotifier(), probe_fn=Script(), retention_days=None)
    a = await db.upsert_branch("HQ", "10.0.0.1")
    await seed_checks(db, a, [400])
    assert await m.maybe_prune() == 0
    assert await db.pool.fetchval("select count(*) from checks") == 2


# --- undelivered alerts are queued and retried -------------------------------------------------


async def make_due(db):
    await db.pool.execute("UPDATE notifications SET next_attempt_at = now() - interval '1 second' WHERE NOT success AND body IS NOT NULL")


async def test_failed_alert_is_queued_with_its_text(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)
    row = await db.pool.fetchrow("SELECT success, body, buttons, attempts, next_attempt_at > now() AS later FROM notifications")
    assert row["success"] is False and row["body"].startswith("🔴 DOWN: HQ")
    assert row["buttons"] is not None and row["attempts"] == 0 and row["later"] is True
    assert await db.pending_alert_count() == 1 and m.alerts_pending == 1


async def test_retry_waits_until_due_then_delivers_marked_as_delayed(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)
    sent_before = len(n.sent)
    n.ok = True
    await cycles(m, 2)
    assert len(n.sent) == sent_before  # not due yet: no early retry
    await make_due(db)
    await cycles(m, 1)
    assert len(n.sent) == sent_before + 1
    assert n.sent[-1].startswith("⏱ DELAYED ALERT") and "🔴 DOWN: HQ" in n.sent[-1]
    row = await db.pool.fetchrow("SELECT success, delivered_at, error FROM notifications")
    assert row["success"] is True and row["delivered_at"] is not None and row["error"] is None
    assert await db.pending_alert_count() == 0 and m.alerts_pending == 0
    await make_due(db)
    await cycles(m, 2)
    assert len(n.sent) == sent_before + 1  # delivered once, never again


async def test_failed_retry_backs_off_and_stops_after_the_first_failure(setup):
    m, n, script, db, a = setup
    b = await db.upsert_branch("Depot", "10.0.0.2")
    await cycles(m, 3)
    n.ok = False
    script.state.update({a: DEAD, b: DEAD})  # canary is fine, so two per-branch alerts, both failing
    await cycles(m, 3)
    assert await db.pending_alert_count() == 2
    await make_due(db)
    attempts_before = len(n.sent)
    await cycles(m, 1)
    assert len(n.sent) == attempts_before + 1  # one try, then it stops: the rest would fail too
    rows = await db.pool.fetch("SELECT attempts, next_attempt_at > now() + interval '90 seconds' AS backed_off FROM notifications ORDER BY id")
    assert [r["attempts"] for r in rows] == [1, 0]
    assert rows[0]["backed_off"] is True  # next try is at least ~2 minutes away


async def test_queued_alerts_arrive_in_their_original_order(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)  # DOWN fails
    script.state[a] = GOOD
    await cycles(m, 3)  # RECOVERED fails
    assert await db.pending_alert_count() == 2
    n.ok = True
    await make_due(db)
    start = len(n.sent)
    await cycles(m, 1)
    delivered = n.sent[start:]
    assert "DOWN: HQ" in delivered[0] and "RECOVERED: HQ" in delivered[1]


async def test_alert_older_than_a_day_is_given_up(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)
    n.ok = True
    await db.pool.execute("UPDATE notifications SET sent_at = now() - interval '25 hours'")
    await make_due(db)
    start = len(n.sent)
    await cycles(m, 1)
    assert len(n.sent) == start  # nothing sent: a day-old outage alert would only confuse
    assert await db.pool.fetchval("SELECT gave_up FROM notifications") is True
    assert await db.pending_alert_count() == 0


async def test_reminders_and_muted_alerts_are_never_queued(setup):
    m, n, script, db, a = setup
    await go_down(m, script, a)
    n.ok = False
    await backdate(db, 31)
    await cycles(m, 1)  # a reminder is due and fails
    assert any(t.startswith("⏰") for t in n.sent)
    assert await db.pending_alert_count() == 0  # the next reminder will follow anyway
    b = await db.upsert_branch("Depot", "10.0.0.2")
    await cycles(m, 3)
    await db.mute_branch(b, 60)
    script.state[b] = DEAD
    await cycles(m, 3)
    assert await db.pending_alert_count() == 0  # muted: suppressed, not queued


async def test_buttons_kept_while_incident_is_open_and_dropped_after_it_closes(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)
    n.ok = True
    await make_due(db)
    await cycles(m, 1)  # incident still open
    assert n.buttons[-1] is not None and n.buttons[-1][0][0][0] == "✅ Acknowledge"

    n.ok = False
    script.state[a] = GOOD
    await cycles(m, 3)  # RECOVERED fails
    n.ok = False
    # queue another DOWN-style alert for the now-closed incident
    iid = await db.pool.fetchval("SELECT id FROM incidents ORDER BY id DESC LIMIT 1")
    await db.record_notification(iid, "DOWN", "telegram", False, "x", body="late", buttons=[[["✅ Acknowledge", f"ack:{iid}"]]])
    n.ok = True
    await make_due(db)
    await cycles(m, 1)
    late = [i for i, t in enumerate(n.sent) if "late" in t][-1]
    assert n.buttons[late] is None  # incident closed: an Acknowledge button would be useless


async def test_queue_survives_a_restart(setup):
    m, n, script, db, a = setup
    await cycles(m, 3)
    n.ok = False
    script.state[a] = DEAD
    await cycles(m, 3)
    n2 = FakeNotifier()
    m2 = Monitor(db, n2, confirm=3, canary=CANARY, probe_fn=script)  # a fresh process
    await m2.restore_state()
    await make_due(db)
    await cycles(m2, 1)
    assert any(t.startswith("⏱ DELAYED ALERT") for t in n2.sent)
    assert await db.pending_alert_count() == 0
