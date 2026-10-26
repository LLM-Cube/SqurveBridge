# Text-to-SQL评估指标目录与定义

状态 / Status: Proposed  
日期 / Date: 2026-09-05  
范围 / Scope: 论文相关工作、工程评估实现、Demo四层映射  
关联 / Related: PR #39 (四层分数包ADR), PR #38 (UI L-strip ADR)

## 1. 上下文 / Context

SqurveBridge需要一个可引用的、权威的评估指标定义目录,以支持:

1. **论文相关工作(Related Work)**: 明确说明Spider EX与BIRD EX的差异、EM与ESM的区别、以及哪些指标可用于自进化反馈vs离线审计。
2. **工程实现(Engineering)**: 为`reproduce/eval/`注册表、Demo API序列化和score bundle contract提供规范化的指标语义。
3. **可引用性(Citability)**: 每个指标都关联到其原始论文、公式定义和已验证的实现(截至2026-08-28的调研)。

当前问题:

- Spider vs BIRD的EX定义混用会导致错误的基准对比。
- 文献中的"Exact Match"既指Spider的clause-set匹配(EM/ESM),也指ROSE附录的字符串相等,两者不同。
- NL2SQL360定义了Query Variance Testing(QVT)但**未定义**CF1指标——它使用子集过滤后报告EX。
- R-VES的官方实现(Mini-Dev `evaluation_ves.py`)与E-SQL论文的简化公式存在差异。
- ROSE/SpotIt等形式化验证工具的κ一致性指标(~25.56%)表明EX本身不是绝对真值。

## 2. 决策 / Decision

维护一个分层的指标目录,按评估维度组织,并明确每个指标是否可用作自进化反馈信号。

### 2.1 结构匹配指标 / Structure Matching

#### EM/ESM (Exact Set Match)
- **来源**: Yu et al., *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL Task*, EMNLP 2018, arXiv:1809.08887
- **定义**: Clause-set matching with `DISABLE_VALUE=True`;将SQL解析为组件集合(SELECT, WHERE, GROUP BY, ORDER BY, JOIN, IUEN, keywords),比较组件集合的完全匹配。
- **重要区分**: **不是**字符串相等。ROSE附录提到的string-EM是不同的度量。
- **SqurveBridge key**: `em`

#### CM / Partial F1 (Component Matching)
- **来源**: Spider官方评估脚本的部分匹配模式
- **定义**: 对七个SQL组件分别计算F1分数:`SELECT`, `WHERE`, `GROUP BY`, `ORDER BY`, `JOIN`, `IUEN` (INTERSECT/UNION/EXCEPT/NESTED), `keywords`
- **SqurveBridge keys**: `cf1_select`, `cf1_where`, `cf1_group`, `cf1_order`, `cf1_join`, `cf1_iuen`, `cf1_keywords`
- **注意**: NL2SQL360(QVT论文)并未定义名为"CF1"的指标——它在子集上报告EX。

#### PCM-F1 (Partial Component Matching)
- **来源**: Hazoom et al., *Text-to-SQL in the Wild: A Naturally-Occurring Dataset Based on Stack Exchange Data*, arXiv:2106.05006
- **定义**: 组件级别的部分匹配F1,包含嵌套查询的递归处理。

#### ETM (Equivalence-Transformed Match)
- **来源**: Ascoli et al., *SQLFixAgent*, arXiv:2407.07313
- **定义**: 基于AST的匹配,包含约26种等价性重写规则(如`A AND B` ≡ `B AND A`)。
- **输出**: 0/1二值结果。

#### TSED (Tree Structure Edit Distance)
- **来源**: Song et al., *When to Stop? Towards Efficient Code Generation in LLMs with Excess Token Prevention*, ICSE-SEIP 2024, arXiv:2312.14725
- **定义**: 使用APTED算法计算树编辑距离。
- **公式**: ICSE-SEIP论文式(8): δ/MaxNodes (距离)。ACL 2024 short paper使用: 1−δ/MaxNodes (相似度)。
- **引用时注意**: 明确使用哪个版本的公式。

