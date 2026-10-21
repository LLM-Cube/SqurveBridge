"""L4: deterministic error attribution for failed samples."""

from __future__ import annotations

from reproduce.eval.registry.registry import MetricRegistry
from reproduce.eval.registry.spec import MetricSpec


def register(registry: MetricRegistry) -> None:
    registry.register_all([
        MetricSpec(
            id="error_root_distribution", layer="L4", source="derived:v2.error_root_distribution",
            aggregation="derived", unit="label", higher_is_better=False, sliceable=False,
            protocol_id="project.failure-bottleneck-distribution", protocol_version="2",
            candidate_scope="selected",
            description="Root-cause distribution over failed (ex=0) samples, with sample ids.",
        ),
        MetricSpec(
            id="stage_attribution", layer="L4", source="derived:v2.stage_attribution",
            aggregation="derived", unit="label", higher_is_better=False,
            sliceable=False,
            protocol_id="project.attribution-outcome-distribution", protocol_version="2",
            candidate_scope="selected",
            description="Workflow bottleneck stage distribution from trace attribution.",
        ),
    ])
