# 02: 统一候选口径与比较策略

**What to build:** 让使用者在同一次离线评测中分别获得 EX@1、Selected-EX 和 observed Oracle@k，并能检查每个分数使用的候选集合及完整比较规则。所有针对选中候选的指标必须引用同一候选身份。

**Blocked by:** 01: 建立 Evaluation Bundle v2 最小闭环。

**Status:** completed

- [x] EX@1、Selected-EX 和 observed Oracle@k 分别记录候选范围、实际 k、分子、分母、失败数、不可用数和 coverage。
- [x] 比较策略显式声明行序、重复行、列顺序或一对一列映射、额外列、绝对与相对数值误差、NULL 和空结果语义。
- [x] 无序比较保持整行关系；列置换在全部行上使用同一个一对一映射。
- [x] 首候选失败而后续候选成功、选中候选不同、执行失败和 gold 不可用的行为测试均产生预期状态。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
