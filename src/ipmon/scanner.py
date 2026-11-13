"""Active IP scanner: find which addresses on a network answer, and which do not.

A host counts as *used* if it answers an ICMP echo OR a TCP connect (an actively refused
connection also proves a host is there). A host that stays silent is reported as *free*, but
that is a best guess: a firewalled or powered-off machine looks the same.

Scans are one-off jobs held in memory; nothing is stored in the database.
"""
from __future__ import annotations

import asyncio
import gzip
import ipaddress
import logging
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
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
    status: str  # "used" | "free"
    latency_ms: float | None = None
    ports: list[int] = field(default_factory=list)
    hostname: str | None = None
    mac: str | None = None
    vendor: str | None = None
    mac_private: bool = False  # randomized "private" MAC (phones, modern laptops): no maker to look up

    def as_dict(self) -> dict:
        return {"ip": self.ip, "status": self.status, "latency_ms": self.latency_ms,
                "ports": self.ports, "hostname": self.hostname, "mac": self.mac,
                "vendor": self.vendor, "mac_private": self.mac_private}


# --- probing -------------------------------------------------------------------


async def _icmp(ip: str, timeout: float) -> float | None:
    try:
        r = await async_ping(ip, count=1, timeout=timeout, privileged=False)
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
    return HostResult(ip, "used" if answered else "free", latency, open_ports)


async def resolve_name(ip: str, timeout: float = 1.5) -> str | None:
    try:
        name, _, _ = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _gethostbyaddr, ip), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    return name


def _gethostbyaddr(ip: str):
    import socket

    return socket.gethostbyaddr(ip)


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
    done: int = 0
    finished_at: float | None = None
    error: str | None = None
    results: list[HostResult] = field(default_factory=list)

    def view(self, since: int = 0) -> dict:
        since = max(0, since)
        used = sum(1 for r in self.results if r.status == "used")
        return {
            "id": self.id, "target": self.target, "state": self.state, "error": self.error,
            "total": self.total, "done": self.done, "used": used, "free": self.done - used,
            "elapsed_s": round((self.finished_at or time.monotonic()) - self.started_at, 1),
            "next": len(self.results), "results": [r.as_dict() for r in self.results[since:]],
        }


class Busy(Exception):
    """Another scan is already running."""


class ScanManager:
    def __init__(self, probe_fn: ProbeFn | None = None, resolve_fn=None, arp_fn=None, concurrency: int = CONCURRENCY):
        self._probe = probe_fn or probe_host
        self._resolve = resolve_fn or resolve_name
        self._arp = arp_fn or read_arp
        self._concurrency = concurrency
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

        async def one(ip: str) -> None:
            async with sem:
                r = await self._probe(ip, ports)
                if r.status == "used":
                    r.hostname = await self._resolve(ip)
                scan.results.append(r)
                scan.done += 1

        try:
            await asyncio.gather(*(one(ip) for ip in hosts))
            # MAC addresses come from the ARP cache, which the probes above just filled in.
            macs = await self._arp()
            for r in scan.results:
                mac = macs.get(r.ip) if r.status == "used" else None
                if mac:
                    r.mac, r.mac_private, r.vendor = mac, is_private_mac(mac), vendor_for(mac)
            scan.state = "done"
        except asyncio.CancelledError:
            scan.state = "cancelled"
        except Exception as exc:
            log.exception("scan %s failed", scan.id)
            scan.state, scan.error = "failed", str(exc) or exc.__class__.__name__
