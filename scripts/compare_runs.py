# -*- coding: utf-8 -*-
"""Side-by-side comparison of two ModelBench JSONL runs (same suite fingerprint).

Usage:
    python scripts/compare_runs.py runA.jsonl runB.jsonl [--labels A,B]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelbench.report import analyze_model, load_records  # noqa: E402
from modelbench.scoring import paired_scores  # noqa: E402


def summarize(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    batch, rows = load_records(path)
    if not rows:
        raise SystemExit(f"no records in {path}")
    stats = analyze_model(rows)
    paired = paired_scores(rows)["models"]
    model = rows[0]["model"]
    ps = paired[model]
    totals = [float(r["total_s"]) for r in rows if r.get("total_s")]
    stats.update(
        {
            "paired": ps["score"],
            "units": ps["units"],
            "paired_wisdom": ps["axis_scores"].get("wisdom", 0.0),
            "paired_exec": ps["axis_scores"].get("exec", 0.0),
            "incomplete": ps.get("incomplete_units", 0),
            "p50": stats.get("total_p50") or 0.0,
            "avg_reasoning_tokens": stats.get("reas_tokens", 0) / max(stats.get("n_total", 1), 1),
        }
    )
    return stats, {"batch": batch, "model": model}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--labels", default="")
    args = ap.parse_args()

    labels = args.labels.split(",") if args.labels else []
    rows = []
    for i, f in enumerate(args.files):
        stats, meta = summarize(f)
        label = labels[i] if i < len(labels) else meta["model"]
        rows.append((label, stats, meta["batch"]))

    head = f"{'arm':<28}{'裸分':>8}{'成对':>8}{'审慎':>8}{'执行':>8}{'故障':>6}{'P50':>7}{'内耗':>8}{'推理tok':>9}  最弱"
    print(head)
    print("-" * len(head))
    for label, s, _batch in rows:
        faults = sum(int(v) for k, v in s.get("faults", {}).items())
        print(
            f"{label:<28}{s['overall']*100:>7.1f}%{s['paired']:>7.1f}%{s['paired_wisdom']:>7.1f}%"
            f"{s['paired_exec']:>7.1f}%{faults:>6}{s['p50']:>6.1f}s{s['think_ratio']:>7.2f}x"
            f"{s.get('avg_reasoning_tokens', 0):>9.0f}  {s.get('worst_cat', '-')}={s.get('worst_cat_rate', 0)*100:.0f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
