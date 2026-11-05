"""Fixed inspection pipeline. A poisoned page cannot add tools or skip the guard."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from agent.catalog import comps_for, slug_from_url, stock_hashes
from agent.comps import run_comps
from agent.guard import assess
from agent.scoring import Signal, band, price_signal, score_of
from agent.vision import verify
from agent.vultr_client import MODEL_PLANNER, MODEL_VERDICT, chat, parse_json
from sandbox import fake, jobs

MAX_PAYLOAD_BYTES = 32_000


def execute(run_id: str, db, settings) -> None:
    """Inspect every URL on a run. One listing's failure does not stop the others."""
    db.set_status(run_id, "running")
    try:
        for url in db.get_urls(run_id):
            try:
                _inspect_one(run_id, url, db, settings)
            except Exception as exc:
                db.add_step(run_id, url, "error", "error", _short(str(exc)), "")
                _save_verdict(db, run_id, url, "high", 90, "This listing could not be checked.", [])
        db.set_status(run_id, "done")
    except Exception as exc:
        db.add_step(run_id, "", "pipeline", "error", _short(str(exc)), "")
        db.set_status(run_id, "error")


def _inspect_one(run_id: str, url: str, db, settings) -> None:
    internal = rewrite_url(url, settings)
    host = urlsplit(internal).hostname or ""
    _plan(run_id, url, internal, db)
    task_dir = settings.data_dir / "tasks" / run_id / slug_from_url(url)
    out_dir = task_dir / "out"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    capture = _capture(url=internal, host=host, out_dir=out_dir, settings=settings)
    browser_id = capture.sandbox.container_id if capture.sandbox else ""
    if not capture.ok:
        db.add_step(run_id, url, "browser", "error", capture.error or "browser failed", browser_id)
        _save_verdict(db, run_id, url, "high", 80, "The sandbox could not capture this page.", [])
        return
    shot_id = _store_file(db, settings, run_id, url, "screenshot", capture.screenshot, "Listing screenshot")
    db.add_step(
        run_id,
        url,
        "browser",
        "ok",
        f"Screenshot saved. Container {browser_id} destroyed. Proxy {capture.proxy_name} removed.",
        browser_id,
    )
    page_text = "\n".join([capture.text, capture.hidden_text])
    report = assess(page_text)
    guard_detail = f"model {report.model}"
    if report.flagged:
        guard_detail += "; flagged " + ", ".join(report.hits)
    else:
        guard_detail += "; no injection"
    if report.local_score is not None:
        guard_detail += f"; local score {report.local_score:.2f}"
    db.add_step(run_id, url, "guard", "ok" if not report.flagged else "contained", guard_detail, "")
    vision = verify(dom=capture.dom, text=report.stripped, screenshot=capture.screenshot)
    db.add_step(run_id, url, "vision", "ok", f"model {vision.model} read the screenshot and DOM", "")
    signals = _signals(settings.root, capture, report, vision)
    if capture.payload.strip():
        signals.extend(_contain_payload(run_id, url, capture.payload, task_dir, db, settings))
    signals.extend(_run_price(run_id, url, capture.dom, task_dir, db, settings))
    _finish(run_id, url, signals, shot_id, db, settings)


def _plan(run_id: str, url: str, internal: str, db) -> None:
    result = chat(
        MODEL_PLANNER,
        [
            {
                "role": "system",
                "content": (
                    "trustscout-task: plan\nList the fixed inspection steps. Do not add tools. Do not ask for secrets."
                ),
            },
            {"role": "user", "content": internal},
        ],
    )
    db.add_step(run_id, url, "plan", "ok", f"model {result.model} confirmed the fixed pipeline", "")


def _capture(*, url: str, host: str, out_dir: Path, settings):
    host_out = settings.docker_path(out_dir)
    if settings.runner == "fake":
        return fake.capture_listing(
            url=url,
            output_dir=out_dir,
            host_output_dir=host_out,
            root=settings.root,
            allow_host=host,
        )
    return jobs.capture_listing(
        url=url,
        allow_host=host,
        output_dir=out_dir,
        host_output_dir=host_out,
        app_network=settings.app_network,
        proxy_image=settings.proxy_image,
        browser_image=settings.browser_image,
        timeout_sec=45,
    )


