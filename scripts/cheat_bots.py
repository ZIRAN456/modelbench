"""Cheat-bot baseline for the paired suite (Hark red-team requirement #4).

Hark asked for the opposite of a reference solution: run *degenerate* solvers
that never read the data, and prove they score 0 on every scoring unit.

Four bots, all blind to the actual reasoning:

``constant``    always the first allowed label / a fixed number
``keyword``     grep for incriminating words in the prompt and pick the label
``last_number`` take the last numeric token in the prompt
``superset``    emit every allowed label at once (the classic "guess all")

Run:  python scripts/cheat_bots.py [--seed 20261112] [--json out.json]

Exit code 0 only when every single unit scores 0 for every bot.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelbench.bank import generate_suite  # noqa: E402
from modelbench.graders import grade_item  # noqa: E402
from modelbench.scoring import units_from_suite  # noqa: E402

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
LABEL_RE = re.compile(r"\[([A-Z_]+(?:[^\]]*?))\]")

KEYWORD_HINTS = {
    "FX": ["fx", "exchange", "rate", "汇"],
    "MISSING": ["missing", "absent", "not found", "缺"],
    "JWT": ["jwt", "token", "lock"],
    "NETWORK": ["packet", "loss", "retransmit", "latency"],
    "CLOCK": ["clock", "skew", "ntp"],
    "POOL": ["pool", "exhaust", "connection"],
    "ALPHA": ["alpha", "keep", "<= 150"],
    "BETA": ["beta", "disable"],
}


def _labels_from_prompt(prompt: str) -> List[str]:
    m = re.search(r"Allowed [a-z_]+ (?:list )?values[^:]*:\s*\[(.*?)\]", prompt, re.S)
    if not m:
        m = re.search(r"strictly from:\s*\[(.*?)\]", prompt, re.S)
    if not m:
        return []
    return re.findall(r'"([A-Z_]+)"', m.group(1))


def _shape(expected: Any, bot: str, prompt: str) -> Any:
    """Build an answer with the expected schema but no real reasoning."""
    labels = _labels_from_prompt(prompt)
    if isinstance(expected, dict):
        out = {}
        for k, v in expected.items():
            if isinstance(v, list):
                if bot == "superset":
                    out[k] = labels or ["CLEAN"]
                elif bot == "keyword":
                    hits = [lb for lb in labels if any(h in prompt.lower() for h in KEYWORD_HINTS.get(lb.split("_")[0], []))]
                    out[k] = hits or labels[:1] or ["CLEAN"]
                else:
                    out[k] = labels[:1] or ["CLEAN"]
            elif isinstance(v, bool):
                out[k] = False
            elif isinstance(v, (int, float)):
                nums = NUM_RE.findall(prompt)
                if bot == "last_number" and nums:
                    out[k] = float(nums[-1])
                elif bot == "superset" and nums:
                    out[k] = float(max(float(n) for n in nums))
                elif nums:
                    out[k] = float(nums[0])  # constant: the first number in the prompt
                else:
                    out[k] = 0
            elif isinstance(v, str):
                if bot == "keyword":
                    for lb in labels or [v]:
                        for kw in KEYWORD_HINTS.get(lb.split("_")[0], []):
                            if kw in prompt.lower():
                                out[k] = lb
                                break
                        if k in out:
                            break
                    out.setdefault(k, labels[0] if labels else "UNKNOWN")
                else:
                    out[k] = labels[0] if labels else "UNKNOWN"
            else:
                out[k] = v
        return out
    return expected


def run(seed: int, limit_units: int | None = None) -> Dict[str, Any]:
    suite = generate_suite(seed=seed, profile="full")
    by_id = {x["id"]: x for x in suite}
    units = units_from_suite(suite)
    bots = ["constant", "keyword", "last_number", "superset"]

    per_bot: Dict[str, Dict[str, float]] = {}
    for bot in bots:
        per_bot[bot] = {}
        for unit_id, u in units.items():
            scores = []
            for item_id in sorted(u["variants"]):
                item = by_id[item_id]
                ans = json.dumps(_shape(item.get("expected"), bot, item["prompt"]))
                trace = None
                if item.get("grader") == "tool_exec":
                    trace = {"called_tools": [], "calls": [], "rounds_exceeded": False, "final_fs": {"files": {}}}
                try:
                    passed, _reason = grade_item(item, ans, tool_trace=trace)
                except Exception:
                    passed = 0
                scores.append(passed)
            per_bot[bot][unit_id] = 1.0 if scores and all(s == 1 for s in scores) else 0.0

    summary = {}
    for bot, unitscores in per_bot.items():
        hit = [u for u, sc in unitscores.items() if sc > 0]
        summary[bot] = {
            "units_scored": len(unitscores),
            "units_passed": len(hit),
            "score_pct": round(100.0 * len(hit) / max(1, len(unitscores)), 2),
            "leaked_units": hit[:20],
        }
    return {"seed": seed, "units": len(units), "bots": summary, "per_unit": per_bot}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    res = run(args.seed)
    print(f"cheat-bot baseline | seed={res['seed']} | units={res['units']}")
    print(f"{'bot':<14}{'units':>7}{'passed':>8}{'score':>9}")
    for bot, s in res["bots"].items():
        print(f"{bot:<14}{s['units_scored']:>7}{s['units_passed']:>8}{s['score_pct']:>8}%")
        if s["leaked_units"]:
            print(f"    leaked: {s['leaked_units']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, ensure_ascii=False)
        print(f"saved -> {args.json}")
    worst = max(s["score_pct"] for s in res["bots"].values())
    print("VERDICT:", "PASS (all bots 0%)" if worst == 0 else f"FAIL (best bot {worst}%)")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
