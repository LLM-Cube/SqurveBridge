"""Variants: a reproduce config with some stages running a different Actor.

A variant is identified by its method, benchmark and the normalized stage swaps
(stage id → Actor). A stage may only take an Actor that some integrated reproduce
config already runs at the same task type, so every choice is one the task can load.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


class VariantError(ValueError):
    def __init__(self, problems: list[dict]):
        self.problems = problems
        super().__init__("; ".join(f"{item['stage']}: {item['reason']}" for item in problems))


def swappable_actors(configs: list[dict]) -> dict[str, list[str]]:
    """Task type → Actors that integrated reproduce configs run at that type."""
    by_type: dict[str, set[str]] = {}
    for config in configs:
        for stage in config.get("stages") or []:
            if stage.get("type") and stage.get("actor"):
                by_type.setdefault(stage["type"], set()).add(stage["actor"])
    return {task_type: sorted(actors) for task_type, actors in sorted(by_type.items())}


def normalize_stages(config: dict, stages, actors_by_type: dict[str, list[str]]) -> dict[str, str]:
    """Validated swaps, dropping any that keep the stage's own Actor; raises VariantError."""
    if stages in (None, {}):
        return {}
    if not isinstance(stages, dict):
        raise VariantError([{"stage": "*", "actor": None, "reason": "stages must map stage ids to Actors"}])
    own = {stage.get("id"): stage for stage in config.get("stages") or []}
    problems, swaps = [], {}
    for stage_id, actor in stages.items():
        stage = own.get(stage_id)
        if stage is None:
            problems.append({"stage": str(stage_id), "actor": actor, "reason": "unknown_stage"})
        elif not isinstance(actor, str) or actor not in actors_by_type.get(stage.get("type"), []):
            problems.append({"stage": stage_id, "actor": actor, "reason": "actor_not_allowed"})
        elif actor != stage.get("actor"):
            swaps[stage_id] = actor
    if problems:
        raise VariantError(problems)
    return dict(sorted(swaps.items()))


def variant_key(method: str, stages: dict[str, str]) -> str:
    """The method itself, or a stable id for the method with these swaps."""
    if not stages:
        return method
    digest = hashlib.sha256(json.dumps(stages, sort_keys=True).encode("utf-8")).hexdigest()[:6]
    return f"{method}.var-{digest}"


def variant_label(method: str, stages: dict[str, str]) -> str:
    if not stages:
        return method
    return f"{method} · " + ", ".join(f"{stage}→{actor}" for stage, actor in sorted(stages.items()))


def runner_overrides(
    project_root: Path,
    configs: list[dict],
    config: dict,
    stages: dict[str, str],
) -> dict[str, dict]:
    """Swaps in the runner's form, each Actor bringing the settings a config runs it with.

    Settings come from a config on the same benchmark when one runs that Actor,
    since some Actors are tuned per benchmark.
    """
    types = {stage.get("id"): stage.get("type") for stage in config.get("stages") or []}
    donors = sorted(configs, key=lambda item: item.get("dataset") != config.get("dataset"))
    return {
        stage_id: {"actor": actor, "actor_args": _donor_actor_args(project_root, donors, types[stage_id], actor)}
        for stage_id, actor in stages.items()
    }


def _donor_actor_args(project_root: Path, donors: list[dict], task_type: str, actor: str) -> dict:
    for donor in donors:
        if not any(stage.get("type") == task_type and stage.get("actor") == actor for stage in donor.get("stages") or []):
            continue
        try:
            raw = json.loads((project_root / donor["config_path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        for task in (raw.get("task") or {}).get("task_meta") or []:
            meta = task.get("meta") or {}
            if task.get("task_type") == task_type and actor in (meta.get("task") or {}).values():
                return copy.deepcopy(meta.get("actor") or {})
    return {}
