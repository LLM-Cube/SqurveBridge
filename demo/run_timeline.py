"""Run timeline: a live reproduce run in the same per-question, per-stage shape as the recorded replay.

The run log is written while the run is in flight, so it is the only source that already
holds per-sample stage events. Every ``actor_trace`` line names its sample, which lets
interleaved (concurrent) samples be told apart. Nothing here is inferred: a stage is
``done`` or ``failed`` only when its trace was logged, ``running`` only when the sample
entered it and no trace has arrived yet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence

_DECODER = json.JSONDecoder()
_TRACE = "actor_trace="
_STARTED = re.compile(r"开始处理样本\s+(\S+)\s*$")
_ENTERED = re.compile(r"样本\s+(\S+)\s+@\s+(\S+)\s*$")
_FINISHED = re.compile(r"样本\s+(\S+)\s+处理完成\s*\(([\d.]+)s\)")
_TEXT_LIMIT = 160

Judge = Callable[[Mapping, str], Mapping]


def _describe(value) -> str:
    """One readable line for a stage output that is not SQL."""
    if value is None:
        return ""
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= _TEXT_LIMIT else value[:_TEXT_LIMIT] + "…"
    if isinstance(value, (list, tuple)):
        head = ", ".join(str(item) for item in value[:6])
        more = f", … (+{len(value) - 6})" if len(value) > 6 else ""
        return f"{len(value)} items: {head}{more}"
    if isinstance(value, dict):
        return ", ".join(f"{key}" for key in list(value)[:8])
    return str(value)


_MIN_STAGE_SECONDS = 0.3


def stage_summary(stage: Mapping) -> str:
    """One line from what an evaluated stage recorded; the recorded replay uses the same line."""
    runtime = stage.get("runtime") or {}
    bits = [f"{stage.get('task_type', 'Task')} · {stage.get('status', 'observed')} · {runtime.get('call_count', 0)} call(s)"]
    signals = [(k, v) for k, v in (stage.get("signals") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    bits += [f"{key}={value:g}" for key, value in signals[:3]]
    for error in (runtime.get("errors") or [])[:1]:
        bits.append(f"error: {str(error)[:80]}")
    return " · ".join(bits)


def stages_from_evaluation(sample: Mapping) -> list[dict]:
    """The stages of one evaluated question, as the recorded replay shows them.

    The question's measured tokens and act time are split across stages by each stage's
    share of model calls. A sample without a token record keeps tokens as not provided.
    """
    stages = list(((sample.get("workflow") or {}).get("stages") or {}).items())
    if not stages:
        return []
    calls = [max((stage.get("runtime") or {}).get("call_count") or 0, 0) for _, stage in stages]
    shares = [count / sum(calls) if sum(calls) else 1 / len(calls) for count in calls]
    tokens = sum((sample.get("tokens") or {}).values()) if sample.get("tokens") else None
    elapsed = sample.get("act_elapsed_s") or 0
    out = []
    for (name, stage), share in zip(stages, shares):
        is_sql = stage.get("task_type") == "GenerateTask"
        out.append({
            "name": name, "kind": "sql" if is_sql else "text",
            "text": (sample.get("pred_sql") or "") if is_sql else stage_summary(stage),
            "tokens": None if tokens is None else round(tokens * share),
            "seconds": round(max(elapsed * share, _MIN_STAGE_SECONDS), 2),
        })
    return out


def _live_summary(record: Mapping) -> str:
    """While running, a stage line shaped like the replay's: actor · status · what it produced."""
    actor = record.get("actor_class") or record.get("actor_name") or "Actor"
    error = record.get("error")
    if error:
        return f"{actor} · failed · error: {str(error).splitlines()[0][:80]}"
    return f"{actor} · observed · {_describe(record.get('result'))}"


