# 10: 迁移所有消费端并完成旧协议收缩

**What to build:** 让 registry、报告、持久化、导出、Demo 和 Meta-Evo 统一消费 v2 评测定义，同时通过兼容视图继续读取历史产物；当全部调用点迁移后移除重复算法入口并完成整体验收。

**Blocked by:** 05: 用统一证据重建 L4 错误归因；06: 修正 L5 组件过程指标；07: 统一 SF1、SC、VES、R-VES、耗时与 Token 口径；08: 接入可验证的 benchmark-native evaluator；09: 建立严格的 L6 运行 cohort 比较。

**Status:** completed

- [x] Registry、JSON、SQLite、JSONL、报告、sanitized export、fitness 和 Meta-Evo 使用同一 v2 值、状态、候选与协议定义。
- [x] 已注册但未计算的指标显示 availability，不消失、不补零；历史 bundle 通过明确兼容视图读取且原校验和保持不变。
- [x] 重新评测历史数据生成新的关联产物，不静默改写或重标旧指标；无调用者后才删除重复实现入口。
- [x] sanitized export 保留协议、aggregate coverage 和 provenance，并排除原始问题/SQL、凭据、私有 URL 与本地路径。
- [x] 端到端离线验收、受影响回归、隐私与发布检查全部通过；若 Demo 展示发生变化，生产构建和 UI 验证通过。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
