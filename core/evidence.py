"""Run-local, bounded runtime evidence. No method operations are performed here."""
from __future__ import annotations

import contextvars
import hashlib
import inspect
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


class ConditionValue:
    TRUE = "true"
    FALSE = "false"
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

    def __init__(self, version: str, actor_category: str, fields: Mapping[str, Any]):
        normalized: dict[str, Any] = {}
        for name, requirement in fields.items():
            if requirement == "conditional":
                requirement = {
                    "requirement": "conditional",
                    "condition": f"{name}_applicable",
                }
            level = requirement.get("requirement") if isinstance(requirement, Mapping) else requirement
            if level not in {"required", "conditional", "optional", "opt_in"}:
                raise ValueError("invalid_capability_requirement")
            if level == "conditional" and (
                not isinstance(requirement, Mapping) or not isinstance(requirement.get("condition"), str)
            ):
                raise ValueError("conditional_requirement_missing_condition")
            normalized[name] = requirement
        super().__init__(version, actor_category=actor_category, fields=normalized)


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


DEFAULT_CAPABILITY_PROFILES = [
    CapabilityProfile(f"{category}-v1", category, fields)
    for category, fields in {
        "reducer": {"actor_class": "required", "output_ref": {
            "requirement": "conditional", "condition": "structured_output_produced"}},
        "parser": {"actor_class": "required", "output_ref": {
            "requirement": "conditional", "condition": "structured_output_produced"}},
        "decomposer": {"actor_class": "required", "decomposition_output_ref": {
            "requirement": "conditional", "condition": "decomposition_output_produced"}},
        "generator": {"actor_class": "required", "candidate_ids": {
            "requirement": "conditional", "condition": "output_candidates_produced"}},
        "scaler": {"actor_class": "required", "input_candidate_ids": {
            "requirement": "conditional", "condition": "input_candidates_observed"},
            "produced_candidate_ids": {
                "requirement": "conditional", "condition": "output_candidates_produced"}},
        "optimizer": {"actor_class": "required", "optimization_attempt_id": "required",
            "output_candidate_ids": {
                "requirement": "conditional", "condition": "multi_candidate_optimization"}},
        "selector": {"actor_class": "required", "ordered_candidate_pool": "required",
            "selected_candidate_id": {
                "requirement": "conditional", "condition": "selection_unambiguous"}},
        "agent": {"actor_class": "required", "output_ref": {
            "requirement": "conditional", "condition": "output_retained"}},
        "nest": {"actor_class": "required", "output_ref": {
            "requirement": "conditional", "condition": "output_retained"}},
    }.items()
]


def evaluate_metric_dependencies(evidence: Mapping[str, Any], profile: AcceptanceProfile) -> dict[str, dict[str, Any]]:
    """Evaluate each metric independently; unavailable metrics do not erase valid ones."""
    result: dict[str, dict[str, Any]] = {}
    for metric, dependencies in profile.values["metric_dependencies"].items():
        missing = []
        for field in dependencies:
            value = evidence.get(field)
            incomplete = isinstance(value, Mapping) and any(
                value.get(key) in {"partial", "unknown", Availability.COLLECTION_FAILED, Availability.DISABLED}
                for key in ("availability", "fetch_completeness", "preservation_completeness", "payload_completeness")
            )
            if value is None or incomplete:
                missing.append(field)
        result[metric] = {"status": "unavailable" if missing else "computable", "missing": missing,
                          "profile_version": profile.version}
    return result


def acceptance_gate(metric_results: Mapping[str, Mapping[str, Any]], required_metrics: set[str]) -> dict[str, Any]:
    missing = sorted(metric for metric in required_metrics
                     if metric not in metric_results or metric_results[metric].get("status") != "computable")
    return {"accepted": not missing, "missing_required_metrics": missing}


