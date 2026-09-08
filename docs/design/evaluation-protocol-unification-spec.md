# 统一评测协议与证据契约

## Implementation Status

`feature/evaluation-protocol-unification` 已实现类型化 bundle v2、严格比较策略、SQLite
ExecutionResult 与安全复用、SQLAnalysis、规则归因、即时组件观察、成本/计时状态、默认关闭的
benchmark-native adapter 边界、显式 L6 cohort 校验，以及 JSON/SQLite/report/sanitized export
消费端。现有无版本 bundle 继续作为 v1 读取，默认 reproduce 路径仍保持历史语义；v2 通过
`tools/eval_scores.py --checkpoint-v2` 显式启用。

项目 vendored 了 Apache-2.0 的官方 Test Suite 执行器子集，并固定、校验其 upstream revision
与源文件 checksum。原生 adapter 还会读取完整 suite manifest，逐文件计算实际 SHA-256，并确认
被执行数据库目录中的 SQLite 集合与 manifest 完全一致；这一限制属于协议行为，而不是待回退的错误。

## Problem Statement

评测使用者需要知道一个分数评价了哪个候选、采用什么比较规则、哪些样本计入分母，以及能否与其他运行或 benchmark 官方分数比较。目前多个评测入口独立解析 SQL、执行查询、比较结果和解释错误；相同预测可能在 EX、SC、效率指标和阶段诊断中得到不同的正确性判断。

当前实现还存在可复现的误判：Schema 精确匹配使用 Recall 与 Precision 相等作为条件，字段匹配使用子串，结构组件丢失排序方向和部分表达式，多轮 EX 与其他指标选择不同候选。阶段耗时、执行失败、缺失观测和跨运行数据也没有足够严格的统一身份与状态约束。

这些问题使分数难以解释，使 L4 主因标签和 L5 组件收益继承上游误差，也让 L6 可能比较来自不同运行条件的记录。现有测试通过不能证明这些协议正确；需要以外部可观测行为验收。

## Solution

沿用现有 Router、Engine、Task、Actor、Dataset、benchmark adapter、评测 registry 和结果产物体系，统一四个概念：SQLAnalysis、ExecutionResult、ComparisonPolicy、EvaluationEvidence。普通质量指标共享可复用的执行事实；显式计时和 Test Suite 评测按自己的实验协议采集新证据。

使用者能够分别查看 benchmark-native scores、项目结构诊断、候选行为、运行成本和组件过程证据。每个结果携带协议版本、候选口径、观测状态及有效样本数量；历史结果保留原始含义，不会因升级被静默重标。

实施顺序为：比较与候选协议 → 执行事实 → SQL 结构与 Schema 对齐 → 归因证据 → 可选 benchmark-native adapter → 结果契约与消费端迁移。契约字段和行为回归从第一阶段开始建立，不将验证推迟到最后。

## User Stories