#### FuncEvalGMN (Graph Matching Networks)
- **来源**: Zhan et al., *Leveraging Semantic Relations in Code and Data to Enhance Accuracy of Deep Learning Models for Text-to-SQL*, COLING 2025, arXiv:2407.14530
- **定义**: RelNode(关系代数节点) + GMN连续相似度/AUC;无公开的二值化阈值。
- **注意**: RelPM是附录基线,不是独立论文。

### 2.2 执行语义指标 / Execution Denotation

#### Spider EX vs BIRD EX
- **关键区别**: **不可混用**
- **Spider EX**: `test_suite.result_eq`,允许列顺序置换;无ORDER BY时使用bag语义(允许行重复)。
- **BIRD EX**: `set(predicted_res)==set(ground_truth_res)`,忽略行顺序,折叠重复行,**列顺序敏感**。
- **SqurveBridge key**: `ex` (需在UI中标注数据集来源以避免混淆)

#### Soft-F1
- **来源**: BIRD Mini-Dev官方评估
- **定义**: Cell-bag F1;将结果表视为单元格的多重集,计算预测与标准答案之间的F1分数;忽略列顺序。
- **SqurveBridge key**: `sf1`

#### Test Suite Accuracy
- **来源**: Zhong, Yu, Klein, *Semantic Evaluation for Text-to-SQL with Distilled Test Suites*, EMNLP 2020, arXiv:2010.02840
- **定义**: 使用蒸馏数据库和测试套件,为语义准确性提供严格上界。
- **用途**: 离线审计,不用于在线反馈。

#### EA_soft / EA_partial
- **来源**: *StatBot.Swiss: Bilingual Open Data Exploration in Natural Language*, ACL Findings 2024, arXiv:2406.03170
- **定义**: 基于子集关系的执行准确性变体;当预测结果是标准答案的子集或超集时给予部分分数。

### 2.3 效率指标 / Efficiency

#### VES (Valid Efficiency Score)
- **来源**: BIRD主评估,公式(4)
- **定义**: VES = √(E_gold/E_pred),其中E是执行时间;无效SQL贡献0分。
- **SqurveBridge key**: `ves`

#### R-VES (Reward Valid Efficiency Score)
- **来源**: BIRD Mini-Dev官方实现 `evaluation_ves.py`
- **定义**: 
  1. 计算比率 τ = E_gold / E_pred
  2. 将τ离散化到bins: r ∈ {1.25, 1, 0.75, 0.5, 0.25, 0}
  3. R-VES = mean(100 × √r)
- **重要区别**: E-SQL论文仅复制了bins,**省略了**外层的√和×100。Agentar的连续奖励函数也≠官方R-VES。
- **SqurveBridge key**: `rves`

#### VES* / VCES
- **来源**: *VCES: Validity, Cost, and Efficiency Score*, EuroMLSys 2026, arXiv:2602.21480
- **定义**: 端到端时间/成本指标,包含网络延迟和云计费成本。
- **Demo状态**: 非主要指标。

### 2.4 模块与过程指标 / Module & Process Metrics (NL2SQLBench)

#### Schema Linking Metrics
- **来源**: Hou et al., *NL2SQLBench: A Comprehensive Benchmark for Evaluating Large Language Models on Natural Language to SQL Translation*, PVLDB 2026, arXiv:2604.16493, 公式(1)-(3)
- **定义**:
  - **Schema Precision (P)**: 正确召回的schema元素占所有召回元素的比例
  - **Schema Recall (R)**: 正确召回的schema元素占所有必需元素的比例
  - **Schema F1**: 2PR/(P+R)
- **SqurveBridge当前状态**: 仅有`sl_recall`;precision和F1待实现。

#### Code Generation Quality
- **来源**: NL2SQLBench 公式(4)-(6)
- **定义**:
  - **CR (Correctness Rate)**: 第一次生成正确的比率 ≡ EX
  - **IR (Improvement Rate)**: 经refinement后修复的比率
  - **ER (Error Rate)**: refinement后仍错误的比率
  - **Pass@k**: 至少一个候选正确的比率
