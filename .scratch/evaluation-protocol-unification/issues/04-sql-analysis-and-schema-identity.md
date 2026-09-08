# 04: 统一 SQLAnalysis、结构诊断与 Schema 对齐

**What to build:** 让同一 SQL 和方言共享一次版本化 SQLAnalysis，使结构指标和 Schema Linking 指标使用一致、精确且可解释的解析与引用身份。

**Blocked by:** 01: 建立 Evaluation Bundle v2 最小闭环。

**Status:** completed

- [x] SQLAnalysis 保留 projections、表达式、谓词、literal 类型和值、JOIN 类型与条件、分组、排序方向、limit、嵌套作用域和集合操作。
- [x] parse success、Schema resolution 和数据库 execution 分别记录；未解析或歧义引用保持 unresolved/ambiguous。
- [x] Schema 表列匹配使用方言相关的规范化完整身份和作用域内 alias resolution，不使用子串匹配。
- [x] Schema exact match 仅在规范化集合完全相等时成立；缺失标注与显式空标注具有独立策略。
- [x] 简化 EM 以项目级 structural match 名称和协议版本输出，旧名称只作为明确的 legacy alias。

## Implementation Evidence

Implemented on `feature/evaluation-protocol-unification`; validated by `tests/test_evaluation_protocol_v2.py` and the repository release gate. Benchmark-native execution remains default-off and reports unavailable unless its pinned upstream implementation and checksummed resources are supplied.
