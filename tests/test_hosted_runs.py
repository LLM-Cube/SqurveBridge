"""Hosted Space: visitors run reproduce configs on their own key, isolated per session."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from demo.session_auth import SessionCredentialRegistry, SqlCredential

HOSTED = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}
CREDENTIAL = dict(provider="qwen", model="qwen-plus", api_key="dummy-visitor-key")


class HostedRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api = api_server

    def setUp(self):
        api = self.api
        api._sql_credentials = SessionCredentialRegistry()
        for registry in (
            api._jobs, api._job_owners, api._job_credentials,
            api._experiment_owners, api._experiment_credentials,
        ):
            registry.clear()
        api._experiments._experiments.clear()
        env = patch.dict(os.environ, HOSTED, clear=False)
        env.start()
        self.addCleanup(env.stop)
        launch = patch.object(api, "_spawn_evaluation_job", return_value=True)
        launch.start()
        self.addCleanup(launch.stop)
        self.visitor = api.app.test_client()
        self.other = api.app.test_client()

    def connect(self, client, **overrides):
        with patch.object(self.api, "_validate_sql_credential", return_value=None):
            response = client.put("/api/sql-auth", json={**CREDENTIAL, **overrides})
        self.assertEqual(response.status_code, 200)

    def start(self, client, **overrides):
        body = {"dataset": "spider", "method": "c3sql", "sample_limit": 3, **overrides}
        return client.post("/api/evaluations", json=body)

    def test_running_a_config_requires_the_visitors_own_key(self):
        response = self.start(self.visitor)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["code"], "credential_required")
        self.assertEqual(self.api._jobs, {})

    def test_the_visitors_key_reaches_only_their_job_process(self):
        self.connect(self.visitor)
        response = self.start(self.visitor)
        self.assertEqual(response.status_code, 202)
        job_id = response.json["job_id"]
        env = self.api._job_environment(self.api._jobs[job_id])
        self.assertEqual(env["SQURVE_LLM_PROVIDER"], "qwen")
        self.assertEqual(env["SQURVE_LLM_MODEL"], "qwen-plus")
        self.assertEqual(env["QWEN_API_KEY"], "dummy-visitor-key")
        self.assertNotIn("dummy-visitor-key", response.get_data(as_text=True))
        self.assertNotIn("dummy-visitor-key", repr(self.api._jobs[job_id]))

    def test_other_visitors_cannot_see_or_control_the_job(self):
        self.connect(self.visitor)
        self.connect(self.other, api_key="dummy-other-key")
        job_id = self.start(self.visitor).json["job_id"]
        self.assertEqual(self.visitor.get(f"/api/evaluations/{job_id}").status_code, 200)
        self.assertEqual(self.other.get(f"/api/evaluations/{job_id}").status_code, 404)
        self.assertEqual(self.other.post(f"/api/evaluations/{job_id}/cancel").status_code, 404)
        self.assertEqual(self.other.get("/api/session").json["jobs"], [])
        self.assertEqual([job["job_id"] for job in self.visitor.get("/api/session").json["jobs"]], [job_id])

    def test_hosted_runs_are_capped_and_one_at_a_time(self):
        self.connect(self.visitor)
        too_big = self.start(self.visitor, sample_limit=50)
        self.assertEqual(too_big.status_code, 400)
        self.assertEqual(self.start(self.visitor).status_code, 202)
        second = self.start(self.visitor)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json["code"], "run_in_progress")

    def test_the_space_has_a_global_cap_on_running_jobs(self):
        self.connect(self.visitor)
        self.api._jobs.update({
            f"busy{i}": {"job_id": f"busy{i}", "status": "running"} for i in range(self.api._HOSTED_MAX_ACTIVE_JOBS)
        })
        response = self.start(self.visitor)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["code"], "capacity_reached")

    def test_experiments_use_the_creators_key_and_stay_private(self):
        self.connect(self.visitor)
        cells = [{"method": "c3sql", "dataset": "spider"}]
        response = self.visitor.post(
            "/api/experiments",
            json={"cells": cells, "sampling": {"limit": 3, "mode": "slice", "seed": 42}},
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        experiment_id = response.json["experiment_id"]
        job_id = next(iter(self.api._job_owners))
        env = self.api._job_environment(self.api._jobs[job_id])
        self.assertEqual(env["QWEN_API_KEY"], "dummy-visitor-key")
        self.assertEqual(self.other.get(f"/api/experiments/{experiment_id}").status_code, 404)
        self.assertEqual(self.other.get("/api/experiments").json["experiments"], [])
        self.assertEqual(len(self.visitor.get("/api/experiments").json["experiments"]), 1)

    def test_experiments_without_a_key_are_refused(self):
        response = self.visitor.post("/api/experiments", json={"cells": [{"method": "c3sql", "dataset": "spider"}]})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["code"], "credential_required")

    def test_local_runs_still_use_the_process_environment(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local"}):
            job_id = self.start(self.visitor)
            self.assertEqual(job_id.status_code, 202)
            self.assertEqual(self.api._job_credentials, {})


if __name__ == "__main__":
    unittest.main()
