"""Experiments: a set of method × benchmark cells run with one shared sampling.

The scheduler owns queueing and per-cell state; evaluation processes stay
behind an `ExperimentBackend`, so the Demo API wires the real subprocess
runner and tests wire a fake one.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

ACTIVE_JOB_STATUSES = frozenset({"starting", "running", "resuming"})
MAX_CELLS = 24


class ExperimentBackend(Protocol):
    def launch(self, method: str, dataset: str, sampling: dict, experiment_id: str) -> str: ...

    def job(self, job_id: str) -> dict | None: ...

    def scores(self, job_id: str) -> dict | None: ...

    def cancel(self, job_id: str) -> None: ...

    def resume(self, job_id: str) -> tuple[bool, str | None]: ...


@dataclass
class Cell:
    cell_id: str
    method: str
    dataset: str
    job_id: str | None = None
    status: str = "queued"
    error_code: str | None = None
    progress: dict = field(default_factory=dict)
    run_id: str | None = None


@dataclass
class Experiment:
    experiment_id: str
    sampling: dict
    cells: list[Cell]
    created_at: float
    cancelled: bool = False
    events: list[dict] = field(default_factory=list)


class ExperimentNotFound(LookupError):
    pass


class ExperimentConflict(RuntimeError):
    pass


def _experiment_status(experiment: Experiment) -> str:
    statuses = [cell.status for cell in experiment.cells]
    if any(status in ACTIVE_JOB_STATUSES for status in statuses):
        return "running"
    if "queued" in statuses:
        return "queued" if not experiment.cancelled else "cancelled"
    if experiment.cancelled:
        return "cancelled"
    if all(status == "completed" for status in statuses):
        return "completed"
    if any(status == "completed" for status in statuses):
        return "partial"
    return "failed"


class ExperimentScheduler:
    """FIFO queue across experiments with one concurrency limit for all cells."""

    def __init__(self, backend: ExperimentBackend, concurrency: int = 2):
        self.backend = backend
        self.concurrency = max(1, int(concurrency))
        self._experiments: dict[str, Experiment] = {}
        self._lock = threading.RLock()

    def create(self, cells: list[tuple[str, str]], sampling: dict) -> dict:
        experiment_id = uuid.uuid4().hex[:10]
        experiment = Experiment(
            experiment_id=experiment_id,
            sampling=dict(sampling),
            cells=[
                Cell(cell_id=f"c{index + 1}", method=method, dataset=dataset)
                for index, (method, dataset) in enumerate(cells)
            ],
            created_at=time.time(),
        )
        with self._lock:
            self._experiments[experiment_id] = experiment
            self._record(experiment, {"type": "experiment_created"})
            self.tick()
            return self._snapshot(experiment)

    def tick(self) -> None:
        """Refresh cell states from the backend, then start queued cells."""
        with self._lock:
            for experiment in self._ordered():
                for cell in experiment.cells:
                    if cell.job_id and cell.status not in {"completed", "failed", "cancelled"} | {"queued"}:
                        self._refresh(experiment, cell)
            active = sum(
                1 for experiment in self._experiments.values()
                for cell in experiment.cells if cell.status in ACTIVE_JOB_STATUSES
            )
            for experiment in self._ordered():
                if experiment.cancelled:
                    continue
                for cell in experiment.cells:
                    if active >= self.concurrency:
                        return
                    if cell.status != "queued":
                        continue
                    self._launch(experiment, cell)
                    if cell.status in ACTIVE_JOB_STATUSES:
                        active += 1

    def get(self, experiment_id: str) -> dict:
        with self._lock:
            self.tick()
            return self._snapshot(self._find(experiment_id))

    def list(self) -> list[dict]:
        with self._lock:
            self.tick()
            return [self._snapshot(experiment) for experiment in reversed(self._ordered())]

    def events(self, experiment_id: str, cursor: int) -> tuple[list[dict], int]:
        with self._lock:
            self.tick()
            experiment = self._find(experiment_id)
            cursor = max(0, min(int(cursor), len(experiment.events)))
            return [dict(event) for event in experiment.events[cursor:]], len(experiment.events)

    def cancel(self, experiment_id: str, cell_id: str | None = None) -> dict:
        with self._lock:
            experiment = self._find(experiment_id)
            cells = experiment.cells if cell_id is None else [self._cell(experiment, cell_id)]
            if cell_id is None:
                experiment.cancelled = True
            for cell in cells:
                if cell.status == "queued":
                    self._set_status(experiment, cell, "cancelled")
                elif cell.status in ACTIVE_JOB_STATUSES and cell.job_id:
                    self.backend.cancel(cell.job_id)
                    self._refresh(experiment, cell)
            self.tick()
            return self._snapshot(experiment)

    def resume(self, experiment_id: str, cell_id: str) -> dict:
        with self._lock:
            experiment = self._find(experiment_id)
            cell = self._cell(experiment, cell_id)
            if cell.status not in {"failed", "cancelled"} or not cell.job_id:
                raise ExperimentConflict("Only a failed or cancelled cell that has started can be resumed.")
            resumed, message = self.backend.resume(cell.job_id)
            if not resumed:
                raise ExperimentConflict(message or "This cell cannot be resumed.")
            experiment.cancelled = False
            cell.error_code = None
            self._refresh(experiment, cell)
            return self._snapshot(experiment)

    def cell_job(self, experiment_id: str, method: str, dataset: str) -> tuple[Cell, dict | None] | None:
        """The cell for (method, dataset) in an experiment and its finished scores, if any."""
        with self._lock:
            self.tick()
            experiment = self._find(experiment_id)
            cell = next((item for item in experiment.cells if (item.method, item.dataset) == (method, dataset)), None)
            if cell is None:
                return None
            scores = self.backend.scores(cell.job_id) if cell.job_id and cell.status == "completed" else None
            return cell, scores

    def experiment_sampling(self, experiment_id: str) -> dict:
        with self._lock:
            return dict(self._find(experiment_id).sampling)

    # -- internals -----------------------------------------------------------

    def _ordered(self) -> list[Experiment]:
        return sorted(self._experiments.values(), key=lambda item: item.created_at)

    def _find(self, experiment_id: str) -> Experiment:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentNotFound(experiment_id)
        return experiment

    @staticmethod
    def _cell(experiment: Experiment, cell_id: str) -> Cell:
        cell = next((item for item in experiment.cells if item.cell_id == cell_id), None)
        if cell is None:
            raise ExperimentNotFound(cell_id)
        return cell

    def _launch(self, experiment: Experiment, cell: Cell) -> None:
        try:
            cell.job_id = self.backend.launch(cell.method, cell.dataset, experiment.sampling, experiment.experiment_id)
        except Exception:  # noqa: BLE001 - any launch failure fails only this cell
            cell.error_code = "launch_failed"
            self._set_status(experiment, cell, "failed")
            return
        self._refresh(experiment, cell)

    def _refresh(self, experiment: Experiment, cell: Cell) -> None:
        job = self.backend.job(cell.job_id) if cell.job_id else None
        if job is None:
            return
        progress = job.get("progress") or {}
        public_progress = {
            "completed": progress.get("completed"),
            "total": progress.get("total"),
            "current_stage": progress.get("current_stage"),
        }
        if public_progress != cell.progress:
            cell.progress = public_progress
            self._record(experiment, {"type": "cell_progress", "cell_id": cell.cell_id, "progress": public_progress})
        cell.run_id = job.get("run_id") or cell.run_id
        status = str(job.get("status") or "running")
        if status == "failed" and not cell.error_code:
            cell.error_code = "evaluation_failed"
        self._set_status(experiment, cell, status)

    def _set_status(self, experiment: Experiment, cell: Cell, status: str) -> None:
        if cell.status == status:
            return
        cell.status = status
        self._record(experiment, {"type": "cell_status", "cell_id": cell.cell_id, "status": status})

    @staticmethod
    def _record(experiment: Experiment, event: dict) -> None:
        experiment.events.append({**event, "at": time.time()})

    def _snapshot(self, experiment: Experiment) -> dict:
        queued = [
            (item.created_at, cell.cell_id, item.experiment_id)
            for item in self._ordered() if not item.cancelled
            for cell in item.cells if cell.status == "queued"
        ]
        positions = {(exp_id, cell_id): index + 1 for index, (_at, cell_id, exp_id) in enumerate(queued)}
        return {
            "experiment_id": experiment.experiment_id,
            "status": _experiment_status(experiment),
            "sampling": dict(experiment.sampling),
            "created_at": experiment.created_at,
            "cells": [
                {
                    "cell_id": cell.cell_id,
                    "method": cell.method,
                    "dataset": cell.dataset,
                    "job_id": cell.job_id,
                    "run_id": cell.run_id,
                    "status": cell.status,
                    "progress": dict(cell.progress),
                    "queue_position": positions.get((experiment.experiment_id, cell.cell_id)),
                    "error_code": cell.error_code,
                }
                for cell in experiment.cells
            ],
        }
