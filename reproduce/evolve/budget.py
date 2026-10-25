"""Promotion policy for smoke, bounded, and full evolution stages."""

from __future__ import annotations

from typing import Iterable

from reproduce.evolve.node import EvolutionNode


def smoke_gate_promote(
        nodes: Iterable[EvolutionNode],
        promote_top_k: int = 2,
        min_fitness: float = 0.0,
) -> list[EvolutionNode]:
    return _promote(nodes, promote_top_k=promote_top_k, decision="smoke_promoted", min_fitness=min_fitness)


def bounded_eval_promote(
        nodes: Iterable[EvolutionNode],
        promote_top_k: int = 1,
        min_fitness: float = 0.0,
) -> list[EvolutionNode]:
    return _promote(nodes, promote_top_k=promote_top_k, decision="full_confirmation", min_fitness=min_fitness)


def _promote(
        nodes: Iterable[EvolutionNode],
        *,
        promote_top_k: int,
        decision: str,
        min_fitness: float = 0.0,
) -> list[EvolutionNode]:
    """Rank passing nodes by fitness and promote the top-k improvements.

    Fitness is baseline-centered (see ``fitness.improvement_from_scores``),
    so requiring ``fitness > min_fitness`` (default 0) keeps DRY no-op
    candidates out of the next stage while still letting cost-only
    improvements through. STOP/REGRESSION rollouts never reach here because
    they are recorded with status "buggy".
    """
    node_list = list(nodes)
    ranked = sorted(
        [
            node for node in node_list
            if node.status == "pass" and ("verification" not in node.metadata or node.metadata["verification"].get("accepted")) and node.fitness is not None and float(node.fitness) > min_fitness
        ],
        key=lambda node: (float(node.fitness), -node.branch_id),
        reverse=True,
    )
    promoted_ids = {node.node_id for node in ranked[:promote_top_k]}
    for node in node_list:
        node.promoted = node.node_id in promoted_ids
        if node.promoted:
            node.decision = decision
    return ranked[:promote_top_k]
