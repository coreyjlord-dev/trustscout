"""HTTP routes: the inspection desk, the mock market, and the live timeline."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from agent.catalog import load_listings
from agent.pipeline import execute
from agent.vultr_client import inference_mode
from app.forms import parse_targets

router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _templates() -> Jinja2Templates:
    return _TEMPLATES


def _ctx(request: Request, **extra) -> dict:
    settings = request.app.state.settings
    mode = "fake-runner" if settings.runner == "fake" else inference_mode()
    base = {
        "request": request,
        "mode": mode,
        "public_base": settings.public_base,
    }
    base.update(extra)
    return base


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Desk: paste URLs or pick a demo listing."""
    listings = load_listings(request.app.state.settings.root)
    return _templates().TemplateResponse(request, "index.html", _ctx(request, listings=listings))


@router.get("/health")
def health() -> dict:
    """Liveness probe for compose and the VM."""
    return {"ok": True}


@router.post("/runs")
def create_run(request: Request, urls: str = Form(...)) -> RedirectResponse:
    """Start an inspection. The human already clicked."""
    settings = request.app.state.settings
    targets = parse_targets(urls, settings.public_base)
    if not targets:
        raise HTTPException(status_code=400, detail="Add at least one listing URL")
    mode = "fake-runner" if settings.runner == "fake" else inference_mode()
    run_id = request.app.state.db.create_run(targets, mode)
    if settings.eager:
        execute(run_id, request.app.state.db, settings)
    else:
        import threading

        threading.Thread(
            target=execute,
            args=(run_id, request.app.state.db, settings),
            daemon=True,
        ).start()
    response = RedirectResponse(url=f"/runs/{run_id}", status_code=303)
    response.headers["HX-Redirect"] = f"/runs/{run_id}"
    return response


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str) -> HTMLResponse:
    """Live timeline. Steps arrive as server-sent events."""
    run = request.app.state.db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Unknown inspection")
    return _templates().TemplateResponse(request, "run.html", _ctx(request, run=run))


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str) -> StreamingResponse:
    """SSE stream of timeline rows, then the finished verdict panel."""
    db = request.app.state.db
    if not db.get_run(run_id):
        raise HTTPException(status_code=404, detail="Unknown inspection")
    last_header = request.headers.get("last-event-id", "0")
    try:
        last_id = int(last_header)
    except ValueError:
        last_id = 0

    async def generate():
        import asyncio

        sent = last_id
        while True:
            if await request.is_disconnected():
                break
            for step in db.steps_after(run_id, sent):
                html = _one_line(_templates().get_template("step.html").render(step=step))
                yield f"id: {step['id']}\nevent: step\ndata: {html}\n\n"
                sent = step["id"]
            run = db.get_run(run_id)
            if run and run["status"] in {"done", "error"} and not db.steps_after(run_id, sent):
                panel = _panel_html(request, run_id)
                yield f"event: done\ndata: {panel}\n\n"
                break
            await asyncio.sleep(0.35)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/runs/{run_id}/print", response_class=HTMLResponse)
def run_print(request: Request, run_id: str) -> HTMLResponse:
    """Server-rendered inspection, for refresh and for tests."""
    db = request.app.state.db
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Unknown inspection")
    return _templates().TemplateResponse(
        request,
        "print.html",
        _ctx(
            request,
            run=run,
            steps=db.list_steps(run_id),
            verdicts=_verdicts(db, run_id),
            approvals=db.list_approvals(run_id),
            artifacts=db.list_artifacts(run_id),
        ),
    )


@router.post("/runs/{run_id}/approve", response_class=HTMLResponse)
def approve(request: Request, run_id: str, action: str = Form(...)) -> HTMLResponse:
    """Log a human click. Nothing is sent, bought, or signed."""
    db = request.app.state.db
    if not db.get_run(run_id):
        raise HTTPException(status_code=404, detail="Unknown inspection")
    allowed = {"message_seller", "save_watchlist"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail="Unknown action")
    note = "Approval recorded. TrustScout did not send a message, did not log in, and did not buy anything."
    row = db.add_approval(run_id, action, note)
    html = _templates().get_template("approval.html").render(approval=row)
    return HTMLResponse(html)


@router.get("/artifacts/{artifact_id}")
def artifact(request: Request, artifact_id: int) -> FileResponse:
    """Serve a receipt PNG that belongs to this control plane's data directory."""
    row = request.app.state.db.get_artifact(artifact_id)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown receipt")
    root = request.app.state.settings.data_dir.resolve()
    path = (root / row["path"]).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=404, detail="Receipt path is outside the data directory")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Receipt file is missing")
    media = "image/png" if path.suffix == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media)


@router.get("/market/{slug}", response_class=HTMLResponse)
def market_listing(request: Request, slug: str) -> HTMLResponse:
    """One Northlot listing. The sandbox screenshots this page."""
    listings = load_listings(request.app.state.settings.root)
    listing = next((item for item in listings if item["slug"] == slug), None)
    if listing is None:
        raise HTTPException(status_code=404, detail="No such listing")
    injection = ""
    payload = ""
    root = request.app.state.settings.root
    if listing.get("hostile") == "injection":
        injection = (root / "mockmarket" / "payloads" / "injection.txt").read_text(encoding="utf-8")
    if listing.get("hostile") == "payload":
        payload = (root / "mockmarket" / "payloads" / "hostile_price.py").read_text(encoding="utf-8")
    return _templates().TemplateResponse(
        request,
        "listing.html",
        {
            "request": request,
            "listing": listing,
            "injection": injection,
            "payload": payload,
        },
    )


def _panel_html(request: Request, run_id: str) -> str:
    db = request.app.state.db
    html = (
        _templates()
        .get_template("panel.html")
        .render(
            run=db.get_run(run_id),
            verdicts=_verdicts(db, run_id),
            approvals=db.list_approvals(run_id),
            artifacts=db.list_artifacts(run_id),
        )
    )
    return _one_line(html)


def _verdicts(db, run_id: str) -> list[dict]:
    rows = []
    for row in db.list_verdicts(run_id):
        item = dict(row)
        try:
            item["reason_list"] = json.loads(item["reasons"])
        except json.JSONDecodeError:
            item["reason_list"] = []
        rows.append(item)
    return rows


def _one_line(html: str) -> str:
    return " ".join(html.split())
