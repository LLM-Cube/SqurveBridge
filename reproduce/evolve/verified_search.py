"""Real Meta-Evo entrypoint: reviewed actions, retained execution, strict acceptance."""
from __future__ import annotations
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from reproduce.evolve.mcts.expand import Action
from reproduce.evolve.mcts.rollout import run_action_rollout, score_from_scores, run_command
from reproduce.evolve.review import ReviewState, compute_verdict
from reproduce.evolve.state_machine import classify_scope_c
from reproduce.evolve.verification import freeze_policy, seal_attempt, seal_failure, write_json


def action_hash(action: Action) -> str:
    return hashlib.sha256(json.dumps(action.to_dict(), sort_keys=True).encode()).hexdigest()


class VerifiedSimulator:
    """A stage-specific simulator consumable by run_search and the existing funnel.

    Construct before any evaluation. Each stage has a frozen policy and freshly
    collected baseline, so smoke and full scores are never interchanged.
    """
    strict_evidence = True

    def __init__(self, *, repo_root: str | Path, directory: str | Path, policy: dict,
                 command: str | list[str], scores_path: str | Path, reviews: dict[str, str | Path],
                 base_ref: str = "HEAD", env: dict[str, str] | None = None):
        self.repo_root, self.root = Path(repo_root), Path(directory)
        resolved = run_command(["git", "rev-parse", base_ref + "^{commit}"], cwd=self.repo_root)
        if resolved.returncode:
            raise ValueError("Cannot resolve Evo base commit")
        fixed_commit = resolved.stdout.strip()
        self.policy = freeze_policy(self.root, {**policy, "base_commit": fixed_commit})
        self.command, self.scores_path, self.reviews = command, scores_path, reviews
        self.base_ref, self.env = fixed_commit, env or {}
        self.baseline: dict[str, Any] | None = None
        self.baseline_dir = self.root / "baselines" / uuid.uuid4().hex

    def establish_baseline(self) -> dict:
        # Real baseline execution follows exactly the same collection path.
        if self.baseline is not None:
            return self.baseline
        action = Action("baseline", "Observed baseline", "ex", "A", "low")
        result = run_action_rollout(repo_root=self.repo_root, action=action,
                                    smoke_command=self.command, scores_path=self.scores_path,
                                    metric="ex", baseline_score=0, base_ref=self.base_ref,
                                    env=self.env, retain_directory=self.baseline_dir,
                                    collection=self.policy.get("collection"))
        self.baseline = result
        write_json(self.baseline_dir / "result.json", result)
        return result

    def __call__(self, action: Action, iteration: int) -> dict:
        # No path is derived directly from an action id supplied by a model.
        attempt = self.root / "attempts" / f"{iteration:04d}-{action_hash(action)[:16]}-{uuid.uuid4().hex[:12]}"
        attempt.mkdir(parents=True, exist_ok=False)
        write_json(attempt / "action.json", action.to_dict())
        required_human = bool(classify_scope_c(action))
        review = {"approved": False, "human_required": required_human}
        review_path = self.reviews.get(action.action_id)
        try:
            ledger = json.loads(Path(review_path).read_text()) if review_path else {}
            review_state = ReviewState.from_dict(ledger)
            review_state.status = "in_review"
            review["approved"] = ledger.get("action_sha256") == action_hash(action) and compute_verdict(review_state) == "approve"
            review["human_approved"] = ledger.get("human_approved") is True
            write_json(attempt / "review-ledger.json", ledger)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if not review["approved"] or (required_human and not review.get("human_approved")):
            result = {"score": None, "verdict": {"verdict": "STOP", "reason": "review_or_human_gate"}, "verification": {"accepted": False, "issues": ["review_or_human_gate"]}}
            write_json(attempt / "rejected.json", result)
            result["verification"] = seal_failure(attempt, self.policy, result["verdict"]["reason"])
            return result
        spent = sum(1 for path in (self.root / "attempts").glob("*/execution.json"))
        if spent >= self.policy["budget"]["rollouts"]:
            result = {"score": None, "verdict": {"verdict": "STOP", "reason": "stage_budget_exhausted"}}
            result["verification"] = seal_failure(attempt, self.policy, "stage_budget_exhausted")
            return result
        try:
            baseline = self.establish_baseline()
        except (OSError, ValueError) as exc:
            baseline = {"error_type": type(exc).__name__}
        if not baseline.get("scores"):
            result = {"score": None, "verdict": {"verdict": "STOP", "reason": "baseline_execution_failed"}}
            write_json(attempt / "rejected.json", result)
            result["verification"] = seal_failure(attempt, self.policy, result["verdict"]["reason"])
            return result
        base_score = score_from_scores(baseline["scores"], "ex")
        try:
            result = run_action_rollout(repo_root=self.repo_root, action=action,
                                        smoke_command=action.run_command or self.command,
                                        scores_path=self.scores_path, metric="ex", baseline_score=base_score,
                                        base_ref=self.base_ref, env=self.env, retain_directory=attempt,
                                        allow_scope_c=required_human and review.get("human_approved", False),
                                        collection=self.policy.get("collection"))
        except (OSError, ValueError, PermissionError) as exc:
            result = {"score": None, "verdict": {"verdict": "STOP", "reason": "execution_or_artifact_failure"}, "error_type": type(exc).__name__}
        if not result.get("scores"):
            write_json(attempt / "failed.json", result)
            result["verification"] = seal_failure(attempt, self.policy, "execution_failed")
            return result
        if (self.baseline_dir / "runtime").is_dir():
            shutil.copytree(self.baseline_dir / "runtime", attempt / "baseline-runtime")
        shutil.copyfile(self.baseline_dir / "execution.json", attempt / "baseline.execution.json")
        verification = seal_attempt(attempt, baseline=baseline["scores"], candidate=result["scores"], policy=self.policy,
                                    actual_diff=result["actual_diff"], execution=result["execution"], review=review,
                                    baseline_evidence=baseline.get("runtime_evidence", "runtime").replace("runtime", "baseline-runtime", 1), candidate_evidence=result.get("runtime_evidence", "runtime"))
        result["verification"] = verification
        result["verification_ref"] = str(Path(getattr(self, "artifact_prefix", ".")) / attempt.relative_to(self.root))
        return result


