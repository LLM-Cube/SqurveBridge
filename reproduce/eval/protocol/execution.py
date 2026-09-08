from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Literal

from pydantic import Field

from .models import StrictModel


class ExecutionTiming(StrictModel):
    query_s: float | None = Field(default=None, ge=0)
    connection_s: float | None = Field(default=None, ge=0)
    fetch_s: float | None = Field(default=None, ge=0)
    materialization_s: float | None = Field(default=None, ge=0)
    total_s: float | None = Field(default=None, ge=0)
    clock: Literal["monotonic"] = "monotonic"
    scope: str = "connection+query+fetch+materialization"


class ExecutionResult(StrictModel):
    execution_id: str
    run_id: str
    instance_id: str
    candidate_id: str
    stage_id: str | None = None
    database_snapshot: str
    backend: str = "sqlite"
    dialect: str = "sqlite"
    status: Literal["success", "execution_error", "timeout", "unsupported", "missing_input", "ground_truth_unavailable"]
    rows: list[tuple[Any, ...]] | None = None
    columns: list[str] | None = None
    execution_complete: bool = False
    fetch_complete: bool = False
    preservation_complete: bool = False
    observed_row_count: int | None = Field(default=None, ge=0)
    known_total_row_count: int | None = Field(default=None, ge=0)
    backend_error_code: str | None = None
    backend_error_source: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)
    reuse_key: str | None = None
    reused_from_execution_id: str | None = None

    @property
    def comparable(self) -> bool:
        return (self.status == "success" and self.execution_complete and self.fetch_complete
                and self.preservation_complete and self.rows is not None)


class BackendCapabilities(StrictModel):
    backend: str
    timeout: bool
    cancellation: bool
    schema_reflection: bool
    explain: bool


class SQLExecutor:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._cache: dict[str, ExecutionResult] = {}
        self.capabilities = BackendCapabilities(backend="sqlite", timeout=False, cancellation=True,
                                                schema_reflection=True, explain=True)

    @staticmethod
    def reuse_key(sql: str, database_snapshot: str, dialect: str,
                  credential_scope: str, session_settings: str) -> str | None:
        normalized = sql.lower()
        if any(token in normalized for token in ("random(", "current_timestamp", "datetime('now", "strftime('%s','now")):
            return None
        data = "\0".join((sql, database_snapshot, dialect, credential_scope, session_settings))
        return hashlib.sha256(data.encode()).hexdigest()

    def missing_input(self, run_id: str, instance_id: str, candidate_id: str, *,
                      stage_id: str = "evaluation.prediction",
                      database_snapshot: str = "unknown") -> ExecutionResult:
        return ExecutionResult(execution_id=f"{run_id}:{instance_id}:{candidate_id}:missing", run_id=run_id,
                               instance_id=instance_id, candidate_id=candidate_id, stage_id=stage_id,
                               database_snapshot=database_snapshot, status="missing_input")

    def unsupported(self, run_id: str, instance_id: str, candidate_id: str, capability: str, *,
                    stage_id: str = "evaluation.prediction",
                    database_snapshot: str = "unknown") -> ExecutionResult:
        return ExecutionResult(execution_id=f"{run_id}:{instance_id}:{candidate_id}:unsupported:{capability}",
                               run_id=run_id, instance_id=instance_id, candidate_id=candidate_id,
                               stage_id=stage_id, database_snapshot=database_snapshot,
                               status="unsupported", backend_error_code=f"unsupported_{capability}",
                               backend_error_source=self.capabilities.backend)

    def execute_sqlite(self, db_path: str, sql: str, *, run_id: str, instance_id: str,
                       candidate_id: str, database_snapshot: str, use_cache: bool = True,
                       efficiency_measurement: bool = False,
                       stage_id: str = "evaluation.prediction") -> ExecutionResult:
        key = self.reuse_key(sql, database_snapshot, "sqlite", "local-readonly", "default")
        if use_cache and not efficiency_measurement and key is not None and key in self._cache:
            source = self._cache[key]
            return source.model_copy(update={"execution_id": f"{run_id}:{instance_id}:{candidate_id}:reused",
                                             "run_id": run_id, "instance_id": instance_id,
                                             "candidate_id": candidate_id, "stage_id": stage_id,
                                             "database_snapshot": database_snapshot,
                                             "timing": ExecutionTiming(),
                                             "reused_from_execution_id": source.execution_id})
        from core.db_connect import execute_sql_observation

        observed = execute_sql_observation("sqlite", sql_query=sql, db_path=db_path, clock=self.clock)
        observed_timing = observed.pop("timing", {})
        result = ExecutionResult(
            execution_id=f"{run_id}:{instance_id}:{candidate_id}:execution", run_id=run_id,
            instance_id=instance_id, candidate_id=candidate_id, stage_id=stage_id,
            database_snapshot=database_snapshot, reuse_key=key,
            timing=ExecutionTiming.model_validate(observed_timing), **observed,
        )
        if use_cache and not efficiency_measurement and key is not None:
            self._cache[key] = result
        return result
