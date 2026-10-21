"""Eval-store writer: long metric tables instead of a wide samples table.

Schema v2 changes against ``reproduce.metrics.eval_store``:

- ``sample_metrics (run_id, instance_id, metric_id, value)`` replaces the wide
  ``samples`` table; adding a metric requires no DDL change.
- ``sample_meta`` holds categorical labels (hardness, db_type, error_root, ...).
- Raw question/SQL text moves to ``sample_text``, written only on request and
  never read by exporters; the default store stays publication-neutral.
- ``sql_features`` and ``stage_metrics`` keep their (already long) layouts.

The per-sample numeric columns derive from the metric registry: every engine
metric with a per-sample source contributes one row per sample under its
source leaf name, so the L6 matrix view can query correctness (``ex``) and
latency (``act_elapsed_s``) uniformly.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from reproduce.eval.aggregate.slicing import extract_value
from reproduce.eval.registry import MetricRegistry, default_registry


STORE_SCHEMA_VERSION = 2

META_FIELDS = ("db_id", "db_type", "hardness", "error_root", "error_sub", "exec_error")
TEXT_FIELDS = ("question", "gold_sql", "pred_sql")


def per_sample_sources(registry: MetricRegistry | None = None) -> Dict[str, str]:
    """Map stored metric ids to per-sample dot-path sources (deduplicated)."""
    registry = registry or default_registry()
    sources: Dict[str, str] = {}
    for spec in registry.engine_metrics():
        leaf = spec.source.split(".")[-1]
        sources.setdefault(leaf, spec.source)
    return sources


def persist_eval_store(
        scores: dict[str, Any],
        db_path: str | Path,
        *,
        registry: MetricRegistry | None = None,
        include_text: bool = False,
) -> Path:
    registry = registry or default_registry()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            _init_schema(conn)
            _insert_run(conn, scores)
            _insert_samples(conn, scores, registry, include_text=include_text)
    return db_path


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            method TEXT,
            dataset TEXT,
            split TEXT,
            generate_num INTEGER,
            scope TEXT,
            timestamp TEXT,
            store_schema_version INTEGER,
            scores_json TEXT
        );
        CREATE TABLE IF NOT EXISTS sample_metrics (
            run_id TEXT,
            instance_id TEXT,
            metric_id TEXT,
            value REAL,
            PRIMARY KEY (run_id, instance_id, metric_id)
        );
        CREATE TABLE IF NOT EXISTS sample_meta (
            run_id TEXT,
            instance_id TEXT,
            field TEXT,
            value TEXT,
            PRIMARY KEY (run_id, instance_id, field)
        );
        CREATE TABLE IF NOT EXISTS sample_text (
            run_id TEXT,
            instance_id TEXT,
            field TEXT,
            value TEXT,
            PRIMARY KEY (run_id, instance_id, field)
        );
        CREATE TABLE IF NOT EXISTS sql_features (
            run_id TEXT,
            instance_id TEXT,
            feature TEXT,
            gold_value REAL,
            pred_value REAL,
            delta_value REAL,
            PRIMARY KEY (run_id, instance_id, feature)
        );
        CREATE TABLE IF NOT EXISTS stage_metrics (
            run_id TEXT,
            instance_id TEXT,
            stage_id TEXT,
            task_type TEXT,
            actor_class TEXT,
            status TEXT,
            metric_name TEXT,
            metric_value REAL,
            PRIMARY KEY (run_id, instance_id, stage_id, metric_name)
        );
        CREATE TABLE IF NOT EXISTS metric_results_v2 (
            run_id TEXT,
            instance_id TEXT,
            metric_id TEXT,
            status TEXT,
            value REAL,
            numerator INTEGER,
            denominator INTEGER,
            coverage REAL NOT NULL,
            protocol_id TEXT NOT NULL,
            protocol_version TEXT NOT NULL,
            candidate_scope TEXT NOT NULL,
            candidate_ids_json TEXT NOT NULL,
            reason TEXT,
            provenance_json TEXT NOT NULL,
            PRIMARY KEY (run_id, instance_id, metric_id)
        );
        CREATE TABLE IF NOT EXISTS run_cohorts_v2 (
            run_id TEXT PRIMARY KEY,
            database_snapshot TEXT NOT NULL,
            candidate_protocol TEXT NOT NULL,
            evaluator_protocol TEXT NOT NULL,
            model_config_id TEXT NOT NULL,
            sample_ids_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attribution_v2 (
            run_id TEXT,
            instance_id TEXT,
            ordinal INTEGER,
            protocol_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (run_id, instance_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS component_observations_v2 (
            run_id TEXT,
            instance_id TEXT,
            ordinal INTEGER,
            protocol_id TEXT NOT NULL,
            observation_json TEXT NOT NULL,
            PRIMARY KEY (run_id, instance_id, ordinal)
        );
        """
    )


