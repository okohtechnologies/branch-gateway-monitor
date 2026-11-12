"""Preview the dashboard with realistic FAKE data. Development only.

Runs the real API and monitor loop against a throwaway schema in your local PostgreSQL
(`demo_ui`, dropped on exit) with scripted probe results: no branches, network or Telegram
needed. Build the frontend first (`npm run build` in frontend/), then:

    .venv\\Scripts\\python scripts\\demo_server.py [--port 8123]

Open http://127.0.0.1:8123 and sign in with  admin@demo.test / demo-password-123
"""
import argparse
import asyncio
import random
import secrets
from datetime import datetime, timedelta, timezone

import uvicorn

from ipmon.api import create_app
from ipmon.auth import hash_password
from ipmon.config import Settings
from ipmon.db import Database
from ipmon.engine import ProbeResult, Status
from ipmon.probe import Target
from ipmon.runner import Monitor

SCHEMA = "demo_ui"
EMAIL, PASSWORD = "admin@demo.test", "demo-password-123"

# name, host, location, probe, port, latency profile (mean, jitter), loss %, enabled
BRANCHES = [
    ("Head Office", "10.20.0.1", "Lagos", "icmp", None, (24, 4), 0.0, True),
    ("North Depot", "10.20.1.1", "Kano", "icmp", None, (310, 45), 8.0, True),
    ("Riverside Branch", "10.20.2.1", "Port Harcourt", "icmp", None, (32, 6), 0.0, True),  # down right now
    ("Airport Kiosk", "10.20.3.1", "Abuja", "icmp", None, (61, 9), 0.0, True),
    ("Warehouse 2", "10.20.4.1", "Ibadan", "tcp", 443, (38, 5), 0.0, True),
    ("Lab Annex", "10.20.5.1", "", "icmp", None, "flapping", 0.0, True),  # stays Pending
    ("Old Store", "10.20.6.1", "Enugu", "icmp", None, (45, 6), 0.0, False),
]


class NullNotifier:
    """Pretends to send. With --telegram-down it fails, to preview undelivered-alert handling."""

    def __init__(self, ok: bool = True):
        self.ok = ok

    async def send(self, text, buttons=None):
        return (True, None) if self.ok else (False, "demo: Telegram unreachable")


async def seed(db: Database) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    ids = {}
    for name, host, loc, probe, port, *_rest, enabled in BRANCHES:
        ids[name] = await db.upsert_branch(name, host, location=loc, probe_type=probe, port=port, enabled=enabled)

    # 7 days of history, one check every 5 minutes, with realistic outages
    rng = random.Random(7)
    rows = []
    for name, *_x, prof, loss, enabled in [(b[0], *b[1:5], b[5], b[6], b[7]) for b in BRANCHES]:
        if name == "Lab Annex":
            continue
        for k in range(7 * 288, -1, -1):
            ts = now - timedelta(minutes=5 * k)
            age_min = 5 * k
            lat, ls, st = None, 0.0, "UP"
            if prof is not None:
                lat = max(1.0, rng.gauss(prof[0], prof[1]))
            if name == "Riverside Branch" and (age_min <= 14 or 175 <= age_min <= 195):
                lat, ls, st = None, 100.0, "DOWN"
            elif name == "North Depot" and age_min <= 42:
                lat, ls, st = max(1.0, rng.gauss(310, 45)), 8.0, "DEGRADED"
            elif name == "North Depot" and rng.random() < 0.01:
                lat, ls, st = lat * 4 if lat else 300.0, 0.0, "UP"
            elif name == "Head Office" and 2870 <= age_min <= 2880:
                lat, ls, st = None, 100.0, "DOWN"
            elif not enabled and age_min < 300:
                continue
            rows.append((ids[name], ts, lat, ls, st))
    await db.pool.executemany(
        "INSERT INTO checks (branch_id, ts, avg_latency_ms, loss_pct, status) VALUES ($1,$2,$3,$4,$5)", rows
    )

    def ago(minutes):
        return now - timedelta(minutes=minutes)

    # open incidents + a believable history
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at) VALUES ($1,'DOWN',$2)", ids["Riverside Branch"], ago(14))
    await db.pool.execute("INSERT INTO incidents (branch_id, severity, started_at, acknowledged_by, acknowledged_at) VALUES ($1,'DEGRADED',$2,'@alex',$3)", ids["North Depot"], ago(42), ago(30))
    for branch, sev, start, mins, ack in [
        ("Riverside Branch", "DOWN", 195, 20, None),
        ("Airport Kiosk", "DOWN", 300, 11, "@alex"),
        ("Head Office", "DOWN", 2880, 4, None),
        ("Warehouse 2", "DEGRADED", 1500, 125, "@sam"),
        ("North Depot", "DEGRADED", 4300, 35, None),
    ]:
        await db.pool.execute(
            "INSERT INTO incidents (branch_id, severity, started_at, ended_at, acknowledged_by, acknowledged_at) VALUES ($1,$2,$3,$4,$5,$6)",
            ids[branch], sev, ago(start), ago(start - mins), ack, ago(start - 2) if ack else None,
        )
    await db.create_user(EMAIL, hash_password(PASSWORD))
    return ids


