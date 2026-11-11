import asyncio

import pytest
import pytest_asyncio

from ipmon.bot import Bot, parse_duration
from ipmon.engine import ProbeResult
from ipmon.runner import Monitor

GOOD = ProbeResult(20.0, 0.0)
DEAD = ProbeResult(None, 100.0)
CHAT = 42


class FakeApi:
    def __init__(self):
        self.calls = []
        self.queue = []

    async def call(self, method, payload, **kw):
        self.calls.append((method, payload))
        if method == "getUpdates":
            if not self.queue:
                raise asyncio.CancelledError
            return True, self.queue.pop(0), None
        return True, {}, None

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def last(self, method):
        return [p for m, p in self.calls if m == method][-1]


class NullNotifier:
    async def send(self, text, buttons=None):
        return True, None


class Script:
    def __init__(self):
        self.state = {}

    async def __call__(self, targets, count, timeout):
        return {t.key: self.state.get(t.key, GOOD) for t in targets}


@pytest_asyncio.fixture
async def env(db):
    script = Script()
    mon = Monitor(db, NullNotifier(), confirm=3, probe_fn=script)
    api = FakeApi()
    bot = Bot(api, db, mon, {CHAT})
    a = await db.upsert_branch("HQ", "10.0.0.1")
    return bot, api, mon, script, db, a


def msg(text, chat=CHAT, user=None):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text,
                                        "from": user or {"first_name": "Alex", "username": "alex"}}}


def press(data, chat=CHAT, rows=None, text="🔴 DOWN: HQ", user=None):
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "data": data, "from": user or {"first_name": "Alex", "username": "alex"},
        "message": {"chat": {"id": chat}, "message_id": 7, "text": text,
                    "reply_markup": {"inline_keyboard": rows or []}}}}


async def cycles(m, k):
    for _ in range(k):
        await m.run_cycle()


async def outage(mon, script, a):
    await cycles(mon, 3)
    script.state[a] = DEAD
    await cycles(mon, 3)


def rows_for(iid, a):
    return [[{"text": "✅ Acknowledge", "callback_data": f"ack:{iid}"},
             {"text": "🔕 Mute 1h", "callback_data": f"mute:{a}:60"}]]


# --- duration parsing ------------------------------------------------------------------


@pytest.mark.parametrize("text,minutes", [("30m", 30), ("2h", 120), ("1d", 1440), ("45M", 45), (" 2h ", 120), ("7d", 10080)])
def test_valid_durations(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "m", "0m", "8d", "30", "-5m", "1.5h", "2 weeks", "10000h", "abc"])
def test_invalid_durations(text):
    assert parse_duration(text) is None


# --- authorisation (FR-18) ---------------------------------------------------------------


async def test_unauthorised_messages_are_ignored_silently(env):
    bot, api, *_ = env
    for text in ("/status", "/help", "/mute HQ 1h", "hello"):
        await bot.handle_update(msg(text, chat=999))
    assert api.calls == []


async def test_unauthorised_button_presses_are_ignored_and_change_nothing(env):
    bot, api, mon, script, db, a = env
    await outage(mon, script, a)
    iid = await db.open_incident_id(a)
    await bot.handle_update(press(f"ack:{iid}", chat=999))
    await bot.handle_update(press(f"mute:{a}:60", chat=999))
    assert api.calls == []
    assert await db.pool.fetchval("SELECT acknowledged_at FROM incidents WHERE id=$1", iid) is None
    assert await db.muted_until(a) is None


# --- commands ---------------------------------------------------------------------------------


async def test_help_and_unknown_and_plain_text(env):
    bot, api, *_ = env
    await bot.handle_update(msg("/help"))
    await bot.handle_update(msg("/start"))
    await bot.handle_update(msg("/nonsense"))
    await bot.handle_update(msg("just chatting"))
    t = api.texts()
    assert "/status" in t[0] and "/mute" in t[0] and t[1] == t[0]
    assert "Unknown command" in t[2] and len(t) == 3


async def test_command_with_bot_suffix(env):
    bot, api, *_ = env
    await bot.handle_update(msg("/help@OTECH_IP_BOT"))
    assert "/status" in api.texts()[0]


async def test_status_lists_branches_with_emoji(env):
    bot, api, mon, script, db, a = env
    await db.upsert_branch("Depot", "10.0.0.2")
    await cycles(mon, 3)
    await bot.handle_update(msg("/status"))
    text = api.texts()[0]
    assert "2 up" in text and "🟢 HQ - 20 ms" in text and "🟢 Depot" in text


