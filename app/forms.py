"""Parse the URL box. Slugs expand into mock-market links."""

from __future__ import annotations

import re

_SLUG = re.compile(r"[a-z0-9-]{1,80}")


def parse_targets(raw: str, public_base: str) -> list[str]:
    """Turn a textarea into absolute listing URLs.

    Blank lines are skipped. A bare slug becomes ``{public_base}/market/{slug}``.
    """
    found: list[str] = []
    base = public_base.rstrip("/")
    for line in raw.splitlines():
        item = line.strip()
        if not item:
            continue
        if item.startswith("http://") or item.startswith("https://"):
            found.append(item)
        elif item.startswith("/market/"):
            found.append(base + item)
        elif _SLUG.fullmatch(item):
            found.append(f"{base}/market/{item}")
        else:
            found.append(item)
    return found