def _parse(log_text: str) -> dict[str, dict]:
    samples: dict[str, dict] = {}

    def sample(instance_id: str) -> dict:
        return samples.setdefault(instance_id, {"stages": [], "entered": None, "done": False})

    for line in (log_text or "").splitlines():
        if _TRACE in line:
            try:
                record, _end = _DECODER.raw_decode(line.split(_TRACE, 1)[1])
            except ValueError:
                continue
            instance_id = record.get("instance_id") if isinstance(record, dict) else None
            if not instance_id:
                continue
            entry = sample(str(instance_id))
            is_sql = record.get("output_name") == "pred_sql"
            result = record.get("result")
            entry["stages"].append({
                "name": record.get("stage_name") or record.get("actor_name") or "",
                "kind": "sql" if is_sql else "text",
                "text": (result if isinstance(result, str) else "") if is_sql and not record.get("error") else _live_summary(record),
                "seconds": record.get("elapsed_s"),
                "state": "failed" if record.get("error") else "done",
                "error": record.get("error"),
            })
            if entry["entered"] == entry["stages"][-1]["name"]:
                entry["entered"] = None
            continue
        if match := _FINISHED.search(line):
            sample(match.group(1))["done"] = True
        elif match := _ENTERED.search(line):
            sample(match.group(1))["entered"] = match.group(2)
        elif match := _STARTED.search(line):
            sample(match.group(1))
    return samples


def _run(entry: dict | None) -> dict:
    if entry is None:
        return {"state": "idle", "stages": [], "pred_sql": "", "ex": None, "result": None}
    stages = [dict(stage) for stage in entry["stages"]]
    seen = {stage["name"] for stage in stages}
    if entry["entered"] and entry["entered"] not in seen and not entry["done"]:
        stages.append({"name": entry["entered"], "kind": "text", "text": "", "seconds": None, "state": "running", "error": None})
    pred_sql = next((stage["text"] for stage in reversed(stages) if stage["kind"] == "sql" and stage["state"] == "done"), "")
    state = "done" if entry["done"] else "running"
    return {"state": state, "stages": stages, "pred_sql": pred_sql, "ex": None, "result": None}


_hardness_cache: dict[str, str | None] = {}


def gold_hardness(gold_sql: str | None) -> str | None:
    """The evaluation's own hardness label for a gold query (None when it cannot be parsed)."""
    if not gold_sql:
        return None
    if gold_sql not in _hardness_cache:
        from reproduce.metrics.sql_parser import SQLFeatureExtractor

        parser = SQLFeatureExtractor(gold_sql)
        _hardness_cache[gold_sql] = parser.classify_hardness() if parser.valid else None
    return _hardness_cache[gold_sql]


def _evaluated_run(sample: Mapping) -> dict:
    stages = [{**stage, "state": "done", "error": None} for stage in stages_from_evaluation(sample)]
    ex = sample.get("ex")
    return {
        "state": "done", "stages": stages, "pred_sql": sample.get("pred_sql") or "",
        "ex": None if ex is None else bool(ex), "result": None, "verdict_source": "evaluation",
    }


def build_run_timeline(
    questions: Sequence[Mapping],
    log_text: str,
    *,
    method: str,
    judge: Judge | None = None,
    since: int = 0,
    evaluation: Sequence[Mapping] | None = None,
) -> dict:
    """The timeline of one method's run.

    ``questions`` are the run's sampled questions in run order. ``judge`` turns a finished
    prediction into ``{"ex": bool, "result": {...}}``; without it the verdict stays unknown
    rather than being guessed. ``since`` is a cursor from an earlier response: only questions
    from that index onward are returned, because earlier ones were already complete.

    ``evaluation`` is the cell's per-sample score bundle once it exists. Its questions then
    read exactly as the recorded replay would show them (stages, tokens, verdict, hardness),
    and every question is returned whatever ``since`` says, so a client replaces what it saw.
    """
    samples = _parse(log_text)
    evaluated = {str(sample.get("instance_id")): sample for sample in evaluation or []}
    items = []
    for index, question in enumerate(questions):
        key = str(question.get("instance_id"))
        sample = evaluated.get(key)
        run = _evaluated_run(sample) if sample else _run(samples.get(key))
        if judge is not None and run["state"] == "done" and run["pred_sql"]:
            verdict = judge(question, run["pred_sql"])
            run["result"] = verdict.get("result")
            if not sample:
                run["ex"] = verdict.get("ex")
        items.append({
            "index": index,
            "id": key,
            "db": question.get("db_id"),
            "hardness": (sample or {}).get("hardness") or gold_hardness(question.get("query")),
            "question": question.get("question"),
            "gold_sql": question.get("query"),
            "run": run,
        })
    cursor = next((item["index"] for item in items if item["run"]["state"] != "done"), len(items))
    corrected = bool(evaluated)
    since = 0 if corrected else max(0, min(int(since), len(items)))
    return {"method": method, "total": len(items), "cursor": cursor, "corrected": corrected, "questions": items[since:]}


