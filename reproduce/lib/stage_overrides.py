"""Swap the Actor of named stages in a reproduce config for one run.

The Demo passes the swaps through ``SQURVE_EVAL_STAGE_ACTORS`` as JSON:
``{"<task_id>": {"actor": "<Actor>", "actor_args": {...}}}``. The config file on
disk is never changed; the run writes the swapped config as its runtime config.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping

STAGE_ACTORS_ENV = "SQURVE_EVAL_STAGE_ACTORS"


def stage_overrides_from_env(environment: Mapping[str, str] | None = None) -> dict:
    values = os.environ if environment is None else environment
    raw = str(values.get(STAGE_ACTORS_ENV) or "").strip()
    if not raw:
        return {}
    overrides = json.loads(raw)
    if not isinstance(overrides, dict) or not all(
        isinstance(item, dict) and isinstance(item.get("actor"), str) for item in overrides.values()
    ):
        raise ValueError(f"{STAGE_ACTORS_ENV} must map task ids to {{actor, actor_args}}")
    return overrides


def apply_stage_overrides(config: dict, overrides: Mapping[str, Mapping]) -> dict:
    """A copy of ``config`` whose named stages run the given Actors.

    The new Actor takes the settings it was given (``actor_args``) but keeps the
    stage's own ``save_dir``, so outputs stay where the rest of the run expects them.
    """
    if not overrides:
        return config
    swapped = copy.deepcopy(config)
    remaining = set(overrides)
    for task in (swapped.get("task") or {}).get("task_meta") or []:
        override = overrides.get(task.get("task_id"))
        if override is None:
            continue
        meta = task.setdefault("meta", {})
        task_options = meta.setdefault("task", {})
        type_key = next((key for key in task_options if key.endswith("_type")), None)
        if type_key is None:
            raise ValueError(f"Stage {task.get('task_id')} does not name an Actor")
        task_options[type_key] = override["actor"]
        original_args = meta.get("actor") or {}
        actor_args = copy.deepcopy(dict(override.get("actor_args") or {}))
        if "save_dir" in original_args:
            actor_args["save_dir"] = original_args["save_dir"]
        meta["actor"] = actor_args
        remaining.discard(task.get("task_id"))
    if remaining:
        raise ValueError(f"Unknown stages: {', '.join(sorted(remaining))}")
    return swapped
