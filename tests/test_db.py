from datetime import datetime, timedelta, timezone

import pytest

from ipmon.engine import ProbeResult, Status

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


async def test_upsert_branch_is_idempotent_and_updates(db):
    a = await db.upsert_branch("HQ", "10.0.0.1", latency_warn_ms=100)
    b = await db.upsert_branch("HQ", "10.0.0.2", latency_warn_ms=150)
    assert a == b
    rows = await db.enabled_branches()
    assert len(rows) == 1 and rows[0]["host"] == "10.0.0.2" and rows[0]["latency_warn_ms"] == 150


async def test_disabled_branch_not_listed(db):
    await db.upsert_branch("On", "10.0.0.1")
    await db.upsert_branch("Off", "10.0.0.2", enabled=False)
    assert [r["name"] for r in await db.enabled_branches()] == ["On"]


async def test_to_config_uses_thresholds(db):
    await db.upsert_branch("HQ", "10.0.0.1", latency_warn_ms=123, loss_warn_pct=7)
    cfg = db.to_config((await db.enabled_branches())[0])
    assert (cfg.latency_warn_ms, cfg.loss_warn_pct) == (123, 7)


async def test_insert_check_branch_and_canary(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    await db.insert_check(bid, ProbeResult(20.0, 0.0), Status.UP, T0)
    await db.insert_check(None, ProbeResult(None, 100.0), Status.DOWN, T0)  # canary
    rows = await db.pool.fetch("SELECT * FROM checks ORDER BY id")
    assert [(r["branch_id"], r["status"], r["avg_latency_ms"]) for r in rows] == [
        (bid, "UP", 20.0),
        (None, "DOWN", None),
    ]


async def test_invalid_status_rejected(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    with pytest.raises(Exception):
        await db.pool.execute(
            "INSERT INTO checks (branch_id, loss_pct, status) VALUES ($1, 0, 'BOGUS')", bid
        )


async def test_incident_lifecycle(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    iid = await db.open_incident(bid, Status.DOWN, T0)
    assert await db.open_incident_id(bid) == iid
    assert await db.close_incident(bid, T0 + timedelta(minutes=5)) == iid
    assert await db.open_incident_id(bid) is None
    assert await db.close_incident(bid, T0) is None  # nothing left to close


async def test_open_incident_twice_returns_same(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    first = await db.open_incident(bid, Status.DOWN, T0)
    again = await db.open_incident(bid, Status.DOWN, T0 + timedelta(minutes=1))
    assert first == again


async def test_unique_index_blocks_second_open_incident(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    await db.open_incident(bid, Status.DOWN, T0)
    with pytest.raises(Exception):
        await db.pool.execute(
            "INSERT INTO incidents (branch_id, severity, started_at) VALUES ($1, 'DOWN', $2)", bid, T0
        )


async def test_reopen_after_close(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    first = await db.open_incident(bid, Status.DOWN, T0)
    await db.close_incident(bid, T0 + timedelta(minutes=1))
    second = await db.open_incident(bid, Status.DEGRADED, T0 + timedelta(minutes=9))
    assert second != first


async def test_set_incident_severity(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    iid = await db.open_incident(bid, Status.DOWN, T0)
    await db.set_incident_severity(iid, Status.DEGRADED)
    assert await db.pool.fetchval("SELECT severity FROM incidents WHERE id=$1", iid) == "DEGRADED"


async def test_record_notification(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    iid = await db.open_incident(bid, Status.DOWN, T0)
    await db.record_notification(iid, "DOWN", "telegram", True)
    await db.record_notification(iid, "DOWN", "telegram", False, "timeout")
    rows = await db.pool.fetch("SELECT success, error FROM notifications ORDER BY id")
    assert [(r["success"], r["error"]) for r in rows] == [(True, None), (False, "timeout")]


async def test_deleting_branch_cascades(db):
    bid = await db.upsert_branch("HQ", "10.0.0.1")
    await db.insert_check(bid, ProbeResult(20.0, 0.0), Status.UP)
    await db.open_incident(bid, Status.DOWN, T0)
    await db.pool.execute("DELETE FROM branches WHERE id=$1", bid)
    assert await db.pool.fetchval("SELECT count(*) FROM checks") == 0
    assert await db.pool.fetchval("SELECT count(*) FROM incidents") == 0
