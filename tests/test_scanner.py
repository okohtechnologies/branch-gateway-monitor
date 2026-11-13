import asyncio

import httpx
import pytest
import pytest_asyncio

from ipmon import auth
from ipmon.api import create_app
from ipmon.runner import Monitor
from ipmon.scanner import (
    Busy, HostResult, ScanManager, TargetError, parse_arp, is_private_mac, parse_proc_arp, parse_target, probe_host, vendor_for,
)
from tests.test_api import FakeNotifier, Script, login, make_settings


# --- target parsing ------------------------------------------------------------


def test_single_ip():
    assert [str(h) for h in parse_target(" 192.168.1.5 ")] == ["192.168.1.5"]


def test_cidr_skips_network_and_broadcast():
    hosts = parse_target("192.168.1.0/30")
    assert [str(h) for h in hosts] == ["192.168.1.1", "192.168.1.2"]


def test_cidr_with_host_bits_is_accepted():
    assert len(parse_target("10.0.0.77/24")) == 254


def test_range_and_shorthand():
    assert len(parse_target("10.0.0.10-10.0.0.20")) == 11
    assert [str(h) for h in parse_target("10.0.0.10-12")] == ["10.0.0.10", "10.0.0.11", "10.0.0.12"]


@pytest.mark.parametrize("bad", [
    "", "   ", "banana", "300.1.1.1", "10.0.0.0/33", "10.0.0.20-10.0.0.10",
    "8.8.8.8", "8.8.8.0/24", "127.0.0.1", "169.254.1.1", "224.0.0.1", "0.0.0.0",
    "10.0.0.0/8", "10.0.0.0/21", "10.0.0.1-10.0.8.1", "::1", "10.0.0.1-8.8.8.8",
])
def test_rejected_targets(bad):
    with pytest.raises(TargetError):
        parse_target(bad)


def test_largest_allowed_is_a_slash_22():
    assert len(parse_target("10.0.0.0/22")) == 1022


# --- arp parsing ---------------------------------------------------------------


def test_parse_arp_windows_and_linux():
    win = "  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic\n  192.168.1.255         ff-ff-ff-ff-ff-ff     static\n  224.0.0.22  01-00-5e-00-00-16  static"
    assert parse_arp(win) == {"192.168.1.1": "AA:BB:CC:DD:EE:FF"}
    linux = "router (192.168.1.1) at aa:bb:cc:dd:ee:01 [ether] on eth0\n? (192.168.1.9) at <incomplete> on eth0"
    assert parse_arp(linux) == {"192.168.1.1": "AA:BB:CC:DD:EE:01"}


def test_parse_proc_arp_skips_incomplete_entries():
    text = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "172.17.0.1       0x1         0x2         02:42:ac:11:00:01     *        eth0\n"
        "172.17.0.9       0x1         0x0         00:00:00:00:00:00     *        eth0\n"
    )
    assert parse_proc_arp(text) == {"172.17.0.1": "02:42:AC:11:00:01"}


# --- manufacturer lookup -------------------------------------------------------


def test_vendor_lookup_known_prefix_in_any_format():
    assert "HUAWEI" in vendor_for("10:C3:AB:46:95:9D")
    assert vendor_for("10-c3-ab-46-95-9d") == vendor_for("10:C3:AB:00:00:00")


def test_randomized_macs_are_flagged_and_have_no_vendor():
    for mac in ("8A:4C:EF:A3:D1:D4", "96:C8:50:4B:38:99", "AE:07:BA:E3:0C:C7", "02:42:AC:11:00:01"):
        assert is_private_mac(mac) and vendor_for(mac) is None
    assert not is_private_mac("10:C3:AB:46:95:9D")


def test_garbage_gives_none():
    assert vendor_for("not-a-mac") is None
    assert vendor_for("") is None


# --- probing real sockets ------------------------------------------------------


async def test_probe_host_sees_open_port_and_refused_means_alive(monkeypatch):
    async def handler(r, w):
        w.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    open_port = server.sockets[0].getsockname()[1]
    closed = await asyncio.start_server(handler, "127.0.0.1", 0)
    closed_port = closed.sockets[0].getsockname()[1]
    closed.close()
    await closed.wait_closed()

    async def no_icmp(ip, timeout):
        return None

    monkeypatch.setattr("ipmon.scanner._icmp", no_icmp)
    async with server:
        r = await probe_host("127.0.0.1", (open_port, closed_port), timeout=1)
    assert r.status == "online"
    assert r.ports == [open_port]  # the refused port proves the host is up but is not "open"


async def test_probe_host_silent_is_free(monkeypatch):
    async def no_icmp(ip, timeout):
        return None

    async def timeout_tcp(ip, port, timeout):
        return False, False

    monkeypatch.setattr("ipmon.scanner._icmp", no_icmp)
    monkeypatch.setattr("ipmon.scanner._tcp", timeout_tcp)
    assert (await probe_host("10.0.0.9", (80,))).status == "free"


