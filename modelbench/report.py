# -*- coding: utf-8 -*-
"""Three-axis leaderboard & stability diagnostic report generator."""

import json
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .scoring import paired_scores


def wilson_ci(p: float, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def percentile(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(len(s) * q)))
    return s[idx]


def load_records(path: str, batch: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if not rows:
        return "", []
    target_batch = batch or rows[-1]["run_id"]
    filtered = [r for r in rows if r.get("run_id") == target_batch]
    return target_batch, filtered


def analyze_model(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_total = len(records)
    n_pass = sum(int(r["passed"]) for r in records)
    overall = n_pass / n_total if n_total else 0.0

    # Axis scores (strictly counting ALL trials in denominator)
    by_axis = defaultdict(list)
    by_cat = defaultdict(list)
    by_item = defaultdict(list)
    by_vgroup = defaultdict(lambda: defaultdict(list))
    by_sgroup = defaultdict(lambda: defaultdict(list))

    ttfts: List[float] = []
    totals: List[float] = []
    reason_chars_sum = 0
    content_chars_sum = 0
    comp_tokens_sum = 0
    reas_tokens_sum = 0
    faults = defaultdict(int)

    for r in records:
        p = int(r["passed"])
        by_axis[r["axis"]].append(p)
        by_cat[r["cat"]].append(p)
        by_item[r["item_id"]].append(p)
        if r.get("variant_group"):
            by_vgroup[r["variant_group"]][r.get("variant", "spec")].append(p)
        if r.get("stress_group"):
            by_sgroup[r["stress_group"]][r.get("stress", "clean")].append(p)
        if r.get("ttft_s") is not None:
            ttfts.append(float(r["ttft_s"]))
        if r.get("total_s") is not None:
            totals.append(float(r["total_s"]))
        reason_chars_sum += int(r.get("reasoning_chars") or 0)
        content_chars_sum += int(r.get("content_chars") or 0)
        comp_tokens_sum += int(r.get("completion_tokens") or 0)
        reas_tokens_sum += int(r.get("reasoning_tokens") or 0)
        ek = r.get("error_kind")
        if ek:
            faults[ek] += 1

    wisdom_rate = sum(by_axis["wisdom"]) / len(by_axis["wisdom"]) if by_axis["wisdom"] else 0.0
    exec_rate = sum(by_axis["exec"]) / len(by_axis["exec"]) if by_axis["exec"] else 0.0

    # Unique item pass averages for Wilson CI (conservative N = unique items)
    uniq_n = len(by_item)
    ci_lo, ci_hi = wilson_ci(overall, uniq_n)

    # Flip rate across items with >=2 trials
    multi_items = [arr for arr in by_item.values() if len(arr) >= 2]
    flips = sum(1 for arr in multi_items if 0 < sum(arr) < len(arr))
    flip_rate = flips / len(multi_items) if multi_items else 0.0

    # Prompt sensitivity drop (best variant - worst variant) & worst_of_3
    prompt_drops: List[float] = []
    worst_variants: List[float] = []
    for _, vmap in by_vgroup.items():
        if len(vmap) >= 2:
            rates = [sum(v) / len(v) for v in vmap.values() if v]
            prompt_drops.append(max(rates) - min(rates))
            worst_variants.append(min(rates))
    prompt_drop = sum(prompt_drops) / len(prompt_drops) if prompt_drops else 0.0
    worst_var_rate = sum(worst_variants) / len(worst_variants) if worst_variants else overall

    # Long-context stress degradation (clean - long)
    stress_drops: List[float] = []
    for _, smap in by_sgroup.items():
        if "clean" in smap and "long" in smap:
            c_r = sum(smap["clean"]) / len(smap["clean"])
            l_r = sum(smap["long"]) / len(smap["long"])
            stress_drops.append(max(0.0, c_r - l_r))
    stress_drop = sum(stress_drops) / len(stress_drops) if stress_drops else 0.0

    # Unweighted worst-case stability floor (no arbitrary weights)
    worst_instability = max(flip_rate, prompt_drop, stress_drop)
    stability = max(0.0, 1.0 - worst_instability)

    cat_rates = {c: sum(v) / len(v) for c, v in sorted(by_cat.items())}
    if cat_rates:
        worst_cat, worst_cat_rate = min(cat_rates.items(), key=lambda kv: (kv[1], kv[0]))
    else:
        worst_cat, worst_cat_rate = "-", 0.0

    # Thinking bloat ratio: take max of visible-char ratio and token-billing ratio so hidden reasoning cannot evade detection
    char_think_ratio = reason_chars_sum / max(content_chars_sum, 1)
    tok_think_ratio = (
        reas_tokens_sum / max(comp_tokens_sum - reas_tokens_sum, 1)
        if comp_tokens_sum > 0 and reas_tokens_sum > 0
        else 0.0
    )
    think_ratio = max(char_think_ratio, tok_think_ratio)

    infra_faults = faults["transport"] + faults["api_error"] + faults["grader_error"]
    infra_pct = infra_faults / n_total if n_total else 0.0

    trunc_cnt = faults.get("truncated", 0) + faults.get("empty", 0) + faults.get("tool_loop", 0)
    trunc_pct = trunc_cnt / n_total if n_total else 0.0

    # Persona tagging (strict engineering gate: zero tolerance for chronic truncation or bloat)
    tags: List[str] = []
    if infra_pct > 0.05:
        tags.append("基础设施异常(>5%)")
    elif trunc_pct > 0.05 or think_ratio > 8.0:
        tags.append("思考内耗/易截断风险")
    elif wisdom_rate >= 0.85 and exec_rate >= 0.90 and flip_rate <= 0.08 and prompt_drop <= 0.15 and trunc_cnt == 0:
        tags.append("可托付实干主力")
    elif wisdom_rate >= 0.80 and exec_rate < 0.75:
        tags.append("偏科理论派(执行弱)")
    elif flip_rate > 0.15:
        tags.append("抽风摇摆型")
    elif prompt_drop > 0.25 or (think_ratio > 4.0 and overall < 0.70):
        tags.append("花拳绣腿高危")
    else:
        tags.append("主力可用(局部有瑕)")

    return {
        "n_pass": n_pass,
        "n_total": n_total,
        "overall": overall,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "wisdom": wisdom_rate,
        "exec": exec_rate,
        "stability": stability,
        "flip_rate": flip_rate,
        "prompt_drop": prompt_drop,
        "worst_var_rate": worst_var_rate,
        "stress_drop": stress_drop,
        "ttft_p50": percentile(ttfts, 0.5),
        "total_p50": percentile(totals, 0.5),
        "total_p95": percentile(totals, 0.95),
        "think_ratio": think_ratio,
        "comp_tokens": comp_tokens_sum,
        "reas_tokens": reas_tokens_sum,
        "faults": dict(faults),
        "infra_pct": infra_pct,
        "cat_rates": cat_rates,
        "worst_cat": worst_cat,
        "worst_cat_rate": worst_cat_rate,
        "tags": tags,
    }


def format_report(path: str, batch: Optional[str] = None) -> str:
    run_id, records = load_records(path, batch=batch)
    if not records:
        return f"No records found in {path} (batch={batch or 'latest'})."

    by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    gvers = set()
    for r in records:
        by_model[r["model"]].append(r)
        gvers.add(r.get("grader_version", "?"))

    lines = [
        f"ModelBench v2 实干家评测战报 | 批次: {run_id} | 套件指纹: {','.join(sorted(gvers))}",
        "-" * 136,
        f"{'模型':30s} {'裸总分':>9s} {'总率':>6s} {'95%CI':>11s} {'审慎':>6s} {'执行':>6s} {'翻转':>5s} {'口语跌':>6s} {'水压跌':>6s} {'P50':>6s} {'膨胀比':>6s} {'最弱分类':24s} 画像结论",
        "-" * 136,
    ]
    summaries = {m: analyze_model(recs) for m, recs in by_model.items()}
    ordered = sorted(summaries.items(), key=lambda kv: (kv[1]["overall"], kv[1]["stability"]), reverse=True)

    for m, s in ordered:
        score_s = f"{s['n_pass']:3d}/{s['n_total']:<3d}"
        ci_s = f"[{s['ci_lo']*100:.0f},{s['ci_hi']*100:.0f}]%"
        wcat_s = f"{s['worst_cat']}={s['worst_cat_rate']*100:.0f}%"
        lines.append(
            f"{m[:30]:30s} {score_s:>9s} {s['overall']*100:5.1f}% {ci_s:>11s} "
            f"{s['wisdom']*100:5.1f}% {s['exec']*100:5.1f}% "
            f"{s['flip_rate']*100:4.0f}% {s['prompt_drop']*100:5.0f}% {s['stress_drop']*100:5.0f}% "
            f"{s['total_p50']:5.1f}s {s['think_ratio']:5.2f}x {wcat_s[:24]:24s} {', '.join(s['tags'])}"
        )

    lines.append("-" * 136)
    lines.append("各分类明细 & 故障分母统计:")
    for m, s in ordered:
        cats_str = " ".join(f"{k}={v*100:.0f}%" for k, v in s["cat_rates"].items())
        flt = s["faults"]
        flt_str = (
            f"trunc={flt.get('truncated',0)} empty={flt.get('empty',0)} "
            f"loop={flt.get('tool_loop',0)} trans={flt.get('transport',0)} "
            f"api={flt.get('api_error',0)} grader={flt.get('grader_error',0)}"
        )
        lines.append(
            f"  * {m}: [{cats_str}] | tokens(comp={s['comp_tokens']}, reas={s['reas_tokens']}) | 故障计数(全计0分): {flt_str}"
        )
        if s["infra_pct"] > 0.05:
            lines.append(f"    [WARN] 链路/传输/判分异常占比 {s['infra_pct']*100:.1f}% > 5%，建议核查网关后再定论。")

    # ---- paired (all-or-nothing) scoring: the only rate that counts for variant groups ----
    paired = paired_scores(records)
    lines.append("")
    lines.append("-" * 136)
    lines.append("成对计分（同组变体全对才得 1 分；单边启发式=0）:")
    lines.append(f"{'模型':30s} {'成对分':>7s} {'单位':>5s} {'审慎':>6s} {'执行':>6s} {'缺件组':>6s}")
    for m, ps in sorted(paired["models"].items(), key=lambda kv: kv[1]["score"], reverse=True):
        ax = ps["axis_scores"]
        lines.append(
            f"{m[:30]:30s} {ps['score']:6.1f}% {ps['units']:5d} "
            f"{ax.get('wisdom', 0.0):5.1f}% {ax.get('exec', 0.0):5.1f}% {len(ps['incomplete_units']):6d}"
        )
    return "\n".join(lines)
