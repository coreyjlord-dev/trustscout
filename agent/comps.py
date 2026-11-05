"""Ask the model for comps code, then run it in the no-network sandbox.

The script is checked for a few dangerous tokens before it runs. The sandbox
is still the boundary: a slip is contained, and a failure is retried once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent.vultr_client import MODEL_COMPS, chat

COMPS_SCRIPT = """import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = json.loads(Path("/out/input.json").read_text(encoding="utf-8"))
prices = [float(item) for item in src["comps"]]
price = float(src["price"])
ordered = sorted(prices)
count = len(ordered)
if count == 0:
    raise SystemExit("no comps")
mid = count // 2
if count % 2:
    median = ordered[mid]
else:
    median = (ordered[mid - 1] + ordered[mid]) / 2
mean = sum(prices) / count
variance = sum((item - mean) ** 2 for item in prices) / count
std = variance ** 0.5 or 1.0
pct_under = (median - price) / median * 100 if median else 0.0
out = {
    "median": median,
    "mean": mean,
    "zscore": round((price - mean) / std, 3),
    "n": count,
    "listed": price,
    "pct_under": round(pct_under, 1),
}
Path("/out/result.json").write_text(json.dumps(out), encoding="utf-8")
fig, ax = plt.subplots(figsize=(7.2, 3.4))
ax.hist(prices, bins=min(8, max(count, 1)), color="#1a1714")
ax.axvline(price, color="#9d2c2c", linewidth=2.5, label="asking")
title = str(src.get("title", "comps"))[:70]
ax.set_title(title)
ax.legend(frameon=False)
fig.tight_layout()
fig.savefig("/out/chart.png", dpi=120)
"""

_FORBIDDEN = (
    "socket",
    "subprocess",
    "urllib",
    "requests",
    "os.system",
    "fork",
    "vultr",
    "environ",
    "shutil",
)


class ScriptRejected(ValueError):
    """The model script used a token the comps job does not allow."""


@dataclass
class CompsRun:
    """Sandbox comps outcome, including the retry."""

    ok: bool
    result: dict | None
    logs: str
    chart: Path | None
    attempts: int
    container_ids: list[str]


def write_comps_code(*, title: str, stderr: str | None, force_bad: bool) -> str:
    """Ask Vultr (or the mock) for the analysis script."""
    repair = ""
    if stderr:
        repair = "PREVIOUS_STDERR:\n" + stderr[-2000:]
    bad = "FORCE_BAD_CODE\n" if force_bad and not stderr else ""
    messages = [
        {
            "role": "system",
            "content": (
                "trustscout-task: comps\n"
                "Write a Python script that reads /out/input.json and writes "
                "/out/result.json plus /out/chart.png. Use matplotlib. "
                "Do not use the network. Return only the script.\n" + bad
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"title": title, "repair": repair}),
        },
    ]
    content = chat(MODEL_COMPS, messages).content.strip()
    if content.startswith("```"):
        lines = [line for line in content.splitlines() if not line.startswith("```")]
        content = "\n".join(lines).strip()
    screen_script(content)
    return content + ("\n" if not content.endswith("\n") else "")


def screen_script(script: str) -> None:
    """Refuse comps scripts that try to open sockets or read the environment."""
    lowered = script.lower()
    for token in _FORBIDDEN:
        if token in lowered:
            raise ScriptRejected(f"comps script contained {token}")


def run_comps(
    *,
    title: str,
    price: float,
    comps: list[float],
    output_dir: Path,
    host_output_dir: Path,
    runner,
    force_bad_first: bool = False,
) -> CompsRun:
    """Generate code, run it with no network, and retry once with stderr."""
    ids: list[str] = []
    logs = ""
    last_error = ""
    for attempt in range(1, 3):
        try:
            script = write_comps_code(
                title=title,
                stderr=last_error or None,
                force_bad=force_bad_first and attempt == 1,
            )
        except ScriptRejected as exc:
            last_error = str(exc)
            logs = last_error
            if attempt == 2:
                break
            continue
        outcome = runner(
            script=script,
            output_dir=output_dir,
            host_output_dir=host_output_dir,
            extra_files={"input.json": json.dumps({"title": title, "price": price, "comps": comps})},
            timeout_sec=30,
            network=None,
        )
        ids.append(outcome.sandbox.container_id)
        logs = outcome.logs
        if outcome.ok and outcome.result:
            return CompsRun(
                ok=True,
                result=outcome.result,
                logs=logs,
                chart=outcome.chart,
                attempts=attempt,
                container_ids=ids,
            )
        last_error = outcome.logs[-1500:] or "comps script failed"
    return CompsRun(ok=False, result=None, logs=logs, chart=None, attempts=len(ids) or 1, container_ids=ids)
