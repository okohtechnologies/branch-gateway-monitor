from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from ipmon import auth
from ipmon.api import create_app
from ipmon.engine import ProbeResult, Status
from ipmon.report import MAX_LINES, BranchReport, Report, build_report, format_report, last_due
from ipmon.runner import Monitor
from tests.test_api import Script, login, make_settings

UTC = timezone.utc
GOOD = ProbeResult(20.0, 0.0)
MON = datetime(2026, 11, 16, 8, 5, tzinfo=UTC)  # a Monday, just after 08:00


class Recorder:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    async def send(self, text, buttons=None):
        self.sent.append(text)
        return self.ok, None if self.ok else "boom"


# --- schedule ------------------------------------------------------------------


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 11, 10, 0, tzinfo=UTC), datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),    # Wednesday
    (datetime(2026, 11, 16, 8, 0, tzinfo=UTC), datetime(2026, 11, 16, 8, 0, tzinfo=UTC)),    # exactly on time
    (datetime(2026, 11, 16, 7, 59, tzinfo=UTC), datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),    # a minute early
    (datetime(2026, 11, 15, 23, 59, tzinfo=UTC), datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),   # Sunday night
    (datetime(2026, 11, 16, 23, 0, tzinfo=UTC), datetime(2026, 11, 16, 8, 0, tzinfo=UTC)),   # Monday evening
])
def test_last_due(now, expected):
    assert last_due(now, UTC) == expected


def test_last_due_follows_the_local_zone():
    lagos = timezone(timedelta(hours=1))
    # 07:30 UTC on Monday is 08:30 in Lagos, so that Monday's report is already due there.
    assert last_due(datetime(2026, 11, 16, 7, 30, tzinfo=UTC), lagos) == datetime(2026, 11, 16, 8, 0, tzinfo=lagos)


# --- the numbers -----------------------------------------------------------------


async def seed(db, now):
    a = await db.upsert_branch("Alpha", "10.0.0.1")
    b = await db.upsert_branch("Bravo", "10.0.0.2")
    c = await db.upsert_branch("Charlie", "10.0.0.3")
    for i in range(100):  # Alpha: 90 up, 10 down
        await db.insert_check(a, GOOD, Status.UP if i >= 10 else Status.DOWN, now - timedelta(hours=i + 1))
        await db.insert_check(b, GOOD, Status.UP, now - timedelta(hours=i + 1))
    await db.insert_check(a, GOOD, Status.UP, now - timedelta(days=10))  # outside the week: ignored
    await db.insert_check(None, GOOD, Status.UP, now - timedelta(hours=1))  # canary: ignored
    return a, b, c


async def test_report_numbers(db):
    a, b, c = await seed(db, MON)
    # Alpha: one 2h outage inside the week, and one that started before the window (only 1 day counts).
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at, ended_at) VALUES ($1,'DOWN',$2,$3)",
                          a, MON - timedelta(days=3), MON - timedelta(days=3) + timedelta(hours=2))
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at, ended_at) VALUES ($1,'DOWN',$2,$3)",
                          a, MON - timedelta(days=8), MON - timedelta(days=6))
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at, ended_at) VALUES ($1,'DEGRADED',$2,$3)",
                          a, MON - timedelta(days=2), MON - timedelta(days=2) + timedelta(minutes=30))
    # Charlie has no checks but an incident that is still open.
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at) VALUES ($1,'DOWN',$2)",
                          c, MON - timedelta(hours=5))
    # An incident from long ago must not appear at all.
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at, ended_at) VALUES ($1,'DOWN',$2,$3)",
                          b, MON - timedelta(days=30), MON - timedelta(days=29))

    r = await build_report(db, MON)
    by = {x.name: x for x in r.branches}
    assert by["Alpha"].checks == 100 and by["Alpha"].uptime_pct == 90.0
    assert by["Alpha"].incidents == 3
    assert by["Alpha"].down_s == 2 * 3600 + 24 * 3600  # the 2h outage + the day of the old one inside the window
    assert by["Alpha"].longest_down_s == 24 * 3600
    assert by["Alpha"].degraded_s == 1800
    assert not by["Alpha"].down_now
    assert by["Bravo"].uptime_pct == 100.0 and by["Bravo"].incidents == 0 and by["Bravo"].down_s == 0
    assert by["Charlie"].uptime_pct is None and by["Charlie"].down_now and by["Charlie"].down_s == 5 * 3600
    assert r.overall_pct == pytest.approx(95.0)  # 190 of 200 checks


