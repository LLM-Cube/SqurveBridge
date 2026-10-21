"""The Demo query API must find bundled benchmarks whatever the working directory."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from demo import api_server
from demo.query_runtime import SqurveDemo


SPIDER_INSTALLED = (Path(api_server._project_root) / "benchmarks" / "spider" / "dev" / "dataset.json").is_file()


@unittest.skipUnless(SPIDER_INSTALLED, "Spider is not installed; this reproduces a bug that needs the real benchmark")
class QueryBenchmarkPathTests(unittest.TestCase):
    def setUp(self):
        api_server.app.config.update(TESTING=True)
        api_server._demo_instances.clear()
        self.addCleanup(api_server._demo_instances.clear)
        self.original_cwd = os.getcwd()
        self.addCleanup(os.chdir, self.original_cwd)

    def query_from(self, working_directory):
        os.chdir(working_directory)
        provider = {"id": "deepseek", "default_model": "deepseek-flash", "models": ["deepseek-flash"], "configured": True}
        generated = {"status": "success", "sql": "SELECT count(*) FROM singer", "trace": []}
        with mock.patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local"}, clear=False), \
                mock.patch.object(api_server, "_llm_provider_catalog", return_value=[provider]), \
                mock.patch.object(SqurveDemo, "generate_sql", return_value=generated):
            return api_server.app.test_client().post(
                "/api/query",
                json={"question": "How many singers are there?", "db_id": "concert_singer", "provider": "deepseek"},
            )

    def test_query_engine_starts_from_the_repository_root(self):
        response = self.query_from(api_server._project_root)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertEqual(response.json["sql"], "SELECT count(*) FROM singer")

    def test_query_engine_starts_from_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            response = self.query_from(elsewhere)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])


class QueryByMethodTests(unittest.TestCase):
    def setUp(self):
        api_server.app.config.update(TESTING=True)
        self.calls = []

    def query(self, **payload):
        provider = {"id": "deepseek", "default_model": "deepseek-flash", "models": ["deepseek-flash"], "configured": True}

        class FakeDemo:
            def generate_sql(inner, **kwargs):
                self.calls.append(kwargs)
                return {"status": "success", "sql": "SELECT 1", "trace": []}

        body = {"question": "How many singers are there?", "db_id": "concert_singer", "provider": "deepseek", **payload}
        database = {"id": "concert_singer", "benchmark": "spider", "schema_path": "schema.json", "db_path": "db.sqlite"}
        with mock.patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local"}, clear=False), \
                mock.patch.object(api_server, "_find_database", return_value=database), \
                mock.patch.object(api_server, "_llm_provider_catalog", return_value=[provider]), \
                mock.patch.object(api_server, "_get_demo", return_value=FakeDemo()):
            return api_server.app.test_client().post("/api/query", json=body)

    def test_a_method_runs_its_reproduce_workflow_for_the_database_benchmark(self):
        config = next(item for item in api_server._config_catalog() if (item["method"], item["dataset"]) == ("c3sql", "spider"))
        expected = [stage["actor"] for stage in config["stages"] if stage.get("actor")]
        response = self.query(method="c3sql")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.calls[0]["use_workflow"])
        self.assertEqual(self.calls[0]["workflow_actor_lis"], expected)
        self.assertEqual(response.json["method"], "c3sql")

    def test_a_method_without_a_config_for_that_benchmark_is_rejected(self):
        response = self.query(method="no-such-method")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
