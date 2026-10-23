# SqurveBridge 当前评测指标

本文档以当前 `SqurveBridge` 代码为准，记录指标的计算方式、数据来源、聚合口径和已知限制。指标分为六层：最终 SQL 质量（L1）、运行成本（L2）、SQL 结构（L3）、错误归因（L4）、组件流程（L5）和跨方法比较（L6）。

## 0. 协议版本与当前入口

历史 `scores.json` 没有 `schema_version`，读取时固定解释为 bundle v1。统一协议通过
`python tools/eval_scores.py --checkpoint-v2 ...` 进入同一离线评测入口并生成 bundle v2；
bundle schema 版本、各 evaluator 的 `protocol_version` 和 SQLite store schema 版本是三个独立版本空间。

v2 的每个指标记录 `status`、`value`、`numerator`、`denominator`、`coverage`、候选范围、
协议身份和 unavailable 原因。当前 v2 已实现 EX@1、Selected-EX、observed Oracle@k、SF1、
self-consistency、project structural match、SQL execution timing，并在 checkpoint 提供相应证据时
计算 Actor/workflow 时间、去重后的 provider/SDK Token 用量和 L5 组件指标。VES/R-VES 及缺少
依赖证据的注册指标保留为 `unavailable`，不会补零或从 bundle 消失。

| v2 指标 | 候选/统计单元 | 计算或可用条件 |
|---|---|---|
| `ex_at_1` | 第一候选 | 第一候选与完整 gold 结果按声明比较策略相等 |
| `selected_ex` | 选中候选 | 记录的选中候选与完整 gold 结果相等 |
| `observed_oracle_at_k` | 已观测候选池 | 池内任一可比较候选正确；`actual_k` 保留实际池大小 |
| `sf1` | 选中候选 | 完整结果上的多重集行匹配 F1 |
| `self_consistency` | 候选对 | 相等结果对数除以全部候选对；失败候选不移出分母 |
| `structural_match` | 选中候选 | 两个 SQLAnalysis 的项目级结构完全匹配 |
| `sql_execution_time` | 选中候选 | 显式启用独立计时，绕过正确性缓存 |
| `actor_time` / `workflow_time` | Actor / workflow | checkpoint 含单调时钟观测；两者不相加 |
| `token_total` | logical call | 每个 logical call 只取 logical-call 汇总或 attempt 汇总之一 |
| `refinement_*` | 即时 stage pair | checkpoint 含 pre/post correctness 与两个 checkpoint identity |
| `selection_*` | selector 输入池 | checkpoint 含候选结果及实际选择 |
| `generation_candidate_diversity` | scaler 输出池 | 文本 distinct 数除以输出候选数；结构 diversity 单独保存 |
| `decomposition_trigger_rate` | decomposition observation | 只表示观测到触发，不表示收益或因果 |
| `native.spider_test_suite` | 验证后的 suite | 默认关闭；manifest 中每个 SQLite 文件实际 hash 匹配并全部执行 |

v2 的本地 SQLite 比较协议明确保留重复行，并声明行序、全局一对一列映射、额外列、
数值绝对/相对误差、NULL 与空结果语义。它属于 SqurveBridge project protocol，不能标记为
Spider/BIRD 官方分数。Spider Test Suite adapter 固定到 vendored upstream revision
`e97acc546ecbee8fa27fa8dbf025ef61493a876c`，并保持默认关闭；只有完整 suite manifest 中每个
数据库文件的实际 SHA-256 与声明匹配时
才可运行，任何缺失都返回 `unavailable`，不会退化为普通 EX。

## 1. 评测数据流

一次 reproduce 运行的结果来自以下数据：

1. 最终或中间阶段 checkpoint 中的 `query`、`pred_sql`、`db_id`、`db_type`。
2. Reduce/Parse 阶段的 `gold_schemas`、`instance_schemas`、`schema_links`。
3. Actor trace 中的 `_act_elapsed_s`、错误信息和阶段快照。
4. Token logger 产生的逐调用 token 数据。
5. 多次运行写入 `workspace/artifacts/<run-id>/scores.json` 和 `eval-store.sqlite` 的记录。

