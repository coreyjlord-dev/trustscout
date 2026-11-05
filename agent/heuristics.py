"""Cheap, local checks for hostile listing text. These are not model calls."""

from __future__ import annotations

import re

# High-confidence patterns. A bare "api key" mention is not enough on its own.
_RULES: tuple[tuple[str, str], ...] = (
    (r"ignore (all |any |previous |prior |above )?instructions", "ignore-instructions"),
    (r"\bvultr_inference_api_key\b", "asks-for-api-key"),
    (r"reveal (all |your )?secrets", "reveal-secrets"),
    (r"you are now", "role-override"),
    (r"https?://evil\.example", "exfil-host"),
    (r"\bapi[_ ]?key\b", "mentions-api-key"),
)

_STRONG = {
    "ignore-instructions",
    "asks-for-api-key",
    "reveal-secrets",
    "role-override",
    "exfil-host",
}


def injection_hits(text: str) -> list[str]:
    """Return the rule names that match ``text``."""
    lowered = text.lower()
    hits = [name for pattern, name in _RULES if re.search(pattern, lowered)]
    if "mentions-api-key" in hits and not (_STRONG & set(hits)):
        hits = [name for name in hits if name != "mentions-api-key"]
    return hits


def strip_hostile(text: str) -> str:
    """Replace lines that carry an injection hit. The guard has already seen them."""
    kept: list[str] = []
    for line in text.splitlines():
        if injection_hits(line):
            kept.append("[removed hostile line]")
        else:
            kept.append(line)
    return "\n".join(kept)
