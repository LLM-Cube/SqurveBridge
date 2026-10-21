"""Weakness profile rendering for evolution workflows."""

from __future__ import annotations

from typing import Any, Optional


def build_weakness_profile(scores: dict[str, Any]) -> str:
    """Render a concise Markdown profile from scores.json."""
    run_id = scores.get("run_id", "unknown-run")
    aggregate = scores.get("aggregate") or {}
    lines = [f"# Weakness Profile: {run_id}", ""]

    if scores.get("schema_version") == 2:
        ex_cell = aggregate.get("ex_at_1") or {}
        ex = ex_cell.get("value") if ex_cell.get("status") == "available" else None
        lines.append(f"- Protocol: {scores.get('evaluator_protocol', 'unknown')}")
        lines.append(f"- EX@1 coverage: {_fmt(ex_cell.get('coverage'))}")
    else:
        ex = ((aggregate.get("ex") or {}).get("avg"))
    lines.append(f"- EX: {_fmt(ex)}")

    token = aggregate.get("token") or {}
    if token:
        lines.append(f"- Total tokens: {token.get('total_tokens', 0)}")

    error_dist = aggregate.get("error_root_distribution") or {}
    lines.extend(["", "## Top Error Roots"])
    if error_dist:
        ranked = sorted(error_dist.items(), key=lambda item: item[1].get("count", 0), reverse=True)
        for root, stats in ranked[:10]:
            lines.append(f"- {root}: {stats.get('count', 0)} ({_fmt_pct(stats.get('pct'))})")
    else:
        lines.append("- none")

    by_hardness = scores.get("by_hardness") or {}
    lines.extend(["", "## Hardness"])
    if by_hardness:
        for hardness in ("easy", "medium", "hard", "extra"):
            stats = by_hardness.get(hardness)
            if stats:
                lines.append(f"- {hardness}: EX={_fmt(stats.get('ex'))}, count={stats.get('count', 0)}")
    else:
        lines.append("- none")

    pipeline = aggregate.get("pipeline") or {}
    lines.extend(["", "## Pipeline"])
    if pipeline:
        for name, stats in pipeline.items():
            if isinstance(stats, dict):
                compact = ", ".join(f"{key}={_fmt(value)}" for key, value in stats.items())
                lines.append(f"- {name}: {compact}")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _fmt_pct(value: Optional[float]) -> str:
    return "null" if value is None else f"{value:.1%}"