阶段指标由 `reproduce/eval/stage_eval.py` 读取 checkpoint 后调用 `core/evaluate.py`；最终和派生指标由 `reproduce/eval/bundle/build.py` 组装。

## 2. L1：最终 SQL 质量

### 2.1 EX（Execution Accuracy）

预测 SQL 和 gold SQL 都在目标数据库执行，结果由 `Evaluator.compare_pandas_table` 比较。默认使用所有 gold 列，要求每个 gold 列能匹配一个预测列；数值使用绝对误差 `1e-2`，结果行默认按顺序比较，预测多出的列在非严格模式下可能被允许。

单样本分数为 0 或 1，聚合为：

\[
EX = \frac{\text{通过样本数}}{\text{有效样本数}}
\]

多轮生成时，当前最终 EX 是 observed Oracle@k：只要任一候选执行正确，该样本记为 1。

来源：`core/evaluate.py` 的 `eval_generate_execute_accuracy` 和 `compare_pandas_table`；`reproduce/eval/utils.py` 的 `_calculate_ex_details`。

状态：可用，但与 Spider test-suite 的重复行、列数、`ORDER BY` 感知比较协议不完全相同，不能直接宣称为官方协议复现。

### 2.2 EM（Exact Match）

使用 `sqlglot` 将 SQL 解析成七个组件集合：`select`、`where`、`group`、`order`、`join`、`iuen`、`keywords`。七个集合全部相等时记为 1，否则为 0。

来源：`reproduce/metrics/evaluators.py::eval_em`，解析器为 `reproduce/metrics/sql_parser.py::parse_components`。代码注释参考 MT-Teql/NL2SQL360；不是 Spider 官方 evaluator 的逐行复现。

状态：结构方向合理，但当前组件抽取可能丢失排序方向、复杂表达式和部分列值差异，因此不能视为严格 SQL Exact Match。

### 2.3 SF1（Soft F1）

先执行两条 SQL，结果行去重后按位置配对。每行中，预测值若能在 gold 行中匹配则计入匹配分；累计得到 TP、FP、FN：

\[
P=\frac{TP}{TP+FP},\quad R=\frac{TP}{TP+FN},\quad SF1=\frac{2PR}{P+R}
\]

两边结果都为空时记为 1；gold 执行失败返回无效，预测执行失败返回 0。数值使用 `1e-2` 绝对误差。

来源：`reproduce/metrics/evaluators.py::_compute_soft_f1`。实现借鉴 BIRD Mini-Dev/NL2SQL360 的 Soft-F1 思路。

状态：适合作为软诊断分数；结果去重、位置配对和宽松值匹配会使它不等价于严格结果集 F1。

### 2.4 SC（Self-Consistency）

对同一问题的多个预测候选执行，统计结果相等的候选对：

\[
SC=\frac{\text{结果相等的有效候选对数}}{\binom{n}{2}}
\]

少于两个预测 SQL 时返回 `None`。执行失败的候选保留在原始候选数分母中，因此会降低 SC。

来源：`reproduce/metrics/evaluators.py::eval_sc`。

状态：一致性指标定义清楚，但它不代表正确性；当前结果比较器与 EX 不完全一致。

### 2.5 VES（Valid Efficiency Score）

只有预测结果与 gold 结果相等时才测时。默认重复执行 10 次，计算每轮：

\[
r_i=\frac{t_{gold,i}}{t_{pred,i}},\quad VES=\sqrt{\operatorname{mean}(r_i)}
\]

默认去除超过 2σ 的异常值；预测错误或没有有效时间比时为 0。

来源：`reproduce/metrics/evaluators.py::eval_ves`；思路来自 BIRD VES。

状态：当前是 VES-like 实现。迭代次数、异常值规则、计时范围与 BIRD 官方脚本不同，不应直接与官方 VES 数字比较。

### 2.6 RVES（Reward-based VES）

当前实现为：

```text
RVES = 0                  if VES <= 0
RVES = min(1.0, VES)      otherwise
```

来源：`reproduce/metrics/evaluators.py::eval_rves`。

状态：项目近似，不是 BIRD Mini-Dev 官方分档奖励实现。

## 3. L2：运行成本

### 3.1 Token

Token logger 按调用累计 prompt、completion 和 total tokens，并按步骤聚合：

