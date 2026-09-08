from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .analysis import structural_analysis, structural_match
from .components import (ComponentTransition, aggregate_component_transitions,
                         evaluate_component_transition, evaluate_decomposition,
                         evaluate_scaling, evaluate_selection)
from .comparison import compare_results
from .execution import ExecutionResult, SQLExecutor
from .attribution import attribute_execution
from .metrics import aggregate_token_observations, self_consistency, soft_f1
from .models import (Candidate, ComparisonPolicy, EvaluationBundleV2, MetricResult,
                     SampleEvaluation)
from .metrics import TimingObservation, TokenObservation


def _schema_mapping(value: Any) -> dict[str, set[str]] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, set[str]] = {}
    for table, raw_columns in value.items():
        if isinstance(raw_columns, dict):
            raw_columns = raw_columns.get("columns", raw_columns.keys())
        if isinstance(raw_columns, (list, tuple, set)):
            columns = {str(item.get("name") if isinstance(item, dict) else item)
                       for item in raw_columns}
            result[str(table)] = {column for column in columns if column and column != "None"}
    return result


def _available_metric(metric_id: str, value: float | None, *, protocol_id: str,
                      candidate_ids: list[str] | None = None, scope: str = "none",
                      numerator: float | None = None, denominator: int | None = None,
                      value_domain: str = "unit_interval", reason: str = "evidence_not_present",
                      provenance: dict[str, Any] | None = None) -> MetricResult:
    available = value is not None
    return MetricResult(
        metric_id=metric_id, protocol_id=protocol_id, protocol_version="2",
        status="available" if available else "unavailable", value=value,
        numerator=numerator if available else None, denominator=denominator if available else None,
        coverage=1 if available else 0, candidate_ids=candidate_ids or [], candidate_scope=scope,
        value_domain=value_domain, reason=None if available else reason,
        provenance=provenance or {},
    )


