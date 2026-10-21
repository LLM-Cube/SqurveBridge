import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask


class FakeSock:
    def __init__(self):
        self.handlers = {}

    def route(self, path):
        def decorator(function):
            self.handlers[path] = function
            return function

        return decorator


class FakeSession:
    next_id = 0

    def __init__(self, settings):
        self.settings = settings
        type(self).next_id += 1
        self.session_id = f"session{type(self).next_id}"
        self.running = True
        self.prompts = []
        self.commands = []

    def public_state(self):
        return {
            "session_id": self.session_id,
            "backend": "pi",
            "profile": self.settings.profile,
            "running": self.running,
        }

    def send_prompt(self, prompt):
        self.prompts.append(prompt)

    def send_command(self, command):
        allowed = {
            "auth_start", "auth_prompt_response", "auth_cancel",
            "model_select", "logout", "prompt", "abort",
        }
        if command.get("type") not in allowed:
            raise ValueError("Unsupported Pi client command")
        self.commands.append(command)

    def read(self, cursor):
        return ([{"type": "ready", "backend": "pi"}], 1)

    def wait_read(self, cursor, timeout=.25):
        return self.read(cursor)

    def stop(self):
        self.running = False


class DisconnectingWebSocket:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)

    def receive(self, timeout=None):
        raise ConnectionError("browser disconnected")

    def close(self):
        pass


class ScriptedWebSocket(DisconnectingWebSocket):
    def __init__(self, commands):
        super().__init__()
        self.commands = list(commands)

    def receive(self, timeout=None):
        if self.commands:
            return self.commands.pop(0)
        raise ConnectionError("browser disconnected")


