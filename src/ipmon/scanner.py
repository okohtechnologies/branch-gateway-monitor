"""Active IP scanner: find which addresses on a network answer, and which do not.

A host is *online* if it answers an ICMP echo, a TCP connect (an actively refused connection also
proves a host is there) or, on our own subnet, ARP. A silent host is *offline* if we know it (it
answered an earlier scan, or someone marked it as assigned) and *free* if we have never seen it.
"Free" is still only "as far as we know": a silent address on its own proves nothing.

Scans run as in-memory jobs; the devices they find are remembered in the database.
"""
from __future__ import annotations

import asyncio
import gzip
import ipaddress
import logging
import re
import secrets
import shutil
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

from icmplib import ICMPLibError, async_ping

log = logging.getLogger(__name__)

MAX_HOSTS = 1024  # a /22
SWEEP_PORTS = (22, 80, 443, 445, 3389)
LOOKUP_PORTS = (21, 22, 23, 53, 80, 135, 139, 443, 445, 3389, 5900, 8080, 8443)
CONCURRENCY = 64
PROBE_TIMEOUT_S = 1.0
KEEP_SCANS = 20


class TargetError(ValueError):
    """The user's target text is not something we are willing to scan."""


def parse_target(text: str) -> list[ipaddress.IPv4Address]:
    """Turn ``10.0.0.5``, ``10.0.0.0/24`` or ``10.0.0.10-10.0.0.50`` into a list of hosts.

    Only private IPv4 space is allowed, so the scanner cannot be pointed at outside networks.
    """
    text = text.strip()
    if not text:
        raise TargetError("enter an IP address, a range like 192.168.1.10-192.168.1.50, or a subnet like 192.168.1.0/24")
    try:
        if "-" in text:
            a, b = (p.strip() for p in text.split("-", 1))
            if re.fullmatch(r"\d{1,3}", b):  # shorthand: 192.168.1.10-50
                b = a.rsplit(".", 1)[0] + "." + b
            start, end = ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)
            if end < start:
                raise TargetError("the range ends before it starts")
            count = int(end) - int(start) + 1
            if count > MAX_HOSTS:
                raise TargetError(f"that range has {count} addresses; the limit is {MAX_HOSTS} (a /22)")
            hosts = [ipaddress.IPv4Address(int(start) + i) for i in range(count)]
        elif "/" in text:
            net = ipaddress.IPv4Network(text, strict=False)
            if net.num_addresses > MAX_HOSTS * 4:  # check before expanding a /8
                raise TargetError(f"that subnet is too large; the limit is {MAX_HOSTS} addresses (a /22)")
            # Skip the network and broadcast addresses, except on /31 and /32.
            hosts = list(net.hosts()) if net.prefixlen < 31 else list(net)
            if len(hosts) > MAX_HOSTS:
                raise TargetError(f"that subnet has {len(hosts)} hosts; the limit is {MAX_HOSTS} (a /22)")
        else:
            hosts = [ipaddress.IPv4Address(text)]
    except TargetError:
        raise
    except ValueError:
        raise TargetError("not a valid IPv4 address, range or subnet") from None
    for h in (hosts[0], hosts[-1]):
        if not h.is_private or h.is_loopback or h.is_link_local or h.is_multicast or h.is_unspecified:
            raise TargetError("only private network addresses (10.x, 172.16-31.x, 192.168.x) can be scanned")
    return hosts


@dataclass
class HostResult:
    ip: str
    # "online"  answered now
    # "offline" did not answer, but is known (seen before or marked as assigned): in use, just not on
    # "free"    did not answer and has never been seen
    status: str
    latency_ms: float | None = None
    ports: list[int] = field(default_factory=list)
    hostname: str | None = None
    mac: str | None = None
    vendor: str | None = None
    mac_private: bool = False  # randomized "private" MAC (phones, modern laptops): no maker to look up
    via: str | None = None  # how an online host was found: "reply" (ping/port) or "arp" (only its ARP answer)
    label: str | None = None  # a name someone gave this address
    known: bool = False  # there is a saved record for this address
    last_seen: datetime | None = None

    def as_dict(self) -> dict:
        return {"ip": self.ip, "status": self.status, "latency_ms": self.latency_ms,
                "ports": self.ports, "hostname": self.hostname, "mac": self.mac,
                "vendor": self.vendor, "mac_private": self.mac_private, "via": self.via,
                "label": self.label, "known": self.known,
                "last_seen": self.last_seen.isoformat() if self.last_seen else None}


