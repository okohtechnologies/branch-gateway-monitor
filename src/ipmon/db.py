"""PostgreSQL storage (asyncpg)."""
from __future__ import annotations

from datetime import datetime
from importlib import resources

import asyncpg

from .engine import BranchConfig, ProbeResult, Status


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str, schema: str | None = None) -> "Database":
        """``schema`` isolates tables (used by tests); default is ``public``."""
        settings = {"search_path": schema} if schema else None
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, server_settings=settings)
        db = cls(pool)
        if schema:
            await pool.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await db.init_schema()
        return db

    async def close(self) -> None:
        await self.pool.close()

    async def init_schema(self) -> None:
        sql = resources.files("ipmon").joinpath("schema.sql").read_text(encoding="utf-8")
        await self.pool.execute(sql)

    # --- branches ---------------------------------------------------------

    async def upsert_branch(
        self,
        name: str,
        host: str,
        *,
        location: str = "",
        probe_type: str = "icmp",
        port: int | None = None,
        latency_warn_ms: float = 200,
        loss_warn_pct: float = 20,
        enabled: bool = True,
    ) -> int:
        """Insert or update a branch by name; returns its id."""
        return await self.pool.fetchval(
            """
            INSERT INTO branches (name, host, location, probe_type, port,
                                  latency_warn_ms, loss_warn_pct, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (name) DO UPDATE SET
                host = EXCLUDED.host, location = EXCLUDED.location,
                probe_type = EXCLUDED.probe_type, port = EXCLUDED.port,
                latency_warn_ms = EXCLUDED.latency_warn_ms,
                loss_warn_pct = EXCLUDED.loss_warn_pct, enabled = EXCLUDED.enabled
            RETURNING id
            """,
            name, host, location, probe_type, port, latency_warn_ms, loss_warn_pct, enabled,
        )

    async def enabled_branches(self) -> list[asyncpg.Record]:
        return await self.pool.fetch("SELECT * FROM branches WHERE enabled ORDER BY id")

    @staticmethod
    def to_config(row: asyncpg.Record) -> BranchConfig:
        return BranchConfig(row["id"], row["latency_warn_ms"], row["loss_warn_pct"])

    # --- checks -----------------------------------------------------------

    async def insert_check(
        self,
        branch_id: int | None,
        result: ProbeResult,
        status: Status,
        ts: datetime | None = None,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO checks (branch_id, ts, avg_latency_ms, loss_pct, status)
            VALUES ($1, COALESCE($2, now()), $3, $4, $5)
            """,
            branch_id, ts, result.avg_latency_ms, result.loss_pct, status.value,
        )

    # --- incidents --------------------------------------------------------

    async def open_incident(self, branch_id: int, severity: Status, started_at: datetime) -> int:
        """Open an incident, or return the existing open one for this branch."""
        existing = await self.open_incident_id(branch_id)
        if existing is not None:
            return existing
        return await self.pool.fetchval(
            "INSERT INTO incidents (branch_id, severity, started_at) VALUES ($1, $2, $3) RETURNING id",
            branch_id, severity.value, started_at,
        )

    async def open_incident_id(self, branch_id: int) -> int | None:
        return await self.pool.fetchval(
            "SELECT id FROM incidents WHERE branch_id = $1 AND ended_at IS NULL", branch_id
        )

    async def set_incident_severity(self, incident_id: int, severity: Status) -> None:
        await self.pool.execute(
            "UPDATE incidents SET severity = $2 WHERE id = $1", incident_id, severity.value
        )

    async def close_incident(self, branch_id: int, ended_at: datetime) -> int | None:
        """Close the open incident for a branch; returns its id (None if none open)."""
        return await self.pool.fetchval(
            """
            UPDATE incidents SET ended_at = $2
            WHERE branch_id = $1 AND ended_at IS NULL RETURNING id
            """,
            branch_id, ended_at,
        )

    # --- notifications ----------------------------------------------------

    async def record_notification(
        self,
        incident_id: int | None,
        kind: str,
        channel: str,
        success: bool,
        error: str | None = None,
        suppressed: bool = False,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO notifications (incident_id, kind, channel, success, error, suppressed)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            incident_id, kind, channel, success, error, suppressed,
        )

    # --- acknowledgement (FR-17) -------------------------------------------------

    async def acknowledge_incident(self, incident_id: int, who: str) -> str:
        """Returns 'acknowledged', 'already' (someone did first), 'closed' or 'missing'."""
        row = await self.pool.fetchrow(
            """
            UPDATE incidents SET acknowledged_by = $2, acknowledged_at = now()
            WHERE id = $1 AND ended_at IS NULL AND acknowledged_at IS NULL RETURNING id
            """,
            incident_id, who,
        )
        if row:
            return "acknowledged"
        state = await self.pool.fetchrow("SELECT ended_at FROM incidents WHERE id = $1", incident_id)
        if state is None:
            return "missing"
        return "closed" if state["ended_at"] else "already"

    # --- mutes -----------------------------------------------------------------------

    async def mute_branch(self, branch_id: int, minutes: int, who: str | None = None) -> datetime:
        """Replace any active mute with a new one; returns when it ends."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM mutes WHERE branch_id = $1 AND until > now()", branch_id)
            return await conn.fetchval(
                """
                INSERT INTO mutes (branch_id, until, created_by)
                VALUES ($1, now() + make_interval(mins => $2), $3) RETURNING until
                """,
                branch_id, minutes, who,
            )

    async def unmute_branch(self, branch_id: int) -> bool:
        """Cancels the active mute by ending it now; False if none was active."""
        return await self.pool.fetchval(
            """
            WITH u AS (UPDATE mutes SET until = now() WHERE branch_id = $1 AND until > now() RETURNING 1)
            SELECT count(*) > 0 FROM u
            """,
            branch_id,
        )

    async def muted_until(self, branch_id: int) -> datetime | None:
        return await self.pool.fetchval(
            "SELECT max(until) FROM mutes WHERE branch_id = $1 AND until > now()", branch_id
        )

    # --- reminders (FR-13) ---------------------------------------------------------------

    async def due_reminders(self, interval_minutes: int) -> list[asyncpg.Record]:
        """Open, unacknowledged, unmuted incidents with no successful alert for ``interval_minutes``."""
        return await self.pool.fetch(
            """
            SELECT i.id AS incident_id, i.branch_id, i.severity, i.started_at, b.name, b.host
            FROM incidents i
            JOIN branches b ON b.id = i.branch_id
            WHERE i.ended_at IS NULL AND i.acknowledged_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM mutes m WHERE m.branch_id = i.branch_id AND m.until > now())
              AND COALESCE(
                    (SELECT max(n.sent_at) FROM notifications n
                     WHERE n.incident_id = i.id AND n.success AND NOT n.suppressed),
                    i.started_at) < now() - make_interval(mins => $1)
            """,
            interval_minutes,
        )

    # --- branch CRUD (API) --------------------------------------------------

    _BRANCH_FIELDS = ("name", "host", "location", "probe_type", "port",
                      "latency_warn_ms", "loss_warn_pct", "enabled")

    async def all_branches(self) -> list[asyncpg.Record]:
        return await self.pool.fetch("SELECT * FROM branches ORDER BY name")

    async def get_branch(self, branch_id: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM branches WHERE id = $1", branch_id)

    async def insert_branch(self, **f) -> asyncpg.Record:
        """Raises DuplicateName if the name is taken."""
        try:
            return await self.pool.fetchrow(
                """
                INSERT INTO branches (name, host, location, probe_type, port,
                                      latency_warn_ms, loss_warn_pct, enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
                """,
                *(f[k] for k in self._BRANCH_FIELDS),
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateName(f["name"]) from exc

    async def update_branch(self, branch_id: int, **f) -> asyncpg.Record | None:
        try:
            return await self.pool.fetchrow(
                """
                UPDATE branches SET name=$2, host=$3, location=$4, probe_type=$5, port=$6,
                       latency_warn_ms=$7, loss_warn_pct=$8, enabled=$9
                WHERE id=$1 RETURNING *
                """,
                branch_id, *(f[k] for k in self._BRANCH_FIELDS),
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateName(f["name"]) from exc

    async def delete_branch(self, branch_id: int) -> bool:
        return await self.pool.fetchval(
            "WITH d AS (DELETE FROM branches WHERE id=$1 RETURNING 1) SELECT count(*) > 0 FROM d",
            branch_id,
        )

    # --- users ----------------------------------------------------------------

    async def create_user(self, email: str, password_hash: str, role: str = "admin") -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            RETURNING id
            """,
            email.lower(), password_hash, role,
        )

    async def get_user(self, email: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM users WHERE email = $1", email.lower())

    # --- dashboard reads ----------------------------------------------------------

    async def overview(self) -> list[asyncpg.Record]:
        """Every branch with its latest check, 24h uptime and open incident."""
        return await self.pool.fetch(
            """
            SELECT b.*,
                   lc.ts AS last_ts, lc.avg_latency_ms, lc.loss_pct, lc.status AS last_status,
                   u.uptime_24h,
                   i.id AS incident_id, i.severity AS incident_severity,
                   i.started_at AS incident_started_at, i.acknowledged_by
            FROM branches b
            LEFT JOIN LATERAL (
                SELECT ts, avg_latency_ms, loss_pct, status FROM checks
                WHERE branch_id = b.id ORDER BY ts DESC LIMIT 1) lc ON TRUE
            LEFT JOIN LATERAL (
                SELECT 100.0 * count(*) FILTER (WHERE status <> 'DOWN') / NULLIF(count(*), 0) AS uptime_24h
                FROM checks WHERE branch_id = b.id AND ts > now() - interval '24 hours') u ON TRUE
            LEFT JOIN incidents i ON i.branch_id = b.id AND i.ended_at IS NULL
            ORDER BY b.name
            """
        )

    async def uptime(self, branch_id: int) -> dict[str, float | None]:
        row = await self.pool.fetchrow(
            """
            SELECT
              100.0 * count(*) FILTER (WHERE status <> 'DOWN' AND ts > now() - interval '24 hours')
                    / NULLIF(count(*) FILTER (WHERE ts > now() - interval '24 hours'), 0) AS d1,
              100.0 * count(*) FILTER (WHERE status <> 'DOWN' AND ts > now() - interval '7 days')
                    / NULLIF(count(*) FILTER (WHERE ts > now() - interval '7 days'), 0) AS d7,
              100.0 * count(*) FILTER (WHERE status <> 'DOWN')
                    / NULLIF(count(*), 0) AS d30
            FROM checks WHERE branch_id = $1 AND ts > now() - interval '30 days'
            """,
            branch_id,
        )
        return {"24h": row["d1"], "7d": row["d7"], "30d": row["d30"]}

    async def series(self, branch_id: int, seconds: int, points: int = 240) -> list[asyncpg.Record]:
        """Check history bucketed to about ``points`` rows: avg latency, max loss, worst status."""
        bucket = max(1, seconds // points)
        return await self.pool.fetch(
            """
            SELECT to_timestamp(floor(extract(epoch FROM ts) / $3) * $3) AS ts,
                   avg(avg_latency_ms) AS avg_latency_ms,
                   max(loss_pct) AS loss_pct,
                   CASE WHEN bool_or(status = 'DOWN') THEN 'DOWN'
                        WHEN bool_or(status = 'DEGRADED') THEN 'DEGRADED' ELSE 'UP' END AS status
            FROM checks
            WHERE branch_id = $1 AND ts > now() - make_interval(secs => $2)
            GROUP BY 1 ORDER BY 1
            """,
            branch_id, float(seconds), float(bucket),
        )

    async def incidents(self, branch_id: int | None = None, open_only: bool = False,
                        limit: int = 100) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT i.*, b.name AS branch_name FROM incidents i
            JOIN branches b ON b.id = i.branch_id
            WHERE ($1::int IS NULL OR i.branch_id = $1)
              AND (NOT $2 OR i.ended_at IS NULL)
            ORDER BY i.started_at DESC LIMIT $3
            """,
            branch_id, open_only, limit,
        )


class DuplicateName(Exception):
    """A branch with this name already exists."""
