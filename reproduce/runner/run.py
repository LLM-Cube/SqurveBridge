import sys
import os
import json
import copy
import re
from datetime import datetime
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from loguru import logger

from core.engine import Engine
from core.task.base import CheckpointState
from core.utils import load_dataset
from core.llm.token_logger import collect_all_token_data
from reproduce.eval.report import (
    print_full_report,
    capture_full_report,
    print_eval_report,
    print_report_header,
    print_stage_eval_report,
)
from reproduce.eval.stage_eval import evaluate_stages_from_config
from reproduce.eval.utils import (
    load_router,
    evaluate,
    evaluate_with_details,
    evaluate_custom,
    evaluate_custom_with_details,
    _load_dataset_from_engine,
    resolve_saved_dataset_path,
)
from reproduce.metrics.assembly import build_scores
from reproduce.metrics.diagnostics import extract_unified_log_diagnostics
from reproduce.eval.bundle.compare import compare_scores
from reproduce.evolve.meta_input import build_meta_evo_input
from reproduce.lib.env_config import api_key_ready, prepare_runtime_llm_config, redact_config_secrets
from reproduce.lib.checkpoints import (
    checkpoint_run_id,
    resolve_checkpoint_state_path,
    select_resume_checkpoint,
)
from reproduce.lib.stage_overrides import apply_stage_overrides, stage_overrides_from_env
from reproduce.lib.paths import REPRODUCE_ROOT, config_filename, config_repo_path, run_identifier
from reproduce.metrics.persistence import persist_scores_bundle
from demo.workspace import (
    artifacts_dir,
    eval_store_path,
    run_dir as workspace_run_dir,
)


def main(dataset_name, method, resume=False, resume_from=None):
    identifier = run_identifier(dataset_name, method)
    resume_checkpoint = select_resume_checkpoint(identifier, resume_from) if (resume or resume_from) else None
    run_id = checkpoint_run_id(resume_checkpoint) if resume_checkpoint else _run_id(identifier)
    config_path = config_filename(dataset_name, method)
    eval_mode = os.environ.get("SQURVE_EVAL_MODE", "full").lower()

    original_cwd = os.getcwd()
    os.chdir(REPRODUCE_ROOT)
    try:
        stage_overrides = stage_overrides_from_env()
        config_dict = apply_stage_overrides(prepare_runtime_llm_config(load_dataset(config_path)), stage_overrides)
        ready, ready_message = api_key_ready(config_dict)
        if not ready:
            raise SystemExit(f"LLM provider is not ready for evaluation: {ready_message}")
        config = isolate_files_config(config_dict, run_id)
        _apply_eval_sample_limit(config)
        runtime_config_path = _write_runtime_config(config, run_id)
        generate_num = config.get("generate_num", 1)
        checkpoint_config = _build_checkpoint_config(
            dataset_name=dataset_name,
            method=method,
            identifier=identifier,
            run_id=run_id,
            config=config,
            generate_num=generate_num,
            resume=resume,
            resume_from=str(resume_checkpoint) if resume_checkpoint else None,
        )

        router, save_lis = load_router(identifier=identifier, config=config)
        if checkpoint_config is not None:
            _attach_checkpoint_states(
                checkpoint_config=checkpoint_config,
                router=router,
                generate_num=generate_num,
                sample_total=0,
                resume=resume or bool(resume_from),
            )

        engine = Engine(router, checkpoint_config=checkpoint_config)
        _fill_checkpoint_sample_totals(checkpoint_config, engine)

        print("执行自定义任务中...")
        engine.execute()

        print("\n正在评估...")
        details_available = eval_mode != "minimal"
        with _suppress_eval_warnings():
            stage_results = evaluate_stages_from_config(
                config,
                generate_num=generate_num,
                quiet=True,
            )
            if eval_mode == "minimal":
                ex_result = evaluate(save_lis, config=config)
                custom_results = _run_custom_metrics(save_lis, config=config)
            else:
                try:
                    ex_result = evaluate_with_details(save_lis, quiet=True, config=config)
                    custom_results = _run_custom_metrics_with_details(save_lis, quiet=True, config=config)
                except Exception as exc:
                    details_available = False
                    print(f"全量评估不可用，退回 minimal 模式: {exc}")
                    ex_result = evaluate(save_lis, config=config)
                    custom_results = _run_custom_metrics(save_lis, config=config)

        sample_total = ex_result.get("total") or 0
        token_data = {} if _env_true("SQURVE_EVAL_SKIP_TOKEN") else collect_all_token_data()

        if eval_mode != "minimal" and details_available:
            scores_path, scores = _persist_scores(
                identifier=identifier,
                run_id=run_id,
                dataset_name=dataset_name,
                method=method,
                config_path=str(runtime_config_path),
                config=config,
                generate_num=generate_num,
                save_lis=save_lis,
                ex_result=ex_result,
                custom_results=custom_results,
                stage_results=stage_results,
                token_data=token_data,
            )
        else:
            scores_path, scores = None, None

        if eval_mode != "scores_only":
            detailed_report_path = (
                str(scores_path.parent / "detailed-report.txt") if scores_path else None
            )
            print_full_report(
                identifier=identifier,
                config=config,
                generate_num=generate_num,
                sample_total=sample_total,
                ex_result=ex_result,
                custom_results=custom_results,
                stage_results=stage_results,
                save_lis=save_lis,
                scores=scores,
                token_data=token_data,
                mode="summary",
                scores_path=str(scores_path) if scores_path else None,
                detailed_report_path=detailed_report_path,
            )

    finally:
        os.chdir(original_cwd)