# --- probing -------------------------------------------------------------------


async def _icmp(ip: str, timeout: float) -> float | None:
    try:
        # Two echo requests: on a slow or lossy link a single dropped packet would make a live host look silent.
        r = await async_ping(ip, count=2, interval=0.2, timeout=timeout, privileged=False)
    except (ICMPLibError, OSError):
        return None
    return round(r.avg_rtt, 2) if r.packets_received else None


async def _tcp(ip: str, port: int, timeout: float) -> tuple[bool, bool]:
    """Returns (host_answered, port_open). A refused connection still means a host is there."""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except ConnectionRefusedError:
        return True, False
    except (OSError, asyncio.TimeoutError):
        return False, False
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return True, True


async def probe_host(ip: str, ports: tuple[int, ...], timeout: float = PROBE_TIMEOUT_S) -> HostResult:
    """Ping and port-check one address concurrently. Never raises."""
    try:
        latency, tcp = await asyncio.gather(
            _icmp(ip, timeout), asyncio.gather(*(_tcp(ip, p, timeout) for p in ports))
        )
    except Exception:  # one odd host must not abort the whole scan
        log.exception("scan probe of %s failed", ip)
        return HostResult(ip, "free")
    open_ports = sorted(p for p, (_, is_open) in zip(ports, tcp) if is_open)
    answered = latency is not None or any(a for a, _ in tcp)
    return HostResult(ip, "online" if answered else "free", latency, open_ports, via="reply" if answered else None)


# --- names ---------------------------------------------------------------------
# Two sources, asked together: the network's reverse DNS (fuller names, but Windows can take 10s+ to
# answer or give up) and NetBIOS (Windows PCs, printers and NAS boxes answer directly in about a second,
# though names are cut to 15 characters). DNS wins when it has an answer.

DNS_TIMEOUT_S = 12.0
NETBIOS_TIMEOUT_S = 1.0
# Its own pool: a lookup that is stuck waiting for DNS must not starve anything else.
_dns_pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="rdns")

# NetBIOS node status request for the wildcard name "*" (RFC 1002).
_NBSTAT = struct.pack(">HHHHHH", 0x4950, 0, 1, 0, 0, 0) + b"\x20" + b"CK" + b"AA" * 15 + b"\x00" + struct.pack(">HH", 0x21, 1)


def parse_nbstat(data: bytes) -> str | None:
    """The computer name from a NetBIOS node status reply (its unique name with suffix 0x00), or None."""
    try:
        pos = 12  # header
        if data[pos] & 0xC0 == 0xC0:
            pos += 2
        else:
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        pos += 2 + 2 + 4 + 2  # type, class, ttl, data length
        count = data[pos]
        pos += 1
        for _ in range(count):
            name = data[pos:pos + 15].decode("ascii", "replace").strip()
            suffix = data[pos + 15]
            (flags,) = struct.unpack(">H", data[pos + 16:pos + 18])
            pos += 18
            if suffix == 0 and not flags & 0x8000 and name:  # 0x8000 marks a group (workgroup) name
                return name
    except (IndexError, struct.error):
        pass
    return None


async def netbios_name(ip: str, timeout: float = NETBIOS_TIMEOUT_S) -> str | None:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        await loop.sock_sendto(sock, _NBSTAT, (ip, 137))
        data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        sock.close()
    return parse_nbstat(data)


async def dns_name(ip: str, timeout: float = DNS_TIMEOUT_S) -> str | None:
    try:
        name, _, _ = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(_dns_pool, socket.gethostbyaddr, ip), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    return name


async def resolve_name(ip: str) -> str | None:
    dns, netbios = await asyncio.gather(dns_name(ip), netbios_name(ip))
    return dns or netbios


_ARP_LINE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\)?\s+(?:at\s+)?([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})")


def parse_arp(output: str) -> dict[str, str]:
    """Read ``arp -a`` output (Windows and Linux/macOS formats) into {ip: "AA:BB:..."}."""
    out = {}
    for m in _ARP_LINE.finditer(output):
        mac = m.group(2).upper().replace("-", ":")
        if mac not in ("FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00") and not mac.startswith("01:00:5E"):
            out[m.group(1)] = mac
    return out


