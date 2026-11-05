"""One real browser sandbox against the mock market. Off unless TRUSTSCOUT_E2E=1."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from agent.pipeline import execute
from app.db import DB
from app.main import create_app
from app.settings import Settings
from sandbox.runner import docker_ready

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def browser_image():
    if os.environ.get("TRUSTSCOUT_E2E") != "1":
        pytest.skip("set TRUSTSCOUT_E2E=1 to build Playwright and screenshot the demo")
    if not docker_ready():
        pytest.skip("Docker is not available")
    subprocess.check_call(["docker", "build", "-t", "trustscout-browser", str(ROOT / "sandbox" / "browser")])
    subprocess.check_call(["docker", "build", "-t", "trustscout-code", str(ROOT / "sandbox" / "code")])
    subprocess.check_call(["docker", "build", "-t", "trustscout-egress", str(ROOT / "sandbox" / "egress")])


def test_real_sandbox_screenshots_a_listing(browser_image, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTSCOUT_MODE", "mock")
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    port = _free_port()
    settings = Settings(
        root=ROOT,
        data_dir=tmp_path,
        host_data_dir=tmp_path,
        public_base=f"http://127.0.0.1:{port}",
        internal_base=f"http://host.docker.internal:{port}",
        app_network="",
        runner="docker",
        code_image="trustscout-code",
        browser_image="trustscout-browser",
        proxy_image="trustscout-egress",
        eager=True,
    )
    app = create_app(settings)
    thread = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": "0.0.0.0", "port": port, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    _wait_until_up(port)
    db: DB = app.state.db
    run_id = db.create_run([f"http://127.0.0.1:{port}/market/oak-writing-desk"], "mock")
    execute(run_id, db, settings)
    verdicts = db.list_verdicts(run_id)
    assert verdicts and verdicts[0]["risk"] == "low"
    shots = [row for row in db.list_artifacts(run_id) if row["kind"] == "screenshot"]
    assert shots
    shot = tmp_path / shots[0]["path"]
    assert shot.stat().st_size > 5000
    steps = db.list_steps(run_id)
    assert any(step["name"] == "browser" and step["status"] == "ok" for step in steps)


def _wait_until_up(port: int) -> None:
    import urllib.request

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("app did not start")
