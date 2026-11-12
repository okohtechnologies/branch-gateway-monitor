"""Health of the monitor itself (FR-31), shared by /health and the heartbeat (FR-30)."""
from __future__ import annotations

import time

from .db import Database
from .runner import Monitor


async def check_health(db: Database, monitor: Monitor, started_at: float) -> tuple[bool, dict]:
    """Healthy means: the database answers AND the probe loop completed a cycle recently.

    Right after startup the loop gets a grace period to finish its first cycle.
    ``started_at`` is a ``time.monotonic()`` value.
    """
    try:
        await db.pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    age = (time.time() - monitor.last_cycle_at.timestamp()) if monitor.last_cycle_at else None
    allowed = monitor.interval * 3 + 30
    loop_ok = (age is not None and age < allowed) or (age is None and time.monotonic() - started_at < allowed)

    ok = db_ok and loop_ok
    return ok, {
        "alerts_pending": monitor.alerts_pending,
        "status": "ok" if ok else "degraded",
        "db": db_ok,
        "probe_loop": {
            "ok": loop_ok,
            "cycles": monitor.cycles,
            "last_cycle_age_s": None if age is None else round(age, 1),
        },
    }