# --- manager -------------------------------------------------------------------


def manager(used=("10.0.0.2", "10.0.0.4"), gate=None):
    async def probe(ip, ports):
        if gate:
            await gate.wait()
        return HostResult(ip, "online" if ip in used else "free", 1.5 if ip in used else None, [80] if ip in used else [])

    async def resolve(ip):
        return f"host-{ip.split('.')[-1]}"

    async def arp():
        return {"10.0.0.2": "10:C3:AB:00:00:02", "10.0.0.4": "8A:00:00:00:00:04"}

    return ScanManager(probe_fn=probe, resolve_fn=resolve, arp_fn=arp)


async def finish(m, scan):
    for _ in range(200):
        if scan.state != "running":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("scan did not finish")


async def test_scan_collects_online_and_free_with_names_and_macs():
    m = manager()
    scan = m.start("10.0.0.0/29")
    await finish(m, scan)
    v = scan.view()
    assert (v["state"], v["total"], v["done"], v["online"], v["offline"], v["free"]) == ("done", 6, 6, 2, 0, 4)
    by_ip = {r["ip"]: r for r in v["results"]}
    assert by_ip["10.0.0.2"]["hostname"] == "host-2" and by_ip["10.0.0.2"]["mac"] == "10:C3:AB:00:00:02"
    assert "HUAWEI" in by_ip["10.0.0.2"]["vendor"] and by_ip["10.0.0.2"]["mac_private"] is False
    assert by_ip["10.0.0.4"]["vendor"] is None and by_ip["10.0.0.4"]["mac_private"] is True
    assert by_ip["10.0.0.3"]["status"] == "free" and by_ip["10.0.0.3"]["hostname"] is None


async def test_view_since_returns_only_new_rows():
    m = manager()
    scan = m.start("10.0.0.0/29")
    await finish(m, scan)
    first = scan.view()
    assert scan.view(first["next"])["results"] == []
    assert len(scan.view(4)["results"]) == 2
    assert len(scan.view(-5)["results"]) == 6


async def test_only_one_scan_at_a_time_and_cancel():
    gate = asyncio.Event()
    m = manager(gate=gate)
    scan = m.start("10.0.0.0/30")
    with pytest.raises(Busy):
        m.start("10.0.0.1")
    assert m.cancel(scan.id)
    await finish(m, scan)
    assert scan.state == "cancelled"
    m.start("10.0.0.1")  # free again
    gate.set()
    await m.shutdown()


async def test_probe_crash_fails_the_scan_cleanly():
    async def boom(ip, ports):
        raise RuntimeError("kaput")

    m = ScanManager(probe_fn=boom, resolve_fn=None, arp_fn=None)
    scan = m.start("10.0.0.1")
    await finish(m, scan)
    assert scan.state == "failed" and "kaput" in scan.error


async def test_single_ip_uses_the_longer_port_list():
    seen = []

    async def probe(ip, ports):
        seen.append(ports)
        return HostResult(ip, "free")

    m = ScanManager(probe_fn=probe, resolve_fn=None, arp_fn=lambda: asyncio.sleep(0, {}))
    await finish(m, m.start("10.0.0.1"))
    await finish(m, m.start("10.0.0.1-2"))
    assert len(seen[0]) > len(seen[1])


# --- api -----------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db):
    mon = Monitor(db, FakeNotifier(), confirm=3, probe_fn=Script())
    app = create_app(make_settings(), db=db, monitor=mon, run_monitor=False, static_dir=None, scanner=manager())
    await db.create_user("admin@example.com", auth.hash_password("correct-horse"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_scan_endpoints_require_login(client):
    assert (await client.post("/api/scans", json={"target": "10.0.0.1"})).status_code == 401
    assert (await client.get("/api/scans/abc")).status_code == 401
    assert (await client.post("/api/scans/abc/cancel")).status_code == 401


async def test_scan_flow_over_api(client):
    await login(client)
    r = await client.post("/api/scans", json={"target": "10.0.0.0/29"})
    assert r.status_code == 201
    sid = r.json()["id"]
    for _ in range(200):
        body = (await client.get(f"/api/scans/{sid}")).json()
        if body["state"] != "running":
            break
        await asyncio.sleep(0.01)
    assert body["state"] == "done" and body["online"] == 2 and len(body["results"]) == 6


async def test_bad_target_is_a_readable_422(client):
    await login(client)
    r = await client.post("/api/scans", json={"target": "8.8.8.0/24"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "target"]
    assert "private" in r.json()["detail"][0]["msg"]


async def test_unknown_scan_is_404(client):
    await login(client)
    assert (await client.get("/api/scans/nope")).status_code == 404
    assert (await client.post("/api/scans/nope/cancel")).status_code == 404
