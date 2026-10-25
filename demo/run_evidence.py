"""Allowlisted run-detail projection; raw SQL and error payloads stay on disk."""
from __future__ import annotations
import math


PROCESS_KEYS = frozenset("correct_ratio incorrect_ratio error_ratio outcome_observed outcome_coverage linking_recall linking_precision linking_fatal_miss_rate generation_pass1 generation_oracle_k generation_exec_validity generation_candidate_diversity refinement_fix_rate refinement_degradation_rate refinement_net_gain refinement_debug_turns selection_accuracy selection_regret selection_missed_correct_rate decomposition_trigger_rate decomposition_trigger_accuracy".split())
CF1_KEYS = frozenset("cf1_select cf1_where cf1_group cf1_order cf1_join cf1_keywords cf1_iuen".split())

def number(value):
    if isinstance(value, dict):
        value = value.get("avg", value.get("value"))
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def run_evidence(scores: dict, *, include_questions: bool = False) -> dict:
    aggregate = scores.get("aggregate") or {}
    process = {key: number(value) for key, value in (aggregate.get("process") or {}).items() if key in PROCESS_KEYS}
    samples = []
    for index, row in enumerate(scores.get("per_sample") or []):
        optimizer = (row.get("pipeline") or {}).get("optimizer") or {}
        ex = number(row.get("ex"))
        samples.append({
            "index": index + 1,
            "question": row.get("question", "")[:1000] if include_questions and isinstance(row.get("question"), str) else None,
            **{key: number(row.get(key)) for key in ("ex", "sf1", "rves", "ves", "em")},
            "cf1": {key: number(value) for key, value in (row.get("cf1") or {}).items() if key in CF1_KEYS},
            "execution_diff": ex == 0 if ex in (0, 1) else None,
            "error": bool(row.get("exec_error")) if ex in (0, 1) and "exec_error" in row else None,
            "c2i": optimizer.get("degradation") if isinstance(optimizer.get("degradation"), bool) and optimizer.get("ex_before") in (0, 1) and optimizer.get("ex_after") in (0, 1) else None,
        })
    return {
        "ex_variant": scores.get("ex_variant") if scores.get("ex_variant") in {"spider", "bird", "custom"} else "custom",
        "process": process,
        "component_f1": {key.removeprefix("cf1_"): number(value) for key, value in (aggregate.get("cf1") or {}).items() if key in CF1_KEYS},
        "sample_evidence": samples,
    }
