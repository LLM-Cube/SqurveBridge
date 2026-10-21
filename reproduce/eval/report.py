"""Format reproduce evaluation results for terminal output.

Produces a detailed, exhaustive report covering:
1. Configuration snapshot (LLM, dataset, task pipeline)
2. Workflow overview (actor chain, execution order)
3. Per-stage / per-actor metrics (reduce, parse, generate, select)
4. Final metrics (EX, EM, SF1, SC, VES, CF1, FD)
5. Placeholder entries for metrics that cannot be computed under current config
6. Intermediate data traceability (file paths for each artifact)
7. Token usage summary
8. Hardness breakdown & error root-cause analysis
"""

from __future__ import annotations

import io
from typing import Dict, List, Optional


def format_v2_report(scores: dict) -> str:
    """Render protocol identity, value, coverage and availability for a v2 bundle."""
    from reproduce.eval.protocol import EvaluationBundleV2

    bundle = EvaluationBundleV2.model_validate(scores)
    lines = [f"Evaluation {bundle.run_id}",
             f"bundle={bundle.bundle_protocol}@{bundle.bundle_protocol_version}",
             f"evaluator={bundle.evaluator_protocol}",
             f"cohort={bundle.dataset}/{bundle.split} snapshot={bundle.database_snapshot}", ""]
    for metric_id, metric in sorted(bundle.aggregate.items()):
        if metric.categorical_counts is not None:
            value = ",".join(f"{key}={count}" for key, count in sorted(metric.categorical_counts.items()))
        else:
            value = "unavailable" if metric.value is None else f"{metric.value:.6g}"
        reason = f" reason={metric.reason}" if metric.reason else ""
        lines.append(f"{metric_id}: {value} status={metric.status.value} "
                     f"coverage={metric.coverage:.3f} protocol={metric.protocol_id}@{metric.protocol_version}{reason}")
    attribution_summary = (bundle.provenance or {}).get("attribution_summary") or {}
    outcomes = attribution_summary.get("outcome_distribution") or {}
    bottlenecks = attribution_summary.get("failure_only_bottleneck_distribution") or {}
    if outcomes:
        lines.extend(["", "attribution outcomes protocol=project.rule-attribution@2"])
        lines.extend(f"{label}: {count}" for label, count in sorted(outcomes.items()))
        lines.append("failure-only bottlenecks")
        lines.extend(f"{label}: {count}" for label, count in sorted(bottlenecks.items()))
    component_counts: Dict[str, int] = {}
    for sample in bundle.samples:
        for observation in sample.component_observations:
            protocol = str(observation.get("protocol_id", "unknown"))
            component_counts[protocol] = component_counts.get(protocol, 0) + 1
    if component_counts:
        lines.extend(["", "component observations"])
        lines.extend(f"{protocol}: {count}" for protocol, count in sorted(component_counts.items()))
    return "\n".join(lines) + "\n"


# ─── Formatting helpers ──────────────────────────────────────────────

def _fmt_avg(value: Optional[float], width: int = 8, precision: int = 4) -> str:
    if not isinstance(value, (int, float)):
        return "—".rjust(width)
    return f"{value:.{precision}f}".rjust(width)


def _fmt_ratio(valid: int, total: int, width: int = 9) -> str:
    if not isinstance(valid, int) or not isinstance(total, int):
        return "—".rjust(width)
    return f"{valid}/{total}".rjust(width)


def _pass_label(generate_num: int) -> str:
    return f"pass@{generate_num}" if generate_num > 1 else "pass@1"


_DIVIDER = "=" * 72
_SUB_DIVIDER = "-" * 72


# ─── CF1 helpers ─────────────────────────────────────────────────────

def aggregate_cf1(scores: List[dict]) -> tuple[Optional[float], Dict[str, float]]:
    if not scores or not isinstance(scores[0], dict):
        return None, {}
    keys = list(scores[0].keys())
    avgs = {key: sum(row[key] for row in scores) / len(scores) for key in keys}
    overall = sum(avgs.values()) / len(avgs) if avgs else None
    return overall, avgs


CF1_COMP_LABELS = {
    "cf1_select": "SELECT", "cf1_where": "WHERE", "cf1_group": "GROUP",
    "cf1_order": "ORDER", "cf1_join": "JOIN", "cf1_iuen": "IUEN", "cf1_keywords": "KW",
}


# ─── Stage metric labels & schema dependency ────────────────────────

