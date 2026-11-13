# ModelBench v2

[![ModelBench CI](https://github.com/ZIRAN456/modelbench/actions/workflows/test.yml/badge.svg)](https://github.com/ZIRAN456/modelbench/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**[English](README.md) · [简体中文](README_CN.md)**

**零依赖、确定性的大模型与 Agent 实干能力评测工具**

*专用于筛选能干实活、稳重不翻车的生产主力模型，淘汰虚浮理论派、工具死循环与高内耗截断选手。*

---

## 核心设计原则

1. **零外部依赖**：纯 Python 3.10+ 标准库实现（`urllib`、`json`、`subprocess`、`concurrent.futures`），无需安装任何第三方库。
2. **零 LLM 裁判漂移**：全部题目采用确定性契约判分——严格 JSON、隔离沙箱单测（`python -I` + `sys.addaudithook`）、有状态工具文件系统终态校验与末行定性断言，判分器自身零误差。
3. **绝不搞拍脑袋加权**：能力通过率、耗时与 Token 代价彻底分账，绝不揉成一个模糊加权分；榜单直出裸总分（`通过/总题` + 95% Wilson 置信区间）、最弱分类短板、翻转/跌落底线与硬闸门标签（截断率 `>5%` 或膨胀比 `>8x` 直接剥夺“可托付实干主力”资格）。
4. **隐藏思考照样现形**：膨胀比取「可见思考字符比」与「计费 `reasoning_tokens / answer_tokens`」二者最大值，即便模型对外隐藏思考文本，偷烧推理 Token 仍会在战报中现出原形。

---

## 54 题实干考场（`--profile full`）

完整版采用 **智慧 27 题 + 执行 27 题** 严格对称设计（`--profile quick` 为 52 题快速筛查版，上下文更短）。原先被全员满分的 5 个类目已下线，替换为**成对 2×2 对抗矩阵**（双故障 / 单故障A / 单故障B / 双正常），**同一组的变体全部答对方得 1 分**，单边启发式拿不到半分：

| 维度 | 题型覆盖 | 专治哪类虚胖 |
|---|---|---|
| **智慧轴（27 题）** | `root_cause`、`root_cause_chain`、`code_fix`、`code_hard`、`code_stress`、`trap_detect`、`cross_audit` | 跨模块雪崩因果追根、跨午夜时间窗与重试熔断器、2.5 万条 $O(N)$ 压测与防篡改入参、矛盾陷阱、分渠道跨表对账 |
| **执行轴（27 题）** | `tool_recovery`、`tool_adversarial`、`tool_deep_chain`、`needle_fidelity`、`long_reversal`、`multi_reversal`、`patch_discipline`、`strict_redline` | 假缓存核验、分页单位漂移、写锁冲突重试 + 写后读回校验类型（抓 `"123"` 冒充 `123`）、冻结期禁写刹车、5.5 万字五针提取与四步撤回、局部最小补丁、防凭据回显与医疗话术绕过红线 |
| **稳定性探针** | 等价措辞三变体（3 组 × 3 变体）+ 5.5 万字长水压对照（4 组） | 同题 `temperature=0` 多遍重跑翻转率（`flip_rate`）、规范/口语/带噪切换跌落（`prompt_drop`）、高水压长上下文跌落（`stress_drop`） |

---

## 快速上手

```bash
# 1. 离线跑通 20 项对抗自检（校验判分器、沙箱拦截钩子、模拟工具循环与榜单口径）
python modelbench.py selftest

# 2. 或以可编辑模式安装为全局 CLI 命令
pip install -e .
modelbench selftest

# 3. 接入任意 OpenAI 兼容端点开跑（不设 OPENAI_BASE_URL 时默认标准官方地址）
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="your-api-key"

modelbench run \
  --models "gpt-4.1,claude-sonnet-4,deepseek-chat" \
  --profile full \
  --reps 3 \
  --workers 6 \
  --out runs/eval_results.jsonl

# 4. 重绘战报或按失败项抽检原始工具轨迹与输出
modelbench report runs/eval_results.jsonl
modelbench inspect runs/eval_results.jsonl --only-failed --limit 20
```

---

## 战报输出示例

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

## 沙箱边界说明

代码题在隔离临时目录中以 `python -I` 子进程执行，同时挂载 `sys.addaudithook` 拦截套接字/外网请求、子进程拉起、注册表/动态库、文件写入及 Python 标准库目录之外的文件读取。需说明：语言层审计钩子属于常规评测卫生隔离；若面向完全不可信的第三方多租户代码，仍应结合操作系统级容器（Docker / `bwrap`）做内核隔离。

## 开源协议

[MIT](LICENSE)