def _insert_run(conn: sqlite3.Connection, scores: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO runs
        (run_id, method, dataset, split, generate_num, scope, timestamp,
         store_schema_version, scores_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scores.get("run_id"),
            scores.get("method"),
            scores.get("dataset"),
            scores.get("split"),
            scores.get("generate_num"),
            scores.get("scope"),
            scores.get("timestamp"),
            STORE_SCHEMA_VERSION,
            json.dumps({k: v for k, v in scores.items() if k not in {"per_sample", "samples"}},
                       ensure_ascii=False),
        ),
    )


def _insert_samples(
        conn: sqlite3.Connection,
        scores: dict,
        registry: MetricRegistry,
        *,
        include_text: bool,
) -> None:
    if scores.get("schema_version") == 2:
        _insert_v2_samples(conn, scores)
        return
    run_id = scores.get("run_id")
    for table in ("sample_metrics", "sample_meta", "sample_text", "sql_features", "stage_metrics"):
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))

    sources = per_sample_sources(registry)
    for sample in scores.get("per_sample") or []:
        instance_id = sample.get("instance_id")
        if instance_id is None:
            continue
        for metric_id, source in sources.items():
            value = extract_value(sample, source)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                conn.execute(
                    "INSERT OR REPLACE INTO sample_metrics VALUES (?, ?, ?, ?)",
                    (run_id, instance_id, metric_id, float(value)),
                )
        tokens = sample.get("tokens")
        if isinstance(tokens, dict) and tokens:
            total = sum(v for v in tokens.values() if isinstance(v, (int, float)))
            conn.execute(
                "INSERT OR REPLACE INTO sample_metrics VALUES (?, ?, ?, ?)",
                (run_id, instance_id, "tokens_total", float(total)),
            )
        for field in META_FIELDS:
            value = sample.get(field)
            if value is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO sample_meta VALUES (?, ?, ?, ?)",
                    (run_id, instance_id, field, str(value)),
                )
        workflow_attr = ((sample.get("workflow") or {}).get("attribution") or {})
        if workflow_attr.get("root_stage"):
            conn.execute(
                "INSERT OR REPLACE INTO sample_meta VALUES (?, ?, ?, ?)",
                (run_id, instance_id, "workflow_root_stage", str(workflow_attr["root_stage"])),
            )
        if include_text:
            for field in TEXT_FIELDS:
                value = sample.get(field)
                if isinstance(value, str) and value:
                    conn.execute(
                        "INSERT OR REPLACE INTO sample_text VALUES (?, ?, ?, ?)",
                        (run_id, instance_id, field, value),
                    )
        _insert_sql_features(conn, run_id, sample)
        _insert_stage_metrics(conn, run_id, sample)