STAGE_METRIC_LABELS = {
    "execute_accuracy": "EX",
    "reduce_recall": "Reduce Recall",
    "reduce_precision": "Reduce Precision",
    "reduce_rate": "Reduce Rate",
    "parse_recall": "Parse Recall",
    "parse_precision": "Parse Precision",
    "parse_exact_matching": "Parse Exact",
}

SCHEMA_DEPENDENT_METRICS = {
    "reduce_recall", "reduce_precision",
    "parse_recall", "parse_precision", "parse_exact_matching",
}

ACTOR_TYPE_LABEL = {
    "ReduceTask": "Schema Reduce",
    "ParseTask": "Schema Parse / Link",
    "GenerateTask": "SQL Generate",
    "SelectTask": "SQL Select / Vote",
}

PLACEHOLDER_REASONS = {
    "gold_schemas_missing": "数据集无 gold_schemas 标注，无法计算",
    "reduce_rate_unavailable": "checkpoint 中 instance_schemas 或 db_size 不可用",
    "generate_num_1": "需 generate_num ≥ 2",
    "no_checkpoint": "checkpoint 不可用",
    "no_candidates": "n_candidates = 1，无多候选",
    "no_selector": "pipeline 中无 Selector 组件",
}


def _metric_note(metric: str, stats: dict) -> str:
    """Human-readable note for a stage metric row."""
    valid_num = stats.get("valid_num", 0)
    total_items = stats.get("total_items", 0)
    note = stats.get("note")
    if note and "No valid evaluation results found" in note:
        if metric in SCHEMA_DEPENDENT_METRICS:
            return PLACEHOLDER_REASONS["gold_schemas_missing"]
        if metric == "reduce_rate":
            return PLACEHOLDER_REASONS["reduce_rate_unavailable"]
    if note:
        return note
    if valid_num == 0:
        if metric in SCHEMA_DEPENDENT_METRICS:
            return PLACEHOLDER_REASONS["gold_schemas_missing"]
        if metric == "reduce_rate":
            return PLACEHOLDER_REASONS["reduce_rate_unavailable"]
    if isinstance(valid_num, int) and isinstance(total_items, int) and 0 < valid_num < total_items:
        return f"{total_items - valid_num} 条跳过"
    iters = stats.get("iterations")
    if iters and iters > 1:
        return f"{iters} iterations avg"
    return ""


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1: Configuration Snapshot
# ═══════════════════════════════════════════════════════════════════════

def print_config_snapshot(config: dict, identifier: str) -> None:
    """Print a concise snapshot of the run configuration."""
    print()
    print(_DIVIDER)
    print(f"  配置快照  |  {identifier}")
    print(_DIVIDER)

    llm = config.get("llm", {})
    print(f"  LLM Provider   : {llm.get('use', '—')}")
    print(f"  Model          : {llm.get('model_name', '—')}")
    print(f"  Temperature    : {llm.get('temperature', '—')}")
    print(f"  Top-P          : {llm.get('top_p', '—')}")
    print(f"  Max Tokens     : {llm.get('max_token', '—')}")
    print(f"  Context Window : {llm.get('context_window', '—')}")
    print(f"  Timeout        : {llm.get('time_out', '—')}s")

    ds = config.get("dataset", {})
    print(f"  Data Source    : {ds.get('data_source', '—')}")
    print(f"  Few-shot       : {ds.get('need_few_shot', False)}")

    db = config.get("database", {})
    print(f"  Schema Source  : {db.get('schema_source', '—')}")
    print(f"  Multi-DB       : {db.get('multi_database', False)}")

    print(f"  Generate Num   : {config.get('generate_num', 1)}")

    ckpt = config.get("checkpoint", {})
    if ckpt:
        print(f"  Checkpoint     : {'enabled' if ckpt.get('enabled', True) else 'disabled'}"
              f"  interval={ckpt.get('interval', '—')}")
    print()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2: Workflow Overview
# ═══════════════════════════════════════════════════════════════════════

