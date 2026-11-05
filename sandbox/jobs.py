"""Browser and code jobs. Each one gets its own container, then that container is removed."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sandbox.runner import (
    SandboxResult,
    SandboxSpec,
    connect_network,
    create_network,
    redact,
    remove_container,
    remove_network,
    run_task,
)

CODE_IMAGE = "trustscout-code"
BROWSER_IMAGE = "trustscout-browser"
EGRESS_IMAGE = "trustscout-egress"


@dataclass
class Capture:
    """Files the browser sandbox wrote for one listing."""

    ok: bool
    error: str
    text: str
    hidden_text: str
    dom: dict
    payload: str
    screenshot: Path | None
    photo: Path | None
    sandbox: SandboxResult | None
    proxy_name: str


@dataclass
class CodeOutcome:
    """Result of one code-sandbox execution."""

    ok: bool
    result: dict | None
    logs: str
    chart: Path | None
    sandbox: SandboxResult


def capture_listing(
    *,
    url: str,
    allow_host: str,
    output_dir: Path,
    host_output_dir: Path,
    app_network: str,
    proxy_image: str = EGRESS_IMAGE,
    browser_image: str = BROWSER_IMAGE,
    timeout_sec: int = 45,
) -> Capture:
    """Screenshot ``url`` through an allowlist proxy. The browser has no other route out."""
    host_output_dir.mkdir(parents=True, exist_ok=True)
    task_id = uuid.uuid4().hex[:10]
    network = f"tsnet-{task_id}"
    proxy_name = f"tsproxy-{task_id}"
    allow_dir = host_output_dir.parent / "proxy"
    allow_dir.mkdir(parents=True, exist_ok=True)
    (allow_dir / "allowlist.txt").write_text(allow_host.strip().lower() + "\n", encoding="utf-8")
    create_network(network, internal=True)
    try:
        proxy_ip = _start_proxy(
            name=proxy_name,
            image=proxy_image,
            allow_dir=allow_dir,
            app_network=app_network,
            sandbox_network=network,
            allow_host=allow_host,
        )
        # Use the IP on the sandbox network. The proxy also sits on the app
        # network, and DNS may hand back that other address.
        (host_output_dir / "task.json").write_text(
            json.dumps({"url": url, "proxy": f"http://{proxy_ip}:8888"}),
            encoding="utf-8",
        )
        result = run_task(
            SandboxSpec(
                image=browser_image,
                output_dir=output_dir,
                host_output_dir=host_output_dir,
                timeout_sec=timeout_sec,
                network=network,
                extra_tmpfs=("/home/pwuser",),
                shm_size="256m",
            )
        )
        return _read_capture(host_output_dir, result, proxy_name)
    finally:
        remove_container(proxy_name)
        remove_network(network)


def run_script(
    *,
    script: str,
    output_dir: Path,
    host_output_dir: Path,
    extra_files: dict[str, str] | None = None,
    image: str = CODE_IMAGE,
    timeout_sec: int = 30,
    network: str | None = None,
) -> CodeOutcome:
    """Run ``script`` in the code image. ``network=None`` means ``--network=none``."""
    host_output_dir.mkdir(parents=True, exist_ok=True)
    (host_output_dir / "script.py").write_text(script, encoding="utf-8")
    for name, contents in (extra_files or {}).items():
        (host_output_dir / name).write_text(contents, encoding="utf-8")
    result = run_task(
        SandboxSpec(
            image=image,
            output_dir=output_dir,
            host_output_dir=host_output_dir,
            timeout_sec=timeout_sec,
            network=network,
        )
    )
    chart = host_output_dir / "chart.png"
    parsed = _read_json(host_output_dir / "result.json")
    ok = result.exit_code == 0 and parsed is not None and not result.timed_out
    return CodeOutcome(
        ok=ok,
        result=parsed,
        logs=result.logs,
        chart=chart if chart.exists() else None,
        sandbox=result,
    )


def _start_proxy(
    *,
    name: str,
    image: str,
    allow_dir: Path,
    app_network: str,
    sandbox_network: str,
    allow_host: str,
) -> str:
    import subprocess

    outer = app_network or "bridge"
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        outer,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "256m",
        "--pids-limit",
        "64",
        "-v",
        f"{allow_dir.resolve()}:/config:ro",
    ]
    if not app_network and allow_host == "host.docker.internal":
        cmd.extend(["--add-host", "host.docker.internal:host-gateway"])
    cmd.append(image)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(redact(proc.stderr or proc.stdout or "proxy start failed"))
    connect_network(sandbox_network, name)
    return _container_ip(name, sandbox_network)


def _container_ip(name: str, network: str) -> str:
    """Return the container's address on one Docker network."""
    import subprocess

    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    networks = json.loads(proc.stdout or "{}")
    info = networks.get(network) or {}
    address = info.get("IPAddress") or ""
    if not address:
        raise RuntimeError(f"no address for {name} on {network}")
    return address


def _read_capture(host_out: Path, result: SandboxResult, proxy_name: str) -> Capture:
    error = ""
    error_path = host_out / "error.txt"
    if error_path.exists():
        error = error_path.read_text(encoding="utf-8")[:1500]
    if result.timed_out:
        error = error or "browser sandbox hit the wall-clock limit"
    if result.exit_code not in {0, None} and not error:
        error = result.logs[-500:] or f"browser exited {result.exit_code}"
    screenshot = host_out / "screenshot.png"
    photo = host_out / "photo.bin"
    payload_path = host_out / "payload.py"
    payload = payload_path.read_text(encoding="utf-8") if payload_path.exists() else ""
    return Capture(
        ok=screenshot.exists() and not error,
        error=error,
        text=(host_out / "text.txt").read_text(encoding="utf-8") if (host_out / "text.txt").exists() else "",
        hidden_text=(
            (host_out / "hidden.txt").read_text(encoding="utf-8") if (host_out / "hidden.txt").exists() else ""
        ),
        dom=_read_json(host_out / "dom.json") or {},
        payload=payload,
        screenshot=screenshot if screenshot.exists() else None,
        photo=photo if photo.exists() else None,
        sandbox=result,
        proxy_name=proxy_name,
    )


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
