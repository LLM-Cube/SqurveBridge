# 07: 统一 SF1、SC、VES、R-VES、耗时与 Token 口径

**What to build:** 让软评分、候选一致性、效率和成本指标使用同一候选及执行身份，并分别声明协议、计时范围、有效样本和缺失原因。

**Blocked by:** 02: 统一候选口径与比较策略；03: 统一 ExecutionResult 与执行证据复用。

**Status:** completed

- [x] SF1 和 SC 声明候选范围、比较策略、尝试数、失败数、缺失数和分母；SC 不把不可执行候选静默移出分母。
- [x] 现有 VES/R-VES 使用明确的 project/legacy evaluator 身份，除非存在经过验证的 native adapter。
- [x] SQL 执行时间、Actor 时间和 workflow 端到端时间分开保存，父子耗时不相加为虚假总量。
- [x] Token 记录携带阶段所有权、调用范围和 coverage；缺失日志输出 unavailable/partial，不输出零成本。
- [x] 使用受控计时器验证采样次数、计时范围和 cache bypass，不依赖墙钟快慢排序。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
