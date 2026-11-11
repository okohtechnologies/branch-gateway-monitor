"""Async probing: ICMP (icmplib) with a TCP connect fallback.

Every probe returns a ProbeResult and never raises: any failure (timeout,
DNS error, permissions) is reported as 100% loss so the engine sees DOWN.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from icmplib import ICMPLibError, async_ping

from .engine import ProbeResult

log = logging.getLogger(__name__)

DEAD = ProbeResult(avg_latency_ms=None, loss_pct=100.0)

@dataclass(frozen=True)
class Target:
    key: int | None  # branch id; None for the canary
    host: str
    probe_type: str = "icmp"  # "icmp" | "tcp"
    port: int | None = None


async def probe_icmp(host: str, count: int = 4, timeout: float = 2.0) -> ProbeResult:
    """FR-6: ``count`` echo requests, ``timeout`` seconds each."""
    try:
        r = await async_ping(host, count=count, interval=0.2, timeout=timeout, privileged=False)
    except ICMPLibError as exc:
        log.warning("icmp probe of %s failed: %s", host, exc)
        return DEAD
    if r.packets_received == 0:
        return DEAD
    return ProbeResult(avg_latency_ms=round(r.avg_rtt, 2), loss_pct=round(r.packet_loss * 100, 1))


async def probe_tcp(host: str, port: int, count: int = 4, timeout: float = 2.0) -> ProbeResult:
    """FR-7: ``count`` connection attempts; loss is the failed fraction."""
    rtts: list[float] = []
    for _ in range(count):
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError):
            continue
        rtts.append((time.monotonic() - start) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    if not rtts:
        return DEAD
    return ProbeResult(
        avg_latency_ms=round(sum(rtts) / len(rtts), 2),
        loss_pct=round((count - len(rtts)) / count * 100, 1),
    )


async def probe(target: Target, count: int = 4, timeout: float = 2.0) -> ProbeResult:
    # Hard ceiling so a stuck probe can never stall the cycle.
    ceiling = count * (timeout + 0.2) + 5
    try:
        if target.probe_type == "tcp":
            if target.port is None:
                log.error("tcp target %s has no port", target.host)
                return DEAD
            coro = probe_tcp(target.host, target.port, count, timeout)
        else:
            coro = probe_icmp(target.host, count, timeout)
        return await asyncio.wait_for(coro, ceiling)
    except asyncio.TimeoutError:
        log.warning("probe of %s exceeded %.0fs ceiling", target.host, ceiling)
        return DEAD
    except Exception:  # a probe must never take the monitor down
        log.exception("unexpected error probing %s", target.host)
        return DEAD


async def probe_all(
    targets: list[Target], count: int = 4, timeout: float = 2.0
) -> dict[int | None, ProbeResult]:
    """FR-8: probe every target concurrently. Keyed by Target.key."""
    results = await asyncio.gather(*(probe(t, count, timeout) for t in targets))
    return {t.key: r for t, r in zip(targets, results)}
