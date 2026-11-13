# Changelog

## v2.2.3 — 2026-11-13

Prompt-only fix found by the first real-model run of the new variants.

### False negative in `v22_cross_audit` / `dual_defect`
- deepseek-v4.1-flash answered `["CLEAN", "FX_DRIFT", "MISSING_SETTLEMENT"]`.
  It had diagnosed both defects correctly but also tagged the case `CLEAN`
  ("nothing else is wrong"), and the exact-list comparison failed it.
- The rule block now states: **CLEAN is exclusive** — never combine it with
  `FX_DRIFT` / `MISSING_SETTLEMENT`.
- Re-run: 6/6 pass, paired unit 100%. The `missing_only` variant that had
  truncated at 8192 tokens earlier also passed in 8.0s, so that failure was model
  verbosity, not an item defect.

### Measured on the new groups (deepseek-v4.1-flash, 1 rep)
| group | items | result |
|---|---|---|
| `v22_cross_audit` | 6 | 6/6 after the fix (unit 1/1) |
| `v22_patch_triplet` | 5 | 5/5 (unit 1/1) |
| `v22_reversal_cascade` | 3 | 3/3, incl. `gamma_150ms` boundary (unit 1/1) |

## v2.2.2 — 2026-11-13

Second-pass close-out of the Hark red-team list: the deep-tier matrices were
already paired, but nine details still leaked (or were never built).

### Paired coverage extended
- `exec_reversal_timestamp_clash_full` was still drawn per seed (a coin flip, the
  same defect the canary had). Both scenarios now ship together in group
  `v222_timestamp_clash` (`_reorder` / `_ghost`), and each variant carries the
  other's distractor marker (a rollback line in the reorder case, a stale
  RETRANSMIT line in the ghost case) so no surface feature labels the answer.
- Audit traps `wisdom_trap_0/2_conflict`, `wisdom_stress_clean/long` always
  answered `consistent: false, computed_total: null` — a free win for any constant
  bot. Each now has a consistent twin (stated total corrected) in
  `v222_audit_pair_*`, so the unit only scores when the model actually re-adds.
- `exec_patch_atomic_triplet` grows three variants: `idempotent_noop` (already at
  target state — any audited write fails), `duplicate_guard` (role already present,
  must not be duplicated) and `reverse_direction` (production -> tests).
- `v22_cross_audit` grows `retransmit_only` (duplicate bank rows must be deduped,
  not flagged missing) and `jpy_precision` (JPY has zero decimals; a float
  implementation reports a phantom FX drift).
- `v22_reversal_cascade` grows `gamma_150ms`: the p99 sits exactly on 150.0, so
  the `<=` / `<` boundary is finally tested.

### Unicode item hardened
- Cf characters are drawn per seed from an 11-char candidate pool (6 injected), so
  hardcoding `\u200b`/`\ufeff` no longer passes.
- The contract now says: NFKC first, then drop Cf **except** U+200C (ZWNJ) and
  U+200D (ZWJ). Blanket "strip all Cf" collapses emoji sequences and Persian
  words; the sandbox proves it by subscribing both spellings and requiring that
  they do **not** collide.
- A bound-method subscriber is registered as well: `weakref.ref(bound_method)`
  dies immediately and now fails with `bound_method_lost`.
- Subprocess stdio is pinned to UTF-8 (`stdin.buffer` + `reconfigure`), so
  non-BMP Cf characters survive the Windows cp936 console.

### Cheat-bot baseline (Hark requirement #4)
- New `scripts/cheat_bots.py`: four degenerate solvers (`constant`, `keyword`,
  `last_number`, `superset`) scored against every unit with paired aggregation.
- Measured: **0.0% on all 42 units for all four bots** (exit code 0 required).
  Regression-tested in `test_degenerate_cheat_bots_score_zero`.

### CI
- Perf probe split further: correctness/mutation run with a 30s budget, while the
  O(N^2) probe uses a tight 1.5s copy, so neither a slow nor a fast runner can
  flip the assertion.
- Suite: 68 raw items -> **42 scoring units** (19 Wisdom / 23 Execution).

## v2.2.1 — 2026-11-13

Closes the scoring hole left open in v2.2.0: the paired contract was written in the
RFC but never enforced in code.

### Paired (all-or-nothing) scoring
- New `modelbench/scoring.py`: `paired_scores()` / `units_from_suite()`.
  A `variant_group` earns credit **only when every variant in the group passes**.
  A one-sided heuristic now scores 0 instead of 75%.
- Incomplete groups (a variant that never ran) score 0 and are reported in
  `incomplete_units`, so a silent gap cannot inflate a score.
- Axis rates are normalised per axis, so the two axes stay comparable even though
  paired groups collapse several raw items into one scoring unit.
- `report` gains a 成对计分 block (paired score / units / per-axis / missing groups).

### Canary rollout becomes a real 2x2 matrix
- `exec_chain_dynamic_rollback` (one variant drawn per seed, i.e. a coin flip) is
  replaced by four co-shipped variants in group `v221_canary_rollback`:
  `fatal_abort`, `transient_recover`, `clean_all_ok`, `dual_fault`.
- New `clean` / `dual` mock behaviours; every probe response now carries the same
  `retry_after_ms` + `latency_ms` distractors, so those fields cannot label a case.
- Anti-cheat: always-commit scores 0 (fails fatal + dual), always-abort scores 0
  (fails clean + transient).
- `patch_triplet` and `reversal_cascade` are now paired groups too.

### Suite
- 57 raw items -> **42 scoring units** (19 Wisdom / 23 Execution).

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
