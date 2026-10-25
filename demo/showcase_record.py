"""Showcase metrics record: archived real runs condensed into a publishable record.

Input is a directory of run batches (``<batch>/<method>/`` holding the job record, the
score bundle and the run log). Output is one deterministic, schema-versioned record
with every metric group, per-run validity and cross-method comparisons.

Only aggregate metrics, per-question outcomes and anonymous ordinals are kept: question
text, SQL, logs, absolute paths and provider payloads never reach the record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = 1
QUALITY_METRICS = ("ex", "em", "sf1", "sc", "ves", "rves")
# Declared before looking at any results: a run whose model calls mostly failed is not a method result.
MAX_DEGENERATE_RATE = 0.2
DEGENERATE_SQL = {"", "select"}
REQUIRED_METRICS = ("ex", "em")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(raw: dict | None) -> dict:
    """Uniform metric: ok with value/counts, or unavailable with the reason it cannot be computed."""
    raw = raw or {}
    if raw.get("avg") is None:
        return {"status": "unavailable", "value": None, "reason": raw.get("note") or "not_computable"}
    total = raw.get("total")
    valid = raw.get("valid")
    metric = {
        "status": "ok", "value": raw["avg"], "numerator": raw.get("pass_count"), "denominator": valid,
        "total": total, "coverage": (valid / total) if total else None,
    }
    if raw.get("interval") is not None:
        metric["interval"] = raw["interval"]
    return metric


def _observed(value) -> dict:
    if value is None:
        return {"status": "unavailable", "value": None, "reason": "not_observed"}
    return {"status": "ok", "value": value}


def _breakdown(rows: dict | None, keys: tuple[str, ...]) -> dict:
    return {name: {key: row.get(key) for key in ("count", *keys)} for name, row in (rows or {}).items()}


def _token_spread(by_step: dict) -> dict:
    """Spread of tokens over the questions that made model calls (step names carry sample ids, so only totals stay)."""
    totals = sorted(step["total_tokens"] for step in (by_step or {}).values() if step.get("total_tokens") is not None)
    if not totals:
        return {"samples": 0, "min": None, "median": None, "max": None}
    middle = len(totals) // 2
    median = totals[middle] if len(totals) % 2 else (totals[middle - 1] + totals[middle]) / 2
    return {"samples": len(totals), "min": totals[0], "median": float(median), "max": totals[-1]}


def _stages(aggregate: dict) -> dict:
    return {
        name: {"task_type": stage.get("task_type"),
               "metrics": {metric: _metric(value) for metric, value in (stage.get("metrics") or {}).items()}}
        for name, stage in (aggregate.get("stage") or {}).items()
    }


def _diagnostics(log: str, samples: list[dict], model_calls: int | None) -> dict:
    degenerate = sum(1 for sample in samples if (sample.get("pred_sql") or "").strip().rstrip(";").lower() in DEGENERATE_SQL)
    rate = degenerate / len(samples) if samples else 0.0
    return {
        "connection_errors": len(re.findall(r"Connection error", log)),
        "retries_exhausted": len(re.findall(r"All \d+ LLM attempts failed|failed after \d+ attempts", log)),
        "fallbacks_to_empty": len(re.findall(r"falling back to 'SELECT'", log)),
        "model_calls": model_calls,
        "degenerate_predictions": degenerate,
        "degenerate_rate": round(rate, 4),
        "valid": rate < MAX_DEGENERATE_RATE,
    }




def _read_run(directory: Path) -> tuple[dict, dict]:
    """The job record and score bundle of a run, or a ValueError naming what is missing."""
    label = f"{directory.parent.name}/{directory.name}"
    try:
        job = _load(directory / "job.json")
        scores = _load(directory / "score-bundle" / "scores.json")
    except (OSError, ValueError) as error:
        raise ValueError(f"{label}: unreadable run ({error})") from error
    aggregate = scores.get("aggregate") or {}
    for name in REQUIRED_METRICS:
        if not isinstance(aggregate.get(name), dict):
            raise ValueError(f"{label}: score bundle has no aggregate metric '{name}'")
    if not scores.get("per_sample"):
        raise ValueError(f"{label}: score bundle has no per-question results")
    return job, scores


def _summarize_run(directory: Path) -> tuple[dict, list[dict]]:
    job, scores = _read_run(directory)
    log = (directory / "run.log").read_text(encoding="utf-8", errors="replace")
    aggregate = scores["aggregate"]
    token = aggregate.get("token") or {}
    latency = aggregate.get("latency") or {}
    llm = (scores.get("config_snapshot") or {}).get("llm") or {}
    summary = {
        "source": {
            "run_ref": f"{directory.parent.name}/{directory.name}",
            "llm": {"provider": llm.get("provider"), "model": llm.get("model")},
            "recorded_on": str(scores.get("timestamp") or "")[:10] or None,
            "dataset": scores["dataset"], "split": scores["split"],
            "wall_seconds": round(job["finished_at"] - job["started_at"], 1) if job.get("finished_at") else None,
        },
        "sampling": {"limit": job["sample_limit"], "mode": job["sample_mode"], "seed": job["sample_seed"],
                     "sample_count": scores["sample_count"]},
        "quality": {name: _metric(aggregate.get(name)) for name in QUALITY_METRICS},
        "cost": {
            "total_calls": token.get("total_calls"), "prompt_tokens": token.get("total_prompt_tokens"),
            "completion_tokens": token.get("total_completion_tokens"), "total_tokens": token.get("total_tokens"),
            "avg_tokens_per_sample": token.get("avg_per_sample"),
            "latency": {key: latency.get(key) for key in ("avg_s", "p50_s", "p95_s")},
            "tokens_per_sample": _token_spread(token.get("by_step")),
        },
        "component_f1": {name.removeprefix("cf1_"): (value or {}).get("avg") for name, value in (aggregate.get("cf1") or {}).items()},
        "error_roots": {name: {"count": row["count"], "pct": row["pct"]} for name, row in (aggregate.get("error_root_distribution") or {}).items()},
        "by_hardness": _breakdown(scores.get("by_hardness"), ("ex", "em")),
        "by_db_type": _breakdown(scores.get("by_db_type"), ("ex", "em")),
        "by_sql_feature": _breakdown(scores.get("by_sql_feature"), ("ex", "em", "sf1", "ves")),
        "by_scenario": _breakdown(scores.get("by_scenario"), ("ex", "em", "sf1", "ves")),
        "structure_delta": {name.removeprefix("delta_"): {"mean": row.get("mean"), "std": row.get("std")}
                            for name, row in (aggregate.get("fd") or {}).items()},
        "stages": _stages(aggregate),
        "process": {name: _observed(value) for name, value in (aggregate.get("process") or {}).items()},
        "bottlenecks": ((scores.get("workflow_trace") or {}).get("aggregate") or {}).get("bottleneck_distribution"),
        "diagnostics": _diagnostics(log, scores["per_sample"], token.get("total_calls")),
    }
    return summary, scores["per_sample"]


def _pairwise(valid: list[str], outcomes: dict[str, list[int]]) -> list[dict]:
    pairs = []
    for i, a in enumerate(valid):
        for b in valid[i + 1:]:
            counts = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
            for first, second in zip(outcomes[a], outcomes[b]):
                counts["both" if first and second else "only_a" if first else "only_b" if second else "neither"] += 1
            pairs.append({"a": a, "b": b, **counts})
    return pairs


def _build_batch(batch: Path) -> dict:
    runs, samples = {}, {}
    for directory in sorted(path for path in batch.iterdir() if path.is_dir()):
        runs[directory.name], samples[directory.name] = _summarize_run(directory)
    identities = {name: [sample["instance_id"] for sample in rows] for name, rows in samples.items()}
    first_ids = next(iter(identities.values()))
    if any(ids != first_ids for ids in identities.values()):
        raise ValueError(f"{batch.name}: runs must have answered the same questions, in the same order")
    # A short fingerprint proves the runs shared one sample without publishing the identifiers.
    fingerprint = hashlib.sha256("|".join(first_ids).encode()).hexdigest()[:12]
    valid = [name for name, run in runs.items() if run["diagnostics"]["valid"]]
    ranking = sorted(valid, key=lambda name: (-(runs[name]["quality"]["ex"]["value"] or 0), runs[name]["cost"]["total_tokens"] or 0, name))
    outcomes = {name: [sample["ex"] for sample in rows] for name, rows in samples.items()}
    first = next(iter(samples.values()))
    matrix = [
        {"q": f"q{i + 1}", "hardness": sample["hardness"], "ex": {name: outcomes[name][i] for name in runs}}
        for i, sample in enumerate(first)
    ]
    return {
        "id": batch.name, "sample_count": len(first_ids),
        "alignment": {"aligned": True, "fingerprint": fingerprint},
        "validity_rule": {
            "max_degenerate_rate": MAX_DEGENERATE_RATE, "degenerate_means": "an empty prediction or a bare SELECT",
            "invalid_when": "the degenerate rate reaches the maximum",
        },
        "runs": runs,
        "ranking": ranking, "invalid_runs": [name for name in runs if name not in valid],
        "pairwise": _pairwise(sorted(valid), outcomes), "question_matrix": matrix,
    }


def build_record(root: Path) -> dict:
    root = Path(root)
    batches = [_build_batch(path) for path in sorted(root.iterdir()) if path.is_dir()]
    return {"schema_version": SCHEMA_VERSION, "batches": batches}


def render_module(record: dict) -> str:
    """The record as a JavaScript module the Demo App imports; stable output for a stable input."""
    body = json.dumps(record, ensure_ascii=False, indent=1)
    return f"// Generated by demo/showcase_record.py; do not edit.\nexport default {body}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the showcase metrics record from archived runs.")
    parser.add_argument("root", type=Path, help="directory of run batches (<batch>/<method>/)")
    parser.add_argument("out", type=Path, help="JavaScript module to write")
    args = parser.parse_args(argv)
    built = build_record(args.root)
    args.out.write_text(render_module(built), encoding="utf-8")
    for batch in built["batches"]:
        print(f"{batch['id']}: ranking={batch['ranking']} invalid={batch['invalid_runs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
