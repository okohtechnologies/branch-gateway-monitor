"""Small validators shared by the branch API and the settings API."""
from __future__ import annotations

import ipaddress
import re

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def validate_host(value: str) -> str:
    """Accept an IP address or a hostname; raise ValueError (FR-4) otherwise."""
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if re.fullmatch(r"[\d.]+", value):  # looks like an IPv4 address but is not one
        raise ValueError("invalid IP address")
    if not _HOSTNAME.match(value):
        raise ValueError("invalid IP address or hostname")
    return value
