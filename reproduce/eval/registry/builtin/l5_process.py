"""L5: process-level metrics over canonical pipeline roles.

Method-specific task ids map to canonical roles through the Squrve
``task_type`` taxonomy (Reduce/Parse/Generate/Optimize/Select/Scale/
Decompose), which keeps process metrics comparable across methods.
Computation lands in ``reproduce/eval/sample/process.py``; these specs declare
the contract so downstream views can already reserve the dimensions.
"""

from __future__ import annotations

from reproduce.eval.registry.registry import MetricRegistry
from reproduce.eval.registry.spec import MetricSpec


CANONICAL_ROLES = (
    "linking",        # ReduceTask + ParseTask
    "generation",     # GenerateTask (+ ScaleTask candidates)
    "refinement",     # OptimizeTask
    "selection",      # SelectTask
    "decomposition",  # DecomposeTask
)


_SPECS = [
    # linking
    ("linking_recall", "Schema recall after reduce/parse stages.", True),
    ("linking_precision", "Schema precision after reduce/parse stages.", True),
    ("linking_fatal_miss_rate", "Fraction of samples losing a gold schema before generation.", False),
    # generation
    ("generation_pass1", "First-candidate execution accuracy.", True),
    ("generation_oracle_k", "Any-candidate (oracle) execution accuracy.", True),
    ("generation_exec_validity", "Fraction of candidates that execute without errors.", True),
    ("generation_candidate_diversity", "Distinct candidates over total candidates.", True),
    # refinement
    ("refinement_fix_rate", "Wrong-to-right rate across the optimize stage.", True),
    ("refinement_degradation_rate", "Right-to-wrong rate across the optimize stage.", False),
    ("refinement_net_gain", "Net EX gain contributed by the optimize stage.", True),
    ("refinement_debug_turns", "Average optimizer debug turns per sample.", False),
    # selection
    ("selection_accuracy", "EX of the selected candidate.", True),
    ("selection_regret", "Oracle EX minus selected EX.", False),
    ("selection_missed_correct_rate", "Rate of discarding a correct candidate.", False),
    # decomposition
    ("decomposition_trigger_rate", "Fraction of samples where decomposition triggered.", True),
    ("decomposition_trigger_accuracy", "EX of samples where decomposition triggered.", True),
    # funnels
    ("funnel_stage_survival", "Per-canonical-role survival rates (still-solvable fractions).", True),
    ("funnel_gate_survival", "Run gate funnel: parseable, timeout, executes, correct, efficiency.", True),
]


def register(registry: MetricRegistry) -> None:
    for metric_id, description, higher_is_better in _SPECS:
        registry.register(MetricSpec(
            id=metric_id, layer="L5", source=f"derived:v2.{metric_id}",
            aggregation="derived", higher_is_better=higher_is_better,
            publication="aggregate_only", sliceable=False,
            protocol_id="project.component-process-metric", protocol_version="2",
            candidate_scope=("stage_pair" if metric_id.startswith(("refinement_", "selection_")) else "none"),
            description=description,
        ))