def print_workflow(config: dict) -> None:
    """Print the execution pipeline and per-actor configurations."""
    task_meta = config.get("task", {}).get("task_meta", [])
    cpx_meta = config.get("task", {}).get("cpx_task_meta", [])
    exec_process = config.get("engine", {}).get("exec_process", [])

    print("  执行流程 (exec_process):")
    for pid in exec_process:
        print(f"    → {pid}")

    if cpx_meta:
        print()
        print("  复合任务展开:")
        for cpx in cpx_meta:
            stages = " → ".join(cpx.get("task_lis", []))
            print(f"    [{cpx['task_id']}] {stages}")

    print()
    print("  组件清单:")
    print(f"    {'Task ID':<20} {'Type':<16} {'Actor':<24} {'关键参数'}")
    print(f"    {'─' * 20} {'─' * 16} {'─' * 24} {'─' * 30}")
    for task in task_meta:
        task_id = task.get("task_id", "?")
        task_type = task.get("task_type", "?")
        actor_meta = task.get("meta", {})
        task_cfg = actor_meta.get("task", {})
        actor_cfg = actor_meta.get("actor", {})

        actor_class = (task_cfg.get("reduce_type") or task_cfg.get("parse_type")
                       or task_cfg.get("generate_type") or task_cfg.get("select_type") or "—")

        params = []
        for k in ("n_candidates", "sc_num", "top_k", "topk_table_num", "topk_column_num",
                   "use_cot", "add_fk", "select_number", "max_attempt_times"):
            if k in actor_cfg:
                params.append(f"{k}={actor_cfg[k]}")
        param_str = ", ".join(params) if params else "—"
        print(f"    {task_id:<20} {task_type:<16} {actor_class:<24} {param_str}")
    print()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3: Report Header
# ═══════════════════════════════════════════════════════════════════════

def print_report_header(
        identifier: str,
        generate_num: int,
        sample_total: int,
        *,
        pass_label: Optional[str] = None,
) -> None:
    k_label = pass_label or _pass_label(generate_num)
    print()
    print(_DIVIDER)
    print(f"  评估结果  {identifier}")
    print(f"  样本 {sample_total} 条  |  {k_label}  |  generate_num={generate_num}")
    print(_DIVIDER)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4: Per-Stage / Per-Actor Metrics
# ═══════════════════════════════════════════════════════════════════════

def print_stage_eval_report(
        stage_results: Dict[str, dict],
        config: Optional[dict] = None,
) -> None:
    """Print per-actor / per-stage metrics with placeholders for unavailable ones."""
    if not stage_results and not config:
        return

    print()
    print("  阶段指标 (Per-Actor Metrics):")
    print(_SUB_DIVIDER)

    task_meta = (config or {}).get("task", {}).get("task_meta", [])
    task_by_id = {t["task_id"]: t for t in task_meta}

    reported_tasks = set()
    for task_id, payload in (stage_results or {}).items():
        reported_tasks.add(task_id)
        _print_stage_block(task_id, payload, task_by_id.get(task_id))

    # Show placeholders for configured stages with no results
    for task in task_meta:
        tid = task["task_id"]
        if tid in reported_tasks:
            continue
        eval_types = task.get("eval_type", [])
        if not eval_types:
            continue
        task_type = task.get("task_type", "")
        actor_label = ACTOR_TYPE_LABEL.get(task_type, task_type)
        print(f"  [{tid}]  {actor_label}")
        for metric in eval_types:
            label = STAGE_METRIC_LABELS.get(metric, metric)
            reason = PLACEHOLDER_REASONS.get("no_checkpoint", "checkpoint 不可用")
            print(f"    {label:<20}      —      —  ({reason})")
        print()


def _print_stage_block(task_id: str, payload: dict, task_config: Optional[dict]) -> None:
    task_type = (task_config or {}).get("task_type", payload.get("task_type", ""))
    actor_label = ACTOR_TYPE_LABEL.get(task_type, task_type)
    actor_meta = (task_config or {}).get("meta", {})
    actor_cfg = actor_meta.get("actor", {})

    # Actor class name
    task_cfg = actor_meta.get("task", {})
    actor_class = (task_cfg.get("reduce_type") or task_cfg.get("parse_type")
                   or task_cfg.get("generate_type") or task_cfg.get("select_type") or "")

    header = f"  [{task_id}]  {actor_label}"
    if actor_class:
        header += f"  ({actor_class})"
    print(header)

    if actor_cfg:
        params = ", ".join(f"{k}={v}" for k, v in actor_cfg.items()
                           if k not in ("save_dir", "is_save"))
        if params:
            print(f"    参数: {params}")

    metrics = payload.get("metrics") or {}
    if not metrics:
        print("    (无可用指标)")
    else:
        print(f"    {'指标':<20} {'平均分':>8}  {'有效/总数':>9}  {'说明'}")
        for metric, stats in metrics.items():
            label = STAGE_METRIC_LABELS.get(metric, metric)
            valid_num = stats.get("valid_num", 0)
            total_items = stats.get("total_items", 0)
            avg = stats.get("avg")
            note = _metric_note(metric, stats)
            print(f"    {label:<20} {_fmt_avg(avg)}  {_fmt_ratio(valid_num, total_items)}  {note}")

    # Show configured but unreported eval_types as placeholders
    configured_evals = set((task_config or {}).get("eval_type", []))
    reported_evals = set(metrics.keys())
    missing = configured_evals - reported_evals
    for metric in sorted(missing):
        label = STAGE_METRIC_LABELS.get(metric, metric)
        if metric in SCHEMA_DEPENDENT_METRICS:
            reason = PLACEHOLDER_REASONS["gold_schemas_missing"]
        else:
            reason = PLACEHOLDER_REASONS["no_checkpoint"]
        print(f"    {label:<20}      —      —  {reason}")

    # Timing info
    timing = payload.get("timing") or {}
    if timing.get("available"):
        print(f"    耗时: 均值 {timing.get('mean_s', 0):.2f}s"
              f"  最大 {timing.get('max_s', 0):.2f}s"
              f"  最小 {timing.get('min_s', 0):.2f}s"
              f"  ({timing.get('sample_count', 0)} 条)")
    print()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5: Final Evaluation Metrics
