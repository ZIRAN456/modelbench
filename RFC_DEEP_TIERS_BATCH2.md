# RFC: ModelBench v2.2 第二批 5 类目红队深度加固与 2×2 矩阵设计方案

## 核心红队准绳（全面吸收 Hark Pro 审定意见）
1. **强制成对/多维绑定计分**：单个题目不再孤立给分，关联变体全部命中方可得 1 分，彻底击穿单边猜测与简单二分类器的 50% 期望得分；
2. **2×2 矩阵化与对称干扰**：每个题目从 2 变体升级为 2×2 状态矩阵（故障均不发生、单发 A、单发 B、双发 A+B），所有表面特征（关键字、错误字样、日期跨度）在各变体中全部对称注入，杜绝局部正则/grep 偷分；
3. **真实物理机制与防误伤**：
   - 题 1（跨表对账）：单号全局唯一，采用批次号/settlement_id 差异；引入合法跨月补结单；时区严格锁死 UTC，金额采用 `Decimal` 比对允许 ±0.01 最小单位舍入容差；
   - 题 2（因果链诊断）：变体全量注入短暂锁等待日志作为背景噪声；补充网关收首字节时间（TTFB）、worker 线程调用栈等不可辩驳物理证据；
   - 题 3（代码抗突变）：废弃直接 `weakref.ref` 绑定方法（规避临时对象立即回收的 Python 底层 Bug），改用 `weakref.WeakMethod`；沙箱挂载私有哨兵对象探针；包含“调用方未注销”的真实泄漏压测；
   - 题 4（原子补丁）：沙箱在写入第三个文件时注入物理 IO 异常（只读/磁盘满），强断言必须回滚前两个已写文件；判定采用 JSON 语义结构对比而非文本 diff；
   - 题 5（条件级联撤回）：阈值卡在 `150.0ms` 临界点，混入 `0.150s` 异构量纲；阶段 2 内部再嵌“首轮作废重测”撤回，击碎直接正则提取。

---

## 5 道深水成对对抗题目详细规程

### 题目一【智慧轴·跨表对账】：`wisdom_cross_audit_drift_tz`
- **考核目标**：多币种时序对账、合法跨月补结 vs 汇差与丢单并发多标签诊断。
- **2×2 变体矩阵**：
  - **变体 00（完美对平）**：包含跨月正常结算（上月 10-31 交易次月 11-01 正常清算，汇率锁定成交日 T+0），总账平，输出 `diff_type: ["CLEAN"]`；
  - **变体 01（纯汇差浮动）**：订单成交于 UTC 23:58，银行结算于次日 02:00（按结算日动态汇率结算），无单据丢失，输出 `diff_type: ["FX_DRIFT"]` 并计算 Decimal 汇差（容差 ±0.01）；
  - **变体 10（纯漏结算丢单）**：1 笔订单因跨行通道故障被漏结，输出 `diff_type: ["MISSING_SETTLEMENT"]` 与丢失单号；
  - **变体 11（汇差与丢单并发）**：既有跨日汇差，又有一笔丢单，输出多标签 `diff_type: ["FX_DRIFT", "MISSING_SETTLEMENT"]`、汇差金额与单号。
- **作弊基线清零断言**：无脑正则抓上月日期流、固定条数轧差流在 2×2 矩阵下得分严格为 0。

---

### 题目二【智慧轴·因果链诊断】：`wisdom_chain_distributed_shadow`
- **考核目标**：分布式链路真假超时穿透。
- **2×2 变体矩阵**：
  - **变体 00（下游真慢，线程耗尽）**：下游 DB 锁导致 upstream trace > 5000ms，网关 504，网关 worker 堆栈显示处于 `socket_read_wait`，元凶为 `UPSTREAM_POOL_EXHAUSTED`；
  - **变体 01（下游假超时，网关自身慢锁）**：下游 12ms 快速返回 ACK，网关 worker 堆栈卡在 `gateway.auth.jwt_rwlock_acquire` 耗时 5100ms，元凶为 `GATEWAY_JWT_LOCK_CONTENTION`；
  - **变体 10（网络中间件丢包重传）**：下游与网关均健康，TCP 重传率 40%，元凶为 `NETWORK_PACKET_LOSS`；
  - **变体 11（时钟漂移假象）**：两台主机 NTP 漂移 -6000ms，实际耗时仅 15ms，元凶为 `HOST_CLOCK_SKEW`。
- **作弊基线清零断言**：词频最高报错提取流、顶层错误直接输出流在 4 变体下得分严格为 0。

---