def isolate_files_config(config: dict, run_id: str) -> dict:
    """Return a config copy whose file outputs/cache live under workspace/runs/<run_id>."""
    isolated = copy.deepcopy(config)
    isolated = _isolate_files_value(isolated, run_id)
    _apply_isolated_file_defaults(isolated, run_id)
    return isolated


def _isolate_files_value(value, run_id: str):
    if isinstance(value, dict):
        return {k: _isolate_files_value(v, run_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_isolate_files_value(item, run_id) for item in value]
    if isinstance(value, str) and _is_file_cache_path(value):
        return _rewrite_files_path(value, run_id)
    return value


def _is_file_cache_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (
        normalized == "../files"
        or normalized.startswith("../files/")
        or normalized == "files"
        or normalized.startswith("files/")
        or normalized == "../workspace/runs"
        or normalized.startswith("../workspace/runs/")
        or "/workspace/runs/" in normalized
    )


def _run_relative_path(run_id: str, *parts: str, trailing_slash: bool = False) -> str:
    """Absolute path under workspace/runs/<run_id>/... (safe when workspace is outside the repo)."""
    path = workspace_run_dir(run_id).joinpath(*parts)
    text = path.as_posix()
    if trailing_slash and not text.endswith("/"):
        text = f"{text}/"
    return text


def _rewrite_files_path(value: str, run_id: str) -> str:
    normalized = value.replace("\\", "/")
    had_trailing_slash = normalized.endswith("/")
    if normalized == "../files":
        suffix = ""
    elif normalized.startswith("../files/"):
        suffix = normalized[len("../files/"):]
    elif normalized == "files":
        suffix = ""
    elif normalized.startswith("files/"):
        suffix = normalized[len("files/"):]
    elif "/workspace/runs/" in normalized:
        # Already isolated under a run; keep only the trailing suffix after run id.
        after = normalized.split("/workspace/runs/", 1)[1]
        parts = after.split("/", 1)
        suffix = parts[1] if len(parts) > 1 else ""
    else:
        suffix = ""

    rewritten = _run_relative_path(run_id)
    if suffix:
        rewritten = f"{rewritten}/{suffix}"
    if had_trailing_slash and not rewritten.endswith("/"):
        rewritten = f"{rewritten}/"
    return rewritten


def _apply_isolated_file_defaults(config: dict, run_id: str) -> None:
    config.setdefault("dataset_save_dir", _run_relative_path(run_id, "datasets", trailing_slash=True))
    config.setdefault("sql_save_dir", _run_relative_path(run_id, "pred_sql", trailing_slash=True))

    config.setdefault("dataset", {})
    config["dataset"].setdefault("data_source_dir", _run_relative_path(run_id, "data_source"))
    config["dataset"].setdefault("few_shot_save_dir", _run_relative_path(run_id, "reasoning_examples", "user"))
    config["dataset"].setdefault("external_save_dir", _run_relative_path(run_id, "external"))

    config.setdefault("database", {})
    config["database"].setdefault("schema_source_dir", _run_relative_path(run_id, "schema_source"))

    config.setdefault("reducer", {})
    config["reducer"].setdefault("reduce_save_dir", _run_relative_path(run_id, "instance_schemas"))

    config.setdefault("parser", {})
    config["parser"].setdefault("parse_save_dir", _run_relative_path(run_id, "schema_links"))

    config.setdefault("generator", {})
    config["generator"].setdefault("generate_save_dir", _run_relative_path(run_id, "pred_sql"))

    config.setdefault("task", {})
    config["task"].setdefault("default_log_save_dir", _run_relative_path(run_id, "log"))


def _write_runtime_config(config: dict, run_id: str) -> Path:
    path = workspace_run_dir(run_id) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact_config_secrets(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _apply_eval_sample_limit(config: dict) -> None:
    raw_limit = os.environ.get("SQURVE_EVAL_SAMPLE_LIMIT")
    if not raw_limit:
        return
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise ValueError(f"SQURVE_EVAL_SAMPLE_LIMIT must be an integer: {raw_limit}") from exc
    if limit <= 0:
        return
    sample_mode = os.environ.get("SQURVE_EVAL_SAMPLE_MODE", "slice").lower()
    if sample_mode not in {"slice", "random"}:
        raise ValueError(f"SQURVE_EVAL_SAMPLE_MODE must be slice or random: {sample_mode}")
    raw_seed = os.environ.get("SQURVE_EVAL_SAMPLE_SEED", "42")
    try:
        sample_seed = int(raw_seed)
    except ValueError as exc:
        raise ValueError(f"SQURVE_EVAL_SAMPLE_SEED must be an integer: {raw_seed}") from exc

    def limit_identifier(identifier: str) -> str:
        parts = identifier.split(":")
        if len(parts) != 3:
            return identifier
        existing = parts[2]
        if sample_mode == "random":
            filters = [] if not existing or existing.isdigit() else existing.split(".")
            filters = [
                item for item in filters
                if not item.startswith(("limit-", "random-", "seed-"))
            ]
            filters.extend([f"random-{limit}", f"seed-{sample_seed}"])
            parts[2] = ".".join(filters)
        elif not existing or existing.isdigit():
            # A demo/session sampling request is authoritative. Configs may keep
            # smaller defaults for ad-hoc smoke runs, but those must not make a
            # cross-method comparison silently use different sample counts.
            parts[2] = str(limit)
        else:
            filters = [
                item for item in existing.split(".")
                if not item.startswith(("limit-", "random-", "seed-"))
            ]
            updated = []
            replaced = False
            for item in filters:
                if item.startswith("limit-"):
                    updated.append(f"limit-{limit}")
                    replaced = True
                else:
                    updated.append(item)
            if not replaced:
                updated.append(f"limit-{limit}")
            parts[2] = ".".join(updated)
        return ":".join(parts)

    dataset = config.get("dataset")
    if isinstance(dataset, dict) and isinstance(dataset.get("data_source"), str):
        dataset["data_source"] = limit_identifier(dataset["data_source"])

    task = config.get("task")
    for task_meta in (task or {}).get("task_meta") or []:
        if isinstance(task_meta, dict) and isinstance(task_meta.get("data_source"), str):
            task_meta["data_source"] = limit_identifier(task_meta["data_source"])


def _build_checkpoint_config(
        *,
        dataset_name: str,
        method: str,
        identifier: str,
        run_id: str,
        config: dict,
        generate_num: int,
        resume: bool,
        resume_from: str | None,
) -> dict | None:
    checkpoint_raw = config.get("checkpoint", {})
    if "checkpoint" not in config and not resume and not resume_from:
        return None
    if not checkpoint_raw.get("enabled", True):
        return None

    ckpt_dir = workspace_run_dir(run_id) / "checkpoints"
    if not resume and not resume_from and ckpt_dir.exists():
        import shutil
        shutil.rmtree(ckpt_dir)

    checkpoint = dict(checkpoint_raw)
    checkpoint["run_id"] = identifier
    checkpoint["checkpoint_dir"] = str(ckpt_dir)
    checkpoint["datasets_dir"] = str(ckpt_dir / "datasets")
    checkpoint["dataset_name"] = dataset_name
    checkpoint["method"] = method
    checkpoint["generate_num"] = generate_num
    if resume_from:
        checkpoint["resume_from"] = resume_from
    return checkpoint


def _attach_checkpoint_states(
        *,
        checkpoint_config: dict,
        router,
        generate_num: int,
        sample_total: int,
        resume: bool,
) -> None:
    states_by_task_id = {}
    state_paths_by_task_id = {}
    run_id = checkpoint_config["run_id"]
    now = datetime.now().strftime("%Y%m%d-%H%M%S")

    tasks_by_id = {task["task_id"]: task for task in (router.task_meta or [])}
    complex_by_id = {task["task_id"]: task for task in (router.cpx_task_meta or [])}
    process_ids = [task_id for task_id in (router.exec_process or []) if task_id != "~p"]

    for iteration in range(1, generate_num + 1):
        state_path = resolve_checkpoint_state_path(
            checkpoint_config["checkpoint_dir"],
            checkpoint_config.get("resume_from"),
            iteration,
        )
        stage_ids = _stage_ids_for_iteration(iteration, process_ids, tasks_by_id, complex_by_id)
        if resume:
            state = CheckpointState.load(state_path)
            if state is None:
                print(f"错误: checkpoint state 不存在或不可读取: {state_path}")
                sys.exit(1)
            print(f"从 checkpoint 恢复: {state.run_id} ({state_path})")
        else:
            state = CheckpointState.create(
                run_id=f"{run_id}-{now}-{iteration}",
                stages=stage_ids,
                sample_total=sample_total,
                ckpt_dir=checkpoint_config["checkpoint_dir"],
            )
            state.save(state_path)

        mapped_task_ids = set(stage_ids)
        process_id = _process_id_for_iteration(iteration, process_ids)
        if process_id:
            mapped_task_ids.add(process_id)
        for task_id in mapped_task_ids:
            _append_checkpoint_mapping(states_by_task_id, task_id, state)
            _append_checkpoint_mapping(state_paths_by_task_id, task_id, str(state_path))

    checkpoint_config["states_by_task_id"] = states_by_task_id
    checkpoint_config["state_paths_by_task_id"] = state_paths_by_task_id


def _append_checkpoint_mapping(mapping: dict, task_id: str, value) -> None:
    if task_id not in mapping:
        mapping[task_id] = value
        return
    current = mapping[task_id]
    if isinstance(current, list):
        current.append(value)
    else:
        mapping[task_id] = [current, value]


def _stage_ids_for_iteration(iteration: int, process_ids: list[str], tasks_by_id: dict, complex_by_id: dict) -> list[str]:
    process_id = _process_id_for_iteration(iteration, process_ids)
    if process_id in complex_by_id:
        return _flatten_task_lis(complex_by_id[process_id].get("task_lis", []))
    if process_id:
        return [process_id]
    suffix = str(iteration)
    return [task_id for task_id in tasks_by_id if task_id.endswith(suffix)]


def _process_id_for_iteration(iteration: int, process_ids: list[str]) -> str | None:
    suffix = str(iteration)
    for task_id in process_ids:
        if task_id.endswith(suffix):
            return task_id
    return process_ids[iteration - 1] if iteration - 1 < len(process_ids) else None


def _flatten_task_lis(task_lis) -> list[str]:
    flattened = []
    for item in task_lis or []:
        if isinstance(item, str):
            flattened.append(item)
        elif isinstance(item, list):
            flattened.extend(_flatten_task_lis(item))
    return flattened


def _fill_checkpoint_sample_totals(checkpoint_config: dict | None, engine: Engine) -> None:
    if checkpoint_config is None:
        return
    states = checkpoint_config.get("states_by_task_id") or {}
    paths = checkpoint_config.get("state_paths_by_task_id") or {}
    seen = set()
    for task_id, state, state_path in _iter_checkpoint_state_entries(states, paths):
        if id(state) in seen or state.sample_total:
            continue
        task = engine.tasks.get(task_id)
        if task is None or not getattr(task, "dataset", None):
            continue
        state.sample_total = len(task.dataset)
        if state_path:
            state.save(state_path)
        seen.add(id(state))


def _iter_checkpoint_state_entries(states: dict, paths: dict):
    for task_id, value in states.items():
        path_value = paths.get(task_id)
        if isinstance(value, list):
            path_list = path_value if isinstance(path_value, list) else [path_value] * len(value)
            for state, state_path in zip(value, path_list):
                yield task_id, state, state_path
        else:
            yield task_id, value, path_value


def _run_custom_metrics(save_lis, config_path=None, quiet=False, config=None):
    """运行 reproduce/metrics/ 中定义的自定义评估指标。"""
    metrics = _load_custom_metrics()
    if metrics is None:
        print("自定义指标不可用 — uv sync 后重试")
        return {}
    eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1 = metrics

    return {
        "em": evaluate_custom(save_lis, config_path, eval_em, quiet=quiet, config=config),
        "sf1": evaluate_custom(save_lis, config_path, eval_sf1, quiet=quiet, config=config),
        "sc": evaluate_custom(save_lis, config_path, eval_sc, quiet=quiet, config=config),
        "ves": evaluate_custom(
            save_lis, config_path, eval_ves, ves_iterations=5, quiet=quiet, config=config
        ),
        "rves": evaluate_custom(
            save_lis, config_path, eval_rves, ves_iterations=5, quiet=quiet, config=config
        ),
        "cf1": evaluate_custom(save_lis, config_path, eval_cf1, quiet=quiet, config=config),
    }


def _run_custom_metrics_with_details(save_lis, config_path=None, quiet=False, config=None):
    metrics = _load_custom_metrics_with_fd()
    if metrics is None:
        print("自定义指标不可用 — uv sync 后重试")
        return {}
    eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1, eval_fd = metrics

    return {
        "em": evaluate_custom_with_details(save_lis, config_path, eval_em, quiet=quiet, config=config),
        "sf1": evaluate_custom_with_details(save_lis, config_path, eval_sf1, quiet=quiet, config=config),
        "sc": evaluate_custom_with_details(save_lis, config_path, eval_sc, quiet=quiet, config=config),
        "ves": evaluate_custom_with_details(
            save_lis, config_path, eval_ves, ves_iterations=5, quiet=quiet, config=config
        ),
        "rves": evaluate_custom_with_details(
            save_lis, config_path, eval_rves, ves_iterations=5, quiet=quiet, config=config
        ),
        "cf1": evaluate_custom_with_details(save_lis, config_path, eval_cf1, quiet=quiet, config=config),
        "fd": evaluate_custom_with_details(save_lis, config_path, eval_fd, quiet=quiet, config=config),
    }


def _load_custom_metrics():
    """Load custom metrics, treating only sqlglot as an optional dependency."""
    try:
        from reproduce.metrics import eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1
    except ModuleNotFoundError as exc:
        if exc.name == "sqlglot":
            return None
        raise
    return eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1


def _load_custom_metrics_with_fd():
    try:
        from reproduce.metrics import eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1, eval_fd
    except ModuleNotFoundError as exc:
        if exc.name == "sqlglot":
            return None
        raise
    return eval_em, eval_sf1, eval_sc, eval_ves, eval_rves, eval_cf1, eval_fd


def _persist_scores(
        *,
        identifier,
        run_id,
        dataset_name,
        method,
        config_path,
        config,
        generate_num,
        save_lis,
        ex_result,
        custom_results,
        stage_results=None,
        token_data=None,
):
    output_dir = _scores_output_dir(run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_lists = [
        loaded
        for path in save_lis
        if isinstance((loaded := load_dataset(resolve_saved_dataset_path(path))), list)
    ]
    if token_data is None:
        token_data = {} if _env_true("SQURVE_EVAL_SKIP_TOKEN") else collect_all_token_data()
    base_dataset = None if _env_true("SQURVE_EVAL_SKIP_PIPELINE_DELTA") else _load_dataset_from_engine(config=config)
    actor_diagnostics = _collect_actor_diagnostics(save_lis)
    scores = build_scores(
        run_id=run_id,
        method=method,
        dataset_name=dataset_name,
        split=config.get("split", "dev"),
        generate_num=generate_num,
        config_path=str(config_repo_path(dataset_name, method)),
        data_lists=data_lists,
        ex_result=ex_result,
        custom_results=custom_results,
        token_data=token_data,
        base_dataset=base_dataset,
        actor_diagnostics=actor_diagnostics,
        stage_results=stage_results,
        scope=os.environ.get("SQURVE_EVAL_SCOPE", "full"),
        statistical_validity=_statistical_validity_for_scope(
            os.environ.get("SQURVE_EVAL_SCOPE", "full")
        ),
        config_snapshot=config,
    )
    if stage_results:
        scores["stage_metrics"] = stage_results
    stage_overrides = stage_overrides_from_env()
    if stage_overrides:
        scores["stage_overrides"] = {stage: item["actor"] for stage, item in stage_overrides.items()}
    persisted = persist_scores_bundle(
        output_dir=output_dir,
        scores=scores,
        token_data=token_data,
        config=config,
        eval_store_path=eval_store_path(),
    )
    scores_path = persisted["scores"]

    # Save detailed report text for offline review
    report_text = capture_full_report(
        identifier=identifier,
        config=config,
        generate_num=generate_num,
        sample_total=ex_result.get("total", 0),
        ex_result=ex_result,
        custom_results=custom_results,
        stage_results=stage_results or {},
        save_lis=save_lis,
        scores=scores,
        token_data=token_data,
    )
    (output_dir / "detailed-report.txt").write_text(report_text, encoding="utf-8")

    (output_dir / "meta-evo-input.json").write_text(
        json.dumps(build_meta_evo_input(scores), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    baseline_path = os.environ.get("SQURVE_EVAL_BASELINE_SCORES")
    if baseline_path:
        baseline_scores = load_dataset(baseline_path)
        if isinstance(baseline_scores, dict):
            (output_dir / "delta-report.json").write_text(
                json.dumps(compare_scores(baseline_scores, scores), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return scores_path, scores


def _statistical_validity_for_scope(scope: str) -> str:
    scope = (scope or "full").lower()
    if scope == "smoke":
        return "none"
    if scope == "full":
        return "full"
    return "bounded"


def _run_id(identifier):
    configured = os.environ.get("SQURVE_EVAL_RUN_ID")
    if configured:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", configured):
            raise ValueError("SQURVE_EVAL_RUN_ID contains unsupported characters")
        return configured
    return f"{identifier}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"


def _scores_output_dir(run_id):
    configured = os.environ.get("SQURVE_EVAL_OUTPUT_DIR")
    if configured:
        return Path(configured)
    return artifacts_dir() / run_id


def _env_true(name):
    return os.environ.get(name, "false").lower() in {"1", "true", "yes", "on"}


def _collect_actor_diagnostics(save_lis):
    paths = []
    configured = os.environ.get("SQURVE_EVAL_UNIFIED_LOG")
    if configured:
        paths.append(Path(configured))
    for save_path in save_lis:
        resolved = resolve_saved_dataset_path(save_path)
        parent = Path(resolved).parent
        paths.extend([
            parent / "unified-log.jsonl",
            parent.parent / "unified-log.jsonl",
            parent / "data_log" / "unified-log.jsonl",
        ])

    diagnostics = {}
    for path in paths:
        for instance_id, values in extract_unified_log_diagnostics(path).items():
            diagnostics.setdefault(instance_id, {}).update(values)
    return diagnostics


def _print_scores_digest(scores):
    """Legacy digest — kept for backward compatibility but superseded by print_full_report."""
    aggregate = scores.get("aggregate") or {}
    error_dist = aggregate.get("error_root_distribution") or {}
    if error_dist:
        print()
        print("错误根因 Top 5:")
        ranked = sorted(error_dist.items(), key=lambda item: item[1].get("count", 0), reverse=True)
        for root, stats in ranked[:5]:
            print(f"  - {root}: {stats.get('count', 0)} ({stats.get('pct', 0):.1%})")


class _suppress_eval_warnings:
    """Hide per-sample SQL execution warnings during batch scoring."""

    def __enter__(self):
        logger.disable("core.evaluate")
        return self

    def __exit__(self, exc_type, exc, tb):
        logger.enable("core.evaluate")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run a Squrve reproduce config from reproduce/configs/<dataset>/<method>.json",
    )
    parser.add_argument("dataset", help="benchmark name, e.g. spider")
    parser.add_argument("method", help="method slug, e.g. dinsql")
    args = parser.parse_args()
    main(args.dataset, args.method)