# ═══════════════════════════════════════════════════════════════════════

def print_eval_report(
        ex_result: dict,
        custom_results: Dict[str, dict],
        generate_num: int,
) -> None:
    rows: List[tuple[str, Optional[float], int, int, str]] = []

    rows.append((
        f"EX ({_pass_label(generate_num)})",
        ex_result.get("avg"),
        ex_result.get("valid", 0),
        ex_result.get("total", 0),
        "执行结果与 gold 一致",
    ))

    metric_specs = [
        ("EM", "em", "SQL 7 组件完全匹配"),
        ("SF1", "sf1", "结果集软匹配 F1"),
        ("VES", "ves", "有效执行效率"),
    ]
    for label, key, note in metric_specs:
        result = custom_results.get(key, {})
        rows.append((label, result.get("avg"), result.get("valid", 0), result.get("total", 0), note))

    # SC: placeholder if generate_num < 2
    sc_result = custom_results.get("sc", {})
    if generate_num >= 2:
        rows.append(("SC", sc_result.get("avg"), sc_result.get("valid", 0), sc_result.get("total", 0),
                      "多轮执行结果自洽"))
    else:
        rows.append(("SC", None, 0, 0, PLACEHOLDER_REASONS["generate_num_1"]))

    # CF1
    cf1_result = custom_results.get("cf1", {})
    cf1_scores = cf1_result.get("scores") or []
    cf1_avg, cf1_components = aggregate_cf1(cf1_scores)
    rows.append((
        "CF1 (mean)", cf1_avg, cf1_result.get("valid", 0), cf1_result.get("total", 0),
        "7 个 SQL 组件 F1 均值",
    ))

    # FD
    fd_result = custom_results.get("fd", {})
    if fd_result:
        rows.append(("FD", fd_result.get("avg"), fd_result.get("valid", 0), fd_result.get("total", 0),
                      "特征偏差距离"))

    print()
    print("  最终指标:")
    print(f"    {'指标':<16} {'平均分':>8}  {'有效/总数':>9}  说明")
    print(f"    {_SUB_DIVIDER}")
    for name, avg, valid, total, note in rows:
        print(f"    {name:<16} {_fmt_avg(avg)}  {_fmt_ratio(valid, total)}  {note}")

    if cf1_components:
        print()
        print("    CF1 组件明细:")
        ordered = [k for k in CF1_COMP_LABELS if k in cf1_components]
        header = "    " + "  ".join(f"{CF1_COMP_LABELS[k]:>7}" for k in ordered)
        values = "    " + "  ".join(f"{cf1_components[k]:7.3f}" for k in ordered)
        print(header)
        print(values)

    errors = _collect_errors(custom_results)
    if errors:
        print()
        print(f"    评估告警 ({len(errors)} 条):")
        for msg in errors[:5]:
            print(f"      ⚠ {msg}")
        if len(errors) > 5:
            print(f"      ... 另有 {len(errors) - 5} 条")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6: Intermediate Data Traceability
# ═══════════════════════════════════════════════════════════════════════

