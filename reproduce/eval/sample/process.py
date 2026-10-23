"""L5 process layer: canonical roles, shared math kernels, and funnels.

This module is the single implementation of process-signal math that was
previously duplicated between ``reproduce.metrics.workflow`` (trace signals)
and ``reproduce.metrics.pipeline_delta`` (stage deltas). Execution adapters
(how a candidate SQL gets scored) stay with their callers; the arithmetic that
turns outcomes into metrics lives here once.

Snapshot contract (v2): cross-stage SQL snapshots are stored under
``pred_sql_before_<stage>`` where ``<stage>`` is the actor/stage name written
by ``reproduce.metrics.snapshots.capture_pred_sql_snapshot``. Readers resolve
keys through :func:`find_before_key`; exact stage-id matches take precedence
and the legacy substring fallback is kept only for pre-contract rows.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from reproduce.eval.aggregate.statistics import mean as _mean


SNAPSHOT_PREFIX = "pred_sql_before_"

# Squrve task_type -> canonical, method-comparable role.
TASK_TYPE_ROLES = {
    "ReduceTask": "linking",
    "ParseTask": "linking",
    "GenerateTask": "generation",
    "ScaleTask": "generation",
    "OptimizeTask": "refinement",
    "SelectTask": "selection",
    "DecomposeTask": "decomposition",
}

GATE_ORDER = ("parseable", "timely", "executable", "correct", "efficient")

_TIMEOUT_MARKERS = ("timeout", "timed out")


def canonical_role(task_type: Optional[str]) -> Optional[str]:
    return TASK_TYPE_ROLES.get(task_type or "")


# ---------------------------------------------------------------------------
# snapshot key contract
# ---------------------------------------------------------------------------

def find_before_key(
        row: dict,
        *,
        stage_id: Optional[str] = None,
        needles: Sequence[str] = (),
) -> Optional[str]:
    """Resolve the pre-stage SQL snapshot key for a row.

    Exact ``pred_sql_before_<stage_id>`` wins; the substring fallback exists
    for rows recorded before the naming contract and will be removed with it.
    """
    if not isinstance(row, dict):
        return None
    if stage_id:
        exact = f"{SNAPSHOT_PREFIX}{stage_id}"
        if exact in row:
            return exact
    for key in row:
        if not key.startswith(SNAPSHOT_PREFIX):
            continue
        suffix = key[len(SNAPSHOT_PREFIX):].lower()
        if any(needle in suffix for needle in needles):
            return key
    return None


# ---------------------------------------------------------------------------
# math kernels (pure; execution adapters live with the callers)
# ---------------------------------------------------------------------------

def linking_outcome(gold: set, pred: set) -> Dict[str, Any]:
    """Recall/precision over schema sets using containment matching.

    Matching follows the legacy trace semantics: a gold item counts as covered
    when any predicted item is a substring of it, and a predicted item counts
    as useful when it appears inside some gold item.
    """
    result: Dict[str, Any] = {
        "gold_count": len(gold) if gold else None,
        "pred_count": len(pred) if pred else None,
        "recall": None,
        "precision": None,
        "missing": [],
        "extra": [],
    }
    if not gold or pred is None:
        return result
    missing = {g for g in gold if not any(p in g for p in pred)}
    useful = {p for p in pred if any(p in g for g in gold)}
    result["missing"] = sorted(missing)
    result["extra"] = sorted(pred - gold)
    result["recall"] = (len(gold) - len(missing)) / len(gold)
    result["precision"] = len(useful) / len(pred) if pred else None
    return result


def generation_outcome(candidate_exs: Sequence[Optional[int]], exec_error_count: int = 0) -> Dict[str, Any]:
    scores = [ex for ex in candidate_exs if isinstance(ex, (int, float))]
    return {
        "candidate_count": len(candidate_exs),
        "pass_1": scores[0] if scores else None,
        "oracle": max(scores) if scores else None,
        "valid_rate": (
            (len(candidate_exs) - exec_error_count) / len(candidate_exs)
            if candidate_exs else None
        ),
    }


def selection_outcome(candidate_exs: Sequence[Optional[float]], selected_ex: Optional[float]) -> Dict[str, Any]:
    numeric = [ex for ex in candidate_exs if isinstance(ex, (int, float))]
    oracle = max(numeric) if numeric else None
    first = numeric[0] if numeric else None
    return {
        "oracle_ex": oracle,
        "first_ex": first,
        "selected_ex": selected_ex,
        "selection_gain": None if selected_ex is None or first is None else selected_ex - first,
        "selection_loss": None if oracle is None or selected_ex is None else oracle - selected_ex,
        "missed_correct": oracle == 1 and selected_ex == 0,
    }


def refinement_outcome(ex_before: Optional[int], ex_after: Optional[int]) -> Dict[str, Any]:
    return {
        "ex_before": ex_before,
        "ex_after": ex_after,
        "fix_success": ex_before == 0 and ex_after == 1,
        "degradation": ex_before == 1 and ex_after == 0,
    }


# ---------------------------------------------------------------------------
# funnels
# ---------------------------------------------------------------------------

def gate_outcome(sample: dict, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Ordered run gates with early termination (adapted from arXiv 2602.15564).

    parseable -> timely -> executable -> correct -> efficient. The timely gate
    fails when execution was killed by a timeout; the efficiency gate needs an
    explicit latency budget and is skipped (None) without one.
    """
    parseable = bool(sample.get("pred_sql"))
    exec_error = sample.get("exec_error")
    timed_out = isinstance(exec_error, str) and any(
        marker in exec_error.lower() for marker in _TIMEOUT_MARKERS)
    timely = parseable and not timed_out
    executable = timely and exec_error is None
    correct = executable and sample.get("ex") == 1
    efficient: Optional[bool] = None
    elapsed = sample.get("act_elapsed_s")
    if timeout_s is not None and correct and isinstance(elapsed, (int, float)):
        efficient = elapsed <= timeout_s

    gates = {
        "parseable": parseable,
        "timely": timely,
        "executable": executable,
        "correct": correct,
        "efficient": efficient,
    }
    failed_at = next((gate for gate in GATE_ORDER if gates[gate] is False), None)
    return {"gates": gates, "failed_at": failed_at}


