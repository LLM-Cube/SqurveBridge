"""Run-local, bounded runtime evidence. No method operations are performed here."""
from __future__ import annotations

import contextvars
import hashlib
import json
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = 1
_current: contextvars.ContextVar[EvidenceRun | None] = contextvars.ContextVar("evidence_run", default=None)
_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("evidence_span", default=None)
_actor: contextvars.ContextVar[int | None] = contextvars.ContextVar("evidence_actor", default=None)


def identity() -> str:
    return uuid.uuid4().hex


class LocalSink:
    """Only the writer thread performs filesystem writes."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def write(self, name: str, payload: bytes) -> None:
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Content and event names are immutable. Re-export never overwrites.
        try:
            with path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError("identity_conflict")


class EvidenceRun:
    """Bounded producer queue, run-local immutable segments, and fail-open capture."""

    def __init__(
        self, directory: str | Path, *, run_id: str, sink: Any = None,
        max_bytes: int = 1_048_576, queue_size: int = 128,
        write_wait_s: float = 1.0, prior_segment: str | None = None,
        checkpoint_ref: str | None = None, capture_outputs: bool = False,
    ):
        if max_bytes <= 0 or queue_size <= 0 or write_wait_s < 0:
            raise ValueError("invalid_collection_limits")
        self.directory = Path(directory)
        self.run_id = run_id
        self.segment_id = identity()
        self.max_bytes = max_bytes
        self.write_wait_s = write_wait_s
        self.capture_outputs = capture_outputs
        self.sink = sink if sink is not None else LocalSink(directory)
        self._queue: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=queue_size)
        self._closed = threading.Event()
        self._failed = threading.Event()
        self._issues: set[str] = set()
        self._lock = threading.Lock()
        self._count = 0
        self._worker = threading.Thread(target=self._write, daemon=True)
        self._manifest = {
            "schema_version": SCHEMA_VERSION, "run_id": run_id,
            "execution_segment_id": self.segment_id,
            "prior_segment": prior_segment, "checkpoint_ref": checkpoint_ref,
            "coverage": {"profile": "common-runtime-v1", "status": "partial",
                         "unsupported": ["method_specific_actor_facts", "llm_usage", "db_fetch_completeness",
                                          "producer_lineage", "acceptance_profile"]},
            "retention": {"output_artifacts": "opt_in" if capture_outputs else "disabled"},
            "limits": {"max_bytes": max_bytes, "queue_size": queue_size, "write_wait_s": write_wait_s},
        }

    def _issue(self, reason: str) -> None:
        self._failed.set()
        with self._lock:
            # Reasons are bounded codes, not exception messages or payloads.
            if len(self._issues) < 32:
                self._issues.add(reason)

    def _write(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                name, data = self._queue.get(timeout=0.02)
            except queue.Empty:
                continue
            try:
                self.sink.write(name, data)
            except Exception:
                self._issue("sink_write_failed")
            finally:
                self._queue.task_done()

    def _enqueue(self, name: str, value: Any) -> bool:
        try:
            # iterencode limits serialized allocation and rejects unsupported values.
            chunks: list[bytes] = []
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True).iterencode(value):
                part = chunk.encode("utf-8")
                size += len(part)
                if size > self.max_bytes:
                    self._issue("payload_limit")
                    return False
                chunks.append(part)
            self._queue.put_nowait((name, b"".join(chunks)))
            return True
        except queue.Full:
            self._issue("queue_limit")
        except Exception:
            self._issue("serialization_failed")
        return False

    def _enqueue_bytes(self, name: str, data: bytes) -> bool:
        try:
            self._queue.put_nowait((name, data))
            return True
        except queue.Full:
            self._issue("queue_limit")
            return False

    def event(self, kind: str, **facts: Any) -> str | None:
        event_id = identity()
        record = {
            **facts, "type": kind, "event_id": event_id,
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id,
            "execution_segment_id": self.segment_id,
            "timestamp_ns": time.time_ns(), "parent_span_id": _span.get(),
        }
        if self._enqueue(f"segments/{self.segment_id}/events/{event_id}.json", record):
            with self._lock:
                self._count += 1
            return event_id
        return None

    def artifact(self, value: Any) -> str | None:
        try:
            chunks: list[bytes] = []
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True).iterencode(value):
                part = chunk.encode("utf-8")
                size += len(part)
                if size > self.max_bytes:
                    self._issue("artifact_limit")
                    return None
                chunks.append(part)
            data = b"".join(chunks)
            ref = hashlib.sha256(data).hexdigest()
            if self._enqueue_bytes(f"artifacts/{ref}.json", data):
                return ref
        except Exception:
            self._issue("artifact_serialization_failed")
        return None

    def __enter__(self) -> EvidenceRun:
        self._worker.start()
        self._enqueue(f"segments/{self.segment_id}/manifest.json", self._manifest)
        self._token = _current.set(self)
        self.event("segment.started")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        _current.reset(self._token)
        self.event("segment.finished", execution_status="failed" if exc_type else "success")
        # Drain with a deadline; a stalled filesystem cannot block the method forever.
        deadline = time.monotonic() + self.write_wait_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.001)
        if self._queue.unfinished_tasks:
            self._issue("write_wait_limit")
        if not self._failed.is_set():
            self._enqueue(f"segments/{self.segment_id}/complete.json", {"event_count": self._count})
        else:
            self._enqueue(f"segments/{self.segment_id}/issues.json", {"issues": sorted(self._issues)})
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.001)
        self._closed.set()
        self._worker.join(timeout=max(0.0, deadline - time.monotonic()))

    @contextmanager
    def operation(self, kind: str, **facts: Any) -> Iterator[str]:
        span_id = identity()
        self.event(kind + ".started", span_id=span_id, **facts)
        token = _span.set(span_id)
        started = time.monotonic_ns()
        try:
            yield span_id
        except BaseException as exc:
            self.event(kind + ".finished", span_id=span_id, execution_status="failed",
                       error_type=type(exc).__name__, duration_ns=time.monotonic_ns() - started)
            raise
        else:
            self.event(kind + ".finished", span_id=span_id, execution_status="success",
                       duration_ns=time.monotonic_ns() - started)
        finally:
            _span.reset(token)


def current_run() -> EvidenceRun | None:
    return _current.get()


def submit_with_evidence(executor: Any, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Propagate context only at Squrve-owned executor boundaries."""
    context = contextvars.copy_context()
    return executor.submit(context.run, fn, *args, **kwargs)


