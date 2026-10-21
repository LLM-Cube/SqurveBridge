# 运行数据采集优化与证据链统一

Status: implemented
Specification: published to local issue tracker
Decision baseline: Q1–Q28 confirmed; Q28 testing seam confirmed

本规格汇总已确认的 Q1–Q28，按 to-spec 登记为 ready-for-agent。字段矩阵中的未确认实现提案保持其提案属性；规格发布本身不代表所有六个实施切片均已完成。

- [规范性采集契约](../../docs/design/runtime-evidence-contract.md)：已确认原则与 Q8–Q28 规则。
- [字段矩阵与实施切片](../../docs/design/runtime-evidence-field-matrix.md)：待整体审阅的字段映射、条件、实施次序。
- [领域术语](../../CONTEXT.md)：概念定义。

Implementation tickets:

- [01 Contract and local store](issues/01-contract-local-store.md)
- [02 Common execution path](issues/02-common-execution-path.md)
- [03 Actor facts and lineage](issues/03-actor-facts-lineage.md)
- [04 LLM and DB operations](issues/04-llm-db-operations.md)
- [05 Failure, recovery and legacy evidence](issues/05-failure-recovery-legacy.md)
- [06 Evaluation dependency and acceptance](issues/06-evaluation-acceptance.md)

## Problem Statement

运行使用者需要知道 Actor 实际看到和产生了什么、哪些操作确实发生、数据是否完整，以及某个指标为什么可计算或不可计算。现有 trace 摘要、阶段快照及 token 日志不足以统一表达候选实例、调用/重试范围、并发生产者归属与采集故障；截断摘要也不能作为完整产物使用。

采集器必须保留这些事实，同时避免改变原方法输出、异常、调用、决策和执行关系。业务执行、采集完整性与评测验收需要分别判定。

## Solution

采用公共执行采集入口、版本化 Actor 能力与字段契约、本地权威证据存储，以及独立 Evaluation 消费层。覆盖顺序、嵌套和并发的公共路径，为每类 Actor 定义最小事实契约；方法专属能力通过版本化适配器增量覆盖。

运行证据支持还原已观测路径、检查决策时可用信息，以及在外部证据齐全时离线重算指标。不承诺确定性重放或所有方法的完整专属覆盖。采集有界且 fail-open；严格验收依据预声明 profile，不改写业务结果或抹掉其他有效指标。

## User Stories

1. As a runtime user, I want collection to preserve business behavior, so that observing a method does not change its decisions.
2. As an operator, I want collection failures isolated, so that a completed model call is not retried because logging failed.
3. As an evaluator, I want explicit evidence gaps, so that unavailable results are not represented as zero.
4. As an Actor developer, I want versioned capability declarations, so that unsupported fields are distinguishable from collector failures.
5. As an evaluator, I want conditions evaluated from independent evidence, so that missing outputs cannot excuse missing observations.
6. As an operator, I want collection settings checked against requirements, so that disabling evidence cannot silently bypass acceptance.
7. As a generation researcher, I want equal SQL from independent samples to retain separate candidate identities, so that sampling cost and frequency remain visible.
8. As an optimizer developer, I want no-op attempts recorded separately, so that unchanged candidates do not hide repeated work.
9. As a selector developer, I want the decision-time candidate pool preserved, so that selection analysis uses the actual alternatives.
10. As an evaluator, I want available information distinguished from observed consumption, so that input presence does not become a causal claim.
11. As a schema developer, I want actual structured outputs referenced, so that evaluation can compare them with reference evidence offline.
12. As a method developer, I want collectors to avoid additional method operations, so that instrumentation does not introduce new parsing or execution.
13. As an orchestration developer, I want plans separated from created tasks, so that unexecuted proposals are not counted as runtime work.
14. As a workflow analyst, I want explicit branch and dependency events, so that missing causal links are not guessed.
15. As an operator, I want a decision information manifest with payload retention states, so that omitted content is visible.
16. As an operator, I want bounded collection resources, so that evidence export cannot block a run indefinitely.
17. As a workflow analyst, I want explicit producer evidence, so that concurrent state changes are not attributed to the wrong Actor.
18. As an evaluator, I want metric computability separated from run acceptance, so that valid metrics survive unrelated evidence failures.
19. As a cost analyst, I want logical calls and observable attempts distinguished, so that retry usage is not counted twice.
20. As a performance analyst, I want measured duration boundaries, so that parent and child durations are not incorrectly summed.
21. As a cost analyst, I want observed usage separated from estimates, so that missing provider data remains identifiable.
22. As a SQL evaluator, I want execution, fetch and retention completeness separated, so that partial results do not silently pass full-result comparison.
23. As an operator, I want unobserved completion preserved, so that crashes do not generate invented outcomes.
24. As an operator, I want resumed segments linked to prior execution, so that recovery and duplicate exports can be distinguished.
25. As a historical-results user, I want source-preserving adapters, so that new schemas do not invent facts about old runs.
26. As an evaluator, I want local reference resolution and version handling, so that offline evaluation is independent of an external telemetry service.
27. As a maintainer, I want declared coverage boundaries, so that common-path support is not advertised as complete method-specific instrumentation.
28. As a maintainer, I want end-to-end non-interference tests, so that the instrumentation contract has behavioral evidence.

