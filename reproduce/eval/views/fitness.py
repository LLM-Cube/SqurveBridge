"""Meta-Evo adapter: read bundle metrics by registry id.

The evolution engine's fitness (``reproduce.evolve.fitness``)
stays the numeric contract; this view removes its hardcoded knowledge of
bundle layout by resolving metric ids through the same accessor the evidence
view uses.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from reproduce.eval.views.evidence import aggregate_metric_value
from reproduce.evolve.fitness import fitness_from_scores


def metric_value(scores: Dict[str, Any], metric_id: str) -> Optional[float]:
    value = aggregate_metric_value(scores, metric_id)
    return value if isinstance(value, (int, float)) else None


def fitness_inputs(scores: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Quality-side fitness inputs resolved by metric id."""
    hard_slices = []
    by_hardness = scores.get("by_hardness") or {}
    for label in ("hard", "extra", "challenging"):
        cell = by_hardness.get(label)
        if isinstance(cell, dict) and isinstance(cell.get("ex"), (int, float)):
            hard_slices.append(float(cell["ex"]))
    return {
        "ex": metric_value(scores, "ex"),
        "em": metric_value(scores, "em"),
        "ves": metric_value(scores, "ves"),
        "hard_slice_score": sum(hard_slices) / len(hard_slices) if hard_slices else None,
        "latency_avg": metric_value(scores, "latency_avg"),
        "token_total": metric_value(scores, "token_total"),
    }


def bundle_fitness(scores: Dict[str, Any], *, delta: Dict[str, Any] | None = None,
                   weights: Dict[str, float] | None = None) -> float:
    """Delegates to the evolution fitness contract (no behavior change)."""
    if scores.get("schema_version") == 2:
        inputs = fitness_inputs(scores)
        if inputs["ex"] is None:
            raise ValueError("v2 fitness requires available ex_at_1 evidence")
        compatibility = {
            "aggregate": {
                "ex": {"avg": inputs["ex"]}, "em": {"avg": inputs["em"]},
                "ves": {"avg": inputs["ves"]},
            },
            "sample_count": len(scores.get("samples") or []),
        }
        return fitness_from_scores(compatibility, delta=delta, weights=weights)
    return fitness_from_scores(scores, delta=delta, weights=weights)