- **SqurveBridge当前状态**: 无直接的`cr`/`ir`/`er` API keys;最接近的代理是`error_root*`和过程指标。

#### Code Refinement Quality
- **来源**: NL2SQLBench 公式(7)-(12)
- **定义**:
  - **CI (Correct to Incorrect)**: refinement使正确SQL变错误的比率 ≡ C2I
  - **I2C (Incorrect to Correct)**: refinement修复错误SQL的比率
  - **E2C (Error to Correct)**: 执行错误修复为正确的比率
  - **C2I, C2E**: 反向退化率
- **关键洞察**: C2I > 0 表明revision可能有害。
- **SqurveBridge keys**: 
  - `process.refinement_degradation_rate` ≈ C2I (代理)
  - `process.refinement_fix_rate` ≈ I2C (代理)
- **Phase 2**: 通过eval schema扩展实现完整的CR/IR/ER/C2I。

### 2.5 意图与形式化验证(离线审计) / Intent & Formal Verification (Offline Audit)

#### FLEX (Filtered Lexical Execution)
- **来源**: Kim et al., *FLEX: Expert-level False Positive Reduction in SQL Semantic Evaluation via Judgment Augmentation*, NAACL 2025
- **定义**: LLM裁判判断预测与标准答案是否语义等价(TEQ)或不等价(TNEQ)。
- **用途**: 减少EX假阳性;离线审计工具,不用于实时反馈。

#### ROSE (Robust Semantic Evaluator)
- **来源**: Pei et al., *When Semantic Equivalence Meets Large Language Models: A New Era for SQL Evaluation*, ACL 2026
- **定义**: Prover(无需gold SQL) + Refuter双系统;在ROSE-VEC上计算κ元指标。
- **关键发现**: EX的κ一致性仅约25.56% vs 专家标注,表明EX本身不是绝对真值。
- **注意**: ROSE附录的string-EM ≠ Spider官方EM。
- **用途**: 离线审计;成本过高,不放在Demo热路径。

#### SpotIt
- **来源**: *SpotIt: Identifying Code Weaknesses based on Natural Language Specification*, ICLR 2026, arXiv:2510.26840
- **定义**: 在EX-pass子集上使用有界SMT验证;不是全集准确性度量。
- **用途**: 离线审计;成本过高,不放在Demo热路径。

#### LLM-SQL-Solver
- **来源**: Zhao et al., *LLM-SQL-Solver: Can LLMs Determine SQL Equivalence?*, arXiv:2312.10321
- **定义**: 使用LLM判断SQL等价性的框架。
- **用途**: 研究工具,非生产指标。

### 2.6 鲁棒性指标 / Robustness

#### QVT (Query Variance Testing)
- **来源**: Li et al., *NL2SQL360: Comprehensive Evaluation of Natural Language-to-SQL Systems*, PVLDB 2024, arXiv:2406.01265
- **定义**: 对同一查询的多个自然语言变体进行测试;准入规则:仅统计模型至少正确回答一个NL变体的SQL组。
- **重要区分**: QVT是鲁棒性测试方法,**不是**名为CF1的指标。论文在子集上报告EX。
- **语义**: 条件化的分数(conditionalizes scores)。

## 3. 自进化反馈分层 / Metrics as Feedback for Self-Evolution

将指标分为五个层次,标识哪些适合作为训练/搜索时的反馈信号:

| 层次 | 信号类型 | 是否需要gold | 用途 |
|------|---------|-------------|-----|
| **(A) 仅执行在线** | 执行成功率、超时、语法错误 | 否 | 实时在线反馈 |
| **(B) 执行自洽性** | Self-Consistency (SC), UCT采样 | 否 | 测试时搜索(Alpha-SQL) |
| **(C) 学习验证器** | FuncEvalGMN, 学习的Reward Model | 需要训练数据 | RL训练时密集奖励 |
| **(D) Gold EX/结构** | EX, EM, VES, CF1组件 | 是 | 标准评估与验证门控 |
| **(E) LLM/SMT审计** | FLEX, ROSE, SpotIt, C2I统计 | 是(或无gold) | 离线审计与误差分析 |

