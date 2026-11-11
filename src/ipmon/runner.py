"""The monitor loop: probe, decide, store, notify, publish."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

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
    ):
        self.db = db
        self.notifier = notifier
        self.interval = interval
        self.canary = canary
        self.probe_count = probe_count
        self.probe_timeout = probe_timeout
        self._probe_fn = probe_fn
        self.reminder_minutes = reminder_minutes
        self.engine = Engine(confirm)
        self.latest: dict[int, dict] = {}  # branch_id -> last probe snapshot
        self.last_cycle_at: datetime | None = None
        self.cycles = 0
        self._subscribers: set[asyncio.Queue] = set()

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

    async def run_forever(self) -> None:
        await self.restore_state()
        while True:
            started = time.monotonic()
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("probe cycle failed; continuing")
            await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - started)))

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

        self.cycles += 1
        self.last_cycle_at = now
        for snap in snapshot:
            confirmed = self.engine.confirmed(snap["branch_id"])
            snap["status"] = confirmed.value if confirmed else "PENDING"
        self._publish({
            "type": "cycle",
            "ts": now.isoformat(),
            "monitor_suspect": self.engine.monitor_suspect,
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
        await self.db.record_notification(incident_id, kind, "telegram", ok, err)

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
