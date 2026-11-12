# -*- coding: utf-8 -*-
"""CLI entry point for ModelBench v2: selftest, run, report, inspect."""

import argparse
import json
import sys
import unittest
from typing import List, Optional

from .report import format_report, load_records
from .runner import run_benchmark


def _configure_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _find_tests_dir() -> str:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "tests"),
        os.path.join(os.path.dirname(here), "tests"),
        os.path.join(os.getcwd(), "tests"),
    ):
        if os.path.isdir(cand):
            return cand
    raise FileNotFoundError("Cannot locate 'tests' directory for selftest")


def cmd_selftest() -> int:
    tests_dir = _find_tests_dir()
    suite = unittest.defaultTestLoader.discover(start_dir=tests_dir, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    return 0 if res.wasSuccessful() else 1


def cmd_run(args: argparse.Namespace) -> int:
    # Mandatory pre-flight grader verification before benchmarking any model
    if not args.skip_selftest:
        tests_dir = _find_tests_dir()
        suite = unittest.defaultTestLoader.discover(start_dir=tests_dir, pattern="test_*.py")
        res = unittest.TextTestRunner(verbosity=0).run(suite)
        if not res.wasSuccessful():
            print("[ABORT] Pre-flight selftest failed. Fix grader/sandbox before benchmarking.", file=sys.stderr)
            return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cats = [c.strip() for c in args.cats.split(",") if c.strip()] if args.cats else None
    print(f"Starting ModelBench v2 ({args.profile}) | models={models} | reps={args.reps} | out={args.out}")

    run_id = run_benchmark(
        models=models,
        reps=args.reps,
        seed=args.seed,
        profile=args.profile,
        out_path=args.out,
        base_url=args.base_url,
        api_key=args.api_key,
        workers=args.workers,
        timeout_s=args.timeout,
        max_tokens=args.max_tokens,
        cats=cats,
        progress_cb=lambda msg: print(msg, flush=True),
    )
    print(f"\nCompleted run_id={run_id}\n")
    print(format_report(args.out, batch=run_id))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    print(format_report(args.file, batch=args.batch))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    run_id, recs = load_records(args.file, batch=args.batch)
    if args.model:
        recs = [r for r in recs if args.model in r["model"]]
    if args.only_failed:
        recs = [r for r in recs if not r["passed"]]
    print(f"Inspecting batch={run_id} ({len(recs)} records):")
    for r in recs[: args.limit]:
        print("-" * 80)
        print(
            f"model={r['model']} | item={r['item_id']}#r{r['rep']} | passed={r['passed']} | "
            f"reason={r['reason']} | ttft={r['ttft_s']}s | total={r['total_s']}s"
        )
        if r.get("tool_calls"):
            print("tool_calls:", json.dumps(r["tool_calls"], ensure_ascii=False))
        print("content:", (r.get("content") or "")[:600])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    p = argparse.ArgumentParser(prog="modelbench", description="ModelBench v2 Practical Benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="Run grader, sandbox, and adversarial self-tests")

    pr = sub.add_parser("run", help="Benchmark one or more OpenAI-compatible models")
    pr.add_argument("--models", required=True, help="Comma-separated model IDs")
    pr.add_argument("--reps", type=int, default=3, help="Repetitions per item (default: 3)")
    pr.add_argument("--seed", type=int, default=20261112, help="Suite RNG seed")
    pr.add_argument("--profile", choices=["quick", "full"], default="quick")
    pr.add_argument("--cats", default="", help="Optional comma-separated categories filter")
    pr.add_argument("--out", default="eval_v2.jsonl", help="Output JSONL path")
    pr.add_argument("--base-url", default=None, help="OpenAI-compatible base URL (or set OPENAI_BASE_URL)")
    pr.add_argument("--api-key", default=None, help="API key (or set OPENAI_API_KEY)")
    pr.add_argument("--workers", type=int, default=4)
    pr.add_argument("--timeout", type=float, default=120.0)
    pr.add_argument("--max-tokens", type=int, default=8192)
    pr.add_argument("--skip-selftest", action="store_true")

    prep = sub.add_parser("report", help="Render leaderboard from JSONL file")
    prep.add_argument("file", help="Path to JSONL output")
    prep.add_argument("--batch", default=None)

    pin = sub.add_parser("inspect", help="Inspect raw outputs and failures")
    pin.add_argument("file", help="Path to JSONL output")
    pin.add_argument("--batch", default=None)
    pin.add_argument("--model", default=None)
    pin.add_argument("--only-failed", action="store_true")
    pin.add_argument("--limit", type=int, default=10)

    args = p.parse_args(argv)
    if args.cmd == "selftest":
        return cmd_selftest()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    return 1
