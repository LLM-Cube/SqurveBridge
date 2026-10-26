"""HTTP API for the interactive Squrve demo workspace."""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import quote, urlsplit

from flask import Flask, jsonify, request
from loguru import logger
from flask_sock import Sock
from sqlglot import exp, parse

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from demo import model_catalog
from demo import comparison_snapshots
from demo.run_evidence import run_evidence
from demo.run_timeline import build_run_timeline, cap_text, make_judge, withhold_content
from demo.experiments import MAX_CELLS, ExperimentConflict, ExperimentNotFound, ExperimentScheduler
from demo.variants import VariantError, normalize_stages, runner_overrides, swappable_actors, variant_key, variant_label
from demo.deployment import deployment_features, deployment_target, route_denial
from demo.file_to_db import process_uploaded_files, sqlite_to_schema
from demo.query_runtime import (
    ACTOR_BY_TYPE,
    WORKFLOW_SKELETONS,
    SqurveDemo,
    _builtin_database_references,
    database_benchmark,
    database_schema_id,
    get_available_databases,
    get_router_config_path,
    get_uploaded_db_root,
)
from demo.workspace import (
    artifacts_dir,
    ensure_layout,
    evaluations_dir,
    run_dir as workspace_run_dir,
    workspace_root,
)
from reproduce.lib.stage_overrides import STAGE_ACTORS_ENV
from reproduce.lib.env_config import (
    PROVIDER_ENV_VARS,
    api_key_ready,
    load_dotenv,
    resolve_api_key,
)
from tools.profile_weakness import build_weakness_json
from tools.evidence import EvidenceError, verify_bundle
from demo.pi_api import register_pi_routes
from demo.session_auth import (
    SessionCredentialRegistry,
    SqlCredential,
    new_session_id,
)


app = Flask(__name__)
sock = Sock(app)
_pi_sessions = register_pi_routes(app, sock, _project_root)


_ROUTE_DENIAL_MESSAGES = {
    "local_only": "This operation is available only in the trusted local Demo App.",
    "agent_disabled": "The Agent is turned off in this Demo.",
}


@app.before_request
def enforce_deployment_policy():
    if not app.testing and deployment_target() != "hf-space":
        _restore_evaluation_jobs_once()
    if request.url_rule is None or not request.path.startswith("/api/"):
        return None
    reason = route_denial(request.method, request.path)
    if reason is not None:
        return jsonify({"message": _ROUTE_DENIAL_MESSAGES[reason], "reason": reason}), 403
    return _hosted_ownership_error()


@app.errorhandler(404)
def api_route_not_found(_error):
    return jsonify({"message": "API route not found."}), 404


_demo_instances: dict[tuple[str, str], SqurveDemo] = {}
_demo_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_processes: dict[str, subprocess.Popen] = {}
ensure_layout()
_run_dir = evaluations_dir()
_job_restore_lock = threading.Lock()
_jobs_restored = False
_max_resume_attempts = max(0, int(os.environ.get("SQURVE_EVAL_MAX_RESUMES", "2")))
_resume_backoff_seconds = max(0.0, float(os.environ.get("SQURVE_EVAL_RESUME_BACKOFF", "2")))
_provider_validation = {"verified": False, "error": None}
_runtime_llm: dict[str, str | None] = {"provider": None, "model": None}
_sql_credentials = SessionCredentialRegistry(max_sessions=128, idle_timeout=1800)

# Hosted runs: every job belongs to the browser session that started it and runs on that
# visitor's own key. Keys stay in memory only (never in job.json) and are dropped when the
# job or experiment ends.
_HOSTED_MAX_SAMPLES = 20
_HOSTED_MAX_CELLS = 4
_HOSTED_MAX_ACTIVE_JOBS = 3
_ACTIVE_JOB_STATUSES = {"starting", "running", "resuming"}
_job_owners: dict[str, str] = {}
_job_credentials: dict[str, SqlCredential] = {}
_experiment_owners: dict[str, str] = {}
_experiment_credentials: dict[str, SqlCredential] = {}
_launch_binding = threading.local()
_session_cookie_name = "squrve_session"
_sample_limits = {3, 10, 20, 50, 100, 200}
_supported_providers = frozenset(model_catalog.PROVIDER_ORDER)


def _provider_model_ids() -> dict[str, list[str]]:
    """Provider → model IDs, sourced from the embedded Pi SDK where possible."""
    return model_catalog.provider_models(_project_root)
_QWEN_ENDPOINTS = {
    "china": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "international": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}
_QWEN_WORKSPACE_REGIONS = {
    "china_workspace": "cn-beijing",
    "singapore_workspace": "ap-southeast-1",
}


def _local_request_error():
    """Restrict local configuration mutations to the trusted Demo App."""
    try:
        is_local = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        is_local = False
    if not is_local:
        return _json_error("Configuration access is restricted to localhost.", 403)
    origin = request.headers.get("Origin")
    allowed_origins = {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:7860",
        "http://localhost:7860",
    }
    if origin and origin not in allowed_origins:
        return _json_error(
            "Configuration access is restricted to the local Squrve workspace.",
            403,
        )
    return None