async def test_status_shows_down_pending_disabled_and_muted(env):
    bot, api, mon, script, db, a = env
    await db.upsert_branch("Off", "10.0.0.3", enabled=False)
    await outage(mon, script, a)
    await db.upsert_branch("New", "10.0.0.4")
    await db.mute_branch(a, 30)
    await bot.handle_update(msg("/status"))
    text = api.texts()[0]
    assert "🔴 HQ - no reply 🔕" in text and "⚪ New - waiting" in text and "⚫ Off - disabled" in text


async def test_status_with_no_branches(db):
    api = FakeApi()
    bot = Bot(api, db, Monitor(db, NullNotifier(), probe_fn=Script()), {CHAT})
    await bot.handle_update(msg("/status"))
    assert "No branches" in api.texts()[0]


async def test_branch_detail_and_matching(env):
    bot, api, mon, script, db, a = env
    await db.upsert_branch("Head Office", "10.0.0.9")
    await cycles(mon, 3)
    await bot.handle_update(msg("/branch hq"))  # case-insensitive exact
    await bot.handle_update(msg("/branch head"))  # unique partial
    await bot.handle_update(msg("/branch nope"))
    await bot.handle_update(msg("/branch"))
    t = api.texts()
    assert "HQ - up" in t[0] and "IP: 10.0.0.1" in t[0] and "24h uptime: 100.00%" in t[0] and "Last incident: none" in t[0]
    assert "Head Office" in t[1]
    assert "No branch named 'nope'" in t[2]
    assert "give a branch name" in t[3]


async def test_branch_ambiguous_partial_match(env):
    bot, api, mon, script, db, a = env
    await db.upsert_branch("HQ North", "10.0.0.5")
    await bot.handle_update(msg("/branch HQ N"))  # unique
    await bot.handle_update(msg("/branch H"))  # matches both
    t = api.texts()
    assert "HQ North" in t[0] and "Several branches match" in t[1] and "HQ, HQ North" in t[1]


async def test_branch_shows_last_incident_and_mute(env):
    bot, api, mon, script, db, a = env
    await outage(mon, script, a)
    await db.mute_branch(a, 60)
    await bot.handle_update(msg("/branch HQ"))
    text = api.texts()[0]
    assert "HQ - down" in text and "Last incident: down ongoing" in text and "🔕 Muted until" in text


async def test_mute_and_unmute_commands(env):
    bot, api, mon, script, db, a = env
    await db.upsert_branch("Head Office", "10.0.0.9")
    await bot.handle_update(msg("/mute HQ 30m"))
    assert await db.muted_until(a) is not None
    assert "muted for 30m" in api.texts()[0]
    await bot.handle_update(msg("/mute Head Office 2h"))  # names with spaces
    assert "Head Office muted for 2h" in api.texts()[1]
    await bot.handle_update(msg("/unmute HQ"))
    assert await db.muted_until(a) is None and "unmuted" in api.texts()[2]
    await bot.handle_update(msg("/unmute HQ"))
    assert "not muted" in api.texts()[3]


@pytest.mark.parametrize("args", ["", "HQ", "HQ soon", "HQ 0m", "HQ 9d", "30m"])
async def test_mute_usage_errors_change_nothing(env, args):
    bot, api, mon, script, db, a = env
    await bot.handle_update(msg(f"/mute {args}"))
    assert "Usage: /mute" in api.texts()[0] or "No branch named" in api.texts()[0]
    assert await db.muted_until(a) is None


async def test_incidents_command(env):
    bot, api, mon, script, db, a = env
    await bot.handle_update(msg("/incidents"))
    assert "No open incidents" in api.texts()[0]
    await outage(mon, script, a)
    await bot.handle_update(msg("/incidents"))
    assert "1 open incident" in api.texts()[1] and "HQ - down" in api.texts()[1]


# --- buttons (FR-16, FR-17) -----------------------------------------------------------------------


async def test_acknowledge_records_who_and_updates_original_message(env):
    bot, api, mon, script, db, a = env
    await outage(mon, script, a)
    iid = await db.open_incident_id(a)
    await bot.handle_update(press(f"ack:{iid}", rows=rows_for(iid, a)))
    row = await db.pool.fetchrow("SELECT acknowledged_by, acknowledged_at FROM incidents WHERE id=$1", iid)
    assert row["acknowledged_by"] == "@alex" and row["acknowledged_at"] is not None
    assert api.last("answerCallbackQuery")["text"] == "Acknowledged"
    edit = api.last("editMessageText")
    assert edit["message_id"] == 7 and "✅ Acknowledged by @alex" in edit["text"] and edit["text"].startswith("🔴 DOWN: HQ")
    buttons = [b["callback_data"] for r in edit["reply_markup"]["inline_keyboard"] for b in r]
    assert buttons == [f"mute:{a}:60"]  # Acknowledge button is gone, Mute stays