def _ordered(sql: str) -> bool:
    return re.search(r"\border\s+by\b", sql or "", re.IGNORECASE) is not None


def _preview(outcome: Mapping) -> dict:
    if not outcome.get("ok"):
        return {"ok": False, "error": outcome.get("error"), "ms": outcome.get("ms")}
    rows = outcome.get("rows") or []
    return {"ok": True, "n": len(rows), "head": [[str(cell) for cell in row] for row in rows[:3]], "ms": outcome.get("ms")}


def make_judge(execute: Callable[[str, str], Mapping]) -> Judge:
    """A verdict from running gold and prediction on the question's database.

    ``execute(db_id, sql)`` returns ``{"ok", "rows" | "error", "ms"}``. The comparison is
    the result sets (order only when the gold query orders). If the gold query itself
    cannot run, the verdict is unknown: a missing reference is not a wrong answer.
    """
    gold_cache: dict[str, Mapping] = {}
    verdicts: dict[tuple[str, str], dict] = {}

    def judge(question: Mapping, pred_sql: str) -> dict:
        key = str(question.get("instance_id"))
        if (key, pred_sql) in verdicts:
            return verdicts[(key, pred_sql)]
        db_id = str(question.get("db_id"))
        if key not in gold_cache:
            gold_cache[key] = execute(db_id, str(question.get("query") or ""))
        gold = gold_cache[key]
        predicted = execute(db_id, pred_sql)
        result = _preview(predicted)
        if not gold.get("ok"):
            return {"ex": None, "result": result}  # not cached: the reference may become runnable
        if not predicted.get("ok"):
            verdicts[(key, pred_sql)] = {"ex": False, "result": result}
        else:
            expected, actual = list(gold.get("rows") or []), list(predicted.get("rows") or [])
            same = expected == actual if _ordered(str(question.get("query"))) else sorted(map(repr, expected)) == sorted(map(repr, actual))
            verdicts[(key, pred_sql)] = {"ex": same, "result": result}
        return verdicts[(key, pred_sql)]

    return judge


def withhold_content(timeline: dict) -> dict:
    """Keep what happened (stages, outcome, row counts) but none of the benchmark's own text.

    For benchmarks a hosted Space does not publish: question, gold SQL, stage outputs, the
    prediction, result rows and database error messages are all dropped.
    """
    for item in timeline["questions"]:
        item["question"] = item["gold_sql"] = None
        run = item["run"]
        run["withheld"] = True
        run["pred_sql"] = ""
        for stage in run["stages"]:
            stage["text"] = ""
            stage["error"] = "withheld" if stage.get("error") else None
        result = run.get("result")
        if result:
            run["result"] = {key: value for key, value in result.items() if key in {"ok", "n", "ms"}}
    return timeline


def cap_text(timeline: dict, limit: int) -> dict:
    """Bound the size of every free-text field a client receives."""
    for item in timeline["questions"]:
        run = item["run"]
        run["pred_sql"] = (run["pred_sql"] or "")[:limit]
        for stage in run["stages"]:
            stage["text"] = (stage.get("text") or "")[:limit]
    return timeline