def _json_error(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


def _hosted() -> bool:
    return deployment_target() == "hf-space"


def _json_code_error(code: str, message: str, status: int):
    return jsonify({"status": "error", "code": code, "message": message}), status


def _hosted_run_binding():
    """(session_id, credential) for a hosted run, or an error response to return."""
    denied = _same_origin_error()
    if denied is not None:
        return None, denied
    session_id, _ = _browser_session(create=False)
    credential = _sql_credentials.get(session_id) if session_id else None
    if credential is None:
        return None, _json_code_error(
            "credential_required",
            "Connect your own LLM key (Configure LLM) before running a config.",
            401,
        )
    if credential.provider not in PROVIDER_ENV_VARS:
        return None, _json_code_error(
            "unsupported_provider", "This provider cannot run reproduce configs.", 400,
        )
    return (session_id, credential), None


def _session_has_active_run(session_id: str) -> bool:
    with _jobs_lock:
        if any(
            owner == session_id and (_jobs.get(job_id) or {}).get("status") in _ACTIVE_JOB_STATUSES
            for job_id, owner in _job_owners.items()
        ):
            return True
    return any(
        _experiment_owners.get(item.get("experiment_id")) == session_id
        and item.get("status") in {"queued", "running"}
        for item in _experiments.list()
    )


def _hosted_capacity_error(session_id: str):
    if _session_has_active_run(session_id):
        return _json_code_error(
            "run_in_progress", "Finish or cancel your current run before starting another.", 429,
        )
    with _jobs_lock:
        active = sum(1 for job in _jobs.values() if job.get("status") in _ACTIVE_JOB_STATUSES)
    if active >= _HOSTED_MAX_ACTIVE_JOBS:
        return _json_code_error(
            "capacity_reached", "The Space is running other visitors' configs. Try again shortly.", 429,
        )
    return None


def _hosted_ownership_error():
    """Hosted jobs and experiments are visible only to the session that started them."""
    if not _hosted() or not request.view_args:
        return None
    session_id, _ = _browser_session(create=False)
    args = request.view_args
    if "job_id" in args and _job_owners.get(args["job_id"]) != session_id:
        return _json_error("Evaluation not found.", 404)
    if "experiment_id" in args and _experiment_owners.get(args["experiment_id"]) != session_id:
        return _json_error("Experiment not found.", 404)
    if "comparison_id" in args and not any(
        owner == session_id and (_jobs.get(job_id) or {}).get("comparison_id") == args["comparison_id"]
        for job_id, owner in _job_owners.items()
    ):
        return _json_error("Comparison not found in the current session.", 404)
    return None


def _apply_credential_env(env: dict[str, str], credential: SqlCredential) -> None:
    for name in PROVIDER_ENV_VARS.values():
        env.pop(name, None)
    env["SQURVE_LLM_PROVIDER"] = credential.provider
    env["SQURVE_LLM_MODEL"] = credential.model
    env[PROVIDER_ENV_VARS[credential.provider]] = credential.api_key
    if credential.base_url:
        env["SQURVE_LLM_BASE_URL"] = credential.base_url
    else:
        env.pop("SQURVE_LLM_BASE_URL", None)


def _prune_experiment_credentials() -> None:
    live = {
        item.get("experiment_id")
        for item in _experiments.list()
        if item.get("status") in {"queued", "running"}
    }
    for experiment_id in [key for key in _experiment_credentials if key not in live]:
        _experiment_credentials.pop(experiment_id, None)


class SqlAuthError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@app.after_request
def _allow_local_frontend(response):
    origin = request.headers.get("Origin", "")
    if origin.startswith(("http://127.0.0.1:", "http://localhost:")):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response


def _get_demo(provider: str, model: str) -> SqurveDemo:
    key = (provider, model)
    if key not in _demo_instances:
        with _demo_lock:
            if key not in _demo_instances:
                _demo_instances[key] = SqurveDemo(provider=provider, model_name=model)
    return _demo_instances[key]


def _session_demo(credential: SqlCredential) -> SqurveDemo:
    return SqurveDemo(
        provider=credential.provider,
        model_name=credential.model,
        api_key=credential.api_key,
        base_url=credential.base_url,
    )


def _browser_session(*, create: bool) -> tuple[str | None, bool]:
    session_id = request.cookies.get(_session_cookie_name, "").strip()
    if session_id:
        return session_id, False
    if not create:
        return None, False
    return new_session_id(), True


def _session_cookie_attributes() -> dict:
    """The Space is shown inside an iframe on huggingface.co, a cross-site context where
    a Lax cookie is never sent back; a partitioned SameSite=None cookie is."""
    if deployment_target() == "hf-space":
        return {"secure": True, "httponly": True, "samesite": "None", "partitioned": True}
    return {"secure": False, "httponly": True, "samesite": "Lax"}


def _set_session_cookie(response, session_id: str) -> None:
    response.set_cookie(_session_cookie_name, session_id, max_age=1800, **_session_cookie_attributes())


def _same_origin_error():
    if deployment_target() != "hf-space":
        return None
    origin = request.headers.get("Origin", "").rstrip("/")
    forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    expected_host = forwarded_host or request.host
    expected_scheme = forwarded_proto or request.scheme
    parsed_origin = urlsplit(origin) if origin else None
    if parsed_origin and (
        parsed_origin.scheme != expected_scheme or parsed_origin.netloc != expected_host
    ):
        return jsonify({
            "status": "error",
            "code": "origin_forbidden",
            "message": "Credential changes require a same-origin request.",
        }), 403
    return None


def _sql_provider_catalog() -> list[dict[str, object]]:
    catalog = []
    for provider, models in _provider_model_ids().items():
        item = {
            "id": provider,
            "models": list(models),
            "default_model": models[0],
            "models_source": "catalog",
        }
        if provider == "qwen":
            item["endpoints"] = [
                {"id": "china_workspace", "label": "China (Beijing) · workspace", "requires_workspace": True},
                {"id": "singapore_workspace", "label": "Singapore · workspace", "requires_workspace": True},
                {"id": "china", "label": "China (Beijing) · shared legacy"},
                {"id": "international", "label": "Singapore · shared legacy"},
            ]
            # Keep the default usable with only an API key. Workspace-scoped
            # endpoints remain available when the caller explicitly selects one.
            item["default_endpoint_id"] = "china"
        catalog.append(item)
    return catalog


def _credential_from_payload(payload: dict) -> SqlCredential:
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = payload.get("api_key")
    if provider not in _supported_providers:
        raise SqlAuthError("unsupported_provider")
    if not model or len(model) > 200 or any(ord(character) < 32 for character in model):
        raise SqlAuthError("unsupported_model")
    if not isinstance(api_key, str) or not api_key.strip():
        raise SqlAuthError("credential_required")
    endpoint_id = str(payload.get("endpoint_id") or "").strip()
    base_url = None
    if provider == "qwen":
        endpoint_id = endpoint_id or "china"
        if endpoint_id in _QWEN_WORKSPACE_REGIONS:
            workspace_id = str(payload.get("workspace_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", workspace_id):
                raise SqlAuthError("unsupported_workspace")
            region = _QWEN_WORKSPACE_REGIONS[endpoint_id]
            base_url = f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"
        else:
            base_url = _QWEN_ENDPOINTS.get(endpoint_id)
            if base_url is None:
                raise SqlAuthError("unsupported_endpoint")
    return SqlCredential(
        provider=provider,
        model=model,
        api_key=api_key.strip(),
        endpoint_id=endpoint_id,
        base_url=base_url,
    )


def _validate_sql_credential(credential: SqlCredential) -> None:
    try:
        demo = _session_demo(credential)
        llm = demo.engine.dataloader.llm
        if llm is None:
            raise SqlAuthError("unsupported_provider")
        llm.time_out = min(float(llm.time_out), 20.0)
        llm.complete("Reply with OK only.")
    except SqlAuthError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403} or any(
            token in message
            for token in ("401", "403", "invalid_api_key", "invalid api key", "incorrect api key")
        ):
            raise SqlAuthError("credential_rejected") from None
        if status_code == 429 or "rate limit" in message or "rate_limit" in message:
            raise SqlAuthError("provider_rate_limited") from None
        if status_code in {400, 404, 422}:
            raise SqlAuthError("model_rejected") from None
        if "timeout" in type(exc).__name__.lower() or "timed out" in message:
            raise SqlAuthError("provider_timeout") from None
        raise SqlAuthError("provider_unreachable") from None


def _sql_auth_error_response(error: SqlAuthError, provider: str = "provider"):
    statuses = {
        "credential_rejected": 401,
        "provider_unreachable": 503,
        "provider_timeout": 504,
        "provider_rate_limited": 429,
        "model_rejected": 400,
        "origin_forbidden": 403,
        "unsupported_provider": 400,
        "unsupported_model": 400,
        "unsupported_endpoint": 400,
        "unsupported_workspace": 400,
        "credential_required": 400,
    }
    messages = {
        "credential_rejected": f"The {provider} credential was rejected.",
        "provider_unreachable": f"The {provider} provider could not be reached.",
        "provider_timeout": f"The {provider} endpoint timed out. Check the selected region and Workspace ID.",
        "provider_rate_limited": f"The {provider} account is currently rate limited.",
        "model_rejected": f"The selected {provider} model is unavailable for this region or workspace.",
        "unsupported_provider": "The selected SQL provider is unsupported.",
        "unsupported_model": "The selected SQL model is unsupported.",
        "unsupported_endpoint": "The selected SQL provider region is unsupported.",
        "unsupported_workspace": "A valid Alibaba Cloud Workspace ID is required for this Qwen endpoint.",
        "credential_required": "An API key is required.",
    }
    return jsonify({
        "status": "error",
        "code": error.code,
        "message": messages.get(error.code, "SQL authentication failed."),
    }), statuses.get(error.code, 400)


_DATABASE_RECORDS_LOCK = threading.Lock()
_DATABASE_RECORDS_CACHE: tuple[tuple[tuple[str, str, str], ...], list[dict]] | None = None


def _load_schema_document(schema_path: str, schema_cache: dict[str, object]) -> object | None:
    if schema_path in schema_cache:
        return schema_cache[schema_path]
    try:
        document = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        document = None
    schema_cache[schema_path] = document
    return document


def _tables_from_schema_document(document: object, db_path: str) -> list:
    if document is None:
        return []
    schemas = document if isinstance(document, list) else [document]
    item = next(
        (
            candidate
            for candidate in schemas
            if isinstance(candidate, dict) and candidate.get("db_id") == database_schema_id(db_path)
        ),
        schemas[0] if schemas and isinstance(schemas[0], dict) else None,
    )
    if not isinstance(item, dict):
        return []
    # Listing must stay cheap: never rewrite schemas or open SQLite here.
    if len(item.get("column_names_original", [])) != len(item.get("column_types", [])):
        return item.get("table_names_original") or item.get("table_names") or []
    return item.get("table_names_original") or item.get("table_names") or []


def _schema_document_for_db(document: object, db_path: str) -> dict | None:
    if document is None:
        return None
    schemas = document if isinstance(document, list) else [document]
    item = next(
        (
            candidate
            for candidate in schemas
            if isinstance(candidate, dict) and candidate.get("db_id") == database_schema_id(db_path)
        ),
        schemas[0] if schemas and isinstance(schemas[0], dict) else None,
    )
    return item if isinstance(item, dict) else None


def _schema_tree_from_document(document: object, db_path: str) -> list[dict] | None:
    """Build a public table/column tree (names, types, keys only) from a Spider-format schema."""
    item = _schema_document_for_db(document, db_path)
    if item is None:
        return None
    table_names = item.get("table_names_original") or item.get("table_names") or []
    column_entries = item.get("column_names_original") or item.get("column_names") or []
    column_types = item.get("column_types") or []

    primary_indexes: set[int] = set()
    for key in item.get("primary_keys") or []:
        if isinstance(key, bool):
            continue
        if isinstance(key, int):
            primary_indexes.add(key)
        elif isinstance(key, (list, tuple)):
            primary_indexes.update(
                index for index in key if isinstance(index, int) and not isinstance(index, bool)
            )

    tables: list[dict] = [
        {"name": str(name)[:120], "columns": []}
        for name in table_names[:300]
        if isinstance(name, str)
    ]
    columns_by_index: dict[int, dict] = {}
    location_by_index: dict[int, tuple[int, str]] = {}
    for index, entry in enumerate(column_entries):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        table_index, column_name = entry
        if isinstance(table_index, bool) or not isinstance(table_index, int):
            continue
        if not 0 <= table_index < len(tables):
            continue
        column: dict = {
            "name": str(column_name)[:120],
            "type": str(column_types[index])[:40] if index < len(column_types) else "",
        }
        if index in primary_indexes:
            column["primary_key"] = True
        tables[table_index]["columns"].append(column)
        columns_by_index[index] = column
        location_by_index[index] = (table_index, column["name"])

    for pair in item.get("foreign_keys") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        source, target = pair
        column = columns_by_index.get(source) if isinstance(source, int) else None
        location = location_by_index.get(target) if isinstance(target, int) else None
        if column is None or location is None:
            continue
        table_index, column_name = location
        column["foreign_key"] = {"table": tables[table_index]["name"], "column": column_name}
    return tables


def _database_records() -> list[dict]:
    global _DATABASE_RECORDS_CACHE
    available = tuple(get_available_databases())
    with _DATABASE_RECORDS_LOCK:
        if _DATABASE_RECORDS_CACHE is not None and _DATABASE_RECORDS_CACHE[0] == available:
            return _DATABASE_RECORDS_CACHE[1]

    schema_cache: dict[str, object] = {}
    # Avoid O(n^2) rebuilds of the builtin catalog via database_benchmark().
    benchmark_by_id = {
        reference_id: benchmark
        for reference_id, benchmark, _db_path, _schema_path in _builtin_database_references()
    }
    records = []
    for db_id, db_path, schema_path in available:
        tables = _tables_from_schema_document(
            _load_schema_document(schema_path, schema_cache),
            db_path,
        )
        try:
            size_bytes = Path(db_path).stat().st_size
        except OSError:
            size_bytes = 0
        records.append({
            "id": db_id,
            "db_path": db_path,
            "schema_path": schema_path,
            "tables": tables,
            "size_bytes": size_bytes,
            "benchmark": benchmark_by_id.get(db_id) or database_benchmark(db_id),
        })

    with _DATABASE_RECORDS_LOCK:
        _DATABASE_RECORDS_CACHE = (available, records)
    return records


def invalidate_database_records_cache() -> None:
    global _DATABASE_RECORDS_CACHE
    with _DATABASE_RECORDS_LOCK:
        _DATABASE_RECORDS_CACHE = None


def _find_database(db_id: str) -> dict | None:
    return next((item for item in _database_records() if item["id"] == db_id), None)


def _config_catalog() -> list[dict]:
    configs = []
    base = _project_root / "reproduce" / "configs"
    for path in sorted(base.glob("*/*.json")):
        if path.parent.name == "evolution" or path.name in {"database-registration.json", "few_shot_examples.json"}:
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        task_meta = config.get("task", {}).get("task_meta", [])
        stages = []
        for task in task_meta:
            meta = task.get("meta", {}).get("task", {})
            actor = next((value for key, value in meta.items() if key.endswith("_type")), None)
            stages.append({
                "id": task.get("task_id"),
                "type": task.get("task_type"),
                "actor": actor,
            })
        llm = config.get("llm", {})
        method = path.stem
        if "smoke" in method.lower() or "slice" in method.lower():
            continue
        data_source = str(config.get("dataset", {}).get("data_source", ""))
        source_parts = data_source.split(":")
        split = source_parts[1] if len(source_parts) == 3 else config.get("split", "unknown")
        benchmark_id = source_parts[0] if len(source_parts) == 3 else path.parent.name
        configs.append({
            "dataset": path.parent.name,
            "method": method,
            "label": f"{method} / {path.parent.name}",
            "config_path": str(path.relative_to(_project_root)),
            "split": split,
            "scope": "smoke config" if "smoke" in method else "slice config" if "slice" in method else "canonical config",
            "provider": llm.get("use"),
            "model": llm.get("model_name"),
            "stages": stages,
            "exec_process": config.get("engine", {}).get("exec_process", []),
            "installed": _benchmark_split_path(benchmark_id, split) is not None,
        })
    return configs


def _benchmark_split_path(benchmark_id: str, split: str) -> Path | None:
    """The installed dataset file of a benchmark split, or None when it is not installed."""
    config_dir = _project_root / "config"
    try:
        records = json.loads((config_dir / "sys_config.json").read_text(encoding="utf-8")).get("benchmark", [])
    except (OSError, ValueError, TypeError):
        return None
    record = next((item for item in records if item.get("id") == benchmark_id), None)
    if not record or not record.get("root_path") or not _IDENTIFIER.fullmatch(split or ""):
        return None
    path = (config_dir / record["root_path"] / split / "dataset.json").resolve()
    return path if path.is_file() else None


def _benchmark_catalog() -> list[dict]:
    path = _project_root / "config" / "sys_config.json"
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("benchmark", [])
    except (OSError, ValueError, TypeError):
        return []
    catalog = []
    preferred_splits = ("dev", "valid", "val", "test", "train")
    # Only expose benchmarks that can support a local, labeled 100-row evaluation.
    hidden_benchmarks = {"spider_dk", "SquRL"}
    for record in records:
        benchmark_id = str(record.get("id", "")).strip()
        if not benchmark_id or benchmark_id in hidden_benchmarks:
            continue
        splits = [str(item.get("sub_id")) for item in record.get("sub_data", []) if item.get("sub_id")]
        default_split = next((split for split in preferred_splits if split in splits), splits[0] if splits else "")
        catalog.append({
            "id": benchmark_id,
            "splits": splits,
            "default_split": default_split,
            "db_type": record.get("db_type"),
        })
    return catalog


def _router_config() -> dict:
    config_path = Path(get_router_config_path())
    if not config_path.is_absolute():
        config_path = _project_root / config_path
    return json.loads(config_path.read_text(encoding="utf-8"))


def _quote_env_value(value: str) -> str:
    if re.search(r'[\s#"\'\\]', value):
        return json.dumps(value)
    return value


def _upsert_dotenv(updates: dict[str, str]) -> None:
    env_path = _project_root / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    remaining = dict(updates)
    rewritten: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rewritten.append(line)
            continue
        body = stripped[len("export "):].strip() if stripped.startswith("export ") else stripped
        key, _ = body.split("=", 1)
        key = key.strip()
        if key in remaining:
            rewritten.append(f"{key}={_quote_env_value(remaining.pop(key))}")
        else:
            rewritten.append(line)
    for key, value in remaining.items():
        rewritten.append(f"{key}={_quote_env_value(value)}")
    if rewritten and rewritten[-1] != "":
        rewritten.append("")
    env_path.write_text("\n".join(rewritten), encoding="utf-8")


def _clear_demo_instances() -> None:
    with _demo_lock:
        _demo_instances.clear()


def _validate_model_id(model: str) -> str:
    cleaned = (model or "").strip()
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise ValueError("model must be a non-empty model ID (max 200 characters)")
    return cleaned


def _apply_provider_config(provider: str, model: str, api_key: str | None = None, persist: bool = True) -> dict:
    if provider not in _supported_providers:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    model = _validate_model_id(model)

    env_name = PROVIDER_ENV_VARS.get(provider)
    if not env_name:
        raise ValueError(f"No environment variable mapping for provider: {provider}")

    key = (api_key or "").strip() or None
    if key:
        os.environ[env_name] = key
    elif not resolve_api_key(provider, None):
        raise ValueError(
            f"{provider} api_key is missing; paste a key here or set {env_name} in repo-root .env"
        )

    os.environ["SQURVE_LLM_PROVIDER"] = provider
    os.environ["SQURVE_LLM_MODEL"] = model
    if persist:
        updates = {
            "SQURVE_LLM_PROVIDER": provider,
            "SQURVE_LLM_MODEL": model,
        }
        if key:
            updates[env_name] = key
        _upsert_dotenv(updates)
    if key:
        _clear_demo_instances()
    elif _runtime_llm.get("provider") != provider or _runtime_llm.get("model") != model:
        _clear_demo_instances()

    _runtime_llm.update(provider=provider, model=model)
    _provider_validation.update(verified=False, error=None)
    return _provider_status()


def _provider_status() -> dict:
    load_dotenv(_project_root / ".env")
    config = _router_config()
    llm = dict(config.get("llm") or {})
    provider = (
        _runtime_llm.get("provider")
        or os.environ.get("SQURVE_LLM_PROVIDER")
        or llm.get("use")
    )
    model = (
        _runtime_llm.get("model")
        or os.environ.get("SQURVE_LLM_MODEL")
        or llm.get("model_name")
    )
    if provider:
        llm["use"] = provider
    if model:
        llm["model_name"] = model
    status_config = {**config, "llm": llm}
    configured, message = api_key_ready(status_config)
    return {
        "configured": configured,
        "verified": _provider_validation["verified"],
        "ready": configured and _provider_validation["error"] is None,
        "message": _provider_validation["error"] or message,
        "provider": provider,
        "model": model,
        "env_var": PROVIDER_ENV_VARS.get(provider or "", None),
    }


def _llm_provider_catalog() -> list[dict]:
    if deployment_target() == "hf-space":
        return _sql_provider_catalog()
    load_dotenv(_project_root / ".env")
    default = _provider_status()
    catalog = []
    for provider, catalog_models in _provider_model_ids().items():
        models = list(catalog_models)
        active_model = default["model"] if provider == default["provider"] else None
        catalog.append({
            "id": provider,
            "configured": bool(resolve_api_key(provider, None)),
            "models": models,
            "default_model": active_model if active_model in models else models[0],
            "models_source": "catalog",
            "env_var": PROVIDER_ENV_VARS[provider],
        })
    return catalog


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "provider": _provider_status()})


@app.get("/api/sql-auth")
def sql_auth_status():
    session_id, _ = _browser_session(create=False)
    status = _sql_credentials.status(session_id or "")
    return jsonify({"status": "ok", **status, "providers": _sql_provider_catalog()})


@app.post("/api/sql-auth/test")
def test_sql_auth():
    origin_error = _same_origin_error()
    if origin_error:
        return origin_error
    payload = request.get_json(silent=True) or {}
    try:
        credential = _credential_from_payload(payload)
        _validate_sql_credential(credential)
    except SqlAuthError as exc:
        return _sql_auth_error_response(exc, str(payload.get("provider") or "provider"))
    return jsonify({
        "status": "ok",
        "validated": True,
        "provider": credential.provider,
        "model": credential.model,
    })


@app.put("/api/sql-auth")
def save_sql_auth():
    origin_error = _same_origin_error()
    if origin_error:
        return origin_error
    payload = request.get_json(silent=True) or {}
    try:
        credential = _credential_from_payload(payload)
        _validate_sql_credential(credential)
    except SqlAuthError as exc:
        return _sql_auth_error_response(exc, str(payload.get("provider") or "provider"))
    credential.validated_at = time.time()
    session_id, _ = _browser_session(create=True)
    _sql_credentials.put(session_id, credential)
    response = jsonify({
        "status": "ok",
        **_sql_credentials.status(session_id),
        "providers": _sql_provider_catalog(),
    })
    _set_session_cookie(response, session_id)
    return response


@app.delete("/api/sql-auth")
def delete_sql_auth():
    origin_error = _same_origin_error()
    if origin_error:
        return origin_error
    session_id, _ = _browser_session(create=False)
    if session_id:
        _sql_credentials.delete(session_id)
    response = jsonify({
        "status": "ok",
        "configured": False,
        "providers": _sql_provider_catalog(),
    })
    response.delete_cookie(_session_cookie_name, **_session_cookie_attributes())
    return response


@app.post("/api/provider")
def update_provider():
    local_error = _local_request_error()
    if local_error:
        return local_error
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = payload.get("api_key")
    persist = bool(payload.get("persist", True))
    if not provider:
        return _json_error("provider is required")
    if not model:
        catalog = next((item for item in _llm_provider_catalog() if item["id"] == provider), None)
        model = (catalog or {}).get("default_model") or (_provider_model_ids().get(provider) or [""])[0]
    if api_key is not None and not isinstance(api_key, str):
        return _json_error("api_key must be a string")
    try:
        status = _apply_provider_config(provider, model, api_key=api_key, persist=persist)
    except ValueError as exc:
        return _json_error(str(exc))
    except RuntimeError:
        return _json_error("Evaluation process could not be started.", 503)
    return jsonify({"status": "ok", "provider": status, "llm_providers": _llm_provider_catalog()})


_listing_rate_window = 60.0
_listing_rate_limit = 30
_listing_rate_hits: dict[str, list[float]] = {}
_listing_rate_lock = threading.Lock()


def _listing_rate_allowed(identity: str) -> bool:
    now = time.monotonic()
    with _listing_rate_lock:
        hits = [t for t in _listing_rate_hits.get(identity, ()) if now - t < _listing_rate_window]
        allowed = len(hits) < _listing_rate_limit
        if allowed:
            hits.append(now)
        _listing_rate_hits[identity] = hits
        if len(_listing_rate_hits) > 4096:
            for key in [k for k, v in _listing_rate_hits.items() if not v or now - v[-1] >= _listing_rate_window]:
                del _listing_rate_hits[key]
    return allowed


