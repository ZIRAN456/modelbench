"""Paired (all-or-nothing) scoring for variant groups.

Red-team requirement (Hark, 2026-11-13):
    "严格 0%" only holds if scoring is paired: a variant group earns credit
    **only when every variant in the group is answered correctly**.  Scoring
    variants independently lets a one-sided heuristic collect 50%-75% for free.

This module turns raw per-item trial records (as written by ``runner.run_benchmark``)
into scoring *units*:

* an item with ``variant_group`` joins that group -> one unit for the whole group;
* an item without a group is its own unit;
* a unit scores 1 for a repetition only if **all** its variants passed in that
  repetition, 0 otherwise;
* a unit that is incomplete (a variant never ran / errored out) scores 0 and is
  reported as ``complete=False`` so the deficit is visible instead of silent.

Axis rates are normalised per axis (units passed / units available), so the two
axes stay comparable no matter how many raw items each group expands into.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set


def units_from_suite(suite: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map unit_id -> {axis, cat, variants:set(item_id)} built from the suite."""
    units: Dict[str, Dict[str, Any]] = {}
    for item in suite:
        group = item.get("variant_group")
        unit_id = group if group else item["id"]
        u = units.setdefault(unit_id, {"axis": item["axis"], "cat": item["cat"], "variants": set()})
        u["variants"].add(item["id"])
    return units


def paired_scores(
    records: Iterable[Dict[str, Any]],
    expected_units: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Aggregate per-item trial records into paired unit scores.

    Parameters
    ----------
    records:
        Trial records with at least ``model``, ``item_id``, ``rep``, ``passed``,
        ``axis`` and optionally ``variant_group``.
    expected_units:
        Output of :func:`units_from_suite`.  When supplied, units that produced
        no records (e.g. a crashed worker) are still counted, as failed and
        incomplete, so a silent gap cannot inflate the score.

    Returns
    -------
    dict with per-model ``units``, ``axis_scores`` (0-100 per axis), ``score``
    (0-100 over all units) and ``incomplete_units``.
    """
    # (model, unit_id, rep) -> list of (item_id, passed)
    trials: Dict[tuple, List[tuple]] = defaultdict(list)
    axes: Dict[str, str] = {}
    unit_variants: Dict[str, Set[str]] = defaultdict(set)
    models: Set[str] = set()

    for rec in records:
        model = rec["model"]
        models.add(model)
        group = rec.get("variant_group")
        unit_id = group if group else rec["item_id"]
        axes.setdefault(unit_id, rec.get("axis") or "unknown")
        unit_variants[unit_id].add(rec["item_id"])
        trials[(model, unit_id, rec.get("rep", 1))].append((rec["item_id"], bool(rec.get("passed"))))

    if expected_units:
        for unit_id, meta in expected_units.items():
            axes.setdefault(unit_id, meta.get("axis", "unknown"))
            unit_variants[unit_id] |= set(meta.get("variants") or ())
        # Every model that produced any record is expected to cover every unit.
        for model in models:
            have = {k[1] for k in trials if k[0] == model}
            for unit_id in expected_units:
                if unit_id not in have:
                    trials[(model, unit_id, 0)].append(("__missing__", False))

    out_models: Dict[str, Any] = {}
    for model in sorted(models):
        unit_scores: Dict[str, float] = {}
        detail: Dict[str, Any] = {}
        for unit_id, variants in unit_variants.items():
            reps = sorted({k[2] for k in trials if k[0] == model and k[1] == unit_id})
            if not reps:
                unit_scores[unit_id] = 0.0
                detail[unit_id] = {"score": 0.0, "complete": False, "axis": axes.get(unit_id), "variants": len(variants)}
                continue
            per_rep = []
            complete = True
            for rep in reps:
                rows = trials[(model, unit_id, rep)]
                seen = {i for i, _ in rows}
                if expected_units and unit_id in expected_units:
                    if not set(variants) <= seen:
                        complete = False
                all_pass = bool(rows) and all(p for _, p in rows) and complete
                per_rep.append(1.0 if all_pass else 0.0)
            unit_scores[unit_id] = sum(per_rep) / len(per_rep)
            detail[unit_id] = {
                "score": round(unit_scores[unit_id], 4),
                "complete": complete,
                "axis": axes.get(unit_id),
                "variants": len(variants),
            }

        axis_tot: Dict[str, List[float]] = defaultdict(list)
        for unit_id, sc in unit_scores.items():
            ax = axes.get(unit_id, "unknown")
            axis_tot[ax].append(sc)
        axis_scores = {
            ax: round(100.0 * sum(v) / len(v), 2) for ax, v in axis_tot.items() if v
        }
        all_scores = list(unit_scores.values())
        out_models[model] = {
            "units": len(unit_scores),
            "score": round(100.0 * sum(all_scores) / len(all_scores), 2) if all_scores else 0.0,
            "axis_scores": axis_scores,
            "incomplete_units": sorted(u for u, d in detail.items() if not d["complete"]),
            "detail": detail,
        }

    return {"models": out_models, "unit_count": len(unit_variants)}
