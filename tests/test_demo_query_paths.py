"""The Demo query API must find bundled benchmarks whatever the working directory."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from demo import api_server
from demo.query_runtime import SqurveDemo


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


if __name__ == "__main__":
    unittest.main()
