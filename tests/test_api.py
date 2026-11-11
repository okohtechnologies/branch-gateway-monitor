from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from ipmon import auth
from ipmon.api import create_app
from ipmon.config import Settings
from ipmon.engine import ProbeResult
from ipmon.runner import Monitor

SECRET = "x" * 48
GOOD = ProbeResult(20.0, 0.0)
DEAD = ProbeResult(None, 100.0)


def make_settings():
    return Settings(
        database_url="postgresql://unused", telegram_bot_token="t", telegram_chat_id="1",
        secret_key=SECRET, _env_file=None,
    )


class FakeNotifier:
    async def send(self, text, buttons=None):
        return True, None


class Script:
    def __init__(self):
        self.state = {}

    async def __call__(self, targets, count, timeout):
        return {t.key: self.state.get(t.key, GOOD) for t in targets}


@pytest_asyncio.fixture
async def env(db):
    script = Script()
    mon = Monitor(db, FakeNotifier(), confirm=3, probe_fn=script)
    app = create_app(make_settings(), db=db, monitor=mon, run_monitor=False, static_dir=None)
    await db.create_user("admin@example.com", auth.hash_password("correct-horse"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, mon, script, db


async def login(client, password="correct-horse"):
    return await client.post("/api/login", json={"email": "admin@example.com", "password": password})


BRANCH = {"name": "HQ", "host": "10.0.0.1"}


# --- auth ---------------------------------------------------------------------


async def test_login_sets_httponly_cookie_and_me_works(env):
    client, *_ = env
    r = await login(client)
    assert r.status_code == 200 and r.json()["email"] == "admin@example.com"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert (await client.get("/api/me")).json()["role"] == "admin"


async def test_wrong_password_and_unknown_email_are_indistinguishable(env):
    client, *_ = env
    bad_pw = await login(client, "nope")
    unknown = await client.post("/api/login", json={"email": "ghost@example.com", "password": "x"})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()


async def test_login_is_throttled_after_repeated_failures(env):
    client, *_ = env
    for _ in range(5):
        assert (await login(client, "nope")).status_code == 401
    assert (await login(client, "nope")).status_code == 429
    assert (await login(client)).status_code == 429  # even the right password is blocked


async def test_endpoints_require_login(env):
    client, *_ = env
    for method, url in [("get", "/api/branches"), ("post", "/api/branches"), ("get", "/api/incidents"),
                        ("get", "/api/me"), ("get", "/api/branches/1"), ("delete", "/api/branches/1")]:
        assert (await getattr(client, method)(url)).status_code == 401, url


async def test_logout_clears_session(env):
    client, *_ = env
    await login(client)
    await client.post("/api/logout")
    assert (await client.get("/api/me")).status_code == 401


async def test_forged_and_expired_tokens_rejected(env):
    client, *_ = env
    forged = auth.make_token("another-secret-" + "y" * 40, "admin@example.com", "admin", 1)
    expired = auth.make_token(SECRET, "admin@example.com", "admin", -1)
    for tok in (forged, expired, "garbage"):
        client.cookies.set(auth.COOKIE, tok)
        assert (await client.get("/api/me")).status_code == 401


# --- branch CRUD and validation (FR-1, FR-2, FR-4) ------------------------------


async def test_create_list_update_delete(env):
    client, mon, script, db = env
    await login(client)
    r = await client.post("/api/branches", json={**BRANCH, "location": "Lagos"})
    assert r.status_code == 201
    bid = r.json()["id"]
    assert r.json()["status"] == "PENDING"

    listed = (await client.get("/api/branches")).json()["branches"]
    assert [b["name"] for b in listed] == ["HQ"]

    r = await client.put(f"/api/branches/{bid}", json={**BRANCH, "name": "HQ2", "enabled": False})
    assert r.status_code == 200 and r.json()["name"] == "HQ2" and r.json()["status"] == "DISABLED"

    assert (await client.delete(f"/api/branches/{bid}")).status_code == 204
    assert (await client.get(f"/api/branches/{bid}")).status_code == 404
    assert (await client.delete(f"/api/branches/{bid}")).status_code == 404
    assert (await client.put(f"/api/branches/{bid}", json=BRANCH)).status_code == 404


async def test_duplicate_name_rejected(env):
    client, *_ = env
    await login(client)
    assert (await client.post("/api/branches", json=BRANCH)).status_code == 201
    r = await client.post("/api/branches", json={**BRANCH, "host": "10.0.0.2"})
    assert r.status_code == 409


async def test_rename_to_existing_name_rejected(env):
    client, *_ = env
    await login(client)
    await client.post("/api/branches", json=BRANCH)
    other = (await client.post("/api/branches", json={"name": "Depot", "host": "10.0.0.2"})).json()["id"]
    assert (await client.put(f"/api/branches/{other}", json=BRANCH)).status_code == 409


@pytest.mark.parametrize("host", ["999.1.1.1", "10.0.0", "bad host", "-bad.example.com", "a..b", ""])
async def test_invalid_hosts_rejected(env, host):
    client, *_ = env
    await login(client)
    assert (await client.post("/api/branches", json={"name": "X", "host": host})).status_code == 422


@pytest.mark.parametrize("host", ["10.0.0.1", "gateway.example.com", "localhost", "2001:db8::1"])
async def test_valid_hosts_accepted(env, host):
    client, *_ = env
    await login(client)
    assert (await client.post("/api/branches", json={"name": "X", "host": host})).status_code == 201


@pytest.mark.parametrize("bad", [
    {"name": "   "},
    {"probe_type": "tcp"},  # tcp needs a port
    {"probe_type": "tcp", "port": 70000},
    {"loss_warn_pct": 150},
    {"latency_warn_ms": 0},
    {"probe_type": "ping"},
])
async def test_invalid_fields_rejected(env, bad):
    client, *_ = env
    await login(client)
    assert (await client.post("/api/branches", json={**BRANCH, **bad})).status_code == 422


# --- dashboard data ----------------------------------------------------------------


async def test_overview_shows_status_latency_and_uptime(env):
    client, mon, script, db = env
    await login(client)
    bid = (await client.post("/api/branches", json=BRANCH)).json()["id"]
    for _ in range(3):
        await mon.run_cycle()
    b = (await client.get("/api/branches")).json()["branches"][0]
    assert b["status"] == "UP" and b["latency_ms"] == 20.0 and b["uptime_24h"] == 100.0
    detail = (await client.get(f"/api/branches/{bid}")).json()
    assert detail["uptime"]["24h"] == 100.0 and detail["open_incident"] is None


async def test_outage_shows_down_with_open_incident(env):
    client, mon, script, db = env
    await login(client)
    bid = (await client.post("/api/branches", json=BRANCH)).json()["id"]
    for _ in range(3):
        await mon.run_cycle()
    script.state[bid] = DEAD
    for _ in range(3):
        await mon.run_cycle()
    b = (await client.get("/api/branches")).json()["branches"][0]
    assert b["status"] == "DOWN" and b["incident_id"] is not None
    assert b["uptime_24h"] == 50.0
    incs = (await client.get("/api/incidents", params={"open_only": True})).json()["incidents"]
    assert len(incs) == 1 and incs[0]["branch_name"] == "HQ" and incs[0]["severity"] == "DOWN"
    assert (await client.get(f"/api/branches/{bid}")).json()["open_incident"]["id"] == incs[0]["id"]


async def test_checks_series_and_range_validation(env):
    client, mon, script, db = env
    await login(client)
    bid = (await client.post("/api/branches", json=BRANCH)).json()["id"]
    for _ in range(3):
        await mon.run_cycle()
    r = await client.get(f"/api/branches/{bid}/checks", params={"range": "1h"})
    pts = r.json()["points"]
    assert r.status_code == 200 and pts and pts[0]["latency_ms"] == 20.0 and pts[0]["status"] == "UP"
    assert (await client.get(f"/api/branches/{bid}/checks", params={"range": "1y"})).status_code == 422
    assert (await client.get("/api/branches/9999/checks")).status_code == 404


async def test_series_buckets_downsample_long_ranges(env):
    client, mon, script, db = env
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    now = datetime.now(timezone.utc)
    for i in range(600):  # 600 checks spread over the last 5 hours
        await db.insert_check(bid, GOOD if i % 100 else DEAD,
                              __import__("ipmon.engine", fromlist=["Status"]).Status.UP if i % 100 else
                              __import__("ipmon.engine", fromlist=["Status"]).Status.DOWN,
                              now - timedelta(seconds=i * 30))
    rows = await db.series(bid, 86400)
    assert 0 < len(rows) <= 240
    assert any(r["status"] == "DOWN" for r in rows)  # worst status survives bucketing


async def test_incidents_filter_by_branch(env):
    client, mon, script, db = env
    await login(client)
    a = (await client.post("/api/branches", json=BRANCH)).json()["id"]
    b = (await client.post("/api/branches", json={"name": "Depot", "host": "10.0.0.2"})).json()["id"]
    await db.open_incident(a, __import__("ipmon.engine", fromlist=["Status"]).Status.DOWN, datetime.now(timezone.utc))
    assert len((await client.get("/api/incidents")).json()["incidents"]) == 1
    assert (await client.get("/api/incidents", params={"branch_id": b})).json()["incidents"] == []


# --- health -----------------------------------------------------------------------


async def test_health_ok_during_startup_grace(env):
    client, *_ = env
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["db"] is True


async def test_health_reports_fresh_and_stale_loop(env):
    client, mon, *_ = env
    mon.last_cycle_at = datetime.now(timezone.utc)
    assert (await client.get("/health")).status_code == 200
    mon.last_cycle_at = datetime.now(timezone.utc) - timedelta(hours=1)
    r = await client.get("/health")
    assert r.status_code == 503 and r.json()["probe_loop"]["ok"] is False


# --- websocket -----------------------------------------------------------------------


def test_websocket_requires_auth_and_says_hello():
    mon = Monitor(None, FakeNotifier(), probe_fn=Script())
    app = create_app(make_settings(), db=object(), monitor=mon, run_monitor=False, static_dir=None)
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass
    client.cookies.set(auth.COOKIE, auth.make_token(SECRET, "a@b.c", "admin", 1))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "hello"}
    assert mon._subscribers == set()  # cleaned up on disconnect


# --- serving the built frontend ------------------------------------------------------------


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "index.html").write_text("<html>SPA</html>")
    (tmp_path / "favicon.txt").write_text("icon")
    (tmp_path.parent / "secret.txt").write_text("TOP-SECRET")  # outside the dist folder
    return tmp_path


def spa_client(dist):
    mon = Monitor(None, FakeNotifier(), probe_fn=Script())
    app = create_app(make_settings(), db=object(), monitor=mon, run_monitor=False, static_dir=dist)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_spa_serves_index_assets_and_client_routes(dist):
    async with spa_client(dist) as c:
        assert "SPA" in (await c.get("/")).text
        assert "SPA" in (await c.get("/branches/7")).text  # client-side route falls back to index
        assert (await c.get("/assets/app.js")).text == "console.log('app')"
        assert (await c.get("/favicon.txt")).text == "icon"


async def test_spa_never_swallows_api_paths(dist):
    async with spa_client(dist) as c:
        r = await c.get("/api/nope")
        assert r.status_code == 404 and "SPA" not in r.text


@pytest.mark.parametrize("path", ["/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt",
                                  "/..\secret.txt", "/%2e%2e%5csecret.txt"])
async def test_spa_blocks_path_traversal(dist, path):
    async with spa_client(dist) as c:
        r = await c.get(path)
        assert "TOP-SECRET" not in r.text
