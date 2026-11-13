"""PostgreSQL storage (asyncpg)."""
from __future__ import annotations

import json
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
        body: str | None = None,
        buttons: list | None = None,
    ) -> None:
        """Log a notification. A failed one that carries its ``body`` is queued for retry in a minute."""
        retry = (not success) and (not suppressed) and body is not None
        await self.pool.execute(
            """
            INSERT INTO notifications (incident_id, kind, channel, success, error, suppressed,
                                       body, buttons, next_attempt_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb,
                    CASE WHEN $9 THEN now() + interval '1 minute' END)
            """,
            incident_id, kind, channel, success, error, suppressed,
            body, json.dumps(buttons) if buttons is not None else None, retry,
        )

    # --- delivery retries -------------------------------------------------------------

    async def due_notification_retries(self, limit: int = 10) -> list[asyncpg.Record]:
        """Undelivered alerts whose retry time has come, oldest first (so order is preserved)."""
        return await self.pool.fetch(
            """
            SELECT id, incident_id, kind, body, buttons, attempts, sent_at
            FROM notifications
            WHERE NOT success AND NOT suppressed AND NOT gave_up AND body IS NOT NULL
              AND next_attempt_at <= now()
            ORDER BY id LIMIT $1
            """,
            limit,
        )

    async def mark_notification_delivered(self, notification_id: int) -> None:
        await self.pool.execute(
            "UPDATE notifications SET success = TRUE, error = NULL, delivered_at = now(), "
            "attempts = attempts + 1, next_attempt_at = NULL WHERE id = $1",
            notification_id,
        )

    async def reschedule_notification(self, notification_id: int, delay_s: int, error: str | None) -> None:
        await self.pool.execute(
            "UPDATE notifications SET attempts = attempts + 1, error = $3, "
            "next_attempt_at = now() + make_interval(secs => $2) WHERE id = $1",
            notification_id, float(delay_s), error,
        )

    async def give_up_notification(self, notification_id: int) -> None:
        await self.pool.execute(
            "UPDATE notifications SET gave_up = TRUE, next_attempt_at = NULL WHERE id = $1", notification_id
        )

    async def pending_alert_count(self) -> int:
        """Alerts waiting to be re-sent (shown as a warning in the dashboard)."""
        return await self.pool.fetchval(
            "SELECT count(*) FROM notifications "
            "WHERE NOT success AND NOT suppressed AND NOT gave_up AND body IS NOT NULL"
        )

    async def incident_is_open(self, incident_id: int) -> bool:
        return bool(await self.pool.fetchval("SELECT ended_at IS NULL FROM incidents WHERE id = $1", incident_id))

    # --- retention ---------------------------------------------------------------------

    async def prune_checks(self, days: int, batch: int = 10_000) -> int:
        """Delete raw checks (canary included) older than ``days``. Returns rows removed.

        Deletes in batches so a large backlog never holds a long lock on the table.
        Incidents and notifications are kept: they are small and are the audit trail.
        """
        total = 0
        while True:
            n = await self.pool.fetchval(
                """
                WITH doomed AS (
                    SELECT id FROM checks WHERE ts < now() - make_interval(days => $1) LIMIT $2
                ), d AS (DELETE FROM checks WHERE id IN (SELECT id FROM doomed) RETURNING 1)
                SELECT count(*) FROM d
                """,
                days, batch,
            )
            total += n
            if n < batch:
                return total

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
        """Create a user, or reset an existing one's password. A reset signs out every device."""
        uid = await self.pool.fetchval(
            """
            INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            RETURNING id
            """,
            email.lower(), password_hash, role,
        )
        await self.revoke_user_sessions(email)
        return uid

    # --- sessions -------------------------------------------------------------------------

    async def create_session(self, sid: str, email: str, expires_at: datetime, ip: str | None, user_agent: str | None) -> None:
        await self.pool.execute(
            "INSERT INTO sessions (id, email, expires_at, ip, user_agent) VALUES ($1, $2, $3, $4, $5)",
            sid, email.lower(), expires_at, ip, (user_agent or "")[:200] or None,
        )

    async def session_active(self, sid: str) -> bool:
        return bool(await self.pool.fetchval(
            "SELECT 1 FROM sessions WHERE id = $1 AND NOT revoked AND expires_at > now()", sid
        ))

    async def revoke_session(self, sid: str) -> None:
        await self.pool.execute("UPDATE sessions SET revoked = TRUE WHERE id = $1", sid)

    async def revoke_user_sessions(self, email: str, except_sid: str | None = None) -> int:
        """Sign a user out everywhere (optionally keeping one session). Returns how many were ended."""
        return await self.pool.fetchval(
            "WITH r AS (UPDATE sessions SET revoked = TRUE WHERE email = $1 AND NOT revoked "
            "AND ($2::text IS NULL OR id <> $2) RETURNING 1) SELECT count(*) FROM r",
            email.lower(), except_sid,
        )

    async def set_password(self, email: str, password_hash: str) -> None:
        await self.pool.execute("UPDATE users SET password_hash = $2 WHERE email = $1", email.lower(), password_hash)

    # --- settings overrides -------------------------------------------------------------

    async def settings_overrides(self) -> dict:
        rows = await self.pool.fetch("SELECT key, value FROM app_settings")
        return {r["key"]: json.loads(r["value"]) for r in rows}

    async def set_setting(self, key: str, value) -> None:
        await self.pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            key, json.dumps(value),
        )

    async def delete_setting(self, key: str) -> None:
        await self.pool.execute("DELETE FROM app_settings WHERE key = $1", key)

    # --- delivery status (Telegram panel) ----------------------------------------------------

    async def last_delivery(self) -> dict:
        """When a notification last got through, and the most recent failure (if any)."""
        delivered = await self.pool.fetchval(
            "SELECT max(COALESCE(delivered_at, sent_at)) FROM notifications WHERE success AND NOT suppressed"
        )
        failure = await self.pool.fetchrow(
            "SELECT error, sent_at FROM notifications WHERE NOT success AND NOT suppressed AND NOT gave_up "
            "AND error IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        return {
            "delivered_at": delivered,
            "last_error": failure["error"] if failure else None,
            "last_error_at": failure["sent_at"] if failure else None,
        }

    async def prune_sessions(self) -> int:
        """Forget sessions that expired or were revoked more than a week ago."""
        return await self.pool.fetchval(
            "WITH d AS (DELETE FROM sessions WHERE expires_at < now() - interval '7 days' "
            "OR (revoked AND created_at < now() - interval '7 days') RETURNING 1) SELECT count(*) FROM d"
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

    async def sparklines(self, seconds: int = 3600, points: int = 30) -> dict[int, list[dict]]:
        """Recent latency per branch, bucketed to about ``points`` values, in ONE query.

        Each value is {"v": avg latency ms or None, "d": True if any check in the bucket was DOWN}.
        """
        bucket = max(1, seconds // points)
        rows = await self.pool.fetch(
            """
            SELECT branch_id,
                   floor(extract(epoch FROM ts) / $2) AS b,
                   avg(avg_latency_ms) AS v,
                   bool_or(status = 'DOWN') AS d
            FROM checks
            WHERE branch_id IS NOT NULL AND ts > now() - make_interval(secs => $1)
            GROUP BY branch_id, b
            ORDER BY branch_id, b
            """,
            float(seconds), float(bucket),
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["branch_id"], []).append({"v": r["v"], "d": r["d"]})
        return out

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


    # --- weekly report ---------------------------------------------------------------

    async def report_checks(self, since: datetime, until: datetime) -> dict[int, tuple[int, int]]:
        """{branch_id: (checks, checks that were not DOWN)} for the period."""
        rows = await self.pool.fetch(
            """
            SELECT branch_id, count(*) AS total, count(*) FILTER (WHERE status <> 'DOWN') AS up
            FROM checks WHERE branch_id IS NOT NULL AND ts >= $1 AND ts < $2
            GROUP BY branch_id
            """,
            since, until,
        )
        return {r["branch_id"]: (r["total"], r["up"]) for r in rows}

    async def report_incidents(self, since: datetime, until: datetime) -> list[asyncpg.Record]:
        """Incidents that overlap the period, including ones still open."""
        return await self.pool.fetch(
            """
            SELECT branch_id, severity, started_at, ended_at FROM incidents
            WHERE started_at < $2 AND COALESCE(ended_at, $2) > $1
            """,
            since, until,
        )

    async def last_notification_at(self, kind: str) -> datetime | None:
        return await self.pool.fetchval("SELECT max(sent_at) FROM notifications WHERE kind = $1", kind)


    # --- ip scanner: remembered devices ---------------------------------------------

    async def known_hosts(self, ips: list[str]) -> dict[str, asyncpg.Record]:
        rows = await self.pool.fetch("SELECT * FROM known_hosts WHERE ip = ANY($1::text[])", ips)
        return {r["ip"]: r for r in rows}

    async def save_seen(self, hosts: list[tuple]) -> None:
        """Remember devices that answered: (ip, mac, hostname, vendor) each.

        Missing details never erase known ones, unless the MAC changed: then a different device now
        holds the address and the old name and maker no longer apply.
        """
        await self.pool.executemany(
            """
            INSERT INTO known_hosts (ip, mac, hostname, vendor, first_seen, last_seen)
            VALUES ($1, $2, $3, $4, now(), now())
            ON CONFLICT (ip) DO UPDATE SET
                hostname = CASE WHEN known_hosts.mac IS NOT NULL AND EXCLUDED.mac IS NOT NULL
                                     AND known_hosts.mac <> EXCLUDED.mac
                                THEN EXCLUDED.hostname ELSE COALESCE(EXCLUDED.hostname, known_hosts.hostname) END,
                vendor   = CASE WHEN known_hosts.mac IS NOT NULL AND EXCLUDED.mac IS NOT NULL
                                     AND known_hosts.mac <> EXCLUDED.mac
                                THEN EXCLUDED.vendor ELSE COALESCE(EXCLUDED.vendor, known_hosts.vendor) END,
                mac      = COALESCE(EXCLUDED.mac, known_hosts.mac),
                first_seen = COALESCE(known_hosts.first_seen, EXCLUDED.first_seen),
                last_seen  = now()
            """,
            hosts,
        )

    async def set_host_label(self, ip: str, label: str) -> None:
        """Name an address. An address that was never seen becomes 'assigned' (offline) this way."""
        await self.pool.execute(
            """
            INSERT INTO known_hosts (ip, label) VALUES ($1, $2)
            ON CONFLICT (ip) DO UPDATE SET label = EXCLUDED.label
            """,
            ip, label,
        )

    async def forget_host(self, ip: str) -> bool:
        return (await self.pool.execute("DELETE FROM known_hosts WHERE ip = $1", ip)) != "DELETE 0"


class DuplicateName(Exception):
    """A branch with this name already exists."""