def _listing_credential(
    payload: dict,
    provider: str,
    hosted: bool,
    session_id: str | None,
) -> tuple[SqlCredential, str | None]:
    """Resolve the key a model listing should use: request, then session, then local env.

    Hosted deployments never read the server environment. The credential checks
    are reused so endpoint and workspace IDs are validated exactly as when
    saving; the model field is a placeholder.
    """
    api_key = payload.get("api_key")
    saved_base_url = None
    if not (isinstance(api_key, str) and api_key.strip()):
        saved = _sql_credentials.get(session_id or "") if hosted else None
        if saved is not None and saved.provider == provider:
            api_key, saved_base_url = saved.api_key, saved.base_url
        elif not hosted:
            load_dotenv(_project_root / ".env")
            api_key = resolve_api_key(provider, None)
    credential = _credential_from_payload({**payload, "api_key": api_key, "model": "listing"})
    return credential, saved_base_url or credential.base_url


@app.post("/api/llm/models")
def list_llm_models():
    origin_error = _same_origin_error()
    if origin_error:
        return origin_error
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or "").strip()
    if provider not in _supported_providers:
        return _json_error("Unsupported provider.")
    hosted = deployment_target() == "hf-space"
    session_id, _ = _browser_session(create=False)
    if hosted and not _listing_rate_allowed(session_id or request.remote_addr or "anonymous"):
        return _json_error("Too many model refreshes; retry shortly.", 429)
    try:
        credential, base_url = _listing_credential(payload, provider, hosted, session_id)
    except SqlAuthError as exc:
        return _json_error(f"Invalid request: {exc.code}.")
    fallback = _provider_model_ids().get(provider) or []
    listing = model_catalog.list_provider_models(
        provider,
        credential.api_key,
        fallback=fallback,
        url=f"{base_url}/models" if base_url else None,
        refresh=bool(payload.get("refresh")),
    )
    models = model_catalog.default_first(provider, list(listing["models"]))
    return jsonify({
        "status": "ok",
        "provider": provider,
        **listing,
        "models": models,
        "default_model": models[0] if models else "",
    })


@app.get("/api/capabilities")
def capabilities():
    return jsonify({
        "actors": ACTOR_BY_TYPE,
        "workflows": WORKFLOW_SKELETONS,
        "llm_providers": _llm_provider_catalog(),
        "benchmarks": _benchmark_catalog(),
        "reproduce_configs": (configs := _config_catalog()),
        # Actors a stage may be swapped to in an experiment variant, by task type.
        "stage_actors": swappable_actors(configs),
        "deployment": {
            "target": deployment_target(),
            "features": deployment_features(),
        },
    })


@app.get("/api/databases")
def databases():
    public_fields = ("id", "tables", "size_bytes", "benchmark")
    return jsonify({
        "databases": [
            {field: database[field] for field in public_fields if database.get(field) is not None}
            for database in _database_records()
        ]
    })


@app.get("/api/databases/<db_id>/schema")
def database_schema(db_id: str):
    database = _find_database(db_id)
    if not database:
        return _json_error("Unknown database.", 404)
    tables = _schema_tree_from_document(
        _load_schema_document(database["schema_path"], {}),
        database["db_path"],
    )
    if tables is None:
        return _json_error("Schema details are unavailable for this database.", 404)
    return jsonify({"db_id": database["id"], "tables": tables})


@app.post("/api/databases/upload")
def upload_database():
    files = request.files.getlist("files")
    if not files:
        return _json_error("Select at least one .sqlite, .db, .csv, .xlsx, or .xls file.")

    allowed = {".sqlite", ".db", ".csv", ".xlsx", ".xls"}
    with tempfile.TemporaryDirectory(prefix="squrve-upload-") as temp_dir:
        paths = []
        used_names = set()
        for index, storage in enumerate(files):
            suffix = Path(storage.filename or "").suffix.lower()
            if suffix not in allowed:
                return _json_error(f"Unsupported file type: {suffix or 'unknown'}")
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(storage.filename).name)
            if safe_name in used_names:
                safe_name = f"{index:02d}-{safe_name}"
            used_names.add(safe_name)
            path = Path(temp_dir) / safe_name
            storage.save(path)
            paths.append(path)
        result = process_uploaded_files(paths, get_uploaded_db_root())
    invalidate_database_records_cache()
    return jsonify({"status": "success", "database": _find_database(result["db_id"])})


@app.post("/api/query")
def query():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    db_id = str(payload.get("db_id", "")).strip()
    database = _find_database(db_id)
    if not question:
        return _json_error("Question is required.")
    if not database:
        return _json_error("Select an uploaded database.")

    mode = payload.get("mode", "direct")
    actors = payload.get("actors") or []
    generator = payload.get("generator") or "DINSQLGenerator"
    method = str(payload.get("method") or "").strip()
    swaps: dict[str, str] = {}
    if method:
        # Replay one question the way a reproduce run does: the method's workflow
        # for the benchmark this database belongs to.
        benchmark = str(database.get("benchmark") or "")
        configs = _config_catalog()
        config = next(
            (item for item in configs if item["method"] == method and item["dataset"] == benchmark),
            None,
        )
        # A variant replays with its swapped Actors, never its method's own.
        try:
            swaps = normalize_stages(config or {}, payload.get("stages"), swappable_actors(configs))
        except VariantError as exc:
            return _json_error(f"Stage swaps are not allowed: {exc}.")
        workflow = [
            swaps.get(stage.get("id"), stage["actor"])
            for stage in (config or {}).get("stages") or [] if stage.get("actor")
        ]
        if not workflow:
            return _json_error(f"No reproduce workflow for {method} on {benchmark or 'this database'}.")
        mode, actors = "workflow", workflow
    hosted = deployment_target() == "hf-space"
    if hosted:
        session_id, _ = _browser_session(create=False)
        credential = _sql_credentials.get(session_id or "")
        if credential is None:
            return jsonify({
                "status": "error",
                "code": "auth_required",
                "message": "Configure a SQL API provider for this browser session.",
            }), 401
        provider = credential.provider
        model = credential.model
        demo = _session_demo(credential)
    else:
        provider = str(payload.get("provider") or _provider_status()["provider"])
        provider_config = next((item for item in _llm_provider_catalog() if item["id"] == provider), None)
        if not provider_config:
            return _json_error(f"Unsupported LLM provider: {provider}")
        # The provider dialog accepts any model ID, so interactive queries must
        # accept the configured model too; the catalog stays suggestions-only.
        try:
            model = _validate_model_id(str(payload.get("model") or provider_config["default_model"]))
        except ValueError as exc:
            return _json_error(str(exc))
        if not provider_config["configured"]:
            return _json_error(f"Provider {provider} is not configured in the local .env.")
        demo = _get_demo(provider, model)
    result = demo.generate_sql(
        question=question,
        db_id=db_id,
        schema_path=database["schema_path"],
        db_path=database["db_path"],
        use_workflow=mode == "workflow" and bool(actors),
        workflow_actor_lis=actors,
        generate_type=generator,
    )
    if method:
        result["method"] = method
        if swaps:
            result["stages"] = swaps
    if result.get("status") == "success":
        result["trace"] = _serialize_public_query_trace(result.get("trace"))
        if not hosted:
            _provider_validation.update(verified=True, error=None)
        result["run_config"] = {
            "database": db_id,
            "llm": {"provider": provider, "model": model},
            "actors": actors if mode == "workflow" else [generator],
        }
    else:
        message = str(result.get("message", ""))
        if hosted:
            code = "credential_rejected" if any(
                token in message.lower() for token in ("invalid_api_key", "incorrect api key", "401", "403")
            ) else "provider_unreachable"
            return _sql_auth_error_response(SqlAuthError(code), provider)
        safe_message = _sanitize_error_message(
            message,
            fallback=f"The configured {provider} provider request failed.",
        )
        if any(token in message.lower() for token in ("invalid_api_key", "incorrect api key", "invalid api key", "401", "403")):
            _provider_validation.update(
                verified=False,
                error=f"The configured {provider} API key was rejected.",
            )
            safe_message = f"The configured {provider} API key was rejected."
        result = {
            "status": "error",
            "sql": "",
            "message": safe_message,
        }
    status = 200 if result.get("status") == "success" else 422
    return jsonify(result), status


def _validate_readonly_sql(sql: str):
    try:
        statements = parse(sql, read="sqlite")
    except Exception as exc:
        raise ValueError(f"SQL parse failed: {exc}") from exc
    if len(statements) != 1:
        raise ValueError("Only one SQL statement can be executed at a time.")
    statement = statements[0]
    blocked = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command)
    if isinstance(statement, blocked) or any(statement.find(kind) for kind in blocked):
        raise ValueError("The demo executes read-only SELECT queries only.")
    if not statement.find(exp.Select) and not isinstance(statement, exp.Select):
        raise ValueError("The demo executes read-only SELECT queries only.")


@app.post("/api/execute")
def execute():
    payload = request.get_json(silent=True) or {}
    db_id = str(payload.get("db_id", "")).strip()
    sql = str(payload.get("sql", "")).strip()
    database = _find_database(db_id)
    if not database:
        return _json_error("Select an uploaded database.")
    if not sql:
        return _json_error("SQL is required.")
    try:
        _validate_readonly_sql(sql)
    except ValueError as exc:
        return _json_error(str(exc))

    started = time.monotonic()
    deadline = started + 5.0
    uri = f"file:{quote(str(Path(database['db_path']).resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        rows = cursor.fetchmany(501)
    except sqlite3.Error as exc:
        return _json_error(f"Execution failed: {exc}", 422)
    finally:
        connection.close()
    truncated = len(rows) > 500
    rows = rows[:500]
    return jsonify({
        "status": "success",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    })


def _summarize_scores(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    aggregate = data.get("aggregate", {})
    metrics = {}
    for name in ("ex", "em", "sf1", "sc", "ves", "rves"):
        value = aggregate.get(name)
        if isinstance(value, dict):
            metrics[name] = value.get("avg")
    stages = []
    for stage_name, stage in data.get("stage_metrics", {}).items():
        stages.append({
            "name": stage_name,
            "task_type": stage.get("task_type"),
            "metrics": stage.get("metrics", {}),
        })
    component_f1 = {}
    for name, value in (aggregate.get("cf1") or {}).items():
        if isinstance(value, dict):
            component_f1[name.removeprefix("cf1_")] = value.get("avg")
    error_roots = []
    for name, value in (aggregate.get("error_root_distribution") or {}).items():
        if isinstance(value, dict):
            error_roots.append({
                "name": name,
                "count": value.get("count"),
                "rate": value.get("pct"),
            })
    error_roots.sort(key=lambda item: item.get("count") or 0, reverse=True)
    token = aggregate.get("token") or {}
    return {
        **run_evidence(data, include_questions=deployment_target() != "hf-space"),
        "run_id": data.get("run_id") or path.parent.name,
        "method": data.get("method"),
        "dataset": data.get("dataset"),
        "split": data.get("split"),
        "scope": data.get("scope"),
        "sample_hash": _sample_hash(data),
        "sampling": _sampling_metadata(data),
        "latency": _latency_summary(data),
        "sample_count": data.get("sample_count"),
        "timestamp": data.get("timestamp"),
        "metrics": metrics,
        "component_f1": component_f1,
        "error_roots": error_roots[:6],
        "token": {
            "total_calls": token.get("total_calls"),
            "total_tokens": token.get("total_tokens"),
            "avg_per_sample": token.get("avg_per_sample"),
        },
        "stages": stages,
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    values = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 4)


def _sampling_metadata(scores: dict, job: dict | None = None) -> dict:
    job = job or {}
    config_snapshot = scores.get("config_snapshot") or {}
    snapshot_sampling = config_snapshot.get("sampling") or {}
    data_source = str(config_snapshot.get("data_source") or "")
    random_match = re.search(r"(?:^|[.:])random-(\d+)(?:\.|$)", data_source)
    seed_match = re.search(r"(?:^|[.:])seed-(\d+)(?:\.|$)", data_source)
    mode = job.get("sample_mode") or snapshot_sampling.get("mode")
    limit = job.get("sample_limit") or snapshot_sampling.get("limit")
    seed = job.get("sample_seed")
    if seed is None:
        seed = snapshot_sampling.get("seed")
    if random_match:
        mode = "random"
        limit = int(random_match.group(1))
        seed = int(seed_match.group(1)) if seed_match else seed
    elif not mode:
        scope_match = re.fullmatch(r"(random|slice)-(\d+)", str(scores.get("scope") or ""))
        if scope_match:
            mode = scope_match.group(1)
            limit = int(scope_match.group(2))
    return {
        "mode": mode,
        "limit": limit or scores.get("sample_count"),
        "seed": seed,
    }


def _latency_summary(scores: dict, job: dict | None = None) -> dict:
    per_sample = scores.get("per_sample") or []
    sample_values = [
        row.get("act_elapsed_s") for row in per_sample
        if isinstance(row.get("act_elapsed_s"), (int, float))
    ]
    # The metrics layer owns latency aggregation (aggregate.latency, added by
    # reproduce.eval.bundle.build); recompute locally only for older bundles.
    aggregated = scores.get("aggregate", {}).get("latency") if isinstance(scores.get("aggregate"), dict) else None
    stage_values: dict[str, list[float]] = {}
    workflow_rows = ((scores.get("workflow_trace") or {}).get("per_sample") or [])
    for row in workflow_rows:
        for stage_id, stage in (row.get("stages") or {}).items():
            elapsed = ((stage.get("runtime") or {}).get("elapsed_s"))
            if isinstance(elapsed, (int, float)):
                stage_values.setdefault(stage_id, []).append(float(elapsed))
    by_stage = {
        stage_id: {
            "sample_count": len(values),
            "mean_s": round(sum(values) / len(values), 4),
            "p95_s": _percentile(values, .95),
        }
        for stage_id, values in stage_values.items() if values
    }
    wall_time = None
    if job and isinstance(job.get("started_at"), (int, float)) and isinstance(job.get("finished_at"), (int, float)):
        wall_time = round(job["finished_at"] - job["started_at"], 4)
    if isinstance(aggregated, dict) and isinstance(aggregated.get("avg_s"), (int, float)):
        return {
            "sample_count": aggregated.get("sample_count"),
            "mean_s": round(aggregated["avg_s"], 4),
            "p50_s": round(aggregated["p50_s"], 4) if isinstance(aggregated.get("p50_s"), (int, float)) else None,
            "p95_s": round(aggregated["p95_s"], 4) if isinstance(aggregated.get("p95_s"), (int, float)) else None,
            "wall_time_s": wall_time,
            "by_stage": by_stage,
        }
    return {
        "sample_count": len(sample_values),
        "mean_s": round(sum(sample_values) / len(sample_values), 4) if sample_values else None,
        "p50_s": _percentile(sample_values, .5),
        "p95_s": _percentile(sample_values, .95),
        "wall_time_s": wall_time,
        "by_stage": by_stage,
    }


_FORBIDDEN_COMPARISON_KEY_MARKERS = {
    "question", "sql", "credential", "api_key", "apikey", "token", "secret",
    "authorization", "source", "excerpt", "code", "snippet", "patch", "diff",
    "prompt", "review_note", "review_notes",
}
_PRIVATE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access_token|auth_token|secret_key|client_secret|password|token|secret|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bapi[\s_-]*key\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:sk[_-]?live[_-]?|sk[_-]?test[_-]?|sk|hf_|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9._-]{6,}"),
    re.compile(r"(?i)\b(?:https?|wss?)://[^\s]+"),
    re.compile(r"(?<![\w.])/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+"),
    re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s]+"),
)
_ERROR_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access_token|auth_token|secret_key|client_secret|password|token|secret|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bapi[\s_-]*key\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:sk[_-]?live[_-]?|sk[_-]?test[_-]?|sk|hf_|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9._-]{6,}"),
)