## Implementation Decisions

- Introduce shared collection at actual Actor invocation boundaries, including existing direct act calls; a new base-class call method alone is insufficient.
- Cover Actor returned artifacts, Dataset mutations and orchestration merges. The bridge content encoder is a database-value matching component and is not the state provenance boundary.
- Freeze versioned CapabilityProfiles at run start; preserve contract and collection settings, and predeclare the versioned acceptance profile.
- Field requirements are REQUIRED, CONDITIONAL, OPTIONAL and OPT_IN. Availability is independently present, not_provided, not_applicable, disabled, collection_failed or redacted. Conditional evaluation is true, false or unknown with supporting evidence references; unknown cannot imply not_applicable or complete evidence. Configuration never waives an applicable requirement; strict preflight rejects known conflicts and post-run validation checks dynamic conditions.
- Preserve separate candidate occurrence, SQL content, operation attempt, event and execution-segment identities. Independent sampling always creates distinct candidates. Optimizer no-op reuses the input candidate while retaining the attempt; exact changes to persisted extracted SQL create a new candidate with parent lineage, including whitespace or comments. Normalization never determines candidate identity.
- Keep runtime observations separate from offline analysis, retaining provenance for both.
- Retain a decision information manifest; payload retention follows the confirmed default and OPT_IN boundary.
- Capture only observed operation and explicit lineage facts. Missing causality, producer identity, usage or completion cannot be inferred.
- Distinguish logical calls from observable physical attempts and declare retry observability as complete, partial or opaque. Usage preserves source and call/attempt scope, with no overlapping double-counting. Only provider/SDK-reported usage is runtime fact; missing is not zero and offline estimates preserve their own provenance.
- Use monotonic clocks for local durations and wall-clock timestamps for correlation. Parent duration is inclusive and must not be summed with child durations as a total. Database execution status, fetch completeness and payload preservation are independent; complete-result metrics reject partial or unknown results unless their benchmark protocol explicitly permits them.
- Preserve incomplete observation when an operation has no observed end. Resume creates a new segment with available prior-run, segment and checkpoint references; re-export keeps event identity while re-execution creates new occurrence identities.
- Implement bounded local persistence, immutable or stable artifact references, deduplication and version validation before optional external export.
- Keep legacy adaptation source-preserving and retain historical metric protocol identities.
- Deliver common sequential, nested and concurrent collection plus minimum contracts for Reducer, Parser, Decomposer, Generator, Scaler, Optimizer, Selector, Agent and Nest. Declare unsupported method-specific observability explicitly. A proposed sequencing is contract/store; common execution; Actor facts; LLM/DB; faults/recovery; Evaluation consumption. Each slice must honor the confirmed non-interference and evidence semantics.
- Concrete field names, storage encoding, resource limits and unresolved mappings are reviewable implementation proposals in the field matrix, not silently accepted assumptions.

