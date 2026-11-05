"""Mock-market catalog: listings, comps, and the stock-photo hashes."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path


def load_listings(root: Path) -> list[dict]:
    """Return the demo listings in catalog order."""
    path = root / "mockmarket" / "listings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("listings.json must be a list")
    return data


def listing_by_slug(root: Path, slug: str) -> dict | None:
    """Return one listing, or None when the slug is not in the demo set."""
    for item in load_listings(root):
        if item["slug"] == slug:
            return item
    return None


def comps_for(root: Path, item_id: str) -> list[float]:
    """Return saved comparable prices for an item id."""
    path = root / "mockmarket" / "comps.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get(item_id) or []
    return [float(item) for item in values]


@lru_cache(maxsize=4)
def stock_hashes(root_str: str) -> dict[str, str]:
    """Map sha256 of each stock file to its name."""
    root = Path(root_str)
    found: dict[str, str] = {}
    stock = root / "mockmarket" / "stock"
    if not stock.exists():
        return found
    for path in sorted(stock.iterdir()):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            found[digest] = path.name
    return found


def slug_from_url(url: str) -> str:
    """Return the last path segment, which is the listing slug for mock-market URLs."""
    path = url.split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1]
