# 03: 统一 ExecutionResult 与执行证据复用

**What to build:** 让每次 SQL 执行产生可追溯的 ExecutionResult，覆盖运行、样本、阶段、候选、数据库快照、结果完整性、规范化错误和计时范围。普通正确性指标可以安全复用同一执行事实，效率实验仍保留独立测量。

**Blocked by:** 01: 建立 Evaluation Bundle v2 最小闭环；02: 统一候选口径与比较策略。

**Status:** completed

- [x] 成功、执行错误、超时、不支持能力、缺失输入和 ground-truth 不可用具有不同状态及稳定的后端错误代码来源。
- [x] 结果记录区分执行成功、fetch 完整性、保存完整性、已观测行数和已知总行数；部分结果不能满足严格结果比较。
- [x] 正确性复用键包含 SQL、数据库快照、方言、凭据范围及会话相关设置，并对非确定查询保守失效。
- [x] 重复效率测量绕过正确性缓存，分别记录查询、连接、fetch、materialization 和整体计时范围。
- [x] 后端不支持 timeout、cancellation、schema 或 explain 时显式报告 unsupported，不模拟成功。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
