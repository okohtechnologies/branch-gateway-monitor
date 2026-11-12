"""Telegram notifications with retry and backoff (FR-14, FR-15, FR-16, FR-20)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from .engine import ProbeResult, Status

log = logging.getLogger(__name__)
# httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

_EMOJI = {Status.UP: "🟢", Status.DEGRADED: "🟠", Status.DOWN: "🔴"}

Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)


class TelegramApi:
    """Thin Bot API caller with retry and backoff. Never raises, never leaks the token."""

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient | None = None,
        retries: int = 3,
        base_delay: float = 1.0,
    ):
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=15)
        self._retries = retries
        self._base_delay = base_delay

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(
        self, method: str, payload: dict, *, retries: int | None = None, timeout: float | None = None
    ) -> tuple[bool, dict | None, str | None]:
        """Returns (ok, result, error)."""
        attempts = self._retries if retries is None else retries
        error = "unknown error"
        for attempt in range(attempts + 1):
            delay = self._base_delay * 2**attempt
            try:
                resp = await self._client.post(
                    f"{self._base}/{method}", json=payload, **({"timeout": timeout} if timeout else {})
                )
            except httpx.HTTPError as exc:
                error = f"network error: {type(exc).__name__}"
            else:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                if resp.status_code == 200:
                    return True, body.get("result"), None
                error = f"HTTP {resp.status_code}: {body.get('description', 'no description')}"
                if resp.status_code == 429:
                    delay = float(body.get("parameters", {}).get("retry_after", delay))
                elif resp.status_code < 500:
                    break  # a 4xx other than 429 will not fix itself
            if attempt < attempts:
                log.warning("telegram %s failed (%s); retrying in %.1fs", method, error, delay)
                await asyncio.sleep(delay)
        log.error("telegram %s failed permanently: %s", method, error)
        return False, None, error


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.AsyncClient | None = None,
        retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.api = TelegramApi(token, client, retries, base_delay)
        self._chat_id = chat_id

    async def aclose(self) -> None:
        await self.api.aclose()

    async def send(self, text: str, buttons: Buttons | None = None) -> tuple[bool, str | None]:
        """Send a message, optionally with inline buttons. Returns (success, error)."""
        payload: dict = {"chat_id": self._chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = keyboard(buttons)
        ok, _, err = await self.api.call("sendMessage", payload)
        return ok, err


def keyboard(buttons: Buttons) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in buttons
        ]
    }


def alert_buttons(incident_id: int, branch_id: int) -> Buttons:
    """FR-16: Acknowledge and Mute 1h on DOWN and DEGRADED alerts."""
    return [[("✅ Acknowledge", f"ack:{incident_id}"), ("🔕 Mute 1h", f"mute:{branch_id}:60")]]


def _stamp(when: datetime) -> str:
    """Time in the host's zone (set TZ in the container), with its abbreviation."""
    return when.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def humanize(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def _details(host: str, result: ProbeResult) -> str:
    latency = "no reply" if result.avg_latency_ms is None else f"{result.avg_latency_ms:g} ms"
    return f"IP: {host}\nLatency: {latency}\nLoss: {result.loss_pct:g}%"


def format_transition(
    name: str, host: str, old: Status | None, new: Status, result: ProbeResult, when: datetime
) -> str:
    """FR-15: branch name, IP, status, latency, loss and time."""
    title = f"{_EMOJI[Status.UP]} RECOVERED: {name}" if new == Status.UP else f"{_EMOJI[new]} {new.value}: {name}"
    return f"{title}\n{_details(host, result)}\nTime: {_stamp(when)}"


def format_reminder(name: str, host: str, severity: str, started_at: datetime, when: datetime) -> str:
    """FR-13: sent periodically while an incident stays open and unacknowledged."""
    emoji = _EMOJI[Status(severity)]
    return (
        f"⏰ STILL {severity}: {name}\n"
        f"IP: {host}\n"
        f"Ongoing for {humanize((when - started_at).total_seconds())}\n"
        f"Time: {_stamp(when)}"
    )


def format_monitor_suspect(when: datetime) -> str:
    return (
        "⚠️ MONITOR MAY BE OFFLINE\n"
        "The canary and every branch are unreachable at once. The monitor's own "
        "network is the likely cause, so per-branch alerts are paused.\n"
        f"Time: {_stamp(when)}"
    )


def format_monitor_recovered(when: datetime) -> str:
    return f"🟢 Monitor connectivity restored.\nTime: {_stamp(when)}"
