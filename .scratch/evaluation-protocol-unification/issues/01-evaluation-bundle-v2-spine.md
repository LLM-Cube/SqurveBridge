# 01: 建立 Evaluation Bundle v2 最小闭环

**What to build:** 让评测使用者能够从保存的 checkpoint、声明式评测配置和合成数据库生成一个类型化的 v2 EX@1 结果。结果必须标明协议版本、候选身份、状态、分子、分母和 coverage，同时历史 v1 bundle 仍可读取。

**Blocked by:** None (can start immediately).

**Status:** completed

- [x] 一个离线评测运行能产出并验证最小 v2 bundle，EX@1 的候选、协议、状态和计数均可追溯。
- [x] v2 边界拒绝不一致身份、非法值、非有限值和分母矛盾，并按指标类型应用正确的值域约束。
- [x] 无版本历史产物继续按 v1 读取；v1 与 v2、bundle schema 与 evaluator protocol 使用独立版本空间。
- [x] 合成 SQLite 与保存 checkpoint 的行为测试通过，不调用 LLM、生产凭据或外部数据库。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
