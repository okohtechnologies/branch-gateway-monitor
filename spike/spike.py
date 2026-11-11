#!/usr/bin/env python3
"""Slice 0 spike: check what the candidate host can actually do.

Stdlib only. Run it on the machine that will host the monitor.

Usage:
  python spike.py --gateway 10.0.0.1 [--gateway 10.0.1.1 ...] \
                  [--tcp 10.0.0.1:443] [--canary 1.1.1.1]

Optional environment variables (never put these in code or Git):
  TELEGRAM_BOT_TOKEN   bot token from @BotFather
  TELEGRAM_CHAT_ID     your chat id; if both are set, one test message is sent
  HEARTBEAT_URL        e.g. https://hc-ping.com/<uuid>; one ping is sent
  HTTPS_PROXY          honoured automatically by urllib if egress needs a proxy
"""
import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def ping(host, count=4, timeout_s=2):
    """Run the system ping and parse loss and average latency."""
    if platform.system() == "Windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout_s * 1000), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_s), host]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=count * (timeout_s + 1) + 5
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}

    loss = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", out, re.I)
    avg = re.search(r"Average = (\d+)ms", out) or re.search(
        r"= [\d.]+/([\d.]+)/", out
    )
    loss_pct = float(loss.group(1)) if loss else 100.0
    return {
        "ok": loss_pct < 100.0,
        "loss_pct": loss_pct,
        "avg_ms": float(avg.group(1)) if avg else None,
    }


def tcp_check(hostport, timeout_s=2):
    host, _, port = hostport.rpartition(":")
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return {"ok": True, "ms": round((time.monotonic() - start) * 1000, 1)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def http(url, data=None, timeout_s=10):
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return {"ok": 200 <= resp.status < 300, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": str(getattr(exc, "reason", exc))}


def telegram_reachable():
    # Unauthenticated request: a 404 from the API still proves we can reach it.
    r = http("https://api.telegram.org/botinvalid/getMe")
    r["ok"] = r.get("status") in (401, 404)
    return r


def telegram_send(token, chat_id):
    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": "Branch Gateway Monitor: Slice 0 spike test"}
    ).encode()
    return http(f"https://api.telegram.org/bot{token}/sendMessage", data=body)


def report(results, name, res):
    results[name] = res
    mark = "PASS" if res.get("ok") else "FAIL"
    detail = {k: v for k, v in res.items() if k != "ok"}
    print(f"[{mark}] {name}  {detail if detail else ''}")


def in_container():
    return os.path.exists("/.dockerenv")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gateway", action="append", default=[], help="gateway IP/host (repeatable)")
    ap.add_argument("--tcp", action="append", default=[], help="host:port TCP check (repeatable)")
    ap.add_argument("--canary", default="1.1.1.1", help="external canary target")
    ap.add_argument("--json", metavar="FILE", help="also write results to FILE")
    args = ap.parse_args()

    results = {}
    print(f"Host: {platform.node()} | {platform.platform()} | container={in_container()}\n")

    print("== ICMP ==")
    for gw in args.gateway:
        report(results, f"icmp:{gw}", ping(gw))
    report(results, f"icmp:canary:{args.canary}", ping(args.canary))

    if args.tcp:
        print("\n== TCP fallback ==")
        for hp in args.tcp:
            report(results, f"tcp:{hp}", tcp_check(hp))

    print("\n== Outbound (egress) ==")
    report(results, "telegram:api-reachable", telegram_reachable())

    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        report(results, "telegram:send-message", telegram_send(token, chat))
    else:
        print("[SKIP] telegram:send-message  (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    hb = os.environ.get("HEARTBEAT_URL")
    if hb:
        report(results, "heartbeat:ping", http(hb))
    else:
        print("[SKIP] heartbeat:ping  (set HEARTBEAT_URL)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    failed = [k for k, v in results.items() if not v.get("ok")]
    print("\n== Summary ==")
    print("All checks passed." if not failed else "Failed: " + ", ".join(failed))
    if any(k.startswith("icmp:") and not k.startswith("icmp:canary") for k in failed):
        print("-> A gateway ignored ICMP: TCP fallback must be in the MVP (run with --tcp host:port).")
    print("Run once on the bare host and once inside Docker (needs --cap-add NET_RAW for raw ICMP).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