def _sanitize_error_message(message: object, *, fallback: str = "The provider request failed.") -> str:
    text = str(message or "")
    lowered = text.lower()
    if any(token in lowered for token in ("invalid_api_key", "incorrect api key", "invalid api key")):
        return "The configured API key was rejected."
    sanitized = text
    for pattern in _PRIVATE_VALUE_PATTERNS:
        sanitized = pattern.sub("[redacted]", sanitized)
    sanitized = sanitized.strip()
    return sanitized[:240] if sanitized else fallback
_PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@+-]{1,160}$")
_PUBLIC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?$")
_PUBLIC_ARTIFACT_REF = re.compile(r"^(?:session:)?[A-Za-z0-9_.:@+-]+(?:/[A-Za-z0-9_.@+-]+)*$")
_PUBLIC_SOURCES = {"artifact", "session", "evidence", "artifacts", "demo-runs", "archive"}
_EVOLUTION_STAGES = {
    "baseline", "weakness_profile", "candidate_change", "smoke",
    "bounded_evaluation", "confirmation", "human_review",
}
_EVOLUTION_FIELDS = {
    "artifact", "artifact_ref", "status", "state", "outcome", "decision",
    "metric", "value", "score", "count", "stage", "actor", "id",
    "root", "category", "delta", "accepted",
}
_EVOLUTION_ARTIFACT_FIELDS = {"artifact", "artifact_ref"}
_EVOLUTION_IDENTIFIER_FIELDS = {
    "status", "state", "outcome", "decision", "metric", "stage",
    "actor", "id", "root", "category",
}
_EVOLUTION_NUMERIC_FIELDS = {"value", "score", "count", "delta", "accepted"}


def _forbidden_comparison_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return any(marker in normalized for marker in _FORBIDDEN_COMPARISON_KEY_MARKERS)


def _sanitize_public_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return None
    sanitized = value
    for pattern in _PRIVATE_VALUE_PATTERNS:
        sanitized = pattern.sub("[redacted]", sanitized)
    return sanitized[:240]


def _sanitize_numeric_tree(value):
    if isinstance(value, dict):
        result = {
            str(key)[:80]: sanitized
            for key, item in value.items()
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(key))
            and (sanitized := _sanitize_numeric_tree(item)) is not None
        }
        return result or None
    if isinstance(value, list):
        result = [item for item in (_sanitize_numeric_tree(item) for item in value[:200]) if item is not None]
        return result or None
    return value if value is None or isinstance(value, (bool, int, float)) else None


def _public_identifier(value) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_public_scalar(value)
    return sanitized if isinstance(sanitized, str) and _PUBLIC_IDENTIFIER.fullmatch(sanitized) else "[redacted]"


def _public_timestamp(value) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_public_scalar(value)
    return sanitized if isinstance(sanitized, str) and _PUBLIC_TIMESTAMP.fullmatch(sanitized) else "[redacted]"


def _public_artifact_ref(value) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_public_scalar(value)
    return sanitized if isinstance(sanitized, str) and _PUBLIC_ARTIFACT_REF.fullmatch(sanitized) else "[redacted]"


def _serialize_public_query_trace(value) -> list[dict]:
    """Expose stage metadata without actor inputs, outputs, errors, or row contents."""
    if not isinstance(value, list):
        return []
    trace = []
    for record in value[:100]:
        if not isinstance(record, dict):
            continue
        actor = _public_identifier(record.get("actor_name") or record.get("actor_class"))
        stage = _public_identifier(record.get("stage_name") or record.get("stage"))
        elapsed_s = record.get("elapsed_s")
        public = {
            "actor_name": actor,
            "stage": stage,
            "status": "failed" if record.get("error") else "completed",
        }
        if isinstance(elapsed_s, (int, float)) and not isinstance(elapsed_s, bool):
            public["elapsed_ms"] = round(float(elapsed_s) * 1000, 3)
        trace.append({
            key: item
            for key, item in public.items()
            if item not in {None, "[redacted]"}
        })
    return trace


def _serialize_aggregate_metrics(value) -> dict:
    if not isinstance(value, dict):
        return {}
    excluded = {"token", "error_root_distribution"}
    return {
        str(key)[:80]: sanitized
        for key, item in value.items()
        if str(key) not in excluded
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(key))
        and (sanitized := _sanitize_numeric_tree(item)) is not None
    }