1. As an evaluation user, I want every score to identify its evaluator and protocol version, so that I can determine what the number means.
2. As an evaluation user, I want EX@1, observed Oracle@k and Selected-EX to be separate, so that sampling budgets are not confused with selection quality.
3. As an evaluation user, I want all metrics for a selected candidate to reference that same candidate, so that comparisons are coherent.
4. As an evaluation user, I want invalid predictions and unavailable gold evidence to have different states, so that model failures are not silently excluded.
5. As an evaluation user, I want numerator, denominator and coverage alongside each rate, so that incomplete evaluations remain visible.
6. As a benchmark user, I want official adapters to identify their upstream revision and applicable dataset, so that official-score claims can be verified.
7. As a benchmark user, I want Test Suite Accuracy to use an identified suite of database instances, so that single-database coincidences can be detected.
8. As a benchmark user, I want unsupported official evaluators to report unavailability, so that a project approximation is never substituted silently.
9. As a SQL developer, I want comparison policies to declare ordering, duplicate, column and numeric rules, so that edge cases have predictable outcomes.
10. As a SQL developer, I want NULL and empty results handled explicitly, so that absence of rows is not confused with execution failure.
11. As a SQL developer, I want one reusable SQL analysis per SQL and dialect, so that structural diagnostics agree across metrics.
12. As a SQL developer, I want structural comparison to preserve expressions, predicates, join conditions and sort direction, so that materially different SQL is distinguishable.
13. As a SQL developer, I want unresolved or ambiguous references recorded explicitly, so that parser success is not mistaken for successful Schema binding.
14. As a Schema Linking developer, I want table and column identities matched precisely, so that similarly named fields do not count as correct matches.
15. As a Schema Linking developer, I want exact matching to require complete normalized-set agreement, so that equally poor Recall and Precision cannot yield success.
16. As an Actor developer, I want every stage observation to retain Task and candidate identity, so that evidence is attributed to the correct component.
17. As an optimizer developer, I want immediate pre-stage and post-stage outcomes, so that downstream changes are not credited to my component.
18. As a selector developer, I want missed-correct rates and regret over the exact input candidate set, so that selector quality is measurable.
19. As a decomposition developer, I want trigger statistics distinguished from measured benefits, so that correlation is not reported as causal improvement.
20. As an evaluation user, I want heuristic labels accompanied by triggering evidence, so that I can inspect why a root cause was suggested.
21. As an evaluation user, I want unknown stage evidence to remain unknown, so that missing instrumentation does not become a pass.
22. As a runtime maintainer, I want normalized execution errors with preserved backend codes, so that failures can be classified without fragile message ordering.
23. As a runtime maintainer, I want backend capability reporting for timeout, cancellation, schema and explain, so that unsupported features are not simulated.
24. As a performance analyst, I want SQL execution time, Actor time and end-to-end time distinguished, so that efficiency comparisons use the intended quantity.
25. As a performance analyst, I want repeated timing observations separate from reusable correctness evidence, so that caching cannot inflate efficiency scores.
26. As a cost analyst, I want token coverage and stage ownership recorded, so that missing logs are not reported as zero usage.
27. As a cross-method analyst, I want explicit run cohorts and sample alignment, so that a matrix cannot silently mix model configurations or execution records.
28. As a cross-method analyst, I want overlap and missingness reported with Oracle metrics, so that gains on a small common subset are not generalized.
29. As a report reader, I want structural similarity clearly separated from benchmark EM, so that project metrics are not mistaken for official scores.
30. As a maintainer, I want typed, versioned bundles that reject inconsistent identities and values, so that invalid artifacts fail at the boundary.
31. As a maintainer, I want legacy callers and historical bundles to remain readable, so that migration can proceed incrementally.
32. As an evidence publisher, I want sanitized exports to retain metric provenance and coverage, so that public artifacts remain useful without exposing private data.
33. As a Meta-Evo user, I want comparisons and fitness to reject incompatible or insufficient evidence, so that automated decisions do not rely on misleading scores.
34. As a maintainer, I want deterministic offline acceptance tests using synthetic databases and saved checkpoints, so that changes can be validated without LLM calls or production credentials.

## Implementation Decisions

### Scope and ownership

- The public implementation repository owns this change. Its real Demo remains a consumer; the separate presentation website and private manuscript are outside implementation scope.
- 本文同时作为已实现 v2 边界的设计依据；历史 v1 算法仍按原协议保留。
- Evaluation-specific analysis, comparison, aggregation and evidence interpretation belong to the evaluation subsystem. Database execution continues through the existing core execution boundary.
- Keep dependency direction explicit: core execution must not import the experiment runner or evaluation subsystem. Share core-safe result types and use adapters or injection for evaluation-specific comparison policies where existing core evaluation callers need compatibility.
- Legacy metric modules contain both forwarding facades and active algorithms. Inventory consumers and migrate individually; do not delete or deprecate the entire namespace in one step.
- Preserve current Actor and Task interfaces except for additive evidence metadata required by the contract. This work does not redesign workflow orchestration.

### SQLAnalysis and Schema evidence

