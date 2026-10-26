# Evaluation Metric & Score-Bundle Four-Layer Architecture

## Status

Proposed

## Date

2026-09-05

## Context / 背景

SqurveBridge Demo's interactive evaluation results and comparison UI presents
Text-to-SQL evaluation outcomes through **run detail score cards** (L-strip UI)
and **comparison matrices**. This ADR defines the **evaluation schema / 评测口径**
for the four-layer metric taxonomy — the mapping between internal score-bundle
keys and user-facing display semantics — not the UI layout itself (which is
addressed by a companion ADR).

The existing multi-dimensional evaluation architecture
(`docs/design/2026-08-12-multidim-eval-architecture.md`) established the L1–L6
layer model, metric registry, and score-bundle contract. However, the **Display
→ API → Bundle field mapping** remained implicit, with the following friction
points:

1. **L2 visibility gap**: The Demo UI and published comparisons prioritize
   **R-VES** (reward-weighted valid-efficiency score) as the primary efficiency
   metric, yet the earlier documentation and some code paths conflated L2 with
   "token/cost metrics," leaving the VES family's layer assignment ambiguous.

2. **Missing NL2SQLBench dimensions**: The established NL2SQLBench metrics
   (`cr`, `ir`, `er`, `c2i`, `c2e`) are not yet exported in score bundles, so
   the UI must render dashed placeholders instead of inventing numbers or
   silently omitting critical red-light indicators (e.g., C2I degradation).

3. **Process layer overlap**: L4 (error attribution) and the emerging L5
   (process-level metrics) both capture failure modes and stage-level signals,
   but their boundaries in the Demo API serialization
   (`demo/api_server.py:_serialize_comparison_run`) were not consistently
   documented.

4. **Evolution feedback loop**: Self-evolution and bounded-search workflows
   (`docs/design/meta-evo-loop.md`, `reproduce/evolve/mcts/`) need explicit
   guidance on which metrics to optimize during autonomous refinement (gold-free
   signals for search) versus offline/labeled evaluation (EX/EM/sf1/rves for
   validation and promotion).

This ADR codifies the **authoritative four-layer display taxonomy** that the
Demo API, UI, and score-bundle exporters must implement, and clarifies the
boundary between shipped metrics (L1–L4) and deferred dimensions (NL2SQLBench,
future L5/L6 enhancements).

## Decision / 决策

SqurveBridge Demo evaluation results expose a **four-layer metric taxonomy** to
users, derived from the internal L1–L6 architecture with the following
public-facing organization:

### Layer 1: Correctness / 正确性 (Primary Display)

**Display name**: Correctness / 正确性

**API keys exported in `aggregate` / `per_sample`**:
- `ex` (Execution Accuracy / 执行准确率): binary result-set match
- `sf1` (Soft-F1 / 软 F1): partial-credit result-set overlap (0–1 continuous)

**Optional secondary correctness signals** (displayed when present):
- `em` (Exact Match / 精确匹配): normalized SQL component-set equality
- `sc` (Self-Consistency / 自洽性): agreement across `generate_num ≥ 2`
  candidates (single-pass configs report `null`)

**Implementation notes**:
- The UI renders `ex` and `sf1` as the **two primary correctness columns** in
  comparison matrices and run-detail cards.
- `em` is displayed as a secondary/diagnostic metric when users drill into
  structure-level analysis; it should not be promoted to the primary pair
  unless explicitly requested.
- Exact-match semantics (EM) remain valuable for SQL-aware analysis, but EX
  (result-set equivalence) is the primary correctness definition aligned with
  standard Text-to-SQL benchmarks.

### Layer 2: Efficiency / 效率 (Primary Display)

**Display name**: Efficiency / 效率

**API keys exported in `aggregate` / `per_sample`**:
- `rves` (R-VES / 奖励效率分): **Primary efficiency metric**; reward-weighted
  valid-efficiency score combining correctness, token usage, and latency under
  a configurable utility function.

**Secondary/control efficiency signals** (displayed in drill-down or verbose
modes):
- `ves` (VES / 有效效率分): valid-efficiency score (unweighted predecessor of
  R-VES); used as a baseline comparison or when reward weights are disabled.

**Explicitly NOT primary L2 display metrics**:
- `token.total_tokens`, `token.avg_per_sample`, `token.total_calls`: captured
  and available in `aggregate.token`, but **not** displayed as standalone
  top-level efficiency indicators.
- `latency.mean_s`, `latency.p50_s`, `latency.p95_s`: captured in
  `aggregate.latency` and per-sample `act_elapsed_s`, but **not** primary
  efficiency display metrics.

