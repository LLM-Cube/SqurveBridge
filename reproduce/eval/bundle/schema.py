"""Score-bundle contract: the shape every scores.json must satisfy.

Version 1 is the historical, implicit contract produced by
``reproduce.metrics.assembly.build_scores`` (no ``schema_version`` key).
Version 2 is the typed, registry-driven protocol bundle.  Version dispatch is
explicit so unversioned historical files retain their original interpretation.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION_KEY = "schema_version"
CURRENT_VERSION = 2

V1_REQUIRED_TOP_KEYS = frozenset({
    "run_id",
    "method",
    "dataset",
    "split",
    "generate_num",
    "config_path",
    "scope",
    "statistical_validity",
    "timestamp",
    "sample_count",
    "aggregate",
    "by_hardness",
    "by_component_hardness",
    "by_db_type",
    "by_sql_feature",
    "by_scenario",
    "qvt",
    "per_sample",
})

V1_OPTIONAL_TOP_KEYS = frozenset({
    "convergence",
    "workflow_trace",
    "config_snapshot",
    SCHEMA_VERSION_KEY,
})

V1_AGGREGATE_KEYS = frozenset({
    "ex", "em", "sf1", "sc", "ves", "rves",
    "cf1", "fd", "error_root_distribution", "pipeline", "token",
})

# Published evidence bundles are sanitized to aggregate-only form; their
# reduced contract is what publication-safe fixtures can rely on.
PUBLISHED_AGGREGATE_KEYS = frozenset({"ex", "em", "sf1", "ves", "rves"})


def bundle_version(scores: dict[str, Any]) -> int:
    value = scores.get(SCHEMA_VERSION_KEY)
    return int(value) if isinstance(value, int) else 1


def validate_bundle(scores: dict[str, Any]) -> list[str]:
    """Return contract violations (empty list = valid)."""
    if bundle_version(scores) == 2:
        try:
            from reproduce.eval.protocol import EvaluationBundleV2
            EvaluationBundleV2.model_validate(scores)
        except Exception as exc:
            return [f"invalid v2 bundle: {exc}"]
        return []
    if bundle_version(scores) != 1:
        return [f"unsupported bundle schema version: {bundle_version(scores)}"]
    problems = []
    if not isinstance(scores, dict):
        return ["bundle must be a dict"]
    missing = V1_REQUIRED_TOP_KEYS - scores.keys()
    if missing:
        problems.append(f"missing top-level keys: {sorted(missing)}")
    unknown = scores.keys() - V1_REQUIRED_TOP_KEYS - V1_OPTIONAL_TOP_KEYS
    if unknown:
        problems.append(f"unknown top-level keys: {sorted(unknown)}")
    aggregate = scores.get("aggregate")
    if isinstance(aggregate, dict):
        missing_aggregate = V1_AGGREGATE_KEYS - aggregate.keys()
        if missing_aggregate:
            problems.append(f"missing aggregate keys: {sorted(missing_aggregate)}")
    else:
        problems.append("aggregate must be a dict")
    return problems


def validate_published_bundle(scores: dict[str, Any]) -> list[str]:
    """Contract for sanitized published bundles (aggregate-only shape)."""
    problems = []
    aggregate = scores.get("aggregate")
    if not isinstance(aggregate, dict):
        return ["aggregate must be a dict"]
    missing = PUBLISHED_AGGREGATE_KEYS - aggregate.keys()
    if missing:
        problems.append(f"missing published aggregate keys: {sorted(missing)}")
    return problems