def _component_evidence(sample: dict[str, Any], *, instance_id: str,
                        known_candidates: set[str]) -> tuple[list[dict[str, Any]], dict[str, MetricResult]]:
    observations: list[Any] = []
    for raw in sample.get("component_observations") or []:
        kind = raw.get("kind")
        context = {
            "stage_id": str(raw["stage_id"]), "task_id": str(raw["task_id"]),
            "instance_id": instance_id, "iteration": int(raw.get("iteration", 0)),
        }
        if kind == "transition":
            observations.append(evaluate_component_transition(
                **context, candidate_id=str(raw["candidate_id"]),
                before_correct=raw.get("before_correct"), after_correct=raw.get("after_correct"),
                before_checkpoint=raw.get("before_checkpoint"), after_checkpoint=raw.get("after_checkpoint"),
            ))
        elif kind == "selection":
            outcome_ids = {str(key) for key in raw["candidate_outcomes"]}
            if not outcome_ids.issubset(known_candidates):
                raise ValueError("selection observation references a candidate outside the sample pool")
            observations.append(evaluate_selection(
                {str(key): value for key, value in raw["candidate_outcomes"].items()},
                str(raw["selected_candidate_id"]), **context,
            ))
        elif kind == "scaling":
            scaling_ids = ({str(key) for key in raw["input_sql"]}
                           | {str(key) for key in raw["output_sql"]})
            if not scaling_ids.issubset(known_candidates):
                raise ValueError("scaling observation references a candidate outside the sample pool")
            observations.append(evaluate_scaling(
                {str(k): str(v) for k, v in raw["input_sql"].items()},
                {str(k): str(v) for k, v in raw["output_sql"].items()},
                {str(k): v for k, v in raw["input_correct"].items()},
                {str(k): v for k, v in raw["output_correct"].items()},
                structural_keys={str(k): str(v) for k, v in (raw.get("structural_keys") or {}).items()},
                **context,
            ))
        elif kind == "decomposition":
            observations.append(evaluate_decomposition(
                **context, triggered=bool(raw["triggered"]), plan_ref=raw.get("plan_ref"),
                runtime_task_ids=[str(value) for value in raw.get("runtime_task_ids") or []],
            ))
    metrics: dict[str, MetricResult] = {}
    transitions = [item for item in observations if isinstance(item, ComponentTransition)]
    if transitions:
        summary = aggregate_component_transitions(transitions)
        transition_candidates = sorted({item.candidate_id for item in transitions} & known_candidates)
        metrics["refinement_fix_rate"] = _available_metric(
            "refinement_fix_rate", summary.fix_rate_given_wrong,
            protocol_id=summary.protocol_id, candidate_ids=transition_candidates, scope="stage_pair",
            numerator=summary.wrong_to_right, denominator=summary.wrong_before_count or None,
        )
        metrics["refinement_degradation_rate"] = _available_metric(
            "refinement_degradation_rate", summary.degradation_rate_given_correct,
            protocol_id=summary.protocol_id, candidate_ids=transition_candidates, scope="stage_pair",
            numerator=summary.right_to_wrong, denominator=summary.correct_before_count or None,
        )
        metrics["refinement_net_gain"] = _available_metric(
            "refinement_net_gain", summary.normalized_net_ex_change,
            protocol_id=summary.protocol_id, candidate_ids=transition_candidates, scope="stage_pair",
            denominator=summary.eligible_pairs or None, value_domain="unbounded",
            provenance=summary.model_dump(mode="json"),
        )
    selections = [item for item in observations if item.protocol_id == "project.selection-observation@2"]
    if selections:
        item = max(selections, key=lambda value: value.iteration)
        metrics["selection_accuracy"] = _available_metric(
            "selection_accuracy", item.selected_correct, protocol_id=item.protocol_id,
            candidate_ids=[item.selected_candidate_id], scope="stage_pair",
            numerator=item.selected_correct,
            denominator=item.selected_denominator or None,
        )
        metrics["selection_regret"] = _available_metric(
            "selection_regret", item.regret, protocol_id=item.protocol_id,
            candidate_ids=item.candidate_ids, scope="stage_pair", numerator=item.regret,
            denominator=item.regret_denominator or None,
        )
        metrics["selection_missed_correct_rate"] = _available_metric(
            "selection_missed_correct_rate", float(item.missed_correct) if item.missed_correct is not None else None,
            protocol_id=item.protocol_id, candidate_ids=item.candidate_ids, scope="stage_pair",
            numerator=item.missed_correct, denominator=item.missed_correct_denominator or None,
        )
    scalings = [item for item in observations if item.protocol_id == "project.scaling-observation@2"]
    if scalings:
        item = max(scalings, key=lambda value: value.iteration)
        metrics["generation_candidate_diversity"] = _available_metric(
            "generation_candidate_diversity", item.textual_diversity,
            protocol_id=item.protocol_id,
            numerator=(item.textual_diversity * item.textual_diversity_denominator
                       if item.textual_diversity is not None else None),
            denominator=item.textual_diversity_denominator or None,
            provenance={"structural_diversity": item.structural_diversity,
                        "structural_diversity_denominator": item.structural_diversity_denominator,
                        "correct_input_count": item.correct_input_count,
                        "surviving_correct_count": item.surviving_correct_count,
                        "gained_correct_count": item.gained_correct_count},
        )
    decompositions = [item for item in observations
                      if item.protocol_id == "project.decomposition-observation@2"]
    if decompositions:
        triggered = float(any(item.triggered for item in decompositions))
        metrics["decomposition_trigger_rate"] = _available_metric(
            "decomposition_trigger_rate", triggered,
            protocol_id="project.decomposition-observation@2", numerator=triggered, denominator=1,
            provenance={"interpretation": "observed_trigger_only"},
        )
    return [item.model_dump(mode="json") for item in observations], metrics


