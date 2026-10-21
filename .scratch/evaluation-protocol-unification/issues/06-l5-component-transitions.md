# 06: 修正 L5 组件过程指标

**What to build:** 让组件评测严格使用该阶段即时前后的 Task、候选和 checkpoint 证据，分别展示优化、选择、扩展和分解行为，避免把下游变化归到错误组件。

**Blocked by:** 02: 统一候选口径与比较策略；03: 统一 ExecutionResult 与执行证据复用；04: 统一 SQLAnalysis、结构诊断与 Schema 对齐。

**Status:** completed

- [x] 每个阶段观察绑定精确的 Task、样本、候选、iteration 及即时 pre/post checkpoint；缺失 checkpoint 保持 unknown。
- [x] 优化报告同时给出 wrong-to-right、right-to-wrong 转移计数、条件率、eligible pair 数和规范化 net EX change。
- [x] 选择报告给出输入候选 Oracle、selected correctness、regret、missed-correct 和未知候选结果，且分母可检查。
- [x] Scaler 的 gain/survival 与 textual/structural diversity 分开；Decomposer trigger 统计不表述为因果收益。
- [x] downstream 再次修改 SQL 的测试不会改变先前组件的即时归因。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
