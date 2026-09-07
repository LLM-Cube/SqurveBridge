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
from typing import Any, Callable, Iterator, Mapping

SCHEMA_VERSION = 1
_current: contextvars.ContextVar[EvidenceRun | None] = contextvars.ContextVar("evidence_run", default=None)
_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("evidence_span", default=None)
_actor: contextvars.ContextVar[int | None] = contextvars.ContextVar("evidence_actor", default=None)


class Availability:
    PRESENT = "present"
    NOT_PROVIDED = "not_provided"
    NOT_APPLICABLE = "not_applicable"
    DISABLED = "disabled"
    COLLECTION_FAILED = "collection_failed"
    REDACTED = "redacted"
    UNKNOWN = "unknown"


class _Profile:
    kind = "profile"

    def __init__(self, version: str, **values: Any):
        self.version = version
        self.values = values

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version, **self.values}


class CapabilityProfile(_Profile):
    kind = "capability"

    def __init__(self, version: str, actor_category: str, fields: Mapping[str, str]):
        super().__init__(version, actor_category=actor_category, fields=dict(fields))


class CollectionConfig(_Profile):
    kind = "collection"

    def __init__(self, version: str = "collection-v1", *, required_fields: set[str] | None = None,
                 enabled_fields: set[str] | None = None):
        super().__init__(version, required_fields=sorted(required_fields or set()),
                         enabled_fields=None if enabled_fields is None else sorted(enabled_fields))


class AcceptanceProfile(_Profile):
    kind = "acceptance"

    def __init__(self, version: str, metric_dependencies: Mapping[str, set[str]]):
        super().__init__(version, metric_dependencies={k: sorted(v) for k, v in metric_dependencies.items()})


def evaluate_metric_dependencies(evidence: Mapping[str, Any], profile: AcceptanceProfile) -> dict[str, dict[str, Any]]:
    """Evaluate each metric independently; unavailable metrics do not erase valid ones."""
    result: dict[str, dict[str, Any]] = {}
    for metric, dependencies in profile.values["metric_dependencies"].items():
        missing = []
        for field in dependencies:
            value = evidence.get(field)
            if value is None or (isinstance(value, Mapping) and value.get("availability") in {
                    Availability.UNKNOWN, Availability.DISABLED, Availability.COLLECTION_FAILED,
                    Availability.REDACTED}):
                missing.append(field)
        result[metric] = {"status": "unavailable" if missing else "computable", "missing": missing,
                          "profile_version": profile.version}
    return result