- Retain SQLGlot and the existing pandas result representation. Pydantic and SQLAlchemy already exist as dependencies; their presence is not evidence that runtime integration is complete.
- SQLAnalysis records parser version, dialect, SQL identity, parse status, scoped references, projections, predicates, joins, grouping, ordering, subqueries, set operations and structural feature counts.
- Preserve literal type/value, arithmetic, column qualification, join predicates and type, ordering direction, limits, nested scopes and set-operation semantics. Do not collapse semantically relevant information into short string fragments.
- Parse success, Schema resolution and database execution are distinct observations. AST differences describe structural edits; they are not a semantic equivalence proof.
- Schema references use normalized database/catalog/schema/table/column identity appropriate to the dialect. Resolve aliases within scope; do not use substring matching. Case folding follows backend identifier rules.
- DatabaseSchema represents observed tables, columns and available relationships, while gold_schemas remains task-level relevance annotation. Reflection does not invent relevance ground truth.
- Schema exact matching requires complete normalized-set equality. Missing annotation is unavailable; empty annotated sets have an explicit, tested policy.
- Keep the seven-component and sixteen-feature diagnostics where useful, but version their extraction semantics. Rename the simplified EM to a clearly namespaced structural-match metric; keep a documented legacy alias rather than changing historical interpretation.

### ExecutionResult and backend boundary

- ExecutionResult carries execution identity, run/sample/stage/candidate identity, backend/dialect, result rows and column metadata, row count/completeness, status, timing scope and normalized error type/code/message.
- Distinguish success, execution error, timeout, unsupported capability, missing input and unavailable ground truth. Truncated or partially fetched results cannot establish exact equality.
- Reuse correctness evidence only for the same SQL, database snapshot, dialect, credentials scope and session-relevant settings. Do not reuse across changed data or nondeterministic queries without an explicit validity policy.
- Repeated efficiency measurements bypass correctness caches and record each timing observation. Query execution, connection/setup, fetching and materialization scopes are named; matched conditions apply to gold and prediction.
- Evolve the existing backend facade incrementally. SQLAlchemy Core may handle common connection and reflection paths; native drivers retain cancellation, timeout, query profile and backend-specific behavior. New backend support is not required merely to introduce the contract.
- Preserve existing execution restrictions. Evaluation of SELECT tasks uses controlled read-only execution where supported; no new support for mutating SQL is implied.

### ComparisonPolicy and candidate protocol

- Every comparator declares row-order sensitivity, column-order/matching rules, duplicate preservation, extra-column handling, numeric absolute/relative tolerance, NULL equality and empty-result semantics.
- Benchmark policies are explicit, versioned choices. A generic policy is not labeled official because it resembles one benchmark's rules.
- Unordered comparison preserves whole-row relationships. If a policy permits column permutation, use a consistent one-to-one column mapping across all rows; per-column independent sorting cannot establish equality.
- Record candidate identity and generation iteration. EX@1 evaluates the declared first candidate, Selected-EX the recorded selection, and observed Oracle@k the maximum over a named candidate set with its actual size. This is not an unbiased pass@k estimator.
- Other candidate-specific metrics consume the declared candidate, not an implicit first row. SC declares its attempted/missing/failed candidate denominator explicitly.
- A model-produced missing, invalid or timed-out prediction is a failure when gold and evaluation infrastructure are available. Gold/infrastructure failures are separately unavailable or failed evaluation states with counts and reasons. No metric may silently change the sample denominator.
- SF1 is a separate soft-scoring policy over shared execution facts. It need not numerically agree with EX; local changes and upstream-native scoring remain distinguishable.
- Existing clipped RVES and local VES remain identified as legacy/project metrics until explicitly replaced or accompanied by versioned native adapters.

### EvaluationEvidence, components and attribution

- Each metric references the analysis, execution, comparison and stage observations used to produce it, including applicability, availability, units, candidate scope and denominator.
- Preserve stage outputs at their immediate boundary with exact Task identity. Final workflow output must not stand in for an earlier stage's output. A missing checkpoint is not a passed stage.
- Component reports distinguish per-sample, per-stage, per-candidate and per-iteration aggregation. Display whether a rate is a macro mean or pooled count; do not combine max valid counts with an unrelated mean without labeling.
- Refinement reports both transition counts and conditional rates: wrong-to-right divided by known wrong-before samples; right-to-wrong divided by known correct-before samples. Also expose total eligible pairs and normalized net EX change. Preserve legacy unconditional rates under explicit names when needed.
- Selection reports candidate Oracle, selected correctness, regret, and missed-correct rate with the declared denominator; unknown candidate outcomes remain visible. Diversity specifies textual versus structural distinctness.
- Decomposition trigger statistics describe behavior. Claiming benefit requires matched comparison evidence outside the basic trigger report.
- L4 emits rule identifiers/version, supporting evidence, alternative observed symptoms and an optional deterministic primary label. Root-cause wording remains heuristic; no causal claim or confidence probability is invented.
- Failure-only bottleneck distributions exclude success and expose unknown separately. General outcome distributions may retain success under a distinct name.
- Validity funnels use actual parsing, execution and timeout observations. Missing error text cannot imply execution success, and a nonempty string cannot imply parse success.
- Stage survival is an explicitly described diagnostic heuristic; a missing gold field is not proof that a later component cannot recover it.
- QVT grouping includes database/snapshot identity and an explicit equivalence or perturbation-group definition. Merely identical SQL text across databases is insufficient.

