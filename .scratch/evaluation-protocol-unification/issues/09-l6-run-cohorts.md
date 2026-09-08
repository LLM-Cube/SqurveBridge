# 09: 建立严格的 L6 运行 cohort 比较

**What to build:** 让跨方法矩阵只比较显式声明且兼容的运行 cohort，并展示样本交集、排除原因、缺失情况和 latency coverage，避免把不同设置或不同运行来源拼成一个单元格。

**Blocked by:** 02: 统一候选口径与比较策略；03: 统一 ExecutionResult 与执行证据复用；07: 统一 SF1、SC、VES、R-VES、耗时与 Token 口径。

**Status:** completed

- [x] cohort 校验 dataset、split、数据库快照、样本 ID、候选协议、evaluator/protocol 和模型配置；有意变化的维度必须显式声明。
- [x] correctness 与 latency 对同一单元格来自同一运行及候选证据，禁止 latest-per-sample 隐式合并。
- [x] 矩阵报告 common-set size、每种排除原因、missingness 和 latency coverage；小交集不会显示为全数据结论。
- [x] 混合快照、evaluator 版本或未声明模型配置的运行被拒绝并给出机器可读原因。
- [x] Oracle headroom 明确标记为该 cohort 和已评测方法集合上的经验上界。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
