# ADR 0001: Reorganize Demo Run-detail Scores into a Four-Layer L-Strip

Status: Proposed  
Date: 2026-09-05  
Scope: `demo-app` History / Run detail / Evidence surfaces; binding to existing score bundle keys. Eval schema additions for CR/IR/ER·C2I are out of scope for this ADR (blocked; separate eval issue).

## Context

Demo Run detail already shows a flat metrics list with five primary metrics. For example, on a c3sql×spider run, users currently see:

- EX: 80%
- EM: 36%
- SF1: 78.1%
- VES: 80.4%
- RVES: 72.9%

History list cards currently headline EX only, without exposing the other dimensions.

Research and product teams want readers to see four evidence layers so that metric-guided optimization (Meta-Evo) can gate on the same story the UI tells users. This creates alignment between the optimization process and the user-facing narrative.

### Agreed Layer Semantics

The four layers represent distinct evidence dimensions:

**L1 - Correctness**: Primary quality signals
- **EX** (Execution Accuracy): Final SQL correctness
- **Soft-F1**: Fine-grained quality metric balancing precision and recall

**L2 - Efficiency**: Resource utilization
- **R-VES** (Reduced Valid Efficiency Score): Primary efficiency metric
- **VES** (Valid Efficiency Score): Reference/contrast metric
- Token counts and latency: Cost reference metrics (never L2 primary)

**L3 - Structure**: Diagnostic structural quality (diagnostic, muted, not primary score)
- **EM** (Exact Match): String-level SQL match
- **Clause CF1**: Component-level F1 scores across SQL clauses
  - `cf1_select`, `cf1_where`, `cf1_group`, `cf1_order`, `cf1_join`, `cf1_iuen`, `cf1_keywords`

**L4 - Process**: Error attribution and process quality
- **Error attribution**: Via `error_root`, `error_roots`, or `error_root_distribution` keys
- **Week-1 process metrics**: 
  - `process.refinement_success_rate`
  - `process.refinement_degradation_rate`
  - `process.refinement_net_gain`
  - `process.generation_exec_validity`
- **Future metrics** (API keys do not exist yet, blocked on eval schema):
  - CR (Candidate Rate)
  - IR (Iteration Rate)
  - ER·C2I (Error Rate · Cost-to-Improve)

## Decision

### 1. Four-Card L-Strip for Run Detail

Refactor the existing Run detail metrics card into a four-card **L-strip** layout. Do not invent a second scoreboard; replace the current flat list with the layered structure.

Each card in the L-strip corresponds to one evidence layer (L1, L2, L3, L4), clearly separating correctness, efficiency, structure, and process metrics.

### 2. Metric Display Names and Bundle Keys

Use the following display names and score bundle keys:

| Display Name | Bundle Key(s) | Layer | Notes |
|--------------|---------------|-------|-------|
| EX | `ex` | L1 | Execution accuracy |
| Soft-F1 | `sf1` | L1 | From `aggregate.sf1.avg` or flat `sf1` |
| R-VES | `rves` | L2 | Primary efficiency metric |
| VES | `ves` | L2 | Efficiency reference |
| EM | `em` | L3 | Exact match |
| CF1 (clauses) | `cf1.cf1_select`, `cf1.cf1_where`, `cf1.cf1_group`, `cf1.cf1_order`, `cf1.cf1_join`, `cf1.cf1_iuen`, `cf1.cf1_keywords` | L3 | Component F1 scores |
| Error Attribution | `error_root`, `error_roots`, `error_root_distribution` | L4 | Error root causes |
| Refinement Success | `process.refinement_success_rate` | L4 | Week-1 process metric |
| Refinement Degradation | `process.refinement_degradation_rate` | L4 | Week-1 process metric |
| Refinement Net Gain | `process.refinement_net_gain` | L4 | Week-1 process metric |
| Generation Exec Validity | `process.generation_exec_validity` | L4 | Week-1 process metric |

**Important**: Never invent metric keys beyond those listed. For future CR/IR/ER·C2I metrics that do not yet exist in the eval schema, use dashed placeholders (缺口) in the UI until the eval schema lands.

### 3. History List Card Updates

History list cards stay **EX-primary**. Do not add the full L-strip to list cards.

Footer chips may show a condensed summary:
- EX · SF1 · R-VES · sample count

This provides a quick overview without overwhelming the list view.

### 4. Evidence Surface Updates

The Evidence detail view keeps its existing content:
- Error attribution section
- SQL feature matrix

Add the following sample table columns when the corresponding data exists in the score bundle:
- **执行差** (Execution difference): Show execution discrepancies
- **报错** (Errors): Error messages or types
- **C2I** (Cost-to-Improve): When process refinement metrics exist

### 5. C2I Red Light Rule

Display a C2I red light indicator only when:
```
process.refinement_degradation_rate > 0
```

Or when a future explicit `c2i` key exists in the bundle.

Never invent values for `cr`, `ir`, `er`, `c2i`, or `c2e` metrics. Use dashed placeholders until the eval schema provides these keys.

### 6. ROSE / SpotIt Placement

ROSE and SpotIt stay off the primary Evidence path. Provide access only via an offline audit link.

## Consequences

### Positive Impacts

1. **Narrative Alignment**: The UI layer story matches research terminology (L1–L4 framing)
2. **Engineering Clarity**: Engineers bind to stable, documented bundle keys
3. **Optimization Consistency**: Meta-Evo gates can cite the same L1–L4 framing that users see
4. **Progressive Disclosure**: Users understand correctness first, then efficiency, then diagnostic details
5. **Future-Proof**: Clear placeholder strategy for CR/IR/ER·C2I metrics when eval schema lands

### Negative Impacts and Tradeoffs

1. **Visual Density Change**: Run detail page layout becomes more complex with four cards instead of one flat list
2. **VES Demotion**: Users accustomed to the flat list may be surprised that VES is now a reference metric rather than a primary headline
3. **Incomplete L4 Taxonomy**: The L4 layer is incomplete until the separate eval issue ships CR/IR/ER·C2I support
4. **Migration Cost**: Existing bookmarks or screenshots showing the old flat list will look different

### Follow-up Work

1. **Implementation PR**: Implement the L-strip layout in `demo-app` (React/Vite components)
2. **Eval Schema Update**: Separate ADR and/or GitHub issue for NL2SQLBench-aligned CR/IR/ER·C2I keys in score bundles
3. **Documentation**: Update user-facing documentation to explain the four layers
4. **Testing**: Verify layout with various bundle shapes (complete L4, partial L4, missing optional metrics)

### Dependencies

**Blocked on**: Eval schema extension for CR/IR/ER·C2I (tracked separately)

**Blocks**: Meta-Evo optimization gates that reference L1–L4 terminology

## Non-Goals

This ADR explicitly does NOT address:

- Studio Methods grid layout or metrics
- Agent sidebar display
- Configure LLM interface
- Publishing detailed evaluation math or formulas
- Changes to Python evaluation code in `reproduce/eval/`
- Changes to score bundle assembly in `reproduce/eval/bundle/`
- Backend API changes in `demo/` Flask server

These are intentionally out of scope. This ADR is documentation-only and describes the target UI direction. Implementation work happens in a follow-up PR.
