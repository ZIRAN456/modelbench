# Changelog

## v2.2.0 — 2026-11-13

Red-team hardened Batch-2 suite (review-driven).

### Suite composition
- **54 items, 27 Wisdom + 27 Execution** (was 50 / 25 + 25).
- Five categories that all models scored 100% on are **retired and replaced** by paired
  adversarial matrices (rule: break-the-ceiling => deepen):
  - `cross_audit` 2 → 4 (`wisdom_cross_audit_drift_tz_{clean,fx,missing,dual}`)
  - `root_cause_chain` 3 → 4 (`wisdom_chain_distributed_shadow_{upstream,gateway,network,clock}`)
  - `code_stress` 2 → 1 (`wisdom_code_unicode_and_leak`)
  - `patch_discipline` 1 → 2 (`exec_patch_atomic_triplet_{commit,rollback}`)
  - `long_reversal` 1 → 2 (`exec_reversal_conditional_cascading_{alpha,beta}`)

### Scoring contract
- **Paired scoring**: a group only scores when *every* variant in the group is answered
  correctly — single-sided heuristics score 0, not 50%.
- Distractor features (staleness timestamps, anomalous latency, decoy keywords) appear in
  **all** variants, so surface features cannot label the case.
- Variants are seed-randomised to defeat memorisation; RFC publishes the *generation rule
  class*, not the item text.

### Fixes
- `exec_chain_dynamic_rollback` variant discovery in tests no longer hardcodes magic seeds.
- `wisdom_code_hard_rollup_on` complexity budget 1.2s → 6.0s: the correct O(N) reference
  solution takes ~0.7s locally, leaving only a 1.6x margin, which flaked on shared Windows
  CI runners. Quadratic solutions still exceed 8s, so the O(N^2) gate is unaffected.
  Correctness/mutation probes now use a loose timeout so only the perf gate depends on
  wall-clock time.

## v2.1.0 — 2026-11-12
- Batch-1 deep tiers: `exec_chain_dynamic_rollback`, `exec_reversal_timestamp_clash_full`,
  `wisdom_code_concurrency_race` with cheating-script baselines proven at 0%.

## v2.0.0 — 2026-11-11
- Two-axis suite (Wisdom / Execution), 150 judgments per model, hard-contract scoring.
