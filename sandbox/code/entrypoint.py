"""Run /out/script.py. Docker has already removed the network and the caps."""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    """Execute the task script with a writable home on the tmpfs."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    os.environ["MPLCONFIGDIR"] = "/tmp"
    os.environ["HOME"] = "/tmp"
    os.chdir("/tmp")
    script = "/out/script.py"
    sys.argv = [script]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
