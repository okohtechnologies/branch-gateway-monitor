"""Settings that can be changed from the dashboard while the monitor runs.

The environment (.env) supplies the DEFAULTS. A value changed in the dashboard is stored as an
override in the database and wins until it is reset. Secrets (bot token, ping URL, database
password) are deliberately not here: they stay in .env.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .validation import validate_host

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    key: str
    kind: str  # "int" | "float" | "bool" | "host" | "choice"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()


FIELDS: dict[str, Field] = {
    f.key: f
    for f in (
        # monitoring
        Field("probe_interval_s", "int", 2, 3600),
        Field("probe_count", "int", 1, 10),
        Field("probe_timeout_s", "float", 0.5, 10),
        Field("confirm", "int", 1, 20),
        Field("canary_enabled", "bool"),
        Field("canary_type", "choice", choices=("tcp", "icmp")),
        Field("canary_host", "host"),
        Field("canary_port", "int", 1, 65535),
        # alerts and data
        Field("reminder_minutes", "int", 0, 1440),
        Field("retention_days", "int", 0, 3650),
        Field("weekly_report_enabled", "bool"),
        Field("default_latency_warn_ms", "float", 1, 60000),
        Field("default_loss_warn_pct", "float", 0, 100),
    )
}


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def check(key: str, raw: Any) -> Any:
    """Validate one value and return it in its proper type. Raises ValueError with a readable reason."""
    field = FIELDS.get(key)
    if field is None:
        raise ValueError("is not a setting you can change")

    if field.kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError("must be true or false")
        return raw

    if field.kind == "choice":
        if raw not in field.choices:
            raise ValueError("must be one of: " + ", ".join(field.choices))
        return raw

    if field.kind == "host":
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("must be an IP address or hostname")
        try:
            return validate_host(raw.strip())
        except ValueError as exc:
            raise ValueError(str(exc)) from None

    # numbers
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ValueError("must be a number")
    if field.kind == "int":
        if isinstance(raw, float) and not raw.is_integer():
            raise ValueError("must be a whole number")
        raw = int(raw)
    else:
        raw = float(raw)
    if raw < field.minimum or raw > field.maximum:
        raise ValueError(f"must be between {_fmt(field.minimum)} and {_fmt(field.maximum)}")
    return raw


def defaults_from(cfg: Any) -> dict[str, Any]:
    """The values the environment provides (the Settings object), for every editable key."""
    return {key: getattr(cfg, key) for key in FIELDS}


def effective(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Defaults with valid overrides on top. A stored value that no longer validates is ignored."""
    values = dict(defaults)
    for key, raw in overrides.items():
        if key not in FIELDS:
            continue
        try:
            values[key] = check(key, raw)
        except ValueError as exc:
            log.warning("ignoring stored setting %s: %s", key, exc)
    return values


def limits() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, f in FIELDS.items():
        entry: dict[str, Any] = {"kind": f.kind}
        if f.minimum is not None:
            entry["min"], entry["max"] = f.minimum, f.maximum
        if f.choices:
            entry["choices"] = list(f.choices)
        out[key] = entry
    return out


def warnings(values: dict[str, Any]) -> list[str]:
    """Combinations that are allowed but probably not what you want."""
    out: list[str] = []
    worst = values["probe_count"] * (values["probe_timeout_s"] + 0.2)  # an unreachable branch, all pings lost
    if worst > values["probe_interval_s"]:
        out.append(
            f"Probing an unreachable branch can take up to {worst:.1f}s, longer than the {values['probe_interval_s']}s interval. "
            "Cycles will run back to back instead of on schedule. Lower the pings or the timeout, or raise the interval."
        )
    return out
