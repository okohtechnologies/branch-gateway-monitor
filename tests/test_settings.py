import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from ipmon import auth, runtime_settings as rs
from ipmon.api import create_app
from ipmon.config import Settings
from ipmon.engine import ProbeResult
from ipmon.probe import Target
from ipmon.runner import Monitor

SECRET = "x" * 48
GOOD = ProbeResult(20.0, 0.0)
CANARY = Target(None, "1.1.1.1", "tcp", 443)


def make_settings():
    return Settings(
        database_url="postgresql://unused", telegram_bot_token="t", telegram_chat_id="1",
        secret_key=SECRET, _env_file=None,
    )


class StubApi:
    def __init__(self):
        self.ok = True
        self.calls = []

    async def call(self, method, payload, **kw):
        self.calls.append(method)
        if self.ok:
            return True, {"username": "demo_bot", "first_name": "Demo Bot"}, None
        return False, None, "network error: ConnectError"


class StubNotifier:
    masked_chat = "•••••6148"

    def __init__(self):
        self.api, self.sent, self.ok = StubApi(), [], True

    async def send(self, text, buttons=None):
        self.sent.append(text)
        return (True, None) if self.ok else (False, "HTTP 502: bad gateway")


class Script:
    async def __call__(self, targets, count, timeout):
        return {t.key: GOOD for t in targets}