**Rationale**: Token counts and latency are **cost inputs** to R-VES and VES,
not independent efficiency dimensions in the four-layer display. Users who need
raw cost breakdowns access them through drill-down views (run-detail "Cost &
Timing" expansion panel) or the verbose report, not the comparison matrix's
primary efficiency column.

**Implementation path** (`demo/api_server.py`):
- `_serialize_comparison_run` already places `token` and `latency` as separate
  top-level objects (not inside `aggregate`) to avoid conflating them with
  primary metrics.
- The UI **must not** render raw token or latency as a peer to EX/SF1/R-VES in
  the primary comparison matrix.

### Layer 3: Structure Diagnostic / 结构诊断 (Muted Display)

**Display name**: Structure / 结构 (or "SQL Structure Analysis")

**API keys exported in `aggregate`** (nested under `cf1` object):
- `cf1.cf1_select`, `cf1.cf1_where`, `cf1.cf1_group`, `cf1.cf1_order`,
  `cf1.cf1_join`, `cf1.cf1_iuen`, `cf1.cf1_keywords`

**Optional structure signals**:
- `sl_recall` (Schema-Linking Recall): captured by linking-aware methods (C3SQL
  reducer/parser); displayed as `null` for methods without explicit linking
  stages.
- `fd` (Feature Delta): predicted-minus-gold SQL feature deltas (mean/std per
  16-dim feature vector); exported as `aggregate.fd` when computed.

**Display semantics**: L3 metrics are **diagnostic** rather than primary
evaluation outcomes. The UI:
- Presents CF1 components in a collapsed "Structure Breakdown" panel or tooltip
  (not inline with EX/R-VES).
- Uses L3 signals to explain **why** a method failed (e.g., low `cf1_join` for
  multi-table queries), not to rank methods head-to-head on structure alone.
- May render a radar chart or heatmap for CF1 components in drill-down views.

**Implementation note**: The existing `_serialize_aggregate_metrics` already
nests `cf1` as a separate object; the UI should not flatten CF1 components into
the primary comparison columns.

### Layer 4: Process & Attribution / 过程与归因 (Analytical Display)

**Display name**: Process / Attribution / 过程与归因

**API keys exported** (denormalized for UI convenience):
- `aggregate.error_root_distribution` → serialized as `errors` in
  `_serialize_comparison_run`:
  ```json
  "errors": {
    "schema_linking": {"count": 12, "pct": 0.24},
    "join_condition": {"count": 8, "pct": 0.16},
    ...
  }
  ```
  - Also accessible per-sample via `per_sample[i].error_root` (single label or
    multi-label list).

**Emerging process-level signals** (deferred to future score-bundle versions;
see "Open Questions" below):
- `process.refinement_fix_rate`: fraction of wrong-to-right corrections across
  the optimize stage.
- `process.refinement_degradation_rate`: fraction of right-to-wrong regressions
  across optimize (the **C2I proxy** for current configs).
- `process.refinement_net_gain`: net EX contribution from refinement.
- `process.generation_exec_validity`: fraction of generated candidates that
  execute without errors (a gold-free quality signal).

**Display semantics**:
- `error_root_distribution` is rendered as a **top-N failure-mode chart** (bar
  or pie) in the run-detail card, with drill-down links to per-sample failure
  lists.
- Process metrics (when shipped) appear in a "Process Diagnostics" panel,
  **not** as primary comparison dimensions.
- **C2I red-light rule**: The UI flags a run with a red indicator when
  `process.refinement_degradation_rate > 0` or when a future explicit `c2i`
  field is non-zero, signaling that the refiner is actively introducing errors.

**Implementation path**: The score-bundle schema reserves
`workflow_trace.aggregate` and `stage_metrics` for process signals, but the
Demo API serialization currently omits most L5 fields. Phase 2 (tracked
separately) will add `process` to the API payload and update the UI cards.

### Explicit Non-Goals / 明确的非目标

1. **NL2SQLBench CR/IR/ER/C2I/C2E**: These dimensions are not yet captured in
   score bundles (`reproduce/eval/` does not compute them). The UI **must**
   render dashed placeholders (`—` or "N/A") when these fields are requested,
   and **must never** synthesize or approximate them from other metrics. A
   future issue will add NL2SQLBench evaluation to the bundle-build pipeline;
   until then, any comparison claiming NL2SQLBench alignment shows only L1–L4.

2. **L6 Cross-Method Comparative Metrics**: Oracle gap (`Δ = EX_dynamic −
   EX_static`), pairwise disagreement (`D_ij`), and difficulty stratification
   (`N(q)`) require multi-run matrices stored in the eval store. The Demo UI
   does not yet implement L6 views; they remain future work.

3. **Real-time cost estimation**: The Demo does not compute or display
   dollar-denominated costs. Token counts and latency are recorded, but
   currency conversions depend on provider-specific pricing that changes
   frequently and is not bundled in the score schema.

4. **ROSE/SpotIt audit signals**: ROSE (schema-grounded hallucination) and
   SpotIt (error-cascade detection) are offline audit tools run separately from
   the primary evaluation pipeline. Their findings may be linked from run
   metadata but are not first-class score-bundle fields.

### Self-Evolution Feedback Note / 自演化反馈说明

The bounded-search MCTS loop (`reproduce/evolve/mcts/orchestrator.py`) and
Meta-Evo workflows (`docs/design/meta-evo-loop.md`) require explicit guidance
on which signals to optimize:

- **Autonomous search/refine (inner loop)** should prefer **gold-free signals**:
  - `process.generation_exec_validity` (syntax correctness, no gold required)
  - `process.refinement_degradation_rate` (C2I proxy, detectable from
    before/after snapshots)
  - Self-consistency (`sc`) when `generate_num ≥ 2`
  - Execution errors / timeout rates (run-gate funnel)

- **Labeled evaluation (promotion gates, validation)** uses **gold-dependent
  metrics**:
  - `ex`, `em`, `sf1`, `rves` (require gold result sets or reference SQL)

This separation ensures that the search loop can iterate quickly without
requerying labeled dev sets, while promotion decisions and published claims
remain anchored to ground truth. Detailed inner-loop feedback contracts live in
`docs/design/evolution-harness-design.md` and
`skills/shared-references/evolution-controller-contract.md`; this ADR
establishes only the high-level principle.

## Consequences / 后果

### Positive / 正面影响

1. **Consistent display semantics**: Demo UI, comparison APIs, and published
   reports agree on which metrics represent "correctness," "efficiency," and
   "diagnostics," eliminating user confusion about why token counts aren't
   top-level comparison dimensions.

2. **NL2SQLBench red-light honesty**: By mandating dashed placeholders for
   missing CR/IR/ER/C2I/C2E, we avoid the trap of approximating these
   dimensions with incorrect proxies, preserving trust when full NL2SQLBench
   support ships.

3. **Evolution-friendly contracts**: Autonomous search knows to avoid
   gold-dependent metrics in tight inner loops, while validation remains
   properly grounded.

4. **Stable API versioning**: The score-bundle schema version remains `1` until
   the registry-driven refactor (documented in the multidim-eval-architecture
   ADR) ships. This ADR does not introduce breaking changes; it clarifies the
   **intended semantics** of existing fields.

### Negative / Trade-offs / 权衡

1. **Token/latency demotion**: Users accustomed to seeing raw token counts in
   primary comparison tables must now drill down into cost panels. This is
   intentional (R-VES is the synthesized efficiency metric), but may require UI
   education (tooltips, onboarding hints).

2. **Deferred L5 process metrics**: The "C2I red light" and
   `refinement_fix_rate` are captured in trace logs but not yet aggregated in
   score bundles. Phase 2 work (tracked separately) is required before the UI
   can display these signals. Until then, users analyzing optimizer behavior
   must inspect per-sample `workflow_trace` manually.

3. **No retroactive bundle migration**: Existing evidence bundles
   (`evidence/reported-results/`) and archived Demo runs predate this ADR and
   may expose metrics (e.g., `token.per_call_p95`) that this decision
   deprioritizes. The UI must handle missing keys gracefully (render `null` or
   "—") rather than assuming all fields are always present.

## Alternatives Considered / 备选方案

### Alternative 1: Promote Token/Latency to L2 Primary

**Proposal**: Display `token.total_tokens` and `latency.mean_s` as peer
efficiency metrics alongside R-VES in the comparison matrix.

**Rejected because**:
- R-VES already **synthesizes** token and latency under a correctness-weighted
  utility function, so displaying raw counts duplicates information.
- Users comparing runs with different sample counts or benchmark splits need
  **normalized** efficiency (tokens-per-correct-sample, latency-under-EX-cap),
  not raw totals.
- Expanding the primary matrix to 5–6 columns degrades readability; drill-down
  panels better serve detailed cost breakdowns.

### Alternative 2: Merge L4 Attribution into L3 Structure

**Proposal**: Treat `error_root_distribution` as a "structural diagnostic"
alongside CF1 components, collapsing L3+L4 into one "Structure & Failure
Analysis" layer.

**Rejected because**:
- **Conceptual distinction**: L3 (CF1, feature deltas) analyzes **what the SQL
  looks like** (structure), while L4 (error roots, stage attribution) explains
  **why it failed** (causality). Merging them obscures this boundary.
- **Use-case separation**: L3 informs prompt engineering ("improve JOIN
  generation"); L4 informs architecture changes ("schema-linking bottleneck").
- The existing registry (`reproduce/eval/registry/builtin/l3_structure.py` vs.
  `l4_attribution.py`) already separates them; retroactive merging would
  contradict the multidim-eval-architecture design.

### Alternative 3: Invent NL2SQLBench Proxies

**Proposal**: Approximate `c2i` as `em_before − em_after` from workflow traces,
and `cr`/`ir` from existing EX slices, so the UI never shows dashes.

**Rejected because**:
- NL2SQLBench's C2I definition includes SQL-level semantic degradation (schema
  errors, predicate inversions) that EM (normalized component sets) doesn't
  capture. Our proxy would be **incorrect**, not approximate.
- Publishing proxy-based C2I numbers as if they were NL2SQLBench-aligned would
  violate the anonymity/publication-privacy policy (no unpublished claims
  without validation).
- Honest "N/A" markers preserve trust and create clear demand for Phase 2
  NL2SQLBench integration, rather than letting users believe the dimension is
  already solved.

## Open Questions & Follow-Up Issues / 开放问题与后续工作

1. **Phase 2: NL2SQLBench Integration**
   - Add `cr`, `ir`, `er`, `c2i`, `c2e` computation to `reproduce/eval/` (new
     evaluator functions or adapters).
   - Update `bundle/build.py` to populate `aggregate.nl2sqlbench` or nest
     fields directly in `aggregate`.
   - Version-bump the score-bundle schema to `2` if field layout changes.
   - Estimated scope: new eval module + bundle schema extension + UI update.

2. **L5 Process Metrics Aggregation**
   - Implement `reproduce/eval/sample/process.py` aggregators for
     `refinement_fix_rate`, `refinement_degradation_rate`, `generation_exec_validity`,
     and gate-funnel survival rates.
   - Add `aggregate.process` object to score bundles (tracked in
     multidim-eval-architecture doc as deferred step 4).
   - Update `demo/api_server.py:_serialize_comparison_run` to expose `process`
     alongside `errors`.
   - Estimated scope: sample-level signal merging + aggregation + API
     serialization.

3. **C2I Red-Light UI Implementation**
   - Once `process.refinement_degradation_rate` ships in bundles, the UI
     comparison cards and run-detail headers must render a visual red indicator
     (icon + tooltip: "Optimizer introduced errors on N% of samples").
   - Threshold TBD: `degradation_rate > 0` (any regression) or
     `degradation_rate > 0.05` (tolerate rare optimizer noise)?

4. **Metric Display Naming Localization**
   - The ADR uses bilingual keys (English metric IDs + Chinese display names in
     notes). The Demo UI currently hardcodes English labels; full i18n
     (locale-aware label rendering) is future work.
   - Consider extracting display-name mappings to a JSON catalog
     (`demo/config/metric-labels.json`) that the UI can toggle by locale.

5. **Backwards Compatibility for Archived Bundles**
   - Existing evidence bundles may lack `rves`, `latency`, or nested `cf1`.
     The UI already handles missing keys by rendering `null` or "—".
   - Document the "graceful degradation" contract: if `rves` is missing but
     `ves` is present, the UI may fall back to VES with a tooltip ("R-VES not
     available in this bundle version").

## References / 参考文献

- `docs/design/2026-08-12-multidim-eval-architecture.md`: L1–L6 layer
  definitions, metric registry, score-bundle schema contract.
- `docs/design/evolution-harness-design.md`: Bounded-search loop, rollout
  isolation, experience warm-start, and the separation of autonomous search
  signals vs. labeled validation.
- `docs/design/meta-evo-loop.md`: Meta-Evo workflow, review gates, reward
  computation, and baseline-centered fitness.
- `reproduce/eval/bundle/schema.py`: Score-bundle contract v1, required keys,
  validation logic.
- `reproduce/eval/registry/builtin/l1_quality.py`, `l2_cost.py`,
  `l3_structure.py`, `l4_attribution.py`, `l5_process.py`: Metric registry
  specs for each layer.
- `demo/api_server.py`: Demo API serialization functions
  (`_serialize_comparison_run`, `_serialize_aggregate_metrics`,
  `_serialize_error_distribution`), public comparison payload format.
- NL2SQLBench paper (arXiv 2602.15564 §3, Appendix B/C): CR/IR/ER/C2I/C2E
  definitions, difficulty stratification, oracle gap.

---

**Document History**:
- 2026-09-05: Initial proposal (this ADR).