async def test_disabled_branches_are_left_out(db):
    await db.upsert_branch("Off", "10.0.0.9", enabled=False)
    assert (await build_report(db, MON)).branches == []


# --- the message -----------------------------------------------------------------


def branch(name, pct, **kw):
    base = dict(checks=100, incidents=0, down_s=0, degraded_s=0, longest_down_s=0, down_now=False)
    return BranchReport(name, uptime_pct=pct, **{**base, **kw})


def test_message_lists_worst_first_and_explains_each_branch():
    r = Report(MON - timedelta(days=7), MON, [
        branch("Good", 100.0),
        branch("Rough", 97.5, incidents=2, down_s=7500, longest_down_s=6000, degraded_s=600),
        branch("New", None, checks=0),
        branch("Dead", 80.0, incidents=1, down_s=3600, longest_down_s=3600, down_now=True),
    ])
    text = format_report(r, UTC)
    assert text.startswith("📊 Weekly report\nMon 09 Nov – Mon 16 Nov")
    assert "Overall: 92.5% uptime · 3 incidents · 3h 5m total downtime" in text
    lines = text.splitlines()
    body = [ln for ln in lines if ln and ln[0] in "🟢🟠🔴⚪"]
    assert [ln.split(":")[0][2:] for ln in body] == ["Dead", "Rough", "Good", "New"]
    assert "🔴 Dead: 80% · 1 incident · 1h 0m down (longest 1h 0m) · DOWN NOW" in text
    assert "🟠 Rough" not in text and "🔴 Rough: 97.5% · 2 incidents · 2h 5m down (longest 1h 40m) · 10m degraded" in text
    assert "🟢 Good: 100% · no incidents" in text
    assert "⚪ New: no data this week" in text


def test_message_with_no_branches():
    assert "No branches are being monitored." in format_report(Report(MON - timedelta(days=7), MON, []), UTC)


def test_long_lists_are_capped_inside_the_telegram_limit():
    r = Report(MON - timedelta(days=7), MON, [branch(f"Branch with a fairly long name number {i}", 99.0 + i / 1000, incidents=1, down_s=999, longest_down_s=999) for i in range(300)])
    text = format_report(r, UTC)
    assert len(text) < 4096
    assert text.count("\n🟠") + text.count("\n🔴") == MAX_LINES
    assert f"…and {300 - MAX_LINES} more branches" in text


# --- sending on schedule -----------------------------------------------------------


@pytest_asyncio.fixture
async def mon(db):
    rec = Recorder()
    m = Monitor(db, rec, confirm=3, probe_fn=Script())
    m.rec = rec
    await db.upsert_branch("HQ", "10.0.0.1")
    return m


async def mark_sent(db, when):
    await db.pool.execute("UPDATE notifications SET sent_at = $1 WHERE kind = 'WEEKLY_REPORT'", when)


async def test_fresh_install_waits_for_the_next_scheduled_time(mon):
    mon._started_at = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)  # Wednesday
    assert not await mon.maybe_send_report(datetime(2026, 11, 14, 12, 0, tzinfo=UTC))
    assert mon.rec.sent == []
    assert await mon.maybe_send_report(MON)  # Monday 08:05
    assert len(mon.rec.sent) == 1 and "Weekly report" in mon.rec.sent[0]