def aggregate_usage(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate non-overlapping reported usage while preserving opaque records."""
    attempt_records = [e for e in events if e.get("type") == "usage.reported" and e.get("scope") == "attempt"]
    logical_records = [e for e in events if e.get("type") == "usage.reported" and e.get("scope") == "logical_call"]
    opaque = [e for e in events if e.get("type") == "usage.reported"
              and e.get("scope") not in {"attempt", "logical_call"}]
    attempt_logical_ids = {e.get("logical_call_id") for e in attempt_records if e.get("logical_call_id")}
    selected = attempt_records + [e for e in logical_records if e.get("logical_call_id") not in attempt_logical_ids]
    totals: dict[str, int] = {}
    for event in selected:
        values = event.get("usage") if isinstance(event.get("usage"), Mapping) else event
        assert isinstance(values, Mapping)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            value = values.get(key)
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return {"totals": totals, "selected_records": len(selected), "opaque_records": len(opaque),
            "complete": not opaque}


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
        max_total_bytes: int = 67_108_864,
        write_wait_s: float = 1.0, prior_segment: str | None = None,
        prior_run_id: str | None = None, checkpoint_ref: str | None = None, capture_outputs: bool = False,
        capture_sql: bool = False,
        capability_profiles: list[CapabilityProfile] | None = None,
        collection_config: CollectionConfig | None = None,
        acceptance_profile: AcceptanceProfile | None = None,
        configuration_identity: str | None = None,
    ):
        if max_bytes <= 0 or max_total_bytes <= 0 or queue_size <= 0 or write_wait_s < 0:
            raise ValueError("invalid_collection_limits")
        self.directory = Path(directory)
        self.run_id = run_id
        self.segment_id = identity()
        self.max_bytes = max_bytes
        self.max_total_bytes = max_total_bytes
        self.write_wait_s = write_wait_s
        self.capture_outputs = capture_outputs
        self.capture_sql = capture_sql
        self.capability_profiles = list(DEFAULT_CAPABILITY_PROFILES if capability_profiles is None else capability_profiles)
        self.collection_config = collection_config or CollectionConfig()
        self.acceptance_profile = acceptance_profile
        self.prior_run_id = prior_run_id
        self.prior_segment = prior_segment
        self.checkpoint_ref = checkpoint_ref
        self._candidates_by_sql: dict[str, list[dict[str, Any]]] = {}
        self._candidate_pools: dict[int, tuple[Any, list[dict[str, Any]]]] = {}
        self._actor_candidate_inputs: dict[str, tuple[Any, list[str]]] = {}
        self._actor_profiles: dict[str, CapabilityProfile] = {}
        self._actor_observed_fields: dict[str, set[str]] = {}
        self._actor_conditions: dict[str, dict[str, str]] = {}
        required = set(self.collection_config.values["required_fields"])
        required.update(
            name
            for profile in self.capability_profiles
            for name, requirement in profile.values.get("fields", {}).items()
            if requirement == "required"
            or (isinstance(requirement, Mapping) and requirement.get("requirement") == "required")
        )
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
        self._total_bytes = 0
        self._observed_fields: set[str] = set()
        self._worker = threading.Thread(target=self._write, daemon=True)
        self._manifest = {
            "schema_version": SCHEMA_VERSION, "run_id": run_id,
            "configuration_identity": configuration_identity,
            "execution_segment_id": self.segment_id,
            "prior_run_id": prior_run_id, "prior_segment": prior_segment, "checkpoint_ref": checkpoint_ref,
            "coverage": {"profile": "runtime-evidence-v1", "status": "partial",
                         "supported": ["actor_minimum_facts", "candidate_lineage", "dataset_mutation",
                                       "task_workflow", "llm_usage", "db_completeness", "offline_acceptance"],
                         "unsupported": ["provider_hidden_retries", "method_specific_optional_payloads"]},
            "retention": {"output_artifacts": "opt_in" if capture_outputs else "disabled",
                          "sql_text": "opt_in" if capture_sql else "disabled"},
            "profiles": {"capabilities": [p.to_dict() for p in self.capability_profiles],
                         "collection": self.collection_config.to_dict(),
                         "acceptance": acceptance_profile.to_dict() if acceptance_profile else None},
            "limits": {"max_bytes": max_bytes, "max_total_bytes": max_total_bytes,
                       "queue_size": queue_size, "write_wait_s": write_wait_s},
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
            data = b"".join(chunks)
            with self._lock:
                exceeded = self._total_bytes + len(data) > self.max_total_bytes
                if not exceeded:
                    self._total_bytes += len(data)
            if exceeded:
                self._issue("run_size_limit")
                return False
            self._queue.put_nowait((name, data))
            return True
        except queue.Full:
            with self._lock:
                self._total_bytes = max(0, self._total_bytes - len(data))
            self._issue("queue_limit")
        except Exception:
            self._issue("serialization_failed")
        return False

    def _enqueue_bytes(self, name: str, data: bytes) -> bool:
        try:
            with self._lock:
                exceeded = self._total_bytes + len(data) > self.max_total_bytes
                if not exceeded:
                    self._total_bytes += len(data)
            if exceeded:
                self._issue("run_size_limit")
                return False
            self._queue.put_nowait((name, data))
            return True
        except queue.Full:
            with self._lock:
                self._total_bytes = max(0, self._total_bytes - len(data))
            self._issue("queue_limit")
            return False

    def _enqueue_control(self, name: str, value: Any) -> bool:
        try:
            data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
            if len(data) > self.max_bytes:
                return False
            self._queue.put_nowait((name, data))
            return True
        except (TypeError, ValueError, queue.Full):
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
                self._observed_fields.update(key for key, value in facts.items() if value is not None)
                explicit_span = facts.get("span_id")
                active_span = (
                    explicit_span
                    if isinstance(explicit_span, str) and explicit_span in self._actor_observed_fields
                    else _span.get()
                )
                if active_span in self._actor_observed_fields:
                    self._actor_observed_fields[active_span].update(
                        key for key, value in facts.items() if value is not None
                    )
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

    def value_ref(self, value: Any) -> str | None:
        """Return a bounded content identity without retaining the payload."""
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True).iterencode(value):
                part = chunk.encode("utf-8")
                size += len(part)
                if size > self.max_bytes:
                    return None
                digest.update(part)
            return digest.hexdigest()
        except Exception:
            return None

    def field(self, name: str, availability: str, *, condition: str | None = None,
              value_ref: str | None = None) -> str | None:
        if availability == Availability.PRESENT:
            self._observed_fields.add(name)
        return self.event("field.availability", field=name, availability=availability,
                          condition=condition, value_ref=value_ref)

    def condition(self, name: str, value: str, *, basis_refs: list[str] | None = None) -> str | None:
        if value not in {ConditionValue.TRUE, ConditionValue.FALSE, ConditionValue.UNKNOWN}:
            raise ValueError("invalid_condition_value")
        span_id = current_span_id()
        if span_id in self._actor_conditions:
            self._actor_conditions[span_id][name] = value
        if value == ConditionValue.UNKNOWN:
            self._issue("condition_unknown")
        return self.event("condition.evaluated", condition=name, value=value, basis_refs=basis_refs or [])

    def candidate(self, sql_text: str, *, producer_span_id: str | None = None,
                  parent_candidate_id: str | None = None) -> dict[str, Any]:
        content_ref = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
        occurrence_id = identity()
        self.event("candidate.created", candidate_occurrence_id=occurrence_id, sql_ref=content_ref,
                   sql_text=sql_text, producer_span_id=producer_span_id,
                   parent_candidate_id=parent_candidate_id)
        candidate = {"candidate_occurrence_id": occurrence_id, "sql_ref": content_ref, "sql_text": sql_text}
        self._candidates_by_sql.setdefault(sql_text, []).append(candidate)
        return candidate

    def candidate_for_input(self, sql_text: str) -> dict[str, Any]:
        candidate = self.candidate(sql_text)
        self.event("candidate.provenance", candidate_occurrence_id=candidate["candidate_occurrence_id"],
                   producer_attribution="unattributed")
        return candidate

    def candidates_for_pool(self, sql_values: list[str], pool_object: Any = None) -> list[dict[str, Any]]:
        known_pool = self._candidate_pools.get(id(pool_object)) if pool_object is not None else None
        if known_pool is not None and known_pool[0] is pool_object:
            return list(known_pool[1])
        # SQL content equality cannot establish occurrence identity. If the
        # exact in-memory pool is unavailable, retain the observed inputs as
        # new, explicitly unattributed occurrences instead of guessing lineage.
        pool = [self.candidate_for_input(sql) for sql in sql_values]
        if pool_object is not None:
            self._candidate_pools[id(pool_object)] = (pool_object, list(pool))
        return pool

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

    def adapt_legacy_record(self, record: Mapping[str, Any], *, source_schema_version: int,
                            adapter_version: str) -> dict[str, Any]:
        original_ref = self.artifact(dict(record))
        if original_ref is None:
            self._issue("legacy_original_unavailable")
        observed = {key: record[key] for key in ("type", "timestamp_ns", "execution_status") if key in record}
        adapted = {"source_schema_version": source_schema_version, "target_schema_version": SCHEMA_VERSION,
                   "adapter_version": adapter_version, "original_record_ref": original_ref,
                   "observed": observed}
        self.legacy_adapt(source_schema_version, SCHEMA_VERSION, adapter_version, original_ref or "")
        return adapted

    def reexport_event(self, record: Mapping[str, Any]) -> bool:
        event_id = record.get("event_id")
        segment_id = record.get("execution_segment_id")
        if not self._safe_path_identity(event_id) or not self._safe_path_identity(segment_id):
            self._issue("reexport_identity_missing")
            return False
        return self._enqueue(f"segments/{segment_id}/events/{event_id}.json", dict(record))

    @staticmethod
    def _safe_path_identity(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value not in {"", ".", ".."}
            and "/" not in value
            and "\\" not in value
            and "\x00" not in value
        )

    def begin_actor(self, span_id: str, profile: CapabilityProfile | None) -> None:
        self._actor_observed_fields[span_id] = set()
        self._actor_conditions[span_id] = {}
        if profile is not None:
            self._actor_profiles[span_id] = profile

    def observe_actor_candidate_inputs(self, values: Any) -> None:
        span_id = current_span_id()
        if span_id is None:
            return
        self._actor_candidate_inputs[span_id] = (values, _sql_values(values))

    def finish_actor(self, span_id: str) -> None:
        profile = self._actor_profiles.pop(span_id, None)
        observed = self._actor_observed_fields.pop(span_id, set())
        conditions = self._actor_conditions.pop(span_id, {})
        self._actor_candidate_inputs.pop(span_id, None)
        if profile is None:
            return
        required = {
            name for name, requirement in profile.values.get("fields", {}).items()
            if requirement == "required"
            or (isinstance(requirement, Mapping) and requirement.get("requirement") == "required")
        }
        missing = required - observed
        for name, requirement in profile.values.get("fields", {}).items():
            if not isinstance(requirement, Mapping) or requirement.get("requirement") != "conditional":
                continue
            condition_name = requirement.get("condition")
            condition_value = conditions.get(condition_name) if isinstance(condition_name, str) else None
            if condition_value == ConditionValue.TRUE and name not in observed:
                missing.add(name)
            elif condition_value is None:
                self._issue("conditional_condition_unobserved")
        if missing:
            self._issue("required_evidence_missing")
            self.event(
                "actor.evidence_incomplete",
                span_id=span_id,
                capability_profile_ref=profile.version,
                missing_required_fields=sorted(missing),
            )

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
        if self.prior_run_id or self.prior_segment or self.checkpoint_ref:
            self.resume(prior_run_id=self.prior_run_id, prior_segment=self.prior_segment,
                        checkpoint_ref=self.checkpoint_ref)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        _current.reset(self._token)
        required = set(self.collection_config.values["required_fields"])
        if required - self._observed_fields:
            self._issue("required_evidence_missing")
        self.event("segment.finished", execution_status="failed" if exc_type else "success")
        # Drain with a deadline; a stalled filesystem cannot block the method forever.
        deadline = time.monotonic() + self.write_wait_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.001)
        if self._queue.unfinished_tasks:
            self._issue("write_wait_limit")
        if not self._failed.is_set():
            self._enqueue_control(f"segments/{self.segment_id}/complete.json", {"event_count": self._count})
        else:
            self._enqueue_control(f"segments/{self.segment_id}/issues.json", {"issues": sorted(self._issues)})
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


def current_span_id() -> str | None:
    return _span.get()


def submit_with_evidence(
    executor: Any,
    fn: Callable[..., Any],
    *args: Any,
    evidence_task: bool = False,
    **kwargs: Any,
) -> Any:
    """Propagate evidence context and optionally record an actual runtime task."""
    run = current_run()
    if run is None:
        return executor.submit(fn, *args, **kwargs)
    context = contextvars.copy_context()
    scheduling_span_id = current_span_id()
    task_id = run.task_created() if evidence_task else None
    if task_id is not None:
        run.workflow_event("scheduled", task_id=task_id, scheduled_by_span_id=scheduling_span_id)

    def invoke() -> Any:
        if task_id is not None:
            run.workflow_event("started", task_id=task_id)
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            if task_id is not None:
                run.workflow_event(
                    "finished",
                    task_id=task_id,
                    execution_status="failed",
                    error_type=type(exc).__name__,
                )
            raise
        if task_id is not None:
            run.workflow_event("finished", task_id=task_id, execution_status="success")
        return result

    return executor.submit(context.run, invoke)


def _sql_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _record_actor_specific(run: EvidenceRun, fn: Callable[..., Any], actor: Any, item: Any,
                           args: tuple[Any, ...], kwargs: Mapping[str, Any], result: Any,
                           span_id: str, category: str, output_ref: str | None,
                           dataset_manifest: Mapping[str, Any], dataset_sql_inputs: list[str]) -> None:
    try:
        bound = inspect.signature(fn).bind_partial(actor, item, *args, **kwargs)
        inputs = {key: value for key, value in bound.arguments.items() if key not in {"self", "item"}}
    except (TypeError, ValueError):
        inputs = dict(kwargs)
    input_manifest = {name: {"availability": Availability.PRESENT, "value_ref": run.value_ref(value)}
                      for name, value in inputs.items()}
    input_manifest.update(dataset_manifest)
    run.event("actor.input_manifest", actor_category=category, fields=input_manifest)

    observed_pool = run._actor_candidate_inputs.get(span_id)
    sql_inputs = observed_pool[1] if observed_pool is not None else (
        _sql_values(inputs.get("pred_sql")) or dataset_sql_inputs
    )
    pool_object = observed_pool[0] if observed_pool is not None else inputs.get("pred_sql")
    sql_outputs = _sql_values(result)
    if isinstance(result, Mapping):
        sql_outputs = _sql_values(result.get("pred_sql") or result.get("sql") or result.get("SQL")
                                  or result.get("query"))

    if category == "generator":
        run.condition("output_candidates_produced", ConditionValue.TRUE if sql_outputs else ConditionValue.FALSE)
        candidates = [run.candidate(sql, producer_span_id=span_id) for sql in sql_outputs]
        if isinstance(result, (list, tuple)):
            run._candidate_pools[id(result)] = (result, list(candidates))
        run.event("generator.output", candidate_ids=[c["candidate_occurrence_id"] for c in candidates],
                  output_ref=output_ref)
    elif category == "scaler":
        run.condition("input_candidates_observed", ConditionValue.TRUE if sql_inputs else ConditionValue.FALSE)
        run.condition("output_candidates_produced", ConditionValue.TRUE if sql_outputs else ConditionValue.FALSE)
        input_candidates = run.candidates_for_pool(sql_inputs, pool_object)
        produced = [run.candidate(sql, producer_span_id=span_id,
                                  parent_candidate_id=input_candidates[0]["candidate_occurrence_id"]
                                  if len(input_candidates) == 1 else None) for sql in sql_outputs]
        run.event("scaler.output", input_candidate_ids=[c["candidate_occurrence_id"] for c in input_candidates],
                  produced_candidate_ids=[c["candidate_occurrence_id"] for c in produced])
    elif category == "optimizer":
        input_candidates = run.candidates_for_pool(sql_inputs, pool_object)
        attempts = []
        if len(input_candidates) == 1 and len(sql_outputs) == 1:
            run.condition("multi_candidate_optimization", ConditionValue.FALSE)
            attempts.append(run.optimization_attempt(input_candidates[0], sql_outputs[0]))
        else:
            run.condition("multi_candidate_optimization", ConditionValue.TRUE)
            produced = [run.candidate(sql, producer_span_id=span_id) for sql in sql_outputs]
            run.event("optimizer.output", optimization_attempt_id=identity(),
                      input_candidate_ids=[c["candidate_occurrence_id"] for c in input_candidates],
                      output_candidate_ids=[c["candidate_occurrence_id"] for c in produced],
                      parentage="unknown" if len(input_candidates) > 1 else "explicit")
        if attempts:
            run.event("optimizer.output", attempts=attempts)
    elif category == "selector":
        pool = run.candidates_for_pool(sql_inputs, pool_object)
        selected_indices = [index for index, sql in enumerate(sql_inputs) if sql in sql_outputs]
        selected = pool[selected_indices[0]] if len(selected_indices) == 1 else None
        run.condition("selection_unambiguous", ConditionValue.TRUE if selected else ConditionValue.FALSE)
        run.event("selector.decision", ordered_candidate_pool=[c["candidate_occurrence_id"] for c in pool],
                  selected_candidate_id=selected["candidate_occurrence_id"] if selected else None,
                  selection_identity="explicit" if selected else "ambiguous",
                  consumption="unknown")
    elif category in {"parser", "reducer"}:
        run.condition("structured_output_produced", ConditionValue.TRUE if result is not None else ConditionValue.FALSE)
        structured_ref = output_ref or (run.artifact(result) if result is not None else None)
        run.event(f"{category}.structured_output", output_ref=structured_ref,
                  output_availability=Availability.PRESENT if result is not None else Availability.NOT_PROVIDED)
    elif category == "decomposer":
        run.condition("decomposition_output_produced", ConditionValue.TRUE if result is not None else ConditionValue.FALSE)
        plan_ref = output_ref or (run.artifact(result) if result is not None else None)
        run.event("decomposer.output", decomposition_output_ref=plan_ref,
                  runtime_tasks_created=False)
    elif category in {"agent", "nest"}:
        run.condition("output_retained", ConditionValue.TRUE if output_ref else ConditionValue.FALSE)
        run.event(f"{category}.decision", output_ref=output_ref,
                  information_consumption="unknown")


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
        profile = matching_profiles[0] if matching_profiles else None
        run.begin_actor(span_id, profile)
        run.event("actor.started", span_id=span_id, actor_class=type(self).__name__,
                  actor_category=actor_category,
                  capability_profile_ref=(profile.version if profile else None),
                  sample_ref=sample_ref)
        token = _span.set(span_id)
        actor_token = _actor.set(id(self))
        start = time.monotonic_ns()
        dataset_manifest: dict[str, Any] = {}
        dataset_sql_inputs: list[str] = []
        dataset = getattr(self, "dataset", None)
        if dataset is not None and isinstance(item, int):
            try:
                row = dataset[item]
                for key in ("query", "pred_sql", "gold_schemas", "instance_schemas", "schema_links", "sub_questions"):
                    if key in row:
                        dataset_manifest[key] = {"availability": Availability.PRESENT,
                                                 "value_ref": run.value_ref(row[key])}
                dataset_sql_inputs = list(_sql_values(row.get("pred_sql")))
            except (IndexError, KeyError, TypeError):
                pass
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
            _record_actor_specific(run, fn, self, item, args, kwargs, result, span_id, category, output_ref,
                                   dataset_manifest, dataset_sql_inputs)
            run.event("actor.finished", span_id=span_id, execution_status="success",
                      duration_ns=duration, output_ref=output_ref)
            return result
        finally:
            run.finish_actor(span_id)
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
    def lifecycle_key(event: Mapping[str, Any]) -> tuple[str, str, str] | None:
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type.endswith((".started", ".finished")):
            return None
        base_type = event_type.rsplit(".", 1)[0]
        for key in ("span_id", "attempt_id", "operation_id", "logical_call_id", "task_id"):
            value = event.get(key)
            if isinstance(value, str):
                return base_type, key, value
        return None

    started = {key for event in events if event.get("type", "").endswith(".started")
               if (key := lifecycle_key(event)) is not None}
    ended = {key for event in events if event.get("type", "").endswith(".finished")
             if (key := lifecycle_key(event)) is not None}
    if started - ended:
        issues.append("unobserved_completion")
    artifact_reference_fields = {
        "output_ref",
        "result_ref",
        "decomposition_output_ref",
        "original_record_ref",
    }
    for event in events:
        for field in artifact_reference_fields:
            if field == "output_ref" and event.get("type") == "merge.observed":
                # Merge output_ref is a bounded content identity, not a retained artifact.
                continue
            reference = event.get(field)
            if reference and reference not in artifacts:
                issues.append(f"{field}_artifact_unavailable")
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


def evaluate_evidence(directory: str | Path, evidence_values: Mapping[str, Any],
                      profile: AcceptanceProfile, *, required_metrics: set[str] | None = None) -> dict[str, Any]:
    """Offline metric dependency and acceptance evaluation over persisted evidence."""
    bundle = read_evidence(directory)
    metrics = evaluate_metric_dependencies(evidence_values, profile)
    db_events = [event for event in bundle["events"] if event.get("type") == "db.finished"]
    incomplete_results = any(
        event.get("fetch_completeness") != "complete"
        or event.get("preservation_completeness") != "complete"
        for event in db_events
    )
    if incomplete_results:
        for metric, dependencies in profile.values["metric_dependencies"].items():
            if any("result" in dependency for dependency in dependencies):
                metrics[metric]["status"] = "unavailable"
                metrics[metric].setdefault("missing", []).append("complete_result_evidence")
    if bundle["evidence_status"] in {"invalid", "unavailable"}:
        for result in metrics.values():
            result["status"] = "unavailable"
            result.setdefault("missing", []).append("runtime_evidence")
    gate = acceptance_gate(metrics, required_metrics or set(metrics))
    return {"execution_status": bundle["execution_status"], "evidence_status": bundle["evidence_status"],
            "evaluation_status": "accepted" if gate["accepted"] else "rejected",
            "metrics": metrics, "acceptance": gate, "profile_version": profile.version}
