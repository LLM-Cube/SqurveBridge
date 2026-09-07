# Runtime Evidence Collection Contract

本契约记录已确认的顶层原则、能力声明、条件要求和候选身份规则。各 Actor 字段矩阵、条件观测方式和状态聚合细节仍在设计中；本文不代表运行时代码已实现。

## 已确认的七条原则

1. Runtime evidence MUST support reconstructing the observed execution path, recomputing derivable evaluation signals offline when required external evidence is available, and inspecting information available to an Actor at decision time. Deterministic reproduction of the same SQL/output is not required.
2. Runtime MUST record observed facts. Evaluation derives signals. Collector MUST NOT invent confidence, decisions, EX, recall, regret or root-cause judgments.
3. Field contracts MUST distinguish REQUIRED / CONDITIONAL / OPTIONAL / OPT_IN.
4. Field absence MUST distinguish not_provided / not_applicable / disabled / collection_failed / redacted. A present value uses present; null alone cannot encode availability.
5. Telemetry collection MUST be fail-open during normal execution: collection errors must not replace an Actor result or its original exception, or trigger business retries merely because collection failed.
6. Strict evaluation MUST use evidence gates. Missing required evidence makes the affected metric/run non-evaluable under its declared acceptance requirements; no value may be fabricated. Strictness applies to acceptance after execution, not to throwing collection exceptions through the Actor.
7. Actor execution status, evidence collection status and evaluation status MUST be represented independently.

## 字段要求与可用性

| Requirement | 含义 |
| --- | --- |
| REQUIRED | 在契约定义的适用范围内必须采集 |
| CONDITIONAL | 满足声明的能力或执行条件时必须采集；具体条件待逐字段确定 |
| OPTIONAL | 有则保存，缺失不违反采集契约 |
| OPT_IN | 高成本、大体积或敏感内容，显式开启后才采集 |

| Availability | 含义 |
| --- | --- |
| present | 已取得值；有效的空集合或数值零也可以是 present |
| not_provided | 方法未提供该信息 |
| not_applicable | 该字段不适用于此执行情形 |
| disabled | collection config 明确关闭采集 |
| collection_failed | 应采集的信息因采集故障未取得 |
| redacted | 内容因遮蔽策略不可见 |

可用性状态说明发生了什么，不豁免字段要求。未开启 OPT_IN 使用 disabled；各字段适用条件和违规判定需要明确声明。完整 LLM reasoning 不属于采集契约。

## 能力、要求与实际观测

CapabilityProfile MUST 由 Actor implementation、Adapter 和 Runtime configuration 共同确定，携带 profile ID 和 version，并在运行开始时冻结、随证据保存。禁止根据最终字段是否存在反推能力。

- Capability：Actor 能产生什么。
- Requirement：什么条件下必须采集。
- CollectionConfig：本次启用哪些采集。
- Availability：本次实际取得字段的情况。

四层判定之后再计算 Evidence Completeness。能力不支持与配置关闭必须分别表达；前者属于能力声明，后者属于采集配置。

CONDITIONAL MUST 包含规范定义的 when 条件。selected_candidate_id 的条件为 selection_decision_produced；candidate_scores 可以使用 scoring_performed，具体条件及适用能力写入字段矩阵。Collector 不得以目标字段存在与否证明条件成立与否。

| 场景 | 字段可用性 | 证据含义 |
| --- | --- | --- |
| Selector 在产生决策前失败 | selected_candidate_id 为 not_applicable | 若其余适用证据齐全，可以 complete |
| 已产生决策，但 Collector 漏采选中身份 | collection_failed | partial |
| 声明不支持某能力，字段条件不成立 | not_applicable | 不构成该字段的采集故障 |
| 能力支持、执行条件成立、采集开启，但采集失败 | collection_failed | 必要字段缺失导致 partial |

业务失败可以完整记录。反之，Actor success 不足以证明它产生了某种具体决策。

### ConditionEvaluation

条件求值 MUST 单独保存 condition、status 和 basis_refs，status 为 true / false / unknown。依据优先来自结构化事件或结果，不得通过目标字段存在与否反推。

| Condition status | 判定 |
| --- | --- |
| true | 按生效的字段要求验证 |
| false | 字段不适用，使用 not_applicable；不构成该字段证据缺失 |
| unknown | 不得推导 not_applicable；证据不能为 complete，严格完整性门禁不通过 |

