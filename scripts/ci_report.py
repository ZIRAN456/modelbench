"""Run a CI command and make failures self-explanatory from the run page.

GitHub's job log is fetched by JavaScript, so a red matrix leg that only says
"Process completed with exit code 1" costs a whole round trip to diagnose.
This wrapper:

* runs the command with inherited stdio passed through,
* on failure writes the tail of the output into ``$GITHUB_STEP_SUMMARY``
  (server-rendered HTML, readable without the log viewer),
* emits ``::error::`` annotations with the failing test names, so they show up
  on the run page directly.

Usage:
    python scripts/ci_report.py python modelbench.py selftest
    python scripts/ci_report.py python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys


def _summarize(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    tail = text[-8000:]
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## ❌ step failed\n\n```text\n")
            fh.write(tail)
            fh.write("\n```\n")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAIL:", "ERROR:", "AssertionError:", "ModuleNotFoundError:")):
            print(f"::error::{stripped[:300]}")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: ci_report.py <command> [args...]", file=sys.stderr)
        return 2
    print(f"[ci_report] {' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out, flush=True)
    if proc.returncode != 0:
        _summarize(out)
        print(f"[ci_report] exit={proc.returncode}", flush=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