def _serialize_error_distribution(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for root, stats in value.items():
        root_id = _public_identifier(root)
        if root_id == "[redacted]" or not isinstance(stats, dict):
            continue
        public = {}
        for field in ("count", "pct"):
            if stats.get(field) is None or isinstance(stats.get(field), (bool, int, float)):
                public[field] = stats.get(field)
        result[root_id] = public
    return result


def _serialize_hardness(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for bucket, metrics in value.items():
        bucket_id = _public_identifier(bucket)
        if bucket_id == "[redacted]" or not isinstance(metrics, dict):
            continue
        public = {
            str(key)[:80]: item
            for key, item in metrics.items()
            if key != "error_dist"
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(key))
            and (item is None or isinstance(item, (bool, int, float)))
        }
        if "error_dist" in metrics:
            public["error_dist"] = _serialize_error_distribution(metrics["error_dist"])
        result[bucket_id] = public
    return result


def _serialize_bottlenecks(value):
    if isinstance(value, dict):
        return {
            identifier: item
            for key, item in value.items()
            if (identifier := _public_identifier(key)) != "[redacted]"
            and (item is None or isinstance(item, (bool, int, float)))
        }
    if isinstance(value, list):
        return [
            identifier for item in value[:100]
            if (identifier := _public_identifier(item)) != "[redacted]"
        ]
    return {}


def _serialize_slices(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for name, metrics in value.items():
        name_id = _public_identifier(name)
        if name_id == "[redacted]" or not isinstance(metrics, dict):
            continue
        public = {
            str(key)[:80]: item
            for key, item in metrics.items()
            if key != "bottlenecks"
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(key))
            and (item is None or isinstance(item, (bool, int, float)))
        }
        if "bottlenecks" in metrics:
            public["bottlenecks"] = _serialize_bottlenecks(metrics["bottlenecks"])
        result[name_id] = public
    return result


def _serialize_qvt(value) -> dict:
    return (_sanitize_numeric_tree(value) or {}) if isinstance(value, dict) else {}


def _serialize_workflows(value) -> list:
    if not isinstance(value, list):
        return []
    records = []
    for row in value[:100]:
        if not isinstance(row, dict):
            continue
        task_id = _public_identifier(row.get("task_id"))
        if task_id in {None, "[redacted]"}:
            continue
        record = {"task_id": task_id}
        for field in ("stages", "eval_type"):
            if isinstance(row.get(field), list):
                record[field] = [
                    identifier for item in row[field][:100]
                    if (identifier := _public_identifier(item)) not in {None, "[redacted]"}
                ]
        records.append(record)
    return records


def _serialize_workflow_aggregate(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("bottleneck_distribution"), dict):
        result["bottleneck_distribution"] = _serialize_bottlenecks(
            value["bottleneck_distribution"],
        )
    if isinstance(value.get("stage_summary"), dict):
        stage_summary = {}
        for stage_id, record in value["stage_summary"].items():
            public_stage = _public_identifier(stage_id)
            if public_stage in {None, "[redacted]"} or not isinstance(record, dict):
                continue
            public = {}
            for field in ("task_type", "actor_class"):
                if field in record:
                    public[field] = _public_identifier(record[field])
            for field in ("status_counts", "metrics", "signals"):
                if field in record:
                    public[field] = _sanitize_numeric_tree(record[field]) or {}
            stage_summary[public_stage] = public
        result["stage_summary"] = stage_summary
    return result


def _serialize_stage_metrics(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for stage_id, record in value.items():
        public_stage = _public_identifier(stage_id)
        if public_stage == "[redacted]" or not isinstance(record, dict):
            continue
        public = {}
        for field in ("iteration", "valid_num", "total_items"):
            if record.get(field) is None or isinstance(record.get(field), (bool, int, float)):
                public[field] = record.get(field)
        for field in ("task_type", "status", "id"):
            if field in record:
                public[field] = _public_identifier(record[field])
        for field in ("metrics", "timing"):
            if field in record:
                public[field] = _sanitize_numeric_tree(record[field]) or {}
        result[public_stage] = public
    return result


def _serialize_sampling(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get("mode") in {"slice", "random"}:
        result["mode"] = value["mode"]
    for field in ("limit", "seed"):
        if value.get(field) is None or isinstance(value.get(field), (int, float)):
            result[field] = value.get(field)
    return result


def _sanitize_evolution_record(value, *, stages: bool = False):
    if not isinstance(value, dict):
        return {}
    allowed = _EVOLUTION_STAGES if stages else _EVOLUTION_FIELDS
    result = {}
    for key, item in value.items():
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if normalized not in allowed or _forbidden_comparison_key(normalized):
            continue
        if isinstance(item, dict):
            result[normalized] = _sanitize_evolution_record(item)
        elif isinstance(item, list):
            result[normalized] = [
                sanitized for entry in item[:100]
                if (sanitized := _sanitize_evolution_scalar(entry, normalized)) is not None
            ]
        else:
            result[normalized] = _sanitize_evolution_scalar(item, normalized)
    return result


def _sanitize_evolution_scalar(value, field):
    if field in _EVOLUTION_NUMERIC_FIELDS:
        return value if value is None or isinstance(value, (bool, int, float)) else None
    if field in _EVOLUTION_ARTIFACT_FIELDS:
        return _public_artifact_ref(value)
    if field in _EVOLUTION_IDENTIFIER_FIELDS:
        return _public_identifier(value)
    return None


def _serialize_comparison_run(
        scores: dict,
        job: dict | None = None,
        source: str = "artifact",
        artifact_ref: str | None = None,
) -> dict:
    sample_ids = [
        str(row.get("instance_id")) for row in (scores.get("per_sample") or [])
        if row.get("instance_id") is not None
    ]
    sample_hash = hashlib.sha256("\n".join(sample_ids).encode("utf-8")).hexdigest()[:16]
    raw_aggregate = scores.get("aggregate") or {}
    aggregate = _serialize_aggregate_metrics(raw_aggregate)
    workflow = scores.get("workflow_trace") or {}
    serialized = {
        "ex_variant": run_evidence(scores)["ex_variant"],
        "process": run_evidence(scores)["process"],
        "sample_evidence": [{key: value for key, value in row.items() if key != "question"} for row in run_evidence(scores)["sample_evidence"]],
        "run_id": _public_identifier(scores.get("run_id")),
        "method": _public_identifier(scores.get("method")),
        "dataset": _public_identifier(scores.get("dataset")),
        "split": _public_identifier(scores.get("split")),
        "scope": _public_identifier(scores.get("scope")),
        "sample_count": _sanitize_public_scalar(scores.get("sample_count")),
        "timestamp": _public_timestamp(scores.get("timestamp")),
        "source": source if source in _PUBLIC_SOURCES else "[redacted]",
        "artifact_ref": _public_artifact_ref(artifact_ref),
        "sampling": _serialize_sampling(_sampling_metadata(scores, job)),
        "sample_hash": sample_hash,
        "aggregate": aggregate,
        "stage_metrics": _serialize_stage_metrics(scores.get("stage_metrics") or {}),
        "workflow": {
            "workflows": _serialize_workflows(workflow.get("workflows") or []),
            "aggregate": _serialize_workflow_aggregate(workflow.get("aggregate") or {}),
        },
        "by_hardness": _serialize_hardness(scores.get("by_hardness") or {}),
        "by_sql_feature": _serialize_slices(scores.get("by_sql_feature") or {}),
        "by_scenario": _serialize_slices(scores.get("by_scenario") or {}),
        "qvt": _serialize_qvt(scores.get("qvt") or {}),
        "token": _sanitize_numeric_tree(raw_aggregate.get("token") or {}) or {},
        "errors": _serialize_error_distribution(raw_aggregate.get("error_root_distribution") or {}),
        "latency": _sanitize_numeric_tree(_latency_summary(scores, job)) or {},
    }
    for field in ("weakness_profile", "evolution_record"):
        if field in scores and scores[field] is not None:
            serialized[field] = _sanitize_evolution_record(
                scores[field],
                stages=field == "evolution_record",
            )
    return serialized


def _comparison_payload(runs: list[dict], expected_methods: list[str], comparison_id: str | None = None) -> dict:
    runs.sort(key=lambda item: expected_methods.index(item["method"]) if item["method"] in expected_methods else len(expected_methods))
    hashes = {run.get("sample_hash") for run in runs if run.get("sample_hash")}
    counts = {run.get("sample_count") for run in runs if run.get("sample_count") is not None}
    sampling_keys = {
        (run.get("dataset"), run.get("split"), run.get("scope"), *(run.get("sampling") or {}).values())
        for run in runs
    }
    complete = bool(expected_methods) and {run.get("method") for run in runs} >= set(expected_methods)
    aligned = complete and len(hashes) == 1 and len(counts) == 1 and len(sampling_keys) == 1
    sampling = runs[0].get("sampling") if runs else {}
    return {
        "comparison_id": comparison_id,
        "expected_methods": expected_methods,
        "missing_methods": [method for method in expected_methods if method not in {run.get("method") for run in runs}],
        "sampling": {
            "dataset": runs[0].get("dataset") if runs else None,
            "split": runs[0].get("split") if runs else None,
            **(sampling or {}),
        },
        "sample_alignment": {
            "aligned": aligned,
            "complete": complete,
            "sample_count": next(iter(counts)) if len(counts) == 1 else None,
            "hash": next(iter(hashes)) if aligned else None,
        },
        "runs": runs,
    }


def _read_scores(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _artifact_comparison(
        dataset: str,
        methods: list[str],
        split: str,
        sample_mode: str,
        sample_limit: int,
        sample_seed: int,
) -> dict:
    candidates = []
    for path in artifacts_dir().glob(f"{dataset}-*/scores.json"):
        scores = _read_scores(path)
        if not scores or scores.get("dataset") != dataset or scores.get("method") not in methods:
            continue
        if (scores.get("config_snapshot") or {}).get("generate_num") != 1:
            continue
        sampling = _sampling_metadata(scores)
        if scores.get("split") != split:
            continue
        if sampling.get("mode") != sample_mode or sampling.get("limit") != sample_limit:
            continue
        if sample_mode == "random" and sampling.get("seed") != sample_seed:
            continue
        candidates.append(_serialize_comparison_run(
            scores,
            artifact_ref=f"workspace/artifacts/{path.parent.name}/scores.json",
        ))

    groups: dict[tuple, list[dict]] = {}
    for run in candidates:
        sampling = run.get("sampling") or {}
        signature = (
            run.get("dataset"), run.get("split"), run.get("scope"), sampling.get("mode"), sampling.get("limit"),
            sampling.get("seed"), run.get("sample_hash"),
        )
        existing = groups.setdefault(signature, [])
        previous = next((item for item in existing if item.get("method") == run.get("method")), None)
        if previous is None:
            existing.append(run)
        elif str(run.get("timestamp") or "") > str(previous.get("timestamp") or ""):
            existing[existing.index(previous)] = run
    selected = max(
        groups.values(),
        key=lambda group: (len({run.get("method") for run in group}), max(str(run.get("timestamp") or "") for run in group)),
        default=[],
    )
    return _comparison_payload(selected, methods)


def _display_path(path: Path) -> str:
    """Prefer a repo-relative path; then workspace-relative; absolute only as last resort."""
    resolved = path.resolve()
    for root in (_project_root, workspace_root()):
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    return str(resolved)


def _resolve_job_log_path(job: dict) -> Path:
    raw = str(job.get("log_path") or "")
    job_id = str(job.get("job_id") or "")
    if not raw:
        return _run_dir / job_id / "run.log"
    path = Path(raw)
    if path.is_absolute():
        return path
    project_candidate = _project_root / path
    if project_candidate.exists() or path.parts[:1] == ("workspace",) or path.parts[:1] == ("tmp",):
        return project_candidate
    # External SQURVE_WORKSPACE_DIR storage may persist workspace-relative paths.
    return workspace_root() / path


def _checkpoint_state_path(job: dict) -> Path:
    run_id = f"{job['dataset']}-{job['method']}-{job['job_id']}"
    return workspace_run_dir(run_id) / "checkpoints" / "state.json"


def _job_has_checkpoint(job: dict) -> bool:
    return _checkpoint_state_path(job).is_file()


def _job_resumable(job: dict) -> bool:
    """Mirror reproduce ``--resume``: failed/cancelled jobs with a checkpoint can continue."""
    return job.get("status") in {"failed", "cancelled"} and _job_has_checkpoint(job)


def _summarize_run_progress(log_text: str, job: dict | None = None) -> dict:
    """Derive Board progress from a full reproduce run log.

    Sample completion is ``样本 <id> 处理完成``; ``样本 <id> @ <stage>`` only marks
    stage entry and must not count as finished work.
    """
    text = log_text or ""
    job = job or {}
    target = int(job.get("sample_limit") or 0)
    started = set(re.findall(r"开始处理样本\s+(\S+)", text))
    finished = set(re.findall(r"样本\s+(\S+)\s+处理完成", text))
    stage_hits = re.findall(r"样本\s+(\S+)\s+@\s+(\S+)", text)
    for sample_id, _stage in stage_hits:
        started.add(sample_id)
    current_stage = stage_hits[-1][1] if stage_hits else "准备中"

    report_total = None
    report_match = re.search(r"评估结果[^\n]*\n\s*样本\s+(\d+)\s*条", text)
    if report_match:
        report_total = int(report_match.group(1))
        current_stage = "评估完成"

    total = target or report_total or max(len(started), len(finished), 1)
    completed = len(finished)
    if job.get("status") == "completed" or report_total is not None:
        completed = max(completed, report_total or total)
        if job.get("status") == "completed":
            current_stage = "评估完成"
    completed = min(completed, total)
    started_count = max(len(started), completed)
    percent = 100 if job.get("status") == "completed" else min(100, round(100 * completed / total))
    return {
        "current_stage": current_stage,
        "started": started_count,
        "completed": completed,
        "total": total,
        "percent": percent,
    }


def _public_job(job: dict) -> dict:
    # Keep filesystem locations server-side; absolute workstation paths must not reach the UI.
    public = {
        key: value
        for key, value in job.items()
        if key not in {"scores_path", "log_path"}
    }
    public["checkpoint_present"] = _job_has_checkpoint(job)
    public["resumable"] = _job_resumable(job)
    scores_path = Path(job.get("scores_path") or "")
    if scores_path.is_file():
        scores = _read_scores(scores_path)
        if scores:
            public["result"] = _summarize_scores(scores_path)
            public["run_id"] = public["result"].get("run_id") or public.get("run_id")
            public["artifact"] = _serialize_comparison_run(
                scores,
                job=job,
                source="session",
                artifact_ref=(
                    f"session:{scores.get('run_id') or job.get('run_id') or job.get('job_id')}"
                    "/scores.json"
                ),
            )
    return public


def _job_state_path(job_id: str) -> Path:
    return _run_dir / job_id / "job.json"


def _persist_job(job: dict) -> None:
    path = _job_state_path(job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _job_environment(job: dict) -> dict[str, str]:
    score_dir = _run_dir / job["job_id"] / "score-bundle"
    child_env = os.environ.copy()
    child_env["SQURVE_EVAL_OUTPUT_DIR"] = str(score_dir)
    child_env["SQURVE_EVAL_RUN_ID"] = f"{job['dataset']}-{job['method']}-{job['job_id']}"
    child_env["SQURVE_EVAL_SAMPLE_LIMIT"] = str(job["sample_limit"])
    child_env["SQURVE_EVAL_SAMPLE_MODE"] = job["sample_mode"]
    child_env["SQURVE_EVAL_SAMPLE_SEED"] = str(job["sample_seed"])
    child_env["SQURVE_EVAL_SCOPE"] = "smoke"
    credential = _job_credentials.get(job["job_id"])
    if credential is not None:
        _apply_credential_env(child_env, credential)
    child_env.pop(STAGE_ACTORS_ENV, None)
    if job.get("stage_overrides"):
        child_env[STAGE_ACTORS_ENV] = json.dumps(job["stage_overrides"])
    return child_env


def _spawn_evaluation_job(
    job_id: str,
    *,
    resume: bool,
    expected_status: str,
) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != expected_status:
            return False
        job_dir = _run_dir / job_id
        log_path = job_dir / "run.log"
        scores_path = job_dir / "score-bundle" / "scores.json"
        checkpoint_path = _checkpoint_state_path(job)
        python = _project_root / ".venv" / "bin" / "python"
        executable = str(python if python.exists() else Path(sys.executable))
        command = [executable, "reproduce/run.py", job["dataset"], job["method"]]
        if resume:
            command.extend(["--resume-from", str(checkpoint_path)])
            job["resume_count"] = int(job.get("resume_count", 0)) + 1
            job["resumed_at"] = time.time()
        log_handle = None
        try:
            log_handle = log_path.open("a" if resume else "w", encoding="utf-8")
            if resume:
                kind = "manual" if job.get("resume_mode") == "manual" else "autonomous"
                log_handle.write(
                    f"\n[demo] {kind} resume {job['resume_count']}/{job.get('max_resume_attempts', _max_resume_attempts)} "
                    f"from {_display_path(checkpoint_path)} "
                    f"(reproduce/run.py --resume-from)\n"
                )
                log_handle.flush()
            process = subprocess.Popen(
                command,
                cwd=_project_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=_job_environment(job),
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            if log_handle is not None:
                log_handle.close()
            job.update({
                "status": "failed",
                "finished_at": time.time(),
                "launch_error": f"Evaluation process could not be started: {type(exc).__name__}",
            })
            _persist_job(job)
            return False
        job.update({"status": "running", "pid": process.pid, "return_code": None})
        job.pop("finished_at", None)
        _processes[job_id] = process
        _persist_job(job)
    threading.Thread(
        target=_monitor_job,
        args=(job_id, process, log_handle, scores_path),
        daemon=True,
    ).start()
    return True


def _resume_job_after_backoff(job_id: str) -> None:
    if _resume_backoff_seconds:
        time.sleep(_resume_backoff_seconds)
    _spawn_evaluation_job(job_id, resume=True, expected_status="resuming")


def _pid_is_running(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _pid_matches_job(pid, job: dict) -> bool:
    """Confirm a restored PID still belongs to this evaluation command."""
    if not _pid_is_running(pid):
        return False
    try:
        command_path = Path(f"/proc/{int(pid)}/cmdline")
        if command_path.is_file():
            command = command_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        else:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(int(pid))],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            command = result.stdout
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False
    expected = ("reproduce/run.py", str(job.get("dataset", "")), str(job.get("method", "")))
    return all(token and token in command for token in expected)


def _watch_restored_process(job_id: str, pid: int) -> None:
    while True:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job or job.get("status") != "running" or job.get("pid") != pid:
                return
            job_snapshot = dict(job)
        matches = _pid_matches_job(pid, job_snapshot)
        if not matches:
            break
        time.sleep(1)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != "running" or job.get("pid") != pid:
            return
        scores_path = _run_dir / job_id / "score-bundle" / "scores.json"
        if scores_path.is_file():
            job["status"] = "completed"
            job["scores_path"] = str(scores_path)
            job["result"] = _summarize_scores(scores_path)
            job["run_id"] = job["result"].get("run_id")
            job["finished_at"] = time.time()
            _persist_job(job)
            return
        if (
            _checkpoint_state_path(job).is_file()
            and int(job.get("resume_count", 0)) < _max_resume_attempts
        ):
            job["status"] = "resuming"
            job["next_resume_at"] = time.time() + _resume_backoff_seconds
            _persist_job(job)
            should_resume = True
        else:
            job["status"] = "failed"
            job["finished_at"] = time.time()
            job["recovery_error"] = "The restored evaluation exited without a score bundle."
            _persist_job(job)
            should_resume = False
    if should_resume:
        _resume_job_after_backoff(job_id)


def _job_with_progress(job: dict) -> dict:
    public = _public_job(dict(job))
    log_path = _resolve_job_log_path(job)
    log_text = ""
    if log_path.is_file():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
    public["progress"] = _summarize_run_progress(log_text, job)
    return public


@app.get("/api/session")
def session_state():
    owner = _browser_session(create=False)[0] if _hosted() else None
    with _jobs_lock:
        jobs = [
            _job_with_progress(job) for job in _jobs.values()
            if not _hosted() or (owner and _job_owners.get(job["job_id"]) == owner)
        ]
    jobs.sort(key=lambda item: item.get("started_at", 0), reverse=True)
    return jsonify({"jobs": jobs})


def _monitor_job(job_id: str, process: subprocess.Popen, log_handle, scores_path: Path):
    return_code = process.wait()
    log_handle.close()
    should_resume = False
    with _jobs_lock:
        job = _jobs[job_id]
        checkpoint_path = _checkpoint_state_path(job)
        can_resume = (
            job.get("status") != "cancelled"
            and return_code != 0
            and checkpoint_path.is_file()
            and int(job.get("resume_count", 0)) < _max_resume_attempts
        )
        if can_resume:
            job["status"] = "resuming"
            job["last_return_code"] = return_code
            job["next_resume_at"] = time.time() + _resume_backoff_seconds
            should_resume = True
        elif job.get("status") != "cancelled":
            job["status"] = "completed" if return_code == 0 and scores_path.exists() else "failed"
        job["return_code"] = return_code
        if not should_resume:
            job["finished_at"] = time.time()
        if scores_path.exists():
            job["scores_path"] = str(scores_path)
            job["result"] = _summarize_scores(scores_path)
            job["run_id"] = job["result"].get("run_id")
        _processes.pop(job_id, None)
        if not should_resume and job.get("status") != "failed":
            _job_credentials.pop(job_id, None)
        _persist_job(job)
    if should_resume:
        threading.Thread(target=_resume_job_after_backoff, args=(job_id,), daemon=True).start()


def _restore_evaluation_jobs_once() -> None:
    global _jobs_restored
    if _jobs_restored:
        return
    with _job_restore_lock:
        if _jobs_restored:
            return
        _jobs_restored = True
        for path in sorted(_run_dir.glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(job, dict) or not job.get("job_id"):
                continue
            with _jobs_lock:
                _jobs[job["job_id"]] = job
            if (
                job.get("status") in {"running", "resuming"}
                and _pid_matches_job(job.get("pid"), job)
            ):
                job["status"] = "running"
                threading.Thread(
                    target=_watch_restored_process,
                    args=(job["job_id"], int(job["pid"])),
                    daemon=True,
                ).start()
            elif (
                job.get("status") in {"running", "resuming"}
                and _checkpoint_state_path(job).is_file()
                and int(job.get("resume_count", 0)) < _max_resume_attempts
            ):
                with _jobs_lock:
                    job["status"] = "resuming"
                    job["next_resume_at"] = time.time()
                    _persist_job(job)
                threading.Thread(target=_resume_job_after_backoff, args=(job["job_id"],), daemon=True).start()
            elif job.get("status") in {"running", "resuming"}:
                with _jobs_lock:
                    job["status"] = "failed"
                    job["finished_at"] = time.time()
                    job["recovery_error"] = "No resumable checkpoint was found after API restart."
                    _persist_job(job)


def _launch_evaluation(
        dataset: str,
        method: str,
        comparison_id: str | None = None,
        sample_limit: int = 100,
        sample_mode: str = "slice",
        sample_seed: int = 42,
        stages: dict[str, str] | None = None,
        *,
        owner: str | None = None,
        credential: SqlCredential | None = None,
) -> dict:
    configs = _config_catalog()
    config = next((item for item in configs if item["dataset"] == dataset and item["method"] == method), None)
    if not config:
        raise ValueError(f"Unknown reproduce configuration: {method} / {dataset}")
    stages = dict(stages or {})

    job_id = uuid.uuid4().hex[:10]
    job_dir = _run_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": job_id,
        "dataset": dataset,
        "method": method,
        "config": config,
        "comparison_id": comparison_id,
        "sample_limit": sample_limit,
        "sample_mode": sample_mode,
        "sample_seed": sample_seed,
        "stages": stages,
        "stage_overrides": runner_overrides(_project_root, configs, config, stages) if stages else {},
        "status": "starting",
        "pid": None,
        "log_path": _display_path(job_dir / "run.log"),
        "started_at": time.time(),
        "run_id": None,
        "resume_count": 0,
        "max_resume_attempts": _max_resume_attempts,
    }
    with _jobs_lock:
        _jobs[job_id] = job
        if owner is not None:
            _job_owners[job_id] = owner
        if credential is not None:
            _job_credentials[job_id] = credential
        _persist_job(job)
    if not _spawn_evaluation_job(job_id, resume=False, expected_status="starting"):
        raise RuntimeError("Evaluation launch was cancelled before the process started.")
    with _jobs_lock:
        return _public_job(dict(_jobs[job_id]))


@app.post("/api/evaluations")
def start_evaluation():
    payload = request.get_json(silent=True) or {}
    validation = _sampling_request(payload)
    if len(validation) == 2:
        return validation
    sample_limit, sample_mode, sample_seed = validation
    binding = None
    if _hosted():
        binding, denied = _hosted_run_binding()
        if denied is not None:
            return denied
        if sample_limit > _HOSTED_MAX_SAMPLES:
            return _json_error(f"Hosted runs are limited to {_HOSTED_MAX_SAMPLES} questions.")
        denied = _hosted_capacity_error(binding[0])
        if denied is not None:
            return denied
    try:
        job = _launch_evaluation(
            str(payload.get("dataset", "")),
            str(payload.get("method", "")),
            str(payload.get("comparison_id")) if payload.get("comparison_id") else None,
            sample_limit,
            sample_mode,
            sample_seed,
            owner=binding[0] if binding else None,
            credential=binding[1] if binding else None,
        )
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify(job), 202


@app.post("/api/comparisons")
def start_comparison():
    payload = request.get_json(silent=True) or {}
    pairs = payload.get("pairs") or []
    validation = _sampling_request(payload)
    if len(validation) == 2:
        return validation
    sample_limit, sample_mode, sample_seed = validation

    if not isinstance(pairs, list) or not 2 <= len(pairs) <= 6:
        return _json_error("Select between 2 and 6 method-benchmark pairs.")
    normalized = []
    seen = set()
    catalog = {(item["dataset"], item["method"]) for item in _config_catalog()}
    for pair in pairs:
        key = (str(pair.get("dataset", "")), str(pair.get("method", "")))
        if key not in catalog:
            return _json_error(f"Unknown reproduce configuration: {key[1]} / {key[0]}")
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    if len(normalized) < 2:
        return _json_error("A comparison requires at least two distinct configurations.")
    comparison_id = uuid.uuid4().hex[:8]
    try:
        jobs = [
            _launch_evaluation(dataset, method, comparison_id, sample_limit, sample_mode, sample_seed)
            for dataset, method in normalized
        ]
    except RuntimeError:
        return _json_error("One or more evaluation processes could not be started.", 503)
    return jsonify({"comparison_id": comparison_id, "jobs": jobs}), 202


@app.get("/api/comparisons/<comparison_id>/results")
def comparison_results(comparison_id: str):
    with _jobs_lock:
        jobs = [dict(job) for job in _jobs.values() if job.get("comparison_id") == comparison_id]
    if not jobs:
        return _json_error("Comparison not found in the current session.", 404)
    expected_methods = list(dict.fromkeys(job.get("method") for job in jobs if job.get("method")))
    runs = []
    for job in jobs:
        scores_path = Path(job.get("scores_path") or "")
        if job.get("status") != "completed" or not scores_path.is_file():
            continue
        scores = _read_scores(scores_path)
        if scores:
            runs.append(_serialize_comparison_run(
                scores,
                job=job,
                source="session",
                artifact_ref=f"session:{scores.get('run_id') or job.get('run_id') or job.get('job_id')}/scores.json",
            ))
    payload = _comparison_payload(runs, expected_methods, comparison_id=comparison_id)
    payload["statuses"] = {job.get("method"): job.get("status") for job in jobs if job.get("method")}
    return jsonify(payload)


@app.get("/api/comparisons/latest/results")
def latest_comparison_results():
    dataset = str(request.args.get("dataset", "spider")).strip()
    split = str(request.args.get("split", "dev")).strip()
    methods = [item.strip() for item in str(request.args.get("methods", "c3sql")).split(",") if item.strip()]
    sample_mode = str(request.args.get("sample_mode", "random")).strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", dataset) or not re.fullmatch(r"[A-Za-z0-9_-]+", split):
        return _json_error("Dataset and split must be simple identifiers.")
    if not methods or len(methods) > 6 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", method) for method in methods):
        return _json_error("Methods must contain between one and six simple identifiers.")
    if sample_mode not in {"slice", "random"}:
        return _json_error("Sample mode must be slice or random.")
    try:
        sample_limit = int(request.args.get("sample_limit", 100))
        sample_seed = int(request.args.get("sample_seed", 42))
    except (TypeError, ValueError):
        return _json_error("Sample limit and seed must be integers.")
    if sample_limit not in _sample_limits:
        return _json_error("Sample limit must be 3, 10, 20, 50, 100, or 200.")
    return jsonify(_artifact_comparison(dataset, list(dict.fromkeys(methods)), split, sample_mode, sample_limit, sample_seed))


def _sampling_request(payload: dict):
    sample_limit = payload["sample_limit"] if "sample_limit" in payload else 100
    sample_mode = str(payload.get("sample_mode", "slice")).lower()
    if sample_mode not in {"slice", "random"}:
        return _json_error("Sample mode must be slice or random.")
    try:
        sample_seed = int(payload.get("sample_seed", 42))
    except (TypeError, ValueError):
        return _json_error("Sample seed must be an integer.")
    try:
        sample_limit = int(sample_limit)
    except (TypeError, ValueError):
        return _json_error("Sample limit must be an integer.")
    if sample_limit not in _sample_limits:
        return _json_error("Sample limit must be 3, 10, 20, 50, 100, or 200.")
    return sample_limit, sample_mode, sample_seed


@app.get("/api/evaluations/<job_id>")
def evaluation_detail(job_id: str):
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        return _json_error("Job not found.", 404)
    log_path = _resolve_job_log_path(job)
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    # Progress must use the full log; the UI only receives a truncated tail for display.
    job["progress"] = _summarize_run_progress(log_text, job)
    job["log"] = log_text[-20000:]
    return jsonify(_public_job(job))


def _evaluation_llm_preflight(dataset: str, method: str) -> None:
    """Raise ValueError when the active provider cannot launch this config."""
    load_dotenv(_project_root / ".env")
    config_path = _project_root / "reproduce" / "configs" / dataset / f"{method}.json"
    if not config_path.is_file():
        raise ValueError(f"Unknown reproduce configuration: {method} / {dataset}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Reproduce configuration is unreadable: {exc}") from exc
    if _hosted():
        return  # the key is the visitor's session credential, checked when the run starts
    from reproduce.lib.env_config import prepare_runtime_llm_config

    resolved = prepare_runtime_llm_config(config)
    ready, message = api_key_ready(resolved)
    if not ready:
        raise ValueError(message or "LLM provider is not ready for evaluation.")


def _terminate_job_process(process: subprocess.Popen, job_id: str) -> None:
    """Stop an evaluation child process and drop it from the live registry."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
    finally:
        _processes.pop(job_id, None)


# --- Run timeline: the live run as per-question, per-stage events -------------------

# Hosted runs show question text and gold SQL only for benchmarks the Space already publishes.
_HOSTED_PUBLIC_BENCHMARKS = {"spider"}
_TIMELINE_SQL_LIMIT = 4000
_RUN_DIR_IN_LOG = re.compile(r"[/\\]runs[/\\]([A-Za-z0-9._-]+)[/\\]")


def _timeline_execute(db_id: str, sql: str) -> dict:
    """Run one read-only statement on a benchmark database; never raises."""
    started = time.monotonic()
    database = _find_database(db_id)
    if not database:
        return {"ok": False, "error": "database not available", "ms": 0}
    try:
        _validate_readonly_sql(sql)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "ms": 0}
    deadline = started + 5.0
    connection = sqlite3.connect(f"file:{quote(str(Path(database['db_path']).resolve()))}?mode=ro", uri=True)
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        rows = connection.execute(sql).fetchmany(501)
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "ms": round((time.monotonic() - started) * 1000, 2)}
    finally:
        connection.close()
    return {"ok": True, "rows": rows, "ms": round((time.monotonic() - started) * 1000, 2)}


def _run_questions(log_text: str) -> list[dict]:
    """The sampled questions of a run, from the data source the run itself wrote."""
    match = _RUN_DIR_IN_LOG.search(log_text or "")
    if not match:
        return []
    for path in sorted((workspace_run_dir(match.group(1)) / "data_source").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


_JUDGE_CACHE_SIZE = 64
_job_judges: OrderedDict = OrderedDict()


def _judge_for(job_id: str):
    """One judge per job, so a finished prediction is executed once however often it is polled."""
    if job_id not in _job_judges:
        _job_judges[job_id] = make_judge(_timeline_execute)
        while len(_job_judges) > _JUDGE_CACHE_SIZE:
            _job_judges.popitem(last=False)
    _job_judges.move_to_end(job_id)
    return _job_judges[job_id]


def _job_timeline(job: dict, since: int) -> dict:
    log_path = _resolve_job_log_path(job)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    timeline = build_run_timeline(
        _run_questions(log_text), log_text,
        method=str(job.get("method") or ""), judge=_judge_for(str(job.get("job_id"))), since=since,
    )
    if _hosted() and str(job.get("dataset")) not in _HOSTED_PUBLIC_BENCHMARKS:
        withhold_content(timeline)
    cap_text(timeline, _TIMELINE_SQL_LIMIT)
    return {"job_id": job.get("job_id"), "dataset": job.get("dataset"), "status": job.get("status"), **timeline}


def _since_argument() -> int:
    try:
        return max(0, int(request.args.get("since", 0)))
    except ValueError:
        return 0


@app.get("/api/evaluations/<job_id>/timeline")
def evaluation_timeline(job_id: str):
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        return _json_error("Job not found.", 404)
    return jsonify(_job_timeline(job, _since_argument()))


@app.get("/api/experiments/<experiment_id>/timeline")
def experiment_timeline(experiment_id: str):
    try:
        snapshot = _experiments.get(experiment_id)
    except ExperimentNotFound:
        return _json_error("Experiment not found.", 404)
    since = _since_argument()
    cells = []
    for cell in snapshot.get("cells", []):
        with _jobs_lock:
            job = dict(_jobs.get(cell.get("job_id") or "") or {})
        timeline = _job_timeline(job, since) if job else {"questions": [], "total": 0, "cursor": 0, "status": cell.get("status")}
        cells.append({
            "cell_id": cell.get("cell_id"), "method": cell.get("method"), "dataset": cell.get("dataset"),
            **{key: value for key, value in timeline.items() if key not in {"method", "dataset"}},
        })
    cursor = min((cell["cursor"] for cell in cells), default=0)
    return jsonify({
        "experiment_id": snapshot.get("experiment_id"), "status": snapshot.get("status"),
        "cursor": cursor, "cells": cells,
    })


@app.post("/api/evaluations/<job_id>/cancel")
def cancel_evaluation(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        process = _processes.get(job_id)
        if not job:
            return _json_error("Evaluation not found.", 404)
        if job.get("status") not in {"running", "resuming"}:
            return _json_error("Only a running or resuming evaluation can be cancelled.", 409)
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        _persist_job(job)
        if process is not None:
            _terminate_job_process(process, job_id)
        _processes.pop(job_id, None)
        return jsonify(_public_job(dict(job)))


@app.post("/api/evaluations/<job_id>/resume")
def resume_evaluation(job_id: str):
    """Manual resume aligned with ``python reproduce/run.py … --resume-from <checkpoint>``."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return _json_error("Evaluation not found.", 404)
        if job.get("status") in {"running", "resuming", "starting"}:
            return _json_error("Evaluation is already active.", 409)
        if not _job_resumable(job):
            return _json_error(
                "Only a failed or cancelled evaluation with a checkpoint can be resumed.",
                409,
            )
        dataset = str(job["dataset"])
        method = str(job["method"])
    try:
        _evaluation_llm_preflight(dataset, method)
    except ValueError as exc:
        return _json_error(str(exc))
    if _hosted():
        binding, denied = _hosted_run_binding()
        if denied is not None:
            return denied
        _job_credentials[job_id] = binding[1]
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or not _job_resumable(job):
            return _json_error(
                "Only a failed or cancelled evaluation with a checkpoint can be resumed.",
                409,
            )
        job["status"] = "resuming"
        job["resume_mode"] = "manual"
        job.pop("finished_at", None)
        job.pop("recovery_error", None)
        job.pop("launch_error", None)
        _persist_job(job)
    if not _spawn_evaluation_job(job_id, resume=True, expected_status="resuming"):
        return _json_error("Evaluation resume could not be started.", 503)
    with _jobs_lock:
        return jsonify(_public_job(dict(_jobs[job_id]))), 202


# --- Experiments -------------------------------------------------------------


class _EvaluationJobBackend:
    """Runs experiment cells as ordinary Demo evaluation jobs."""

    def launch(self, method: str, dataset: str, sampling: dict, experiment_id: str, stages: dict[str, str]) -> str:
        bound = getattr(_launch_binding, "value", None)
        if bound is not None:
            _experiment_owners.setdefault(experiment_id, bound[0])
            _experiment_credentials.setdefault(experiment_id, bound[1])
        job = _launch_evaluation(
            dataset, method, f"exp-{experiment_id}", sampling["limit"], sampling["mode"], sampling["seed"],
            stages=stages,
            owner=_experiment_owners.get(experiment_id),
            credential=_experiment_credentials.get(experiment_id),
        )
        return job["job_id"]

    def job(self, job_id: str) -> dict | None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            return _job_with_progress(job) if job else None

    def scores(self, job_id: str) -> dict | None:
        with _jobs_lock:
            path = Path((_jobs.get(job_id) or {}).get("scores_path") or "")
        return _read_scores(path) if path.is_file() else None

    def cancel(self, job_id: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job or job.get("status") not in {"running", "resuming", "starting"}:
                return
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            _persist_job(job)
            process = _processes.get(job_id)
            if process is not None:
                _terminate_job_process(process, job_id)

    def resume(self, job_id: str) -> tuple[bool, str | None]:
        response = resume_evaluation(job_id)
        body, status = response if isinstance(response, tuple) else (response, response.status_code)
        if status >= 400:
            return False, (body.get_json(silent=True) or {}).get("message")
        return True, None


_experiments = ExperimentScheduler(
    _EvaluationJobBackend(),
    concurrency=max(1, int(os.environ.get("SQURVE_EXPERIMENT_CONCURRENCY", "2"))),
)
_experiment_ticker_started = False
_experiment_ticker_lock = threading.Lock()


def _start_experiment_ticker() -> None:
    """Advance queues in the background so cells start without a client polling."""
    global _experiment_ticker_started
    if app.config.get("TESTING"):
        return
    with _experiment_ticker_lock:
        if _experiment_ticker_started:
            return
        _experiment_ticker_started = True

    def run() -> None:
        while True:
            time.sleep(1.0)
            try:
                _experiments.tick()
                _prune_experiment_credentials()
            except Exception:  # noqa: BLE001 - keep the ticker alive across one bad tick
                logger.exception("Experiment queue tick failed")

    threading.Thread(target=run, name="experiment-ticker", daemon=True).start()


def _experiment_sampling(payload: dict):
    sampling = payload.get("sampling") or {}
    if not isinstance(sampling, dict):
        return None
    validation = _sampling_request({
        "sample_limit": sampling.get("limit", 100),
        "sample_mode": sampling.get("mode", "slice"),
        "sample_seed": sampling.get("seed", 42),
    })
    if len(validation) == 2:
        return None
    limit, mode, seed = validation
    return {"limit": limit, "mode": mode, "seed": seed}


def _cell_preflight(method: str, dataset: str, catalog: set[tuple[str, str]]) -> str | None:
    if (dataset, method) not in catalog:
        return "config_missing"
    try:
        _evaluation_llm_preflight(dataset, method)
    except ValueError:
        return "credential_missing"
    return None


@app.post("/api/experiments")
def create_experiment():
    payload = request.get_json(silent=True) or {}
    raw_cells = payload.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        return _json_error("Select at least one method × benchmark cell.")
    binding = None
    if _hosted():
        binding, denied = _hosted_run_binding()
        if denied is not None:
            return denied
        if len(raw_cells) > _HOSTED_MAX_CELLS:
            return _json_error(f"Hosted experiments can hold at most {_HOSTED_MAX_CELLS} cells.")
        denied = _hosted_capacity_error(binding[0])
        if denied is not None:
            return denied
    configs = _config_catalog()
    actors_by_type = swappable_actors(configs)
    cells: list[tuple[str, str, dict[str, str]]] = []
    invalid = []
    for item in raw_cells:
        if not isinstance(item, dict):
            return _json_error("Each cell needs a method and a dataset.")
        method, dataset = str(item.get("method") or "").strip(), str(item.get("dataset") or "").strip()
        config = next((entry for entry in configs if (entry["method"], entry["dataset"]) == (method, dataset)), None)
        try:
            stages = normalize_stages(config or {}, item.get("stages"), actors_by_type)
        except VariantError as exc:
            invalid.append({"method": method, "dataset": dataset, "stages": exc.problems})
            continue
        if not any((method, dataset, stages) == existing for existing in cells):
            cells.append((method, dataset, stages))
    if invalid:
        summary = ", ".join(
            f"{item['method']}/{item['dataset']} {problem['stage']} ({problem['reason']})"
            for item in invalid for problem in item["stages"]
        )
        return jsonify({"status": "error", "code": "invalid_stages",
                        "message": f"Some stage swaps are not allowed: {summary}.", "cells": invalid}), 400
    if len(cells) > MAX_CELLS:
        return _json_error(f"An experiment can hold at most {MAX_CELLS} cells.")
    sampling = _experiment_sampling(payload)
    if sampling is None:
        return _json_error("Sampling needs limit 3, 10, 20, 50, 100, or 200, mode slice or random, and an integer seed.")
    catalog = {(item["dataset"], item["method"]) for item in configs}
    rejected = [
        {"method": method, "dataset": dataset, "error_code": code}
        for method, dataset in dict.fromkeys((method, dataset) for method, dataset, _stages in cells)
        if (code := _cell_preflight(method, dataset, catalog))
    ]
    if rejected:
        summary = ", ".join(f"{item['method']}/{item['dataset']} ({item['error_code']})" for item in rejected)
        return jsonify({"status": "error", "message": f"Some cells cannot run yet: {summary}.", "cells": rejected}), 400
    if binding is not None and sampling["limit"] > _HOSTED_MAX_SAMPLES:
        return _json_error(f"Hosted runs are limited to {_HOSTED_MAX_SAMPLES} questions.")
    _launch_binding.value = binding
    try:
        experiment = _experiments.create(cells, sampling)
    finally:
        _launch_binding.value = None
    _start_experiment_ticker()
    return jsonify(experiment), 202


@app.get("/api/experiments")
def list_experiments():
    experiments = _experiments.list()
    if _hosted():
        owner = _browser_session(create=False)[0]
        experiments = [
            item for item in experiments
            if owner and _experiment_owners.get(item.get("experiment_id")) == owner
        ]
    return jsonify({"experiments": experiments})


@app.get("/api/experiments/<experiment_id>")
def experiment_detail(experiment_id: str):
    try:
        return jsonify(_experiments.get(experiment_id))
    except ExperimentNotFound:
        return _json_error("Experiment not found.", 404)



@app.get("/api/experiments/<experiment_id>/events")
def experiment_events(experiment_id: str):
    try:
        cursor = max(0, int(request.args.get("cursor", 0)))
    except ValueError:
        return _json_error("cursor must be an integer.")
    try:
        events, next_cursor = _experiments.events(experiment_id, cursor)
        snapshot = _experiments.get(experiment_id)
    except ExperimentNotFound:
        return _json_error("Experiment not found.", 404)
    return jsonify({**snapshot, "events": events, "cursor": next_cursor})


@app.post("/api/experiments/<experiment_id>/cancel")
def cancel_experiment(experiment_id: str):
    try:
        return jsonify(_experiments.cancel(experiment_id))
    except ExperimentNotFound:
        return _json_error("Experiment not found.", 404)


@app.post("/api/experiments/<experiment_id>/cells/<cell_id>/cancel")
def cancel_experiment_cell(experiment_id: str, cell_id: str):
    try:
        return jsonify(_experiments.cancel(experiment_id, cell_id))
    except ExperimentNotFound:
        return _json_error("Experiment or cell not found.", 404)


@app.post("/api/experiments/<experiment_id>/cells/<cell_id>/resume")
def resume_experiment_cell(experiment_id: str, cell_id: str):
    try:
        return jsonify(_experiments.resume(experiment_id, cell_id)), 202
    except ExperimentNotFound:
        return _json_error("Experiment or cell not found.", 404)
    except ExperimentConflict as exc:
        return _json_error(str(exc), 409)


# --- Results: cross-benchmark comparison -------------------------------------
# Every number here is a derived evaluation signal read from a run's own
# scores (run-local evidence); nothing is recomputed or filled in.

_METRIC_LABELS = {
    "ex": "Execution accuracy",
    "em": "Exact match",
    "sf1": "Structure F1",
    "sc": "Self-consistency",
    "ves": "Valid efficiency score",
    "rves": "Relative VES",
}
# Simple ids; a dot is allowed but never "..", since some ids end up in file paths.
_IDENTIFIER = re.compile(r"(?!.*\.\.)[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
_MAX_MATRIX_AXIS = 12


def _identifier_list(raw: str, maximum: int = _MAX_MATRIX_AXIS) -> list[str] | None:
    values = list(dict.fromkeys(item.strip() for item in str(raw or "").split(",") if item.strip()))
    if not values or len(values) > maximum or not all(_IDENTIFIER.fullmatch(item) for item in values):
        return None
    return values


def _run_metric_values(scores: dict) -> tuple[dict, dict]:
    values, not_computable = {}, {}
    for key, item in (scores.get("aggregate") or {}).items():
        if not _IDENTIFIER.fullmatch(str(key)):
            continue
        # Local runs report {"avg": …}; exported evidence bundles flatten to the number.
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            values[key] = float(item)
            continue
        if not isinstance(item, dict) or "avg" not in item:
            continue
        average = item.get("avg")
        if isinstance(average, (int, float)) and not isinstance(average, bool):
            values[key] = float(average)
        else:
            values[key] = None
            not_computable[key] = str(item.get("note") or "not_computable")[:160]
    return values, not_computable


def _run_cost(scores: dict) -> dict:
    aggregate = scores.get("aggregate") or {}
    token = aggregate.get("token") or {}
    latency = (aggregate.get("latency") or {}).get("avg_s")
    # A run that recorded no LLM calls did not track tokens; that is unknown, not zero.
    tokens = token.get("total_tokens") if token.get("total_calls") else None
    if not isinstance(latency, (int, float)):
        sample_latency = [
            row["latency_ms"] / 1000 for row in scores.get("per_sample") or []
            if isinstance(row.get("latency_ms"), (int, float))
        ]
        latency = sum(sample_latency) / len(sample_latency) if sample_latency else None
    return {
        "tokens_total": tokens if isinstance(tokens, (int, float)) else None,
        "latency_s": float(latency) if isinstance(latency, (int, float)) else None,
    }


def _sample_hash(scores: dict) -> str:
    # Sorted, so runs over the same questions align whatever order they were recorded in.
    # Without per-question rows the questions are unknown, so there is nothing to align on.
    ids = sorted(str(row.get("instance_id")) for row in scores.get("per_sample") or [] if row.get("instance_id") is not None)
    if not ids:
        return None
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]


def _load_run_scores(run_dir: Path) -> dict | None:
    """A run's scores, with per-question rows from an evidence bundle's diagnostics when present."""
    scores = _read_scores(run_dir / "scores.json")
    if not scores or scores.get("per_sample"):
        return scores
    diagnostics = run_dir / "sample-diagnostics.jsonl"
    if diagnostics.is_file():
        rows = []
        try:
            for line in diagnostics.read_text(encoding="utf-8").splitlines():
                record = json.loads(line) if line.strip() else None
                if isinstance(record, dict) and record.get("instance_id") is not None:
                    rows.append({
                        "instance_id": record["instance_id"],
                        "latency_ms": record.get("latency_ms"),
                        **{key: value for key, value in (record.get("metrics") or {}).items() if key in {"ex", "em"}},
                    })
        except (OSError, ValueError):
            rows = []
        scores = {**scores, "per_sample": rows}
    return scores


def _matrix_cell(method: str, dataset: str, scores: dict | None, source: str | None, status: str) -> dict:
    cell = {
        "method": method,
        "dataset": dataset,
        "stages": _scores_stages(scores) if scores else {},
        "status": status,
        "values": {},
        "not_computable": {},
        "cost": {"tokens_total": None, "latency_s": None},
        "sample_count": None,
        "sample_hash": None,
        "run_id": None,
        "source": source,
        "timestamp": None,
    }
    if scores:
        values, not_computable = _run_metric_values(scores)
        cell.update({
            "values": values,
            "not_computable": not_computable,
            "cost": _run_cost(scores),
            "sample_count": _sanitize_public_scalar(scores.get("sample_count")),
            "sample_hash": _sample_hash(scores),
            "ex_variant": run_evidence(scores)["ex_variant"],
        "process": run_evidence(scores)["process"],
        "sample_evidence": [{key: value for key, value in row.items() if key != "question"} for row in run_evidence(scores)["sample_evidence"]],
        "run_id": _public_identifier(scores.get("run_id")),
            "timestamp": _public_timestamp(scores.get("timestamp")),
        })
    return cell


def _sampling_matches(scores: dict, job: dict | None, split: str, sampling: dict) -> bool:
    if scores.get("split") != split:
        return False
    meta = _sampling_metadata(scores, job)
    mode = meta.get("mode") or "slice"
    if mode != sampling["mode"] or meta.get("limit") != sampling["limit"]:
        return False
    return mode != "random" or meta.get("seed") == sampling["seed"]


def _scores_stages(scores: dict) -> dict[str, str]:
    stages = scores.get("stage_overrides")
    if not isinstance(stages, dict):
        return {}
    return {str(stage): str(actor) for stage, actor in sorted(stages.items())}


def _scores_variant(scores: dict) -> str:
    return variant_key(str(scores.get("method")), _scores_stages(scores))


def _archived_scores(split: str, sampling: dict, source_filter: str | None = None) -> dict[tuple[str, str], tuple[dict, str]]:
    """Latest archived run per (method, dataset) with the requested sampling, and where it came from."""
    latest: dict[tuple[str, str], tuple[dict, str]] = {}
    for source, run_dir in _archive_run_dirs():
        if source_filter == "recorded" and source != "evidence":
            continue
        if source_filter == "archive" and source == "evidence":
            continue
        scores = _load_run_scores(run_dir)
        if not scores:
            continue
        job = _read_scores(run_dir.parent / "job.json") if source == "demo-runs" else None
        if not _sampling_matches(scores, job, split, sampling):
            continue
        # A variant run never stands in for its method's own configuration.
        key = (_scores_variant(scores), str(scores.get("dataset")))
        previous = latest.get(key)
        if previous is None or str(scores.get("timestamp") or "") > str(previous[0].get("timestamp") or ""):
            # Demo runs live in this workspace's session store; everything else is archived evidence.
            latest[key] = (scores, "session" if source == "demo-runs" else "archive")
    return latest


def _mark_alignment(cells: list[dict], datasets: list[str]) -> dict:
    alignment = {}
    for dataset in datasets:
        measured = [cell for cell in cells if cell["dataset"] == dataset and cell["status"] == "ok" and cell["sample_hash"]]
        hashes = Counter(cell["sample_hash"] for cell in measured)
        if not hashes:
            alignment[dataset] = {"aligned": False, "sample_hash": None, "methods": []}
            continue
        # Cells with no per-question rows cannot be shown to share questions with anything.
        for cell in cells:
            if cell["dataset"] == dataset and cell["status"] == "ok" and not cell["sample_hash"]:
                cell["status"] = "unaligned"
        newest = {cell["sample_hash"]: str(cell.get("timestamp") or "") for cell in measured}
        reference = max(hashes, key=lambda value: (hashes[value], newest.get(value, "")))
        for cell in measured:
            if cell["sample_hash"] != reference:
                cell["status"] = "unaligned"
        alignment[dataset] = {
            "aligned": len(hashes) == 1,
            "sample_hash": reference,
            "methods": [cell["method"] for cell in measured if cell["sample_hash"] == reference],
        }
    return alignment


def _results_matrix(args) -> tuple[dict | None, tuple | None]:
    experiment_id = str(args.get("experiment_id") or "").strip()
    method_limit = MAX_CELLS if experiment_id else _MAX_MATRIX_AXIS
    methods = _identifier_list(args.get("methods", ""), method_limit)
    datasets = _identifier_list(args.get("datasets", ""))
    if methods is None or datasets is None:
        return None, _json_error(f"methods need 1 to {method_limit} and datasets 1 to {_MAX_MATRIX_AXIS} simple identifiers.")
    split = str(args.get("split", "dev")).strip()
    if not _IDENTIFIER.fullmatch(split):
        return None, _json_error("split must be a simple identifier.")
    if experiment_id:
        try:
            sampling = _experiments.experiment_sampling(experiment_id)
        except ExperimentNotFound:
            return None, _json_error("Experiment not found.", 404)
    else:
        validation = _sampling_request({
            "sample_limit": args.get("sample_limit", 100),
            "sample_mode": args.get("sample_mode", "random"),
            "sample_seed": args.get("sample_seed", 42),
        })
        if len(validation) == 2:
            return None, validation
        limit, mode, seed = validation
        sampling = {"limit": limit, "mode": mode, "seed": seed}

    cells = []
    labels = {}
    archived = {} if experiment_id else _archived_scores(split, sampling, args.get("source"))
    for method in methods:
        for dataset in datasets:
            if experiment_id:
                found = _experiments.cell_job(experiment_id, method, dataset)
                if found is None:
                    cells.append(_matrix_cell(method, dataset, None, None, "missing"))
                    continue
                cell, scores = found
                labels[method] = variant_label(cell.method, cell.stages)
                if scores:
                    cells.append(_matrix_cell(method, dataset, scores, "session", "ok"))
                else:
                    # Execution state is reported as observed; a cancelled run is not a failed one.
                    status = cell.status if cell.status in {"failed", "cancelled"} else "running"
                    cells.append(_matrix_cell(method, dataset, None, "session", status))
            else:
                scores, source = archived.get((method, dataset), (None, None))
                if scores:
                    labels[method] = variant_label(str(scores.get("method")), _scores_stages(scores))
                cells.append(_matrix_cell(method, dataset, scores, source, "ok" if scores else "missing"))

    alignment = _mark_alignment(cells, datasets)
    metric_ids = list(dict.fromkeys(
        [key for key in _METRIC_LABELS if any(key in cell["values"] for cell in cells)]
        + [key for cell in cells for key in cell["values"] if key not in _METRIC_LABELS]
    ))
    for cell in cells:
        cell.pop("timestamp", None)
    return {
        "experiment_id": experiment_id or None,
        "split": split,
        "sampling": sampling,
        "methods": methods,
        # Display names for variant columns; a plain method is its own label.
        "labels": {method: labels.get(method, method) for method in methods},
        "datasets": datasets,
        "metrics": [
            {"id": key, "label": _METRIC_LABELS.get(key, key.upper()), "higher_is_better": True}
            for key in metric_ids
        ],
        "cells": cells,
        "alignment": alignment,
    }, None


@app.get("/api/results/matrix")
def results_matrix():
    payload, error = _results_matrix(request.args)
    return error if error else jsonify(payload)


@app.get("/api/results/matrix.csv")
def results_matrix_csv():
    payload, error = _results_matrix(request.args)
    if error:
        return error
    metric_ids = [metric["id"] for metric in payload["metrics"]]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["method", "dataset", "status", *metric_ids, "tokens_total", "latency_s", "sample_count", "run_id"])
    for cell in payload["cells"]:
        writer.writerow([
            cell["method"], cell["dataset"], cell["status"],
            *("" if cell["values"].get(key) is None else cell["values"][key] for key in metric_ids),
            "" if cell["cost"]["tokens_total"] is None else cell["cost"]["tokens_total"],
            "" if cell["cost"]["latency_s"] is None else cell["cost"]["latency_s"],
            "" if cell["sample_count"] is None else cell["sample_count"],
            cell["run_id"] or "",
        ])
    return app.response_class(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=squrve-results.csv"},
    )


_DIFF_CATEGORIES = ("both_right", "a_only", "b_only", "both_wrong", "unknown")
_DIFF_TEXT_LIMIT = 2000


def _scores_for_run(run_id: str) -> dict | None:
    resolved = _resolve_archive_run(run_id)
    return _load_run_scores(resolved[1]) if resolved else None


def _correctness(row: dict | None) -> bool | None:
    value = (row or {}).get("ex")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= 1
    return None


def _diff_category(a: bool | None, b: bool | None) -> str:
    if a is None or b is None:
        return "unknown"
    if a and b:
        return "both_right"
    if a:
        return "a_only"
    if b:
        return "b_only"
    return "both_wrong"


_benchmark_question_cache: dict[tuple[str, float], dict[str, dict]] = {}


def _benchmark_questions(dataset: str, split: str) -> dict[str, dict]:
    """instance_id → {db_id, question} from an installed benchmark split, or {} when absent."""
    path = _benchmark_split_path(dataset, split)
    if path is None:
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    key = (str(path), mtime)
    if key not in _benchmark_question_cache:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        _benchmark_question_cache.clear()
        _benchmark_question_cache[key] = {
            str(row.get("instance_id")): {"db_id": row.get("db_id"), "question": row.get("question")}
            for row in rows if isinstance(row, dict) and row.get("instance_id") is not None
        }
    return _benchmark_question_cache[key]


def _public_text(value) -> str | None:
    return value[:_DIFF_TEXT_LIMIT] if isinstance(value, str) else None


# --- Comparison snapshots -----------------------------------------------------

def _snapshot_run_data(run_id: str) -> dict | None:
    resolved = _resolve_archive_run(run_id)
    if not resolved:
        return None
    scores = _load_run_scores(resolved[1])
    if not scores:
        return None
    return {
        "dataset": scores.get("dataset"), "split": scores.get("split"), "sampling": _sampling_metadata(scores),
        "values": _run_metric_values(scores)[0], "cost": _run_cost(scores),
        "sample_count": scores.get("sample_count"), "sample_hash": _sample_hash(scores),
        "scores_hash": hashlib.sha256((resolved[1] / "scores.json").read_bytes()).hexdigest(),
    }


@app.post("/api/comparison-snapshots")
def create_comparison_snapshot():
    payload = request.get_json(silent=True) or {}
    try:
        if not isinstance(payload, dict):
            raise ValueError("A comparison snapshot must be an object.")
        members = payload.get("members")
        if isinstance(members, list):
            hydrated = []
            for member in members:
                if not isinstance(member, dict):
                    raise ValueError("Each comparison member must be an object.")
                data = _snapshot_run_data(str(member.get("run_id") or ""))
                if data and (data["dataset"] != payload.get("benchmark") or data["split"] != payload.get("split", "dev")):
                    raise ValueError("Snapshot members must share benchmark and split.")
                if data and payload.get("sampling"):
                    expected = payload["sampling"]
                    actual = data["sampling"]
                    if any(expected.get(key) != actual.get(key) for key in ("limit", "mode")) or (expected.get("mode") == "random" and expected.get("seed") != actual.get("seed")):
                        raise ValueError("Snapshot sampling is not aligned.")
                hydrated.append({**member, "values": {}, "cost": {}, **(data or {})})
            hashes = {member.get("sample_hash") for member in hydrated if member.get("scores_hash")}
            if len(hashes) > 1 or (len(hydrated) > 1 and None in hashes):
                raise ValueError("Snapshot sample fingerprints are not aligned.")
            payload = {**payload, "members": hydrated}
        saved = comparison_snapshots.save(workspace_root(), payload)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify(saved), 201


@app.get("/api/comparison-snapshots")
def list_comparison_snapshots():
    return jsonify({"snapshots": comparison_snapshots.list_snapshots(workspace_root())})


@app.get("/api/comparison-snapshots/<snapshot_id>")
def comparison_snapshot_detail(snapshot_id: str):
    snapshot = comparison_snapshots.load(
        workspace_root(),
        snapshot_id,
        run_exists=lambda run_id: _resolve_archive_run(run_id) is not None,
        resolve_run=_snapshot_run_data,
    )
    if snapshot is None:
        return _json_error("Comparison snapshot not found.", 404)
    return jsonify(snapshot)


@app.get("/api/results/diff")
def results_diff():
    a_id = str(request.args.get("a") or "").strip()
    b_id = str(request.args.get("b") or "").strip()
    if not _safe_archive_run_id(a_id) or not _safe_archive_run_id(b_id):
        return _json_error("a and b must be run identifiers.")
    category = str(request.args.get("category") or "").strip()
    if category and category not in _DIFF_CATEGORIES:
        return _json_error(f"category must be one of: {', '.join(_DIFF_CATEGORIES)}.")
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return _json_error("limit and offset must be integers.")
    a_scores, b_scores = _scores_for_run(a_id), _scores_for_run(b_id)
    if not a_scores or not b_scores:
        return _json_error("Run not found.", 404)
    dataset = str(request.args.get("dataset") or "").strip()
    datasets = {str(a_scores.get("dataset")), str(b_scores.get("dataset"))}
    if len(datasets) != 1 or (dataset and dataset not in datasets):
        return jsonify({"status": "error", "code": "different_benchmarks",
                        "message": "Both runs must be on the same benchmark."}), 409
    a_hash = _sample_hash(a_scores)
    if a_hash is None or a_hash != _sample_hash(b_scores):
        return jsonify({"status": "error", "code": "unaligned",
                        "message": "These runs answered different questions and cannot be compared question by question."}), 409
    # The public Space shows which questions each method got right, not the SQL it generated.
    public_sql = deployment_target() != "hf-space"

    b_rows = {str(row.get("instance_id")): row for row in b_scores.get("per_sample") or []}
    # Exported evidence keeps only ids and metrics; the installed benchmark supplies the question.
    installed = _benchmark_questions(str(a_scores.get("dataset") or ""), str(a_scores.get("split") or "dev"))
    rows = []
    for a_row in a_scores.get("per_sample") or []:
        instance_id = str(a_row.get("instance_id"))
        b_row = b_rows.get(instance_id) or {}
        a_ok, b_ok = _correctness(a_row), _correctness(b_row)
        known = installed.get(instance_id, {})
        db_id = a_row.get("db_id") or b_row.get("db_id") or known.get("db_id")
        rows.append({
            "instance_id": _public_identifier(instance_id),
            "category": _diff_category(a_ok, b_ok),
            "db_id": _public_identifier(db_id) if db_id else None,
            "question": _public_text(a_row.get("question") or b_row.get("question") or known.get("question")),
            "a": {"correct": a_ok, "sql": _public_text(a_row.get("pred_sql")) if public_sql else None},
            "b": {"correct": b_ok, "sql": _public_text(b_row.get("pred_sql")) if public_sql else None},
        })
    summary = {name: sum(1 for row in rows if row["category"] == name) for name in _DIFF_CATEGORIES}
    selected = [row for row in rows if not category or row["category"] == category]

    def side(scores: dict) -> dict:
        return {
            "ex_variant": run_evidence(scores)["ex_variant"],
        "process": run_evidence(scores)["process"],
        "sample_evidence": [{key: value for key, value in row.items() if key != "question"} for row in run_evidence(scores)["sample_evidence"]],
        "run_id": _public_identifier(scores.get("run_id")),
            "method": _public_identifier(scores.get("method")),
            "variant": _public_identifier(_scores_variant(scores)),
            "label": _public_text(variant_label(str(scores.get("method")), _scores_stages(scores))),
            "stages": {
                _public_identifier(stage): _public_identifier(actor) for stage, actor in _scores_stages(scores).items()
            },
            "dataset": _public_identifier(scores.get("dataset")),
        }

    return jsonify({
        "a": side(a_scores),
        "b": side(b_scores),
        "summary": summary,
        "total": len(selected),
        "offset": offset,
        "rows": selected[offset:offset + limit],
    })

@app.post("/api/evaluations/<job_id>/profile")
def profile_evaluation(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return _json_error("Evaluation not found.", 404)
        scores_path = Path(job.get("scores_path", ""))
        if job.get("status") != "completed" or not scores_path.exists():
            return _json_error("A completed score bundle from this session is required.", 409)
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    profile = build_weakness_json(scores, top_n=5)
    profile_path = scores_path.parent.parent / "weakness-profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    with _jobs_lock:
        _jobs[job_id]["weakness_profile"] = profile
    return jsonify({
        "job_id": job_id,
        "status": "profiled",
        "profile": profile,
        "next_gate": "candidate_plan_review",
        "allowed_scope": ["actor", "prompt", "config", "task_method_branch"],
    })


_ARCHIVE_TEXT_SUFFIXES = {".json", ".md", ".txt", ".jsonl", ".log"}
_ARCHIVE_MAX_BYTES = 2_000_000


def _archive_roots() -> list[tuple[str, Path]]:
    return [
        ("evidence", _project_root / "evidence" / "reported-results"),
        ("artifacts", artifacts_dir()),
        ("demo-runs", evaluations_dir()),
    ]


def _safe_archive_run_id(run_id: str) -> str | None:
    value = str(run_id or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value) or ".." in value:
        return None
    return value


def _archive_run_dirs() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    evidence_root = _project_root / "evidence" / "reported-results"
    if evidence_root.is_dir():
        for path in sorted(evidence_root.iterdir()):
            if not path.is_dir():
                continue
            try:
                verify_bundle(path)
            except EvidenceError:
                continue
            found.append(("evidence", path))
    artifacts_root = artifacts_dir()
    if artifacts_root.is_dir():
        for path in sorted(artifacts_root.iterdir()):
            if path.is_dir() and (path / "scores.json").is_file():
                found.append(("artifacts", path))
    demo_root = evaluations_dir()
    if demo_root.is_dir():
        for job_dir in sorted(demo_root.iterdir()):
            bundle = job_dir / "score-bundle"
            if bundle.is_dir() and (bundle / "scores.json").is_file():
                found.append(("demo-runs", bundle))
    return found


def _archive_metric_avg(aggregate: dict, name: str):
    value = (aggregate or {}).get(name)
    if isinstance(value, dict):
        return value.get("avg")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _archive_entry(source: str, run_dir: Path) -> dict | None:
    scores_path = run_dir / "scores.json"
    if not scores_path.is_file():
        return None
    try:
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = str(scores.get("run_id") or run_dir.name)
    if source == "demo-runs":
        run_id = str(scores.get("run_id") or f"demo-{run_dir.parent.name}")
    aggregate = scores.get("aggregate") or {}
    token = aggregate.get("token") or {}
    files = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir).as_posix()
        if path.suffix.lower() not in _ARCHIVE_TEXT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(run_dir).parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append({
            "path": rel,
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size_bytes": size,
            "kind": (
                "markdown" if path.suffix.lower() == ".md"
                else "json" if path.suffix.lower() in {".json", ".jsonl"}
                else "text"
            ),
        })
    sampling = _sampling_metadata(scores)
    return {
        "run_id": run_id,
        **run_evidence(scores, include_questions=deployment_target() != "hf-space"),
        "dir_name": run_dir.name if source in {"artifacts", "evidence"} else run_dir.parent.name,
        "source": source,
        "dataset": scores.get("dataset"),
        "method": scores.get("method"),
        "variant": _scores_variant(scores),
        "label": variant_label(str(scores.get("method")), _scores_stages(scores)),
        "stages": _scores_stages(scores),
        "split": scores.get("split"),
        "scope": scores.get("scope"),
        "sample_count": scores.get("sample_count"),
        "timestamp": scores.get("timestamp"),
        "sampling": sampling,
        "metrics": {
            name: _archive_metric_avg(aggregate, name)
            for name in ("ex", "em", "sf1", "sc", "ves", "rves")
        },
        "token": {
            "total_tokens": token.get("total_tokens"),
            "avg_per_sample": token.get("avg_per_sample"),
            "total_calls": token.get("total_calls"),
        },
        "files": files,
        "file_count": len(files),
        "has_scores": True,
        "has_report": any(item["name"] == "detailed-report.txt" for item in files),
        "has_markdown": any(item["kind"] == "markdown" for item in files),
    }


def _resolve_archive_run(run_id: str) -> tuple[str, Path] | None:
    safe = _safe_archive_run_id(run_id)
    if not safe:
        return None
    for source, run_dir in _archive_run_dirs():
        entry = _archive_entry(source, run_dir)
        if not entry:
            continue
        if entry["run_id"] == safe or entry["dir_name"] == safe:
            return source, run_dir
    return None


def _resolve_archive_file(run_dir: Path, relative: str) -> tuple[Path, Path] | None:
    rel = str(relative or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = run_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file() or path.suffix.lower() not in _ARCHIVE_TEXT_SUFFIXES:
        return None
    return root, path


@app.get("/api/archive")
def archive_catalog():
    query = str(request.args.get("q", "")).strip().lower()
    dataset = str(request.args.get("dataset", "")).strip().lower()
    method = str(request.args.get("method", "")).strip().lower()
    source_filter = str(request.args.get("source", "")).strip().lower()
    runs = []
    for source, run_dir in _archive_run_dirs():
        if source_filter and source != source_filter:
            continue
        entry = _archive_entry(source, run_dir)
        if not entry:
            continue
        haystack = " ".join([
            entry["run_id"],
            entry.get("dir_name") or "",
            str(entry.get("dataset") or ""),
            str(entry.get("method") or ""),
            str(entry.get("scope") or ""),
            " ".join(item["name"] for item in entry["files"]),
        ]).lower()
        if dataset and str(entry.get("dataset") or "").lower() != dataset:
            continue
        if method and str(entry.get("method") or "").lower() != method:
            continue
        if query and query not in haystack:
            continue
        runs.append(entry)
    runs.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    datasets = sorted({item.get("dataset") for item in runs if item.get("dataset")})
    methods = sorted({item.get("method") for item in runs if item.get("method")})
    return jsonify({
        "runs": runs,
        "total": len(runs),
        "filters": {
            "datasets": datasets,
            "methods": methods,
            "sources": ["evidence", "artifacts", "demo-runs"],
        },
    })


@app.get("/api/archive/<run_id>")
def archive_detail(run_id: str):
    resolved = _resolve_archive_run(run_id)
    if not resolved:
        return _json_error("Archive run not found.", 404)
    source, run_dir = resolved
    entry = _archive_entry(source, run_dir)
    if not entry:
        return _json_error("Archive run is unreadable.", 404)
    return jsonify(entry)


@app.get("/api/archive/<run_id>/files/<path:file_path>")
def archive_file(run_id: str, file_path: str):
    resolved = _resolve_archive_run(run_id)
    if not resolved:
        return _json_error("Archive run not found.", 404)
    _, run_dir = resolved
    resolved_file = _resolve_archive_file(run_dir, file_path)
    if not resolved_file:
        return _json_error("Archive file not found or not readable.", 404)
    root, path = resolved_file
    size = path.stat().st_size
    truncated = size > _ARCHIVE_MAX_BYTES
    raw = path.read_bytes()[:_ARCHIVE_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    payload = {
        "run_id": run_id,
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": size,
        "truncated": truncated,
        "kind": (
            "markdown" if path.suffix.lower() == ".md"
            else "json" if path.suffix.lower() in {".json", ".jsonl"}
            else "text"
        ),
        "content": text,
    }
    if path.suffix.lower() == ".json":
        try:
            payload["json"] = json.loads(text)
        except json.JSONDecodeError:
            payload["json"] = None
    return jsonify(payload)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=7861, type=int)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