unknown 属于条件求值，不加入 FieldAvailability 枚举。证据仅因条件依据缺失而不完整时使用 partial；已有 unavailable 或 invalid 原因不能被该规则掩盖。

### 配置与两阶段验证

CollectionConfig MUST NOT 豁免 REQUIRED 或条件为 true 的 CONDITIONAL 字段。OPTIONAL 可以关闭，OPT_IN 默认关闭。

Preflight validation 检查静态可判定的冲突；严格模式在业务运行前以 ConfigurationContractError 拒绝冲突配置。动态条件在运行后做 evidence validation。

若动态条件成立而字段采集被关闭，availability 保留真实的 disabled，同时记录 contract_violation，证据为 partial。普通模式不因此改变业务执行结果；严格门禁拒绝不满足必要证据要求的结果。配置错误、业务异常、采集故障必须分别记录。

## 候选身份与 SQL 内容身份

candidate_id 表示一次候选产生或变换的实例身份；sql_artifact_id 表示原始 SQL 内容身份；normalized_sql_hash 表示规范化 SQL 内容的指纹。三者 MUST NOT 混用。

独立 sampling 即使生成相同 SQL，也必须产生不同 candidate_id，可以引用同一个 SQL artifact。不能用 SQL hash 合并采样次数、调用成本或生成路径。

Optimizer 的一次调用对应独立 optimization_attempt_id。SQL 未改变时保持 candidate_id；SQL 改变时产生新 candidate_id，并通过 parent_candidate_id 引用输入候选。Selector 仅选择已有候选时沿用身份。候选 lineage 与 operation attempts 分别保留。

变换的 no-op 判定 MUST 比较已持久化的 extracted_sql_text 精确内容，而不是原始 LLM response。文本完全相等才沿用 candidate_id；任何空白、大小写、注释变化均产生新候选并关联父候选。该规则仅判定变换是否复用输入候选，不能用于合并独立 sampling。

原始响应与 SQL 提取产物分别表达。raw_sql_hash 标识提取后的原始 SQL 内容；normalized_sql_hash 仅供诊断，不得决定候选身份，也不证明语义等价。规范化算法与版本、跨样本产物去重范围仍待确认。

## 已确认的 Actor 事实采集边界

### Q11 — Structured Outputs

Reducer and Parser record references to structured outputs actually produced during execution.

Set comparison against gold/reference evidence, including precision, recall, coverage, and fatal-miss signals, is performed only by the Evaluation layer.

### Q12 — No Observer-Induced Operations

Collectors MUST NOT invoke additional parsing, execution, validation, retrieval, generation, or other method operations solely for evidence collection.

### Q13 — Information Availability vs Consumption

Selector and Optimizer evidence MUST preserve the candidate pool, input information, and feedback available at decision time.

"Available to the Actor" and "observed to be consumed" are distinct facts.

Consumption MUST NOT be inferred merely because information was present in Actor input. When actual consumption cannot be observed, it remains unknown.

Only operations that occurred in the observed runtime path may be represented as runtime evidence.

Additional analysis may be performed offline by Evaluation and MUST preserve its provenance.

### Q14 — Plan vs Execution

Decomposer evidence MUST distinguish decomposition output from runtime task creation.

Only actually created runtime tasks receive task identities.

Plan-to-task mappings are recorded when spawning occurs. Dependencies are recorded only when explicitly produced by the method or runtime; Collectors MUST NOT infer them.

### Q15 — Event-Based Workflow Evidence

Agent and Nest runtime evidence records observed scheduling, branch creation, start, finish, gather, pruning, cancellation, and explicit parent/dependency relationships.

Workflow topology statistics such as depth, branch count, parallelism, and critical path are Evaluation-derived signals.

Missing causal evidence MUST NOT be completed by inference.

### Q16 — Artifact Retention Boundary

Runtime evidence MUST preserve a manifest of the information available at decision time.

Extracted SQL, structured schema/linking outputs, candidate lineage, task lineage, and necessary structured decision inputs/feedback are retained by default or referenced through stable immutable artifacts.

Large or sensitive payloads, including full prompts, raw model responses, retrieved documents, conversation histories, and large query results, are OPT_IN.

