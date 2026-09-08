# 05: 用统一证据重建 L4 错误归因

**What to build:** 让 L4 的每个错误标签都能追溯到统一解析、Schema、执行和结构差异证据，使用者可以检查触发规则、替代症状和未知项，而不会把启发式标签误解为因果结论。

**Blocked by:** 03: 统一 ExecutionResult 与执行证据复用；04: 统一 SQLAnalysis、结构诊断与 Schema 对齐。

**Status:** completed

- [x] 每个归因结果包含规则 ID、规则版本、候选身份、支持证据引用、替代已观测症状和可选确定性主标签。
- [x] parse、binding、execution、timeout 和结构差异来自实际对应观察；缺失错误文本不能推断执行成功，非空 SQL 不能推断解析成功。
- [x] failure-only bottleneck 分布排除成功并单列 unknown；一般 outcome 分布使用不同名称表达成功。
- [x] 相同输入离线重复计算得到相同规则结果，且不产生 confidence probability 或因果声明。
- [x] 报告、bundle、store 和 sanitized export 均保留归因协议与证据来源。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