def parse_proc_arp(text: str) -> dict[str, str]:
    """Read Linux's /proc/net/arp (columns: IP, HW type, flags, MAC, mask, device)."""
    out = {}
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 4 and cols[2] != "0x0" and cols[3] != "00:00:00:00:00:00":  # 0x0 = incomplete entry
            out[cols[0]] = cols[3].upper()
    return out


async def read_arp() -> dict[str, str]:
    """MAC addresses of hosts on our own subnet that we have recently talked to."""
    if shutil.which("arp") is None:  # slim Linux images (our Docker image) ship without it
        try:
            with open("/proc/net/arp", encoding="ascii") as f:
                return parse_proc_arp(f.read())
        except OSError:
            return {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
    except (OSError, asyncio.TimeoutError):
        return {}
    return parse_arp(stdout.decode(errors="replace"))


# --- manufacturer lookup ---------------------------------------------------------

OUI_FILE = Path(__file__).with_name("oui.txt.gz")  # rebuilt by scripts/build_oui.py


@lru_cache(maxsize=1)
def _oui_table() -> dict[str, str]:
    try:
        with gzip.open(OUI_FILE, "rt", encoding="utf-8") as f:
            return dict(line.rstrip("\n").split("\t", 1) for line in f if "\t" in line)
    except (OSError, ValueError):
        log.warning("manufacturer list %s is missing or unreadable; MAC makers will not be shown", OUI_FILE)
        return {}


def is_private_mac(mac: str) -> bool:
    """True for a locally administered (randomized) address: bit 1 of the first byte is set."""
    try:
        return bool(int(mac[:2], 16) & 0x02)
    except ValueError:
        return False


def vendor_for(mac: str) -> str | None:
    """Manufacturer of a MAC address, or None if unknown or the address is randomized."""
    if is_private_mac(mac):
        return None
    return _oui_table().get(mac.replace(":", "").replace("-", "").upper()[:6])


# --- jobs ----------------------------------------------------------------------

ProbeFn = Callable[[str, tuple[int, ...]], Awaitable[HostResult]]


@dataclass
class Scan:
    id: str
    target: str
    total: int
    started_at: float
    state: str = "running"  # running | done | cancelled | failed
    phase: str = "scanning"  # while running: "scanning" addresses, then "naming" (waiting for name lookups)
    done: int = 0
    finished_at: float | None = None
    error: str | None = None
    results: list[HostResult] = field(default_factory=list)
    # Rows are only appended while the scan runs; at the end some may change (ARP finds devices that
    # ignore pings). Bumping `version` tells a polling client to discard what it has and re-read everything.
    version: int = 0

    def view(self, since: int = 0, version: int | None = None) -> dict:
        reset = version is not None and version != self.version
        since = 0 if reset else max(0, since)
        counts = {s: sum(1 for r in self.results if r.status == s) for s in ("online", "offline", "free")}
        return {
            "id": self.id, "target": self.target, "state": self.state, "phase": self.phase, "error": self.error,
            "total": self.total, "done": self.done, **counts,
            "elapsed_s": round((self.finished_at or time.monotonic()) - self.started_at, 1),
            "version": self.version, "reset": reset,
            "next": len(self.results), "results": [r.as_dict() for r in self.results[since:]],
        }


class Busy(Exception):
    """Another scan is already running."""


class ScanManager:
    def __init__(self, probe_fn: ProbeFn | None = None, resolve_fn=None, arp_fn=None, concurrency: int = CONCURRENCY, db=None):
        self._probe = probe_fn or probe_host
        self._resolve = resolve_fn or resolve_name
        self._arp = arp_fn or read_arp
        self._concurrency = concurrency
        self.db = db  # remembers devices between scans; without one every scan starts from nothing
        self._scans: dict[str, Scan] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def get(self, scan_id: str) -> Scan | None:
        return self._scans.get(scan_id)

    def start(self, target: str) -> Scan:
        """Validate and launch a scan. Raises TargetError or Busy."""
        hosts = parse_target(target)
        if any(s.state == "running" for s in self._scans.values()):
            raise Busy
        ports = LOOKUP_PORTS if len(hosts) == 1 else SWEEP_PORTS
        scan = Scan(secrets.token_urlsafe(8), target.strip(), len(hosts), time.monotonic())
        self._scans[scan.id] = scan
        while len(self._scans) > KEEP_SCANS:  # forget the oldest finished ones
            old = next((k for k, s in self._scans.items() if s.state != "running"), None)
            if old is None:
                break
            del self._scans[old]
        task = asyncio.create_task(self._run(scan, [str(h) for h in hosts], ports))
        self._tasks[scan.id] = task
        task.add_done_callback(lambda t: self._settle(scan, t))
        return scan

    def _settle(self, scan: Scan, task: asyncio.Task) -> None:
        """Runs however the task ended, including when it was cancelled before it ever started."""
        if scan.state == "running":
            scan.state = "cancelled" if task.cancelled() else "failed"
        scan.finished_at = scan.finished_at or time.monotonic()
        self._tasks.pop(scan.id, None)

    def cancel(self, scan_id: str) -> bool:
        task = self._tasks.get(scan_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, scan: Scan, hosts: list[str], ports: tuple[int, ...]) -> None:
        sem = asyncio.Semaphore(self._concurrency)
        known = await self._load_known(hosts)
        lookups: dict[str, asyncio.Task] = {}  # name lookups run alongside the probing, not inside it

        def lookup(ip: str) -> None:
            lookups.setdefault(ip, asyncio.create_task(self._resolve(ip)))

        async def one(ip: str) -> None:
            async with sem:
                r = await self._probe(ip, ports)
                if r.status == "online":
                    lookup(ip)  # slow, so it must not hold a probe slot
                self._apply_known(r, known.get(ip))
                scan.results.append(r)
                scan.done += 1

        try:
            await asyncio.gather(*(one(ip) for ip in hosts))
            scan.phase = "naming"
            changed = await self._add_arp(scan, lookup)
            by_ip = {r.ip: r for r in scan.results}
            names = await asyncio.gather(*lookups.values(), return_exceptions=True)  # a failed lookup is just "no name"
            for ip, name in zip(lookups, names):
                if isinstance(name, str) and name and by_ip[ip].hostname != name:
                    by_ip[ip].hostname, changed = name, True
            if changed:
                scan.version += 1  # rows the client already has were revised: it must re-read them all
            await self._remember(scan)
            scan.state = "done"
        except asyncio.CancelledError:
            scan.state = "cancelled"
        except Exception as exc:
            log.exception("scan %s failed", scan.id)
            scan.state, scan.error = "failed", str(exc) or exc.__class__.__name__
        finally:
            for t in lookups.values():
                t.cancel()  # a no-op for finished ones; stops stragglers if the scan was cancelled or failed

    async def _load_known(self, hosts: list[str]) -> dict:
        if self.db is None:
            return {}
        try:
            return await self.db.known_hosts(hosts)
        except Exception:  # remembering is a bonus: a database hiccup must not stop a scan
            log.exception("could not load remembered devices")
            return {}

    @staticmethod
    def _apply_known(r: HostResult, k) -> None:
        """Fill in what we remember about this address. An unanswered known address is offline, not free."""
        if k is None:
            return
        r.known, r.label, r.last_seen = True, k["label"], k["last_seen"]
        if r.status != "online":
            r.status = "offline"
            r.hostname, r.mac, r.vendor = k["hostname"], k["mac"], k["vendor"]
            r.mac_private = bool(k["mac"]) and is_private_mac(k["mac"])
        else:
            r.hostname = r.hostname or k["hostname"]

    async def _add_arp(self, scan: Scan, lookup) -> bool:
        """MAC addresses come from the ARP cache, which the probes just filled in.

        A device on our own subnet answers ARP even when its firewall drops pings and connections, so an
        address with an ARP entry is online even if every probe was ignored. Returns True if any row changed.
        """
        macs = await self._arp()
        changed = False
        for r in scan.results:
            mac = macs.get(r.ip)
            if not mac:
                continue
            if r.status != "online":
                r.status, r.via, r.latency_ms = "online", "arp", None
                lookup(r.ip)
            r.mac, r.mac_private, r.vendor = mac, is_private_mac(mac), vendor_for(mac)
            changed = True
        return changed

    async def _remember(self, scan: Scan) -> None:
        if self.db is None:
            return
        seen = [(r.ip, r.mac, r.hostname, r.vendor) for r in scan.results if r.status == "online"]
        if not seen:
            return
        try:
            await self.db.save_seen(seen)
        except Exception:
            log.exception("could not save scan results")