## Testing Decisions

The user confirmed this testing seam in Q28: use existing Squrve execution entry points as the primary acceptance seam with controllable LLM, DB and evidence-sink substitutes. Compare collection enabled/disabled behavior for outputs, exceptions, logical calls, decisions and execution relationships. Concurrent execution need not have an identical incidental completion order.

Cover sequential, nested, concurrent, retry, collection failure, truncation, crash, resume and offline-read paths. Verify independent equal-SQL candidates, optimizer no-op attempts, missing condition evidence, producer attribution gaps, usage overlap and partial query results. Verify strict rejection when necessary evidence is missing while other valid metrics remain available.

Existing process-layer snapshot tests and public trace redaction tests are useful compatibility precedents; they do not establish lifecycle or concurrency correctness. Add focused storage and identity checks where the top-level seam cannot precisely exercise corruption.

Evaluate collection overhead against measured resource budgets separately from behavioral equality. Run appropriate focused regressions and the repository regression suite for implementation; current design-only edits receive document and link checks.

## Out of Scope

- Deterministic replay of identical SQL or outputs.
- Collector-induced parsing, execution, retrieval, validation or generation.
- Inferred consumption, causality, producer attribution or hidden retries.
- New metric formulas or claims of complete L1–L6 computability from runtime evidence alone.
- Full method-specific coverage in the first implementation.
- External telemetry as the authoritative evidence store.
- Rewriting historical metrics under a new protocol without explicit new evaluation provenance.

## Further Notes

The implementation branch now contains all six scoped slices: bounded run-local persistence; sequential, nested and concurrent lifecycle capture; minimum adapters for all Actor categories; LLM/DB attempts, usage and completeness; mutation, merge, failure, resume and legacy evidence; and offline metric dependency/acceptance evaluation. Coverage remains `partial` only for explicitly unsupported provider-hidden retries and method-specific optional payloads, which the confirmed contract forbids the runtime from inferring. Implementation states its research track, hypothesis, supporting evidence and target repository according to workspace guidance.

## Comments

- 本轮按用户再次调用 to-spec 更新并发布现有本地 issue；复用 Q28 已确认的测试入口，不重复访谈，不将实现提案升级为已确认决策。

- Q26–Q28 已确认：顺序/嵌套/并发公共采集及所有 Actor 类别最小契约；本地证据为权威存储；使用现有执行入口进行非干扰验收。已整理字段矩阵与六个实施切片，等待整体设计审阅。

- Q23–Q25 已确认并原文写入采集契约：结束未观测不推断业务结果，不补造耗时；恢复创建执行段，重导出保留 event ID，重新执行使用新身份；历史适配保留原始证据、版本与来源，不补造缺失事实或重标历史指标协议。

- Q20–Q22 已确认并原文写入采集契约：区分逻辑调用与可观测尝试，usage 标记来源与范围并避免重复计数；单调时钟计时，父耗时包含子操作，缺失 usage 不补零；数据库执行、结果读取、内容保存分别判定，部分结果比较须有明确 benchmark 协议依据。

- Q17–Q19 已确认并写入采集契约：采集及导出必须有界，截断不能伪装完整；producer 必须有明确输出、修改或合并依据；指标可计算性与基于预声明版本化 profile 的运行验收分别判定，保留独立有效指标。

- Q14–Q16 已确认并写入采集契约：分解输出与任务创建分离，创建时记录对应关系；工作流只记录观测事件与明确因果关系；决策信息清单必须保存，必要结构化产物默认保留，大体积或敏感 payload 为 OPT_IN。信息存在与完整内容保留分别表达。

- Q11–Q13 已确认并写入采集契约：记录实际结构化输出引用；Collector 不增加方法操作；区分决策时可用信息与已观测消费；离线分析保留独立来源。Actor 字段矩阵仍待确认。

- 2026-09-07: Created as a separate local Markdown feature issue from the runtime-data collection optimization proposal.