def print_intermediate_data_paths(config: dict, save_lis: List[str] = None) -> None:
    """Print all intermediate data storage paths for traceability."""
    print()
    print("  中间数据路径 (Intermediate Data Paths):")
    print(_SUB_DIVIDER)

    task_meta = config.get("task", {}).get("task_meta", [])
    for task in task_meta:
        task_id = task.get("task_id", "?")
        ds_path = task.get("dataset_save_path", "—")
        actor_cfg = task.get("meta", {}).get("actor", {})
        save_dir = actor_cfg.get("save_dir", "—")

        print(f"    [{task_id}]")
        print(f"      dataset_checkpoint : {ds_path}")
        if save_dir != "—":
            print(f"      actor_save_dir     : {save_dir}")

    ds_dir = config.get("dataset_save_dir", "—")
    sql_dir = config.get("sql_save_dir", "—")
    print("    [global]")
    print(f"      dataset_save_dir   : {ds_dir}")
    print(f"      sql_save_dir       : {sql_dir}")

    if save_lis:
        print("    [final output]")
        for i, path in enumerate(save_lis):
            print(f"      iteration {i + 1}       : {path}")
    print()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7: Token Usage
# ═══════════════════════════════════════════════════════════════════════

def print_token_summary(token_data: Optional[dict], *, full: bool = False) -> None:
    if not token_data:
        return
    total = token_data.get("total") or {}
    if not total.get("calls"):
        print()
        print("  Token 统计: 0 次调用 (流式模式可能未启用 include_usage)")
        return

    print()
    print("  Token 统计:")
    print(_SUB_DIVIDER)
    print(f"    总调用次数       : {total.get('calls', 0)}")
    print(f"    Prompt Tokens    : {total.get('prompt_tokens', 0)}")
    print(f"    Completion Tokens: {total.get('completion_tokens', 0)}")
    print(f"    Total Tokens     : {total.get('total_tokens', 0)}")

    if not full:
        by_tag = token_data.get("by_tag") or {}
        if by_tag:
            print("    (逐样本明细已写入 scores / detailed-report，终端不展开)")
        return

    by_tag = token_data.get("by_tag") or {}
    if by_tag:
        by_step: dict[str, dict] = {}
        for tag, stats in by_tag.items():
            step = tag.split("|")[-1] if tag else "unknown"
            cur = by_step.setdefault(step, {"calls": 0, "total_tokens": 0})
            cur["calls"] += stats.get("calls", 0)
            cur["total_tokens"] += stats.get("total_tokens", 0)

        print()
        print(f"    {'阶段/样本':<24} {'调用':>6} {'Token总量':>10} {'均值/调用':>10}")
        for step, stats in sorted(by_step.items(), key=lambda x: -x[1]["total_tokens"]):
            avg = stats["total_tokens"] / stats["calls"] if stats["calls"] else 0
            print(f"    {step:<24} {stats['calls']:>6} {stats['total_tokens']:>10} {avg:>10.0f}")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8: Hardness Breakdown & Error Analysis
# ═══════════════════════════════════════════════════════════════════════

def print_hardness_breakdown(scores: dict) -> None:
    by_hardness = scores.get("by_hardness") or {}
    if not by_hardness:
        return

    print()
    print("  难度分层:")
    print(_SUB_DIVIDER)
    print(f"    {'难度':<10} {'EX':>8} {'EM':>8} {'样本数':>6}")
    for hardness in ("easy", "medium", "hard", "extra"):
        stats = by_hardness.get(hardness, {})
        ex = _fmt_avg(stats.get("ex"))
        em = _fmt_avg(stats.get("em"))
        count = stats.get("count", 0)
        print(f"    {hardness:<10} {ex} {em} {count:>6}")


def print_error_analysis(scores: dict) -> None:
    aggregate = scores.get("aggregate") or {}
    error_dist = aggregate.get("error_root_distribution") or {}
    if not error_dist:
        return

    print()
    print("  错误根因分析:")
    print(_SUB_DIVIDER)
    ranked = sorted(error_dist.items(), key=lambda item: item[1].get("count", 0), reverse=True)
    for root, stats in ranked[:10]:
        count = stats.get("count", 0)
        pct = stats.get("pct", 0)
        print(f"    {root:<30} {count:>4} 条  ({pct:.1%})")