class PiApiTests(unittest.TestCase):
    def api_module(self):
        try:
            return importlib.import_module("demo.pi_api")
        except ModuleNotFoundError:
            self.fail("demo.pi_api must expose the Pi chat API")

    def make_client(self, environment=None):
        module = self.api_module()
        app = Flask(__name__)
        sock = FakeSock()
        registry = module.register_pi_routes(
            app,
            sock,
            Path("/workspace"),
            environment=environment or {},
            session_factory=FakeSession,
        )
        return app.test_client(), registry, sock

    def catalog_for(self, root, environment=None):
        app = Flask(__name__)
        self.api_module().register_pi_routes(
            app, FakeSock(), Path(root), environment=environment or {}, session_factory=FakeSession,
        )
        return app.test_client().get("/api/agent").json

    def install_fake_sdk(self, root, *, sdk=True, bridge=True):
        if sdk:
            entry = Path(root) / "demo/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
        if bridge:
            (Path(root) / "demo").mkdir(exist_ok=True)
            (Path(root) / "demo/pi_agent_bridge.mjs").write_text("")

    def test_catalog_is_available_when_the_npm_sdk_is_installed(self):
        with tempfile.TemporaryDirectory() as root:
            self.install_fake_sdk(root)
            catalog = self.catalog_for(root, {"PI_NODE_BINARY": sys.executable})
        self.assertTrue(catalog["available"])
        self.assertIsNone(catalog["unavailable_reason"])

    def test_catalog_names_why_the_agent_is_unavailable(self):
        cases = {
            "node_missing": ({"sdk": True, "bridge": True}, {"PI_NODE_BINARY": "definitely-not-a-node-binary"}),
            "sdk_missing": ({"sdk": False, "bridge": True}, {"PI_NODE_BINARY": sys.executable}),
            "bridge_missing": ({"sdk": True, "bridge": False}, {"PI_NODE_BINARY": sys.executable}),
        }
        for reason, (files, environment) in cases.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                self.install_fake_sdk(root, **files)
                catalog = self.catalog_for(root, environment)
                self.assertFalse(catalog["available"])
                self.assertEqual(catalog["unavailable_reason"], reason)

    def test_catalog_exposes_pi_and_project_skills(self):
        client, _, _ = self.make_client({"SQURVE_DEPLOYMENT_TARGET": "hf-space"})
        response = client.get("/api/agent")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["backend"], "pi")
        self.assertEqual(response.json["profile"], "hosted-readonly")
        self.assertEqual(response.json["skills"], sorted(self.api_module().PI_SKILLS))

    def test_catalog_discovers_all_skills_from_the_project(self):
        module = self.api_module()
        with self.subTest("real project root"):
            app = Flask(__name__)
            sock = FakeSock()
            root = Path(__file__).resolve().parents[1]
            module.register_pi_routes(
                app,
                sock,
                root,
                environment={},
                session_factory=FakeSession,
            )
            response = app.test_client().get("/api/agent")
        self.assertEqual(
            set(response.json["skills"]),
            module.discover_pi_skills(root),
        )

    def test_session_accepts_chat_messages_and_returns_events(self):
        client, registry, sock = self.make_client()
        started = client.post("/api/agent/sessions", json={})
        self.assertEqual(started.status_code, 201)
        session_id = started.json["session_id"]
        sent = client.post(f"/api/agent/sessions/{session_id}/messages", json={"message": "/run smoke"})
        self.assertEqual(sent.status_code, 202)
        self.assertEqual(registry.get(session_id).prompts, ["/run smoke"])
        events = client.get(f"/api/agent/sessions/{session_id}/events?cursor=0")
        self.assertEqual(events.json["events"][0]["type"], "ready")
        self.assertIn("/api/agent/sessions/<session_id>/ws", sock.handlers)

    def test_stop_closes_the_pi_session(self):
        client, registry, _ = self.make_client()
        session_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        response = client.post(f"/api/agent/sessions/{session_id}/stop")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(registry.get(session_id))

    def test_websocket_disconnect_stops_and_removes_session(self):
        client, registry, sock = self.make_client()
        session_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        session = registry.get(session_id)

        sock.handlers["/api/agent/sessions/<session_id>/ws"](
            DisconnectingWebSocket(), session_id
        )

        self.assertFalse(session.running)
        self.assertIsNone(registry.get(session_id))

    def test_websocket_relays_typed_pi_auth_commands_without_echoing_secrets(self):
        client, registry, sock = self.make_client({"SQURVE_DEPLOYMENT_TARGET": "hf-space"})
        session_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        session = registry.get(session_id)
        socket = ScriptedWebSocket([
            '{"type":"auth_start","provider":"anthropic","method":"api_key"}',
            '{"type":"auth_prompt_response","request_id":"auth-1","value":"ws-pi-secret"}',
        ])

        sock.handlers["/api/agent/sessions/<session_id>/ws"](socket, session_id)

        self.assertEqual(session.commands[0]["type"], "auth_start")
        self.assertEqual(session.commands[1]["value"], "ws-pi-secret")
        self.assertNotIn("ws-pi-secret", "".join(socket.messages))

    def test_websocket_rejects_unknown_commands_without_echoing_payload(self):
        client, _, sock = self.make_client({"SQURVE_DEPLOYMENT_TARGET": "hf-space"})
        session_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        socket = ScriptedWebSocket(['{"type":"unknown","value":"unknown-secret"}'])

        sock.handlers["/api/agent/sessions/<session_id>/ws"](socket, session_id)

        payload = "".join(socket.messages)
        self.assertIn("Unsupported Pi client command", payload)
        self.assertNotIn("unknown-secret", payload)

    def test_hosted_pi_sessions_keep_credentials_process_local_and_out_of_child_env(self):
        environment = {
            "SQURVE_DEPLOYMENT_TARGET": "hf-space",
            "QWEN_API_KEY": "sql-boundary-secret",
            "ANTHROPIC_API_KEY": "server-pi-secret",
            "SQURVE_LLM_PROVIDER": "qwen",
            "SQURVE_LLM_MODEL": "qwen-plus",
        }
        client, registry, sock = self.make_client(environment)
        first_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        second_id = client.post("/api/agent/sessions", json={}).json["session_id"]
        first = registry.get(first_id)
        second = registry.get(second_id)

        self.assertIsNone(first.settings.provider)
        self.assertIsNone(first.settings.model)
        self.assertNotIn("QWEN_API_KEY", first.settings.child_environment())
        self.assertNotIn("ANTHROPIC_API_KEY", first.settings.child_environment())

        first_socket = ScriptedWebSocket(['{"type":"auth_prompt_response","request_id":"a","value":"first-pi-secret"}'])
        second_socket = ScriptedWebSocket(['{"type":"auth_prompt_response","request_id":"b","value":"second-pi-secret"}'])
        sock.handlers["/api/agent/sessions/<session_id>/ws"](first_socket, first_id)
        sock.handlers["/api/agent/sessions/<session_id>/ws"](second_socket, second_id)

        self.assertEqual(first.commands[0]["value"], "first-pi-secret")
        self.assertEqual(second.commands[0]["value"], "second-pi-secret")
        self.assertNotIn("second-pi-secret", str(first.commands))
        self.assertNotIn("first-pi-secret", str(second.commands))
        self.assertNotIn("first-pi-secret", "".join(first_socket.messages))
        self.assertNotIn("second-pi-secret", "".join(second_socket.messages))

    def test_hosted_space_limits_agent_sessions_below_http_thread_capacity(self):
        client, _, _ = self.make_client({"SQURVE_DEPLOYMENT_TARGET": "hf-space"})
        self.assertEqual(client.post("/api/agent/sessions", json={}).status_code, 201)
        self.assertEqual(client.post("/api/agent/sessions", json={}).status_code, 201)
        blocked = client.post("/api/agent/sessions", json={})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("limit", blocked.json["message"])


if __name__ == "__main__":
    unittest.main()