Presence of an input and preservation of its full content are distinct facts. If payload capture is disabled, the system MUST NOT claim that the complete decision context has been preserved.

### Q17 — Bounded Collection

Evidence collection MUST enforce bounded memory, queue, artifact size, and write-wait limits. Collection and export MUST NOT block runtime indefinitely. Exceeding a limit records an explicit collection failure or truncation state; truncated payloads MUST NOT be represented as complete artifacts. Concrete limits are implementation parameters determined through measurement.

### Q18 — Explicit Producer Attribution

Field or artifact producer attribution MUST rely on explicit output, instrumented mutation, or merge evidence. Temporal proximity or before/after state difference alone MUST NOT establish producer identity. Unattributed observed changes remain unattributed. Merge operations record their input and output lineage explicitly.

### Q19 — Metric Computability vs Run Acceptance

Metric computability and run acceptance are independent decisions. Each metric validates its own evidence dependencies and retains valid results even when other metrics are unavailable. Strict evaluation uses a predeclared, versioned acceptance profile containing required metrics and evidence requirements. Missing required evidence causes acceptance failure without invalidating independently valid metrics. Requirements MUST NOT be weakened post hoc to make a run pass.

### Q20 — Logical Call and Attempt Accounting

LLM and other retriable operations MUST distinguish the logical call from observable physical attempts. Each observable attempt receives its own identity, status, and parent logical-call reference. Retries hidden inside an SDK or provider MUST NOT be inferred; retry observability is explicitly described as complete, partial, or opaque.

Usage records MUST declare whether they apply to an attempt or the logical call and MUST preserve their provenance. Logical-call and attempt-level usage MUST NOT be double-counted.

### Q21 — Timing and Usage Semantics

Local operation durations MUST be measured using a monotonic clock; wall-clock timestamps are retained for temporal correlation and human inspection. Parent/Actor duration is inclusive of internal waits and child operations and MUST NOT be added to child durations as a total.

Token usage records only provider/SDK-reported usage as observed. Missing usage MUST NOT be represented as zero. Offline estimates are separate derived evidence and preserve estimator identity, version, and assumptions.

### Q22 — Execution, Fetch, and Preservation Completeness

Database evidence MUST distinguish query execution status, result-fetch completeness, and payload-preservation completeness.

Observed row count and known total row count are separate facts; an unknown total MUST NOT be inferred. Successfully executing a query does not establish that its result was fully consumed or preserved.

Partial or unknown result sets MUST NOT be used by metrics requiring complete result-set comparison unless the corresponding benchmark protocol explicitly permits partial-result semantics.

### Q23 — Unobserved Completion

When an operation start is observed but its end is not observed, runtime evidence preserves the operation as incompletely observed. Absence of an end event MUST NOT be interpreted as success, failure, or cancellation. Missing end timestamps and complete durations MUST NOT be fabricated. Recovery is recorded as a separate observed event or execution segment.

### Q24 — Resume and Duplicate Identity

Resumed execution creates a new execution-segment identity and explicitly references the originating run, prior segment, and checkpoint when available. Re-exporting the same evidence occurrence preserves its event identity for deduplication. Re-executed operations always receive new occurrence identities, even when their inputs or outputs are identical. Content equality MUST NOT be used to deduplicate execution occurrences.

### Q25 — Legacy Evidence Adaptation

Historical evidence is interpreted through versioned compatibility adapters that preserve source schema version, original records, transformation provenance, and target schema version. Adapters MAY perform only transformations supported by observed historical evidence and MUST NOT fabricate missing candidates, producers, call relationships, completeness, or other runtime facts. Historical metrics retain their original protocol identity; computability under a newer contract is determined solely by the evidence actually available.

### Q26 — Initial Coverage Scope

The first implementation covers the common collection path for sequential, nested, and concurrent Squrve execution and defines a minimum fact contract for every Actor category. Method-specific evidence is added through versioned CapabilityProfiles and adapters. Unsupported observability capabilities are declared explicitly; first-version coverage MUST NOT be represented as complete method-specific coverage.

### Q27 — Local Evidence First

The first implementation persists versioned manifests, event records, and immutable or stably referenced artifacts within the run-local evidence directory. Local write/read, reference resolution, deduplication, version handling, and completeness validation are implemented before external telemetry integration. External telemetry systems are optional exporters and are not the authoritative evidence store.