def make_probe(ids: dict[str, int]):
    by_id = {ids[b[0]]: b for b in BRANCHES}
    flip = {"n": 0}

    async def probe(targets, count, timeout):
        flip["n"] += 1
        out = {}
        for t in targets:
            if t.key is None:  # canary
                out[None] = ProbeResult(round(random.gauss(80, 8), 1), 0.0)
                continue
            b = by_id[t.key]
            prof, loss = b[5], b[6]
            if b[0] == "Riverside Branch":
                out[t.key] = ProbeResult(None, 100.0)  # the scripted outage
            elif prof == "flapping":
                out[t.key] = ProbeResult(None, 100.0) if flip["n"] % 2 else ProbeResult(40.0, 0.0)
            elif prof is None:
                out[t.key] = ProbeResult(None, 100.0)
            else:
                out[t.key] = ProbeResult(round(max(1.0, random.gauss(prof[0], prof[1])), 1), loss)
        return out

    return probe


async def main(port: int, telegram_down: bool) -> None:
    real = Settings()  # only used to find your local database
    secret = secrets.token_urlsafe(48)
    settings = Settings(
        database_url=real.database_url, telegram_bot_token="demo", telegram_chat_id="0",
        secret_key=secret, _env_file=None,
    )
    admin = await Database.connect(real.database_url)
    await admin.pool.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    await admin.close()

    db = await Database.connect(real.database_url, schema=SCHEMA)
    try:
        ids = await seed(db)
        if telegram_down:  # two alerts that could not be delivered and are waiting to be retried
            for kind, text in (("DOWN", "demo DOWN alert"), ("DEGRADED", "demo DEGRADED alert")):
                await db.record_notification(None, kind, "telegram", False, "demo: Telegram unreachable", body=text)
        import ipmon.api as api_module
        api_module.WS_RECHECK_S = 2  # so a revoked session is noticed within seconds in the preview
        mon = Monitor(
            db, NullNotifier(ok=not telegram_down), interval=3, confirm=3, canary=Target(None, "1.1.1.1", "tcp", 443),
            probe_fn=make_probe(ids), reminder_minutes=None, retention_days=None,
        )
        for b in BRANCHES:  # healthy branches are already "confirmed up"; Lab Annex is not, so it stays Pending
            if b[7] and b[5] not in ("flapping", None) and b[0] != "North Depot":
                mon.engine.restore(ids[b[0]], Status.UP)
        app = create_app(settings, db=db, monitor=mon, run_monitor=True)
        print(f"\nDemo dashboard: http://127.0.0.1:{port}   sign in: {EMAIL} / {PASSWORD}\n(Ctrl+C to stop; the demo data is removed on exit)\n")
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()
    finally:
        await db.close()
        cleanup = await Database.connect(real.database_url)
        await cleanup.pool.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await cleanup.close()
        print("Demo data removed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--telegram-down", action="store_true", help="simulate undelivered Telegram alerts")
    args = ap.parse_args()
    asyncio.run(main(args.port, args.telegram_down))
