"""Public API for the unified evaluation protocol."""

from .analysis import SQLAnalysis, schema_exact_match, structural_analysis, structural_match
from .attribution import AttributionResult, attribute_execution
from .cohort import CohortComparison, compare_bundles
from .comparison import ComparisonOutcome, compare_results
from .components import (ComponentTransition, ComponentTransitionSummary, DecompositionObservation,
                         ScalingObservation, SelectionObservation, aggregate_component_transitions,
                         evaluate_component_transition, evaluate_decomposition, evaluate_scaling,
                         evaluate_selection)
from .execution import BackendCapabilities, ExecutionResult, ExecutionTiming, SQLExecutor
from .models import (Availability, Candidate, CohortDescriptor, ComparisonPolicy,
                     EvaluationBundleV2, MetricResult, SampleEvaluation)
from .metrics import (TimingObservation, TokenObservation, aggregate_token_observations,
                      legacy_efficiency_metric,
                      self_consistency, soft_f1)
from .native import NativeAdapterSpec, NativeEvaluatorRegistry, register_builtin_spider_test_suite
from .offline import evaluate_checkpoint

__all__ = [
    "AttributionResult", "Availability", "BackendCapabilities", "Candidate", "CohortComparison", "CohortDescriptor",
    "ComparisonOutcome", "ComparisonPolicy", "ComponentTransition", "ComponentTransitionSummary",
    "DecompositionObservation",
    "EvaluationBundleV2",
    "ExecutionResult", "ExecutionTiming", "MetricResult", "NativeAdapterSpec",
    "NativeEvaluatorRegistry", "SQLAnalysis", "SQLExecutor", "SampleEvaluation", "ScalingObservation",
    "SelectionObservation", "TimingObservation", "TokenObservation", "aggregate_component_transitions",
    "aggregate_token_observations",
    "attribute_execution",
    "compare_bundles", "compare_results",
    "evaluate_checkpoint", "evaluate_component_transition", "evaluate_decomposition", "evaluate_scaling",
    "evaluate_selection",
    "legacy_efficiency_metric", "register_builtin_spider_test_suite", "schema_exact_match",
    "self_consistency", "soft_f1",
    "structural_analysis", "structural_match",
]