### Q28 — Non-Interference Acceptance

Runtime instrumentation is validated through the existing Squrve execution entry points using controllable LLM, database, and evidence-sink substitutes. Runs with collection enabled and disabled MUST preserve defined business-behavior invariants including outputs, exceptions, logical calls, decisions, and execution relationships. Tests cover sequential, concurrent, retry, collection-failure, truncation, crash, resume, and offline-read paths. Timing equality is not required; collection overhead is evaluated separately against measured budgets.

### 适用说明

以上是已确认的语义边界，具体字段名称及 requirement 矩阵仍需确定。消费行为未知不等同于 FieldAvailability 的缺失，也不等同于 ConditionEvaluation 的 unknown：三者描述不同对象。

采集器可以记录原运行实际执行过的解析及结果；不能为生成 parse_success 而增加解析操作。离线分析产物必须能与原运行事实区分，并保留分析来源，不能回写成 Actor 决策时已知的信息。

Q16 明确了 Q13 中“保留可用信息”的范围：决策时信息清单必须保留，完整 payload 是否保存由相应采集要求与配置决定。输入存在、内容保存和实际消费是不同事实。关闭 OPT_IN 的内容使用 disabled；不能仅凭清单齐全宣称完整决策内容已保存。完整性仍相对于声明的采集契约判定。

Q15 中的因果关系来自明确的运行证据，事件时间戳或写入顺序本身不作为补造依赖边的依据。

Q17 的截断完整性标记不替代字段可用性；保存了摘要不代表保存了所需完整内容。Q18 允许记录未归属的变化，但不允许将当前 span 自动填成已证明的 producer。Q19 的 acceptance profile 与 CapabilityProfile、CollectionConfig 分别表示验收要求、方法能力和采集选择。

Q20 的 retry observability 描述重试观测范围，不替代 evidence_status。Q21 允许记录服务明确报告的零 usage，但不允许将缺失转换为零。Q22 的部分结果例外必须由对应 benchmark protocol 明确规定，采集器不能自行放宽比较要求。

Q23 保留完成未观测的事实，恢复记录不能替代原操作结束事件。Q24 区分重导出和重新执行：前者保留事件身份，后者创建新的操作实例。Q25 的原始记录及转换来源用于审计，不允许把转换后 schema 完整误称为原运行证据完整。

## 三个独立状态维度

- execution_status：业务执行状态。
- evidence_status：complete / partial / unavailable / invalid。
- evaluation_status：评测可用程度，可出现 partially_evaluable；完整枚举及聚合规则待定。

证据完整不保证全部指标可计算：gold、数据库或其他外部证据仍可能不可用。业务失败也不代表证据必然不完整，例如完整记录的一次执行错误。

讨论中的 incomplete 是“不完整”的描述，序列化状态使用已确定的 partial，不新增同义枚举。

## 指标依赖

Evaluation MUST 在计算前检查其协议声明的依赖，区分 computable、unavailable 和 invalid，并保留具体原因，例如 gold_result_unavailable。

| 指标 | 依赖示例 |
| --- | --- |
| schema_recall | predicted_schema、gold_schema |
| selection_regret | candidate_pool、selected_candidate、对应候选的执行评分 |
| actor_latency | 可用且一致的计时证据 |

依赖表只定义方向。计时遵守 Q21，结果集完整性遵守 Q22；具体字段、usage 聚合去重规则和不完整运行的验收聚合细节仍待确定。

## 已核实的实现边界

跨 Actor 状态来源包括 Dataset 行、Actor 返回值及编排器合并结果。Bridge content encoder 是数据库值匹配工具，不是状态传递容器。统一生命周期采集需要覆盖现有直接 act 调用；仅新增 __call__ 并不能覆盖当前路径。

## 规范参考

本项目四级要求是自定义映射，OPTIONAL 不等同于 OpenTelemetry 的 Recommended。参考 [OpenTelemetry attribute requirement levels](https://opentelemetry.io/docs/specs/semconv/general/attribute-requirement-level/) 和 [OpenTelemetry error handling](https://opentelemetry.io/docs/specs/otel/error-handling/)。本契约不宣称完整实现 OTel 或 OpenInference。
