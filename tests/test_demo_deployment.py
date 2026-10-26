import unittest

from demo.deployment import (
    deployment_features,
    deployment_target,
    hosted_experiments_enabled,
    hosted_route_allowed,
    is_hf_space,
)


class DemoDeploymentTests(unittest.TestCase):
    def test_local_is_the_default(self):
        self.assertEqual(deployment_target({}), "local")
        self.assertFalse(is_hf_space({}))
        self.assertTrue(deployment_features({})["database_upload"])

    def test_hf_space_keeps_the_llm_demo_and_disables_local_mutations(self):
        env = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}
        self.assertTrue(is_hf_space(env))
        self.assertEqual(
            deployment_features(env),
            {
                "live_sql": True,
                "sql_execution": True,
                "recorded_evidence": True,
                "provider_configuration": False,
                "session_sql_auth": True,
                "database_upload": False,
                "agent_chat": False,
                "live_evaluation": False,
            },
        )

    def test_hf_space_route_policy(self):
        env = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}
        self.assertTrue(hosted_route_allowed("POST", "/api/query", env))
        self.assertTrue(hosted_route_allowed("POST", "/api/execute", env))
        self.assertTrue(hosted_route_allowed("POST", "/api/agent/sessions", env))
        self.assertTrue(hosted_route_allowed("GET", "/api/sql-auth", env))
        self.assertTrue(hosted_route_allowed("POST", "/api/sql-auth/test", env))
        self.assertTrue(hosted_route_allowed("PUT", "/api/sql-auth", env))
        self.assertTrue(hosted_route_allowed("DELETE", "/api/sql-auth", env))
        self.assertTrue(hosted_route_allowed("GET", "/api/archive", env))
        self.assertFalse(hosted_route_allowed(
            "GET", "/api/archive/public-run/files/scores.json", env
        ))
        self.assertFalse(hosted_route_allowed("POST", "/api/provider", env))
        self.assertFalse(hosted_route_allowed("POST", "/api/databases/upload", env))
        self.assertFalse(hosted_route_allowed("POST", "/api/evaluations", env))
        self.assertFalse(hosted_route_allowed("POST", "/api/comparisons", env))
        self.assertTrue(hosted_route_allowed("GET", "/api/terminals", env))

    def test_hosted_experiments_disabled_by_default(self):
        env = {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}
        self.assertFalse(hosted_experiments_enabled(env))
        self.assertFalse(deployment_features(env)["live_evaluation"])
        self.assertFalse(hosted_route_allowed("POST", "/api/experiments", env))
        self.assertFalse(hosted_route_allowed("GET", "/api/experiments/abc123", env))

    def test_hosted_experiments_opt_in(self):
        env = {"SQURVE_DEPLOYMENT_TARGET": "hf-space", "SQURVE_HOSTED_EXPERIMENTS": "on"}
        self.assertTrue(hosted_experiments_enabled(env))
        self.assertTrue(deployment_features(env)["live_evaluation"])
        self.assertTrue(hosted_route_allowed("POST", "/api/experiments", env))
        self.assertTrue(hosted_route_allowed("GET", "/api/experiments/abc123", env))
        self.assertTrue(hosted_route_allowed("POST", "/api/evaluations", env))
        # Other dangerous routes still blocked
        self.assertFalse(hosted_route_allowed("POST", "/api/provider", env))
        self.assertFalse(hosted_route_allowed("POST", "/api/databases/upload", env))
        self.assertFalse(hosted_route_allowed("POST", "/api/comparisons", env))

    def test_hosted_experiments_various_on_values(self):
        for value in ["on", "1", "true", "yes", "ON", "True", "YES"]:
            with self.subTest(value=value):
                env = {"SQURVE_DEPLOYMENT_TARGET": "hf-space", "SQURVE_HOSTED_EXPERIMENTS": value}
                self.assertTrue(hosted_experiments_enabled(env), f"Failed for value: {value}")

    def test_local_mode_always_allows_experiments(self):
        # Local mode doesn't need the opt-in
        env = {"SQURVE_DEPLOYMENT_TARGET": "local"}
        self.assertTrue(deployment_features(env)["live_evaluation"])
        self.assertTrue(hosted_route_allowed("POST", "/api/experiments", env))


if __name__ == "__main__":
    unittest.main()