async def test_second_acknowledge_does_not_overwrite(env):
    bot, api, mon, script, db, a = env
    await outage(mon, script, a)
    iid = await db.open_incident_id(a)
    await bot.handle_update(press(f"ack:{iid}", rows=rows_for(iid, a)))
    await bot.handle_update(press(f"ack:{iid}", rows=rows_for(iid, a), user={"first_name": "Sam"}))
    assert await db.pool.fetchval("SELECT acknowledged_by FROM incidents WHERE id=$1", iid) == "@alex"
    assert "already acknowledged" in api.last("answerCallbackQuery")["text"]


async def test_ack_on_resolved_incident(env):
    bot, api, mon, script, db, a = env
    await outage(mon, script, a)
    iid = await db.open_incident_id(a)
    script.state[a] = GOOD
    await cycles(mon, 3)
    await bot.handle_update(press(f"ack:{iid}", rows=rows_for(iid, a)))
    assert "already resolved" in api.last("answerCallbackQuery")["text"]
    assert api.last("editMessageReplyMarkup")["reply_markup"]["inline_keyboard"] == []


async def test_mute_button_mutes_and_swaps_to_unmute_then_back(env):
    bot, api, mon, script, db, a = env
    iid = 1
    await bot.handle_update(press(f"mute:{a}:60", rows=rows_for(iid, a)))
    assert await db.muted_until(a) is not None
    rows = api.last("editMessageReplyMarkup")["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for r in rows for b in r] == [f"ack:{iid}", f"unmute:{a}"]
    await bot.handle_update(press(f"unmute:{a}", rows=rows))
    assert await db.muted_until(a) is None
    rows = api.last("editMessageReplyMarkup")["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for r in rows for b in r] == [f"ack:{iid}", f"mute:{a}:60"]


@pytest.mark.parametrize("data", ["", "garbage", "ack:abc", "ack:1; DROP TABLE", "mute:1", "mute:1:0", "mute:1:999999999", "mute:99999:60"])
async def test_malformed_or_stale_callbacks_do_nothing(env, data):
    bot, api, mon, script, db, a = env
    await bot.handle_update(press(data))
    assert "no longer valid" in api.last("answerCallbackQuery")["text"]
    assert await db.pool.fetchval("SELECT count(*) FROM mutes") == 0
    assert not [c for c in api.calls if c[0].startswith("edit")]


# --- polling loop ---------------------------------------------------------------------------------


async def test_run_forever_advances_offset_and_survives_handler_errors(env):
    bot, api, mon, script, db, a = env
    api.queue = [
        [{"update_id": 10, "message": {"chat": {"id": CHAT}, "text": "/help", "from": {}}},
         {"update_id": 11, "message": None}],  # malformed: handler raises
        [{"update_id": 12, "message": {"chat": {"id": CHAT}, "text": "/help", "from": {}}}],
    ]
    with pytest.raises(asyncio.CancelledError):
        await bot.run_forever()
    polls = [p for m, p in api.calls if m == "getUpdates"]
    assert [p.get("offset") for p in polls] == [None, 12, 13]
    assert len(api.texts()) == 2  # the update after the bad one was still processed


async def test_run_forever_backs_off_on_api_failure(env, monkeypatch):
    bot, api, *_ = env
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("ipmon.bot.asyncio.sleep", fake_sleep)
    results = iter([(False, None, "boom"), (False, None, "boom"), (True, [], None)])

    async def call(method, payload, **kw):
        try:
            return next(results)
        except StopIteration:
            raise asyncio.CancelledError

    api.call = call
    with pytest.raises(asyncio.CancelledError):
        await bot.run_forever()
    assert sleeps == [1.0, 2.0]


async def test_branch_without_any_data_yet_does_not_crash(env):
    bot, api, mon, script, db, a = env  # no probe cycle has run
    await bot.handle_update(msg("/branch HQ"))
    assert "Latency: no data yet" in api.texts()[0]


async def test_branch_after_restart_uses_last_stored_check(env):
    bot, api, mon, script, db, a = env
    await cycles(mon, 3)
    mon.latest.clear()  # simulates a restart: no live snapshot, but history exists
    await bot.handle_update(msg("/branch HQ"))
    assert "Latency: 20 ms" in api.texts()[0]
