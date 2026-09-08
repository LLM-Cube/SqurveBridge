"""Deterministic comparison of two score bundles.

The delta produced here is the contract consumed by the evolution fitness
(cost/latency/regression terms) and by delta reports.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def compare_scores(previous: dict, current: dict) -> dict:
    from reproduce.eval.bundle.schema import bundle_version

    previous_version = bundle_version(previous)
    current_version = bundle_version(current)
    if previous_version != current_version:
        return {
            "runs": {"previous": previous.get("run_id"), "current": current.get("run_id")},
            "status": "incompatible_bundle_schema",
            "schema_versions": {"previous": previous_version, "current": current_version},
            "metrics": {}, "regressions": {}, "improvements": {},
        }
    if previous_version == 2:
        return _compare_v2(previous, current)
    return {
        "runs": {
            "previous": previous.get("run_id"),
            "current": current.get("run_id"),
        },
        "metrics": {
            "ex": _metric_delta(previous, current, ("aggregate", "ex", "avg")),
            "em": _metric_delta(previous, current, ("aggregate", "em", "avg")),
            "sf1": _metric_delta(previous, current, ("aggregate", "sf1", "avg")),
            "ves": _metric_delta(previous, current, ("aggregate", "ves", "avg")),
            "token_total": _metric_delta(previous, current, ("aggregate", "token", "total_tokens")),
            "latency_avg": _sample_mean_delta(previous, current, "act_elapsed_s"),
            "optimizer_fix_success_rate": _metric_delta(
                previous, current, ("aggregate", "pipeline", "optimizer", "fix_success_rate")
            ),
            "scaler_gain": _metric_delta(previous, current, ("aggregate", "pipeline", "scaler", "scaler_gain")),
            "selection_accuracy": _metric_delta(
                previous, current, ("aggregate", "pipeline", "selector", "selection_accuracy")
            ),
        },
        "regressions": {
            "ex": _ex_regressions(previous, current),
        },
        "improvements": {
            "ex": _ex_improvements(previous, current),
        },
    }


def _compare_v2(previous: dict, current: dict) -> dict:
    """Compare v2 bundles only after strict cohort compatibility succeeds."""
    from reproduce.eval.protocol import EvaluationBundleV2, compare_bundles

    before = EvaluationBundleV2.model_validate(previous)
    after = EvaluationBundleV2.model_validate(current)
    cohort = compare_bundles(before.cohort(), after.cohort())
    if not cohort.compatible:
        return {"runs": {"previous": before.run_id, "current": after.run_id},
                "status": "incompatible_cohort", "cohort": cohort.model_dump(mode="json"),
                "metrics": {}, "regressions": {}, "improvements": {}}
    metric_ids = sorted(set(before.aggregate) | set(after.aggregate))
    metrics = {}
    for metric_id in metric_ids:
        old = before.aggregate.get(metric_id)
        new = after.aggregate.get(metric_id)
        old_value = old.value if old and old.status.value == "available" else None
        new_value = new.value if new and new.status.value == "available" else None
        metrics[metric_id] = {"previous": old_value, "current": new_value,
                              "delta": new_value - old_value
                              if old_value is not None and new_value is not None else None,
                              "previous_status": old.status.value if old else "unavailable",
                              "current_status": new.status.value if new else "unavailable"}
    before_ex = _v2_sample_metric(before, "ex_at_1")
    after_ex = _v2_sample_metric(after, "ex_at_1")
    return {
        "runs": {"previous": before.run_id, "current": after.run_id}, "status": "compatible",
        "cohort": cohort.model_dump(mode="json"), "metrics": metrics,
        "regressions": {"ex_at_1": sorted(key for key, value in before_ex.items()
                                            if value == 1 and after_ex.get(key) == 0)},
        "improvements": {"ex_at_1": sorted(key for key, value in before_ex.items()
                                             if value == 0 and after_ex.get(key) == 1)},
    }


def _v2_sample_metric(bundle: Any, metric_id: str) -> Dict[str, Any]:
    return {sample.instance_id: sample.metrics[metric_id].value for sample in bundle.samples
            if metric_id in sample.metrics and sample.metrics[metric_id].status.value == "available"}


def _metric_delta(previous: dict, current: dict, path: tuple[str, ...]) -> dict:
    before = _get(previous, path)
    after = _get(current, path)
    return {
        "previous": before,
        "current": after,
        "delta": after - before if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None,
    }


def _sample_mean_delta(previous: dict, current: dict, field: str) -> dict:
    before = _sample_mean(previous, field)
    after = _sample_mean(current, field)
    return {
        "previous": before,
        "current": after,
        "delta": after - before if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None,
    }


def _sample_mean(scores: dict, field: str) -> Optional[float]:
    values = [
        row.get(field) for row in scores.get("per_sample") or []
        if isinstance(row.get(field), (int, float))
    ]
    return sum(values) / len(values) if values else None


def _get(data: dict, path: tuple[str, ...]) -> Optional[Any]:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _sample_ex_by_id(scores: dict) -> Dict[str, Any]:
    return {
        str(sample.get("instance_id")): sample.get("ex")
        for sample in scores.get("per_sample") or []
        if sample.get("instance_id") is not None
    }


def _ex_regressions(previous: dict, current: dict) -> list[str]:
    before = _sample_ex_by_id(previous)
    after = _sample_ex_by_id(current)
    return sorted(sample_id for sample_id, old in before.items() if old == 1 and after.get(sample_id) == 0)


def _ex_improvements(previous: dict, current: dict) -> list[str]:
    before = _sample_ex_by_id(previous)
    after = _sample_ex_by_id(current)
    return sorted(sample_id for sample_id, old in before.items() if old == 0 and after.get(sample_id) == 1)