def run_verified_search(*, actions: list[Action], simulator: VerifiedSimulator,
                        journal_path: str | Path, evolve_dir: str | Path, rollouts: int = 20,
                        cumulative_updates: bool = True, max_chain_depth: int = 3) -> dict:
    from reproduce.evolve.mcts.orchestrator import run_search
    # Baseline is established inside the simulator only after review permits a
    # candidate. Neither malformed nor unreviewed actions spend execution budget.
    simulator.artifact_prefix = str(simulator.root.resolve().relative_to(Path(evolve_dir).resolve()))
    if rollouts > simulator.policy["budget"]["rollouts"]:
        raise ValueError("Search budget exceeds the frozen stage budget")
    result = run_search(actions=actions, simulator=simulator, journal_path=journal_path,
                        evolve_dir=evolve_dir, stage=simulator.policy.get("stage", "smoke"),
                        rollouts=rollouts, target_bonus_weight=0,
                        cumulative_updates=cumulative_updates, max_chain_depth=max_chain_depth)
    from reproduce.evolve.journal import EvolutionJournal
    journal = EvolutionJournal.read(journal_path)
    accepted = [node for node in journal.nodes if node.stage == simulator.policy.get("stage", "smoke") and node.metadata.get("verification", {}).get("accepted") and str(node.metadata.get("verification_ref", "")).startswith(simulator.artifact_prefix + "/")]
    result["search_control_reward"] = result.get("best_reward")
    measured = [node.fitness for node in journal.nodes if node.fitness is not None and node.stage == simulator.policy.get("stage", "smoke")]
    result["best_reward"] = max(measured) if measured else None
    result["tree_score_kind"] = "scheduling_reward"
    result["verdict"] = {"verdict": "CONTINUE" if accepted else "STOP", "rule": "evo-acceptance-v1", "accepted_nodes": [node.node_id for node in accepted], "scope": simulator.policy.get("stage")}
    write_json(simulator.root / "search-result.json", result)
    return result
