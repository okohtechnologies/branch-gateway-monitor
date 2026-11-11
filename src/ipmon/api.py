"""FastAPI app: auth, branch CRUD, dashboard queries, WebSocket feed, /health."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from . import auth
from .bot import Bot
from .config import Settings
from .db import Database, DuplicateName
from .health import check_health
from .heartbeat import Heartbeat
from .notifier import TelegramNotifier
from .probe import Target
from .runner import Monitor

log = logging.getLogger(__name__)

RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


# --- models -------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253)
    location: str = Field(default="", max_length=200)
    probe_type: Literal["icmp", "tcp"] = "icmp"
    port: int | None = Field(default=None, ge=1, le=65535)
    latency_warn_ms: float = Field(default=200, gt=0, le=60000)
    loss_warn_pct: float = Field(default=20, ge=0, le=100)
    enabled: bool = True

    @field_validator("name", "host", "location")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("host")
    @classmethod
    def _valid_host(cls, v: str) -> str:  # FR-4
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        if re.fullmatch(r"[\d.]+", v):  # looks like an IPv4 address but is not one
            raise ValueError("invalid IP address")
        if not _HOSTNAME.match(v):
            raise ValueError("invalid IP address or hostname")
        return v

    @model_validator(mode="after")
    def _tcp_needs_port(self):
        if self.probe_type == "tcp" and self.port is None:
            raise ValueError("port is required for tcp probes")
        return self


# --- helpers ------------------------------------------------------------------


def _branch_dict(row, monitor: Monitor, extra: dict | None = None) -> dict:
    confirmed = monitor.engine.confirmed(row["id"])
    d = {
        "id": row["id"], "name": row["name"], "host": row["host"], "location": row["location"],
        "probe_type": row["probe_type"], "port": row["port"],
        "latency_warn_ms": row["latency_warn_ms"], "loss_warn_pct": row["loss_warn_pct"],
        "enabled": row["enabled"],
        "status": (confirmed.value if confirmed else "PENDING") if row["enabled"] else "DISABLED",
    }
    d.update(extra or {})
    return d


def _incident_dict(r) -> dict:
    return {k: r[k] for k in ("id", "branch_id", "branch_name", "severity", "started_at",
                              "ended_at", "acknowledged_by", "acknowledged_at")}


# --- app factory --------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    monitor: Monitor | None = None,
    run_monitor: bool = True,
    static_dir: Path | None = None,
) -> FastAPI:
    owned: dict = {}
    started_at = time.monotonic()

    async def _healthy(app: FastAPI) -> bool:
        return (await check_health(app.state.db, app.state.monitor, started_at))[0]

    throttle = auth.LoginThrottle()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or Settings()
        app.state.settings = cfg
        if db is None:
            owned["db"] = await Database.connect(cfg.database_url)
        app.state.db = db or owned["db"]
        if monitor is None:
            notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
            owned["notifier"] = notifier
            canary = Target(None, cfg.canary_host, cfg.canary_type, cfg.canary_port)
            owned["monitor"] = Monitor(
                app.state.db, notifier, interval=cfg.probe_interval_s, confirm=cfg.confirm,
                canary=canary, probe_count=cfg.probe_count, probe_timeout=cfg.probe_timeout_s,
                reminder_minutes=cfg.reminder_minutes or None,
            )
        app.state.monitor = monitor or owned["monitor"]
        tasks = []
        if run_monitor:
            tasks.append(asyncio.create_task(app.state.monitor.run_forever()))
            if cfg.heartbeat_url:
                hb = Heartbeat(
                    cfg.heartbeat_url,
                    lambda: _healthy(app),
                    interval=cfg.heartbeat_interval_s,
                )
                owned["heartbeat"] = hb
                tasks.append(asyncio.create_task(hb.run_forever()))
            if cfg.bot_enabled and "notifier" in owned:
                bot = Bot(owned["notifier"].api, app.state.db, app.state.monitor, {int(cfg.telegram_chat_id)})
                tasks.append(asyncio.create_task(bot.run_forever()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if "heartbeat" in owned:
                await owned["heartbeat"].aclose()
            if "notifier" in owned:
                await owned["notifier"].aclose()
            if "db" in owned:
                await owned["db"].close()

    app = FastAPI(title="Branch Gateway Monitor", lifespan=lifespan)

    # Tests construct the app without running lifespan, so state is also set eagerly.
    if db is not None and monitor is not None and settings is not None:
        app.state.settings, app.state.db, app.state.monitor = settings, db, monitor

    def current_user(request: Request) -> dict:
        user = auth.read_token(request.app.state.settings.secret_key, request.cookies.get(auth.COOKIE))
        if not user:
            raise HTTPException(401, "Not authenticated")
        return user

    # --- auth ---------------------------------------------------------------

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response):
        ip = request.client.host if request.client else "unknown"
        if throttle.blocked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again later.")
        row = await request.app.state.db.get_user(body.email)
        if not auth.verify_password(row["password_hash"] if row else None, body.password):
            throttle.record_failure(ip)
            raise HTTPException(401, "Invalid email or password")
        throttle.reset(ip)
        cfg = request.app.state.settings
        token = auth.make_token(cfg.secret_key, row["email"], row["role"], cfg.session_hours)
        response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax",
                            secure=cfg.cookie_secure, max_age=cfg.session_hours * 3600)
        return {"email": row["email"], "role": row["role"]}

    @app.post("/api/logout")
    async def logout(response: Response):
        response.delete_cookie(auth.COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    async def me(user: dict = Depends(current_user)):
        return {"email": user["sub"], "role": user["role"]}

    # --- branches -----------------------------------------------------------

    @app.get("/api/branches")
    async def list_branches(request: Request, _: dict = Depends(current_user)):
        mon: Monitor = request.app.state.monitor
        out = []
        for r in await request.app.state.db.overview():
            out.append(_branch_dict(r, mon, {
                "latency_ms": r["avg_latency_ms"], "loss_pct": r["loss_pct"],
                "last_check_at": r["last_ts"], "uptime_24h": r["uptime_24h"],
                "incident_id": r["incident_id"], "incident_started_at": r["incident_started_at"],
                "acknowledged_by": r["acknowledged_by"],
            }))
        return {"branches": out, "monitor_suspect": mon.engine.monitor_suspect}

    @app.post("/api/branches", status_code=201)
    async def create_branch(body: BranchIn, request: Request, _: dict = Depends(current_user)):
        try:
            row = await request.app.state.db.insert_branch(**body.model_dump())
        except DuplicateName:
            raise HTTPException(409, "A branch with this name already exists")
        return _branch_dict(row, request.app.state.monitor)

    @app.get("/api/branches/{branch_id}")
    async def get_branch(branch_id: int, request: Request, _: dict = Depends(current_user)):
        d: Database = request.app.state.db
        row = await d.get_branch(branch_id)
        if row is None:
            raise HTTPException(404, "Branch not found")
        snap = request.app.state.monitor.latest.get(branch_id, {})
        open_inc = await d.incidents(branch_id, open_only=True, limit=1)
        return _branch_dict(row, request.app.state.monitor, {
            "latency_ms": snap.get("avg_latency_ms"), "loss_pct": snap.get("loss_pct"),
            "uptime": await d.uptime(branch_id),
            "open_incident": _incident_dict(open_inc[0]) if open_inc else None,
        })

    @app.put("/api/branches/{branch_id}")
    async def update_branch(branch_id: int, body: BranchIn, request: Request, _: dict = Depends(current_user)):
        try:
            row = await request.app.state.db.update_branch(branch_id, **body.model_dump())
        except DuplicateName:
            raise HTTPException(409, "A branch with this name already exists")
        if row is None:
            raise HTTPException(404, "Branch not found")
        return _branch_dict(row, request.app.state.monitor)

    @app.delete("/api/branches/{branch_id}", status_code=204)
    async def delete_branch(branch_id: int, request: Request, _: dict = Depends(current_user)):
        if not await request.app.state.db.delete_branch(branch_id):
            raise HTTPException(404, "Branch not found")
        return Response(status_code=204)

    @app.get("/api/branches/{branch_id}/checks")
    async def branch_checks(branch_id: int, request: Request, range: str = "1h", _: dict = Depends(current_user)):
        if range not in RANGES:
            raise HTTPException(422, f"range must be one of {', '.join(RANGES)}")
        d: Database = request.app.state.db
        if await d.get_branch(branch_id) is None:
            raise HTTPException(404, "Branch not found")
        rows = await d.series(branch_id, RANGES[range])
        return {"range": range, "points": [
            {"ts": r["ts"], "latency_ms": r["avg_latency_ms"], "loss_pct": r["loss_pct"], "status": r["status"]}
            for r in rows]}

    # --- incidents ----------------------------------------------------------

    @app.get("/api/incidents")
    async def list_incidents(request: Request, branch_id: int | None = None, open_only: bool = False,
                             limit: int = 100, _: dict = Depends(current_user)):
        rows = await request.app.state.db.incidents(branch_id, open_only, max(1, min(limit, 500)))
        return {"incidents": [_incident_dict(r) for r in rows]}

    # --- health (unauthenticated: no secrets, used by external checks) -------

    @app.get("/health")
    async def health(request: Request):
        ok, body = await check_health(request.app.state.db, request.app.state.monitor, started_at)
        return JSONResponse(body, status_code=200 if ok else 503)

    # --- live feed ------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        cfg = websocket.app.state.settings
        if not auth.read_token(cfg.secret_key, websocket.cookies.get(auth.COOKIE)):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        mon: Monitor = websocket.app.state.monitor
        q = mon.subscribe()
        try:
            await websocket.send_json({"type": "hello"})
            while True:
                get = asyncio.create_task(q.get())
                recv = asyncio.create_task(websocket.receive_text())
                done, pending = await asyncio.wait({get, recv}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                if recv in done:
                    recv.result()  # raises WebSocketDisconnect when the client leaves
                    continue
                await websocket.send_json(get.result())
        except WebSocketDisconnect:
            pass
        finally:
            mon.unsubscribe(q)

    # --- built frontend (optional) ---------------------------------------------

    # IPMON_FRONTEND_DIST lets the container point at the built UI wherever it was copied.
    default_dist = os.environ.get("IPMON_FRONTEND_DIST") or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    dist = static_dir if static_dir is not None else Path(default_dist)
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith(("api/", "ws")):
                raise HTTPException(404)
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn ipmon.api:app_factory --factory``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