def _insert_v2_samples(conn: sqlite3.Connection, scores: dict) -> None:
    """Persist v2 without flattening away status, protocol or candidate scope."""
    from reproduce.eval.protocol import EvaluationBundleV2

    bundle = EvaluationBundleV2.model_validate(scores)
    for table in ("sample_metrics", "sample_meta", "sample_text", "sql_features", "stage_metrics"):
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (bundle.run_id,))
    conn.execute("DELETE FROM metric_results_v2 WHERE run_id = ?", (bundle.run_id,))
    conn.execute("DELETE FROM run_cohorts_v2 WHERE run_id = ?", (bundle.run_id,))
    conn.execute("DELETE FROM attribution_v2 WHERE run_id = ?", (bundle.run_id,))
    conn.execute("DELETE FROM component_observations_v2 WHERE run_id = ?", (bundle.run_id,))
    conn.execute(
        "INSERT INTO run_cohorts_v2 VALUES (?, ?, ?, ?, ?, ?)",
        (bundle.run_id, bundle.database_snapshot, bundle.candidate_protocol,
         bundle.evaluator_protocol, bundle.model_config_id,
         json.dumps([sample.instance_id for sample in bundle.samples])),
    )
    for sample in bundle.samples:
        for metric in sample.metrics.values():
            conn.execute(
                "INSERT INTO metric_results_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bundle.run_id, sample.instance_id, metric.metric_id, metric.status.value,
                 metric.value, metric.numerator, metric.denominator, metric.coverage,
                 metric.protocol_id, metric.protocol_version, metric.candidate_scope,
                 json.dumps(metric.candidate_ids), metric.reason,
                 json.dumps(metric.provenance, ensure_ascii=False)),
            )
            if metric.value is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO sample_metrics VALUES (?, ?, ?, ?)",
                    (bundle.run_id, sample.instance_id, metric.metric_id, metric.value),
                )
        for ordinal, attribution in enumerate(sample.attribution):
            conn.execute(
                "INSERT INTO attribution_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
                (bundle.run_id, sample.instance_id, ordinal,
                 attribution.get("attribution_protocol", "unknown"),
                 attribution.get("rule_id", "unknown"), attribution.get("candidate_id", "unknown"),
                 json.dumps(attribution, ensure_ascii=False)),
            )
        for ordinal, observation in enumerate(sample.component_observations):
            conn.execute(
                "INSERT INTO component_observations_v2 VALUES (?, ?, ?, ?, ?)",
                (bundle.run_id, sample.instance_id, ordinal,
                 observation.get("protocol_id", "unknown"),
                 json.dumps(observation, ensure_ascii=False)),
            )


def load_v2_run_metric(db_path: str | Path, run_id: str, metric_id: str) -> Dict[str, float]:
    """Load one metric from exactly one run; never merge latest values."""
    result: Dict[str, float] = {}
    with closing(sqlite3.connect(db_path)) as conn:
        for instance_id, value in conn.execute(
                "SELECT instance_id, value FROM metric_results_v2 "
                "WHERE run_id = ? AND metric_id = ? AND status = 'available'",
                (run_id, metric_id)):
            result[str(instance_id)] = float(value)
    return result


def _insert_sql_features(conn: sqlite3.Connection, run_id: str, sample: dict) -> None:
    features = sample.get("sql_features") or {}
    gold = features.get("gold") or {}
    pred = features.get("pred") or {}
    delta = features.get("delta") or {}
    for feature in sorted(set(gold) | set(pred) | set(delta)):
        conn.execute(
            "INSERT OR REPLACE INTO sql_features VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sample.get("instance_id"),
                feature,
                _num(gold.get(feature)),
                _num(pred.get(feature)),
                _num(delta.get(feature)),
            ),
        )


def _insert_stage_metrics(conn: sqlite3.Connection, run_id: str, sample: dict) -> None:
    stages = ((sample.get("workflow") or {}).get("stages") or {})
    for stage_id, payload in stages.items():
        metrics = payload.get("metrics") or {}
        if not metrics:
            metrics = {"__status__": None}
        for metric_name, metric_value in metrics.items():
            conn.execute(
                "INSERT OR REPLACE INTO stage_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    sample.get("instance_id"),
                    stage_id,
                    payload.get("task_type"),
                    payload.get("actor_class"),
                    payload.get("status"),
                    metric_name,
                    _num(metric_value),
                ),
            )


def load_sample_metric(
        db_path: str | Path,
        metric_id: str,
        *,
        dataset: Optional[str] = None,
        split: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """Return {method: {instance_id: value}} for one metric across runs.

    When a method has several runs, the latest timestamp wins per sample.
    """
    query = """
        SELECT r.method, m.instance_id, m.value, r.timestamp
        FROM sample_metrics m JOIN runs r ON r.run_id = m.run_id
        WHERE m.metric_id = ?
    """
    params: List[Any] = [metric_id]
    if dataset is not None:
        query += " AND r.dataset = ?"
        params.append(dataset)
    if split is not None:
        query += " AND r.split = ?"
        params.append(split)
    query += " ORDER BY r.timestamp"

    result: Dict[str, Dict[str, float]] = {}
    with closing(sqlite3.connect(db_path)) as conn:
        for method, instance_id, value, _timestamp in conn.execute(query, params):
            result.setdefault(method, {})[str(instance_id)] = value
    return result


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None
