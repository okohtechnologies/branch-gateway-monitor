"""The weekly uptime report sent to Telegram.

Uptime is the share of checks that were not DOWN (the same definition the dashboard uses).
Downtime is the time covered by DOWN incidents; DEGRADED incidents are counted separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .notifier import humanize

REPORT_WEEKDAY = 0  # Monday
REPORT_HOUR = 8  # 08:00 in the server's time zone (set TZ in the container)
WINDOW_DAYS = 7
MAX_LINES = 25  # keeps the message well inside Telegram's 4096-character limit


def last_due(now: datetime, tz: tzinfo | None = None) -> datetime:
    """The most recent scheduled send time at or before ``now``."""
    local = now.astimezone(tz)
    due = local.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
    due -= timedelta(days=(local.weekday() - REPORT_WEEKDAY) % 7)
    if due > local:
        due -= timedelta(days=7)
    return due


@dataclass
class BranchReport:
    name: str
    checks: int
    uptime_pct: float | None  # None: no checks in the period (new, disabled or monitor was off)
    incidents: int
    down_s: float
    degraded_s: float
    longest_down_s: float
    down_now: bool


@dataclass
class Report:
    start: datetime
    end: datetime
    branches: list[BranchReport]

    @property
    def overall_pct(self) -> float | None:
        total = sum(b.checks for b in self.branches)
        if not total:
            return None
        return sum((b.uptime_pct or 0) * b.checks for b in self.branches) / total


async def build_report(db, now: datetime, days: int = WINDOW_DAYS) -> Report:
    since = now - timedelta(days=days)
    checks = await db.report_checks(since, now)
    incidents = await db.report_incidents(since, now)
    branches = []
    for b in await db.enabled_branches():
        total, up = checks.get(b["id"], (0, 0))
        down = degraded = longest = 0.0
        count = 0
        down_now = False
        for i in (i for i in incidents if i["branch_id"] == b["id"]):
            count += 1
            span = (min(i["ended_at"] or now, now) - max(i["started_at"], since)).total_seconds()
            span = max(0.0, span)
            if i["severity"] == "DOWN":
                down += span
                longest = max(longest, span)
                down_now = down_now or i["ended_at"] is None
            else:
                degraded += span
        branches.append(BranchReport(
            b["name"], total, 100.0 * up / total if total else None, count, down, degraded, longest, down_now,
        ))
    return Report(since, now, branches)


def _pct(p: float) -> str:
    return f"{p:.2f}".rstrip("0").rstrip(".") + "%"


def _line(b: BranchReport) -> str:
    if b.uptime_pct is None:
        return f"⚪ {b.name}: no data this week"
    if b.down_now:
        emoji = "🔴"
    elif b.uptime_pct >= 99.9 and b.down_s == 0:
        emoji = "🟢"
    elif b.uptime_pct >= 99:
        emoji = "🟠"
    else:
        emoji = "🔴"
    parts = [_pct(b.uptime_pct)]
    if b.incidents == 0:
        parts.append("no incidents")
    else:
        parts.append(f"{b.incidents} incident{'s' if b.incidents != 1 else ''}")
        if b.down_s:
            parts.append(f"{humanize(b.down_s)} down (longest {humanize(b.longest_down_s)})")
        if b.degraded_s:
            parts.append(f"{humanize(b.degraded_s)} degraded")
    if b.down_now:
        parts.append("DOWN NOW")
    return f"{emoji} {b.name}: " + " · ".join(parts)


FOOTER = "\n\n— OTech NetWatch · Made by Okoh Technologies"


def format_report(r: Report, tz: tzinfo | None = None) -> str:
    fmt = "%a %d %b"
    head = f"📊 Weekly report\n{r.start.astimezone(tz):{fmt}} – {r.end.astimezone(tz):{fmt}}"
    if not r.branches:
        return head + "\n\nNo branches are being monitored."
    overall = r.overall_pct
    incidents = sum(b.incidents for b in r.branches)
    down = sum(b.down_s for b in r.branches)
    summary = "Overall: " + (_pct(overall) + " uptime" if overall is not None else "no data")
    summary += f" · {incidents} incident{'s' if incidents != 1 else ''}"
    if down:
        summary += f" · {humanize(down)} total downtime"
    # Worst first, so the branches that need attention are at the top.
    ordered = sorted(r.branches, key=lambda b: (b.uptime_pct is None, b.uptime_pct if b.uptime_pct is not None else 0, b.name.lower()))
    lines = [_line(b) for b in ordered[:MAX_LINES]]
    if len(ordered) > MAX_LINES:
        lines.append(f"…and {len(ordered) - MAX_LINES} more branches (the best performing).")
    return f"{head}\n\n{summary}\n\n" + "\n".join(lines)