### 题目三【智慧轴·代码抗突变】：`wisdom_code_unicode_and_leak`
- **考核目标**：不可见控制字符清洗 + 绑定方法弱引用生命周期管理。
- **沙箱工程与判定规则**：
  - 实现 `MessageRouter.subscribe(topic, handler)` 与 `publish(topic, payload)`；
  - **字符清洗**：输入 topic 混入随机抽取的 Cf 字符集（`\u200b`, `\u2060`, `\ufeff`, `\u200e`, `\u202a` 等），必须准确归一化清洗；同时保留合法多语言连字；
  - **内存防漏**：使用 `weakref.WeakMethod`。测试代码挂载 `class Handler: def on_event(self, p): ...`，注册后故意释放 `handler` 强引用；
  - **功能与泄漏双断言**：
    1. 实例存活期内，`publish` 必须精确调用监听器；
    2. 实例释放后，经过多次 `gc.collect()`，沙箱内部通过哨兵 weakref 检测到存活引用数为 0，且后续 `publish` 不报错、无死句柄堆积。
- **作弊基线清零断言**：普通 `strip()` 流在多 Cf 字符穿透下 0 分；强引用闭包流在 GC 哨兵检测下 0 分；直接用 `weakref.ref(inst.method)` 导致监听器失效在功能断言下 0 分。

---

### 题目四【执行轴·局部最小补丁】：`exec_patch_atomic_triplet`
- **考核目标**：跨三文件联动原子修改与失败事务回滚。
- **沙箱物理注入与规则**：
  - 割接涉及 `routes.json`、`auth.json`、`ratelimit.json`；
  - **变体 A（正常成功）**：正常执行割接，语义对比 3 处配置修改是否精确吻合目标，相邻配置完好；
  - **变体 B（事务回滚注入）**：在工具修改到第 3 个文件时，沙箱注入 `IOError: Disk quota exceeded`，要求模型必须主动捕获异常并对前两个文件执行补偿性回滚恢复原状；沙箱最终校验 3 个文件是否完全恢复初始状态。
- **作弊基线清零断言**：静态查找表替换流、忽略异常不回滚流在变体 B 下直接 0 分。

---

### 题目五【执行轴·长文本条件嵌套撤回】：`exec_reversal_conditional_cascading`
- **考核目标**：临界值与嵌套反转穿透。
- **成对对抗规则**：
  - 判定基准值设为严格临界点 `150.0ms`；
  - 阶段 2 内部注入嵌套反转：“压测阶段 2 初始测得 p99 为 142ms，但经核实该数据混入预热脏请求已作废；在相同并发下复测定稿为 158ms（变体 A） / 148ms（变体 B）”；
  - 单位穿插混入 `0.158s` 与 `148ms`；
  - 规则：`p99 <= 150.0ms` 执行 Plan-Alpha，`p99 > 150.0ms` 执行 Plan-Beta。
- **作弊基线清零断言**：直接提取第一段数字流、抓取最后出现的数字流在嵌套反转下得分严格为 0。

---

## 附录 A — 退化脚本（作弊 bot）实测（v2.2.2）

Hark 第二批意见第 4 条要求：不是只测"聪明解能不能过"，而是测**不读数据的退化解能不能拿分**。
`scripts/cheat_bots.py` 实现四种盲解，按**成对**口径聚合（同组全对才记 1 个 unit）：

| bot | 行为 | 42 units 命中 |
|---|---|---|
| `constant` | 取允许标签表中的第一个值 / 题面第一个数字 | **0 (0.0%)** |
| `keyword` | 命中关键词（FX / MISSING / JWT / NETWORK / CLOCK / POOL）即选对应标签 | **0 (0.0%)** |
| `last_number` | 取题面最后一个数字 | **0 (0.0%)** |
| `superset` | 把所有允许标签一次性全交（"全都猜"） | **0 (0.0%)** |

命令：`python scripts/cheat_bots.py --seed 20261112`（非 0 分即返回非零退出码，可直接进 CI）。

首轮实测露过 4 个 unit（`wisdom_trap_0_conflict` / `trap_2_conflict` / `stress_clean` / `stress_long`），
原因是这些题**答案恒为 `{"consistent": false, "computed_total": null}`**——任何盲猜都中。
v2.2.2 为每一道补了"数字自洽"的孪生变体（`v222_audit_pair_*`，把声称的合计改成正确合计），
成对计分后盲猜必挂，故四条均为 0。

## 附录 B — v2.2.2 新增变体清单

| 组 | 新增变体 | 斩杀的单边启发式 |
|---|---|---|
| `v222_timestamp_clash` | `reorder` + `ghost` 同卷 | 只认"出现 ABORTED 就是幽灵回滚" |
| `v222_audit_pair_*` | `inconsistent` + `consistent` | 恒答 false / null |
| `v22_patch_triplet` | `idempotent_noop`、`duplicate_guard`、`reverse_direction` | 不做读改写、重复追加、只会单向推 |
| `v22_cross_audit` | `retransmit_only`、`jpy_precision` | 见重复行就报缺单、用 float 算钱 |
| `v22_reversal_cascade` | `gamma_150ms`（恰好 150.0） | 只按 `>` 判、忽略边界 |
| unicode 题 | 随机 Cf 池 + ZWJ/ZWNJ 共现 + bound method 探针 | 硬编码 `\u200b`、一刀切 strip 全部 Cf、`weakref.ref(绑定方法)` |
