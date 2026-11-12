"""Dead-man's-switch heartbeat (FR-30).

Pings an external service (e.g. healthchecks.io) every minute, but ONLY while the
monitor is healthy. If the monitor dies, hangs, or loses its network, the pings stop
and the external service alerts you: the one failure this process can never report
about itself.

The ping URL is a secret (it identifies your check), so it is never logged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and the ping URL is a secret.
logging.getLogger("httpx").setLevel(logging.WARNING)


def validate_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("HEARTBEAT_URL must be a full http(s) URL")
    return url.strip()


class Heartbeat:
    def __init__(
        self,
        url: str,
        is_healthy: Callable[[], Awaitable[bool]],
        *,
        interval: float = 60,
        client: httpx.AsyncClient | None = None,
    ):
        self._url = validate_url(url)
        self._host = urlsplit(self._url).netloc  # safe to log; the path holds the secret
        self._is_healthy = is_healthy
        self.interval = interval
        self._client = client or httpx.AsyncClient(timeout=10)
        self._failing = False  # log state changes, not every minute
        self.last_delivered_at: float | None = None  # unix time of the last ping that got through

    @property
    def failing(self) -> bool:
        return self._failing

    async def aclose(self) -> None:
        await self._client.aclose()

    async def beat(self) -> bool:
        """One tick. Returns True if a ping was delivered."""
        try:
            healthy = await self._is_healthy()
        except Exception:
            log.exception("heartbeat health check failed")
            healthy = False
        if not healthy:
            log.warning("monitor unhealthy: withholding heartbeat so the external alert fires")
            return False
        try:
            resp = await self._client.get(self._url)
            delivered = resp.status_code < 300
            problem = None if delivered else f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            delivered, problem = False, type(exc).__name__  # str(exc) could include the URL
        if delivered and self._failing:
            log.info("heartbeat delivery restored (%s)", self._host)
        elif not delivered and not self._failing:
            log.warning("heartbeat delivery to %s failed: %s", self._host, problem)
        self._failing = not delivered
        if delivered:
            self.last_delivered_at = time.time()
        return delivered

    async def run_forever(self) -> None:
        while True:
            await self.beat()
            await asyncio.sleep(self.interval)
