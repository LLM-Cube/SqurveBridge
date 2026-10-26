"""Structured actor runtime trace collection."""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Optional


TRACE_KEY = "_actor_trace"
MAX_VALUE_CHARS = 2000


def snapshot_row(dataset: Any, item: int) -> dict:
    try:
        row = dataset[item]
    except Exception:
        return {}
    if not isinstance(row, dict):
        return {}
    return copy.deepcopy(row)


def record_actor_trace(
        *,
        dataset: Any,
        item: int,
        actor: Any,
        result: Any = None,
        error: Optional[BaseException | str] = None,
        elapsed_s: Optional[float] = None,
        before_row: Optional[dict] = None,
        inputs: Optional[dict] = None,
        data_logger: Any = None,
        stage_name: Optional[str] = None,
) -> dict:
    """Append a compact structured trace record to the current dataset row."""
    try:
        row = dataset[item]
    except Exception:
        return {}
    if not isinstance(row, dict):
        return {}

    before_row = before_row or {}
    after_row = snapshot_row(dataset, item)
    record = {
        "instance_id": row.get("instance_id"),
        "actor_name": getattr(actor, "name", None) or getattr(actor, "NAME", None) or actor.__class__.__name__,
        "actor_class": actor.__class__.__name__,
        "stage_name": stage_name,
        "output_name": getattr(actor, "output_name", None),
        "strategy": getattr(actor, "strategy", None),
        "elapsed_s": elapsed_s,
        "inputs": _summarize_mapping(inputs or {}),
        "result": _summarize_value(result),
        "row_delta": _row_delta(before_row, after_row),
        "error": str(error) if error else None,
        "timestamp_ms": int(time.time() * 1000),
    }
    traces = row.setdefault(TRACE_KEY, [])
    if isinstance(traces, list):
        traces.append(record)
    else:
        row[TRACE_KEY] = [record]

    if data_logger is not None:
        try:
            data_logger.info("actor_trace=" + json.dumps(record, ensure_ascii=False, default=str))
        except Exception:
            pass
    return record


def _row_delta(before: dict, after: dict) -> dict:
    ignored = {TRACE_KEY}
    changed = {}
    added = {}
    removed = []
    for key, value in after.items():
        if key in ignored:
            continue
        if key not in before:
            added[key] = _summarize_value(value)
        elif before.get(key) != value:
            changed[key] = {
                "before": _summarize_value(before.get(key)),
                "after": _summarize_value(value),
            }
    for key in before:
        if key not in after and key not in ignored:
            removed.append(key)
    return {"added": added, "changed": changed, "removed": removed}


def _summarize_mapping(values: dict) -> dict:
    return {
        str(key): _summarize_value(value)
        for key, value in values.items()
        if key not in {"data_logger"}
    }


def _summarize_value(value: Any):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "...<truncated>"
    if isinstance(value, (list, tuple)):
        items = [_summarize_value(item) for item in list(value)[:20]]
        if len(value) > 20:
            items.append(f"...<{len(value) - 20} more>")
        return items
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                result["..."] = f"<{len(value) - 40} more>"
                break
            result[str(key)] = _summarize_value(item)
        return result
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        encoded = repr(value)
    return encoded if len(encoded) <= MAX_VALUE_CHARS else encoded[:MAX_VALUE_CHARS] + "...<truncated>"
