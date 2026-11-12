"""The monitor loop: probe, decide, store, notify, publish."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from .db import Database
from .engine import (
    Engine,
    MonitorSuspectEnded,
    MonitorSuspectStarted,
    Status,
    Transition,
    classify,
)
from .notifier import (
    alert_buttons,
    format_delayed,
    format_monitor_recovered,
    format_monitor_suspect,
    format_reminder,
    format_transition,
)
from .probe import Target, probe_all

log = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self,
        db: Database,
        notifier,
        *,
        interval: float = 30,
        confirm: int = 3,
        canary: Target | None = None,
        probe_count: int = 4,
        probe_timeout: float = 2.0,
        probe_fn=probe_all,
        reminder_minutes: int | None = 30,
        retention_days: int | None = 30,
    ):
        self.db = db
        self.notifier = notifier
        self.interval = interval
        self.canary = canary
        self.probe_count = probe_count
        self.probe_timeout = probe_timeout
        self._probe_fn = probe_fn
        self.reminder_minutes = reminder_minutes
        self.retention_days = retention_days
        self._last_prune: float | None = None
        self.engine = Engine(confirm)
        self.latest: dict[int, dict] = {}  # branch_id -> last probe snapshot
        self.last_cycle_at: datetime | None = None
        self.alerts_pending = 0  # undelivered alerts waiting to be re-sent
        self.cycles = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._wake = asyncio.Event()  # set when settings change, so a new interval applies at once

    # --- settings changed while running ---------------------------------------------

    def apply_settings(self, values: dict) -> None:
        """Make new values (see runtime_settings) take effect immediately, without a restart."""
        self.interval = float(values["probe_interval_s"])
        self.probe_count = int(values["probe_count"])
        self.probe_timeout = float(values["probe_timeout_s"])
        self.reminder_minutes = int(values["reminder_minutes"]) or None
        self.retention_days = int(values["retention_days"]) or None
        self.engine.set_confirm(int(values["confirm"]))
        canary = (
            Target(None, values["canary_host"], values["canary_type"], int(values["canary_port"]))
            if values["canary_enabled"]
            else None
        )
        if canary != self.canary:
            self.canary = canary
            self.engine.reset_canary()  # a stale canary verdict must not keep suppressing alerts
        self._wake.set()

    # --- live feed (WebSocket) ----------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, message: dict) -> None:
        for q in list(self._subscribers):
            if q.full():  # slow client: drop its oldest message, never block the loop
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    # --- lifecycle ----------------------------------------------------------

    async def restore_state(self) -> None:
        """After a restart, branches with an open incident are not 'new'."""
        rows = await self.db.pool.fetch(
            "SELECT branch_id, severity FROM incidents WHERE ended_at IS NULL"
        )
        for r in rows:
            self.engine.restore(r["branch_id"], Status(r["severity"]))

    PRUNE_EVERY_S = 6 * 3600

    async def maybe_prune(self) -> int:
        """Delete expired raw checks at start-up and then every few hours."""
        now = time.monotonic()
        if self._last_prune is not None and now - self._last_prune < self.PRUNE_EVERY_S:
            return 0
        self._last_prune = now
        await self.db.prune_sessions()
        if not self.retention_days:
            return 0
        removed = await self.db.prune_checks(self.retention_days)
        if removed:
            log.info("pruned %d checks older than %d days", removed, self.retention_days)
        return removed

    async def run_forever(self) -> None:
        await self.restore_state()
        while True:
            started = time.monotonic()
            try:
                await self.maybe_prune()
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("probe cycle failed; continuing")
            delay = max(0.0, self.interval - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._wake.wait(), delay)  # new settings cut the wait short
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    # --- one cycle ------------------------------------------------------------

    async def run_cycle(self) -> list:
        rows = await self.db.enabled_branches()  # re-read each cycle: FR-3
        by_id = {r["id"]: r for r in rows}
        targets = [Target(r["id"], r["host"], r["probe_type"], r["port"]) for r in rows]
        if self.canary:
            targets.append(self.canary)

        results = await self._probe_fn(targets, self.probe_count, self.probe_timeout)
        canary_result = results.pop(None, None) if self.canary else None
        configs = [self.db.to_config(r) for r in rows]
        now = datetime.now(timezone.utc)

        snapshot = []
        for cfg in configs:
            res = results[cfg.id]
            status = classify(res, cfg.latency_warn_ms, cfg.loss_warn_pct)
            await self.db.insert_check(cfg.id, res, status, now)
            snap = {
                "branch_id": cfg.id,
                "name": by_id[cfg.id]["name"],
                "raw_status": status.value,
                "avg_latency_ms": res.avg_latency_ms,
                "loss_pct": res.loss_pct,
                "ts": now.isoformat(),
            }
            self.latest[cfg.id] = snap
            snapshot.append(snap)
        for gone in set(self.latest) - set(by_id):
            del self.latest[gone]
        if canary_result is not None:
            canary_status = classify(canary_result, float("inf"), 100.0)
            await self.db.insert_check(None, canary_result, canary_status, now)

        events = self.engine.process_cycle(configs, results, canary_result)
        for event in events:
            await self._handle(event, by_id, now)
        await self._send_reminders(now)
        await self._retry_notifications(now)
        self.alerts_pending = await self.db.pending_alert_count()

        self.cycles += 1
        self.last_cycle_at = now
        for snap in snapshot:
            confirmed = self.engine.confirmed(snap["branch_id"])
            snap["status"] = confirmed.value if confirmed else "PENDING"
        self._publish({
            "type": "cycle",
            "ts": now.isoformat(),
            "monitor_suspect": self.engine.monitor_suspect,
            "alerts_pending": self.alerts_pending,
            "branches": snapshot,
        })
        return events

    async def _handle(self, event, by_id: dict, now: datetime) -> None:
        if isinstance(event, Transition):
            row = by_id.get(event.branch_id)
            if row is None:
                return
            if event.new == Status.UP:
                incident_id = await self.db.close_incident(event.branch_id, now)
            else:
                incident_id = await self.db.open_incident(event.branch_id, event.new, now)
                await self.db.set_incident_severity(incident_id, event.new)
            text = format_transition(row["name"], row["host"], event.old, event.new, event.result, now)
            kind = "RECOVERED" if event.new == Status.UP else event.new.value
            buttons = None if event.new == Status.UP else alert_buttons(incident_id, event.branch_id)
            await self._notify(incident_id, kind, text, buttons, branch_id=event.branch_id)
            self._publish({"type": "transition", "branch_id": event.branch_id,
                           "name": row["name"], "status": event.new.value, "ts": now.isoformat()})
        elif isinstance(event, MonitorSuspectStarted):
            await self._notify(None, "MONITOR_SUSPECT", format_monitor_suspect(now))
        elif isinstance(event, MonitorSuspectEnded):
            await self._notify(None, "MONITOR_RECOVERED", format_monitor_recovered(now))

    async def _notify(
        self,
        incident_id: int | None,
        kind: str,
        text: str,
        buttons=None,
        branch_id: int | None = None,
    ) -> None:
        # Muting suppresses notifications only: probing and history continue.
        if branch_id is not None and await self.db.muted_until(branch_id):
            await self.db.record_notification(incident_id, kind, "telegram", True, None, suppressed=True)
            return
        ok, err = await self.notifier.send(text, buttons)
        # A failed alert keeps its text so it can be re-sent. Reminders are not queued: the next one follows anyway.
        keep = (not ok) and kind != "REMINDER"
        await self.db.record_notification(
            incident_id, kind, "telegram", ok, err,
            body=text if keep else None, buttons=buttons if keep else None,
        )

    RETRY_GIVE_UP = timedelta(hours=24)
    RETRY_MAX_DELAY_S = 900

    async def _retry_notifications(self, now: datetime) -> None:
        """Re-send alerts that could not be delivered, oldest first, with growing delays.

        Stops at the first failure: if Telegram is unreachable the rest would fail as well.
        """
        for r in await self.db.due_notification_retries(limit=10):
            if now - r["sent_at"] > self.RETRY_GIVE_UP:
                await self.db.give_up_notification(r["id"])
                log.warning("gave up re-sending a %s alert older than 24h", r["kind"])
                continue
            buttons = None
            if r["buttons"] and r["incident_id"] is not None and await self.db.incident_is_open(r["incident_id"]):
                # Buttons only make sense while the incident is still open.
                buttons = [[tuple(b) for b in row] for row in json.loads(r["buttons"])]
            ok, err = await self.notifier.send(format_delayed(r["body"], r["sent_at"]), buttons)
            if ok:
                await self.db.mark_notification_delivered(r["id"])
                log.info("delivered a delayed %s alert after %d earlier attempt(s)", r["kind"], r["attempts"] + 1)
            else:
                delay = min(self.RETRY_MAX_DELAY_S, 60 * 2 ** (r["attempts"] + 1))
                await self.db.reschedule_notification(r["id"], delay, err)
                break

    async def _send_reminders(self, now: datetime) -> None:
        """FR-13: re-alert for incidents still open and unacknowledged."""
        if not self.reminder_minutes:
            return
        for r in await self.db.due_reminders(self.reminder_minutes):
            text = format_reminder(r["name"], r["host"], r["severity"], r["started_at"], now)
            await self._notify(
                r["incident_id"], "REMINDER", text, alert_buttons(r["incident_id"], r["branch_id"]),
                branch_id=r["branch_id"],
            )
