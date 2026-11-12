"""Status engine: classification, debouncing, and monitor-suspect detection.

Pure logic, no I/O. One call to ``Engine.process_cycle`` handles one probe
cycle (all branches plus the canary) and returns the events to act on.

Rules implemented (PRD v0.2):
  FR-10  classify a result as UP / DEGRADED / DOWN
  FR-11  confirm a status change only after ``confirm`` consecutive results
  FR-33  canary DOWN + every enabled branch DOWN (>= 2 branches) means the
         monitor is suspect: per-branch DOWN events are suppressed
  FR-34  when suspicion clears, branches still DOWN are alerted normally
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass(frozen=True)
class ProbeResult:
    avg_latency_ms: float | None  # None when no reply at all
    loss_pct: float  # 0..100


@dataclass(frozen=True)
class BranchConfig:
    id: int
    latency_warn_ms: float
    loss_warn_pct: float


def classify(result: ProbeResult, latency_warn_ms: float, loss_warn_pct: float) -> Status:
    """FR-10. DOWN: all packets lost. DEGRADED: over either threshold."""
    if result.loss_pct >= 100.0 or result.avg_latency_ms is None:
        return Status.DOWN
    if result.loss_pct > loss_warn_pct or result.avg_latency_ms > latency_warn_ms:
        return Status.DEGRADED
    return Status.UP


# --- Events -----------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    """A confirmed status change for one branch."""

    branch_id: int
    old: Status | None  # None the first time a status is confirmed
    new: Status
    result: ProbeResult


@dataclass(frozen=True)
class MonitorSuspectStarted:
    pass


@dataclass(frozen=True)
class MonitorSuspectEnded:
    pass


Event = Transition | MonitorSuspectStarted | MonitorSuspectEnded


# --- Debouncer --------------------------------------------------------------


class Debouncer:
    """FR-11. Tracks one target's confirmed status plus a pending candidate."""

    def __init__(self, confirm: int = 3):
        if confirm < 1:
            raise ValueError("confirm must be >= 1")
        self.confirm = confirm
        self.confirmed: Status | None = None
        self._candidate: Status | None = None
        self._count = 0

    def update(self, observed: Status) -> tuple[Status | None, Status] | None:
        """Feed one observation. Returns (old, new) when a change is confirmed."""
        if observed == self.confirmed:
            self._candidate, self._count = None, 0
            return None

        if observed == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = observed, 1

        if self._count >= self.confirm:
            old, self.confirmed = self.confirmed, observed
            self._candidate, self._count = None, 0
            # The very first confirmation of UP is just "we know the baseline".
            if old is None and observed == Status.UP:
                return None
            return old, observed
        return None


# --- Engine -----------------------------------------------------------------


class Engine:
    def __init__(self, confirm: int = 3):
        self.confirm = confirm
        self._branches: dict[int, Debouncer] = {}
        self._canary = Debouncer(confirm)
        self.monitor_suspect = False
        # Branch DOWN transitions swallowed while suspect: id -> (old, result)
        self._suppressed: dict[int, tuple[Status | None, ProbeResult]] = {}

    def confirmed(self, branch_id: int) -> Status | None:
        d = self._branches.get(branch_id)
        return d.confirmed if d else None

    def set_confirm(self, confirm: int) -> None:
        """Change how many consecutive results confirm a status change, for every branch, immediately."""
        if confirm < 1:
            raise ValueError("confirm must be >= 1")
        self.confirm = confirm
        self._canary.confirm = confirm
        for deb in self._branches.values():
            deb.confirm = confirm

    def reset_canary(self) -> None:
        """Forget the canary's history (its target changed or it was switched off)."""
        self._canary = Debouncer(self.confirm)

    def restore(self, branch_id: int, status: Status) -> None:
        """Seed a branch's confirmed status after a restart (e.g. from an open incident)."""
        self._branches.setdefault(branch_id, Debouncer(self.confirm)).confirmed = status

    def process_cycle(
        self,
        branches: list[BranchConfig],
        results: dict[int, ProbeResult],
        canary: ProbeResult | None,
    ) -> list[Event]:
        """Handle one probe cycle.

        ``branches`` are the *enabled* branches. Muted branches must be
        included (probing continues while muted); muting is a notification
        concern handled by the caller. ``canary`` is None if not configured.
        """
        # Forget removed or disabled branches.
        enabled = {b.id for b in branches}
        for gone in set(self._branches) - enabled:
            del self._branches[gone]
            self._suppressed.pop(gone, None)

        # 1. Update every debouncer first, so suspicion sees a consistent view.
        raw: list[Transition] = []
        for b in branches:
            res = results.get(b.id)
            if res is None:
                continue
            deb = self._branches.setdefault(b.id, Debouncer(self.confirm))
            change = deb.update(classify(res, b.latency_warn_ms, b.loss_warn_pct))
            if change:
                raw.append(Transition(b.id, change[0], change[1], res))

        if canary is not None:
            self._canary.update(classify(canary, float("inf"), 100.0))

        # 2. Decide whether the monitor itself is suspect.
        events: list[Event] = []
        was_suspect = self.monitor_suspect
        now_suspect = self._is_suspect(branches)
        self.monitor_suspect = now_suspect

        if now_suspect and not was_suspect:
            events.append(MonitorSuspectStarted())

        # 3. Emit or swallow branch transitions.
        for t in raw:
            prior = self._suppressed.get(t.branch_id)
            if prior is not None:
                # Its DOWN was never announced, so there is nothing to recover from.
                if t.new == Status.UP:
                    del self._suppressed[t.branch_id]
                    continue
                t = Transition(t.branch_id, prior[0], t.new, t.result)
            if now_suspect and t.new == Status.DOWN:
                self._suppressed[t.branch_id] = (t.old, t.result)
            else:
                self._suppressed.pop(t.branch_id, None)
                events.append(t)

        # 4. Suspicion cleared: re-evaluate branches that are still DOWN (FR-34).
        if was_suspect and not now_suspect:
            events.append(MonitorSuspectEnded())
            for bid, (old, res) in self._suppressed.items():
                if self.confirmed(bid) == Status.DOWN:
                    events.append(Transition(bid, old, Status.DOWN, res))
            self._suppressed.clear()
        elif not now_suspect:
            self._suppressed.clear()

        return events

    def _is_suspect(self, branches: list[BranchConfig]) -> bool:
        """FR-33 entry; exit when canary recovers or any branch is not DOWN."""
        if len(branches) < 2 or self._canary.confirmed != Status.DOWN:
            return False
        return all(self.confirmed(b.id) == Status.DOWN for b in branches)
