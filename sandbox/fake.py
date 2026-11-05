"""In-process stand-in used by unit tests. This is not a sandbox and must not be deployed."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.jobs import Capture, CodeOutcome
from sandbox.runner import SandboxResult

_SEQ = 0


def _sandbox(logs: str = "", exit_code: int = 0) -> SandboxResult:
    global _SEQ
    _SEQ += 1
    return SandboxResult(
        name=f"fake-{_SEQ}",
        container_id=f"fake{_SEQ:04d}",
        exit_code=exit_code,
        timed_out=False,
        oom_killed=False,
        destroyed=True,
        logs=logs,
        runtime="fake",
        host_config={"NetworkMode": "none"},
        env_names=["PATH", "HOME"],
    )


def capture_listing(*, url: str, output_dir: Path, host_output_dir: Path, root: Path, **_ignored) -> Capture:
    """Write fixture receipts for a mock-market URL without opening a browser."""
    host_output_dir.mkdir(parents=True, exist_ok=True)
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    listings = json.loads((root / "mockmarket" / "listings.json").read_text(encoding="utf-8"))
    listing = next((item for item in listings if item["slug"] == slug), None)
    if listing is None:
        return Capture(
            ok=False,
            error=f"no fixture for {slug}",
            text="",
            hidden_text="",
            dom={},
            payload="",
            screenshot=None,
            photo=None,
            sandbox=_sandbox(exit_code=1),
            proxy_name="fake-proxy",
        )
    photo_path = root / "mockmarket" / listing["photo"]
    photo_bytes = photo_path.read_bytes()
    (host_output_dir / "photo.bin").write_bytes(photo_bytes)
    png = _tiny_png()
    shot = host_output_dir / "screenshot.png"
    shot.write_bytes(png)
    hidden = ""
    payload = ""
    if listing.get("hostile") == "injection":
        hidden = (root / "mockmarket" / "payloads" / "injection.txt").read_text(encoding="utf-8")
    if listing.get("hostile") == "payload":
        payload = (root / "mockmarket" / "payloads" / "hostile_price.py").read_text(encoding="utf-8")
        (host_output_dir / "payload.py").write_text(payload, encoding="utf-8")
    dom = {
        "title": listing["title"],
        "price": f"${listing['price']}",
        "seller": listing["seller"],
        "seller_profile": listing["seller_profile"],
        "item_id": listing["item_id"],
        "condition": listing["condition"],
        "photo_src": "/market/" + listing["photo"],
    }
    text = "\n".join(
        [
            listing["title"],
            f"${listing['price']}",
            listing["seller"],
            listing["seller_profile"],
            listing["item_id"],
            listing["blurb"],
        ]
    )
    (host_output_dir / "dom.json").write_text(json.dumps(dom), encoding="utf-8")
    (host_output_dir / "text.txt").write_text(text, encoding="utf-8")
    (host_output_dir / "hidden.txt").write_text(hidden, encoding="utf-8")
    return Capture(
        ok=True,
        error="",
        text=text,
        hidden_text=hidden,
        dom=dom,
        payload=payload,
        screenshot=shot,
        photo=host_output_dir / "photo.bin",
        sandbox=_sandbox("fake browser"),
        proxy_name="fake-proxy",
    )


def run_script(
    *,
    script: str,
    host_output_dir: Path,
    extra_files: dict[str, str] | None = None,
    **_ignored,
) -> CodeOutcome:
    """Pretend to run comps code. Hostile payloads are reported as contained, not executed."""
    host_output_dir.mkdir(parents=True, exist_ok=True)
    (host_output_dir / "script.py").write_text(script, encoding="utf-8")
    if "STAGE rm" in script or "STAGE fork" in script:
        logs = "\n".join(
            [
                "STAGE rm",
                "RM_RC 1",
                "ROOT_OK True",
                "STAGE exfil",
                "CURL_RC 7",
                "CURL_ERR curl: (7) Couldn't connect to server",
                "STAGE fork",
                "FORK_BLOCKED 127",
                "STAGE loop",
            ]
        )
        return CodeOutcome(ok=False, result=None, logs=logs, chart=None, sandbox=_sandbox(logs, exit_code=137))
    if "this is not python" in script:
        return CodeOutcome(
            ok=False,
            result=None,
            logs="SyntaxError: invalid syntax",
            chart=None,
            sandbox=_sandbox("SyntaxError: invalid syntax", exit_code=1),
        )
    payload = json.loads((extra_files or {})["input.json"])
    from agent.stats import summarize

    summary = summarize(float(payload["price"]), [float(item) for item in payload["comps"]])
    (host_output_dir / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    chart = host_output_dir / "chart.png"
    chart.write_bytes(_tiny_png())
    return CodeOutcome(ok=True, result=summary, logs="fake comps", chart=chart, sandbox=_sandbox("fake comps"))


def _tiny_png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000c4944415408d763f8cfff1f000005fe02fe4c2b6c9e0000000049454e44ae426082"
    )