@pytest_asyncio.fixture
async def env(db):
    notifier = StubNotifier()
    mon = Monitor(db, notifier, confirm=3, canary=CANARY, probe_fn=Script())
    app = create_app(make_settings(), db=db, monitor=mon, run_monitor=False, static_dir=None)
    await db.create_user("admin@example.com", auth.hash_password("correct-horse"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/login", json={"email": "admin@example.com", "password": "correct-horse"})
        yield client, mon, notifier, db, app


async def another_browser(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- the registry: validation, defaults, overrides ------------------------------------------------


@pytest.mark.parametrize("key,raw,expected", [
    ("probe_interval_s", 5, 5), ("probe_interval_s", 5.0, 5), ("probe_timeout_s", 1, 1.0), ("probe_timeout_s", 1.5, 1.5),
    ("confirm", 4, 4), ("canary_enabled", False, False), ("canary_type", "icmp", "icmp"),
    ("canary_host", " 1.1.1.1 ", "1.1.1.1"), ("canary_host", "example.com", "example.com"),
    ("reminder_minutes", 0, 0), ("retention_days", 3650, 3650), ("default_loss_warn_pct", 0, 0.0),
])
def test_valid_values_are_accepted_and_typed(key, raw, expected):
    got = rs.check(key, raw)
    assert got == expected and type(got) is type(expected)


@pytest.mark.parametrize("key,raw,fragment", [
    ("probe_interval_s", 1, "between 2 and 3600"), ("probe_interval_s", 3601, "between 2 and 3600"),
    ("probe_interval_s", 2.5, "whole number"), ("probe_interval_s", "5", "must be a number"),
    ("probe_interval_s", True, "must be a number"), ("probe_interval_s", float("nan"), "must be a number"),
    ("probe_timeout_s", 0.1, "between 0.5 and 10"), ("probe_count", 0, "between 1 and 10"),
    ("confirm", 21, "between 1 and 20"), ("canary_enabled", "yes", "true or false"), ("canary_enabled", 1, "true or false"),
    ("canary_type", "udp", "one of: tcp, icmp"), ("canary_host", "", "IP address or hostname"),
    ("canary_host", "999.1.1.1", "invalid IP"), ("canary_host", "bad host", "invalid"), ("canary_host", 5, "IP address or hostname"),
    ("canary_port", 70000, "between 1 and 65535"), ("reminder_minutes", -1, "between 0 and 1440"),
    ("default_loss_warn_pct", 101, "between 0 and 100"), ("default_latency_warn_ms", 0, "between 1 and 60000"),
    ("telegram_bot_token", "x", "not a setting you can change"),
])
def test_invalid_values_are_rejected_with_a_readable_reason(key, raw, fragment):
    with pytest.raises(ValueError) as e:
        rs.check(key, raw)
    assert fragment in str(e.value)


def test_secrets_are_not_editable():
    for secret in ("telegram_bot_token", "telegram_chat_id", "secret_key", "database_url", "heartbeat_url"):
        assert secret not in rs.FIELDS


def test_effective_applies_overrides_and_ignores_stored_garbage():
    defaults = rs.defaults_from(make_settings())
    values = rs.effective(defaults, {"probe_interval_s": 5, "confirm": 999, "unknown": 1, "canary_type": "icmp"})
    assert values["probe_interval_s"] == 5 and values["canary_type"] == "icmp"
    assert values["confirm"] == defaults["confirm"]  # 999 no longer validates: falls back, never crashes
    assert "unknown" not in values


def test_warning_when_an_unreachable_probe_outlasts_the_interval():
    values = rs.defaults_from(make_settings())
    assert rs.warnings(values) == []
    values.update(probe_interval_s=5, probe_count=4, probe_timeout_s=2.0)  # 4 x 2.2s = 8.8s > 5s
    [w] = rs.warnings(values)
    assert "8.8s" in w and "5s" in w


def test_limits_describe_every_field():
    lim = rs.limits()
    assert set(lim) == set(rs.FIELDS)
    assert lim["probe_interval_s"] == {"kind": "int", "min": 2, "max": 3600}
    assert lim["canary_type"]["choices"] == ["tcp", "icmp"]


# --- the API ----------------------------------------------------------------------------------------


async def test_every_new_endpoint_requires_login(env):
    _, _, _, _, app = env
    anon = await another_browser(app)
    for method, url in [("get", "/api/settings"), ("put", "/api/settings"), ("get", "/api/status"), ("get", "/api/telegram"),
                        ("post", "/api/telegram/test"), ("post", "/api/account/password")]:
        assert (await getattr(anon, method)(url)).status_code == 401, url


async def test_get_settings_starts_at_the_defaults(env):
    client, *_ = env
    data = (await client.get("/api/settings")).json()
    assert data["values"] == data["defaults"] and data["custom"] == [] and data["warnings"] == []
    assert data["values"]["probe_interval_s"] == 30 and data["values"]["default_latency_warn_ms"] == 200
    assert "limits" in data


async def test_changing_settings_applies_to_the_running_monitor_immediately(env):
    client, mon, *_ = env
    assert (mon.interval, mon.probe_count, mon.engine.confirm) == (30.0, 4, 3)
    r = await client.put("/api/settings", json={"values": {"probe_interval_s": 5, "probe_count": 3, "probe_timeout_s": 1, "confirm": 4}})
    assert r.status_code == 200
    assert sorted(r.json()["custom"]) == ["confirm", "probe_count", "probe_interval_s", "probe_timeout_s"]
    assert (mon.interval, mon.probe_count, mon.probe_timeout, mon.engine.confirm) == (5.0, 3, 1.0, 4)  # no restart


async def test_a_change_survives_a_restart(env):
    client, mon, notifier, db, app = env
    await client.put("/api/settings", json={"values": {"reminder_minutes": 10}})
    fresh = Monitor(db, notifier, probe_fn=Script())  # a new process starts with env defaults...
    fresh.apply_settings(rs.effective(rs.defaults_from(make_settings()), await db.settings_overrides()))  # ...then loads overrides
    assert fresh.reminder_minutes == 10


async def test_null_resets_a_setting_to_its_default(env):
    client, mon, *_ = env
    await client.put("/api/settings", json={"values": {"probe_interval_s": 7}})
    r = await client.put("/api/settings", json={"values": {"probe_interval_s": None}})
    assert r.json()["values"]["probe_interval_s"] == 30 and r.json()["custom"] == []
    assert mon.interval == 30.0


async def test_an_invalid_edit_changes_nothing_at_all(env):
    client, mon, _, db, _ = env
    r = await client.put("/api/settings", json={"values": {"probe_interval_s": 5, "confirm": 999, "canary_host": "bad host"}})
    assert r.status_code == 422
    msgs = " | ".join(f'{d["loc"][-1]}: {d["msg"]}' for d in r.json()["detail"])
    assert "confirm: must be between 1 and 20" in msgs and "canary_host: invalid" in msgs
    assert await db.settings_overrides() == {}  # the valid probe_interval_s was NOT applied either
    assert mon.interval == 30.0


async def test_unknown_and_secret_keys_cannot_be_written(env):
    client, _, _, db, _ = env
    for key in ("telegram_bot_token", "secret_key", "nonsense"):
        r = await client.put("/api/settings", json={"values": {key: "x"}})
        assert r.status_code == 422, key
    assert await db.settings_overrides() == {}


async def test_the_new_confirm_value_reaches_branches_already_being_tracked(env):
    client, mon, _, db, _ = env
    a = await db.upsert_branch("HQ", "10.0.0.1")
    await mon.run_cycle()
    await client.put("/api/settings", json={"values": {"confirm": 2}})
    assert mon.engine.confirm == 2 and mon.engine._branches[a].confirm == 2 and mon.engine._canary.confirm == 2


async def test_switching_the_canary_off_and_on_again(env):
    client, mon, *_ = env
    assert mon.canary == CANARY
    await client.put("/api/settings", json={"values": {"canary_enabled": False}})
    assert mon.canary is None
    await client.put("/api/settings", json={"values": {"canary_enabled": True, "canary_host": "8.8.8.8", "canary_type": "icmp", "canary_port": 53}})
    assert mon.canary == Target(None, "8.8.8.8", "icmp", 53)


async def test_zero_turns_reminders_and_pruning_off(env):
    client, mon, *_ = env
    await client.put("/api/settings", json={"values": {"reminder_minutes": 0, "retention_days": 0}})
    assert mon.reminder_minutes is None and mon.retention_days is None
    await client.put("/api/settings", json={"values": {"reminder_minutes": 15, "retention_days": 14}})
    assert mon.reminder_minutes == 15 and mon.retention_days == 14


async def test_a_risky_combination_is_saved_but_warned_about(env):
    client, *_ = env
    r = await client.put("/api/settings", json={"values": {"probe_interval_s": 5}})  # default 4 pings x 2.2s = 8.8s
    assert r.status_code == 200 and len(r.json()["warnings"]) == 1


async def test_new_branch_defaults_are_served_for_the_add_form(env):
    client, *_ = env
    await client.put("/api/settings", json={"values": {"default_latency_warn_ms": 150, "default_loss_warn_pct": 10}})
    v = (await client.get("/api/settings")).json()["values"]
    assert (v["default_latency_warn_ms"], v["default_loss_warn_pct"]) == (150.0, 10.0)


# --- monitor wakes up when the interval changes ------------------------------------------------------


async def test_a_new_interval_takes_effect_without_waiting_out_the_old_one(db):
    mon = Monitor(db, StubNotifier(), interval=300, confirm=3, probe_fn=Script())
    await db.upsert_branch("HQ", "10.0.0.1")
    task = asyncio.create_task(mon.run_forever())
    for _ in range(100):
        if mon.cycles >= 1:
            break
        await asyncio.sleep(0.05)
    assert mon.cycles == 1  # the loop is now sleeping for 300 seconds
    values = rs.defaults_from(make_settings())
    values["probe_interval_s"] = 2
    mon.apply_settings(values)  # without the wake-up this would wait 5 minutes
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert mon.cycles >= 2


# --- monitor health panel ---------------------------------------------------------------------------


async def test_status_reports_loop_database_alerts_and_heartbeat(env):
    client, mon, _, _, app = env
    await mon.run_cycle()
    s = (await client.get("/api/status")).json()
    assert s["ok"] is True and s["database"] is True and s["probe_loop"]["cycles"] == 1
    assert s["interval_s"] == 30.0 and s["alerts_pending"] == 0 and s["uptime_s"] >= 0
    assert s["heartbeat"] == {"configured": False, "failing": False, "last_ok_age_s": None}
    app.state.heartbeat = SimpleNamespace(failing=False, last_delivered_at=time.time() - 12)
    hb = (await client.get("/api/status")).json()["heartbeat"]
    assert hb["configured"] is True and hb["failing"] is False and 11 <= hb["last_ok_age_s"] <= 14
    app.state.heartbeat = SimpleNamespace(failing=True, last_delivered_at=None)
    hb = (await client.get("/api/status")).json()["heartbeat"]
    assert hb["failing"] is True and hb["last_ok_age_s"] is None


# --- telegram -----------------------------------------------------------------------------------------


async def test_telegram_status_shows_bot_masked_chat_and_last_delivery(env):
    client, mon, notifier, db, _ = env
    await db.record_notification(None, "DOWN", "telegram", True)
    t = (await client.get("/api/telegram")).json()
    assert t["configured"] is True and t["bot"] == {"username": "demo_bot", "name": "Demo Bot"}
    assert t["chat"].endswith("6148") and "1" not in t["chat"].replace("6148", "")  # only the last four digits are visible
    assert t["delivered_at"] is not None and t["last_error"] is None and t["pending"] == 0


async def test_telegram_status_reports_when_telegram_is_unreachable(env):
    client, mon, notifier, db, _ = env
    notifier.api.ok = False
    await db.record_notification(None, "DOWN", "telegram", False, "network error: ConnectError", body="x")
    t = (await client.get("/api/telegram")).json()
    assert t["bot"] is None and "ConnectError" in t["bot_error"]
    assert t["last_error"] == "network error: ConnectError"


async def test_telegram_status_without_a_telegram_client(env):
    client, mon, _, _, _ = env
    mon.notifier = SimpleNamespace(send=None)  # e.g. a notifier with no Bot API client
    t = (await client.get("/api/telegram")).json()
    assert t["configured"] is False and t["bot"] is None and t["chat"] is None


async def test_test_message_is_sent_and_recorded(env):
    client, _, notifier, db, _ = env
    r = await client.post("/api/telegram/test")
    assert r.json() == {"ok": True, "error": None}
    assert len(notifier.sent) == 1 and "Test message" in notifier.sent[0]
    row = await db.pool.fetchrow("SELECT kind, success FROM notifications")
    assert (row["kind"], row["success"]) == ("TEST", True)
    assert await db.pending_alert_count() == 0  # a failed TEST is never queued for retry


async def test_a_failed_test_message_reports_why_and_is_not_retried(env):
    client, _, notifier, db, _ = env
    notifier.ok = False
    r = await client.post("/api/telegram/test")
    assert r.json() == {"ok": False, "error": "HTTP 502: bad gateway"}
    assert await db.pending_alert_count() == 0


async def test_test_messages_are_rate_limited(env):
    client, _, notifier, _, _ = env
    assert (await client.post("/api/telegram/test")).status_code == 200
    r = await client.post("/api/telegram/test")
    assert r.status_code == 429 and r.headers["retry-after"] == "5"
    assert len(notifier.sent) == 1


# --- changing the password ------------------------------------------------------------------------------


async def change(client, current="correct-horse", new="a-brand-new-password"):
    return await client.post("/api/account/password", json={"current_password": current, "new_password": new})


async def test_wrong_current_password_is_a_400_not_a_logout(env):
    client, *_ = env
    r = await change(client, current="wrong")
    assert r.status_code == 400 and "not correct" in r.json()["detail"]
    assert (await client.get("/api/me")).status_code == 200  # still signed in: a 401 here would have kicked the user out


async def test_new_password_rules(env):
    client, *_ = env
    short = await change(client, new="too-short")
    assert short.status_code == 422
    same = await change(client, new="correct-horse")
    assert same.status_code == 422 and "different" in str(same.json()["detail"])


async def test_successful_change_keeps_this_device_and_signs_out_the_others(env):
    client, mon, notifier, db, app = env
    other = await another_browser(app)
    await other.post("/api/login", json={"email": "admin@example.com", "password": "correct-horse"})
    assert (await other.get("/api/me")).status_code == 200

    r = await change(client)
    assert r.status_code == 200 and r.json() == {"ok": True, "signed_out_others": 1}
    assert (await client.get("/api/me")).status_code == 200  # this device stays signed in
    assert (await other.get("/api/me")).status_code == 401  # the other one does not

    fresh = await another_browser(app)
    old = await fresh.post("/api/login", json={"email": "admin@example.com", "password": "correct-horse"})
    new = await fresh.post("/api/login", json={"email": "admin@example.com", "password": "a-brand-new-password"})
    assert old.status_code == 401 and new.status_code == 200


async def test_guessing_the_current_password_is_throttled(env):
    client, *_ = env
    for _ in range(5):
        assert (await change(client, current="nope")).status_code == 400
    r = await change(client, current="nope")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert (await change(client)).status_code == 429  # even the right password waits out the lockout
