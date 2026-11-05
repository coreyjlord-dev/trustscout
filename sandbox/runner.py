"""Launch one container per task and always destroy it.

The sandbox is the security boundary. It is never the app process. No environment
variables are passed in, and the only bind mount is the per-task output directory.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("trustscout.sandbox")

MAX_TIMEOUT_SEC = 90
MEMORY = "1g"
CPUS = "1"
PIDS_LIMIT = "128"


class SandboxError(RuntimeError):
    """The control plane could not start or reap a sandbox."""


@dataclass
class SandboxSpec:
    """Inputs for a single ``docker run``."""

    image: str
    output_dir: Path
    host_output_dir: Path | None = None
    timeout_sec: int = MAX_TIMEOUT_SEC
    network: str | None = None
    extra_tmpfs: tuple[str, ...] = ()
    shm_size: str | None = None
    command: tuple[str, ...] = ()


@dataclass
class SandboxResult:
    """What remained after the container was removed."""

    name: str
    container_id: str
    exit_code: int | None
    timed_out: bool
    oom_killed: bool
    destroyed: bool
    logs: str
    runtime: str
    host_config: dict
    env_names: list[str] = field(default_factory=list)
    launch_error: str = ""


def choose_runtime() -> str:
    """Prefer gVisor (``runsc``). Fall back to runc with Docker's default seccomp."""
    forced = os.environ.get("TRUSTSCOUT_RUNTIME", "").strip()
    if forced:
        return forced
    try:
        proc = subprocess.run(
            ["docker", "info", "-f", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("docker info failed (%s); using runc", exc)
        return "runc"
    if "runsc" in (proc.stdout or ""):
        return "runsc"
    return "runc"


def run_task(spec: SandboxSpec) -> SandboxResult:
    """Run ``spec`` in a fresh container and ``docker rm -f`` it before returning."""
    if not 1 <= spec.timeout_sec <= MAX_TIMEOUT_SEC:
        raise ValueError(f"timeout_sec must be between 1 and {MAX_TIMEOUT_SEC}")
    runtime = choose_runtime()
    name = f"ts-{uuid.uuid4().hex[:12]}"
    host_out = (spec.host_output_dir or spec.output_dir).resolve()
    host_out.mkdir(parents=True, exist_ok=True)
    os.chmod(host_out, 0o1777)
    cmd = _run_command(spec, name=name, runtime=runtime, host_out=host_out)
    logger.info("sandbox start name=%s image=%s runtime=%s", name, spec.image, runtime)
    timed_out = {"value": False}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timer = threading.Timer(spec.timeout_sec, lambda: _kill(name, timed_out))
    timer.daemon = True
    timer.start()
    launch_error = ""
    try:
        try:
            stdout, stderr = proc.communicate(timeout=spec.timeout_sec + 25)
        except subprocess.TimeoutExpired:
            timed_out["value"] = True
            _kill(name, timed_out)
            stdout, stderr = proc.communicate(timeout=20)
        launch_error = ((stderr or "") + (stdout or "")).strip()[-2000:]
    finally:
        timer.cancel()
    inspect = _inspect(name)
    logs = redact(_logs(name) or launch_error)
    result = _result_from_inspect(
        name=name,
        runtime=runtime,
        inspect=inspect,
        logs=logs,
        launch_error=redact(launch_error),
        proc_code=proc.returncode,
        timed_out=timed_out["value"],
    )
    _remove(name)
    result.destroyed = _inspect(name) is None
    logger.info(
        "sandbox done name=%s exit=%s timeout=%s oom=%s destroyed=%s",
        name,
        result.exit_code,
        result.timed_out,
        result.oom_killed,
        result.destroyed,
    )
    return result


def _run_command(spec: SandboxSpec, *, name: str, runtime: str, host_out: Path) -> list[str]:
    """Build the docker CLI. There is no ``-e`` and no extra volume."""
    cmd = [
        "docker",
        "run",
        "--name",
        name,
        "--runtime",
        runtime,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--memory",
        MEMORY,
        "--memory-swap",
        MEMORY,
        "--cpus",
        CPUS,
        "--pids-limit",
        PIDS_LIMIT,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        spec.network or "none",
        "-v",
        f"{host_out}:/out",
    ]
    for mount in spec.extra_tmpfs:
        cmd.extend(["--tmpfs", mount])
    if spec.shm_size:
        cmd.extend(["--shm-size", spec.shm_size])
    cmd.append(spec.image)
    cmd.extend(spec.command)
    return cmd


def _result_from_inspect(
    *,
    name: str,
    runtime: str,
    inspect: dict | None,
    logs: str,
    launch_error: str,
    proc_code: int | None,
    timed_out: bool,
) -> SandboxResult:
    exit_code = proc_code
    oom = False
    container_id = ""
    env_names: list[str] = []
    host_config: dict = {}
    if inspect:
        state = inspect.get("State") or {}
        oom = bool(state.get("OOMKilled"))
        container_id = (inspect.get("Id") or "")[:12]
        if state.get("ExitCode") is not None:
            exit_code = state.get("ExitCode")
        env_names = [item.split("=", 1)[0] for item in (inspect.get("Config") or {}).get("Env") or []]
        raw = inspect.get("HostConfig") or {}
        host_config = {
            "Memory": raw.get("Memory"),
            "MemorySwap": raw.get("MemorySwap"),
            "NanoCpus": raw.get("NanoCpus"),
            "PidsLimit": raw.get("PidsLimit"),
            "ReadonlyRootfs": raw.get("ReadonlyRootfs"),
            "CapDrop": raw.get("CapDrop") or [],
            "SecurityOpt": raw.get("SecurityOpt") or [],
            "NetworkMode": raw.get("NetworkMode"),
            "Binds": raw.get("Binds") or [],
        }
    if oom:
        timed_out = False
    return SandboxResult(
        name=name,
        container_id=container_id,
        exit_code=exit_code,
        timed_out=timed_out,
        oom_killed=oom,
        destroyed=False,
        logs=logs[-8000:],
        runtime=runtime,
        host_config=host_config,
        env_names=env_names,
        launch_error=launch_error[-2000:],
    )


def _kill(name: str, flag: dict) -> None:
    flag["value"] = True
    subprocess.run(["docker", "kill", name], capture_output=True, text=True, check=False)


def _remove(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, check=False)


def _inspect(name: str) -> dict | None:
    proc = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    import json

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not data:
        return None
    return data[0]


def _logs(name: str) -> str:
    proc = subprocess.run(
        ["docker", "logs", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def redact(text: str) -> str:
    """Drop the inference key if it ever appears in sandbox output."""
    key = os.environ.get("VULTR_INFERENCE_API_KEY", "").strip()
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def remove_container(name: str) -> None:
    """Force-remove a container the control plane started outside ``run_task``."""
    _remove(name)


def create_network(name: str, *, internal: bool) -> None:
    """Create a Docker network. Internal networks have no route off the host."""
    cmd = ["docker", "network", "create"]
    if internal:
        cmd.append("--internal")
    cmd.append(name)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 and "already exists" not in (proc.stderr or ""):
        raise SandboxError(redact(proc.stderr or proc.stdout or "network create failed"))


def remove_network(name: str) -> None:
    """Delete a task network. Safe to call if it is already gone."""
    subprocess.run(["docker", "network", "rm", name], capture_output=True, text=True, check=False)


def connect_network(network: str, container: str) -> None:
    """Attach a running container to another network."""
    proc = subprocess.run(
        ["docker", "network", "connect", network, container],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SandboxError(redact(proc.stderr or "network connect failed"))


def docker_ready() -> bool:
    """Return True when the daemon answers ``docker info``."""
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0
