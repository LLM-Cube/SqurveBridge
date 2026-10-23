"""MCTS orchestration for Meta-Evo smoke search."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any, Callable, List

from reproduce.eval.bundle.compare import compare_scores
from reproduce.evolve.artifacts import create_node_dir, write_json, write_status
from reproduce.evolve.budget import bounded_eval_promote, smoke_gate_promote
from reproduce.evolve.experience import merge_priors
from reproduce.evolve.fitness import (
    R_INVALID,
    compute_fitness,
    fitness_from_scores,
    improvement_from_scores,
)
from reproduce.evolve.journal import EvolutionJournal
from reproduce.evolve.node import EvolutionNode
from reproduce.evolve.process_artifacts import (
    append_process_event,
    create_attempt_dir,
    render_progress,
    update_artifact_manifest,
    write_json as write_process_json,
)
from reproduce.evolve.state_machine import (
    EvolvePhase,
    next_resume_action,
    read_state,
    transition_evolve_dir,
)
from reproduce.evolve.mcts.expand import (
    Action,
    combine_actions,
    filter_executable,
    generate_actions,
    load_actions,
)
from reproduce.evolve.mcts.rollout import run_action_rollout, rollout_verdict, score_from_scores
from reproduce.evolve.mcts.tree import (
    EXPLORATION_C,
    ActionStats,
    TreeNode,
    add_child,
    ancestor_action_ids,
    backpropagate,
    best_path,
    child_action_ids,
    decay_exploration,
    progressive_width,
    record_action_result,
    select_action,
    select_leaf,
    uct_score,
    warm_start_action_stats,
)


def select_leaf_for_progress(
        root: TreeNode,
        *,
        progress: float,
        exploration: float,
        stagnant_branches: set[int] | None = None,
) -> TreeNode:
    if progress >= 0.7 and root.children:
        return _top_k_exploitation_leaf(root)
    if stagnant_branches:
        return _select_leaf_pruned(root, exploration=exploration, stagnant_branches=stagnant_branches)
    return select_leaf(root, exploration=exploration)


def _select_leaf_pruned(root: TreeNode, *, exploration: float, stagnant_branches: set[int]) -> TreeNode:
    """UCT descent that avoids stagnant branches and closed subtrees.

    A stagnant branch stays reachable when every alternative is also stagnant
    or closed, so pruning never deadlocks the search.
    """
    node = root
    while node.children:
        open_children = [child for child in node.children if child.status != "closed"]
        if not open_children:
            return node
        preferred = [child for child in open_children if child.branch_id not in stagnant_branches]
        pool = preferred or open_children
        node = max(pool, key=lambda child: uct_score(child, max(node.visits, 1), exploration))
        if node.visits == 0:
            return node
    return node


def should_force_backprop(iteration: int, rollouts: int, node: TreeNode, recent_best: float | None) -> bool:
    progress = (iteration + 1) / max(rollouts, 1)
    if recent_best is not None and node.average_score >= recent_best:
        return False
    if progress > 0.8:
        return iteration % 2 == 0
    if progress > 0.4:
        return iteration % 3 == 0
    return False


def run_search(
        *,
        actions: List[Action],
        rollouts: int = 20,
        simulator: Callable[[Action, int], float | dict[str, Any]],
        baseline_score: float = 0.0,
        baseline_scores: dict[str, Any] | None = None,
        journal_path: str | Path | None = None,
        evolve_dir: str | Path | None = None,
        stage: str = "smoke",
        smoke_promote_top_k: int = 2,
        bounded_promote_top_k: int = 1,
        dry_round_limit: int = 4,
        stagnation_window: int = 4,
        cumulative_updates: bool = False,
        max_chain_depth: int = 3,
        prior_journals: List[dict[str, Any]] | None = None,
        experience_discount: float = 0.3,
        target_bonus_weight: float = 0.15,
        fitness_weights: dict[str, float] | None = None,
) -> dict:
    """Search the candidate-update space with UCT over actions.

    Reward is baseline-centered: fitness(candidate) - fitness(baseline), so
    a no-op candidate scores 0. ``baseline_scores`` turns the multi-objective
    terms (cost, latency, per-sample regression) into real signal and enables
    the ``target_bonus_weight`` term that rewards actions improving the
    metric they declared as ``target_metric``.

    ``cumulative_updates`` makes a rollout apply the whole root->leaf action
    chain (capped at ``max_chain_depth``); only actions individually verified
    with a CONTINUE verdict may be stacked onto a chain. Rollout results are
    memoized per chain, and a memo hit never re-backpropagates: each tree
    node is scored exactly once, so repeat visits either extend the tree or
    close exhausted subtrees instead of inflating statistics.

    ``prior_journals`` warm-starts the action bandit with discounted
    pseudo-counts from past runs; failed patterns are down-weighted, not
    excluded.
    """
    root = TreeNode(node_id="root")
    nodes_by_id = {"root": root}
    action_stats: dict[str, ActionStats] = {}
    if prior_journals:
        warm_start_action_stats(
            action_stats,
            merge_priors(prior_journals),
            discount=experience_discount,
            failed_reward=R_INVALID / 2,
            known_action_ids={action.action_id for action in actions},
        )
    rollout_memo: dict[tuple[str, ...], dict[str, Any]] = {}
    raw_scores: dict[str, float | None] = {}
    verified_action_ids: set[str] = set()
    journal = EvolutionJournal.read(journal_path) if journal_path else None
    namespace = f"search-{uuid.uuid4().hex[:12]}-" if getattr(simulator, "strict_evidence", False) else ""
    branch_offset = max((node.branch_id for node in journal.nodes), default=0) if namespace and journal else 0
    evaluations = 0
    exhausted = False

    for iteration in range(rollouts):
        if exhausted:
            break
        progress = iteration / max(rollouts, 1)
        exploration = decay_exploration(EXPLORATION_C, progress)
        stagnant_branches = _stagnant_branches(journal)

        leaf = None
        for _attempt in range(len(actions) + 4):
            root_width = progressive_width(root.visits + 1)
            if len(root.children) < root_width:
                candidate = root
                width = root_width
            else:
                candidate = select_leaf_for_progress(
                    root,
                    progress=progress,
                    exploration=exploration,
                    stagnant_branches=stagnant_branches,
                )
                width = progressive_width(max(candidate.visits, 1))
            expanded = False
            if len(candidate.children) < width and candidate.depth < max_chain_depth:
                action = _select_expansion_action(
                    actions=actions,
                    leaf=candidate,
                    nodes_by_id=nodes_by_id,
                    action_stats=action_stats,
                    exploration=exploration,
                    parent_visits=max(root.visits, 1),
                    verified_action_ids=verified_action_ids if cumulative_updates else None,
                )
                if action is not None:
                    branch_id = branch_offset + len(nodes_by_id) if candidate.node_id == "root" else candidate.branch_id
                    candidate = add_child(
                        candidate, f"{namespace}node-{len(nodes_by_id)}", action.to_dict(), branch_id=branch_id,
                    )
                    nodes_by_id[candidate.node_id] = candidate
                    expanded = True
            if not candidate.action:
                # Root with nothing left to expand: the pool is exhausted.
                exhausted = True
                break
            if expanded or candidate.visits == 0:
                leaf = candidate
                break
            # Already-scored leaf that cannot grow: close it and reselect.
            candidate.status = "closed"
        if exhausted or leaf is None:
            break

        recent_best = _recent_best(root, window=4)
        memo_key = _rollout_key(leaf, nodes_by_id, cumulative=cumulative_updates)
        # The evaluated action is the composite chain in cumulative mode; it
        # is what gets recorded in the journal so later stages replay the
        # whole change, not just the leaf.
        rollout_action = _rollout_action(leaf, nodes_by_id, cumulative=cumulative_updates)
        rollout = rollout_memo.get(memo_key)
        fresh = rollout is None
        if fresh:
            if evolve_dir:
                append_process_event(evolve_dir, {
                    "type": "search.selection", "search_id": namespace or "legacy", "iteration": iteration, "selected": leaf.node_id,
                    "action": rollout_action.action_id, "rule": "uct-progressive-width-v1",
                    "exploration": exploration, "alternatives": root.to_dict(),
                    "candidate_action_ids": [item.action_id for item in actions],
                    "max_chain_depth": max_chain_depth, "cumulative_updates": cumulative_updates,
                    "randomness": "deterministic-selection",
                })
            rollout = _normalize_rollout_result(
                simulator(rollout_action, iteration),
                baseline_score=baseline_score,
            )
            rollout_memo[memo_key] = rollout
        reward = _rollout_reward(
            rollout,
            baseline_scores=baseline_scores,
            baseline_score=baseline_score,
            action=rollout_action.to_dict(),
            target_bonus_weight=target_bonus_weight,
            weights=fitness_weights,
        )
        raw_scores[leaf.node_id] = rollout.get("score")
        # New tree nodes always get their single backpropagation (selection
        # needs their statistics), but only fresh simulator calls count as
        # evaluations: memo reuse must not inflate bandit stats, the budget,
        # or the stagnation counters.
        backpropagate(leaf, reward, nodes_by_id)
        if fresh:
            evaluations += 1
            record_action_result(action_stats, str(leaf.action.get("action_id")), reward)
        if leaf.depth == 1 and ((rollout.get("verification") or {}).get("accepted") if "verification" in rollout else (rollout.get("verdict") or {}).get("verdict") == "CONTINUE"):
            verified_action_ids.add(str(leaf.action.get("action_id")))
        if should_force_backprop(iteration, rollouts, leaf, recent_best):
            leaf.forced_backprop = True
        if journal is not None:
            _record_rollout_node(
                journal=journal,
                evolve_dir=evolve_dir,
                tree_node=leaf,
                rollout=rollout,
                baseline_score=baseline_score,
                stage=stage,
                fitness=reward,
                fitness_abs=_absolute_fitness(rollout, baseline_scores),
                evaluated_action=rollout_action.to_dict(),
            )
            stage_nodes = [node for node in journal.nodes if node.stage == stage] if namespace else journal.nodes
            if stage == "smoke":
                smoke_gate_promote(stage_nodes, promote_top_k=smoke_promote_top_k)
            elif stage == "bounded":
                bounded_eval_promote(stage_nodes, promote_top_k=bounded_promote_top_k)
            if namespace:
                eligible = [node for node in stage_nodes if node.status == "pass" and node.metadata.get("verification", {}).get("accepted") and (node.promoted or stage == "full")]
                journal.best_node = max(eligible, key=lambda node: node.fitness).node_id if eligible else None
            _rewrite_node_artifacts(journal=journal, evolve_dir=evolve_dir)
            # Stagnation is judged per fresh evaluation, never on memo hits,
            # so dry_rounds counts informative rollouts rather than loop turns.
            journal.rounds_completed = evaluations
            if fresh:
                journal.global_stagnant(window=stagnation_window)
                for branch_id in {node.branch_id for node in journal.nodes}:
                    journal.branch_stagnant(branch_id)
            journal.write(journal_path)
            if int(journal.stagnation.get("dry_rounds") or 0) >= dry_round_limit:
                break

    path = best_path(root)
    best_node = path[-1] if path else None
    best_raw = raw_scores.get(best_node.node_id) if best_node else None
    return {
        "tree": root.to_dict(),
        "best_path": [node.action for node in path if node.action],
        "best_reward": best_node.average_score if best_node else None,
        "evaluations": evaluations,
        "verdict": rollout_verdict(
            baseline=baseline_score,
            current=best_raw if best_raw is not None else baseline_score,
        ),
    }


def run_bounded_funnel(
        *,
        actions: List[Action],
        smoke_simulator: Callable[[Action, int], float | dict[str, Any]],
        bounded_simulator: Callable[[Action, int], float | dict[str, Any]] | None = None,
        full_simulator: Callable[[Action, int], float | dict[str, Any]] | None = None,
        baseline_score: float = 0.0,
        baseline_scores: dict[str, Any] | None = None,
        journal_path: str | Path | None = None,
        evolve_dir: str | Path | None = None,
        smoke_rollouts: int = 20,
        bounded_rollouts: int = 10,
        smoke_promote_top_k: int = 2,
        bounded_promote_top_k: int = 1,
        dry_round_limit: int = 4,
        stagnation_window: int = 4,
        cumulative_updates: bool = True,
        max_chain_depth: int = 3,
        prior_journals: List[dict[str, Any]] | None = None,
        experience_discount: float = 0.3,
        target_bonus_weight: float = 0.15,
        fitness_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    # Pool-loading gate: actions with neither patches nor a run command can
    # only re-measure the baseline, so they never enter the search.
    actions, skipped_no_patch = filter_executable(actions)
    search_options = {
        "stagnation_window": stagnation_window,
        "cumulative_updates": cumulative_updates,
        "max_chain_depth": max_chain_depth,
        "prior_journals": prior_journals,
        "experience_discount": experience_discount,
        "target_bonus_weight": target_bonus_weight,
        "fitness_weights": fitness_weights,
    }
    state = _prepare_funnel_state(evolve_dir)
    journal = EvolutionJournal.read(journal_path) if journal_path else None
    action = next_resume_action(state, journal) if state and journal else "run_smoke"

    smoke_result = None
    if action == "run_smoke":
        _transition_if_possible(evolve_dir, EvolvePhase.SMOKE_RUNNING, reason="run_smoke", active_stage="smoke")
        smoke_result = run_search(
            actions=actions,
            rollouts=smoke_rollouts,
            simulator=smoke_simulator,
            baseline_score=baseline_score,
            baseline_scores=baseline_scores,
            journal_path=journal_path,
            evolve_dir=evolve_dir,
            stage="smoke",
            smoke_promote_top_k=smoke_promote_top_k,
            dry_round_limit=dry_round_limit,
            **search_options,
        )
        _write_tree_artifact(evolve_dir, "smoke", smoke_result)
        if journal_path:
            journal = EvolutionJournal.read(journal_path)
            if [node for node in journal.nodes if node.promoted and node.status == "pass" and (not getattr(smoke_simulator, "strict_evidence", False) or node.stage == "smoke")]:
                _transition_if_possible(
                    evolve_dir,
                    EvolvePhase.SMOKE_PROMOTED,
                    reason="smoke_promoted",
                    active_stage=None,
                    artifact_refs=["journal.json", "mcts-tree.smoke.json"],
                )
            else:
                _transition_if_possible(
                    evolve_dir,
                    EvolvePhase.STOPPED,
                    reason="smoke_no_promotions",
                    active_stage=None,
                    artifact_refs=["journal.json", "mcts-tree.smoke.json"],
                )
    elif action in {"run_bounded", "run_full", "reconcile_review", "await_review"}:
        smoke_result = {"skipped": True, "reason": f"resume_action:{action}"}

    if not journal_path:
        return {"smoke": smoke_result, "bounded": None, "full": None, "skipped_no_patch": skipped_no_patch}

    journal = EvolutionJournal.read(journal_path)
    smoke_promoted = [node.node_id for node in journal.nodes if node.promoted and node.status == "pass" and (not getattr(smoke_simulator, "strict_evidence", False) or node.stage == "smoke")]
    bounded_actions = [
        Action.from_dict((journal.get_node(node_id).metadata or {}).get("action") or {})
        for node_id in smoke_promoted[:smoke_promote_top_k]
    ]
    bounded_result = None
    state = _read_funnel_state(evolve_dir)
    action = next_resume_action(state, journal) if state else "run_bounded"
    if bounded_simulator and bounded_actions and action == "run_bounded":
        _transition_if_possible(evolve_dir, EvolvePhase.BOUNDED_RUNNING, reason="run_bounded", active_stage="bounded")
        bounded_result = run_search(
            actions=bounded_actions,
            rollouts=bounded_rollouts,
            simulator=bounded_simulator,
            baseline_score=baseline_score,
            baseline_scores=baseline_scores,
            journal_path=journal_path,
            evolve_dir=evolve_dir,
            stage="bounded",
            bounded_promote_top_k=bounded_promote_top_k,
            dry_round_limit=dry_round_limit,
            **search_options,
        )
        _write_tree_artifact(evolve_dir, "bounded", bounded_result)
        journal = EvolutionJournal.read(journal_path)
        if journal.best_node:
            _transition_if_possible(
                evolve_dir,
                EvolvePhase.BOUNDED_PROMOTED,
                reason="bounded_promoted",
                active_stage=None,
                current_node=journal.best_node,
                artifact_refs=["journal.json", "mcts-tree.bounded.json"],
            )
        else:
            _transition_if_possible(
                evolve_dir,
                EvolvePhase.STOPPED,
                reason="bounded_no_best_node",
                active_stage=None,
                artifact_refs=["journal.json", "mcts-tree.bounded.json"],
            )
    elif action in {"run_full", "reconcile_review", "await_review"}:
        bounded_result = {"skipped": True, "reason": f"resume_action:{action}"}

    full_result = None
    journal = EvolutionJournal.read(journal_path)
    state = _read_funnel_state(evolve_dir)
    action = next_resume_action(state, journal) if state else "run_full"
    if full_simulator and action in {"run_full", "reconcile_review"}:
        best = journal.get_node(journal.best_node) if journal.best_node else None
        if best is not None:
            _transition_if_possible(
                evolve_dir,
                EvolvePhase.FULL_CONFIRMING,
                reason="run_full_confirmation",
                active_stage="full",
                current_node=best.node_id,
                artifact_refs=["journal.json"],
            )
            full_result = full_simulator(Action.from_dict((best.metadata or {}).get("action") or {}), 0)
            _write_full_artifacts(evolve_dir, best.node_id, full_result)
            if isinstance(full_result, dict) and "verification" in full_result and not full_result["verification"].get("accepted"):
                _transition_if_possible(evolve_dir, EvolvePhase.FAILED, reason="full_evidence_or_evaluation_rejected", active_stage=None)
                return {"smoke": smoke_result, "bounded": bounded_result, "full": full_result, "skipped_no_patch": skipped_no_patch}
            _transition_if_possible(
                evolve_dir,
                EvolvePhase.REVIEW_PENDING,
                reason="full_confirmation_complete",
                active_stage=None,
                current_node=best.node_id,
                artifact_refs=[
                    f"nodes/{best.node_id}/scores.full.json",
                    "comparison-report.json",
                    "comparison-report.md",
                    "journal.json",
                ],
            )
    elif action == "await_review":
        full_result = {"skipped": True, "reason": "await_review"}

    return {
        "smoke": smoke_result,
        "bounded": bounded_result,
        "full": full_result,
        "skipped_no_patch": skipped_no_patch,
    }


def _top_k_exploitation_leaf(root: TreeNode, top_k: int = 3) -> TreeNode:
    candidates = sorted(root.children, key=lambda node: (node.average_score, node.visits), reverse=True)[:top_k]
    node = candidates[0] if candidates else root
    while node.children:
        ranked = sorted(node.children, key=lambda child: (child.average_score, child.visits), reverse=True)
        node = ranked[0]
    return node


def _recent_best(root: TreeNode, window: int = 4) -> float | None:
    scores = root.scores[-window:]
    return max(scores) if scores else None


def _select_expansion_action(
        *,
        actions: List[Action],
        leaf: TreeNode,
        nodes_by_id: dict[str, TreeNode],
        action_stats: dict[str, ActionStats],
        exploration: float,
        parent_visits: int,
        verified_action_ids: set[str] | None = None,
) -> Action | None:
    """Choose the update to expand, excluding ones already on this branch.

    When ``verified_action_ids`` is provided (cumulative mode) and the leaf
    is not the root, only actions that individually earned a CONTINUE verdict
    may extend a chain — unverified actions never get stacked blindly.
    """
    blocked = set(ancestor_action_ids(leaf, nodes_by_id)) | set(child_action_ids(leaf))
    available = [action for action in actions if action.action_id not in blocked]
    if verified_action_ids is not None and leaf.node_id != "root":
        available = [action for action in available if action.action_id in verified_action_ids]
    return select_action(available, action_stats, parent_visits=parent_visits, exploration=exploration)


def _rollout_key(leaf: TreeNode, nodes_by_id: dict[str, TreeNode], *, cumulative: bool) -> tuple[str, ...]:
    chain = ancestor_action_ids(leaf, nodes_by_id)
    if cumulative:
        return tuple(chain)
    return (chain[-1],) if chain else ()


def _rollout_action(leaf: TreeNode, nodes_by_id: dict[str, TreeNode], *, cumulative: bool) -> Action:
    """Build the action a rollout should evaluate for this leaf.

    In cumulative mode this is the composite of the whole root->leaf chain
    (merged patches, applied in order); otherwise just the leaf action.
    """
    if not cumulative:
        return Action.from_dict(leaf.action)
    chain_dicts = _chain_action_dicts(leaf, nodes_by_id)
    return combine_actions([Action.from_dict(item) for item in chain_dicts])


def _chain_action_dicts(leaf: TreeNode, nodes_by_id: dict[str, TreeNode]) -> List[dict]:
    chain: List[dict] = []
    current: TreeNode | None = leaf
    while current is not None:
        if current.action:
            chain.append(current.action)
        current = nodes_by_id.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    return chain


def _stagnant_branches(journal: EvolutionJournal | None) -> set[int]:
    if journal is None:
        return set()
    return {int(branch) for branch in journal.stagnation.get("branch_stagnant") or []}


def _rollout_reward(
        rollout: dict[str, Any],
        *,
        baseline_scores: dict[str, Any] | None,
        baseline_score: float = 0.0,
        action: dict[str, Any] | None = None,
        target_bonus_weight: float = 0.15,
        weights: dict[str, float] | None = None,
) -> float:
    """Return the baseline-centered reward backpropagated into the tree.

    A candidate that reproduces the baseline scores exactly earns 0. The
    optional target bonus adds ``target_bonus_weight * delta(target_metric)``
    so an action is credited in proportion to how much it actually moved the
    metric it declared to fix.
    """
    if "verification" in rollout:
        verified = rollout["verification"]
        measurement = verified.get("measurement") or {}
        return measurement["fitness"] if verified.get("verifiable") and measurement.get("computable") else R_INVALID
    verdict = rollout.get("verdict") or {}
    if rollout.get("score") is None or verdict.get("verdict") == "STOP":
        return R_INVALID
    scores = rollout.get("scores")
    if isinstance(scores, dict) and isinstance(baseline_scores, dict):
        delta = compare_scores(baseline_scores, scores)
        if delta.get("status", "compatible") != "compatible":
            return R_INVALID
        reward = improvement_from_scores(scores, baseline_scores, delta=delta, weights=weights)
        reward += _target_metric_bonus(action, baseline_scores, scores, target_bonus_weight)
        return round(reward, 6)
    if isinstance(scores, dict):
        return round(fitness_from_scores(scores, weights=weights) - compute_fitness(ex=baseline_score), 6)
    return round(compute_fitness(ex=rollout.get("score")) - compute_fitness(ex=baseline_score), 6)


def _target_metric_bonus(
        action: dict[str, Any] | None,
        baseline_scores: dict[str, Any],
        candidate_scores: dict[str, Any],
        weight: float,
) -> float:
    metric = str((action or {}).get("target_metric") or "")
    if not metric or not weight:
        return 0.0
    base = score_from_scores(baseline_scores, metric)
    candidate = score_from_scores(candidate_scores, metric)
    if isinstance(base, (int, float)) and isinstance(candidate, (int, float)):
        return weight * (float(candidate) - float(base))
    return 0.0


def _absolute_fitness(rollout: dict[str, Any], baseline_scores: dict[str, Any] | None) -> float | None:
    """Absolute fitness for reporting alongside the baseline-centered reward."""
    if "verification" in rollout:
        return None  # Strict reporting exposes measured, baseline-centered contributions instead.
    scores = rollout.get("scores")
    if isinstance(scores, dict):
        delta = compare_scores(baseline_scores, scores) if isinstance(baseline_scores, dict) else None
        return fitness_from_scores(scores, delta=delta)
    score = rollout.get("score")
    return compute_fitness(ex=score) if isinstance(score, (int, float)) else None


def _normalize_rollout_result(result: float | dict[str, Any], *, baseline_score: float) -> dict[str, Any]:
    if isinstance(result, dict):
        score = result.get("score")
        verdict = result.get("verdict") or rollout_verdict(baseline=baseline_score, current=score)
        return {**result, "score": score, "verdict": verdict}
    score = float(result)
    return {
        "score": score,
        "verdict": rollout_verdict(baseline=baseline_score, current=score),
    }


def _record_rollout_node(
        *,
        journal: EvolutionJournal,
        evolve_dir: str | Path | None,
        tree_node: TreeNode,
        rollout: dict[str, Any],
        baseline_score: float,
        stage: str,
        fitness: float,
        fitness_abs: float | None = None,
        evaluated_action: dict[str, Any] | None = None,
) -> None:
    # Record what the rollout actually evaluated (the composite chain in
    # cumulative mode) so bounded/full stages replay the full change set.
    action = evaluated_action or tree_node.action or {}
    existing_ids = {node.node_id for node in journal.nodes}
    score = rollout.get("score")
    verdict = rollout.get("verdict") or rollout_verdict(baseline=baseline_score, current=score)
    verdict_name = verdict.get("verdict")
    status = "buggy" if verdict_name in {"STOP", "REGRESSION"} else "pass"
    verification = rollout.get("verification")
    if verification is not None:
        status = "pass" if verification.get("accepted") else "buggy"
    if verification is not None:
        measurement = verification.get("measurement") or {}
        fitness = measurement.get("fitness") if verification.get("verifiable") and measurement.get("computable") else None
        verdict = {"verdict": "CONTINUE" if verification.get("accepted") else "STOP", "rule": "evo-acceptance-v1", "fitness": fitness, "issues": verification.get("issues", [])}
    score_metadata = _scores_metadata(stage, rollout)
    if tree_node.node_id in existing_ids:
        existing = journal.get_node(tree_node.node_id)
        merged_scores = {**existing.scores, **score_metadata}
        merged_metadata = {**existing.metadata, "action": action, "fitness_abs": fitness_abs, **({"verification": verification, "verification_ref": rollout.get("verification_ref"), "scheduling_control": R_INVALID if fitness is None else None} if verification is not None else {})}
        journal.update_node(
            tree_node.node_id,
            status=status,
            fitness=fitness,
            scores=merged_scores,
            delta=verdict,
            metadata=merged_metadata,
        )
    else:
        node = EvolutionNode(
            node_id=tree_node.node_id,
            parent_id=tree_node.parent_id or "baseline",
            branch_id=tree_node.branch_id,
            stage=stage,
            method=journal.method or "",
            benchmark=journal.benchmark or "",
            target_dimensions=[str(action.get("target_metric", "ex"))],
            change_scope=str(action.get("scope", "")),
            fitness=fitness,
            status=status,
            decision="candidate",
            scores=score_metadata,
            delta=verdict,
            metadata={"action": action, "fitness_abs": fitness_abs, **({"verification": verification, "verification_ref": rollout.get("verification_ref"), "scheduling_control": R_INVALID if fitness is None else None} if verification is not None else {})},
        )
        journal.add_node(node)
    if evolve_dir:
        node_dir = create_node_dir(evolve_dir, tree_node.node_id)
        attempt_dir = create_attempt_dir(evolve_dir, tree_node.node_id, stage)
        _write_action_artifacts(node_dir, action, rollout)
        _write_scores_artifact(node_dir, stage, rollout)
        _write_attempt_artifacts(attempt_dir, stage, action, rollout, status, fitness)
        write_json(node_dir / "node.json", journal.get_node(tree_node.node_id).to_dict())
        write_json(node_dir / "delta.json", verdict)
        write_status(node_dir, status, fitness=fitness, stage=stage, verdict=verdict)
        update_artifact_manifest(
            evolve_dir,
            [
                node_dir / "node.json",
                node_dir / "delta.json",
                node_dir / "status.json",
                attempt_dir / "command.json",
                attempt_dir / "status.json",
            ],
            kind="node",
            phase=f"{stage}_running",
            round=journal.round,
            producer="mcts.run_search",
            node_id=tree_node.node_id,
        )
        append_process_event(evolve_dir, {
            "type": "artifact",
            "phase": f"{stage}_running",
            "round": journal.round,
            "node_id": tree_node.node_id,
            "stage": stage,
            "producer": "mcts.run_search",
            "inputs": ["action-pool.json"],
            "outputs": [
                f"nodes/{tree_node.node_id}/node.json",
                f"nodes/{tree_node.node_id}/attempts/{attempt_dir.name}/status.json",
            ],
            "status": status,
        })
        render_progress(evolve_dir)


def _scores_metadata(stage: str, rollout: dict[str, Any]) -> dict[str, Any]:
    metadata = {}
    if rollout.get("score") is not None:
        metadata[stage] = rollout.get("score")
    return metadata


def _write_action_artifacts(node_dir: Path, action: dict[str, Any], rollout: dict[str, Any]) -> None:
    plan_path = node_dir / "change-plan.md"
    if not plan_path.exists():
        plan_path.write_text(f"# Change Plan\n\n{action.get('description', '')}\n", encoding="utf-8")
    patch_path = node_dir / "patch.diff"
    if rollout.get("actual_diff") is not None:
        patch_path.write_text(rollout["actual_diff"], encoding="utf-8")
    elif not patch_path.exists():
        patch_path.write_text("# Planned changes only; actual patch unavailable\n" + json.dumps(action.get("patches") or [], ensure_ascii=False, indent=2), encoding="utf-8")
    command = action.get("run_command") or rollout.get("run_command") or ""
    command_path = node_dir / "run-command.sh"
    if not command_path.exists():
        command_path.write_text((command.rstrip() + "\n") if command else "# command unavailable\n", encoding="utf-8")
    report_path = node_dir / "evaluator-report.md"
    if not report_path.exists():
        report_path.write_text("# Evaluator Report\n\nPending bounded evaluation report.\n", encoding="utf-8")


def _write_scores_artifact(node_dir: Path, stage: str, rollout: dict[str, Any]) -> None:
    scores = rollout.get("scores")
    if not isinstance(scores, dict):
        return
    filename = {
        "smoke": "scores.smoke50.json",
        "bounded": "scores.bounded200.json",
        "full": "scores.full.json",
    }.get(stage, f"scores.{stage}.json")
    write_json(node_dir / filename, scores)


def _write_attempt_artifacts(
        attempt_dir: Path,
        stage: str,
        action: dict[str, Any],
        rollout: dict[str, Any],
        status: str,
        fitness: float | None,
) -> None:
    write_process_json(attempt_dir / "command.json", {
        "stage": stage,
        "action_id": action.get("action_id"),
        "run_command": action.get("run_command") or rollout.get("run_command") or "",
    })
    (attempt_dir / "stdout.txt").write_text(str(rollout.get("stdout", "")), encoding="utf-8")
    (attempt_dir / "stderr.txt").write_text(str(rollout.get("stderr", "")), encoding="utf-8")
    if isinstance(rollout.get("scores"), dict):
        write_process_json(attempt_dir / "scores.json", rollout["scores"])
    write_process_json(attempt_dir / "status.json", {
        "status": status,
        "fitness": fitness,
        "verdict": rollout.get("verdict"),
    })


def _prepare_funnel_state(evolve_dir: str | Path | None):
    state = _read_funnel_state(evolve_dir)
    if state and state.phase in {EvolvePhase.INITIALIZED, EvolvePhase.BASELINE_LOADED, EvolvePhase.WEAKNESS_PROFILED}:
        return _transition_if_possible(
            evolve_dir,
            EvolvePhase.ACTIONS_GENERATED,
            reason="funnel_actions_ready",
            active_stage=None,
            artifact_refs=["journal.json"],
        )
    return state


def _read_funnel_state(evolve_dir: str | Path | None):
    if not evolve_dir:
        return None
    state_path = Path(evolve_dir) / "evolve-state.json"
    return read_state(state_path) if state_path.exists() else None


def _transition_if_possible(
        evolve_dir: str | Path | None,
        phase: EvolvePhase,
        *,
        reason: str,
        active_stage: str | None = None,
        current_node: str | None = None,
        artifact_refs: list[str] | None = None,
):
    if not evolve_dir:
        return None
    try:
        return transition_evolve_dir(
            evolve_dir,
            phase,
            reason=reason,
            artifact_refs=artifact_refs or ["journal.json"],
            active_stage=active_stage,
            current_node=current_node,
            producer="mcts.run_bounded_funnel",
        )
    except ValueError:
        return _read_funnel_state(evolve_dir)


def _write_tree_artifact(evolve_dir: str | Path | None, stage: str, result: dict[str, Any] | None) -> None:
    if not evolve_dir or result is None:
        return
    path = Path(evolve_dir) / f"mcts-tree.{stage}.json"
    write_process_json(path, result)
    update_artifact_manifest(
        evolve_dir,
        [path],
        kind="report",
        phase=f"{stage}_running",
        producer="mcts.run_bounded_funnel",
    )


def _write_full_artifacts(evolve_dir: str | Path | None, node_id: str, full_result: Any) -> None:
    if not evolve_dir:
        return
    evolve_dir = Path(evolve_dir)
    node_dir = create_node_dir(evolve_dir, node_id)
    scores = full_result.get("scores") if isinstance(full_result, dict) else None
    if isinstance(scores, dict):
        write_process_json(node_dir / "scores.full.json", scores)
    else:
        write_process_json(node_dir / "scores.full.json", {"result": full_result})
    write_process_json(evolve_dir / "comparison-report.json", {
        "best_node": node_id,
        "full_result": full_result,
    })
    (evolve_dir / "comparison-report.md").write_text(
        f"# Full Confirmation\n\n- Best node: {node_id}\n",
        encoding="utf-8",
    )
    update_artifact_manifest(
        evolve_dir,
        [
            node_dir / "scores.full.json",
            evolve_dir / "comparison-report.json",
            evolve_dir / "comparison-report.md",
        ],
        kind="score",
        phase="full_confirming",
        producer="mcts.run_bounded_funnel",
        node_id=node_id,
    )


def _rewrite_node_artifacts(*, journal: EvolutionJournal, evolve_dir: str | Path | None) -> None:
    if not evolve_dir:
        return
    for node in journal.nodes:
        node_dir = create_node_dir(evolve_dir, node.node_id)
        write_json(node_dir / "node.json", node.to_dict())


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic MCTS over a JSON action pool")
    parser.add_argument("--actions", required=True, help="JSON file containing candidate actions")
    parser.add_argument("--weakness-profile", help="Optional weakness profile used when action pool is empty")
    parser.add_argument("--rollouts", type=int, default=20)
    parser.add_argument("--baseline-score", type=float, default=0.0)
    parser.add_argument(
        "--baseline-scores",
        help="Baseline scores.json enabling cost, latency, and regression terms in the fitness",
    )
    parser.add_argument("--simulated-scores", help="Optional JSON map action_id -> score")
    parser.add_argument("--repo-root", help="Run real rollouts in git worktrees rooted at this repo")
    parser.add_argument("--smoke-command", help="Command to run inside each worktree")
    parser.add_argument("--scores-path", help="scores.json path relative to worktree after smoke command")
    parser.add_argument("--metric", default="ex", help="Metric path to optimize, e.g. ex or cf1.cf1_join.avg")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--policy-config", help="Optional reproduce/configs/evolution/*.json policy file")
    parser.add_argument("--journal", help="Optional artifacts/evolve/<slug>/journal.json to update")
    parser.add_argument("--evolve-dir", help="Optional artifacts/evolve/<slug> directory for node artifacts")
    parser.add_argument("--stage", default="smoke", choices=["smoke", "bounded", "full"])
    parser.add_argument(
        "--prior-journal",
        action="append",
        default=[],
        help="Past journal.json to warm-start the action bandit from (repeatable)",
    )
    parser.add_argument(
        "--no-cumulative",
        action="store_true",
        help="Evaluate only leaf actions instead of the full root->leaf chain",
    )
    parser.add_argument("--max-chain-depth", type=int, help="Maximum stacked actions per chain (default 3)")
    parser.add_argument("--output", help="Write search result JSON to this path")
    args = parser.parse_args(argv)

    actions = load_actions(args.actions)
    if not actions and args.weakness_profile:
        actions = generate_actions(Path(args.weakness_profile).read_text(encoding="utf-8"))
    actions, skipped_action_ids = filter_executable(actions)
    simulated = json.loads(Path(args.simulated_scores).read_text(encoding="utf-8")) if args.simulated_scores else {}
    policy = _load_policy(args.policy_config)
    policy_env = dict(policy.get("env") or {})
    promotion = policy.get("promotion") or {}
    dry_round_limit = int(policy.get("dry_round_limit", 4))
    stagnation_window = int(policy.get("stagnation_window", 4))
    cumulative_updates = bool(policy.get("cumulative_updates", True)) and not args.no_cumulative
    max_chain_depth = int(args.max_chain_depth or policy.get("max_chain_depth", 3))
    experience_policy = policy.get("experience") or {}
    experience_discount = float(experience_policy.get("discount", 0.3))
    target_bonus_weight = float(policy.get("target_bonus_weight", 0.15))
    fitness_weights = policy.get("fitness_weights") or None
    prior_journals = [
        json.loads(Path(path).read_text(encoding="utf-8")) for path in args.prior_journal
    ]
    if "promote_top_k" in policy:
        if args.stage == "smoke":
            promotion.setdefault("smoke_top_k", policy["promote_top_k"])
        elif args.stage == "bounded":
            promotion.setdefault("bounded_top_k", policy["promote_top_k"])

    if args.repo_root and args.smoke_command and args.scores_path:
        from reproduce.evolve.verified_search import VerifiedSimulator, run_verified_search
        if not args.evolve_dir or not args.journal or not policy.get("acceptance"):
            parser.error("Real Evo requires --evolve-dir, --journal and a versioned acceptance policy; historical summaries cannot enable strict promotion.")
        acceptance = {**policy["acceptance"], "stage": args.stage, "budget": {"rollouts": args.rollouts}}
        verified = VerifiedSimulator(repo_root=args.repo_root,
                                     directory=Path(args.evolve_dir) / "verification" / args.stage,
                                     policy=acceptance, command=args.smoke_command,
                                     scores_path=args.scores_path, reviews=policy.get("reviews") or {},
                                     base_ref=args.base_ref, env=policy_env)
        result = run_verified_search(actions=actions, simulator=verified, journal_path=args.journal,
                                     evolve_dir=args.evolve_dir, rollouts=args.rollouts,
                                     cumulative_updates=cumulative_updates, max_chain_depth=max_chain_depth)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        else:
            print(payload)
        return 0

    def simulator(action: Action, _iteration: int) -> float | dict[str, Any]:
        if args.repo_root and args.smoke_command and args.scores_path:
            return run_action_rollout(
                repo_root=args.repo_root,
                action=action,
                smoke_command=args.smoke_command,
                scores_path=args.scores_path,
                metric=args.metric,
                baseline_score=args.baseline_score,
                base_ref=args.base_ref,
                env=policy_env,
            )
        return float(simulated.get(action.action_id, args.baseline_score))

    baseline_scores = (
        json.loads(Path(args.baseline_scores).read_text(encoding="utf-8")) if args.baseline_scores else None
    )
    result = run_search(
        actions=actions,
        rollouts=args.rollouts,
        simulator=simulator,
        baseline_score=args.baseline_score,
        baseline_scores=baseline_scores,
        journal_path=args.journal,
        evolve_dir=args.evolve_dir,
        stage=args.stage,
        smoke_promote_top_k=int(promotion.get("smoke_top_k", 2)),
        bounded_promote_top_k=int(promotion.get("bounded_top_k", 1)),
        dry_round_limit=dry_round_limit,
        stagnation_window=stagnation_window,
        cumulative_updates=cumulative_updates,
        max_chain_depth=max_chain_depth,
        prior_journals=prior_journals or None,
        experience_discount=experience_discount,
        target_bonus_weight=target_bonus_weight,
        fitness_weights=fitness_weights,
    )
    if skipped_action_ids:
        result["skipped_no_patch"] = skipped_action_ids
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