### Benchmark-native evaluators

- Benchmark packages own native protocol metadata and resources. The metric-adapter flow remains optional and default-off, with applicability, dependency, licensing and confirmation requirements retained.
- The attachment proposes benchmark-owned evaluator adapters, whereas the existing reference describes a separate optional external-metric boundary. Adopt benchmark ownership for native semantics in this design, but retain one integration contract and registry entry path; update the old reference and migration mapping together rather than creating competing plugin systems.
- Introduce native evaluators incrementally for supported, reproducible benchmark inputs. Record upstream revision, options, dataset/split, database identity and resource checksums. No adapter becomes default merely because it is installed.
- Test Suite Accuracy requires identified suite databases and execution on each required instance. Without those resources return unavailable, not a generic EX fallback. Passing a finite suite is stronger empirical evidence, not a proof for all possible databases.

### Bundles, consumers and migration

- Use typed Pydantic boundary models for MetricValue, MetricEvidence, SampleScore, AggregateScore and EvaluationBundle. Evolve the existing bundle contract instead of introducing a parallel artifact format.
- Introduce explicit bundle version 2 on the existing integer version convention. Bundle schema version and evaluator/protocol versions are independent. Existing unversioned artifacts remain v1; eval-store schema v2 already exists and is a separate version space.
- Validate metric-specific bounds, non-finite values, identity references, availability and denominator consistency. Do not constrain unbounded VES or signed FD to generic probability bounds.
- Report, registry, export, storage and fitness consume consistent definitions. Registered-but-uncomputed metrics must show their availability state, not disappear or become zero.
- Retain JSON, SQLite and JSONL. Historical evidence bundles remain immutable and checksum-valid; reevaluation creates a new linked artifact. Legacy readers continue to work through an explicit translation or compatibility view.
- Store Actor duration and workflow end-to-end duration separately. Preserve per-stage timings instead of overwriting one scalar. Token usage carries logging coverage and execution scope; absent logs are not zero cost.
- L6 accepts explicit run cohorts, enforcing compatible dataset/split/snapshot, sample identifiers, candidate protocol, evaluator and model/configuration scope. Deliberately varied dimensions are named. Correctness and latency for one cell come from the same run/candidate evidence.
- L6 reports common-set size, exclusions and latency coverage. Do not use latest-per-sample merging across runs. Oracle headroom is labeled an empirical upper bound over the evaluated methods and cohort.
- Retain Wilson intervals for appropriate binary observations and bootstrap support for declared continuous summaries. State the statistical unit; correlated candidates or repeated runs are not independent samples. Scope labels are not significance tests.
- Sanitized exports retain protocol, aggregate coverage and provenance without raw benchmark questions/SQL, credentials, private URLs or local paths. Privacy and publication-completeness checks remain acceptance gates.

## Testing Decisions