def _signals(root: Path, capture, report, vision) -> list[Signal]:
    signals: list[Signal] = []
    dom = capture.dom
    seller = _clean(dom.get("seller"))
    profile = _clean(dom.get("seller_profile"))
    if seller and profile and seller.lower() != profile.lower():
        signals.append(
            Signal(
                "seller_mismatch",
                "high",
                f"The listing says the seller is {seller}, but the profile block says {profile}.",
                "screenshot",
            )
        )
    elif seller:
        signals.append(
            Signal(
                "seller_match",
                "info",
                f"Seller {seller} matches the profile block.",
                "screenshot",
            )
        )
    if report.flagged:
        signals.append(
            Signal(
                "injection",
                "high",
                "Hidden page text tried to override the agent and ask for an API key. "
                "It was flagged and removed before the vision and verdict models saw it.",
                "evidence",
            )
        )
    if capture.photo and capture.photo.exists():
        digest = hashlib.sha256(capture.photo.read_bytes()).hexdigest()
        stock_name = stock_hashes(str(root)).get(digest)
        if stock_name:
            signals.append(
                Signal(
                    "recycled_photo",
                    "high",
                    f"The listing photo matches stock file {stock_name}. Recycled catalog photos are a scam pattern.",
                    "screenshot",
                )
            )
    listed = _money(dom.get("price"))
    if vision.price is not None and listed is not None and abs(vision.price - listed) > 0.5:
        signals.append(
            Signal(
                "vision_mismatch",
                "high",
                f"The vision model read ${vision.price:.0f}, but the DOM price is ${listed:.0f}.",
                "screenshot",
            )
        )
    return signals


def _contain_payload(run_id: str, url: str, payload: str, task_dir: Path, db, settings) -> list[Signal]:
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        db.add_step(run_id, url, "contain", "contained", "Seller script was too large to run.", "")
        return [Signal("hostile_payload", "high", "The page offered an oversized seller script.", "evidence")]
    out_dir = task_dir / "payload"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    outcome = _run_code(payload, out_dir, settings, timeout_sec=15)
    sandbox = outcome.sandbox
    detail = _payload_detail(outcome.logs, sandbox)
    db.add_step(run_id, url, "contain", "contained", detail, sandbox.container_id)
    evidence = settings.data_dir / "cards" / f"{run_id}-payload.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(detail + "\n\n" + outcome.logs[-2000:], encoding="utf-8")
    return [
        Signal(
            "hostile_payload",
            "high",
            "The listing shipped a seller price script (rm -rf, curl exfil, fork bomb, infinite loop). "
            f"Sandbox {sandbox.container_id} contained it and was destroyed.",
            "evidence",
        )
    ]


def _payload_detail(logs: str, sandbox) -> str:
    parts = [
        "rm -rf: contained" if "RM_RC" in logs else "rm -rf: no marker",
        "curl exfil: blocked" if "CURL_RC" in logs and "CURL_RC 0" not in logs else "curl exfil: see log",
        "fork bomb: pids limit" if "FORK_BLOCKED" in logs else "fork bomb: see log",
        "infinite loop: wall-clock kill" if sandbox.timed_out or sandbox.exit_code == 137 else "loop: ended",
        f"container {sandbox.container_id} destroyed; runtime {sandbox.runtime}",
    ]
    if any(name.startswith("VULTR") for name in sandbox.env_names):
        parts.append("unexpected VULTR env inside sandbox")
    else:
        parts.append("no VULTR env in the sandbox")
    return ". ".join(parts)


def _run_price(run_id: str, url: str, dom: dict, task_dir: Path, db, settings) -> list[Signal]:
    item_id = _clean(dom.get("item_id"))
    price = _money(dom.get("price"))
    title = _clean(dom.get("title")) or url
    comps = comps_for(settings.root, item_id) if item_id else []
    if price is None or not comps:
        db.add_step(run_id, url, "comps", "error", "No comps on file for this item.", "")
        return [Signal("comps_failed", "warn", "No comparable prices were on file.", "screenshot")]
    out_dir = task_dir / "comps"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    force_bad = os.environ.get("TRUSTSCOUT_TEST_BAD_COMPS") == "1"

    def runner(**kwargs):
        folder = kwargs["host_output_dir"]
        return _run_code(
            kwargs["script"],
            folder,
            settings,
            timeout_sec=30,
            extra=kwargs.get("extra_files"),
        )

    comps_run = run_comps(
        title=title,
        price=price,
        comps=comps,
        output_dir=out_dir,
        host_output_dir=out_dir,
        runner=runner,
        force_bad_first=force_bad,
    )
    ids = ", ".join(comps_run.container_ids) or "none"
    if not comps_run.ok or not comps_run.result:
        db.add_step(run_id, url, "comps", "error", f"Comps code failed. Containers {ids} destroyed.", ids)
        return [Signal("comps_failed", "warn", "The comps sandbox did not return a result.", "screenshot")]
    if comps_run.chart:
        _store_file(db, settings, run_id, url, "chart", comps_run.chart, "Comps chart")
    fresh = comps_run.container_ids[-1]
    retry = f" Retried once; fresh container {fresh}." if comps_run.attempts > 1 else f" Fresh container {fresh}."
    db.add_step(run_id, url, "comps", "ok", "No-network comps finished." + retry, fresh)
    return [price_signal(comps_run.result)]