```text
total_tokens       = 所有调用的 token 总和
avg_per_sample     = total_tokens / 评估样本数
per_call_mean      = 某步骤 token 总和 / 调用次数
per_call_p95       = 某步骤逐调用 token 总量的 95 分位数
```

来源：`core/llm/token_logger.py` 和 `reproduce/eval/bundle/build.py::_aggregate_token`。

状态：算术定义明确；是否完整取决于 token 日志是否覆盖全部 Actor。当前没有统一货币价格换算。

### 3.2 Latency

`_act_elapsed_s` 是 Actor 单次处理耗时。结果包计算：

```text
avg_s = mean(_act_elapsed_s)
p50_s = 50 分位数
p95_s = 95 分位数
```

来源：`core/task/meta/MetaTask.py`、`ComplexTask.py` 和 `bundle/build.py::_aggregate_latency`。

状态：它是 Actor 时间，不必然等于完整工作流端到端延迟；不同阶段可能覆盖同一个字段。

## 4. L3：SQL 结构

### 4.1 CF1（Component F1）

对七个 SQL 组件分别计算集合 F1：

\[
CF1_c=\frac{2|G_c\cap P_c|}{|G_c|+|P_c|}
\]

两边都是空集合时记为 1。组件为：`select`、`where`、`group`、`order`、`join`、`iuen`、`keywords`。

来源：`reproduce/metrics/evaluators.py::eval_cf1` 和 `sql_parser.py::parse_components`。

状态：用于结构诊断；组件表达能力有限，不能替代 EX。

### 4.2 FD（Feature Delta）

解析 16 个 SQL 特征，逐项计算：

\[
FD_f=count_f(pred)-count_f(gold)
\]

16 个特征为：`query_fields`、`group_by`、`order_by`、`limit`、`join`、`predicate`、`aggregation`、`scalar_function`、`subquery`、`set_operation`、`math_compute`、`logical_connector`、`distinct`、`like`、`control_flow`、`window`。

来源：`reproduce/metrics/sql_parser.py::extract` 和 `compute_delta`。

状态：是结构数量偏差向量，不是语义准确率；解析失败时应视为无效。

### 4.3 Schema Linking Recall

\[
SLRecall=\frac{\text{被预测 schema 覆盖的 gold schema 数}}{\text{gold schema 数}}
\]

来源：Reduce/Parse checkpoint 的 `gold_schemas`、`instance_schemas`、`schema_links`，由 `core/evaluate.py` 和 workflow trace 计算。

状态：当前 schema 匹配采用包含/子串逻辑，可能产生误匹配；应在发布前改为规范化后的精确键匹配。

上述状态描述历史 v1。v2 `SQLAnalysis` 使用 scope-aware alias resolution 和完整 `table.column`
身份，分别输出 `resolved`、`unresolved`、`ambiguous`、`not_provided` 与 `parse_failed`；缺少 Schema
不会被解释为解析或绑定成功。

## 5. L4：错误归因

### 5.1 根因分类

只对 `EX=0` 的样本运行固定优先级决策树：

```text
执行错误
→ JOIN 差异
→ SELECT 列差异
→ WHERE 差异
→ GROUP BY 差异
→ ORDER BY 差异
→ 子查询/集合操作差异
→ 关键词差异
→ Schema Linking Recall < 0.5
→ 分类标签不一致
→ generation_error
```

其中结构差异来自 CF1 和 FD，执行错误再细分为 `syntax_error`、`column_not_found`、`table_not_found`、`ambiguous_column`、`timeout` 和 `other_exec_error`。

来源：`reproduce/metrics/errors.py::classify_error`。

### 5.2 根因分布

对每个根因 `r`：

\[
count(r)=\#\{i:EX_i=0\land root_i=r\}
\]

\[
pct(r)=\frac{count(r)}{\sum_{r'}count(r')}
\]

`pct` 的分母是已归因失败样本数，不是全部样本数。

### 5.3 阶段归因

workflow trace 按工作流顺序检查：

```text
最终 EX=1                         → success
Reduce/Parse fatal_schema_miss    → 对应阶段
Generate oracle_ex=0              → Generate
Select 丢弃正确候选               → Select
阶段有 runtime error              → 对应阶段
其他                               → unknown
```