def _metric(metric_id: str, candidate_ids: list[str], outcomes: list[bool | None],
            scope: str, *, failed: int = 0) -> MetricResult:
    unavailable = sum(outcome is None for outcome in outcomes)
    denominator = len(outcomes)
    available_count = denominator - unavailable
    if metric_id == "observed_oracle_at_k":
        if any(outcome is True for outcome in outcomes):
            value = 1.0
        elif denominator and available_count == denominator:
            value = 0.0
        else:
            value = None
        numerator = int(value == 1) if value is not None else None
    else:
        value = float(outcomes[0]) if outcomes and outcomes[0] is not None else None
        numerator = int(value) if value is not None else None
    return MetricResult(
        metric_id=metric_id, protocol_id="project.execution-result-equality",
        protocol_version="2.0.0", status=("available" if value is not None else
                                           "partial" if available_count else "unavailable"),
        value=value, numerator=numerator, denominator=1 if value is not None else None,
        coverage=available_count / denominator if denominator else 0,
        candidate_ids=candidate_ids, candidate_scope=scope, actual_k=denominator,
        failed_count=failed, unavailable_count=unavailable, value_domain="unit_interval",
        reason=(None if value is not None else "incomplete_candidate_outcomes"
                if available_count else "no_comparable_execution"),
    )


