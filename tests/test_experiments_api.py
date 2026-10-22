"""Experiments: method × benchmark runs with shared sampling, tested through the HTTP API."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from demo.experiments import ExperimentScheduler

SAMPLING = {"limit": 20, "mode": "slice", "seed": 42}
READY_ENV = {"SQURVE_LLM_PROVIDER": "deepseek", "SQURVE_LLM_MODEL": "deepseek-flash", "DEEPSEEK_API_KEY": "test-value"}


class FakeBackend:
    """Stands in for evaluation subprocesses; tests move jobs between states."""

    def __init__(self):
        self.jobs = {}
        self.launched = []
        self.launched_stages = []
        self.cancelled = []
        self.resumed = []
        self.fail_launch = set()

    def launch(self, method, dataset, sampling, experiment_id, stages):
        if (method, dataset) in self.fail_launch:
            raise RuntimeError("could not start")
        job_id = f"job{len(self.jobs) + 1}"
        self.jobs[job_id] = {
            "job_id": job_id,
            "method": method,
            "dataset": dataset,
            "status": "running",
            "run_id": None,
            "progress": {"completed": 0, "total": sampling["limit"], "current_stage": "reduce"},
        }
        self.launched.append((method, dataset, dict(sampling), experiment_id))
        self.launched_stages.append(dict(stages))
        return job_id

    def job(self, job_id):
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    def scores(self, job_id):
        return self.jobs.get(job_id, {}).get("scores")

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.jobs[job_id]["status"] = "cancelled"

    def resume(self, job_id):
        self.resumed.append(job_id)
        self.jobs[job_id]["status"] = "running"
        return True, None

    def advance(self, job_id, *, completed=None, stage=None):
        progress = self.jobs[job_id]["progress"]
        if completed is not None:
            progress["completed"] = completed
        if stage is not None:
            progress["current_stage"] = stage

    def finish(self, job_id, status="completed", scores=None):
        self.jobs[job_id]["status"] = status
        if scores is not None:
            self.jobs[job_id]["scores"] = scores
            self.jobs[job_id]["run_id"] = scores.get("run_id")


class ExperimentApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api_server = api_server

    def setUp(self):
        self.backend = FakeBackend()
        self.original = self.api_server._experiments
        self.api_server._experiments = ExperimentScheduler(self.backend, concurrency=2)
        self.addCleanup(setattr, self.api_server, "_experiments", self.original)
        self.client = self.api_server.app.test_client()
        env = patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local", **READY_ENV}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def create(self, cells, sampling=SAMPLING):
        return self.client.post("/api/experiments", json={"cells": cells, "sampling": sampling})

    def snapshot(self, experiment_id):
        return self.client.get(f"/api/experiments/{experiment_id}").json


class CreateExperimentTests(ExperimentApiTestCase):
    def test_unknown_cells_are_rejected_with_a_reason_per_cell(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "no-such-method", "dataset": "spider"},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json["cells"],
            [{"method": "no-such-method", "dataset": "spider", "error_code": "config_missing"}],
        )
        self.assertIn("no-such-method/spider (config_missing)", response.json["message"])
        self.assertEqual(self.backend.launched, [])

    def test_cells_without_a_provider_key_are_rejected(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            response = self.create([{"method": "c3sql", "dataset": "spider"}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["cells"][0]["error_code"], "credential_missing")

    def test_an_experiment_needs_cells_and_valid_sampling(self):
        self.assertEqual(self.create([]).status_code, 400)
        self.assertEqual(
            self.create([{"method": "c3sql", "dataset": "spider"}], {"limit": 7, "mode": "slice"}).status_code,
            400,
        )

    def test_cells_beyond_the_concurrency_limit_wait_in_a_queue(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
            {"method": "resdsql", "dataset": "spider"},
            {"method": "c3sql", "dataset": "spider"},
        ])
        self.assertEqual(response.status_code, 202)
        experiment = self.snapshot(response.json["experiment_id"])
        self.assertEqual(len(experiment["cells"]), 3, "duplicate cells are merged")
        self.assertEqual([cell["status"] for cell in experiment["cells"]], ["running", "running", "queued"])
        self.assertEqual(experiment["cells"][2]["queue_position"], 1)
        self.assertEqual(experiment["status"], "running")
        self.assertEqual(experiment["sampling"], SAMPLING)
        self.assertEqual(len(self.backend.launched), 2)
        self.assertEqual(self.backend.launched[0][2], SAMPLING)


class ExperimentProgressTests(ExperimentApiTestCase):
    def start_three(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
            {"method": "resdsql", "dataset": "spider"},
        ])
        return response.json["experiment_id"]

    def test_a_queued_cell_starts_when_a_running_cell_finishes(self):
        experiment_id = self.start_three()
        self.backend.finish("job1")
        experiment = self.snapshot(experiment_id)
        self.assertEqual([cell["status"] for cell in experiment["cells"]], ["completed", "running", "running"])
        self.assertIsNone(experiment["cells"][2]["queue_position"])
        self.assertEqual(len(self.backend.launched), 3)

    def test_experiment_status_summarises_its_cells(self):
        experiment_id = self.start_three()
        for job_id in ("job1", "job2"):
            self.backend.finish(job_id)
        self.snapshot(experiment_id)
        self.backend.finish("job3", status="failed")
        experiment = self.snapshot(experiment_id)
        self.assertEqual(experiment["status"], "partial")
        self.assertEqual(experiment["cells"][2]["error_code"], "evaluation_failed")

    def test_all_completed_cells_complete_the_experiment(self):
        experiment_id = self.create([{"method": "c3sql", "dataset": "spider"}]).json["experiment_id"]
        self.backend.finish("job1")
        self.assertEqual(self.snapshot(experiment_id)["status"], "completed")

    def test_a_cell_that_cannot_start_fails_alone(self):
        self.backend.fail_launch.add(("dinsql", "spider"))
        experiment_id = self.start_three()
        cells = self.snapshot(experiment_id)["cells"]
        self.assertEqual([cell["status"] for cell in cells], ["running", "failed", "running"])
        self.assertEqual(cells[1]["error_code"], "launch_failed")

    def test_events_report_progress_and_status_changes_after_a_cursor(self):
        experiment_id = self.create([{"method": "c3sql", "dataset": "spider"}]).json["experiment_id"]
        first = self.client.get(f"/api/experiments/{experiment_id}/events?cursor=0").json
        self.backend.advance("job1", completed=5, stage="generate")
        self.backend.finish("job1")
        later = self.client.get(f"/api/experiments/{experiment_id}/events?cursor={first['cursor']}").json
        kinds = [(event["type"], event.get("status")) for event in later["events"]]
        self.assertIn(("cell_progress", None), kinds)
        self.assertIn(("cell_status", "completed"), kinds)
        self.assertEqual(later["status"], "completed")
        again = self.client.get(f"/api/experiments/{experiment_id}/events?cursor={later['cursor']}").json
        self.assertEqual(again["events"], [])

    def test_unknown_experiment_is_not_found(self):
        self.assertEqual(self.client.get("/api/experiments/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/experiments/nope/events").status_code, 404)

    def test_experiments_are_listed_newest_first(self):
        first = self.create([{"method": "c3sql", "dataset": "spider"}]).json["experiment_id"]
        second = self.create([{"method": "dinsql", "dataset": "spider"}]).json["experiment_id"]
        listed = [item["experiment_id"] for item in self.client.get("/api/experiments").json["experiments"]]
        self.assertEqual(listed, [second, first])


class ExperimentControlTests(ExperimentApiTestCase):
    def test_cancelling_an_experiment_stops_running_cells_and_drops_queued_ones(self):
        experiment_id = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
            {"method": "resdsql", "dataset": "spider"},
        ]).json["experiment_id"]
        response = self.client.post(f"/api/experiments/{experiment_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([cell["status"] for cell in response.json["cells"]], ["cancelled"] * 3)
        self.assertEqual(response.json["status"], "cancelled")
        self.assertEqual(sorted(self.backend.cancelled), ["job1", "job2"])
        self.assertEqual(len(self.backend.launched), 2, "queued cells never start after cancel")

    def test_cancelling_one_cell_lets_the_queue_move_on(self):
        experiment_id = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
            {"method": "resdsql", "dataset": "spider"},
        ]).json["experiment_id"]
        response = self.client.post(f"/api/experiments/{experiment_id}/cells/c1/cancel")
        self.assertEqual([cell["status"] for cell in response.json["cells"]], ["cancelled", "running", "running"])

    def test_a_failed_cell_can_be_resumed(self):
        experiment_id = self.create([{"method": "c3sql", "dataset": "spider"}]).json["experiment_id"]
        self.backend.finish("job1", status="failed")
        self.snapshot(experiment_id)
        response = self.client.post(f"/api/experiments/{experiment_id}/cells/c1/resume")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["cells"][0]["status"], "running")
        self.assertIsNone(response.json["cells"][0]["error_code"])
        self.assertEqual(self.backend.resumed, ["job1"])

    def test_a_running_cell_cannot_be_resumed(self):
        experiment_id = self.create([{"method": "c3sql", "dataset": "spider"}]).json["experiment_id"]
        response = self.client.post(f"/api/experiments/{experiment_id}/cells/c1/resume")
        self.assertEqual(response.status_code, 409)

    def test_resuming_a_cell_waits_for_capacity(self):
        experiment_id = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
            {"method": "resdsql", "dataset": "spider"},
        ]).json["experiment_id"]
        self.backend.finish("job1", status="failed")
        self.snapshot(experiment_id)  # job3 starts in the freed slot; two cells now run
        response = self.client.post(f"/api/experiments/{experiment_id}/cells/c1/resume")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.backend.resumed, [])

    def test_cancelling_after_some_cells_finished_is_partial(self):
        experiment_id = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "dinsql", "dataset": "spider"},
        ]).json["experiment_id"]
        self.backend.finish("job1")
        response = self.client.post(f"/api/experiments/{experiment_id}/cancel")
        self.assertEqual(response.json["status"], "partial")

    def test_hosted_space_cannot_run_experiments(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.create([{"method": "c3sql", "dataset": "spider"}])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.backend.launched, [])


if __name__ == "__main__":
    unittest.main()