def gate_funnel(per_sample: List[dict], *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
    total = len(per_sample)
    survival = {gate: 0 for gate in GATE_ORDER}
    failures = Counter()
    efficiency_known = 0
    for sample in per_sample:
        outcome = gate_outcome(sample, timeout_s=timeout_s)
        for gate in GATE_ORDER:
            if outcome["gates"][gate]:
                survival[gate] += 1
        if outcome["gates"]["efficient"] is not None:
            efficiency_known += 1
        if outcome["failed_at"]:
            failures[outcome["failed_at"]] += 1
    rates = {
        gate: (survival[gate] / total if total else None)
        for gate in GATE_ORDER
    }
    if timeout_s is None:
        rates["efficient"] = None
    return {
        "sample_count": total,
        "survival": rates,
        "failed_at": dict(failures),
        "timeout_s": timeout_s,
    }


def stage_survival_funnel(per_sample: List[dict]) -> Dict[str, Any]:
    """Still-solvable fraction after each canonical role, plus death causes.

    Uses workflow-trace signals: linking survives while no fatal schema miss
    occurred; generation survives while some candidate was correct; selection
    survives while the selected candidate (or the final answer) is correct.
    Samples without a given role keep their previous survival state.
    """
    total = len(per_sample)
    counts = {"linking": 0, "generation": 0, "selection": 0, "final": 0}
    for sample in per_sample:
        stages = ((sample.get("workflow") or {}).get("stages") or {})
        by_role: Dict[str, List[dict]] = {}
        for stage in stages.values():
            role = canonical_role(stage.get("task_type"))
            if role:
                by_role.setdefault(role, []).append(stage)

        alive = True
        if any((stage.get("signals") or {}).get("fatal_schema_miss") for stage in by_role.get("linking", [])):
            alive = False
        if alive:
            counts["linking"] += 1

        if alive:
            generation = by_role.get("generation", [])
            oracles = [
                (stage.get("signals") or {}).get("oracle_ex")
                for stage in generation
            ]
            known = [value for value in oracles if value is not None]
            if known and max(known) == 0:
                alive = False
        if alive:
            counts["generation"] += 1

        if alive:
            selection = by_role.get("selection", [])
            if any((stage.get("signals") or {}).get("missed_correct_candidate") for stage in selection):
                alive = False
        if alive:
            counts["selection"] += 1

        if sample.get("ex") == 1:
            counts["final"] += 1

    return {
        "sample_count": total,
        "survival": {
            role: (count / total if total else None)
            for role, count in counts.items()
        },
    }


# ---------------------------------------------------------------------------
# bundle-level L5 summary
# ---------------------------------------------------------------------------

def process_summary(per_sample: List[dict], aggregate: Dict[str, Any]) -> Dict[str, Any]:
    """L5 metrics keyed by registry ids, read from assembled evidence."""
    pipeline = aggregate.get("pipeline") or {}
    scaler = pipeline.get("scaler") or {}
    optimizer = pipeline.get("optimizer") or {}
    selector = pipeline.get("selector") or {}
    decomposer = pipeline.get("decomposer") or {}

    linking = _linking_from_stages(per_sample)
    generation_pass1 = scaler.get("pass_1")
    generation_oracle = scaler.get("pass_k")
    if generation_pass1 is None and generation_oracle is None:
        generation_pass1, generation_oracle = _generation_from_stages(per_sample)

    # Outcome ratios partition observed final outcomes. Unknown EX is excluded,
    # with coverage and denominator retained; these are offline gold signals.
    observed = [row for row in per_sample if row.get("ex") == 1 or (row.get("ex") == 0 and "exec_error" in row)]
    total = len(observed)
    correct = sum(row.get("ex") == 1 for row in observed)
    errors = sum(row.get("ex") == 0 and bool(row.get("exec_error")) for row in observed)
    return {
        "correct_ratio": correct / total if total else None,
        "incorrect_ratio": (total - correct - errors) / total if total else None,
        "error_ratio": errors / total if total else None,
        "outcome_observed": total,
        "outcome_coverage": total / len(per_sample) if per_sample else None,
        "linking_recall": linking.get("recall"),
        "linking_precision": linking.get("precision"),
        "linking_fatal_miss_rate": linking.get("fatal_miss_rate"),
        "generation_pass1": generation_pass1,
        "generation_oracle_k": generation_oracle,
        "generation_exec_validity": _generation_validity(per_sample),
        "generation_candidate_diversity": scaler.get("avg_candidate_diversity"),
        "refinement_fix_rate": optimizer.get("fix_success_rate"),
        "refinement_degradation_rate": optimizer.get("degradation_rate"),
        "refinement_net_gain": optimizer.get("net_gain"),
        "refinement_debug_turns": optimizer.get("avg_debug_turns"),
        "selection_accuracy": selector.get("selection_accuracy"),
        "selection_regret": selector.get("selection_loss"),
        "selection_missed_correct_rate": _selection_missed_rate(per_sample),
        "decomposition_trigger_rate": decomposer.get("trigger_rate"),
        "decomposition_trigger_accuracy": decomposer.get("trigger_accuracy"),
    }


def _stages_by_role(sample: dict, role: str) -> List[dict]:
    stages = ((sample.get("workflow") or {}).get("stages") or {})
    return [
        stage for stage in stages.values()
        if canonical_role(stage.get("task_type")) == role
    ]


def _linking_from_stages(per_sample: List[dict]) -> Dict[str, Optional[float]]:
    recalls: List[float] = []
    precisions: List[float] = []
    fatal = 0
    observed = 0
    for sample in per_sample:
        stages = _stages_by_role(sample, "linking")
        if not stages:
            continue
        observed += 1
        if any((stage.get("signals") or {}).get("fatal_schema_miss") for stage in stages):
            fatal += 1
        for stage in stages:
            metrics = stage.get("metrics") or {}
            for key, bucket in (("reduce_recall", recalls), ("parse_recall", recalls),
                                ("reduce_precision", precisions), ("parse_precision", precisions)):
                value = metrics.get(key)
                if isinstance(value, (int, float)):
                    bucket.append(float(value))
    return {
        "recall": _mean(recalls),
        "precision": _mean(precisions),
        "fatal_miss_rate": (fatal / observed) if observed else None,
    }


def _generation_from_stages(per_sample: List[dict]) -> tuple[Optional[float], Optional[float]]:
    firsts: List[float] = []
    oracles: List[float] = []
    for sample in per_sample:
        for stage in _stages_by_role(sample, "generation"):
            signals = stage.get("signals") or {}
            if isinstance(signals.get("first_ex"), (int, float)):
                firsts.append(float(signals["first_ex"]))
            if isinstance(signals.get("oracle_ex"), (int, float)):
                oracles.append(float(signals["oracle_ex"]))
    return _mean(firsts), _mean(oracles)


def _generation_validity(per_sample: List[dict]) -> Optional[float]:
    rates: List[float] = []
    for sample in per_sample:
        for stage in _stages_by_role(sample, "generation"):
            signals = stage.get("signals") or {}
            candidates = signals.get("candidate_count")
            valid = signals.get("valid_sql_count")
            if isinstance(candidates, int) and candidates > 0 and isinstance(valid, int):
                rates.append(valid / candidates)
    return _mean(rates)


def _selection_missed_rate(per_sample: List[dict]) -> Optional[float]:
    outcomes: List[float] = []
    for sample in per_sample:
        for stage in _stages_by_role(sample, "selection"):
            signals = stage.get("signals") or {}
            missed = signals.get("missed_correct_candidate")
            if isinstance(missed, bool):
                outcomes.append(1.0 if missed else 0.0)
    return _mean(outcomes)


