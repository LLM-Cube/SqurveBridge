"""A showcase Demo turns the embedded Agent off on the server, not just in the page."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from demo import api_server
from demo.deployment import deployment_features, route_denial

AGENT_OFF = {"SQURVE_DEMO_AGENT": "off"}


class AgentSwitchPolicyTests(unittest.TestCase):
    def test_the_agent_is_on_unless_switched_off(self):
        self.assertTrue(deployment_features({})["agent_chat"])
        self.assertTrue(deployment_features({"SQURVE_DEMO_AGENT": "on"})["agent_chat"])
        for value in ("off", "OFF", "0", "false", "no"):
            self.assertFalse(deployment_features({"SQURVE_DEMO_AGENT": value})["agent_chat"], value)

    def test_switching_the_agent_off_changes_nothing_else(self):
        on, off = deployment_features({}), deployment_features(AGENT_OFF)
        self.assertEqual({**on, "agent_chat": False}, off)

    def test_only_agent_routes_are_refused(self):
        self.assertEqual(route_denial("GET", "/api/agent", AGENT_OFF), "agent_disabled")
        self.assertEqual(route_denial("POST", "/api/agent/sessions", AGENT_OFF), "agent_disabled")
        self.assertEqual(route_denial("GET", "/api/agent/sessions/abc/ws", AGENT_OFF), "agent_disabled")
        self.assertIsNone(route_denial("GET", "/api/agents-catalog", AGENT_OFF))
        self.assertIsNone(route_denial("POST", "/api/query", AGENT_OFF))
        self.assertIsNone(route_denial("POST", "/api/agent/sessions", {}))

    def test_it_combines_with_the_hosted_space(self):
        env = {**AGENT_OFF, "SQURVE_DEPLOYMENT_TARGET": "hf-space"}
        self.assertFalse(deployment_features(env)["agent_chat"])
        self.assertFalse(deployment_features(env)["database_upload"])
        self.assertEqual(route_denial("POST", "/api/agent/sessions", env), "agent_disabled")
        self.assertEqual(route_denial("POST", "/api/provider", env), "local_only")


class AgentSwitchApiTests(unittest.TestCase):
    def setUp(self):
        api_server.app.config.update(TESTING=True)
        self.client = api_server.app.test_client()

    def test_capabilities_report_the_agent_off(self):
        with mock.patch.dict(os.environ, AGENT_OFF, clear=False):
            response = self.client.get("/api/capabilities")
        self.assertFalse(response.json["deployment"]["features"]["agent_chat"])

    def test_agent_routes_are_refused_before_the_pi_backend(self):
        with mock.patch.dict(os.environ, AGENT_OFF, clear=False), \
                mock.patch.object(api_server._pi_sessions, "create", side_effect=AssertionError("Pi started")):
            for method, path in (
                ("get", "/api/agent"),
                ("post", "/api/agent/sessions"),
                ("post", "/api/agent/sessions/abc/messages"),
                ("get", "/api/agent/sessions/abc/events"),
            ):
                response = getattr(self.client, method)(path, json={})
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.json["reason"], "agent_disabled", path)

    def test_without_the_switch_the_agent_catalog_still_answers(self):
        with mock.patch.dict(os.environ, {"SQURVE_DEMO_AGENT": "on"}, clear=False):
            response = self.client.get("/api/agent")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["enabled"])


if __name__ == "__main__":
    unittest.main()