来源：`reproduce/metrics/workflow.py::_attribute_sample` 和 `_aggregate_trace`。

状态：这是启发式主因分类，不是人工标注或因果估计；一个样本只保留一个主根因/主阶段。

v2 以每个样本记录的 selected candidate 为统计单元，同时保留 `outcome_distribution`（允许 success）与
`failure_only_bottleneck_distribution`（排除 success、单列 unknown）。每条规则结果引用实际
execution、SQLAnalysis 以及存在时的 structural metric evidence；它仍是确定性规则标签，不是因果结论。

## 6. L5：组件流程指标

### 6.1 Linking

```text
linking_recall          = Reduce/Parse schema recall 的均值
linking_precision       = Reduce/Parse schema precision 的均值
linking_fatal_miss_rate = 发生关键 schema 遗漏的样本数 / 观测样本数
```

### 6.2 Generation / Scale

```text
generation_pass1              = 第一候选 EX 的均值
generation_oracle_k            = 候选中任意正确的比例
generation_exec_validity       = 可执行候选数 / 候选总数
generation_candidate_diversity = 不同候选数 / 候选总数
```

### 6.3 Refinement / Optimize

```text
refinement_fix_rate         = EX_before=0 且 EX_after=1 的比例
refinement_degradation_rate = EX_before=1 且 EX_after=0 的比例
refinement_net_gain         = Σ(EX_after - EX_before)
refinement_debug_turns      = 平均调试轮数
```

### 6.4 Selection

```text
selection_accuracy            = 实际选中候选的 EX
selection_regret              = oracle_ex - selected_ex
selection_missed_correct_rate = 有正确候选但最终选错的比例
```

### 6.5 Decomposition

```text
decomposition_trigger_rate     = 触发分解的样本数 / 总样本数
decomposition_trigger_accuracy = 触发分解样本的最终 EX
```

历史 v1 指标由 `reproduce/eval/sample/process.py` 从阶段指标、trace 和
`pred_sql_before_<stage>` 快照派生。v2 从 checkpoint 的 `component_observations` 构造类型化即时观察，
写入 bundle、SQLite store 和报告；未提供的已注册指标显式输出 unavailable。

状态：这些是项目自定义流程诊断指标。它们依赖完整快照和统一 EX 比较器；没有未使用组件的对照运行时，不能解释为因果增益。

### 6.6 Funnel

运行漏斗按顺序计算：

```text
parseable → timely → executable → correct → efficient
```

每一门的 survival rate 为通过该门的样本数除以总样本数。当前 `parseable` 主要检查 `pred_sql` 非空；没有效率预算时 `efficient` 为未知。

## 7. L6：跨方法比较

输入是共同样本集合上的矩阵 `(query, method) -> (correctness, latency)`。

### 7.1 Oracle Gap

\[
EX_{static}=\max_m\mathbb{E}_q[Y_m(q)]
\]

\[
EX_{dynamic}=\mathbb{E}_q[\max_mY_m(q)]
\]

\[
oracle\_gap=EX_{dynamic}-EX_{static}
\]

它表示逐题 Oracle 选择相对于最佳固定方法的理论上限，不是实际路由器收益。

### 7.2 Method Disagreement

正确性分歧：

\[
D_{sample}(i,j)=\mathbb{E}_q[\mathbf{1}(Y_i(q)\neq Y_j(q))]
\]

效率分歧：

\[
D_{eff}(i,j)=\mathbb{E}_q\left[\frac{|t_i(q)-t_j(q)|}{t_i(q)+t_j(q)}\right]
\]

组合距离：

\[
D(i,j)=\frac{D_{sample}(i,j)+D_{eff}(i,j)}{2}
\]

### 7.3 Empirical Difficulty

```text
N(q) = 能正确解决 query q 的方法数量
```

输出每个 query 的 `N(q)` 和直方图。

### 7.4 Uniquely Solved

统计只有某一个方法正确解决的 query 数量，表示方法的不可替代性。

### 7.5 Efficiency Headroom

对每个难度层级 `N(q)`，只看正确方法的耗时：

\[
headroom=\frac{\operatorname{mean}(t_{max})-\operatorname{mean}(t_{min})}{\operatorname{mean}(t_{max})}
\]

