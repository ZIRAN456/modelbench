# ModelBench v2

[![ModelBench CI](https://github.com/ZIRAN456/modelbench/actions/workflows/test.yml/badge.svg)](https://github.com/ZIRAN456/modelbench/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**[English](README.md) · [简体中文](README_CN.md)**

**Zero-Dependency Practical Benchmark for LLM & Agent Wisdom, Execution, and Stability**

*Filter out flaky chatbots and overthinking token-burners: deterministic code graders, multi-turn stateful tool sandboxes, flip-rate stability probes, and dual-channel reasoning bloat detection.*

---

## Core Design Principles

1. **Zero External Dependencies**: Pure Python 3.10+ standard library only (`urllib`, `json`, `subprocess`, `concurrent.futures`). Runs anywhere with zero `pip` dependency trees.
2. **Zero LLM-as-a-Judge Drift**: Every item is graded deterministically by strict JSON schemas, out-of-process isolated execution (`python -I` + `sys.addaudithook`), stateful mock filesystem assertions, or exact final-line contracts.
3. **Zero Weighted Blends**: Capability accuracy, latency, and token cost are never mashed into an arbitrary weighted score. Reports show raw pass rate (`pass/total` + 95% Wilson CI), weakest category, worst-case instability, and hard gating labels (`trunc > 5%` or `think_ratio > 8x` disqualifies mainstay status).
4. **Hidden Reasoning Bloat Detection**: Computes thinking bloat as `max(visible_reasoning_chars / content_chars, reasoning_tokens / answer_tokens)` from OpenAI-compatible usage billing so models that hide chain-of-thought text still cannot disguise excessive token burn.

---

## 57-Item Practical Suite / 42 Paired Scoring Units (`--profile full`)

**68 raw items collapse into 42 scoring units** (19 Judgment / 23 Execution; `--profile quick` runs a faster subset with shorter contexts). Five categories that every model used to ace are now replaced by paired **2x2 adversarial matrices** (double-fault / single-fault A / single-fault B / clean) scored all-or-nothing:

> **Terminology**: the **Judgment** axis (internal key `wisdom`, formerly "Wisdom") measures close reading, reconciliation, root-cause attribution and rule compliance — **not** an IQ test.

| Axis | Categories | What It Catches |
|---|---|---|
| **Judgment (25)** | `root_cause`, `root_cause_chain`, `code_fix`, `code_hard`, `code_stress`, `trap_detect`, `cross_audit` | Cross-module cascading failure attribution, algorithmic edge cases (cross-midnight windows, retry budget circuit breakers, 25k-item $O(N)$ latency & input-mutation guards), contradictory data traps, cross-table ledger reconciliation |
| **Execution (25)** | `tool_recovery`, `tool_adversarial`, `tool_deep_chain`, `needle_fidelity`, `long_reversal`, `multi_reversal`, `patch_discipline`, `strict_redline` | Stale cache verification, unit-drift pagination, write-lock retries + post-write readback type safety (`"123"` vs `123`), maintenance freeze-window write aborts, 55k-char multi-needle overrides, surgical config patching, medical/secret-echo redlines |
| **Stability Probes** | `variant_group` (3 groups × 3 variants) & `stress_group` (4 clean/55k-long pairs) | Measures intra-item `flip_rate` across repeated `temperature=0` trials, `prompt_drop` under colloquial/noisy phrasing, and `stress_drop` under 55k-char heterogeneous distractions |

---

## Quick Start

```bash
# 1. Verify graders, sandbox audit hooks, and mock runner offline (20 adversarial self-tests)
python modelbench.py selftest

# 2. Or install editable CLI entrypoint
pip install -e .
modelbench selftest

# 3. Run benchmark against any OpenAI-compatible endpoint
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="your-api-key"

modelbench run \
  --models "gpt-4.1,claude-sonnet-4,deepseek-chat" \
  --profile full \
  --reps 3 \
  --workers 6 \
  --out runs/eval_results.jsonl

# 4. Render scorecard or inspect exact failure traces
modelbench report runs/eval_results.jsonl
modelbench inspect runs/eval_results.jsonl --only-failed --limit 20
```

---

## Sample Scorecard Output

```
ModelBench v2 实干家评测战报 | 批次: 20261112-192251-sample | 套件指纹: d3961f46
----------------------------------------------------------------------------------------------------------------------------------------
模型                                   裸总分     总率       95%CI     智慧     执行    翻转    口语跌    水压跌    P50    膨胀比 最弱分类                     画像结论
----------------------------------------------------------------------------------------------------------------------------------------
model-pro-reasoning              149/150  99.3%   [92,100]% 100.0%  98.7%    2%     0%     0%  12.7s  6.46x strict_redline=94%       可托付实干主力
model-balanced-medium            146/150  97.3%    [88,99]% 100.0%  94.7%    2%     0%     0%   7.7s  5.12x tool_adversarial=80%     可托付实干主力
model-fast-flash                 138/150  92.0%    [81,97]%  97.3%  86.7%    8%    22%     0%   4.6s  0.10x root_cause_chain=78%     主力可用(局部有瑕)
```

---

## Sandbox Security Notice

Generated Python fixes execute inside a fresh `python -I` subprocess with an empty working directory, sanitized environment, strict timeout, and `sys.addaudithook` blocking socket/HTTP, subprocess spawning, ctypes/registry access, file writes, and reads outside Python stdlib prefixes. Note that language-level audit hooks are defense-in-depth for benchmark hygiene; for completely untrusted third-party inputs in multi-tenant environments, run inside an OS container (`docker` / `bwrap`).

## License

[MIT](LICENSE)
