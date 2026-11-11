import json
from datetime import datetime, timezone

import httpx

from ipmon.engine import ProbeResult, Status
from ipmon.notifier import (
    TelegramNotifier,
    format_monitor_suspect,
    format_transition,
)

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
TOKEN = "123:SECRET-TOKEN"


def make(handler, retries=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramNotifier(TOKEN, "42", client=client, retries=retries, base_delay=0)


async def test_send_success_posts_chat_id_and_text():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    ok, err = await make(handler).send("hi")
    assert (ok, err) == (True, None)
    assert seen == [{"chat_id": "42", "text": "hi"}]


async def test_retries_server_error_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(502 if len(calls) < 3 else 200, json={"ok": True})

    ok, _ = await make(handler).send("hi")
    assert ok and len(calls) == 3


async def test_gives_up_after_retries():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, json={"description": "boom"})

    ok, err = await make(handler, retries=2).send("hi")
    assert not ok and len(calls) == 3 and "500" in err and "boom" in err


async def test_client_error_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"description": "chat not found"})

    ok, err = await make(handler).send("hi")
    assert not ok and len(calls) == 1 and "chat not found" in err


async def test_429_is_retried_using_retry_after():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"description": "slow down", "parameters": {"retry_after": 0}})
        return httpx.Response(200, json={"ok": True})

    ok, _ = await make(handler).send("hi")
    assert ok and len(calls) == 2


async def test_network_error_is_retried_and_never_leaks_token():
    def handler(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")

    ok, err = await make(handler, retries=1).send("hi")
    assert not ok and "ConnectError" in err and "SECRET" not in err


def test_format_down():
    text = format_transition("HQ", "10.0.0.1", Status.UP, Status.DOWN, ProbeResult(None, 100.0), WHEN)
    assert text.startswith("🔴 DOWN: HQ")
    assert "IP: 10.0.0.1" in text and "no reply" in text and "Loss: 100%" in text
    assert "2026-01-02 03:04:05 UTC" in text


def test_format_degraded_and_recovered():
    deg = format_transition("HQ", "10.0.0.1", Status.UP, Status.DEGRADED, ProbeResult(450.5, 5.0), WHEN)
    assert deg.startswith("🟠 DEGRADED: HQ") and "450.5 ms" in deg
    rec = format_transition("HQ", "10.0.0.1", Status.DOWN, Status.UP, ProbeResult(20.0, 0.0), WHEN)
    assert rec.startswith("🟢 RECOVERED: HQ")


def test_format_monitor_suspect():
    assert "MONITOR MAY BE OFFLINE" in format_monitor_suspect(WHEN)
