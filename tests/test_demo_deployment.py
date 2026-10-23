import unittest

from demo.deployment import (
    deployment_features,
    deployment_target,
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


if __name__ == "__main__":
    unittest.main()
