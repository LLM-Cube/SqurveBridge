"""Versioned, offline Evo acceptance. Measurements and scheduling sentinels differ.

This module makes no model or database calls. Run-local evidence is authoritative;
legacy scores remain inspectable but cannot pass this strict gate without evidence.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from core.evidence import read_evidence, aggregate_usage
from reproduce.eval.bundle.compare import compare_scores

VERSION = "evo-acceptance-v1"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def freeze_policy(root: Path, policy: dict) -> dict:
    """Write once, before running. Resumption cannot change the contract."""
    if policy.get("version") != VERSION or not policy.get("required_metrics"):
        raise ValueError("A versioned policy with required_metrics is mandatory")
    if not isinstance(policy.get("weights"), dict) or not policy["weights"]:
        raise ValueError("Explicit fitness weights are mandatory")
    if any(not finite(weight) or weight < 0 for weight in policy["weights"].values()):
        raise ValueError("Weights must be finite and nonnegative")
    allowed = {"ex", "em", "sf1", "ves", "rves", "cost", "latency", "regression", "hard_slice"}
    if (set(policy["weights"]) | set(policy["required_metrics"])) - allowed:
        raise ValueError("Unsupported acceptance metric")
    for key in ("minimum_improvement", "max_regression_rate", "tolerance"):
        if not finite(policy.get(key)) or policy[key] < 0:
            raise ValueError(f"Explicit nonnegative {key} is mandatory")
    dependencies = policy.get("evidence_dependencies") or {}
    active = set(policy["required_metrics"]) | {key for key, weight in policy["weights"].items() if weight > 0}
    if not all(isinstance(dependencies.get(key), list) and "runtime" in dependencies[key] for key in active):
        raise ValueError("Every active metric must declare evidence dependencies")
    for key, required in (("cost", "usage"), ("latency", "actor_timing")):
        if key in active and required not in dependencies[key]:
            raise ValueError(f"{key} requires {required} evidence")
    budget = (policy.get("budget") or {}).get("rollouts")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ValueError("A positive stage rollout budget is mandatory")
    selection = policy.get("sample_selection") or {}
    if selection.get("mode") != "fixed_ids" or not isinstance(selection.get("sample_ids"), list) or not selection["sample_ids"] or len(set(selection["sample_ids"])) != len(selection["sample_ids"]):
        raise ValueError("Predeclare the fixed sample ids for this stage")
    path = root / "acceptance-policy.json"
    if path.exists() and json.loads(path.read_text()) != policy:
        raise ValueError("Acceptance policy is immutable after execution starts")
    write_json(path, policy)
    return policy


def sample_map(scores: dict, metric: str) -> dict:
    if scores.get("schema_version", 1) == 2:
        metric = {"ex": "ex_at_1", "em": "structural_match"}.get(metric, metric)
        return {str(row["instance_id"]): (row.get("metrics", {}).get(metric) or {}).get("value")
                for row in scores.get("samples", [])
                if (row.get("metrics", {}).get(metric) or {}).get("status") == "available"}
    return {str(row["instance_id"]): row.get(metric) for row in scores.get("per_sample", [])
            if row.get("instance_id") is not None}


def metric_value(scores: dict, metric: str, tolerance: float) -> tuple[float | None, str | None]:
    name = {"ex": "ex_at_1", "em": "structural_match"}.get(metric, metric) if scores.get("schema_version") == 2 else metric
    item = (scores.get("aggregate") or {}).get(name)
    value = item.get("value") if isinstance(item, dict) and scores.get("schema_version") == 2 else item.get("avg") if isinstance(item, dict) else item
    if not finite(value):
        return None, "missing_metric"
    rows = sample_map(scores, metric)
    if not rows or any(not finite(value) for value in rows.values()):
        return None, "incomplete_metric_coverage"
    expected_ids = {str(row.get("instance_id")) for row in scores.get("samples" if scores.get("schema_version") == 2 else "per_sample", [])}
    if set(rows) != expected_ids or (scores.get("schema_version") == 2 and (item.get("status") != "available" or item.get("coverage") != 1)):
        return None, "incomplete_metric_coverage"
    expected = sum(rows.values()) / len(rows)
    if not math.isclose(value, expected, abs_tol=tolerance, rel_tol=0):
        return None, "aggregate_sample_conflict"
    return value, None


def assess(baseline: dict, candidate: dict, policy: dict) -> dict:
    """Recompute active fitness terms, preserving valid inputs if another fails."""
    problems = []
    tolerance = policy["tolerance"]
    v1, v2 = baseline.get("schema_version", 1), candidate.get("schema_version", 1)
    if v1 != v2:
        problems.append("incompatible_bundle_schema")
    for key in ("dataset", "split", "ex_variant"):
        if not baseline.get(key) or baseline.get(key) != candidate.get(key):
            problems.append(f"incompatible_{key}")
    before, after = sample_map(baseline, "ex"), sample_map(candidate, "ex")
    for scores in (baseline, candidate):
        rows = scores.get("samples" if scores.get("schema_version") == 2 else "per_sample", [])
        ids = [str(row.get("instance_id")) for row in rows]
        if len(ids) != len(set(ids)) or not ids:
            problems.append("invalid_sample_identity")
    declared = policy.get("sample_selection", {}).get("sample_ids")
    if declared and (set(before) != set(map(str, declared)) or set(after) != set(map(str, declared))):
        problems.append("undeclared_sample_cohort")
    if not before or set(before) != set(after):
        problems.append("incompatible_sample_cohort")
    for key in ("database_snapshot", "candidate_protocol", "evaluator_protocol", "model_config_id"):
        if baseline.get(key) in (None, "", "unknown") or baseline.get(key) != candidate.get(key):
            problems.append(f"incompatible_{key}")
    if v1 == v2 == 2:
        try:
            comparison = compare_scores(baseline, candidate)
            if comparison.get("status") != "compatible":
                problems.append("incompatible_protocol")
        except (ValueError, TypeError, KeyError):
            problems.append("invalid_protocol_bundle")
    else:
        comparison = compare_scores(baseline, candidate) if v1 == v2 == 1 else {}
    regressions = sorted(key for key in before.keys() & after.keys() if before[key] == 1 and after[key] == 0)
    improvements = sorted(key for key in before.keys() & after.keys() if before[key] == 0 and after[key] == 1)
    regression = len(regressions) / len(before) if before and all(value in (0, 1) for value in [*before.values(), *after.values()]) else None
    active = set(policy["required_metrics"]) | {key for key, weight in policy["weights"].items() if weight > 0}
    inputs, contributions = {}, {}
    for key in sorted(active):
        error = None
        if key in {"cost", "latency"}:
            field = "token_total" if key == "cost" else "latency_avg"
            item = comparison.get("metrics", {}).get(field) or {}
            old, new = item.get("previous"), item.get("current")
            if key == "latency" and v1 == v2 == 2:
                old = baseline.get("provenance", {}).get("runtime_actor_latency_s")
                new = candidate.get("provenance", {}).get("runtime_actor_latency_s")
            if not finite(old) or not finite(new) or old <= 0:
                error = "missing_cost_or_latency_baseline"
                change = None
            else:
                change = max(-1, min(1, -(new - old) / old))
        elif key == "regression":
            old, new, change = 0, regression, -regression if regression is not None else None
            error = "incomplete_regression_coverage" if regression is None else None
        elif key == "hard_slice":
            def hard(scores):
                rows = [row.get("ex") for row in scores.get("per_sample", []) if row.get("hardness") in {"hard", "extra", "challenging"}]
                return sum(rows) / len(rows) if rows and all(finite(value) for value in rows) else None
            old, new = hard(baseline), hard(candidate)
            change = new - old if old is not None and new is not None else None
            error = "missing_hard_slice" if change is None else None
        else:
            old, old_error = metric_value(baseline, key, tolerance)
            new, new_error = metric_value(candidate, key, tolerance)
            error = old_error or new_error
            change = new - old if old is not None and new is not None else None
        inputs[key] = {"baseline": old, "candidate": new, "change": change, "reason": error, "purpose": "eval_gold" if key not in {"cost", "latency"} else "observed_cost"}
        if error:
            problems.append(f"{key}:{error}")
        elif change is not None:
            contributions[key] = policy["weights"].get(key, 0) * change
    fitness = round(sum(contributions.values()), 12) if not problems else None
    if regression is None or regression > policy["max_regression_rate"]:
        problems.append("regression_threshold")
    accepted = fitness is not None and not problems and fitness > policy["minimum_improvement"]
    return {"version": VERSION, "computable": fitness is not None, "fitness": fitness,
            "inputs": inputs, "contributions": contributions, "weights": policy["weights"],
            "regressions": regressions, "improvements": improvements, "regression_rate": regression,
            "accepted": accepted, "issues": sorted(set(problems)), "scope": policy.get("stage", "unspecified"),
            "rules": {key: policy[key] for key in ("minimum_improvement", "max_regression_rate", "tolerance")}}


def seal_attempt(root: Path, *, baseline: dict, candidate: dict, policy: dict,
                 actual_diff: str, execution: dict, review: dict,
                 baseline_evidence: str, candidate_evidence: str) -> dict:
    """Retained artifacts are sealed before the temporary worktree disappears."""
    freeze_policy(root, policy)
    write_json(root / "baseline.scores.json", baseline)
    write_json(root / "candidate.scores.json", candidate)
    write_json(root / "execution.json", execution)
    write_json(root / "review.json", review)
    (root / "actual.patch").write_text(actual_diff, encoding="utf-8")
    decision = assess(baseline, candidate, policy)
    write_json(root / "measurement.json", decision)
    files = {str(path.relative_to(root)): digest(path) for path in root.rglob('*') if path.is_file() and path.name not in {"verification.json", "seal.json", "report.md"}}
    write_json(root / "seal.json", {"version": VERSION, "files": files,
                                   "baseline_evidence": baseline_evidence, "candidate_evidence": candidate_evidence})
    result = verify_attempt(root)
    write_json(root / "verification.json", result)
    (root / "report.md").write_text(f"# Evo verification\n\nScope: {policy.get('stage', 'unspecified')}\n\nAccepted: {result['accepted']}\n\nFitness: {result.get('measurement', {}).get('fitness')}\n\nIssues: {', '.join(result['issues']) or 'none'}\n\nActual changes: actual.patch. Measured inputs, contributions, sample regressions and rules: verification.json. Explanations remain hypotheses; this is not proof of causal improvement or global optimality.\n", encoding="utf-8")
    return result


def verify_attempt(directory: str | Path) -> dict:
    """Shared gate for real promotion and local verification; no external calls."""
    root = Path(directory).resolve()
    issues, evidence_details = [], {}
    try:
        if not (root / "seal.json").is_file() and (root / "failure-seal.json").is_file():
            failure = json.loads((root / "failure-seal.json").read_text())
            if failure.get("version") != VERSION:
                issues.append("unsupported_failure_seal")
            for relative, expected in failure.get("files", {}).items():
                path = (root / relative).resolve()
                if root not in path.parents or not path.is_file() or digest(path) != expected:
                    issues.append(f"integrity:{relative}")
            return {"accepted": False, "verifiable": False, "attempt_status": "rejected_or_incomplete", "inspection_complete": not issues,
                    "issues": sorted(set(issues + [failure.get("reason", "unknown_failure")]))}
        seal = json.loads((root / "seal.json").read_text())
        if seal["version"] != VERSION:
            raise ValueError("unsupported_seal")
        required = {"baseline.scores.json", "candidate.scores.json", "acceptance-policy.json", "execution.json", "review.json", "actual.patch", "measurement.json"}
        if not required.issubset(seal["files"]):
            issues.append("missing_sealed_inputs")
        for relative, expected in seal["files"].items():
            path = (root / relative).resolve()
            if root not in path.parents or not path.is_file() or digest(path) != expected:
                issues.append(f"integrity:{relative}")
        if issues:
            return {"accepted": False, "issues": issues}
        read = lambda name: json.loads((root / name).read_text())
        baseline, candidate, policy = read("baseline.scores.json"), read("candidate.scores.json"), read("acceptance-policy.json")
        measurement = assess(baseline, candidate, policy)
        if read("measurement.json") != measurement:
            issues.append("decision_recomputation_conflict")
        if baseline.get("run_id") == candidate.get("run_id") or not candidate.get("run_id"):
            issues.append("run_identity_conflict")
        execution, review = read("execution.json"), read("review.json")
        baseline_execution = read("baseline.execution.json") if (root / "baseline.execution.json").is_file() else {}
        if not baseline_execution or any(baseline_execution.get(key) != execution.get(key) for key in ("base_commit", "base_tree")) or execution.get("base_commit") != policy.get("base_commit"):
            issues.append("base_execution_identity_conflict")
        if not execution.get("config_sha256") or not (root / "config.redacted.json").is_file() or digest(root / "config.redacted.json") != execution.get("config_sha256"):
            issues.append("missing_executed_config_identity")
        if digest(root / "actual.patch") != execution.get("patch_sha256"):
            issues.append("actual_patch_identity_conflict")
        if (root / "review-ledger.json").is_file() and (root / "action.json").is_file():
            from reproduce.evolve.review import ReviewState, compute_verdict
            ledger = read("review-ledger.json")
            state = ReviewState.from_dict(ledger)
            state.status = "in_review"
            action_identity = hashlib.sha256(json.dumps(read("action.json"), sort_keys=True).encode()).hexdigest()
            from reproduce.evolve.state_machine import classify_scope_c
            from reproduce.evolve.mcts.expand import Action
            if classify_scope_c(Action.from_dict(read("action.json"))) and not ledger.get("human_approved"):
                issues.append("human_gate_required")
            if compute_verdict(state) != "approve" or ledger.get("action_sha256") != action_identity:
                issues.append("review_identity_conflict")
        else:
            issues.append("missing_review_ledger")
        if execution.get("returncode") != 0 or not execution.get("base_commit") or not execution.get("base_tree") or not execution.get("command"):
            issues.append("incomplete_execution_identity")
        if not review.get("approved") or (review.get("human_required") and not review.get("human_approved")):
            issues.append("review_or_human_gate")
        active = set(policy["required_metrics"]) | {key for key, weight in policy["weights"].items() if weight > 0}
        gold_identities = []
        for label, scores in (("baseline", baseline), ("candidate", candidate)):
            path = (root / seal[f"{label}_evidence"]).resolve()
            if root not in path.parents:
                raise ValueError("evidence_reference_escape")
            evidence = read_evidence(path)
            config_path = path / "config.redacted.json"
            manifests = list((path / "segments").glob("*/manifest.json"))
            config_identity = digest(config_path) if config_path.is_file() else None
            if not config_identity or not manifests or any(json.loads(manifest.read_text()).get("configuration_identity") != config_identity for manifest in manifests):
                issues.append(f"{label}:execution_config_identity_conflict")
            if config_identity != (execution if label == "candidate" else baseline_execution).get("config_sha256"):
                issues.append("executed_config_content_conflict")
            from reproduce.evolve.evaluation import verify_evaluation
            issues.extend(f"{label}:{issue}" for issue in verify_evaluation(path, scores, evidence))
            evidence_details[label] = {key: evidence[key] for key in ("evidence_status", "execution_status", "issues")}
            if evidence["evidence_status"] != "complete" or evidence["execution_status"] != "success":
                issues.append(f"{label}:incomplete_runtime_evidence")
            event_ids, candidate_ids = {}, {}
            for event in evidence["events"]:
                identity = event.get("event_id")
                if identity in event_ids and event_ids[identity] != event:
                    issues.append(f"{label}:event_identity_conflict")
                event_ids[identity] = event
                if event.get("type") == "candidate.created":
                    candidate_id = event.get("candidate_occurrence_id")
                    if candidate_id in candidate_ids and candidate_ids[candidate_id] != event:
                        issues.append(f"{label}:candidate_identity_conflict")
                    candidate_ids[candidate_id] = event
            try:
                checkpoint = json.loads((path / "evaluation" / "checkpoint.json").read_text())
                gold_identities.append({sample["instance_id"]: hashlib.sha256(str(sample.get("gold_sql")).encode()).hexdigest() for sample in checkpoint["samples"]})
            except (OSError, ValueError, KeyError):
                issues.append(f"{label}:gold_inputs_unavailable")
            run_ids = {event.get("run_id") for event in evidence["events"]}
            if run_ids != {scores.get("run_id")}:
                issues.append(f"{label}:evidence_score_identity_conflict")
            for metric in active:
                for dependency in policy.get("evidence_dependencies", {}).get(metric, ["runtime"]):
                    events = evidence["events"]
                    if dependency == "runtime":
                        continue
                    if dependency == "usage":
                        calls = {event.get("logical_call_id") for event in events if event.get("type") == "logical_call.started" and event.get("operation_kind") == "llm"}
                        reports = [event for event in events if event.get("type") == "usage.reported"]
                        reported = {event.get("logical_call_id") for event in reports if event.get("usage")}
                        usage = aggregate_usage(events)
                        if not calls or not calls.issubset(reported) or not usage["complete"]:
                            issues.append(f"{label}:{metric}:missing_usage")
                    elif dependency == "actor_timing":
                        started = [event for event in events if event.get("type") == "actor.started"]
                        finished = [event for event in events if event.get("type") == "actor.finished" and finite(event.get("duration_ns"))]
                        if not started or len(started) != len(finished):
                            issues.append(f"{label}:{metric}:incomplete_actor_timing")
                    elif dependency == "db_results":
                        calls = [event for event in events if event.get("type") == "db.finished"]
                        if not calls or any(event.get("fetch_completeness") != "complete" or event.get("preservation_completeness") != "complete" for event in calls):
                            issues.append(f"{label}:{metric}:incomplete_db_results")
                    else:
                        issues.append(f"{label}:{metric}:unsupported_dependency:{dependency}")
        if len(gold_identities) != 2 or gold_identities[0] != gold_identities[1]:
            issues.append("gold_cohort_conflict")
        return {"version": VERSION, "accepted": measurement["accepted"] and not issues,
                "verifiable": not issues, "measurement": measurement, "evidence": evidence_details, "issues": sorted(set(issues + measurement["issues"]))}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"accepted": False, "issues": [f"unverifiable:{type(exc).__name__}"]}


def seal_failure(root: Path, policy: dict, reason: str) -> dict:
    """A rejected/incomplete attempt remains inspectable and never validates success."""
    freeze_policy(root, policy)
    write_json(root / 'failure-seal.json', {
        'version': VERSION, 'reason': reason,
        'files': {str(path.relative_to(root)): digest(path) for path in root.rglob('*')
                  if path.is_file() and path.name != 'failure-seal.json'},
    })
    return {'accepted': False, 'verifiable': False, 'issues': [reason], 'attempt_status': 'rejected_or_incomplete'}
