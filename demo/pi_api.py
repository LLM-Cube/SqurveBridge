"""Flask routes for the embedded Pi chat backend."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from collections.abc import Callable, Mapping

from flask import jsonify, request

from demo.deployment import is_hf_space
from demo.pi_backend import PI_SKILLS, PiAgentSession, PiBackendSettings, discover_pi_skills


class PiSessionRegistry:
    def __init__(self, max_sessions: int = 8) -> None:
        self.max_sessions = max_sessions
        self._sessions: dict[str, PiAgentSession] = {}
        self._lock = threading.RLock()

    def create(self, settings: PiBackendSettings, session_factory: Callable) -> PiAgentSession:
        with self._lock:
            active = [session for session in self._sessions.values() if session.running]
            if len(active) >= self.max_sessions:
                raise RuntimeError("Pi agent session limit reached")
            session = session_factory(settings)
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> PiAgentSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str, *, stop: bool = True) -> PiAgentSession | None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None and stop:
            session.stop()
        return session


_SDK_ENTRY = Path("demo/node_modules/@earendil-works/pi-coding-agent/dist/index.js")
_BRIDGE_SCRIPT = Path("demo/pi_agent_bridge.mjs")
_UNAVAILABLE_MESSAGES = {
    "node_missing": "Node.js is required for the Pi backend",
    "sdk_missing": "The embedded Pi SDK is not installed",
    "bridge_missing": "The Pi bridge script is missing",
}


def register_pi_routes(
    app,
    sock,
    project_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    session_factory: Callable = PiAgentSession,
) -> PiSessionRegistry:
    root = project_root.resolve()
    registry = PiSessionRegistry(max_sessions=2 if is_hf_space(environment) else 8)

    def values() -> Mapping[str, str]:
        return os.environ if environment is None else environment

    def settings() -> PiBackendSettings:
        return PiBackendSettings.from_environment(values(), root)

    def unavailable_reason(current: PiBackendSettings) -> str | None:
        """Why the embedded Pi agent cannot start here, or None when it can."""
        if not shutil.which(current.node_binary):
            return "node_missing"
        if not (root / _SDK_ENTRY).is_file():
            return "sdk_missing"
        if not (root / _BRIDGE_SCRIPT).is_file():
            return "bridge_missing"
        return None

    def find_session(session_id: str):
        session = registry.get(session_id)
        if session is None:
            return None, (jsonify({"status": "error", "message": "Pi agent session not found"}), 404)
        return session, None

    @app.get("/api/agent")
    def pi_agent_catalog():
        current = settings()
        reason = unavailable_reason(current)
        return jsonify({
            "enabled": True,
            "available": reason is None,
            "unavailable_reason": reason,
            "backend": "pi",
            "profile": current.profile,
            "provider": current.provider,
            "model": current.model,
            "skills": sorted(discover_pi_skills(root) or PI_SKILLS),
            "tools": list(current.tools),
        })

    @app.post("/api/agent/sessions")
    def start_pi_agent_session():
        current = settings()
        if session_factory is PiAgentSession:
            reason = unavailable_reason(current)
            if reason is not None:
                return jsonify({"status": "error", "code": reason, "message": _UNAVAILABLE_MESSAGES[reason]}), 503
        try:
            session = registry.create(current, session_factory)
        except RuntimeError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        return jsonify(session.public_state()), 201

    @app.post("/api/agent/sessions/<session_id>/messages")
    def send_pi_agent_message(session_id: str):
        session, error = find_session(session_id)
        if error:
            return error
        message = str((request.get_json(silent=True) or {}).get("message", ""))
        try:
            session.send_prompt(message)
        except (RuntimeError, ValueError) as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        return jsonify({"status": "accepted"}), 202

    @app.get("/api/agent/sessions/<session_id>/events")
    def read_pi_agent_events(session_id: str):
        session, error = find_session(session_id)
        if error:
            return error
        try:
            cursor = max(0, int(request.args.get("cursor", 0)))
        except ValueError:
            return jsonify({"status": "error", "message": "cursor must be an integer"}), 400
        events, next_cursor = session.read(cursor)
        return jsonify({**session.public_state(), "events": events, "cursor": next_cursor})

    @app.post("/api/agent/sessions/<session_id>/stop")
    def stop_pi_agent_session(session_id: str):
        session, error = find_session(session_id)
        if error:
            return error
        session.stop()
        state = session.public_state()
        registry.remove(session_id, stop=False)
        return jsonify(state)

    @sock.route("/api/agent/sessions/<session_id>/ws")
    def pi_agent_websocket(ws, session_id: str):
        session = registry.get(session_id)
        if session is None:
            ws.close()
            return
        cursor = 0
        try:
            ws.send(json.dumps({"type": "session", **session.public_state()}))
            while session.running:
                try:
                    raw = ws.receive(timeout=.05)
                except TimeoutError:
                    raw = None
                except Exception:
                    break
                if raw:
                    try:
                        command = json.loads(raw)
                        if not isinstance(command, dict):
                            raise ValueError("Pi client commands must be JSON objects")
                        session.send_command(command)
                    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                        ws.send(json.dumps({"type": "command_error", "message": str(exc)}))
                events, cursor = session.wait_read(cursor, timeout=.05)
                for event in events:
                    ws.send(json.dumps(event, ensure_ascii=False))
            events, _ = session.read(cursor)
            for event in events:
                ws.send(json.dumps(event, ensure_ascii=False))
        finally:
            registry.remove(session_id)

    return registry
