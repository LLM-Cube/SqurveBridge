"""Process and protocol helpers for the embedded Pi agent backend."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from collections.abc import Callable

from demo.deployment import deployment_target
PI_SKILLS = {
    "candidate-reader",
    "integration-pipeline",
    "config-adapter",
    "run",
    "meta-evo",
}
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
FULL_TOOLS = ("read", "bash", "edit", "write", "grep", "find", "ls")
PI_CLIENT_COMMANDS = {
    "context",
    "auth_start",
    "auth_prompt_response",
    "auth_cancel",
    "model_select",
    "logout",
    "prompt",
    "abort",
}
SAFE_CHILD_ENV_VARS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "NODE_EXTRA_CA_CERTS",
    "PATH",
    "SSL_CERT_FILE",
    "TMPDIR",
)


def discover_pi_skills(project_root: Path) -> set[str]:
    """Return Pi skill names declared by SqurveBridge's Skills SSOT."""
    names: set[str] = set()
    for skill_file in (project_root / "skills").glob("*/SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        match = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", text)
        names.add(match.group(1).strip() if match else skill_file.parent.name)
    return names


@dataclass(frozen=True)
class PiBackendSettings:
    project_root: Path
    profile: str
    tools: tuple[str, ...]
    provider: str | None
    model: str | None
    node_binary: str = "node"
    api_base: str | None = None
    source_environment: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None,
        project_root: Path,
    ) -> "PiBackendSettings":
        values = os.environ if environment is None else environment
        hosted = deployment_target(values) == "hf-space"
        provider = None if hosted else (values.get("PI_AGENT_PROVIDER") or values.get("SQURVE_LLM_PROVIDER") or None)
        model = None if hosted else (values.get("PI_AGENT_MODEL") or values.get("SQURVE_LLM_MODEL") or None)
        return cls(
            project_root=project_root.resolve(),
            profile="hosted-readonly" if hosted else "local-full",
            tools=READ_ONLY_TOOLS if hosted else FULL_TOOLS,
            provider=provider,
            model=model,
            node_binary=values.get("PI_NODE_BINARY", "node"),
            source_environment=dict(values),
        )

    def child_environment(self) -> dict[str, str]:
        if self.profile != "hosted-readonly":
            return dict(self.source_environment)
        child = {
            name: self.source_environment[name]
            for name in SAFE_CHILD_ENV_VARS
            if self.source_environment.get(name)
        }
        return child

    def command(self) -> list[str]:
        command = [
            self.node_binary,
            str(self.project_root / "demo" / "pi_agent_bridge.mjs"),
            "--cwd",
            str(self.project_root),
            "--profile",
            self.profile,
            "--tools",
            json.dumps(self.tools),
        ]
        if self.provider:
            command.extend(("--provider", self.provider))
        if self.model:
            command.extend(("--model", self.model))
        if self.api_base:
            command.extend(("--api-base", self.api_base))
        return command


def normalize_pi_prompt(prompt: str, skills: set[str] | None = None) -> str:
    """Translate legacy Squrve skill shortcuts to Pi's Agent Skills syntax."""
    stripped = prompt.strip()
    if not stripped.startswith("/") or stripped.startswith("/skill:"):
        return stripped
    command, separator, arguments = stripped.partition(" ")
    name = command[1:]
    if name not in (PI_SKILLS if skills is None else skills):
        return stripped
    suffix = f" {arguments}" if separator else ""
    return f"/skill:{name}{suffix}"


class PiEventDecoder:
    """Decode strict newline-delimited JSON emitted by the Pi bridge."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[dict]:
        self._buffer += chunk
        records: list[dict] = []
        while "\n" in self._buffer:
            raw, self._buffer = self._buffer.split("\n", 1)
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Pi bridge events must be JSON objects")
            records.append(value)
        return records


class PiAgentSession:
    """One Pi SDK bridge process with a structured, cursor-based event stream."""

    def __init__(
        self,
        settings: PiBackendSettings,
        process_factory: Callable = subprocess.Popen,
    ) -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.settings = settings
        self.skill_names = discover_pi_skills(settings.project_root) or set(PI_SKILLS)
        self._condition = threading.Condition(threading.RLock())
        self._events: list[dict] = []
        self._event_base = 0
        self._closed = False
        self.process = process_factory(
            settings.command(),
            cwd=settings.project_root,
            env=settings.child_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    @property
    def running(self) -> bool:
        return not self._closed and self.process.poll() is None

    def _append(self, event: dict) -> None:
        with self._condition:
            self._events.append(event)
            if len(self._events) > 5000:
                trim = len(self._events) - 4000
                self._events = self._events[trim:]
                self._event_base += trim
            self._condition.notify_all()

    def _read_stdout(self) -> None:
        decoder = PiEventDecoder()
        try:
            for chunk in self.process.stdout:
                for event in decoder.feed(chunk):
                    self._append(event)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._append({"type": "bridge_error", "message": str(exc)})
        finally:
            code = self.process.wait()
            self._closed = True
            self._append({"type": "exit", "exit_code": code})

    def _read_stderr(self) -> None:
        try:
            for line in self.process.stderr:
                message = line.strip()
                if message and self.settings.profile != "hosted-readonly":
                    self._append({"type": "bridge_log", "message": message[:4000]})
        except OSError:
            return

    def send(self, payload: dict) -> None:
        if self.process.stdin is None:
            raise RuntimeError("Pi agent input is unavailable")
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError("Pi agent session is not running") from exc

    def send_prompt(self, prompt: str) -> None:
        self.send_command({"type": "prompt", "message": prompt})

    def send_command(self, command: Mapping[str, object]) -> None:
        command_type = str(command.get("type", ""))
        if command_type not in PI_CLIENT_COMMANDS:
            raise ValueError("Unsupported Pi client command")
        payload = dict(command)
        if command_type == "prompt":
            message = normalize_pi_prompt(
                str(payload.get("message", "")),
                getattr(self, "skill_names", None),
            )
            if not message or len(message) > 65536:
                raise ValueError("Pi prompt must contain between 1 and 65536 characters")
            payload = {"type": "prompt", "message": message}
        elif command_type == "auth_prompt_response":
            value = str(payload.get("value", ""))
            if len(value) > 65536:
                raise ValueError("Pi authentication response is too long")
            payload["value"] = value
        self.send(payload)

    def read(self, cursor: int) -> tuple[list[dict], int]:
        with self._condition:
            end = self._event_base + len(self._events)
            normalized = max(self._event_base, min(cursor, end))
            return self._events[normalized - self._event_base:].copy(), end

    def wait_read(self, cursor: int, timeout: float = .25) -> tuple[list[dict], int]:
        with self._condition:
            if cursor >= self._event_base + len(self._events) and self.running:
                self._condition.wait(timeout)
            return self.read(cursor)

    def stop(self) -> None:
        if self._closed:
            return
        try:
            self.send({"type": "abort"})
        except RuntimeError:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        finally:
            self._closed = True

    def public_state(self) -> dict:
        return {
            "session_id": self.session_id,
            "backend": "pi",
            "profile": self.settings.profile,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "running": self.running,
        }