def print_workflow_attribution(scores: dict) -> None:
    workflow = scores.get("workflow_trace") or {}
    aggregate = workflow.get("aggregate") or {}
    bottlenecks = aggregate.get("bottleneck_distribution") or {}
    if not bottlenecks:
        return

    print()
    print("  Workflow 归因:")
    print(_SUB_DIVIDER)
    total = sum(count for count in bottlenecks.values() if isinstance(count, int))
    for stage, count in sorted(bottlenecks.items(), key=lambda item: item[1], reverse=True):
        pct = count / total if total else 0
        print(f"    {stage:<30} {count:>4} 条  ({pct:.1%})")

    stage_summary = aggregate.get("stage_summary") or {}
    if stage_summary:
        print()
        print(f"    {'Stage':<24} {'Actor':<24} {'Pass':>6} {'Fail':>6} {'Observed':>8}")
        for task_id, payload in stage_summary.items():
            counts = payload.get("status_counts") or {}
            print(
                f"    {task_id:<24} {str(payload.get('actor_class') or '—'):<24} "
                f"{counts.get('pass', 0):>6} {counts.get('fail', 0):>6} "
                f"{counts.get('observed', 0):>8}"
            )


def print_sql_feature_slices(scores: dict) -> None:
    slices = scores.get("by_sql_feature") or {}
    qvt = scores.get("qvt") or {}
    if not slices and not qvt:
        return
    print()
    print("  SQL 特征切片:")
    print(_SUB_DIVIDER)
    ranked = sorted(
        ((name, payload) for name, payload in slices.items() if payload.get("count")),
        key=lambda item: item[1].get("count", 0),
        reverse=True,
    )
    print(f"    {'Feature':<22} {'Count':>6} {'EX':>8} {'EM':>8}")
    for name, payload in ranked[:8]:
        print(
            f"    {name:<22} {payload.get('count', 0):>6} "
            f"{_fmt_avg(payload.get('ex'))} {_fmt_avg(payload.get('em'))}"
        )
    if qvt.get("eligible_groups"):
        print()
        print(
            "    QVT: "
            f"groups={qvt.get('eligible_groups')} "
            f"flip_rate={_fmt_avg(qvt.get('flip_rate'), width=0)} "
            f"stable={_fmt_avg(qvt.get('stable_group_rate'), width=0)}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Full Report Entry Points
# ═══════════════════════════════════════════════════════════════════════

def print_report_footer(
        *,
        scores_path: Optional[str] = None,
        detailed_report_path: Optional[str] = None,
) -> None:
    print()
    print(_DIVIDER)
    if scores_path or detailed_report_path:
        print("  逐样本明细已落盘:")
        if scores_path:
            print(f"    scores.json         : {scores_path}")
        if detailed_report_path:
            print(f"    detailed-report.txt : {detailed_report_path}")
    print("  报告完成")
    print(_DIVIDER)
    print()


def print_full_report(
        *,
        identifier: str,
        config: dict,
        generate_num: int,
        sample_total: int,
        ex_result: dict,
        custom_results: Dict[str, dict],
        stage_results: Dict[str, dict],
        save_lis: List[str] = None,
        scores: Optional[dict] = None,
        token_data: Optional[dict] = None,
        mode: str = "summary",
        scores_path: Optional[str] = None,
        detailed_report_path: Optional[str] = None,
) -> None:
    """Print evaluation report.

    mode=summary (default): config/workflow + aggregate metrics for terminal.
    mode=full: also includes intermediate paths and per-tag token breakdown.
    """
    full = mode == "full"

    print_config_snapshot(config, identifier)
    print_workflow(config)

    print_report_header(identifier, generate_num, sample_total)
    print_stage_eval_report(stage_results, config=config if full else None)
    print_eval_report(ex_result, custom_results, generate_num)

    if full:
        print_intermediate_data_paths(config, save_lis)

    print_token_summary(token_data, full=full)

    if scores:
        print_workflow_attribution(scores)
        print_sql_feature_slices(scores)
        print_hardness_breakdown(scores)
        print_error_analysis(scores)

    print_report_footer(
        scores_path=scores_path if not full else None,
        detailed_report_path=detailed_report_path if not full else None,
    )


def capture_full_report(**kwargs) -> str:
    """Capture the full report as a string (for persistence)."""
    buf = io.StringIO()
    import contextlib
    payload = dict(kwargs)
    payload["mode"] = "full"
    with contextlib.redirect_stdout(buf):
        print_full_report(**payload)
    return buf.getvalue()


# ─── Helpers ─────────────────────────────────────────────────────────

def _collect_errors(custom_results: Dict[str, dict]) -> List[str]:
    errors: List[str] = []
    for result in custom_results.values():
        for msg in result.get("errors") or []:
            if msg not in errors:
                errors.append(msg)
    return errors