### 具体应用场景

#### RL训练
- Arctic风格: gold EX三档奖励(1.0正确/0.1可执行/0惩罚)
- 密集奖励: 执行结果Soft-F1或学习的GMNScore

#### 测试时搜索
- Self-Consistency + UCT (Alpha-SQL方法)
- 当前**不使用**metric bundles作为搜索目标

#### 离线审计(不作为内循环奖励)
- EM/VES: 结构与效率分析
- SpotIt/FLEX: 减少假阳性/假阴性
- C2I统计: 识别refinement是否有害

#### Graph-Reward-SQL
- GMNScore是已验证的RelNode RL奖励
- 但仍需要gold SQL进行训练

## 4. SqurveBridge实现映射 / Implementation Mapping

### 4.1 Demo L-strip (PR #38 / #39)

**当前使用的指标子集**:

- **L1 正确性**: `ex`, `sf1`, `em`
- **L2 效率**: `rves`, `ves` (control)
- **L3 结构**: `cf1_select`, `cf1_where`, `cf1_group`, `cf1_order`, `cf1_join`, `cf1_iuen`, `cf1_keywords`
- **L4 归因**: `error_root*` (分布)

### 4.2 Phase 2: NL2SQLBench指标

通过eval schema扩展添加:

- `cr` / `ir` / `er` (Correctness/Improvement/Error Rates)
- `process.c2i` / `i2c` / `e2c` / `c2e` (Refinement转换率)
- `schema_precision` / `schema_f1`
- `pass@k` / `oracle@k`

**UI处理**: Phase 2实现前显示为虚线占位符"—"或"N/A";**绝不**合成代理值。

### 4.3 论文相关工作

可引用本ADR + 对应的主要论文作为指标定义来源。交叉引用PR #39获取SqurveBridge的具体key映射。

## 5. 后果 / Consequences

### 正面

1. **规范化**: 统一的指标定义消除Spider/BIRD EX、EM/string-EM的混淆。
2. **可引用性**: 每个指标都链接到原始论文和已验证的公式(截至2026-08-28)。
3. **工程清晰性**: 注册表、Demo API和score bundle共享相同的语义。
4. **自进化指导**: 明确哪些指标适合RL奖励、测试时搜索或仅用于离线审计。

### 负面

1. **复杂性**: 20+指标及其变体需要仔细的文档维护。
2. **版本差异**: TSED公式、R-VES实现在不同论文中存在差异,需显式标注使用的版本。
3. **实现待办**: NL2SQLBench的CR/IR/ER/C2I需要eval schema扩展和额外的采集逻辑。

### 风险

1. **指标蔓延**: 避免为每个新论文添加新指标;优先整合到现有层次。
2. **成本**: ROSE/SpotIt放在Demo热路径会导致高昂的LLM/SMT成本;保持为离线审计工具。
3. **误解**: 必须在UI和文档中标注BIRD vs Spider EX的差异。

## 6. 备选方案 / Alternatives Considered

### 6.1 仅使用String-EM
- **拒绝理由**: 字符串相等有极高的假阴性率;无法识别等价SQL(如列顺序、WHERE子句顺序)。

### 6.2 仅使用单一EX作为真值
- **拒绝理由**: 
  - Gold SQL存在错误(FLEX/ROSE/SpotIt证据)
  - ROSE κ~25.56%表明EX与专家判断的一致性不足
  - 需要多层指标进行交叉验证

### 6.3 将ROSE/SpotIt放在Demo热路径
- **拒绝理由**: 
  - 每次查询需要多次LLM调用或SMT求解,成本过高
  - 延迟不可接受(用户期望<10s响应)
  - 适合批量离线审计,不适合实时Demo

