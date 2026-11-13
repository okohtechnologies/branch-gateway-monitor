"""Rebuild src/ipmon/oui.txt.gz (MAC prefix -> manufacturer) from the IEEE's public OUI list.

    curl -o oui.csv https://standards-oui.ieee.org/oui/oui.csv
    python scripts/build_oui.py oui.csv

Only the 24-bit (MA-L) assignments are kept, one "AABBCC<TAB>Name" line each.
"""
from __future__ import annotations

import csv
import gzip
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "ipmon" / "oui.txt.gz"


def main(src: str) -> None:
    rows: dict[str, str] = {}
    with open(src, newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            prefix = (rec.get("Assignment") or "").strip().upper()
            name = re.sub(r"\s+", " ", (rec.get("Organization Name") or "").strip())
            if rec.get("Registry") == "MA-L" and re.fullmatch(r"[0-9A-F]{6}", prefix) and name:
                rows[prefix] = name
    if len(rows) < 10_000:
        sys.exit(f"only {len(rows)} entries found: is that the right file?")
    text = "".join(f"{p}\t{n}\n" for p, n in sorted(rows.items()))
    with gzip.GzipFile(OUT, "wb", mtime=0) as gz:  # mtime=0 keeps the output reproducible
        gz.write(text.encode("utf-8"))
    print(f"wrote {len(rows)} entries to {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