def instrument_actor(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def traced(self: Any, item: Any, *args: Any, **kwargs: Any) -> Any:
        run = current_run()
        if run is None or _actor.get() == id(self):
            return fn(self, item, *args, **kwargs)
        span_id = identity()
        sample_ref = None
        if isinstance(item, (int, str)):
            sample_ref = hashlib.sha256(str(item).encode("utf-8")).hexdigest()
        run.event("actor.started", span_id=span_id, actor_class=type(self).__name__,
                  sample_ref=sample_ref)
        token = _span.set(span_id)
        actor_token = _actor.set(id(self))
        start = time.monotonic_ns()
        try:
            result = fn(self, item, *args, **kwargs)
        except BaseException as exc:
            run.event("actor.finished", span_id=span_id, execution_status="failed",
                      error_type=type(exc).__name__, duration_ns=time.monotonic_ns() - start)
            raise
        else:
            duration = time.monotonic_ns() - start
            output_ref = run.artifact(result) if run.capture_outputs else None
            run.event("actor.finished", span_id=span_id, execution_status="success",
                      duration_ns=duration, output_ref=output_ref)
            return result
        finally:
            _actor.reset(actor_token)
            _span.reset(token)
    return traced


def read_evidence(directory: str | Path) -> dict[str, Any]:
    """Read immutable evidence and reject corrupt identities or missing content."""
    root = Path(directory)
    events: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}
    issues: list[str] = []
    invalid = False
    coverage_profiles: set[str] = set()
    segments = sorted((root / "segments").glob("*")) if (root / "segments").exists() else []
    for segment in segments:
        try:
            manifest = json.loads((segment / "manifest.json").read_bytes())
            if manifest["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported_schema_version")
            if manifest.get("execution_segment_id") != segment.name:
                raise ValueError("manifest_segment_identity_conflict")
            coverage = manifest.get("coverage", {})
            coverage_profiles.add(str(coverage.get("profile", "unknown")))
            segment_events = []
            for path in sorted((segment / "events").glob("*.json")):
                record = json.loads(path.read_bytes())
                if record["event_id"] != path.stem or record["execution_segment_id"] != segment.name:
                    raise ValueError("event_identity_conflict")
                if record["schema_version"] != SCHEMA_VERSION or record["run_id"] != manifest["run_id"]:
                    raise ValueError("event_manifest_conflict")
                segment_events.append(record)
            events.extend(segment_events)
            marker = segment / "complete.json"
            if not marker.exists():
                issues.append("segment_incomplete")
            elif json.loads(marker.read_bytes())["event_count"] != len(segment_events):
                issues.append("event_count_mismatch")
            issue_marker = segment / "issues.json"
            if issue_marker.exists():
                declared = json.loads(issue_marker.read_bytes()).get("issues", [])
                issues.extend(str(item) for item in declared)
        except (ValueError, KeyError, OSError, TypeError):
            invalid = True
            issues.append("invalid_segment")
    for path in sorted((root / "artifacts").glob("*.json")):
        try:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != path.stem:
                raise ValueError("artifact_hash_mismatch")
            artifacts[path.stem] = json.loads(data)
        except (ValueError, OSError):
            invalid = True
            issues.append("invalid_artifact")
    started = {e["span_id"] for e in events if e["type"].endswith(".started") and "span_id" in e}
    ended = {e["span_id"] for e in events if e["type"].endswith(".finished") and "span_id" in e}
    if started - ended:
        issues.append("unobserved_completion")
    for event in events:
        if event.get("output_ref") and event["output_ref"] not in artifacts:
            issues.append("output_artifact_unavailable")
    status = "invalid" if invalid else "unavailable" if not segments else "partial" if issues else "complete"
    coverage_status = "unknown"
    if segments:
        try:
            manifests = [json.loads((segment / "manifest.json").read_bytes()) for segment in segments]
            coverage_profiles = {str(item.get("coverage", {}).get("profile", "unknown")) for item in manifests}
            if len(coverage_profiles) > 1:
                issues.append("coverage_profile_conflict")
            statuses = {str(item.get("coverage", {}).get("status", "unknown")) for item in manifests}
            coverage_status = next(iter(statuses)) if len(statuses) == 1 else "partial"
        except (OSError, ValueError, TypeError):
            coverage_status = "unknown"
    return {"schema_version": SCHEMA_VERSION, "events": events, "artifacts": artifacts,
            "issues": issues, "evidence_status": status, "coverage_status": coverage_status}