### 6.4 合成NL2SQLBench指标的代理
- **拒绝理由**: 
  - `refinement_degradation_rate`仅是C2I的近似,不是定义相同的指标
  - 显示合成值会误导用户认为已实现完整的NL2SQLBench支持
  - 宁可显示"—"或"N/A",等待Phase 2真实实现

## 7. 未决问题 / Open Questions

### 7.1 实现时间线
**问题**: 何时在bundle中实现NL2SQLBench的CR/IR/ER/C2I?  
**依赖**: eval schema扩展、stage checkpoint增强、pipeline delta重构。  
**建议**: 作为独立issue跟踪;UI先使用占位符。

### 7.2 Soft-F1门控R-VES显示
**问题**: 是否应当仅在Soft-F1 > 阈值时显示R-VES?  
**理由**: 当答案部分正确时,效率分数的语义存在疑问。  
**当前**: 无门控;BIRD官方也无此要求。  
**待定**: 收集用户反馈后决定。

### 7.3 数据集切换时的EX标注
**问题**: Demo UI如何明确显示当前使用的是Spider EX还是BIRD EX?  
**影响**: 用户可能误将两者直接对比,导致错误结论。  
**建议**: 在score card添加dataset标签;在对比视图中警告混合数据集的比较无效。

### 7.4 L6跨方法对比指标
**问题**: 何时实现Oracle Gap、Pairwise Disagreement、Difficulty Stratification?  
**依赖**: eval store长表schema + 跨run查询API。  
**当前**: 已在多维评估架构ADR中定义,但未在Demo/bundle中实现。

### 7.5 TSED公式版本选择
**问题**: 引用ICSE-SEIP式(8) δ/MaxNodes,还是ACL 2024 short的1−δ/MaxNodes?  
**影响**: 数值范围不同(距离vs相似度);报告时需统一。  
**建议**: 明确选择一个版本并在bundle schema中文档化。

## 8. 参考文献 / References

### 主要论文引用

1. Yu et al., *Spider*, EMNLP 2018, arXiv:1809.08887
2. Hazoom et al., *Text-to-SQL in the Wild*, arXiv:2106.05006
3. Ascoli et al., *SQLFixAgent*, arXiv:2407.07313
4. Song et al., *Excess Token Prevention*, ICSE-SEIP 2024, arXiv:2312.14725
5. Zhan et al., *FuncEval*, COLING 2025, arXiv:2407.14530
6. Zhong, Yu, Klein, *Semantic Evaluation*, EMNLP 2020, arXiv:2010.02840
7. *StatBot.Swiss*, ACL Findings 2024, arXiv:2406.03170
8. BIRD Benchmark官方文档与Mini-Dev评估脚本
9. *VCES*, EuroMLSys 2026, arXiv:2602.21480
10. Hou et al., *NL2SQLBench*, PVLDB 2026, arXiv:2604.16493
11. Kim et al., *FLEX*, NAACL 2025
12. Pei et al., *ROSE*, ACL 2026
13. *SpotIt*, ICLR 2026, arXiv:2510.26840
14. Zhao et al., *LLM-SQL-Solver*, arXiv:2312.10321
15. Li et al., *NL2SQL360*, PVLDB 2024, arXiv:2406.01265

### SqurveBridge内部文档

- `docs/design/2026-08-12-multidim-eval-architecture.md`: L1-L6层次定义
- PR #39: 四层score-bundle ADR,定义`ex`/`sf1`/`rves`/`ves`/`em`/`cf1_*`/`error_root`的映射
- PR #38: Demo UI L-strip ADR,定义四层显示语义
- `reproduce/eval/registry/builtin/`: 指标注册表实现
- `demo/api_server.py`: API序列化实现
- `reproduce/eval/bundle/schema.py`: Score bundle contract

---

**文档维护**: 当添加新指标或论文更新时,同步更新本ADR。  
**质量门控**: `uv run python tools/release_check.py --skip-history`必须通过。