来源：`reproduce/eval/views/matrix.py`。公式与动态工作流研究中的跨工作流比较定义一致，但输入运行必须共享样本、模型、配置和可比的时间环境。

## 8. 统计与结果字段

当前结果包包含：

```text
aggregate
by_hardness
by_component_hardness
by_db_type
by_sql_feature
by_scenario
qvt
per_sample
stage_metrics
workflow_trace
```

EX/EM 在结果组装时可附加 Wilson 95% 区间；连续指标提供 bootstrap 工具，但并非所有输出都已接入区间。每个指标应同时查看 `valid`、`total` 和样本范围。

## 9. 当前可信度结论

可以直接作为工程诊断基础的内容：统一运行内的 EX、Token 汇总、Latency 汇总、CF1/FD 结构差异、候选集和优化前后差值。

需要标记为近似或项目自定义的内容：SF1、VES、RVES、Schema Linking 精确匹配、L4 根因、L5 阶段归因和所有 Oracle 指标。

发布或论文比较前必须先统一：结果表比较器、Schema 精确匹配、EM/CF1 解析、`EX@1`/`Oracle@k`/`Selected-EX` 命名，以及组件前后快照的可比性。

## Run detail presentation and signal purpose (#41–46)

Live and history run details share four layers: L1 EX + SF1; L2 R-VES
with VES as reference; L3 EM and all available clause CF1 values; L4 observed
outcomes, process evidence and error attribution. History summary cards remain
EX-primary. The eight-column sample table precedes attribution. Missing evidence
is displayed as unknown. Questions are omitted on hosted deployments and raw
SQL/error payloads are never part of this projection.

`ex_variant` is `spider`, `bird`, or `custom`. It describes the evaluator protocol,
not the dataset name. The current reproduce evaluator emits `custom`; legacy
bundles without provenance also read as custom. Official labels require actual
execution of the corresponding declared evaluator. EX displays its variant even
on a single benchmark. This avoids silently claiming official benchmark scores.
Current R-VES remains the project approximation described above; no official
binned thresholds are implemented, so the expandable definition shows the actual
formula instead of fabricated bins.

The version-1 `aggregate.process` outcome contract partitions *observed final
outcomes*: `correct_ratio` (CR) is EX=1, `incorrect_ratio` (IR) is EX=0 without
an execution error, and `error_ratio` (ER) is EX=0 with an execution error. Their
common denominator is `outcome_observed`; `outcome_coverage` reports that count
over all samples. No observed outcomes means null ratios. These project-defined
ratios are not a claim of complete NL2SQLBench taxonomy conformance.
C2I uses the existing `refinement_degradation_rate` and per-sample optimizer
`degradation` fields; it is red only when the aggregate rate is positive.
Unobserved before/after execution stays unknown. This is an observed change,
not causal proof that an optimizer caused the result.

### Offline gold evaluation versus production feedback (#45)

`eval_gold_*` denotes a **purpose namespace** for label-dependent EX, EM, SF1,
R-VES, clause CF1 and CR/IR/ER. Existing score-bundle metric IDs retain their
compatibility names. These signals belong to outer-loop evaluation and must not
be passed off as label-free production feedback. C2I based on gold before/after
EX is also an offline evaluation signal.

`feedback_*` denotes inner-loop observations available without reference SQL:
execution success/error (separate from EX correctness), syntax/parse validity,
latency and observed token usage with coverage. Missing usage is unknown, never
zero. An optional judge or solver must declare its protocol, inputs, cost,
provenance and uncertainty; it cannot inherit an EX correctness claim. FLEX,
ROSE and SpotIt remain optional offline research directions, not new production
judges or primary Demo UI features. No new judge is implemented by this note.
Evolution acceptance profiles must state each signal's purpose and dependencies;
review approval does not turn an offline signal into production feedback.

Strict Evo acceptance and offline checks are described in
[Verifiable evolution](VERIFIABLE_EVOLUTION.md). The detailed Demo dashboard
separates scope, correctness, efficiency/cost, structure, process/errors and
sample evidence. Each table includes units, meaning, sample counts and missing
reasons; a missing fingerprint never supports a comparison claim.