async def test_sent_once_per_week_even_across_restarts(db, mon):
    mon._started_at = datetime(2026, 11, 11, tzinfo=UTC)
    assert await mon.maybe_send_report(MON)
    await mark_sent(db, MON)
    assert not await mon.maybe_send_report(MON + timedelta(minutes=1))
    assert not await mon.maybe_send_report(MON + timedelta(days=3))
    # A "restarted" monitor (new object, later start time) reads the same log and also stays quiet.
    again = Monitor(db, mon.rec, probe_fn=Script())
    again._started_at = MON + timedelta(hours=1)
    assert not await again.maybe_send_report(MON + timedelta(hours=2))
    # ...and the next Monday it sends again.
    assert await mon.maybe_send_report(MON + timedelta(days=7))
    assert len(mon.rec.sent) == 2


async def test_missed_report_is_sent_when_the_monitor_comes_back(db, mon):
    await db.pool.execute("INSERT INTO notifications (kind, channel, success, sent_at) VALUES ('WEEKLY_REPORT','telegram',true,$1)",
                          datetime(2026, 11, 9, 8, 0, 5, tzinfo=UTC))
    mon._started_at = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # PC was off on Monday the 16th
    assert await mon.maybe_send_report(datetime(2026, 11, 18, 9, 1, tzinfo=UTC))
    assert len(mon.rec.sent) == 1  # one catch-up, not one per missed week


async def test_disabled_sends_nothing(mon):
    mon._started_at = datetime(2026, 11, 11, tzinfo=UTC)
    mon.report_enabled = False
    assert not await mon.maybe_send_report(MON)
    assert mon.rec.sent == []


async def test_failed_delivery_is_queued_for_retry_not_resent_every_cycle(db):
    rec = Recorder(ok=False)
    m = Monitor(db, rec, probe_fn=Script())
    m._started_at = datetime(2026, 11, 11, tzinfo=UTC)
    assert await m.maybe_send_report(MON)
    await mark_sent(db, MON)
    assert not await m.maybe_send_report(MON + timedelta(seconds=30))
    assert len(rec.sent) == 1
    assert await db.pending_alert_count() == 1  # the existing retry machinery will deliver it


# --- api ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db):
    rec = Recorder()
    m = Monitor(db, rec, confirm=3, probe_fn=Script())
    app = create_app(make_settings(), db=db, monitor=m, run_monitor=False, static_dir=None)
    await db.create_user("admin@example.com", auth.hash_password("correct-horse"))
    await db.upsert_branch("HQ", "10.0.0.1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.rec, c.mon = rec, m
        yield c


async def test_send_now_requires_login(client):
    assert (await client.post("/api/report/send")).status_code == 401


async def test_send_now_sends_and_is_throttled_and_does_not_count_as_the_scheduled_one(client, db):
    await login(client)
    r = await client.post("/api/report/send")
    assert r.status_code == 200 and r.json() == {"ok": True, "error": None}
    assert "Weekly report" in client.rec.sent[0] and "HQ" in client.rec.sent[0]
    assert await db.last_notification_at("WEEKLY_REPORT") is None  # Monday's report is still due
    assert await db.last_notification_at("REPORT_MANUAL") is not None
    assert (await client.post("/api/report/send")).status_code == 429


async def test_send_now_reports_a_telegram_failure(client):
    await login(client)
    client.rec.ok = False
    assert (await client.post("/api/report/send")).json() == {"ok": False, "error": "boom"}


async def test_setting_switches_the_schedule_on_and_off(client):
    await login(client)
    assert client.mon.report_enabled is True
    assert (await client.get("/api/settings")).json()["values"]["weekly_report_enabled"] is True
    r = await client.put("/api/settings", json={"values": {"weekly_report_enabled": False}})
    assert r.status_code == 200 and client.mon.report_enabled is False
    assert (await client.put("/api/settings", json={"values": {"weekly_report_enabled": "no"}})).status_code == 422
    assert (await client.put("/api/settings", json={"values": {"weekly_report_enabled": None}})).status_code == 200
    assert client.mon.report_enabled is True