def acceptance_gate(metric_results: Mapping[str, Mapping[str, Any]], required_metrics: set[str]) -> dict[str, Any]:
    missing = sorted(metric for metric in required_metrics
                     if metric not in metric_results or metric_results[metric].get("status") != "computable")
    return {"accepted": not missing, "missing_required_metrics": missing}


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
        capture_sql: bool = False,
        capability_profiles: list[CapabilityProfile] | None = None,
        collection_config: CollectionConfig | None = None,
        acceptance_profile: AcceptanceProfile | None = None,
    ):
        if max_bytes <= 0 or queue_size <= 0 or write_wait_s < 0:
            raise ValueError("invalid_collection_limits")
        self.directory = Path(directory)
        self.run_id = run_id
        self.segment_id = identity()
        self.max_bytes = max_bytes
        self.write_wait_s = write_wait_s
        self.capture_outputs = capture_outputs
        self.capture_sql = capture_sql
        self.capability_profiles = capability_profiles or []
        self.collection_config = collection_config or CollectionConfig()
        self.acceptance_profile = acceptance_profile
        required = set(self.collection_config.values["required_fields"])
        enabled_value = self.collection_config.values["enabled_fields"]
        enabled = set(enabled_value or [])
        if enabled_value is not None and not required.issubset(enabled):
            raise ValueError("required_field_disabled")
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
            "retention": {"output_artifacts": "opt_in" if capture_outputs else "disabled",
                          "sql_text": "opt_in" if capture_sql else "disabled"},
            "profiles": {"capabilities": [p.to_dict() for p in self.capability_profiles],
                         "collection": self.collection_config.to_dict(),
                         "acceptance": acceptance_profile.to_dict() if acceptance_profile else None},
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

    def field(self, name: str, availability: str, *, condition: str | None = None,
              value_ref: str | None = None) -> str | None:
        return self.event("field.availability", field=name, availability=availability,
                          condition=condition, value_ref=value_ref)

    def candidate(self, sql_text: str, *, producer_span_id: str | None = None,
                  parent_candidate_id: str | None = None) -> dict[str, Any]:
        content_ref = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
        occurrence_id = identity()
        self.event("candidate.created", candidate_occurrence_id=occurrence_id, sql_ref=content_ref,
                   sql_text=sql_text, producer_span_id=producer_span_id,
                   parent_candidate_id=parent_candidate_id)
        return {"candidate_occurrence_id": occurrence_id, "sql_ref": content_ref, "sql_text": sql_text}

    def logical_call(self, kind: str, **facts: Any) -> dict[str, Any]:
        logical_call_id = identity()
        self.event("logical_call.started", logical_call_id=logical_call_id, operation_kind=kind, **facts)
        return {"logical_call_id": logical_call_id, "operation_kind": kind}

    def attempt(self, logical_call: Mapping[str, Any], status: str, *, physical: bool = True,
                retry_observability: str = "opaque", **facts: Any) -> dict[str, Any]:
        attempt_id = identity()
        self.event("llm.attempt", logical_call_id=logical_call["logical_call_id"], attempt_id=attempt_id,
                   status=status, physical=physical, retry_observability=retry_observability, **facts)
        return {"logical_call_id": logical_call["logical_call_id"], "attempt_id": attempt_id}

    def usage(self, call: Mapping[str, Any], *, scope: str, source: str, **reported: Any) -> str | None:
        return self.event("usage.reported", logical_call_id=call.get("logical_call_id"),
                          attempt_id=call.get("attempt_id"), scope=scope, source=source, usage=reported)

    def db_operation(self, sql_text: str, *, execution_status: str, fetch_completeness: str,
                     preservation_completeness: str, **facts: Any) -> str | None:
        sql_ref = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
        operation_id = identity()
        self.event("db.started", operation_id=operation_id, sql_ref=sql_ref,
                   sql_text=sql_text if self.capture_sql else None,
                   sql_text_availability=Availability.PRESENT if self.capture_sql else Availability.DISABLED)
        return self.event("db.finished", operation_id=operation_id, sql_ref=sql_ref,
                          execution_status=execution_status, fetch_completeness=fetch_completeness,
                          preservation_completeness=preservation_completeness, **facts)

    def producer_change(self, field: str, *, old_ref: str | None, new_ref: str | None,
                        producer_basis_refs: list[str] | None = None) -> str | None:
        return self.event("producer.change", field=field, old_ref=old_ref, new_ref=new_ref,
                          producer_basis_refs=producer_basis_refs or [],
                          producer_attribution="explicit" if producer_basis_refs else "unattributed")

    def merge(self, object_type: str, input_refs: list[str], output_ref: str) -> str | None:
        return self.event("merge.observed", object_type=object_type, input_refs=input_refs,
                          output_ref=output_ref,
                          lineage_status="explicit" if input_refs and output_ref else "unattributed")

    def optimization_attempt(self, input_candidate: Mapping[str, Any], output_sql: str,
                             *, edit_intent: str | None = None) -> dict[str, Any]:
        """Record an optimizer attempt; exact extracted SQL equality preserves identity."""
        changed = output_sql != input_candidate.get("sql_text")
        output = (self.candidate(output_sql, parent_candidate_id=input_candidate.get("candidate_occurrence_id"))
                  if changed else dict(input_candidate))
        attempt_id = identity()
        self.event("optimizer.attempt", optimization_attempt_id=attempt_id,
                   input_candidate_id=input_candidate.get("candidate_occurrence_id"),
                   output_candidate_id=output.get("candidate_occurrence_id"), changed=changed,
                   edit_intent=edit_intent)
        return {"optimization_attempt_id": attempt_id, "input_candidate_id": input_candidate.get("candidate_occurrence_id"),
                "output_candidate_id": output.get("candidate_occurrence_id"), "changed": changed}

    def task_created(self, *, plan_ref: str | None = None, parent_task_id: str | None = None,
                     dependency_refs: list[str] | None = None) -> str:
        task_id = identity()
        self.event("task.created", task_id=task_id, plan_ref=plan_ref, parent_task_id=parent_task_id,
                   dependency_refs=dependency_refs or [], dependencies_explicit=dependency_refs is not None)
        return task_id

    def workflow_event(self, kind: str, *, task_id: str | None = None,
                       parent_task_id: str | None = None, dependency_refs: list[str] | None = None,
                       **facts: Any) -> str | None:
        return self.event("workflow." + kind, task_id=task_id, parent_task_id=parent_task_id,
                          dependency_refs=dependency_refs or [], **facts)

    def legacy_adapt(self, source_schema_version: int, target_schema_version: int,
                     adapter_version: str, original_record_ref: str) -> str | None:
        return self.event("legacy.adapted", source_schema_version=source_schema_version,
                          target_schema_version=target_schema_version, adapter_version=adapter_version,
                          original_record_ref=original_record_ref, fabricated_facts=[])

    def evaluate(self, evidence: Mapping[str, Any], *, required_metrics: set[str] | None = None) -> dict[str, Any]:
        if self.acceptance_profile is None:
            raise ValueError("acceptance_profile_required")
        metrics = evaluate_metric_dependencies(evidence, self.acceptance_profile)
        required = required_metrics or set(metrics)
        gate = acceptance_gate(metrics, required)
        self.event("evaluation.finished", profile_version=self.acceptance_profile.version,
                   metrics=metrics, acceptance=gate)
        return {"metrics": metrics, "acceptance": gate}

    def resume(self, *, prior_run_id: str | None, prior_segment: str | None,
               checkpoint_ref: str | None) -> str | None:
        return self.event("execution.resumed", prior_run_id=prior_run_id, prior_segment=prior_segment,
                          checkpoint_ref=checkpoint_ref, new_segment_id=self.segment_id)

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
        second_deadline = time.monotonic() + self.write_wait_s
        while self._queue.unfinished_tasks and time.monotonic() < second_deadline:
            time.sleep(0.001)
        self._closed.set()
        self._worker.join(timeout=max(0.0, second_deadline - time.monotonic()))

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
        actor_category = type(self).__module__.split(".")[-2] if "." in type(self).__module__ else "unknown"
        matching_profiles = [p for p in run.capability_profiles
                             if p.values.get("actor_category") == actor_category]
        run.event("actor.started", span_id=span_id, actor_class=type(self).__name__,
                  actor_category=actor_category,
                  capability_profile_ref=(matching_profiles[0].version if matching_profiles else None),
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
            category = type(self).__module__.split(".")[-2] if "." in type(self).__module__ else "unknown"
            if isinstance(result, Mapping):
                structured_keys = sorted(str(key) for key in result.keys()
                                         if str(key).lower() in {"sql", "query", "schema", "schemas", "links",
                                                                  "candidates", "selected", "tasks", "plan"})
                if structured_keys:
                    run.event("actor.structured_output", actor_category=category,
                              output_ref=output_ref, fields=structured_keys,
                              availability={key: Availability.PRESENT for key in structured_keys})
                sql_value = result.get("sql") or result.get("SQL") or result.get("query")
                if isinstance(sql_value, str):
                    run.candidate(sql_value, producer_span_id=span_id)
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
                if not isinstance(record.get("type"), str) or record["event_id"] != path.stem or record["execution_segment_id"] != segment.name:
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
    started = {e["span_id"] for e in events if isinstance(e.get("type"), str)
               and e["type"].endswith(".started") and "span_id" in e}
    ended = {e["span_id"] for e in events if isinstance(e.get("type"), str)
             and e["type"].endswith(".finished") and "span_id" in e}
    if started - ended:
        issues.append("unobserved_completion")
    for event in events:
        if event.get("output_ref") and event["output_ref"] not in artifacts:
            issues.append("output_artifact_unavailable")
    status = "invalid" if invalid else "unavailable" if not segments else "partial" if issues else "complete"
    coverage_status = "unknown"
    execution_status = "unknown"
    evaluation_status = "not_requested"
    if segments:
        try:
            manifests = [json.loads((segment / "manifest.json").read_bytes()) for segment in segments]
            coverage_profiles = {str(item.get("coverage", {}).get("profile", "unknown")) for item in manifests}
            if len(coverage_profiles) > 1:
                issues.append("coverage_profile_conflict")
            statuses = {str(item.get("coverage", {}).get("status", "unknown")) for item in manifests}
            coverage_status = next(iter(statuses)) if len(statuses) == 1 else "partial"
            finished = [e for e in events if e.get("type") == "segment.finished"]
            if finished:
                execution_status = "failed" if any(e.get("execution_status") == "failed" for e in finished) else "success"
            evaluation_events = [e for e in events if e.get("type") == "evaluation.finished"]
            if evaluation_events:
                evaluation_status = "accepted" if all(e.get("acceptance", {}).get("accepted")
                                                       for e in evaluation_events) else "rejected"
            elif any(item.get("profiles", {}).get("acceptance") for item in manifests):
                evaluation_status = "pending_dependency_check"
        except (OSError, ValueError, TypeError):
            coverage_status = "unknown"
    status = "invalid" if invalid else "unavailable" if not segments else "partial" if issues else "complete"
    return {"schema_version": SCHEMA_VERSION, "events": events, "artifacts": artifacts,
            "issues": issues, "evidence_status": status, "coverage_status": coverage_status,
            "execution_status": execution_status, "evaluation_status": evaluation_status}
