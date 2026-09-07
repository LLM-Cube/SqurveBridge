# 运行证据字段矩阵与实施切片

Status: common-runtime-implemented; method-specific-adapters-follow-up

依据：[已确认采集契约](runtime-evidence-contract.md)。Q1–Q28 的语义约束已确认；本文将其映射为拟议字段、条件与实施顺序，尚待整体审阅。未标注的实现细节不应被当作已获批准的独立产品决策。

## 1. 统一判定

R = REQUIRED；C = CONDITIONAL；O = OPTIONAL；I = OPT_IN。每个 C 字段必须声明条件及条件证据，不以字段有无反推条件。能力声明、采集配置、验收配置和契约版本随运行保存。

字段内容与字段可用性分开。条件为 unknown 时不能通过完整性验收；缺失值不能补零。内容截断、读取不完整、未保存以及消费未知分别表达，不混入同一个状态。

## 2. 公共记录

| 字段组 | 要求 | 适用条件 / 内容 |
| --- | --- | --- |
| schema_version、event_id、run_id、execution_segment_id | R | 每个新契约事件；历史适配不补造运行身份 |
| sample_id、task_id、span_id | C | 事件实际属于对应样本、已创建任务或操作 |
| parent_span_id、parent_task_id、branch_id | C | 存在明确父级 / 分支关系；根节点不虚构父级 |
| actor_class、actor_category、capability_profile_ref | R | Actor 记录；引用本次冻结的能力声明 |
| event_type、wall_timestamp | R | 所有运行事件；时间戳不证明因果 |
| monotonic_duration | C | 在同一可比较的单调时钟范围内观测到开始及结束 |
| execution_status、error_ref | C | 观测到对应结果或错误；缺少结束事件不造终态 |
| input_manifest_ref | C | 输入交付 Actor / 决策发生；区分存在和内容保存 |
| output_refs、producer_basis_refs | C | 实际产生输出；producer 必须有明确依据 |
| condition_evaluations、field_availability | R | 适用字段的契约判定记录；不要求枚举任意潜在字段 |
| collection_issues、payload_completeness | C | 采集异常、截断或内容保留状态需要报告时 |

采集进程也可能在写出问题记录前崩溃，因此读端不能用“没有 collection_issues”证明完整。缺少必需记录或未观测结束应独立触发检查。

## 3. Actor 最小事实矩阵

下列字段名称为建议映射。所有输入/输出引用指向当时实际的产物，不触发额外方法操作。

| 类别 | 最小字段与要求 | 条件依据 | 扩展与限制 |
| --- | --- | --- | --- |
| Reducer | C input_schema_ref、output_schema_ref | 接收 schema / 产生结构化 schema 输出 | scoring 实际发生且能力支持时 C score_ref；集合差异、precision/recall 均离线计算 |
| Parser | C input_question_ref、input_schema_ref、linking_output_ref | 对应输入已提供 / 结构化链接输出已产生 | 表列值链接按实际能力；C confidence 依赖评分操作，O mention；I 完整检索文档 |
| Decomposer | C decomposition_output_ref、plan_to_task_edges | 分解输出产生 / runtime spawning 发生 | 计划节点可有计划内标识，但不是 task ID；明确产生依赖时 C dependency_edges |
| Generator | C candidate_id、sql_ref、producer_span_id | 新候选实际产生 | C generation_attempt_ref；C parse_result_ref 仅在原路径实际解析时；I prompt 与原始模型响应 |
| Scaler | C input_candidate_ids、produced_candidate_ids、lineage_edges | 候选输入已提供 / 候选产生 / 明确关联产生 | C requested_count、sampling_config_ref 在使用对应参数时；不借采集增加采样或 normalization |
| Optimizer | R optimization_attempt_id；C input_candidate_ids、decision_manifest_ref、feedback_refs、output_candidate_ids | 调用发生；接收输入 / 提供反馈 / 产生输出 | no-op 沿用候选，变换新建候选；O declared_edit_intent；多候选输入不得擅自选一个 parent |
| Selector | C ordered_candidate_pool_ref、decision_manifest_ref、selected_candidate_id | 收到候选池 / 做出决策 | C scores、ranks 依赖实际评分、排名；O selection_reason；只有一个候选也保留池 |
| Agent | C chosen_action、child_task_refs、routing_refs、stop_reason | 对应调度、创建、路由或停止事实被观测 | available_actions 仅在确实提供时记录；不从 chosen_action 推造备选动作 |
| Nest | C child_operation_refs、branch_events、merge_input_refs、merge_output_refs | 对应子调用、分支、合并实际发生 | 保留已观测调度、取消、剪枝；Evaluation 计算拓扑统计，不猜测依赖 |

