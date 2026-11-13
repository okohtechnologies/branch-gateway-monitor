import asyncio

import httpx
import pytest
import pytest_asyncio

from ipmon import auth
from ipmon.api import create_app
from ipmon.runner import Monitor
import struct
import time

from ipmon.scanner import HostResult, ScanManager, parse_nbstat
from tests.test_api import FakeNotifier, Script, login, make_settings
from tests.test_scanner import finish

HUAWEI = "10:C3:AB:46:95:9D"


def mgr(db, online=(), arp=None, names=None):
    """A scanner whose network is fake: `online` answer probes, `arp` is the ARP table."""
    async def probe(ip, ports):
        if ip in online:
            return HostResult(ip, "online", 1.0, [80], via="reply")
        return HostResult(ip, "free")

    async def resolve(ip):
        return (names or {}).get(ip)

    async def arp_table():
        return arp or {}

    return ScanManager(probe_fn=probe, resolve_fn=resolve, arp_fn=arp_table, db=db)


async def scan(m, target="10.0.0.0/29"):
    s = m.start(target)
    await finish(m, s)
    assert s.state == "done", s.error
    return s, {r["ip"]: r for r in s.view()["results"]}


async def test_a_device_that_answered_before_is_offline_not_free(db):
    _, first = await scan(mgr(db, online={"10.0.0.2"}, arp={"10.0.0.2": HUAWEI}, names={"10.0.0.2": "router"}))
    assert first["10.0.0.2"]["status"] == "online" and first["10.0.0.3"]["status"] == "free"

    s, second = await scan(mgr(db))  # nothing answers this time
    assert second["10.0.0.2"]["status"] == "offline"
    r = second["10.0.0.2"]
    assert (r["hostname"], r["mac"], r["known"]) == ("router", HUAWEI, True)
    assert "HUAWEI" in r["vendor"] and r["last_seen"] is not None
    assert second["10.0.0.3"]["status"] == "free" and second["10.0.0.3"]["known"] is False
    v = s.view()
    assert (v["online"], v["offline"], v["free"]) == (0, 1, 5)


async def test_naming_a_never_seen_address_marks_it_assigned(db):
    await db.set_host_label("10.0.0.5", "Reception printer")
    _, rows = await scan(mgr(db))
    r = rows["10.0.0.5"]
    assert r["status"] == "offline" and r["label"] == "Reception printer" and r["last_seen"] is None


async def test_online_device_keeps_its_label_and_falls_back_to_the_remembered_name(db):
    await scan(mgr(db, online={"10.0.0.2"}, names={"10.0.0.2": "laptop"}))
    await db.set_host_label("10.0.0.2", "Ada's laptop")
    _, rows = await scan(mgr(db, online={"10.0.0.2"}))  # DNS gives no name this time
    r = rows["10.0.0.2"]
    assert r["status"] == "online" and r["label"] == "Ada's laptop" and r["hostname"] == "laptop"


async def test_device_that_ignores_pings_but_answers_arp_is_online(db):
    m = mgr(db, arp={"10.0.0.4": "AA:BB:CC:00:00:04"}, names={"10.0.0.4": "firewalled-pc"})
    s, rows = await scan(m)
    r = rows["10.0.0.4"]
    assert (r["status"], r["via"], r["mac"], r["hostname"]) == ("online", "arp", "AA:BB:CC:00:00:04", "firewalled-pc")
    assert r["latency_ms"] is None
    assert (await db.known_hosts(["10.0.0.4"]))["10.0.0.4"]["mac"] == "AA:BB:CC:00:00:04"  # now remembered


async def test_arp_discovery_makes_clients_reload_rows_they_already_have(db):
    s, _ = await scan(mgr(db, arp={"10.0.0.4": "AA:BB:CC:00:00:04"}))
    assert s.version == 1
    v = s.view(since=6, version=0)  # a client that polled before the final step
    assert v["reset"] is True and len(v["results"]) == 6 and v["version"] == 1
    v = s.view(since=6, version=1)  # an up-to-date client gets nothing new
    assert v["reset"] is False and v["results"] == []


async def test_no_arp_discovery_means_no_reset(db):
    s, _ = await scan(mgr(db, online={"10.0.0.2"}))
    assert s.version == 0 and s.view(since=3, version=0)["reset"] is False


