"""Retain offline evaluation inputs separately from observed method execution.

Only explicitly opted-in real Evo runs copy SQL, database snapshots and outputs.
The offline verifier reuses saved execution results and never executes SQL.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from core.db_path import resolve_sqlite_file
from core.evidence import read_evidence
from reproduce.eval.protocol.execution import ExecutionResult
from reproduce.eval.protocol.offline import evaluate_checkpoint
from reproduce.evolve.verification import digest, write_json


def usage_for_sample(runtime: dict, sample_ref: str) -> list[dict]:
    spans = {event["span_id"] for event in runtime["events"] if event["type"] == "actor.started" and event.get("sample_ref") == sample_ref}
    reports = []
    for event in runtime["events"]:
        if event["type"] != "usage.reported" or event.get("parent_span_id") not in spans:
            continue
        usage = event.get("usage") or {}
        reports.append({"stage_id": event.get("parent_span_id"), "logical_call_id": event.get("logical_call_id"),
                        "attempt_id": event.get("attempt_id"), "scope": event.get("scope"),
                        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
                        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
                        "coverage": 1 if usage.get("prompt_tokens", usage.get("input_tokens")) is not None and usage.get("completion_tokens", usage.get("output_tokens")) is not None else 0,
                        "source": event.get("source") or "unknown"})
    return reports


def runtime_latency(runtime: dict) -> float | None:
    starts = [event for event in runtime["events"] if event["type"] == "actor.started"]
    ends = [event for event in runtime["events"] if event["type"] == "actor.finished"]
    samples = {event.get("sample_ref") for event in starts if event.get("sample_ref")}
    if not samples or len(starts) != len(ends) or any(not isinstance(event.get("duration_ns"), (int, float)) for event in ends):
        return None
    return sum(event["duration_ns"] for event in ends) / len(samples) / 1e9


def build_evaluation(scores: dict, dataset, directory: Path, config: dict) -> dict | None:
    collection = json.loads(os.environ.get('SQURVE_EVO_COLLECTION', '{}'))
    if not collection.get('capture_sql') or not collection.get('capture_outputs'):
        return None
    if dataset is None:
        return None
    runtime = read_evidence(directory)
    target = directory / 'evaluation'
    target.mkdir(exist_ok=True)
    starts = {event['span_id']: event.get('sample_ref') for event in runtime['events'] if event['type'] == 'actor.started'}
    candidates = [event for event in runtime['events'] if event['type'] == 'candidate.created']
    raw = {key: scores[key] for key in ('run_id', 'dataset', 'split', 'method')}
    raw['model_config_id'] = hashlib.sha256(json.dumps(config.get('llm') or {}, sort_keys=True).encode()).hexdigest()
    raw['samples'] = []
    for index, row in enumerate(scores.get('per_sample') or []):
        if row.get('db_type', 'sqlite') != 'sqlite':
            return None  # An unsupported backend is a declared gap, not fake evidence.
        source = Path(resolve_sqlite_file(dataset.db_path, row.get('db_id', '')))
        if not source.is_file():
            return None
        snapshot_name = digest(source) + '.sqlite'
        snapshot = target / snapshot_name
        if not snapshot.exists():
            size = sum(path.stat().st_size for path in target.glob('*') if path.is_file())
            if size + source.stat().st_size > int(collection.get('evaluation_max_bytes', 67_108_864)):
                write_json(target / 'unavailable.json', {'reason': 'evaluation_retention_limit'})
                return None
            shutil.copyfile(source, snapshot)
        sample_ref = hashlib.sha256(str(row.get('index', index)).encode()).hexdigest()
        matching = [event for event in candidates if event.get('sql_text') == row.get('pred_sql') and starts.get(event.get('producer_span_id') or event.get('parent_span_id')) == sample_ref]
        candidate_id = matching[-1]['candidate_occurrence_id'] if matching else 'unobserved-' + str(index)
        raw['samples'].append({
            'instance_id': str(row['instance_id']), 'db_path': str(snapshot.resolve()),
            'gold_sql': row.get('gold_sql'), 'candidates': [{'candidate_id': candidate_id, 'occurrence_id': candidate_id, 'sql': row.get('pred_sql') or ''}],
            'selected_candidate_id': candidate_id, 'runtime_sample_ref': sample_ref,
            'token_usage': usage_for_sample(runtime, sample_ref),
        })
    write_json(target / 'checkpoint.json', raw)
    evaluation_config = {'comparison': {}}
    write_json(target / 'config.json', evaluation_config)
    # These SQL executions are explicitly evaluation-only and occur outside the
    # EvidenceRun context. Their provenance cannot be confused with the method.
    bundle = evaluate_checkpoint(target / 'checkpoint.json', evaluation_config).model_dump(mode='json')
    bundle['provenance']['runtime_actor_latency_s'] = runtime_latency(runtime)
    write_json(target / 'scores.json', bundle)
    return bundle


def verify_evaluation(directory: Path, scores: dict, runtime: dict) -> list[str]:
    root = directory / 'evaluation'
    issues = []
    try:
        checkpoint = json.loads((root / 'checkpoint.json').read_text())
        config = json.loads((root / 'config.json').read_text())
        saved = json.loads((root / 'scores.json').read_text())
        if saved != scores:
            issues.append('evaluation_score_conflict')
        starts = {event['span_id']: event.get('sample_ref') for event in runtime['events'] if event['type'] == 'actor.started'}
        observed = {event['candidate_occurrence_id']: event for event in runtime['events'] if event['type'] == 'candidate.created'}
        if not checkpoint.get('samples') or checkpoint.get('run_id') != scores.get('run_id'):
            issues.append('missing_evaluation_sample_identity')
        by_id = {row['instance_id']: row for row in scores.get('samples', [])}
        for sample in checkpoint.get('samples', []):
            for candidate in sample['candidates']:
                event = observed.get(candidate['candidate_id'])
                if not event or event.get('sql_text') != candidate['sql'] or starts.get(event.get('producer_span_id') or event.get('parent_span_id')) != sample.get('runtime_sample_ref'):
                    issues.append('unobserved_evaluation_candidate')
            if sample.get('token_usage') != usage_for_sample(runtime, sample.get('runtime_sample_ref')):
                issues.append('runtime_usage_conflict')
            # Use retained content addressed DB snapshots for identity, never SQL.
            original = Path(sample['db_path'])
            path = root / original.name
            if path.suffix != '.sqlite' or not path.is_file() or digest(path) != path.stem:
                issues.append('database_snapshot_unavailable')
            sample['db_path'] = str(path)
        if issues:
            return sorted(set(issues))
        class SavedExecutor:
            def execute_sqlite(self, db_path, sql, *, run_id, instance_id, candidate_id, **kwargs):
                matches = [item for item in by_id[instance_id]['execution_results'] if item['candidate_id'] == candidate_id]
                if len(matches) != 1:
                    raise ValueError('execution_identity_conflict')
                return ExecutionResult.model_validate(matches[0])
        with tempfile.TemporaryDirectory(prefix='evo-offline-') as temporary:
            relocated = Path(temporary) / 'checkpoint.json'
            write_json(relocated, checkpoint)
            recomputed = evaluate_checkpoint(relocated, config, executor=SavedExecutor()).model_dump(mode='json')
        # The checkpoint location is provenance, not a measurement identity.
        recomputed['provenance']['runtime_actor_latency_s'] = runtime_latency(runtime)
        recomputed['provenance']['checkpoint'] = saved['provenance']['checkpoint']
        if recomputed != saved:
            issues.append('offline_metric_recomputation_conflict')
    except (OSError, ValueError, KeyError, TypeError):
        issues.append('offline_evaluation_unavailable')
    return sorted(set(issues))
