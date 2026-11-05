"""Open one listing URL and write receipts to /out.

Proxy and URL come from /out/task.json. No environment variables are read.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

OUT = Path("/out")
EXTRACT = """
() => {
  const textOf = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.textContent || "").trim() : "";
  };
  const hidden = [];
  document.querySelectorAll("body *").forEach((el) => {
    const style = getComputedStyle(el);
    const hiddenCss = style.display === "none"
      || style.visibility === "hidden"
      || parseFloat(style.fontSize) === 0;
    if (!hiddenCss) return;
    const value = (el.textContent || "").trim();
    if (value) hidden.push(value);
  });
  const photo = document.querySelector("img.listing-photo");
  return {
    title: textOf("[data-field=title]"),
    price: textOf("[data-field=price]"),
    seller: textOf("[data-field=seller]"),
    seller_profile: textOf("[data-field=seller-profile]"),
    item_id: textOf("[data-field=item-id]"),
    condition: textOf("[data-field=condition]"),
    photo_src: photo ? (photo.getAttribute("src") || "") : "",
    payload: textOf("#seller-price-script"),
    text: document.body ? document.body.innerText : "",
    hidden_text: hidden.join("\\n"),
  };
}
"""


def main() -> None:
    """Screenshot the URL in task.json and dump DOM text beside it."""
    task = json.loads((OUT / "task.json").read_text(encoding="utf-8"))
    url = task["url"]
    proxy = task["proxy"]
    error = ""
    extracted: dict = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                proxy={"server": proxy},
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="load", timeout=25000)
            page.screenshot(path=str(OUT / "screenshot.png"), full_page=True)
            extracted = page.evaluate(EXTRACT)
            _save_photo(page, url, extracted.get("photo_src") or "")
            browser.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (OUT / "error.txt").write_text(error, encoding="utf-8")
    (OUT / "text.txt").write_text(extracted.get("text") or "", encoding="utf-8")
    (OUT / "hidden.txt").write_text(extracted.get("hidden_text") or "", encoding="utf-8")
    payload = (extracted.get("payload") or "").strip()
    if payload:
        (OUT / "payload.py").write_text(payload + "\n", encoding="utf-8")
    dom = {
        "title": extracted.get("title") or "",
        "price": extracted.get("price") or "",
        "seller": extracted.get("seller") or "",
        "seller_profile": extracted.get("seller_profile") or "",
        "item_id": extracted.get("item_id") or "",
        "condition": extracted.get("condition") or "",
        "photo_src": extracted.get("photo_src") or "",
    }
    (OUT / "dom.json").write_text(json.dumps(dom), encoding="utf-8")
    meta = {"url": url, "error": error or None}
    (OUT / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if error:
        raise SystemExit(1)


def _save_photo(page, page_url: str, src: str) -> None:
    if not src:
        return
    try:
        response = page.request.get(urljoin(page_url, src), timeout=15000)
    except Exception:
        return
    if response.ok:
        (OUT / "photo.bin").write_bytes(response.body())


if __name__ == "__main__":
    main()