async def test_arp_entries_outside_the_scan_are_ignored(db):
    _, rows = await scan(mgr(db, arp={"10.9.9.9": "AA:BB:CC:00:00:99"}))
    assert "10.9.9.9" not in rows and all(r["status"] == "free" for r in rows.values())


async def test_a_new_device_on_an_old_address_does_not_inherit_the_old_name(db):
    await db.save_seen([("10.0.0.2", "AA:AA:AA:AA:AA:01", "old-pc", "Old Maker")])
    await db.save_seen([("10.0.0.2", "AA:AA:AA:AA:AA:02", None, None)])  # different MAC
    k = (await db.known_hosts(["10.0.0.2"]))["10.0.0.2"]
    assert (k["mac"], k["hostname"], k["vendor"]) == ("AA:AA:AA:AA:AA:02", None, None)


async def test_missing_details_never_erase_what_is_known_for_the_same_device(db):
    await db.save_seen([("10.0.0.2", "AA:AA:AA:AA:AA:01", "pc", "Maker")])
    first = (await db.known_hosts(["10.0.0.2"]))["10.0.0.2"]["first_seen"]
    await db.save_seen([("10.0.0.2", None, None, None)])
    k = (await db.known_hosts(["10.0.0.2"]))["10.0.0.2"]
    assert (k["mac"], k["hostname"], k["vendor"], k["first_seen"]) == ("AA:AA:AA:AA:AA:01", "pc", "Maker", first)


async def test_a_database_failure_does_not_break_scanning():
    class Broken:
        async def known_hosts(self, ips):
            raise RuntimeError("db down")

        async def save_seen(self, rows):
            raise RuntimeError("db down")

    m = mgr(Broken(), online={"10.0.0.2"})
    s, rows = await scan(m)
    assert rows["10.0.0.2"]["status"] == "online" and rows["10.0.0.3"]["status"] == "free"


async def test_scans_without_a_database_still_work():
    s, rows = await scan(mgr(None, online={"10.0.0.2"}))
    assert rows["10.0.0.2"]["status"] == "online"


async def test_cancelled_scan_remembers_nothing(db):
    gate = asyncio.Event()

    async def probe(ip, ports):
        await gate.wait()
        return HostResult(ip, "online", 1.0)

    m = ScanManager(probe_fn=probe, resolve_fn=None, arp_fn=None, db=db)
    s = m.start("10.0.0.1-3")
    m.cancel(s.id)
    await finish(m, s)
    assert s.state == "cancelled"
    assert await db.known_hosts(["10.0.0.1", "10.0.0.2", "10.0.0.3"]) == {}


# --- names ---------------------------------------------------------------------


def nbstat_reply(entries):
    """A NetBIOS node-status reply carrying (name, suffix, flags) entries."""
    body = bytes([len(entries)])
    for name, suffix, flags in entries:
        body += name.ljust(15).encode() + bytes([suffix]) + struct.pack(">H", flags)
    body += b"\x00" * 46  # statistics block
    answer = b" " + b"CK" + b"AA" * 15 + b"\x00" + struct.pack(">HHIH", 0x21, 1, 0, len(body))
    return struct.pack(">HHHHHH", 0x4950, 0x8400, 0, 1, 0, 0) + answer + body


def test_netbios_reply_gives_the_computer_name():
    data = nbstat_reply([("WORKGROUP", 0x00, 0x8400), ("TSH-LAB", 0x00, 0x0400), ("TSH-LAB", 0x20, 0x0400)])
    assert parse_nbstat(data) == "TSH-LAB"  # skips the workgroup (group flag), takes the unique 0x00 name


def test_netbios_reply_with_only_groups_or_garbage_gives_none():
    assert parse_nbstat(nbstat_reply([("WORKGROUP", 0x00, 0x8400)])) is None
    assert parse_nbstat(nbstat_reply([])) is None
    assert parse_nbstat(b"") is None
    assert parse_nbstat(b"\x00" * 5) is None
    assert parse_nbstat(nbstat_reply([("PC", 0, 0x0400)])[:-70]) in (None, "PC")  # truncated: never raises