缺少方法专属结构化输出时声明覆盖缺口，不额外调用模型或解析器补齐。新类别至少继承公共契约，未支持的专属能力显式声明。

## 4. 操作与产物

| 对象 | 最小记录 | 约束 |
| --- | --- | --- |
| LLM / 可重试操作 | logical_call_id、可观测 attempt_id 与父引用、status、retry_observability | complete / partial / opaque 表示重试观测范围，不猜测隐藏尝试 |
| Usage | scope、所属调用/尝试、source_ref、报告的用量 | 不混加覆盖相同调用的 attempt 与 logical-call usage；字段重叠关系遵循来源定义 |
| DB | operation_id、sql_ref、数据库身份、执行状态、读取完整性、保存完整性 | candidate_ref 仅在明确关联时存在；schema 检索 SQL 不虚构 candidate |
| DB 行数 | observed_row_count、known_total_row_count | 仅在对应事实可观测时记录，未知总数不推算 |
| Artifact | artifact_id/ref、类型、内容身份信息、完整性、保存状态 | 引用能解析且内容完整才满足需要完整 payload 的依赖 |
| Field change / merge | old/new refs、显式 producer 依据、合并输入输出关系 | 无依据只保留未归属变化；不从并发前后差异断言写入者 |
| Recovery | 新 segment ID、可用的前段与 checkpoint 引用 | 不关闭原操作，不补造结束时间；旧事件重导出保持 ID |
| Legacy adaptation | 原记录、源/目标版本、适配器版本、转换来源 | 无事实依据不生成候选、关系或完整性 |

## 5. 读端判定与待审阅实现提案

- complete 只在适用契约要求可判定且满足时使用；partial 表示存在可用证据但有缺口；unavailable 表示没有可用证据；invalid 表示证据存在结构、身份或一致性冲突。聚合应保留所有具体问题，避免一个摘要状态遮蔽局部有效数据。
- 对同一 event_id 的相同重导出去重；同 ID 不同内容拟报告 identity conflict，不静默覆盖。属于实现提案，需整体审阅。
- Usage 保留所有原始报告；拟按声明范围选择不重叠的记录进行聚合。范围不清时不声称总 usage 精确完整，不能擅自选择较大值或累加。
- 跨进程单调时钟值不直接相减。跨机器时间与明确依赖证据不足时，关键路径结果不可用。
- immutable SQL 内容缺失时不能凭 hash 声称已完成精确文本比较；具体失败路径必须符合“不伪造身份、不改变业务”约束，并在实现设计中列出处理方式。
- 脱敏、大小限制、OPT_IN 与必要 payload 要求冲突时仍报告缺口；不为通过验收放松保留要求。
- 本地具体编码、产物去重范围、hash 算法、规范化版本及资源上限属于实施前需明确的技术参数；本稿未将其伪装成用户已经确认的规则。

## 6. 实施切片

| 切片 | 交付行为 | 主要验收 |
| --- | --- | --- |
| 1. 契约与本地存储 | 版本化 profiles、公共事件、manifest、不可变产物读写和校验 | 写读一致、引用失效、截断、重复导出、版本不兼容不伪装成功 |
| 2. 公共执行路径 | 顺序、嵌套、并发统一记录生命周期与上下文 | 输出/异常/调用关系不变；上下文不串样本，producer 无依据不猜 |
| 3. Actor facts 与 lineage | 九类最小契约、候选及计划/任务对应关系、决策信息清单 | 独立同文采样、优化 no-op、选中身份、计划未创建任务、缺失条件 |
| 4. LLM / DB 操作 | 调用/尝试、reported usage、计时及三种 DB 完整性 | 不重复记账；usage 缺失不补零；DB 部分结果不进入严格比较 |
| 5. 故障与恢复 | 有界采集、崩溃未结束、执行段与旧证据适配 | 业务异常保持原样，恢复不伪造旧终态，旧版本不补造事实 |
| 6. Evaluation 接入 | 依赖检查、独立有效结果、版本化验收 profile | 成本缺失时保留有效 EX；必需项缺失仍拒绝运行验收 |

各切片通过现有 Squrve 执行入口与受控替身验证；底层读写/身份校验只补必要的定向测试。并发比较业务关系和明确约束，不要求不可控的全局完成顺序一致。故障用可重复注入，真实模型/数据库外部请求不作为首轮测试必要条件。

## 7. 设计交付与实施边界

当前运行时已实现公共 evidence store、生命周期、候选/调用/DB/producer/merge 记录和离线完整性判定；字段矩阵中标为 C/O/I 的方法专属字段仍需由各 Actor adapter 在实际路径中提供。公共代码事实以当前 SqurveBridge 为依据，实验性原型遵守 workspace 的私有开发仓库边界。