- Proposed primary acceptance seam: saved Dataset/Task checkpoints plus a declared evaluation configuration and a synthetic local database enter the existing offline evaluation flow; the observable outputs are validated score bundles, reports, store records and sanitized exports. Reuse this flow rather than adding a separate test-only evaluation engine.
- 用户已确认该测试 seam；实现测试使用保存 checkpoint、声明式配置和合成 SQLite。
- Test public behavior: expected scores, statuses, evidence identities, numerator/denominator, protocol metadata, reader compatibility and export content. Do not snapshot internal AST object layouts or copy production algorithms into expected-value calculations.
- Use small, hand-verifiable synthetic SQLite fixtures and saved checkpoints. No LLM calls, real benchmark redistribution, production credentials or external database services are required for the primary suite.
- Prior art is the existing evaluation-foundation, bundle-build, process-layer, evaluation-view, checkpoint/resume and evidence-export regression suites. Preserve their unittest execution path; adopting pytest is not a prerequisite for this work.
- Add focused comparator cases only where they express an independently meaningful public contract: unordered whole rows, duplicates, column permutations, extra columns, NULL, empty results, numeric tolerances and truncated results.
- Add structural cases for reversed ordering, changed arithmetic, changed right-hand column, changed predicate literal, JOIN type/condition, nested scope, IN/LIKE/BETWEEN values, ambiguous references and similarly named Schema fields.
- Add cross-metric cases where a first candidate fails but another succeeds, execution fails, gold is unavailable, or a stage checkpoint is missing. Verify that candidate-specific quality, stage evidence and attribution remain aligned.
- Add component cases with both known correct and incorrect inputs to distinguish unconditional transition shares from conditional fix/degradation rates. Verify exact stage-pair identity when downstream stages alter SQL.
- Add cross-run cases rejecting mixed snapshots, evaluator versions and implicit latest-per-sample merges, and report reduced sample intersection explicitly.
- Add serialization tests for v1 reads, v2 round trips, unsupported versions, invalid values and sanitized exports. Existing published bundles are aggregate-only; use synthetic records for per-sample replay rather than claiming historical full-run equivalence.
- For native adapters, use small known-answer cases against a pinned upstream evaluator and its explicit options. Resource-dependent Test Suite checks remain optional and report unavailability clearly; optional checks cannot silently count as passed.
- Efficiency tests assert timing scope, sampling count and cache bypass using controlled clocks; avoid brittle wall-time rankings. No claim of live backend parity follows from mocked adapters.
- Later real-backend integration tests may use Testcontainers as a separate optional suite. Current acceptance does not require a test-framework migration or deployment.
- Required completion evidence: semantic regressions pass, existing affected regression suites pass, consumer contract/privacy checks pass, and affected Demo displays build and receive UI verification when changed. Passing tests cannot substitute for unavailable native benchmark resources.

## Out of Scope

- A new generic SQL framework, a parallel database execution system, replacement of the Router/Engine, or wholesale rewriting of Actors.
- New parser stacks such as Calcite, JSqlParser or SQLFluff for the same core responsibility.
- Mandatory migration to SQLAlchemy for every native operation, or adding every database dialect in the first release.
- Replacing unittest with pytest as a condition for delivering evaluation correctness.
- Parquet/PyArrow, DuckDB, distributed execution, and OpenTelemetry deployment. Preserve extension opportunities without adding these systems now.
- Claims of causal component responsibility, formal SQL equivalence, statistically significant model improvement, or new benchmark leaderboard results without the required experiments.
- Regenerating benchmark suites, bulk downloading datasets, rerunning paid model experiments, rewriting historical evidence, public release deployment, or changing the presentation website and manuscript.

## Further Notes

- This spec synthesizes the supplied proposal and inspected implementation. Earlier illustrative directory trees are not authoritative: the Demo currently uses flat API/runtime modules, the harness is principally installation scripts and documents, and active metric algorithms remain in the legacy namespace.
- The local audit reproduced semantic edge cases while 49 existing evaluation-related tests passed. This motivates new behavioral regressions; it does not quantify the effect on historical benchmark scores.
- Primary source references for protocol work: [Spider evaluator](https://github.com/taoyds/spider), [Test Suite evaluator](https://github.com/taoyds/test-suite-sql-eval), [BIRD VES](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/main/bird/llm/src/evaluation_ves.py), [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev), [SQLGlot](https://sqlglot.com/), [SQLAlchemy Core](https://docs.sqlalchemy.org/en/20/core/), and [dynamic workflow comparison definitions](https://arxiv.org/abs/2602.15564). Pin exact upstream revisions when implementing adapters; links alone do not establish parity.
- Publication state: implemented on the feature branch and tracked through the repository local Markdown issue tracker.
