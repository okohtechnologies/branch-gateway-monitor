import asyncio
import logging

import httpx
import pytest

from ipmon.heartbeat import Heartbeat, validate_url

URL = "https://hc-ping.example.com/11111111-2222-3333-4444-SECRETUUID"


def make(handler, healthy=True, url=URL):
    pings = []

    def wrapped(req):
        pings.append(str(req.url))
        return handler(req)

    async def is_healthy():
        return healthy() if callable(healthy) else healthy

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return Heartbeat(url, is_healthy, client=client, interval=0), pings


def ok(req):
    return httpx.Response(200, text="OK")


async def test_pings_when_healthy():
    hb, pings = make(ok)
    assert await hb.beat() is True
    assert pings == [URL]


async def test_withholds_ping_when_unhealthy():
    hb, pings = make(ok, healthy=False)
    assert await hb.beat() is False
    assert pings == []  # silence is the alarm


async def test_resumes_when_health_returns():
    state = {"ok": False}
    hb, pings = make(ok, healthy=lambda: state["ok"])
    assert await hb.beat() is False
    state["ok"] = True
    assert await hb.beat() is True
    assert len(pings) == 1


async def test_health_check_crash_counts_as_unhealthy():
    async def boom():
        raise RuntimeError("db exploded")

    hb = Heartbeat(URL, boom, client=httpx.AsyncClient(transport=httpx.MockTransport(ok)))
    assert await hb.beat() is False


async def test_http_error_response_is_a_failed_delivery():
    hb, _ = make(lambda req: httpx.Response(500))
    assert await hb.beat() is False


async def test_network_error_does_not_raise():
    def boom(req):
        raise httpx.ConnectError("no route")

    hb, _ = make(boom)
    assert await hb.beat() is False


async def test_secret_url_never_appears_in_logs(caplog):
    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")  # even if the error text has the URL

    caplog.set_level(logging.DEBUG)
    hb, _ = make(boom)
    await hb.beat()
    hb2, _ = make(lambda req: httpx.Response(404))
    await hb2.beat()
    hb3, _ = make(ok, healthy=False)
    await hb3.beat()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETUUID" not in logged and "hc-ping.example.com" in logged


async def test_logs_failure_once_then_recovery(caplog):
    caplog.set_level(logging.INFO)
    results = iter([500, 500, 500, 200])
    hb, _ = make(lambda req: httpx.Response(next(results)))
    for _ in range(4):
        await hb.beat()
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("failed" in m for m in msgs) == 1  # not once per minute
    assert sum("restored" in m for m in msgs) == 1


async def test_run_forever_keeps_beating_and_survives_errors():
    attempts = []

    def flaky(req):
        attempts.append(1)
        if len(attempts) == 2:
            raise httpx.ConnectError("blip")
        if len(attempts) >= 4:
            raise asyncio.CancelledError
        return httpx.Response(200)

    hb, _ = make(flaky)
    with pytest.raises(asyncio.CancelledError):
        await hb.run_forever()
    assert len(attempts) == 4


@pytest.mark.parametrize("bad", ["", "not a url", "ftp://x.example/abc", "https://", "hc-ping.com/abc"])
def test_rejects_invalid_urls(bad):
    with pytest.raises(ValueError):
        validate_url(bad)


def test_accepts_valid_urls_and_strips_whitespace():
    assert validate_url("  https://hc-ping.com/abc  ") == "https://hc-ping.com/abc"
    assert validate_url("http://192.168.1.5:8080/ping/x") == "http://192.168.1.5:8080/ping/x"
