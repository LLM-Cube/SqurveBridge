import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from demo.session_auth import SessionCredentialRegistry


class SpaceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api_server = api_server

    def setUp(self):
        self.api_server._sql_credentials = SessionCredentialRegistry()
        self.client = self.api_server.app.test_client()

    def test_capabilities_identify_the_same_app_as_hf_hosted(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["deployment"]["target"], "hf-space")
        self.assertTrue(response.json["deployment"]["features"]["live_sql"])
        self.assertFalse(response.json["deployment"]["features"]["agent_chat"])
        self.assertTrue(response.json["deployment"]["features"]["session_sql_auth"])

    def test_qwen_sql_credentials_use_only_allowlisted_regions(self):
        china = self.api_server._credential_from_payload({
            "provider": "qwen",
            "model": "qwen-plus",
            "api_key": "test-key",
            "endpoint_id": "china",
        })
        international = self.api_server._credential_from_payload({
            "provider": "qwen",
            "model": "qwen-plus",
            "api_key": "test-key",
            "endpoint_id": "international",
        })
        self.assertEqual(china.base_url, self.api_server._QWEN_ENDPOINTS["china"])
        self.assertEqual(international.base_url, self.api_server._QWEN_ENDPOINTS["international"])
        workspace = self.api_server._credential_from_payload({
            "provider": "qwen",
            "model": "qwen-plus",
            "api_key": "test-key",
            "endpoint_id": "china_workspace",
            "workspace_id": "llm-valid-123",
        })
        self.assertEqual(
            workspace.base_url,
            "https://llm-valid-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
        with self.assertRaises(self.api_server.SqlAuthError) as invalid_workspace:
            self.api_server._credential_from_payload({
                "provider": "qwen",
                "model": "qwen-plus",
                "api_key": "test-key",
                "endpoint_id": "china_workspace",
                "workspace_id": "../attacker",
            })
        self.assertEqual(invalid_workspace.exception.code, "unsupported_workspace")
        with self.assertRaises(self.api_server.SqlAuthError) as raised:
            self.api_server._credential_from_payload({
                "provider": "qwen",
                "model": "qwen-plus",
                "api_key": "test-key",
                "endpoint_id": "https://attacker.invalid",
            })
        self.assertEqual(raised.exception.code, "unsupported_endpoint")

    def test_hosted_requests_never_restore_local_evaluation_jobs(self):
        self.api_server.app.config.update(TESTING=False)
        try:
            with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
                self.api_server, "_restore_evaluation_jobs_once"
            ) as restore:
                response = self.client.get("/api/capabilities")
            self.assertEqual(response.status_code, 200)
            restore.assert_not_called()
        finally:
            self.api_server.app.config.update(TESTING=True)

    def test_database_catalog_exposes_public_reference_benchmarks_without_paths(self):
        records = [
            ("college_2", "/tmp/college_2.sqlite", "/tmp/spider.json"),
            ("superhero", "/tmp/superhero.sqlite", "/tmp/bird.json"),
            ("ccks_macro", "/tmp/ccks_macro.sqlite", "/tmp/bull.json"),
            ("mimic_iv", "/tmp/mimic_iv.sqlite", "/tmp/ehr.json"),
        ]
        with patch.object(self.api_server, "get_available_databases", return_value=records), patch.object(
            self.api_server, "database_benchmark", side_effect=["spider", "bird", "bull-en", "ehrsql-2024"]
        ), patch.object(
            self.api_server, "_builtin_database_references", return_value=[]
        ), patch("demo.api_server.Path.is_file", return_value=True), patch(
            "demo.api_server.Path.stat", return_value=Mock(st_size=2048)
        ), patch("demo.api_server.Path.read_text", return_value="[]"), patch(
            "demo.api_server.Path.write_text"
        ), patch("demo.api_server.json.loads", return_value=[{
            "db_id": "college_2",
            "table_names_original": ["classroom"],
            "column_names_original": [[-1, "*"], [0, "building"]],
            "column_types": ["text", "text"],
        }]):
            response = self.client.get("/api/databases")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["databases"], [
            {"id": "college_2", "benchmark": "spider", "tables": ["classroom"], "size_bytes": 2048},
            {"id": "superhero", "benchmark": "bird", "tables": ["classroom"], "size_bytes": 2048},
            {"id": "ccks_macro", "benchmark": "bull-en", "tables": ["classroom"], "size_bytes": 2048},
            {"id": "mimic_iv", "benchmark": "ehrsql-2024", "tables": ["classroom"], "size_bytes": 2048},
        ])
        self.assertNotIn("db_path", response.get_data(as_text=True))
        self.assertNotIn("schema_path", response.get_data(as_text=True))

    def test_builtin_database_references_include_every_installed_database(self):
        from demo import query_runtime

        with tempfile.TemporaryDirectory() as directory, patch.object(
            query_runtime, "_project_root", Path(directory)
        ):
            for benchmark, database_relative, schema_relative in query_runtime.BUILTIN_BENCHMARK_DATABASES:
                database = Path(directory) / database_relative / f"{benchmark}.sqlite"
                database.parent.mkdir(parents=True, exist_ok=True)
                database.touch()
                schema = Path(directory) / schema_relative
                schema.parent.mkdir(parents=True, exist_ok=True)
                schema.write_text("[]", encoding="utf-8")

            references = query_runtime._builtin_database_references()

        self.assertEqual(
            {(db_id, benchmark) for db_id, benchmark, _db_path, _schema_path in references},
            {(benchmark, benchmark) for benchmark, _database, _schema in query_runtime.BUILTIN_BENCHMARK_DATABASES},
        )

    def test_builtin_database_references_namespace_colliding_ids(self):
        from demo import query_runtime

        with tempfile.TemporaryDirectory() as directory, patch.object(
            query_runtime, "_project_root", Path(directory)
        ):
            for benchmark, database_relative, schema_relative in query_runtime.BUILTIN_BENCHMARK_DATABASES:
                database = Path(directory) / database_relative / "formula_1.sqlite"
                database.parent.mkdir(parents=True, exist_ok=True)
                database.touch()
                schema = Path(directory) / schema_relative
                schema.parent.mkdir(parents=True, exist_ok=True)
                schema.write_text("[]", encoding="utf-8")

            references = query_runtime._builtin_database_references()

        self.assertIn(("formula_1", "spider"), {(db_id, benchmark) for db_id, benchmark, _db_path, _schema_path in references})
        self.assertIn(("bird__formula_1", "bird"), {(db_id, benchmark) for db_id, benchmark, _db_path, _schema_path in references})

    def test_hosted_space_rejects_local_only_mutations(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            for method, path in (
                ("post", "/api/provider"),
                ("post", "/api/databases/upload"),
                ("post", "/api/evaluations"),
                ("post", "/api/comparisons"),
            ):
                response = getattr(self.client, method)(path, json={})
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.json["reason"], "local_only")

    def test_hosted_space_rejects_raw_archive_file_content(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.get("/api/archive/public-run/files/scores.json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["reason"], "local_only")

    def test_hosted_space_exposes_the_squrve_provider_catalog_for_byok(self):
        environment = {
            "SQURVE_DEPLOYMENT_TARGET": "hf-space",
            "SQURVE_LLM_PROVIDER": "qwen",
            "SQURVE_LLM_MODEL": "qwen-plus",
        }
        with patch.dict(os.environ, environment, clear=False):
            response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        providers = {item["id"]: item for item in response.json["llm_providers"]}
        self.assertEqual(set(providers), set(self.api_server._provider_model_ids()))
        self.assertIn("qwen-plus", providers["qwen"]["models"])
        self.assertNotIn("env_var", providers["qwen"])

    def test_public_query_trace_excludes_runtime_inputs_outputs_and_errors(self):
        trace = self.api_server._serialize_public_query_trace([{
            "actor_name": "C3SQLGenerator",
            "actor_class": "C3SQLGenerator",
            "stage_name": "generate",
            "elapsed_s": 0.125,
            "inputs": {"question": "private benchmark question"},
            "result": "SELECT private_column FROM private_table",
            "row_delta": {"added": {"pred_sql": "private SQL"}},
            "error": "Bearer private-token",
            "timestamp_ms": 123,
        }])

        self.assertEqual(trace, [{
            "actor_name": "C3SQLGenerator",
            "stage": "generate",
            "status": "failed",
            "elapsed_ms": 125.0,
        }])
        serialized = json.dumps(trace)
        for forbidden in ("question", "SELECT", "row_delta", "Bearer", "timestamp"):
            self.assertNotIn(forbidden, serialized)

    def test_hosted_sql_auth_is_cookie_scoped_and_secret_free(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "sql-secret-a"}
        first = self.api_server.app.test_client()
        second = self.api_server.app.test_client()
        environment = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}

        with patch.dict(os.environ, environment, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", return_value=None
        ):
            saved = first.put("/api/sql-auth", json=payload)
            first_status = first.get("/api/sql-auth")
            second_status = second.get("/api/sql-auth")

        self.assertEqual(saved.status_code, 200)
        self.assertIn("Secure", saved.headers["Set-Cookie"])
        self.assertIn("HttpOnly", saved.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", saved.headers["Set-Cookie"])
        self.assertTrue(first_status.json["configured"])
        self.assertEqual(first_status.json["provider"], "qwen")
        self.assertFalse(second_status.json["configured"])
        self.assertNotIn("sql-secret-a", first_status.get_data(as_text=True))
        self.assertNotIn("sql-secret-a", saved.get_data(as_text=True))

    def test_testing_a_sql_key_does_not_activate_it(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "test-only-secret"}
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", return_value=None
        ):
            tested = self.client.post("/api/sql-auth/test", json=payload)
            status = self.client.get("/api/sql-auth")

        self.assertEqual(tested.status_code, 200)
        self.assertEqual(tested.json["validated"], True)
        self.assertFalse(status.json["configured"])

    def test_hosted_sql_auth_accepts_a_custom_provider_model_id(self):
        payload = {
            "provider": "qwen",
            "model": "qwen-custom-latest",
            "api_key": "custom-model-secret",
        }
        with patch.dict(
            os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False
        ), patch.object(self.api_server, "_validate_sql_credential", return_value=None):
            saved = self.client.put("/api/sql-auth", json=payload)
            status = self.client.get("/api/sql-auth")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(status.json["provider"], "qwen")
        self.assertEqual(status.json["model"], "qwen-custom-latest")
        self.assertNotIn("custom-model-secret", saved.get_data(as_text=True))

    def test_disconnecting_sql_auth_removes_the_session_credential(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "disconnect-secret"}
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", return_value=None
        ):
            self.client.put("/api/sql-auth", json=payload)
            disconnected = self.client.delete("/api/sql-auth")
            status = self.client.get("/api/sql-auth")

        self.assertEqual(disconnected.status_code, 200)
        self.assertFalse(status.json["configured"])
        self.assertIn("Max-Age=0", disconnected.headers["Set-Cookie"])
        self.assertNotIn("disconnect-secret", disconnected.get_data(as_text=True))

    def test_sql_session_timeout_and_capacity_eviction_remove_access(self):
        now = [100.0]
        self.api_server._sql_credentials = SessionCredentialRegistry(
            max_sessions=1, idle_timeout=30, clock=lambda: now[0]
        )
        first = self.api_server.app.test_client()
        second = self.api_server.app.test_client()
        environment = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}
        with patch.dict(os.environ, environment, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", return_value=None
        ):
            first.put("/api/sql-auth", json={"provider": "qwen", "model": "qwen-plus", "api_key": "evicted-sql-secret"})
            second.put("/api/sql-auth", json={"provider": "deepseek", "model": "deepseek-chat", "api_key": "active-sql-secret"})
            self.assertFalse(first.get("/api/sql-auth").json["configured"])
            self.assertTrue(second.get("/api/sql-auth").json["configured"])
            now[0] += 31
            expired = second.get("/api/sql-auth")

        self.assertFalse(expired.json["configured"])
        self.assertNotIn("evicted-sql-secret", expired.get_data(as_text=True))
        self.assertNotIn("active-sql-secret", expired.get_data(as_text=True))

    def test_hosted_sql_auth_rejects_cross_origin_mutation(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "cross-origin-secret"}
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.put(
                "/api/sql-auth",
                json=payload,
                headers={"Origin": "https://attacker.example"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["code"], "origin_forbidden")
        self.assertNotIn("cross-origin-secret", response.get_data(as_text=True))

    def test_hosted_sql_auth_accepts_the_forwarded_space_origin(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "forwarded-secret"}
        headers = {
            "Origin": "https://demo.hf.space",
            "X-Forwarded-Host": "demo.hf.space",
            "X-Forwarded-Proto": "https",
        }
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", return_value=None
        ):
            response = self.client.put("/api/sql-auth", json=payload, headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("forwarded-secret", response.get_data(as_text=True))

    def test_hosted_sql_auth_redacts_rejected_credentials(self):
        payload = {"provider": "qwen", "model": "qwen-plus", "api_key": "rejected-secret"}
        error = self.api_server.SqlAuthError("credential_rejected")
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
            self.api_server, "_validate_sql_credential", side_effect=error
        ):
            response = self.client.put("/api/sql-auth", json=payload)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["code"], "credential_rejected")
        self.assertNotIn("rejected-secret", response.get_data(as_text=True))

    def test_sql_auth_classifies_model_rate_limit_and_timeout_failures(self):
        credential = self.api_server._credential_from_payload({
            "provider": "qwen",
            "model": "qwen-plus",
            "api_key": "classification-secret",
            "endpoint_id": "china_workspace",
            "workspace_id": "llm-valid-123",
        })

        class ProviderError(Exception):
            def __init__(self, message, status_code=None):
                super().__init__(message)
                self.status_code = status_code

        cases = [
            (ProviderError("model not found", 404), "model_rejected"),
            (ProviderError("rate limit reached", 429), "provider_rate_limited"),
            (TimeoutError("request timed out"), "provider_timeout"),
        ]
        for exception, expected in cases:
            with self.subTest(expected=expected), patch.object(
                self.api_server, "_session_demo", side_effect=exception
            ), self.assertRaises(self.api_server.SqlAuthError) as raised:
                self.api_server._validate_sql_credential(credential)
            self.assertEqual(raised.exception.code, expected)

    def test_hosted_query_requires_and_uses_only_the_session_credential(self):
        database = {"id": "demo", "db_path": "/tmp/demo.sqlite", "schema_path": "/tmp/schema.json"}
        fake_demo = Mock()
        fake_demo.generate_sql.return_value = {"status": "success", "sql": "SELECT 1"}
        captured = []

        def session_demo(credential):
            captured.append(credential)
            return fake_demo

        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), patch.object(
            self.api_server, "_find_database", return_value=database
        ), patch.object(self.api_server, "_validate_sql_credential", return_value=None), patch.object(
            self.api_server, "_session_demo", side_effect=session_demo
        ):
            missing = self.client.post("/api/query", json={"question": "Count rows", "db_id": "demo"})
            self.client.put(
                "/api/sql-auth",
                json={"provider": "qwen", "model": "qwen-plus", "api_key": "session-sql-secret"},
            )
            generated = self.client.post(
                "/api/query",
                json={
                    "question": "Count rows",
                    "db_id": "demo",
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                },
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json["code"], "auth_required")
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.json["run_config"]["llm"], {"provider": "qwen", "model": "qwen-plus"})
        self.assertEqual(captured[-1].api_key, "session-sql-secret")
        self.assertNotIn("session-sql-secret", generated.get_data(as_text=True))

    def test_local_query_errors_do_not_echo_provider_secrets(self):
        database = {"id": "demo", "db_path": "/tmp/demo.sqlite", "schema_path": "/tmp/schema.json"}
        fake_secret = "sk-" + "proj-leaked-secret-value"
        fake_demo = Mock()
        fake_demo.generate_sql.return_value = {
            "status": "error",
            "sql": "",
            "message": f"Incorrect API key provided: {fake_secret}",
        }
        provider_config = {
            "id": "openai",
            "default_model": "gpt-4.1-mini",
            "models": ["gpt-4.1-mini"],
            "configured": True,
        }

        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local"}, clear=False), patch.object(
            self.api_server, "_find_database", return_value=database
        ), patch.object(self.api_server, "_llm_provider_catalog", return_value=[provider_config]), patch.object(
            self.api_server, "_provider_status", return_value={"provider": "openai"}
        ), patch.object(self.api_server, "_get_demo", return_value=fake_demo):
            response = self.client.post(
                "/api/query",
                json={
                    "question": "Count rows",
                    "db_id": "demo",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["status"], "error")
        self.assertNotIn(fake_secret, response.get_data(as_text=True))
        self.assertIn("rejected", response.json["message"].lower())

    def test_sanitize_error_message_covers_common_secret_shapes(self):
        sanitized = self.api_server._sanitize_error_message(
            "API key: api-secret; Bearer bearer-secret; password=pw-secret; "
            "client_secret=oauth-secret; AIzaSyLeakTestTokenValue"
        )
        self.assertNotIn("api-secret", sanitized)
        self.assertNotIn("bearer-secret", sanitized)
        self.assertNotIn("pw-secret", sanitized)
        self.assertNotIn("oauth-secret", sanitized)
        self.assertNotIn("AIzaSyLeakTestTokenValue", sanitized)

    def test_sanitize_error_message_removes_private_urls_and_paths(self):
        private_path = "/" + "Users/person/project/config.json"
        sanitized = self.api_server._sanitize_error_message(
            f"Request failed at https://private.example/v1 from {private_path}"
        )
        self.assertNotIn("private.example", sanitized)
        self.assertNotIn(private_path, sanitized)

    def test_squrve_demo_direct_key_bypasses_environment_resolution(self):
        from demo import query_runtime

        config = {
            "api_key": {},
            "llm": {"use": "qwen", "model_name": "qwen-plus"},
            "dataset": {},
            "database": {},
            "task": {"task_meta": []},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with patch.object(query_runtime, "resolve_config_api_keys") as resolve, patch.object(
                query_runtime, "Engine", return_value=Mock()
            ), patch.object(query_runtime.Router, "init_config") as init_config:
                query_runtime.SqurveDemo(
                    config_path=str(config_path),
                    provider="qwen",
                    model_name="qwen-plus",
                    api_key="test-direct-secret",
                )

        resolve.assert_not_called()
        routed_config = init_config.call_args.args[0]
        self.assertEqual(routed_config["api_key"]["qwen"], "test-direct-secret")

    def test_hosted_space_retains_core_llm_routes(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            self.assertNotEqual(self.client.post("/api/query", json={}).status_code, 403)
            self.assertNotEqual(self.client.post("/api/execute", json={}).status_code, 403)
            self.assertEqual(self.client.get("/api/archive").status_code, 200)

    def test_comparison_results_expose_only_sanitized_sample_diagnostics(self):
        private_path = "/" + "Users/private/project/file.py"
        unix_paths = [
            "/" + "home/alice/private/project",
            "/" + "root/private/project",
            "/" + "workspace/private/project",
        ]
        standalone_secret = "sk-" + "live-secret-value"
        arbitrary_url = "https://" + "example.test/private"
        forbidden = {
            "question": "private benchmark question",
            "gold_sql": "SELECT private_gold",
            "pred_sql": "SELECT private_prediction",
        }
        scores = {
            "run_id": f"safe-run {standalone_secret}",
            "method": f"c3sql {unix_paths[1]}",
            "dataset": f"spider {arbitrary_url}",
            "split": "dev",
            "timestamp": f"2026-01-01 {unix_paths[2]}",
            "scope": "sample",
            "sample_count": 1,
            "aggregate": {
                "ex": {"avg": 0.0},
                "token": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cost_usd": 0.004,
                    "provider": "must-not-pass",
                },
                "error_root_distribution": {
                    "execution_error": {
                        "count": 2,
                        "pct": 0.5,
                        "sample_ids": ["dev_1", "dev_2"],
                        "details": ["private prose"],
                    },
                },
                "nested": forbidden,
                "patch_summary": "private patch summary",
                "source_excerpt": "private source excerpt",
                "code_snippet": "private code snippet",
            },
            "stage_metrics": {
                "generate": {
                    "iteration": 2,
                    "task_type": "GenerateTask",
                    "valid_num": 9,
                    "total_items": 10,
                    "metrics": {"execute_accuracy": 0.9},
                    "timing": {"elapsed_s": 1.2},
                    "dataset_save_path": unix_paths[0],
                    "per_sample": [{"notes": "private prose"}],
                    "notes": ["internal roadmap text"],
                    "details": ["private candidate instruction"],
                },
            },
            "workflow_trace": {
                "workflows": [{
                    "task_id": "c3sql_full",
                    "stages": ["reduce", "generate"],
                    "eval_type": ["execute_accuracy"],
                    "notes": ["private prose"],
                }],
                "aggregate": {
                    "bottleneck_distribution": {"generate": 2, "success": 8},
                    "stage_summary": {
                        "generate": {
                            "task_type": "GenerateTask",
                            "actor_class": "C3SQLGenerator",
                            "status_counts": {"pass": 8, "fail": 2},
                            "metrics": {"execute_accuracy": 0.8},
                            "signals": {"oracle_ex": 0.9},
                            "notes": ["private prose"],
                            "dataset_save_path": unix_paths[0],
                            "source_excerpt": "private source",
                        },
                    },
                    "details": ["private prose"],
                },
            },
            "by_hardness": {
                "hard": {
                    "ex": 0.0,
                    "cf1_join": 0.4,
                    "cf1_where": 0.7,
                    "error_dist": {
                        "missing_join": {"count": 1, "pct": 0.25, "sample_ids": ["dev_1"]},
                    },
                    "notes": ["private prose"],
                },
            },
            "by_sql_feature": {
                "group_by": {
                    "ex": 0.0,
                    "bottlenecks": {"generate": 3, "reduce": 1},
                    "details": ["private prose"],
                },
            },
            "by_scenario": {"join": {"ex": 0.0, "nested": forbidden}},
            "qvt": {
                "eligible_groups": 4,
                "avg_group_exec_acc": 0.75,
                "stable_group_rate": 0.5,
                "flip_rate": 0.25,
                "groups": {
                    "g1": {"exec_acc": 1.0, "stable": True, "flip": False, "notes": "private prose"},
                },
            },
            "weakness_profile": {
                "summary": "schema linking",
                "status": "recorded",
                "nested": forbidden,
            },
            "evolution_record": {
                "baseline": {"artifact": "scores.json", "nested": forbidden},
                "candidate_change": {
                    "status": "recorded",
                    "source": "private candidate source",
                    "diff": "private patch",
                    "review_notes": "do not publish",
                    "credential": "Bearer abc.def",
                    "path": private_path,
                    "url": "https://internal.example.test/roadmap",
                },
            },
            "per_sample": [{
                "instance_id": "dev_1",
                "db_id": f"concert_singer {unix_paths[1]}",
                "hardness": f"hard {unix_paths[2]}",
                "ex": 0,
                "error_root": "execution_error",
                "error_sub": "missing_column",
                "sl_recall": 0.5,
                "act_elapsed_s": 0.9,
                "error_sub": f"failure at {unix_paths[0]} {arbitrary_url}",
                **forbidden,
            }],
        }

        def comparison(*_args, **_kwargs):
            run = self.api_server._serialize_comparison_run(scores)
            return self.api_server._comparison_payload([run], ["c3sql"])

        with patch.object(self.api_server, "_artifact_comparison", side_effect=comparison):
            response = self.client.get("/api/comparisons/latest/results")

        self.assertEqual(response.status_code, 200)
        run = response.json["runs"][0]
        self.assertEqual(run["errors"]["execution_error"], {
            "count": 2,
            "pct": 0.5,
        })
        self.assertEqual(run["by_hardness"]["hard"]["cf1_join"], 0.4)
        self.assertEqual(run["by_hardness"]["hard"]["cf1_where"], 0.7)
        self.assertEqual(run["by_hardness"]["hard"]["error_dist"]["missing_join"]["pct"], 0.25)
        self.assertEqual(run["by_sql_feature"]["group_by"]["bottlenecks"], {"generate": 3, "reduce": 1})
        self.assertEqual(run["qvt"]["eligible_groups"], 4)
        self.assertEqual(run["qvt"]["groups"]["g1"], {"exec_acc": 1.0, "stable": True, "flip": False})
        self.assertEqual(run["stage_metrics"]["generate"], {
            "iteration": 2,
            "valid_num": 9,
            "total_items": 10,
            "task_type": "GenerateTask",
            "metrics": {"execute_accuracy": 0.9},
            "timing": {"elapsed_s": 1.2},
        })
        self.assertEqual(run["workflow"]["workflows"], [{
            "task_id": "c3sql_full",
            "stages": ["reduce", "generate"],
            "eval_type": ["execute_accuracy"],
        }])
        self.assertEqual(run["workflow"]["aggregate"], {
            "bottleneck_distribution": {"generate": 2, "success": 8},
            "stage_summary": {
                "generate": {
                    "task_type": "GenerateTask",
                    "actor_class": "C3SQLGenerator",
                    "status_counts": {"pass": 8, "fail": 2},
                    "metrics": {"execute_accuracy": 0.8},
                    "signals": {"oracle_ex": 0.9},
                },
            },
        })
        self.assertEqual(run["token"], {
            "input_tokens": 120,
            "output_tokens": 30,
            "cost_usd": 0.004,
        })
        self.assertNotIn("summary", run["weakness_profile"])
        self.assertEqual(run["weakness_profile"]["status"], "recorded")
        self.assertEqual(
            run["evolution_record"]["candidate_change"]["status"],
            "recorded",
        )
        self.assertNotIn("samples", run)
        serialized = response.get_data(as_text=True)
        for forbidden in (
            "question", "gold_sql", "pred_sql", "private benchmark question",
            "private candidate source", "private patch", "do not publish",
            "abc.def", private_path, "internal.example.test",
            standalone_secret, arbitrary_url, *unix_paths,
            "private patch summary", "private source excerpt", "private code snippet",
            "dev_1", "dev_2",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_localhost_7860_provider_request_reaches_business_validation(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "local"}, clear=False):
            response = self.client.post(
                "/api/provider",
                json={},
                headers={"Origin": "http://localhost:7860"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["message"], "provider is required")

    def test_hosted_policy_still_rejects_local_routes_from_port_7860(self):
        headers = {"Origin": "http://localhost:7860"}
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            provider = self.client.post("/api/provider", json={}, headers=headers)
        self.assertEqual(provider.status_code, 403)
        self.assertEqual(provider.json["reason"], "local_only")

    def test_removed_coding_agent_terminal_api_is_not_exposed(self):
        for target in ("local", "hf-space"):
            with self.subTest(target=target):
                with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": target}, clear=False):
                    response = self.client.get("/api/terminals")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json, {"message": "API route not found."})


if __name__ == "__main__":
    unittest.main()