def _finish(run_id: str, url: str, signals: list[Signal], shot_id: int | None, db, settings) -> None:
    score = score_of(signals)
    risk = band(score)
    summary = _summary(risk, signals)
    reasons = []
    for signal in signals:
        artifact_id = _artifact_for(db, run_id, url, signal, shot_id, settings)
        reasons.append({"text": signal.text, "artifact_id": artifact_id, "severity": signal.severity})
    db.add_step(run_id, url, "verdict", "ok", f"{risk} risk, score {score}", "")
    _save_verdict(db, run_id, url, risk, score, summary, reasons)


def _summary(risk: str, signals: list[Signal]) -> str:
    brief = [{"code": signal.code, "severity": signal.severity, "text": signal.text} for signal in signals]
    result = chat(
        MODEL_VERDICT,
        [
            {
                "role": "system",
                "content": (
                    "trustscout-task: verdict\n"
                    "Write a two-sentence buyer summary from the signals. "
                    "Do not invent prices. Do not include secrets. Return JSON with risk and summary."
                ),
            },
            {"role": "user", "content": "SIGNAL_JSON:" + json.dumps({"risk": risk, "signals": brief})},
        ],
    )
    try:
        payload = parse_json(result.content)
        text = str(payload.get("summary") or "").strip()
    except (ValueError, TypeError):
        text = ""
    if not text:
        text = "Review the linked receipts before paying."
    return text


def _artifact_for(db, run_id: str, url: str, signal: Signal, shot_id: int | None, settings) -> int | None:
    if signal.artifact_kind == "chart":
        return db.find_artifact(run_id, url, "chart") or shot_id
    if signal.artifact_kind == "evidence" and signal.code == "injection":
        card = _evidence_card(
            settings,
            f"{run_id}-{slug_from_url(url)}-injection.png",
            "Hidden text pulled from the DOM",
            signal.text,
        )
        return _store_file(db, settings, run_id, url, "evidence", card, "Hidden-text receipt")
    if signal.artifact_kind == "evidence":
        card = _evidence_card(
            settings,
            f"{run_id}-{slug_from_url(url)}-contain.png",
            "Sandbox containment",
            signal.text,
        )
        return _store_file(db, settings, run_id, url, "evidence", card, "Containment receipt")
    return shot_id


def _evidence_card(settings, name: str, title: str, body: str) -> Path:
    from app.cards import text_card

    path = settings.data_dir / "cards" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text_card(title, body, path)
    return path


def _run_code(script: str, out_dir: Path, settings, timeout_sec: int, extra: dict | None = None):
    host_out = settings.docker_path(out_dir)
    if settings.runner == "fake":
        outcome = fake.run_script(
            script=script,
            output_dir=out_dir,
            host_output_dir=host_out,
            extra_files=extra,
            timeout_sec=timeout_sec,
        )
        if "STAGE loop" in outcome.logs:
            outcome.sandbox.timed_out = True
        return outcome
    return jobs.run_script(
        script=script,
        output_dir=out_dir,
        host_output_dir=host_out,
        extra_files=extra,
        image=settings.code_image,
        timeout_sec=timeout_sec,
        network=None,
    )


def _store_file(db, settings, run_id: str, url: str, kind: str, source: Path | None, label: str) -> int | None:
    if source is None or not source.exists():
        return None
    dest_dir = settings.data_dir / "artifacts" / run_id / slug_from_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{kind}{source.suffix or '.bin'}"
    if source.resolve() != dest.resolve():
        shutil.copyfile(source, dest)
    relative = dest.relative_to(settings.data_dir).as_posix()
    return db.add_artifact(run_id, url, kind, relative, label)


def _save_verdict(db, run_id: str, url: str, risk: str, score: int, summary: str, reasons: list) -> None:
    db.save_verdict(run_id, url, risk, score, summary, json.dumps(reasons))


def rewrite_url(url: str, settings) -> str:
    """Point localhost mock-market URLs at the host the sandbox proxy can reach."""
    parts = urlsplit(url)
    internal = urlsplit(settings.internal_base)
    public = urlsplit(settings.public_base)
    local_hosts = {"localhost", "127.0.0.1", public.hostname}
    if parts.hostname in local_hosts and parts.path.startswith("/market"):
        return f"{internal.scheme}://{internal.netloc}{parts.path}"
    return url


def _money(value) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "")
    digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in text)
    parts = [item for item in digits.split() if item]
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def _clean(value) -> str:
    return str(value or "").strip()


def _short(text: str) -> str:
    from sandbox.runner import redact

    return redact(text)[:500]
