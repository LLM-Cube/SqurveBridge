"""Assemble detailed reproduce metrics into the scores.json contract.

Registry-era home of the bundle builder (previously
``reproduce.metrics.assembly``). Output stays contract-compatible with schema
version 1; the differences against the legacy builder are strictly additive or
bug fixes:

- ``aggregate.latency``: avg/p50/p95 per-sample actor wall time (previously
  captured per sample but never aggregated).
- ``aggregate.sl_recall``: schema-linking recall surfaced from per-sample rows.
- ``aggregate.stage``: per-stage Evaluator metrics (parse/reduce recall,
  precision, ...) surfaced from stage results.
- ``aggregate.token.by_step[*].per_call_p95``: a real 95th percentile over
  per-call token totals (previously ``max`` over per-tag means).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from reproduce.eval.aggregate.slicing import slice_rows
from reproduce.eval.aggregate.statistics import percentile, wilson_interval
from reproduce.eval.registry import default_registry
from reproduce.eval.sample.process import gate_funnel, process_summary, stage_survival_funnel
from reproduce.metrics.errors import classify_error
from reproduce.metrics.diagnostics import extract_actor_diagnostics
from reproduce.metrics.evaluators import _resolve_sql
from reproduce.metrics.feature_slices import (
    aggregate_scenarios,
    aggregate_sql_feature_slices,
    compute_qvt,
    enrich_sql_features,
)
from reproduce.metrics.pipeline_delta import compute_pipeline_delta
from reproduce.metrics.sql_parser import SQLFeatureExtractor
from reproduce.metrics.workflow import attach_workflow_trace, build_workflow_trace


METRIC_KEYS = ("em", "sf1", "sc", "ves", "rves")
CF1_KEYS = tuple(f"cf1_{key}" for key in SQLFeatureExtractor.COMPONENTS)
HARDNESS_ORDER = ("easy", "medium", "hard", "extra")


def build_scores(
        *,
        run_id: str,
        method: str,
        dataset_name: str,
        split: str,
        generate_num: int,
        config_path: str,
        data_lists: List[List[dict]],
        ex_result: dict,
        custom_results: Dict[str, dict],
        token_data: Optional[dict] = None,
        base_dataset: Any = None,
        actor_diagnostics: Optional[Dict[str, dict]] = None,
        stage_results: Optional[Dict[str, dict]] = None,
        scope: str = "full",
        statistical_validity: str = "full",
        convergence: Optional[dict] = None,
        config_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rows = (data_lists[0] if data_lists else []) or []
    if not isinstance(rows, list):
        rows = []
    ex_by_id = _details_by_id(ex_result.get("per_sample") or [])
    metric_by_id = {
        metric: _per_sample_by_id((custom_results.get(metric) or {}).get("per_sample") or [], "score")
        for metric in (*METRIC_KEYS, "cf1", "fd")
    }
    token_by_sample = _token_by_sample(token_data or {})
    actor_diagnostics = actor_diagnostics or {}

    per_sample = []
    for index, row in enumerate(rows):
        instance_id = str(row.get("instance_id", index))
        row_with_diagnostics = {**actor_diagnostics.get(instance_id, {}), **row}
        gold_sql = _resolve_sql(row_with_diagnostics.get("query"))
        pred_sql = _resolve_sql(row_with_diagnostics.get("pred_sql"))
        ex_detail = ex_by_id.get(instance_id, {})
        ex = ex_detail.get("ex")
        cf1 = metric_by_id["cf1"].get(instance_id)
        fd = metric_by_id["fd"].get(instance_id)
        exec_error = ex_detail.get("exec_error")
        diagnostics = extract_actor_diagnostics(row_with_diagnostics)
        error_root = None
        error_sub = None
        if ex == 0:
            classified = classify_error(
                row=row_with_diagnostics,
                pred_sql=pred_sql,
                gold_sql=gold_sql,
                cf1=cf1 if isinstance(cf1, dict) else None,
                fd=fd if isinstance(fd, dict) else None,
                exec_error=exec_error,
                sl_recall=diagnostics.get("sl_recall"),
                pred_classification=diagnostics.get("pred_classification"),
                gold_classification=diagnostics.get("gold_classification"),
            )
            error_root = classified.get("error_root")
            error_sub = classified.get("error_sub")

        sample = {
            "index": index,
            "instance_id": instance_id,
            "db_id": row.get("db_id"),
            "db_type": row.get("db_type"),
            "hardness": auto_hardness(gold_sql),
            "question": row.get("question"),
            "gold_sql": gold_sql,
            "pred_sql": pred_sql,
            "ex": ex,
            "em": metric_by_id["em"].get(instance_id),
            "sf1": metric_by_id["sf1"].get(instance_id),
            "sc": metric_by_id["sc"].get(instance_id),
            "ves": metric_by_id["ves"].get(instance_id),
            "rves": metric_by_id["rves"].get(instance_id),
            "cf1": cf1,
            "fd": fd,
            "error_root": error_root,
            "error_sub": error_sub,
            "exec_error": exec_error,
            "sl_recall": diagnostics.get("sl_recall"),
            "pred_classification": diagnostics.get("pred_classification"),
            "gold_classification": diagnostics.get("gold_classification"),
            "pipeline": compute_pipeline_delta(row_with_diagnostics, dataset=base_dataset),
            "tokens": token_by_sample.get(instance_id, {}),
            "act_elapsed_s": row.get("_act_elapsed_s"),
        }
        enrich_sql_features(sample)
        per_sample.append(sample)

    workflow_trace = None
    if config_snapshot:
        workflow_trace = build_workflow_trace(
            config=config_snapshot,
            stage_results=stage_results or {},
            final_per_sample=per_sample,
            data_rows=rows,
            dataset=base_dataset,
        )
        attach_workflow_trace(per_sample, workflow_trace)

    aggregate = _aggregate(ex_result, custom_results, per_sample, token_data or {})
    aggregate["latency"] = _aggregate_latency(per_sample)
    aggregate["sl_recall"] = _aggregate_sl_recall(per_sample)
    stage_summary = _aggregate_stage(stage_results or {})
    if stage_summary:
        aggregate["stage"] = stage_summary
    aggregate["process"] = process_summary(per_sample, aggregate)
    aggregate["funnel"] = {
        "gate": gate_funnel(per_sample),
        "stage": stage_survival_funnel(per_sample),
    }
    _attach_intervals(aggregate, per_sample)

    result = {
        "run_id": run_id,
        "ex_variant": "custom",
        "method": method,
        "dataset": dataset_name,
        "split": split,
        "generate_num": generate_num,
        "config_path": config_path,
        "scope": scope,
        "statistical_validity": statistical_validity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(rows),
        "convergence": convergence,
        "aggregate": aggregate,
        "by_hardness": _by_hardness(per_sample),
        "by_component_hardness": _by_component_hardness(per_sample),
        "by_db_type": _by_db_type(per_sample),
        "by_sql_feature": aggregate_sql_feature_slices(per_sample),
        "by_scenario": aggregate_scenarios(per_sample),
        "qvt": compute_qvt(per_sample),
        "per_sample": per_sample,
    }
    if workflow_trace:
        result["workflow_trace"] = workflow_trace
    if config_snapshot:
        result["config_snapshot"] = _build_config_snapshot(config_snapshot)
    return result


def _build_config_snapshot(config: dict) -> dict:
    """Extract a reproducibility-relevant config snapshot for scores.json."""
    llm = config.get("llm", {})
    task_meta = config.get("task", {}).get("task_meta", [])
    cpx_meta = config.get("task", {}).get("cpx_task_meta", [])
    exec_process = config.get("engine", {}).get("exec_process", [])

    actors = []
    for task in task_meta:
        actor_meta = task.get("meta", {})
        task_cfg = actor_meta.get("task", {})
        actor_cfg = actor_meta.get("actor", {})
        actor_class = (task_cfg.get("reduce_type") or task_cfg.get("parse_type")
                       or task_cfg.get("generate_type") or task_cfg.get("select_type") or "unknown")
        actors.append({
            "task_id": task.get("task_id"),
            "task_type": task.get("task_type"),
            "actor_class": actor_class,
            "actor_params": {k: v for k, v in actor_cfg.items() if k not in ("save_dir", "is_save")},
            "eval_type": task.get("eval_type", []),
            "dataset_save_path": task.get("dataset_save_path"),
        })

    workflow = []
    for cpx in cpx_meta:
        workflow.append({
            "task_id": cpx.get("task_id"),
            "stages": cpx.get("task_lis", []),
        })

    return {
        "llm": {
            "provider": llm.get("use"),
            "model": llm.get("model_name"),
            "temperature": llm.get("temperature"),
            "top_p": llm.get("top_p"),
            "max_token": llm.get("max_token"),
        },
        "data_source": config.get("dataset", {}).get("data_source"),
        "sampling": config.get("sampling"),
        "generate_num": config.get("generate_num", 1),
        "exec_process": exec_process,
        "workflow": workflow,
        "actors": actors,
    }


def auto_hardness(sql: Optional[str]) -> Optional[str]:
    if not sql:
        return None
    parser = SQLFeatureExtractor(sql)
    if not parser.valid:
        return None
    return parser.classify_hardness()


def _per_sample_by_id(items: Iterable[dict], value_key: str) -> Dict[str, Any]:
    result = {}
    for item in items:
        instance_id = item.get("instance_id")
        if instance_id is None:
            continue
        if value_key in item:
            value = item[value_key]
        else:
            value = {k: v for k, v in item.items() if k not in {"instance_id", "index"}}
        result[str(instance_id)] = value
    return result


def _details_by_id(items: Iterable[dict]) -> Dict[str, dict]:
    return {
        str(item["instance_id"]): item
        for item in items
        if isinstance(item, dict) and item.get("instance_id") is not None
    }


def _aggregate(ex_result: dict, custom_results: Dict[str, dict], per_sample: List[dict], token_data: dict) -> dict:
    aggregate = {
        "ex": {
            "avg": ex_result.get("avg"),
            "pass_count": ex_result.get("pass_count"),
            "valid": ex_result.get("valid", 0),
            "total": ex_result.get("total", len(per_sample)),
        }
    }
    for metric in METRIC_KEYS:
        result = custom_results.get(metric) or {}
        aggregate[metric] = {
            "avg": result.get("avg"),
            "valid": result.get("valid", 0),
            "total": result.get("total", len(per_sample)),
        }
        if metric == "sc" and aggregate[metric]["avg"] is None:
            aggregate[metric]["note"] = "require generate_num>=2"

    aggregate["cf1"] = _aggregate_cf1(per_sample)
    aggregate["fd"] = _aggregate_fd(per_sample)
    aggregate["error_root_distribution"] = _error_distribution(per_sample)
    aggregate["pipeline"] = _aggregate_pipeline(per_sample)
    aggregate["token"] = _aggregate_token(token_data, len(per_sample))
    return aggregate


def _aggregate_cf1(per_sample: List[dict]) -> dict:
    result = {}
    for key in CF1_KEYS:
        values = [
            sample["cf1"][key]
            for sample in per_sample
            if isinstance(sample.get("cf1"), dict) and isinstance(sample["cf1"].get(key), (int, float))
        ]
        result[key] = {"avg": _mean(values)}
    return result


def _aggregate_fd(per_sample: List[dict]) -> dict:
    keys = sorted({
        key
        for sample in per_sample
        if isinstance(sample.get("fd"), dict)
        for key in sample["fd"].keys()
    })
    result = {}
    for key in keys:
        values = [
            sample["fd"][key]
            for sample in per_sample
            if isinstance(sample.get("fd"), dict) and isinstance(sample["fd"].get(key), (int, float))
        ]
        result[key] = {"mean": _mean(values), "std": _std(values)}
    return result


def _error_distribution(per_sample: List[dict]) -> dict:
    failed = [sample for sample in per_sample if sample.get("ex") == 0]
    counter = Counter(sample.get("error_root") for sample in failed if sample.get("error_root"))
    result = {}
    for root, count in counter.items():
        result[root] = {
            "count": count,
            "pct": count / len(failed) if failed else 0,
            "sample_ids": [
                sample["instance_id"]
                for sample in failed
                if sample.get("error_root") == root
            ],
        }
    return result


def _aggregate_pipeline(per_sample: List[dict]) -> dict:
    scaler_rows = [sample["pipeline"]["scaler"] for sample in per_sample if sample["pipeline"]["scaler"].get("has_scaler")]
    optimizer_rows = [
        sample["pipeline"]["optimizer"]
        for sample in per_sample
        if sample["pipeline"]["optimizer"].get("has_optimizer")
    ]
    selector_rows = [
        sample["pipeline"]["selector"]
        for sample in per_sample
        if sample["pipeline"]["selector"].get("has_selector")
    ]
    decomposer_rows = [
        sample["pipeline"]["decomposer"]
        for sample in per_sample
        if sample["pipeline"]["decomposer"].get("has_decomposer")
    ]
    return {
        "scaler": {
            "samples_with_scaler": len(scaler_rows),
            "avg_candidate_count": _mean([
                row["candidate_count"] for row in scaler_rows if isinstance(row.get("candidate_count"), (int, float))
            ]),
            "avg_candidate_diversity": _mean([
                row["candidate_diversity"]
                for row in scaler_rows
                if isinstance(row.get("candidate_diversity"), (int, float))
            ]),
            "pass_1": _mean([row["pass_1"] for row in scaler_rows if isinstance(row.get("pass_1"), (int, float))]),
            "pass_k": _mean([row["pass_k"] for row in scaler_rows if isinstance(row.get("pass_k"), (int, float))]),
            "scaler_gain": _mean([
                row["scaler_gain"] for row in scaler_rows if isinstance(row.get("scaler_gain"), (int, float))
            ]),
        },
        "optimizer": {
            "samples_with_optimizer": len(optimizer_rows),
            "fix_success_rate": _bool_rate(optimizer_rows, "fix_success"),
            "degradation_rate": _bool_rate(optimizer_rows, "degradation"),
            "net_gain": sum(
                (row.get("ex_after") or 0) - (row.get("ex_before") or 0)
                for row in optimizer_rows
                if isinstance(row.get("ex_before"), (int, float)) and isinstance(row.get("ex_after"), (int, float))
            ) if optimizer_rows else None,
            "avg_debug_turns": _mean([
                row["debug_turns"] for row in optimizer_rows if isinstance(row.get("debug_turns"), (int, float))
            ]),
        },
        "selector": {
            "samples_with_selector": len(selector_rows),
            "oracle_rate": _mean([
                row["oracle_ex"] for row in selector_rows if isinstance(row.get("oracle_ex"), (int, float))
            ]),
            "selection_accuracy": _mean([
                row["selection_accuracy"]
                for row in selector_rows
                if isinstance(row.get("selection_accuracy"), (int, float))
            ]),
            "selection_gain": _mean([
                row["selection_gain"] for row in selector_rows if isinstance(row.get("selection_gain"), (int, float))
            ]),
            "selection_loss": _mean([
                row["selection_loss"] for row in selector_rows if isinstance(row.get("selection_loss"), (int, float))
            ]),
        },
        "decomposer": {
            "samples_with_decomposer": len(decomposer_rows),
            "trigger_rate": _ratio(len(decomposer_rows), len(per_sample)),
            "trigger_accuracy": _mean([
                sample["ex"]
                for sample in per_sample
                if sample["pipeline"]["decomposer"].get("has_decomposer")
                and isinstance(sample.get("ex"), (int, float))
            ]),
            "avg_sub_question_count": _mean([
                sample["pipeline"]["decomposer"]["sub_question_count"]
                for sample in per_sample
                if isinstance(sample["pipeline"]["decomposer"].get("sub_question_count"), (int, float))
            ]),
        },
    }


def _aggregate_token(token_data: dict, sample_count: int) -> dict:
    total = token_data.get("total") or {}
    by_tag = token_data.get("by_tag") or {}
    call_totals_by_step = _call_totals_by_step(token_data)
    by_step = {}
    for tag, stats in by_tag.items():
        step = tag.split("|")[-1] if tag else "unknown"
        current = by_step.setdefault(step, {"calls": 0, "total_tokens": 0})
        current["calls"] += stats.get("calls", 0)
        current["total_tokens"] += stats.get("total_tokens", 0)

    formatted = {}
    for step, stats in by_step.items():
        call_totals = call_totals_by_step.get(step) or []
        formatted[step] = {
            "calls": stats["calls"],
            "total_tokens": stats["total_tokens"],
            "per_call_mean": stats["total_tokens"] / stats["calls"] if stats["calls"] else None,
            # Real 95th percentile over per-call token totals. The legacy
            # builder reported max() over per-tag means under this key.
            "per_call_p95": percentile(call_totals, 95) if call_totals else None,
        }

    return {
        "total_calls": total.get("calls", 0),
        "total_prompt_tokens": total.get("prompt_tokens", 0),
        "total_completion_tokens": total.get("completion_tokens", 0),
        "total_tokens": total.get("total_tokens", 0),
        "avg_per_sample": total.get("total_tokens", 0) / sample_count if sample_count else None,
        "by_step": formatted,
    }


def _call_totals_by_step(token_data: dict) -> Dict[str, List[float]]:
    result: Dict[str, List[float]] = defaultdict(list)
    for record in token_data.get("records") or []:
        tag = record.get("tag") or ""
        step = tag.split("|")[-1] if tag else "unknown"
        total_tokens = record.get("total_tokens")
        if isinstance(total_tokens, (int, float)):
            result[step].append(float(total_tokens))
    return dict(result)


def _token_by_sample(token_data: dict) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = defaultdict(dict)
    for record in token_data.get("records") or []:
        tag = record.get("tag")
        if not tag or not tag.startswith("sample:"):
            continue
        parts = tag.split("|")
        sample_id = parts[0].removeprefix("sample:")
        step = parts[-1] if len(parts) > 1 else "unknown"
        result[sample_id][step] = result[sample_id].get(step, 0) + record.get("total_tokens", 0)
    return dict(result)


def _attach_intervals(aggregate: Dict[str, Any], per_sample: List[dict]) -> None:
    """Wilson 95% intervals for the Bernoulli quality metrics.

    Bounded slices (n=50 smoke runs) must not read like full-split results;
    the interval makes the sample-size caveat part of the number itself.
    """
    for metric in ("ex", "em"):
        cell = aggregate.get(metric)
        values = [
            float(sample[metric])
            for sample in per_sample
            if isinstance(sample.get(metric), (int, float))
        ]
        if isinstance(cell, dict) and values:
            interval = wilson_interval(sum(values), len(values))
            if interval:
                cell["interval"] = [round(bound, 6) for bound in interval]
                cell["interval_kind"] = "wilson95"


def _aggregate_latency(per_sample: List[dict]) -> dict:
    values = [
        float(sample["act_elapsed_s"])
        for sample in per_sample
        if isinstance(sample.get("act_elapsed_s"), (int, float))
    ]
    return {
        "sample_count": len(values),
        "avg_s": _mean(values),
        "p50_s": percentile(values, 50),
        "p95_s": percentile(values, 95),
    }


def _aggregate_sl_recall(per_sample: List[dict]) -> dict:
    values = [
        float(sample["sl_recall"])
        for sample in per_sample
        if isinstance(sample.get("sl_recall"), (int, float))
    ]
    return {"avg": _mean(values), "valid": len(values), "total": len(per_sample)}


def _aggregate_stage(stage_results: Dict[str, dict]) -> dict:
    """Surface per-stage Evaluator metrics (recall/precision/...) in aggregate."""
    summary = {}
    for task_id, payload in stage_results.items():
        metrics = payload.get("metrics") or {}
        if not metrics:
            continue
        summary[task_id] = {
            "task_type": payload.get("task_type"),
            "metrics": {
                name: {
                    "avg": stats.get("avg"),
                    "valid": stats.get("valid_num", 0),
                    "total": stats.get("total_items", 0),
                }
                for name, stats in metrics.items()
            },
        }
    return summary


def _by_hardness(per_sample: List[dict]) -> dict:
    registry = default_registry()
    groups = slice_rows(per_sample, registry.get_slice("hardness"))
    return {label: _slice_cell(rows) for label, rows in groups.items()}


def _by_db_type(per_sample: List[dict]) -> dict:
    registry = default_registry()
    groups = slice_rows(per_sample, registry.get_slice("db_type"))
    return {label: _slice_cell(rows) for label, rows in groups.items()}


def _slice_cell(rows: List[dict]) -> dict:
    return {
        "count": len(rows),
        "ex": _mean([sample["ex"] for sample in rows if isinstance(sample.get("ex"), (int, float))]),
        "em": _mean([sample["em"] for sample in rows if isinstance(sample.get("em"), (int, float))]),
        "cf1_join": _mean([
            sample["cf1"]["cf1_join"]
            for sample in rows
            if isinstance(sample.get("cf1"), dict) and isinstance(sample["cf1"].get("cf1_join"), (int, float))
        ]),
        "cf1_where": _mean([
            sample["cf1"]["cf1_where"]
            for sample in rows
            if isinstance(sample.get("cf1"), dict) and isinstance(sample["cf1"].get("cf1_where"), (int, float))
        ]),
        "error_dist": _error_distribution(rows),
    }


def _by_component_hardness(per_sample: List[dict]) -> dict:
    registry = default_registry()
    groups = slice_rows(per_sample, registry.get_slice("hardness"))
    result = {}
    for component in CF1_KEYS:
        result[component] = {}
        for hardness, rows in groups.items():
            values = [
                sample["cf1"][component]
                for sample in rows
                if isinstance(sample.get("cf1"), dict)
                and isinstance(sample["cf1"].get(component), (int, float))
            ]
            result[component][hardness] = _mean(values)
    return result


def _mean(values: List[float]) -> Optional[float]:
    # Deliberately not statistics.mean: the legacy builder returned int means
    # for all-int inputs (e.g. ex of [1, 1] -> 1, not 1.0), and this module
    # promises output-identical bundles for pre-existing fields.
    return sum(values) / len(values) if values else None


def _std(values: List[float]) -> Optional[float]:
    return statistics.pstdev(values) if len(values) > 1 else 0 if values else None


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def _bool_rate(rows: List[dict], key: str) -> Optional[float]:
    values = [row[key] for row in rows if isinstance(row.get(key), bool)]
    return sum(1 for value in values if value) / len(values) if values else None
