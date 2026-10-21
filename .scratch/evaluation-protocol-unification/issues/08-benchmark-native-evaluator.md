# 08: 接入可验证的 benchmark-native evaluator

**What to build:** 让受支持 benchmark 可以通过统一、默认关闭的 adapter 运行其原生评价协议，并让使用者验证上游版本、适用数据和资源身份。缺少资源或不支持时明确返回 unavailable。

**Blocked by:** 03: 统一 ExecutionResult 与执行证据复用；04: 统一 SQLAnalysis、结构诊断与 Schema 对齐。

**Status:** completed

- [x] 原生 adapter 通过一个注册入口声明 benchmark、dataset/split、上游 revision、options、依赖、许可状态和资源校验和。
- [x] 至少一个受支持 adapter 使用固定上游实现和小型已知答案用例验证结果，且安装后仍保持 default-off。
- [x] Test Suite Accuracy 只在已识别的全部 suite 数据库实例可用时运行；资源缺失返回 unavailable，不回退到普通 EX。
- [x] project metric、native metric 和 unsupported native metric 在 bundle、报告与 export 中名称和协议身份不同。
- [x] 可选资源检查不会被计为已通过的必需验收，也不产生 leaderboard 或全局语义等价声明。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
