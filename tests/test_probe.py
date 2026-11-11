import asyncio
import time

from ipmon.probe import DEAD, Target, probe, probe_all, probe_tcp


async def _server():
    async def handle(reader, writer):
        writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def test_tcp_success():
    srv, port = await _server()
    async with srv:
        r = await probe_tcp("127.0.0.1", port, count=3)
    assert r.loss_pct == 0.0 and r.avg_latency_ms is not None


async def test_tcp_refused_is_dead():
    srv, port = await _server()
    srv.close()
    await srv.wait_closed()  # port now closed
    assert await probe_tcp("127.0.0.1", port, count=2, timeout=1) == DEAD


async def test_tcp_without_port_is_dead():
    assert await probe(Target(1, "127.0.0.1", "tcp", None)) == DEAD


async def test_icmp_localhost_is_up():
    r = await probe(Target(1, "127.0.0.1"), count=2)
    assert r.loss_pct == 0.0 and r.avg_latency_ms is not None


async def test_unresolvable_host_is_dead_not_exception():
    assert await probe(Target(1, "no-such-host.invalid"), count=1, timeout=1) == DEAD


async def test_icmp_unreachable_is_dead():
    # TEST-NET-1 (RFC 5737) is never routable.
    assert await probe(Target(1, "192.0.2.1"), count=1, timeout=1) == DEAD


async def test_probe_all_is_concurrent_and_keyed():
    srv, port = await _server()
    targets = [Target(1, "127.0.0.1", "tcp", port), Target(2, "192.0.2.1"), Target(None, "127.0.0.1")]
    start = time.monotonic()
    async with srv:
        results = await probe_all(targets, count=1, timeout=1)
    elapsed = time.monotonic() - start
    assert set(results) == {1, 2, None}
    assert results[1].loss_pct == 0.0
    assert results[2] == DEAD
    assert results[None].loss_pct == 0.0
    assert elapsed < 4  # one slow target must not serialise the rest

