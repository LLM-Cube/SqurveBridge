import json
import contextvars
import functools
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Optional


_thread_context = threading.local()
_original_thread_pool_submit = ThreadPoolExecutor.submit
_thread_pool_submit_patched = False
_global_loggers: list["TokenLogger"] = []
_global_loggers_lock = threading.Lock()
_logical_call_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("logical_call_id", default=None)
_attempt_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("attempt_id", default=None)


def observe_llm_call(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Record one observable provider attempt without inferring hidden retries."""
    @functools.wraps(fn)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            from core.evidence import current_run, identity
            run = current_run()
        except Exception:
            run = None
        if run is None:
            return fn(self, *args, **kwargs)
        logical_call_id = identity()
        attempt_id = identity()
        model = getattr(self, "model_name", None)
        run.event("logical_call.started", logical_call_id=logical_call_id,
                  operation_kind="llm", model=model, retry_observability="opaque")
        run.event("llm.attempt.started", logical_call_id=logical_call_id,
                  attempt_id=attempt_id, model=model, retry_observability="opaque")
        logical_token = _logical_call_id.set(logical_call_id)
        attempt_token = _attempt_id.set(attempt_id)
        started = time.monotonic_ns()
        try:
            result = fn(self, *args, **kwargs)
        except BaseException as exc:
            duration = time.monotonic_ns() - started
            run.event("llm.attempt.finished", logical_call_id=logical_call_id, attempt_id=attempt_id,
                      execution_status="failed", error_type=type(exc).__name__, duration_ns=duration)
            run.event("logical_call.finished", logical_call_id=logical_call_id,
                      execution_status="failed", duration_ns=duration)
            raise
        else:
            duration = time.monotonic_ns() - started
            run.event("llm.attempt.finished", logical_call_id=logical_call_id, attempt_id=attempt_id,
                      execution_status="success", duration_ns=duration)
            run.event("logical_call.finished", logical_call_id=logical_call_id,
                      execution_status="success", duration_ns=duration)
            return result
        finally:
            _attempt_id.reset(attempt_token)
            _logical_call_id.reset(logical_token)
    return wrapped


def observe_llm_stream_call(fn: Callable[..., Iterator[Any]]) -> Callable[..., Iterator[Any]]:
    """Keep attempt identity active until a stream is fully consumed or fails."""
    @functools.wraps(fn)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Iterator[Any]:
        try:
            from core.evidence import current_run, identity
            run = current_run()
        except Exception:
            run = None
        if run is None:
            yield from fn(self, *args, **kwargs)
            return
        logical_call_id = identity()
        attempt_id = identity()
        model = getattr(self, "model_name", None)
        run.event("logical_call.started", logical_call_id=logical_call_id,
                  operation_kind="llm_stream", model=model, retry_observability="opaque")
        run.event("llm.attempt.started", logical_call_id=logical_call_id,
                  attempt_id=attempt_id, model=model, retry_observability="opaque")
        logical_token = _logical_call_id.set(logical_call_id)
        attempt_token = _attempt_id.set(attempt_id)
        started = time.monotonic_ns()
        try:
            yield from fn(self, *args, **kwargs)
        except BaseException as exc:
            duration = time.monotonic_ns() - started
            run.event("llm.attempt.finished", logical_call_id=logical_call_id, attempt_id=attempt_id,
                      execution_status="failed", error_type=type(exc).__name__, duration_ns=duration)
            run.event("logical_call.finished", logical_call_id=logical_call_id,
                      execution_status="failed", duration_ns=duration)
            raise
        else:
            duration = time.monotonic_ns() - started
            run.event("llm.attempt.finished", logical_call_id=logical_call_id, attempt_id=attempt_id,
                      execution_status="success", duration_ns=duration)
            run.event("logical_call.finished", logical_call_id=logical_call_id,
                      execution_status="success", duration_ns=duration)
        finally:
            _attempt_id.reset(attempt_token)
            _logical_call_id.reset(logical_token)
    return wrapped


def get_llm_tag() -> Optional[str]:
    return getattr(_thread_context, "tag", None)


def set_llm_tag(tag: Optional[str]) -> Optional[str]:
    previous = get_llm_tag()
    _thread_context.tag = tag
    return previous


def _patch_thread_pool_submit() -> None:
    global _thread_pool_submit_patched
    if _thread_pool_submit_patched:
        return

    def submit_with_llm_tag(self, fn, /, *args, **kwargs):
        captured_tag = get_llm_tag()

        def run_with_llm_tag(*fn_args, **fn_kwargs):
            previous = set_llm_tag(captured_tag)
            try:
                return fn(*fn_args, **fn_kwargs)
            finally:
                set_llm_tag(previous)

        return _original_thread_pool_submit(self, run_with_llm_tag, *args, **kwargs)

    ThreadPoolExecutor.submit = submit_with_llm_tag  # type: ignore[method-assign]
    _thread_pool_submit_patched = True


@contextmanager
def llm_tag(tag: Optional[str]):
    previous = set_llm_tag(tag)
    try:
        yield
    finally:
        set_llm_tag(previous)


@contextmanager
def append_llm_tag(part: str):
    previous = get_llm_tag()
    tag = f"{previous}|{part}" if previous else part
    set_llm_tag(tag)
    try:
        yield
    finally:
        set_llm_tag(previous)


def _read_optional_usage_value(usage: Any, key: str) -> Optional[int]:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get(key)
    else:
        value = getattr(usage, key, None)
    return None if value is None else int(value)


def _percentile(values: Iterable[int], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil((percentile / 100) * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def _distribution(values: list[int]) -> Dict[str, float]:
    if not values:
        return {"mean": 0, "median": 0, "p95": 0, "min": 0, "max": 0}
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": _percentile(values, 95),
        "min": min(values),
        "max": max(values),
    }


class TokenLogger:
    def __init__(self):
        self.records: list[Dict[str, Any]] = []
        self._lock = threading.Lock()
        with _global_loggers_lock:
            _global_loggers.append(self)

    def record(self, usage: Any, model: Optional[str] = None, tag: Optional[str] = None) -> None:
        reported_prompt_tokens = _read_optional_usage_value(usage, "prompt_tokens")
        reported_completion_tokens = _read_optional_usage_value(usage, "completion_tokens")
        reported_total_tokens = _read_optional_usage_value(usage, "total_tokens")
        prompt_tokens = reported_prompt_tokens or 0
        completion_tokens = reported_completion_tokens or 0
        if reported_total_tokens is not None:
            total_tokens = reported_total_tokens
        elif reported_prompt_tokens is not None and reported_completion_tokens is not None:
            total_tokens = reported_prompt_tokens + reported_completion_tokens
        else:
            total_tokens = 0
        if (reported_prompt_tokens is None and reported_completion_tokens is None
                and reported_total_tokens is None):
            return

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tag": tag if tag is not None else get_llm_tag(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        with self._lock:
            self.records.append(record)
        # Evidence is observational and fail-open; token logging remains usable
        # when the optional local collector is absent or unavailable.
        try:
            from core.evidence import current_run
            run = current_run()
            if run is not None:
                logical_call_id = _logical_call_id.get()
                attempt_id = _attempt_id.get()
                evidence_total_tokens = reported_total_tokens
                if (
                    evidence_total_tokens is None
                    and reported_prompt_tokens is not None
                    and reported_completion_tokens is not None
                ):
                    evidence_total_tokens = reported_prompt_tokens + reported_completion_tokens
                reported_usage = {
                    key: value
                    for key, value in {
                        "prompt_tokens": reported_prompt_tokens,
                        "completion_tokens": reported_completion_tokens,
                        "total_tokens": evidence_total_tokens,
                    }.items()
                    if value is not None
                }
                run.event("usage.reported", scope="attempt" if attempt_id else "provider_observation",
                          source="provider_sdk", logical_call_id=logical_call_id, attempt_id=attempt_id,
                          retry_observability="opaque",
                          model=model, tag=record["tag"], usage=reported_usage)
        except Exception:
            pass

    def total(self) -> Dict[str, int]:
        with self._lock:
            records = list(self.records)
        return {
            "calls": len(records),
            "prompt_tokens": sum(record["prompt_tokens"] for record in records),
            "completion_tokens": sum(record["completion_tokens"] for record in records),
            "total_tokens": sum(record["total_tokens"] for record in records),
        }

    def reset(self) -> None:
        with self._lock:
            self.records.clear()

    def by_tag(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            records = list(self.records)
        grouped: Dict[str, list[Dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record.get("tag") or "", []).append(record)

        stats = {}
        for tag, tag_records in grouped.items():
            totals = [record["total_tokens"] for record in tag_records]
            distribution = _distribution(totals)
            stats[tag] = {
                "calls": len(tag_records),
                "prompt_tokens": sum(record["prompt_tokens"] for record in tag_records),
                "completion_tokens": sum(record["completion_tokens"] for record in tag_records),
                "total_tokens": sum(totals),
                **distribution,
            }
        return stats

    def by_step_and_sample(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            records = list(self.records)

        per_step_sample: Dict[str, Dict[str, int]] = {}
        for record in records:
            sample_id, step = self._parse_tag(record.get("tag"))
            if not sample_id:
                continue
            step = step or "unknown"
            per_step_sample.setdefault(step, {})
            per_step_sample[step][sample_id] = per_step_sample[step].get(sample_id, 0) + record["total_tokens"]

        stats = {}
        for step, sample_totals in per_step_sample.items():
            totals = list(sample_totals.values())
            distribution = _distribution(totals)
            stats[step] = {
                "samples": len(sample_totals),
                "per_sample_mean": distribution["mean"],
                "per_sample_median": distribution["median"],
                "per_sample_p95": distribution["p95"],
                "per_sample_min": distribution["min"],
                "per_sample_max": distribution["max"],
            }
        return stats

    def summary(self) -> Dict[str, Any]:
        return {
            "total": self.total(),
            "by_tag": self.by_tag(),
            "by_step_and_sample": self.by_step_and_sample(),
        }

    def dump_jsonl(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = list(self.records)
        with output_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def dump_summary_json(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _parse_tag(tag: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not tag:
            return None, None
        parts = tag.split("|")
        sample = parts[0]
        if not sample.startswith("sample:"):
            return None, parts[-1] if parts else None
        sample_id = sample.removeprefix("sample:")
        step = parts[-1] if len(parts) > 1 else None
        return sample_id, step


def record_completion_usage(model: Any, response: Any) -> None:
    token_logger = getattr(model, "token_logger", None)
    usage = getattr(response, "usage", None)
    if token_logger is not None and usage is not None:
        token_logger.record(model=getattr(model, "model_name", None), usage=usage)


def collect_all_token_data() -> Dict[str, Any]:
    with _global_loggers_lock:
        loggers = list(_global_loggers)

    records: list[Dict[str, Any]] = []
    total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for logger in loggers:
        with logger._lock:
            records.extend(dict(record) for record in logger.records)
        logger_total = logger.total()
        for key in total:
            total[key] += logger_total.get(key, 0)

    return {
        "total": total,
        "by_tag": _stats_by_tag(records),
        "by_step_and_sample": _stats_by_step_and_sample(records),
        "records": records,
    }


def reset_all_token_loggers() -> None:
    with _global_loggers_lock:
        loggers = list(_global_loggers)
    for logger in loggers:
        logger.reset()


def _stats_by_tag(records: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record.get("tag") or "", []).append(record)
    result = {}
    for tag, tag_records in grouped.items():
        totals = [record["total_tokens"] for record in tag_records]
        result[tag] = {
            "calls": len(tag_records),
            "prompt_tokens": sum(record["prompt_tokens"] for record in tag_records),
            "completion_tokens": sum(record["completion_tokens"] for record in tag_records),
            "total_tokens": sum(totals),
            **_distribution(totals),
        }
    return result


def _stats_by_step_and_sample(records: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    per_step_sample: Dict[str, Dict[str, int]] = {}
    for record in records:
        sample_id, step = TokenLogger._parse_tag(record.get("tag"))
        if not sample_id:
            continue
        step = step or "unknown"
        per_step_sample.setdefault(step, {})
        per_step_sample[step][sample_id] = per_step_sample[step].get(sample_id, 0) + record["total_tokens"]

    result = {}
    for step, sample_totals in per_step_sample.items():
        totals = list(sample_totals.values())
        distribution = _distribution(totals)
        result[step] = {
            "samples": len(sample_totals),
            "per_sample_mean": distribution["mean"],
            "per_sample_median": distribution["median"],
            "per_sample_p95": distribution["p95"],
            "per_sample_min": distribution["min"],
            "per_sample_max": distribution["max"],
        }
    return result


def _chunk_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    if getattr(delta, "reasoning_content", None) is not None:
        return ""
    return getattr(delta, "content", None) or ""


def collect_stream_completion(model: Any, response: Any) -> str:
    completion_response = ""
    usage_chunk = None
    for chunk in response:
        completion_response += _chunk_text(chunk)
        if getattr(chunk, "usage", None) is not None:
            usage_chunk = chunk
    if usage_chunk is not None:
        record_completion_usage(model, usage_chunk)
    return completion_response


_patch_thread_pool_submit()
