"""L1: final-SQL quality metrics."""

from __future__ import annotations

from reproduce.eval.registry.registry import MetricRegistry
from reproduce.eval.registry.spec import MetricSpec


def register(registry: MetricRegistry) -> None:
    registry.register_all([
        MetricSpec(
            id="ex_at_1", layer="L1", source="derived:v2.ex_at_1", aggregation="derived",
            interval="wilson", protocol_id="project.execution-result-equality",
            protocol_version="2.0.0", candidate_scope="first",
            description="Unified v2 execution accuracy for the first observed candidate.",
        ),
        MetricSpec(
            id="selected_ex", layer="L1", source="derived:v2.selected_ex", aggregation="derived",
            interval="wilson", protocol_id="project.execution-result-equality",
            protocol_version="2.0.0", candidate_scope="selected",
            description="Unified v2 execution accuracy for the recorded selected candidate.",
        ),
        MetricSpec(
            id="observed_oracle_at_k", layer="L1", source="derived:v2.observed_oracle_at_k",
            aggregation="derived", interval="wilson",
            protocol_id="project.execution-result-equality", protocol_version="2.0.0",
            candidate_scope="observed_pool",
            description="Observed maximum correctness over the named candidate pool.",
        ),
        MetricSpec(
            id="structural_match", layer="L1", source="derived:v2.structural_match",
            aggregation="derived", interval="wilson",
            protocol_id="project.sqlglot-structural-match", protocol_version="2.0.0",
            candidate_scope="selected",
            description="Project structural match; not benchmark-native exact match.",
        ),
        MetricSpec(
            id="self_consistency", layer="L1", source="derived:v2.self_consistency",
            aggregation="derived", protocol_id="project.candidate-self-consistency",
            protocol_version="2.0.0", candidate_scope="observed_pool",
            description="Candidate-pair consistency with failed pairs retained in the denominator.",
        ),
        MetricSpec(
            id="native.spider_test_suite", layer="L1", source="derived:v2.native.spider_test_suite",
            aggregation="derived", interval="wilson", protocol_id="native.spider_test_suite",
            protocol_version="e97acc546ecbee8fa27fa8dbf025ef61493a876c",
            candidate_scope="selected", availability="default_off_requires_verified_suite_resources",
            description="Pinned upstream Spider Test Suite Accuracy adapter (default-off).",
        ),
        MetricSpec(
            id="ex", layer="L1", source="ex", aggregation="rate",
            interval="wilson",
            description="Execution accuracy against gold result sets.",
        ),
        MetricSpec(
            id="em", layer="L1", source="em", aggregation="rate",
            interval="wilson",
            description="Exact match over the seven normalized SQL component sets.",
        ),
        MetricSpec(
            id="sf1", layer="L1", source="sf1", aggregation="mean",
            interval="bootstrap",
            description="Soft result-set F1 with partial credit for overlap.",
        ),
        MetricSpec(
            id="sc", layer="L1", source="sc", aggregation="mean",
            description="Self-consistency across generate_num candidates (needs generate_num >= 2).",
        ),
        MetricSpec(
            id="ves", layer="L1", source="ves", aggregation="mean",
            interval="bootstrap",
            description="Valid-efficiency score; efficiency counted only when the result is correct.",
            protocol_id="project.legacy-ves", protocol_version="1", candidate_scope="selected",
        ),
        MetricSpec(
            id="rves", layer="L1", source="rves", aggregation="mean",
            interval="bootstrap",
            description="Reward-weighted valid-efficiency score.",
            protocol_id="project.legacy-rves", protocol_version="1", candidate_scope="selected",
        ),
    ])