async def test_slow_name_lookups_do_not_hold_up_probing(db):
    async def probe(ip, ports):
        return HostResult(ip, "online", 1.0)

    async def slow_name(ip):
        await asyncio.sleep(0.3)
        return "pc-" + ip.split(".")[-1]

    async def no_arp():
        return {}

    m = ScanManager(probe_fn=probe, resolve_fn=slow_name, arp_fn=no_arp, db=db, concurrency=1)
    started = time.monotonic()
    s = m.start("10.0.0.1-5")
    await finish(m, s)
    # One probe slot and five 0.3s lookups: run inside the slot they would take 1.5s; alongside, about 0.3s.
    assert time.monotonic() - started < 1.0
    names = {r["ip"]: r["hostname"] for r in s.view()["results"]}
    assert names["10.0.0.3"] == "pc-3" and all(names.values())
    assert s.version == 1  # names arrived after rows were sent: clients must reload once


async def test_a_lookup_that_blows_up_just_means_no_name(db):
    async def explode(ip):
        raise RuntimeError("resolver crashed")

    m = ScanManager(probe_fn=lambda ip, ports: asyncio.sleep(0, HostResult(ip, "online", 1.0)),
                    resolve_fn=explode, arp_fn=lambda: asyncio.sleep(0, {}), db=db)
    s, rows = await scan(m, "10.0.0.1")
    assert rows["10.0.0.1"]["status"] == "online" and rows["10.0.0.1"]["hostname"] is None


async def test_cancelling_while_names_are_pending_stops_cleanly(db):
    never = asyncio.Event()

    async def hang(ip):
        await never.wait()

    m = ScanManager(probe_fn=lambda ip, ports: asyncio.sleep(0, HostResult(ip, "online", 1.0)),
                    resolve_fn=hang, arp_fn=lambda: asyncio.sleep(0, {}), db=db)
    s = m.start("10.0.0.1-3")
    for _ in range(200):
        if s.phase == "naming":
            break
        await asyncio.sleep(0.01)
    assert s.phase == "naming" and s.state == "running"
    assert s.view()["phase"] == "naming"
    m.cancel(s.id)
    await finish(m, s)
    assert s.state == "cancelled"
    assert await db.known_hosts(["10.0.0.1"]) == {}


# --- api -----------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db):
    mon = Monitor(db, FakeNotifier(), confirm=3, probe_fn=Script())
    scanner = mgr(db, online={"10.0.0.2"})
    app = create_app(make_settings(), db=db, monitor=mon, run_monitor=False, static_dir=None, scanner=scanner)
    await db.create_user("admin@example.com", auth.hash_password("correct-horse"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def poll(client, sid):
    for _ in range(200):
        body = (await client.get(f"/api/scans/{sid}")).json()
        if body["state"] != "running":
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("scan did not finish")


async def test_naming_and_forgetting_require_login(client):
    assert (await client.put("/api/scanner/hosts/10.0.0.5", json={"label": "x"})).status_code == 401
    assert (await client.delete("/api/scanner/hosts/10.0.0.5")).status_code == 401


async def test_name_an_address_then_scan_then_forget(client):
    await login(client)
    r = await client.put("/api/scanner/hosts/10.0.0.5", json={"label": "  Reception printer "})
    assert r.status_code == 200 and r.json() == {"ip": "10.0.0.5", "label": "Reception printer"}

    sid = (await client.post("/api/scans", json={"target": "10.0.0.0/29"})).json()["id"]
    body = await poll(client, sid)
    by_ip = {h["ip"]: h for h in body["results"]}
    assert by_ip["10.0.0.5"]["status"] == "offline" and by_ip["10.0.0.5"]["label"] == "Reception printer"
    assert (body["online"], body["offline"], body["free"]) == (1, 1, 4)

    assert (await client.delete("/api/scanner/hosts/10.0.0.5")).status_code == 204
    assert (await client.delete("/api/scanner/hosts/10.0.0.5")).status_code == 404  # already forgotten
    body = await poll(client, (await client.post("/api/scans", json={"target": "10.0.0.5"})).json()["id"])
    assert body["results"][0]["status"] == "free"


@pytest.mark.parametrize("ip", ["8.8.8.8", "127.0.0.1", "banana", "10.0.0.1-5", "10.0.0.300"])
async def test_only_single_private_addresses_can_be_named(client, ip):
    await login(client)
    r = await client.put(f"/api/scanner/hosts/{ip}", json={"label": "x"})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["path", "ip"]
    assert (await client.delete(f"/api/scanner/hosts/{ip}")).status_code == 422


@pytest.mark.parametrize("label", ["", "   ", "x" * 81])
async def test_bad_names_are_rejected(client, label):
    await login(client)
    assert (await client.put("/api/scanner/hosts/10.0.0.5", json={"label": label})).status_code == 422
