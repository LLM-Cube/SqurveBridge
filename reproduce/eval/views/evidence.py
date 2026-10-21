"""Publication view derived from the metric registry.

``tools/evidence.py`` owns the hard privacy contract (forbidden keys, secret
patterns, checksums). This module owns the *completeness* direction that the
generic sanitizer cannot know: which metrics are supposed to survive export.
Silent metric loss (cf1/token/hardness missing from published bundles,
``token_usage`` allowed but never written) is the failure mode this closes.

The completeness assertions run inside the regular unittest suite, which the
release gate (`tools/release_check.py`) executes on every run.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

from reproduce.eval.registry import MetricRegistry, default_registry


# aggregate locations that hold registry metrics, by layer convention
_L1_IDS = ("ex", "em", "sf1", "sc", "ves", "rves")


def aggregate_metric_value(scores: Dict[str, Any], metric_id: str) -> Any:
    """Resolve a registry metric id from either historical or typed bundles."""
    aggregate = scores.get("aggregate") or {}
    if scores.get("schema_version") == 2:
        aliases = {"ex": "ex_at_1", "em": "structural_match", "sc": "self_consistency"}
        cell = aggregate.get(aliases.get(metric_id, metric_id))
        return cell.get("value") if isinstance(cell, dict) and cell.get("status") == "available" else None
    if metric_id in _L1_IDS:
        cell = aggregate.get(metric_id)
        return cell.get("avg") if isinstance(cell, dict) else cell
    if metric_id.startswith("cf1_"):
        cell = (aggregate.get("cf1") or {}).get(metric_id)
        return cell.get("avg") if isinstance(cell, dict) else cell
    if metric_id == "sl_recall":
        cell = aggregate.get("sl_recall")
        return cell.get("avg") if isinstance(cell, dict) else None
    if metric_id == "latency_avg":
        return (aggregate.get("latency") or {}).get("avg_s")
    if metric_id == "latency_p50":
        return (aggregate.get("latency") or {}).get("p50_s")
    if metric_id == "latency_p95":
        return (aggregate.get("latency") or {}).get("p95_s")
    if metric_id == "token_total":
        return (aggregate.get("token") or {}).get("total_tokens")
    if metric_id == "token_avg_per_sample":
        return (aggregate.get("token") or {}).get("avg_per_sample")
    if metric_id == "cost_per_correct":
        token_total = (aggregate.get("token") or {}).get("total_tokens")
        ex = aggregate.get("ex") or {}
        pass_count = ex.get("pass_count")
        if isinstance(token_total, (int, float)) and isinstance(pass_count, int) and pass_count > 0:
            return token_total / pass_count
        return None
    if metric_id == "error_root_distribution":
        return aggregate.get("error_root_distribution")
    return None


def publishable_metric_ids(registry: MetricRegistry | None = None) -> List[str]:
    registry = registry or default_registry()
    return [
        spec.id for spec in registry.metrics()
        if spec.publication in {"public", "aggregate_only"} and spec.layer in {"L1", "L2", "L3", "L4"}
    ]


def publication_completeness(scores: Dict[str, Any], registry: MetricRegistry | None = None) -> List[str]:
    """Declared publishable metrics that the bundle fails to provide.

    A metric counts as missing only when the bundle carries the raw material
    for it (fd and derived stage summaries are excluded from the strict set).
    """
    registry = registry or default_registry()
    missing = []
    for metric_id in publishable_metric_ids(registry):
        spec = registry.get(metric_id)
        if spec.aggregation == "derived" and metric_id not in {"error_root_distribution",
                                                               "token_total",
                                                               "token_avg_per_sample",
                                                               "cost_per_correct"}:
            continue
        if aggregate_metric_value(scores, metric_id) is None and not _legitimately_absent(scores, metric_id):
            missing.append(metric_id)
    return missing


def _legitimately_absent(scores: Dict[str, Any], metric_id: str) -> bool:
    """A metric may be absent when its raw signal was never captured."""
    per_sample = scores.get("per_sample") or []
    aggregate = scores.get("aggregate") or {}
    if metric_id == "sc":
        return True  # requires generate_num >= 2 by contract
    if metric_id == "sl_recall":
        return not any(isinstance(row.get("sl_recall"), (int, float)) for row in per_sample)
    if metric_id.startswith("latency_"):
        return not any(isinstance(row.get("act_elapsed_s"), (int, float)) for row in per_sample)
    if metric_id in {"token_total", "token_avg_per_sample", "cost_per_correct"}:
        return not (aggregate.get("token") or {}).get("total_calls")
    if metric_id.startswith("cf1_"):
        return not any(isinstance(row.get("cf1"), dict) for row in per_sample)
    if metric_id == "error_root_distribution":
        return all(row.get("ex") != 0 for row in per_sample)
    return False


def sample_diagnostics(scores: Dict[str, Any], registry: MetricRegistry | None = None) -> List[Dict[str, Any]]:
    """Publication-safe per-sample diagnostic records.

    Shape follows ``tools/evidence.ALLOWED_DIAGNOSTIC_FIELDS``; this builder
    also fills ``token_usage`` (allowed by the contract but historically never
    written by hand-rolled exports).
    """
    registry = registry or default_registry()
    if scores.get("schema_version") == 2:
        return _v2_sample_diagnostics(scores)
    records = []
    for row in scores.get("per_sample") or []:
        metrics = {}
        for metric_id in _L1_IDS:
            value = row.get(metric_id)
            if isinstance(value, (int, float)):
                metrics[metric_id] = value
        record: Dict[str, Any] = {
            "instance_id": row.get("instance_id"),
            "metrics": metrics,
            "stage_status": "completed" if row.get("exec_error") is None else "exec_error",
        }
        if row.get("error_root"):
            record["error_category"] = row["error_root"]
        if row.get("error_sub"):
            record["error_categories"] = [row["error_sub"]]
        if isinstance(row.get("act_elapsed_s"), (int, float)):
            record["latency_ms"] = round(float(row["act_elapsed_s"]) * 1000, 3)
        tokens = row.get("tokens")
        if isinstance(tokens, dict) and tokens:
            record["token_usage"] = {
                str(step): int(count)
                for step, count in tokens.items()
                if isinstance(count, (int, float))
            }
        records.append(record)
    return records


def _v2_sample_diagnostics(scores: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = []
    for row in scores.get("samples") or []:
        metrics = {
            metric_id: cell.get("value")
            for metric_id, cell in (row.get("metrics") or {}).items()
            if isinstance(cell, dict) and cell.get("status") == "available"
        }
        records.append({
            "instance_id": row.get("instance_id"), "metrics": metrics,
            "stage_status": "completed",
            "metric_availability": {
                metric_id: {"status": cell.get("status"), "reason": cell.get("reason")}
                for metric_id, cell in (row.get("metrics") or {}).items()
            },
        })
    return records


def sanitized_v2_export(scores: Dict[str, Any]) -> Dict[str, Any]:
    """Return protocol-rich v2 evidence without questions, SQL, paths or credentials."""
    from reproduce.eval.protocol import EvaluationBundleV2

    bundle = EvaluationBundleV2.model_validate(scores)
    public_run_id = _public_id("run", bundle.run_id)
    samples = []
    for sample in bundle.samples:
        candidate_ids = {candidate.candidate_id: _public_id("candidate", candidate.candidate_id)
                         for candidate in sample.candidates}
        metrics = {}
        for key, value in sample.metrics.items():
            public_key = _public_label(key)
            if public_key is not None:
                metrics[public_key] = _public_metric(value.model_dump(mode="json"), candidate_ids)
        samples.append({"instance_id": _public_id("sample", sample.instance_id),
                        "candidate_ids": list(candidate_ids.values()),
                        "selected_candidate_id": candidate_ids.get(sample.selected_candidate_id),
                        "metrics": metrics,
                        "attribution": [_public_attribution(value, candidate_ids)
                                        for value in sample.attribution],
                        "component_observations": [_public_component(value, candidate_ids)
                                                   for value in sample.component_observations]})
    aggregate = {}
    for key, value in bundle.aggregate.items():
        public_key = _public_label(key)
        if public_key is not None:
            aggregate[public_key] = _public_metric(value.model_dump(mode="json"), {})
    summary = _safe_nested((bundle.provenance or {}).get("attribution_summary"))
    return {
        "schema_version": 2, "bundle_protocol": bundle.bundle_protocol,
        "bundle_protocol_version": bundle.bundle_protocol_version, "run_id": public_run_id,
        "dataset": _public_label(bundle.dataset), "split": _public_label(bundle.split),
        "method": _public_label(bundle.method),
        "candidate_protocol": _public_label(bundle.candidate_protocol),
        "evaluator_protocol": _public_label(bundle.evaluator_protocol),
        "aggregate": aggregate,
        "samples": samples,
        "provenance": {"source_run_id": public_run_id,
                       "source_bundle_protocol": bundle.bundle_protocol_version,
                       "attribution_summary": summary},
    }


_UNSAFE_TEXT = re.compile(
    r"(?i)(?:\bselect\b.+\bfrom\b|https?://|/(?:users|home|root|private|tmp)/|"
    r"(?:api[_-]?key|password|bearer|secret|token|sk-[a-z0-9_-]+))"
)
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}")


def _public_id(prefix: str, value: str | None) -> str | None:
    if value is None:
        return None
    digest = hashlib.sha256(f"{prefix}\0{value}".encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _public_label(value: str) -> str | None:
    return value if _SAFE_LABEL.fullmatch(value) and not _UNSAFE_TEXT.search(value) else None


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, str) and _UNSAFE_TEXT.search(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _safe_nested(value: Any) -> Any:
    scalar = _safe_scalar(value)
    if scalar is not None or value is None:
        return scalar
    if isinstance(value, list):
        return [clean for item in value if (clean := _safe_nested(item)) is not None]
    if isinstance(value, dict):
        return {str(key): clean for key, item in value.items()
                if not _UNSAFE_TEXT.search(str(key)) and (clean := _safe_nested(item)) is not None}
    return None


def _pick_public(value: Dict[str, Any], keys: set[str]) -> Dict[str, Any]:
    result = {}
    for key in keys:
        if key not in value:
            continue
        clean = _safe_nested(value[key])
        if clean is not None or value[key] is None:
            result[key] = clean
    return result


def _public_metric(metric: Dict[str, Any], candidate_ids: Dict[str, str]) -> Dict[str, Any]:
    keys = {"metric_id", "protocol_id", "protocol_version", "status", "value", "categorical_counts", "numerator",
            "denominator", "coverage", "candidate_ids", "candidate_scope", "actual_k",
            "failed_count", "unavailable_count", "value_domain", "reason", "evidence_refs"}
    result = _pick_public(metric, keys - {"candidate_ids", "evidence_refs"})
    result["candidate_ids"] = [candidate_ids.get(value, _public_id("candidate", value))
                               for value in metric.get("candidate_ids") or []]
    result["evidence_refs"] = [_public_id("evidence", value)
                               for value in metric.get("evidence_refs") or []]
    provenance = metric.get("provenance") or {}
    safe_provenance_keys = {"identity", "native", "scope", "inclusive", "precision", "recall",
                            "legacy_alias", "legacy_alias_equivalent", "adapter_id", "benchmark",
                            "dataset", "split", "upstream_revision", "dependencies", "license_status",
                            "resource_checksums", "test_suite", "comparison_policy"}
    result["provenance"] = _pick_public(provenance, safe_provenance_keys)
    return result


def _public_attribution(value: Dict[str, Any], candidate_ids: Dict[str, str]) -> Dict[str, Any]:
    keys = {"attribution_protocol", "rule_id", "rule_version", "candidate_id", "primary_label",
            "supporting_evidence_refs", "alternative_observed_symptoms", "unknown_items", "interpretation"}
    result = _pick_public(value, keys - {"candidate_id", "supporting_evidence_refs"})
    result["candidate_id"] = candidate_ids.get(value.get("candidate_id"),
                                                _public_id("candidate", value.get("candidate_id")))
    result["supporting_evidence_refs"] = [_public_id("evidence", ref)
                                          for ref in value.get("supporting_evidence_refs") or []]
    return result


def _public_component(value: Dict[str, Any], candidate_ids: Dict[str, str]) -> Dict[str, Any]:
    keys = {"protocol_id", "stage_id", "task_id", "candidate_id", "iteration", "status",
            "before_checkpoint", "after_checkpoint", "eligible_pairs", "wrong_to_right",
            "right_to_wrong", "net_ex_change", "candidate_ids", "selected_candidate_id",
            "input_oracle", "selected_correct", "regret", "missed_correct",
            "unknown_candidate_outcomes", "input_candidate_count", "known_candidate_count",
            "oracle_denominator", "selected_denominator", "regret_denominator",
            "missed_correct_denominator", "correct_input_count", "surviving_correct_count",
            "gained_correct_count", "input_correct_denominator", "output_correct_denominator",
            "textual_diversity_denominator", "structural_diversity_denominator",
            "triggered", "plan_ref", "runtime_task_ids", "interpretation",
            "textual_diversity", "structural_diversity"}
    identity_keys = {"stage_id", "task_id", "candidate_id", "selected_candidate_id",
                     "before_checkpoint", "after_checkpoint", "plan_ref", "runtime_task_ids",
                     "candidate_ids"}
    result = _pick_public(value, keys - identity_keys)
    for key in ("stage_id", "task_id", "before_checkpoint", "after_checkpoint", "plan_ref"):
        if value.get(key) is not None:
            result[key] = _public_id(key, str(value[key]))
    for key in ("candidate_id", "selected_candidate_id"):
        if value.get(key) is not None:
            result[key] = candidate_ids.get(str(value[key]), _public_id("candidate", str(value[key])))
    result["candidate_ids"] = [candidate_ids.get(str(item), _public_id("candidate", str(item)))
                               for item in value.get("candidate_ids") or []]
    result["runtime_task_ids"] = [_public_id("task", str(item))
                                  for item in value.get("runtime_task_ids") or []]
    return result
