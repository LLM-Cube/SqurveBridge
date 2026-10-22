"""Benchmark comparison snapshots: a comparison kept as it was, checked against the runs it names.

A snapshot records the benchmark, the source, the sampling and each member's
run. Reading one back reports a member whose run has since been removed rather
than dropping it, so an old number is never shown as if it were current.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"[0-9a-f]{12}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
_MAX_MEMBERS = 6


def _folder(root: Path) -> Path:
    return root / "comparisons"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(root: Path, payload: dict) -> dict:
    benchmark = str(payload.get("benchmark") or "")
    members = payload.get("members") or []
    if not _IDENTIFIER.fullmatch(benchmark):
        raise ValueError("A snapshot names one benchmark.")
    if not isinstance(members, list) or not 1 <= len(members) <= _MAX_MEMBERS:
        raise ValueError(f"A snapshot holds between 1 and {_MAX_MEMBERS} members.")
    clean = []
    for member in members:
        if str(member.get("dataset", benchmark)) != benchmark:
            raise ValueError("Every member must belong to the snapshot's benchmark.")
        clean.append({
            "method": str(member.get("method") or ""),
            "variant": str(member.get("variant") or member.get("method") or ""),
            "label": str(member.get("label") or member.get("method") or ""),
            "run_id": str(member.get("run_id") or "") or None,
            "stages": {str(stage): str(actor) for stage, actor in (member.get("stages") or {}).items()},
        })
    snapshot = {
        "id": uuid.uuid4().hex[:12],
        "name": str(payload.get("name") or f"{benchmark} comparison")[:120],
        "benchmark": benchmark,
        "source": str(payload.get("source") or "archive"),
        "sampling": dict(payload.get("sampling") or {}),
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "members": clean,
    }
    folder = _folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{snapshot['id']}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def load(root: Path, snapshot_id: str, run_exists: Callable[[str], bool]) -> dict | None:
    if not _ID.fullmatch(str(snapshot_id)):
        return None
    path = _folder(root) / f"{snapshot_id}.json"
    if not path.is_file():
        return None
    snapshot = _read(path)
    for member in snapshot["members"]:
        member["status"] = "ok" if member.get("run_id") and run_exists(member["run_id"]) else "run_missing"
    return snapshot


def list_snapshots(root: Path) -> list[dict]:
    folder = _folder(root)
    if not folder.is_dir():
        return []
    summaries = []
    for path in sorted(folder.glob("*.json")):
        snapshot = _read(path)
        summaries.append({
            "id": snapshot["id"],
            "name": snapshot["name"],
            "benchmark": snapshot["benchmark"],
            "saved_at": snapshot["saved_at"],
            "members": len(snapshot["members"]),
        })
    return sorted(summaries, key=lambda item: item["saved_at"], reverse=True)