def evaluate_checkpoint(checkpoint_path: str | Path, config: dict[str, Any],
                        *, executor: SQLExecutor | None = None) -> EvaluationBundleV2:
    path = Path(checkpoint_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    executor = executor or SQLExecutor()
    policy = ComparisonPolicy.model_validate(config.get("comparison") or {})
    db_paths = sorted({str(sample.get("db_path")) for sample in raw.get("samples") or [] if sample.get("db_path")})
    snapshot_by_path: dict[str, str] = {}
    for db_path in db_paths:
        db_file = Path(db_path)
        snapshot_by_path[db_path] = (hashlib.sha256(db_file.read_bytes()).hexdigest()
                                     if db_file.is_file() else "missing")
    snapshot = raw.get("database_snapshot") or hashlib.sha256(
        "\0".join(f"{key}:{snapshot_by_path[key]}" for key in db_paths).encode()).hexdigest()
    samples: list[SampleEvaluation] = []
    for sample in raw.get("samples") or []:
        instance_id = str(sample["instance_id"])
        schema = _schema_mapping(sample.get("schema") or sample.get("instance_schema"))
        candidates = [Candidate(candidate_id=str(item["candidate_id"]),
                                occurrence_id=str(item.get("occurrence_id") or f"{instance_id}:{index}"),
                                sql=str(item["sql"]), ordinal=index,
                                parent_candidate_id=item.get("parent_candidate_id"))
                      for index, item in enumerate(sample.get("candidates") or [])]
        gold_sql = sample.get("gold_sql")
        sample_snapshot = (f"{raw['database_snapshot']}:{sample['db_path']}"
                           if raw.get("database_snapshot") else snapshot_by_path.get(str(sample.get("db_path")), "missing"))
        if gold_sql:
            gold = executor.execute_sqlite(sample["db_path"], gold_sql, run_id=raw["run_id"],
                                           instance_id=instance_id, candidate_id="__gold__",
                                           database_snapshot=sample_snapshot,
                                           stage_id="evaluation.gold")
        else:
            gold = ExecutionResult(execution_id=f"{raw['run_id']}:{instance_id}:gold:unavailable",
                                   run_id=raw["run_id"], instance_id=instance_id, candidate_id="__gold__",
                                   stage_id="evaluation.gold", database_snapshot=sample_snapshot,
                                   status="ground_truth_unavailable")
        results = [executor.execute_sqlite(sample["db_path"], candidate.sql, run_id=raw["run_id"],
                                           instance_id=instance_id, candidate_id=candidate.candidate_id,
                                           database_snapshot=sample_snapshot,
                                           stage_id="evaluation.prediction") for candidate in candidates]
        outcomes = []
        for result in results:
            if not gold.comparable:
                outcomes.append(None)
            elif result.comparable:
                outcomes.append(compare_results(result.rows or [], gold.rows or [], policy,
                                                predicted_columns=result.columns,
                                                gold_columns=gold.columns).equal)
            elif result.status in {"execution_error", "timeout", "missing_input"}:
                outcomes.append(False)
            else:
                outcomes.append(None)
        selected_id = sample.get("selected_candidate_id")
        selected_index = next((i for i, c in enumerate(candidates) if c.candidate_id == selected_id), None)
        selected_outcomes = [outcomes[selected_index]] if selected_index is not None else [None]
        selected_result = results[selected_index] if selected_index is not None else None
        metrics = {
            "ex_at_1": _metric("ex_at_1", [candidates[0].candidate_id] if candidates else [],
                               outcomes[:1] or [None], "first",
                               failed=int(bool(results) and results[0].status != "success")),
            "selected_ex": _metric("selected_ex", [selected_id] if selected_id else [],
                                   selected_outcomes, "selected",
                                   failed=int(selected_result is not None and selected_result.status != "success")),
            "observed_oracle_at_k": _metric("observed_oracle_at_k", [c.candidate_id for c in candidates],
                                            outcomes, "observed_pool",
                                            failed=sum(result.status != "success" for result in results)),
        }
        if selected_result is not None:
            metrics["sf1"] = soft_f1(selected_result, gold, candidate_id=selected_id, policy=policy)
            timing_result = selected_result
            if config.get("measure_efficiency"):
                timing_result = executor.execute_sqlite(
                    sample["db_path"], candidates[selected_index].sql, run_id=raw["run_id"],
                    instance_id=instance_id, candidate_id=selected_id,
                    database_snapshot=sample_snapshot, use_cache=False, efficiency_measurement=True,
                    stage_id="evaluation.efficiency",
                )
            timing_value = timing_result.timing.total_s if config.get("measure_efficiency") else None
            metrics["sql_execution_time"] = MetricResult(
                metric_id="sql_execution_time", protocol_id="monotonic-sql-execution-timing",
                protocol_version="1", status="available" if timing_value is not None else "unavailable",
                value=timing_value, coverage=1 if timing_value is not None else 0,
                candidate_ids=[selected_id], candidate_scope="selected", actual_k=1,
                value_domain="non_negative",
                reason=None if timing_value is not None else "efficiency_measurement_not_requested",
                provenance={"scope": "connection+query+fetch+materialization", "inclusive": True,
                            "cache_bypass": True},
            )
        metrics["self_consistency"] = self_consistency(results, policy)
        runtime = sample.get("runtime_evidence") or {}
        token_payloads = runtime.get("token_usage") or sample.get("token_usage") or []
        token_observations = [TokenObservation.model_validate(item) for item in token_payloads
                              if isinstance(item, dict)]
        token_total, token_coverage, token_provenance = aggregate_token_observations(token_observations)
        token_complete = token_total is not None
        metrics["token_total"] = MetricResult(
            metric_id="token_total", protocol_id="provider-reported-token-usage",
            protocol_version="1", status="available" if token_complete else "unavailable",
            value=float(token_total) if token_complete else None, coverage=token_coverage,
            candidate_scope="none", value_domain="non_negative",
            reason=None if token_complete else "token_evidence_not_present_or_partial",
            provenance={**token_provenance, "usage_scope": "provider_or_sdk_reported"},
        )
        analyses = {candidate.candidate_id: structural_analysis(candidate.sql, "sqlite", schema=schema)
                    for candidate in candidates}
        gold_analysis = structural_analysis(gold_sql, "sqlite", schema=schema) if gold_sql else None
        structural_value = (structural_match(analyses[selected_id], gold_analysis)
                            if selected_id in analyses and gold_analysis is not None else None)
        metrics["structural_match"] = MetricResult(
            metric_id="structural_match", protocol_id="project.sqlglot-structural-match",
            protocol_version="2.0.0", status="available" if structural_value is not None else "unavailable",
            value=float(structural_value) if structural_value is not None else None,
            numerator=int(structural_value) if structural_value is not None else None,
            denominator=1 if structural_value is not None else None,
            coverage=1 if structural_value is not None else 0,
            candidate_ids=[selected_id] if selected_id else [], candidate_scope="selected", actual_k=1,
            value_domain="unit_interval", reason=None if structural_value is not None else "analysis_unavailable",
            provenance={"legacy_alias": "em", "legacy_alias_equivalent": False},
        )
        for metric_id in ("ves", "rves"):
            metrics[metric_id] = MetricResult(
                metric_id=metric_id, protocol_id=f"project.legacy-{metric_id}", protocol_version="1",
                status="unavailable", coverage=0, candidate_ids=[selected_id] if selected_id else [],
                candidate_scope="selected", actual_k=1, value_domain="non_negative",
                reason="efficiency_measurement_not_requested",
                provenance={"identity": "project_legacy", "native": False},
            )
        timing_observations = [TimingObservation.model_validate(item)
                               for item in runtime.get("timings") or [] if isinstance(item, dict)]
        for metric_id, scope in (("actor_time", "actor_inclusive"),
                                 ("workflow_time", "workflow_end_to_end")):
            matching_timings = [item for item in timing_observations
                                if item.scope == scope and item.clock == "monotonic"
                                and item.status in {"complete", "success"}
                                and item.duration_s is not None]
            timing_value = matching_timings[0].duration_s if len(matching_timings) == 1 else None
            metrics[metric_id] = MetricResult(
                metric_id=metric_id, protocol_id="monotonic-operation-timing", protocol_version="1",
                status="available" if timing_value is not None else "unavailable", value=timing_value,
                coverage=1 if timing_value is not None else 0, candidate_scope="none",
                value_domain="non_negative",
                reason=None if timing_value is not None else "runtime_timing_evidence_not_present",
                provenance={"scope": scope, "inclusive": True, "clock": "monotonic",
                            "operation_id": (matching_timings[0].operation_id
                                             if len(matching_timings) == 1 else None)},
            )
        component_observations, component_metrics = _component_evidence(
            sample, instance_id=instance_id,
            known_candidates={candidate.candidate_id for candidate in candidates},
        )
        metrics.update(component_metrics)
        from reproduce.eval.registry import default_registry
        for metric_spec in default_registry().metrics():
            if (not metric_spec.source.startswith("derived:v2")
                    or metric_spec.layer == "L4" or metric_spec.id in metrics):
                continue
            candidate_ids = [selected_id] if metric_spec.candidate_scope == "selected" and selected_id else []
            metrics[metric_spec.id] = MetricResult(
                metric_id=metric_spec.id, protocol_id=metric_spec.protocol_id,
                protocol_version=metric_spec.protocol_version, status="unavailable", coverage=0,
                candidate_ids=candidate_ids, candidate_scope=(metric_spec.candidate_scope
                                                               if metric_spec.candidate_scope in {"first", "selected", "observed_pool", "stage_pair", "none"}
                                                               else "none"),
                actual_k=1 if candidate_ids else 0,
                value_domain="unit_interval" if metric_spec.unit == "ratio" else "non_negative",
                reason=metric_spec.availability,
            )
        attributions = []
        for result in results:
            analysis = analyses[result.candidate_id]
            schema_resolution = (analysis.schema_resolution
                                 if analysis.schema_resolution in {"resolved", "unresolved", "ambiguous"}
                                 else None)
            differences = (["selected_structural_mismatch"]
                           if result.candidate_id == selected_id and structural_value is False else [])
            outcome = outcomes[next(index for index, item in enumerate(results)
                                    if item.candidate_id == result.candidate_id)]
            attributions.append(attribute_execution(
                result, parse_success=analysis.parse_success,
                schema_resolution=schema_resolution,
                execution_correct=outcome,
                structural_differences=differences,
                analysis_ref=f"analysis:{analysis.analysis_id}",
                structural_ref=(f"metric:{instance_id}:structural_match"
                                if result.candidate_id == selected_id else None),
            ).model_dump(mode="json"))
        samples.append(SampleEvaluation(
            instance_id=instance_id, candidates=candidates, selected_candidate_id=selected_id,
            metrics=metrics, execution_results=[gold.model_dump(mode="json")] +
                    [result.model_dump(mode="json") for result in results],
            analyses={key: value.model_dump(mode="json") for key, value in analyses.items()},
            attribution=attributions, component_observations=component_observations,
        ))
    aggregate: dict[str, MetricResult] = {}
    metric_ids = sorted({metric_id for sample in samples for metric_id in sample.metrics})
    for metric_id in metric_ids:
        records = [sample.metrics[metric_id] for sample in samples if metric_id in sample.metrics]
        available = [record for record in records if record.status == "available"]
        numerator = sum(record.value or 0 for record in available)
        denominator = len(available)
        exemplar = records[0]
        aggregate[metric_id] = MetricResult(
            metric_id=metric_id, protocol_id=exemplar.protocol_id,
            protocol_version=exemplar.protocol_version, status="available" if denominator else "unavailable",
            value=numerator / denominator if denominator else None, numerator=numerator if denominator else None,
            denominator=denominator if denominator else None,
            coverage=(sum(record.coverage for record in records) / len(records) if records else 0),
            candidate_scope=exemplar.candidate_scope,
            failed_count=sum(record.failed_count for record in records),
            unavailable_count=len(records) - denominator, value_domain=exemplar.value_domain,
            reason=None if denominator else "no_evaluable_samples",
        )
    outcome_distribution: dict[str, int] = {}
    failure_bottlenecks: dict[str, int] = {}
    for sample in samples:
        for attribution in sample.attribution:
            if attribution.get("candidate_id") != sample.selected_candidate_id:
                continue
            label = str(attribution.get("primary_label") or "unknown")
            outcome_distribution[label] = outcome_distribution.get(label, 0) + 1
            if label != "success":
                failure_bottlenecks[label] = failure_bottlenecks.get(label, 0) + 1
    if samples:
        aggregate["error_root_distribution"] = MetricResult(
            metric_id="error_root_distribution", protocol_id="project.failure-bottleneck-distribution",
            protocol_version="2", status="available", categorical_counts=failure_bottlenecks,
            coverage=1, candidate_scope="selected", value_domain="unbounded",
        )
        aggregate["stage_attribution"] = MetricResult(
            metric_id="stage_attribution", protocol_id="project.attribution-outcome-distribution",
            protocol_version="2", status="available", categorical_counts=outcome_distribution,
            coverage=1, candidate_scope="selected", value_domain="unbounded",
        )
    from reproduce.eval.registry import default_registry
    for metric_spec in default_registry().metrics():
        if metric_spec.id in aggregate:
            continue
        scope = (metric_spec.candidate_scope
                 if metric_spec.candidate_scope in {"first", "selected", "observed_pool", "stage_pair", "none"}
                 else "none")
        aggregate[metric_spec.id] = MetricResult(
            metric_id=metric_spec.id, protocol_id=metric_spec.protocol_id,
            protocol_version=metric_spec.protocol_version, status="unavailable", coverage=0,
            candidate_scope=scope,
            value_domain="unit_interval" if metric_spec.unit == "ratio" else "non_negative",
            reason=("not_applicable:cross_run_metric" if metric_spec.layer == "L6"
                    else metric_spec.availability),
        )
    return EvaluationBundleV2(
        run_id=raw["run_id"], dataset=raw["dataset"], split=raw["split"], method=raw["method"],
        database_snapshot=snapshot, model_config_id=str(raw.get("model_config_id", "unknown")),
        samples=samples, aggregate=aggregate,
        provenance={"checkpoint": str(path), "comparison_policy": policy.model_dump(mode="json"),
                    "attribution_summary": {
                        "outcome_distribution": outcome_distribution,
                        "failure_only_bottleneck_distribution": failure_bottlenecks,
                        "protocol_id": "project.rule-attribution@2",
                    }},
    )
