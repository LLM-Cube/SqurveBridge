from __future__ import annotations

import os
from collections.abc import Mapping


DEPLOYMENT_TARGET_ENV = "SQURVE_DEPLOYMENT_TARGET"
# Independent of the target: a showcase Demo runs without the embedded Agent.
AGENT_ENV = "SQURVE_DEMO_AGENT"
_AGENT_OFF_VALUES = frozenset({"off", "0", "false", "no"})
_AGENT_ROUTE_PREFIX = "/api/agent"
_HOSTED_FORBIDDEN = (
    ("POST", "/api/provider"),
    ("POST", "/api/databases/upload"),
    ("POST", "/api/evaluations"),
    ("POST", "/api/comparisons"),
)
_HOSTED_FORBIDDEN_PREFIXES = (
    "/api/evaluations/",
    "/api/experiments",
)


def deployment_target(environment: Mapping[str, str] | None = None) -> str:
    values = os.environ if environment is None else environment
    value = str(values.get(DEPLOYMENT_TARGET_ENV, "local")).strip().lower()
    return value if value in {"local", "hf-space"} else "local"


def is_hf_space(environment: Mapping[str, str] | None = None) -> bool:
    return deployment_target(environment) == "hf-space"


def agent_enabled(environment: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environment is None else environment
    return str(values.get(AGENT_ENV, "on")).strip().lower() not in _AGENT_OFF_VALUES


def deployment_features(environment: Mapping[str, str] | None = None) -> dict[str, bool]:
    hosted = is_hf_space(environment)
    return {
        "live_sql": True,
        "sql_execution": True,
        "recorded_evidence": True,
        "provider_configuration": not hosted,
        "session_sql_auth": hosted,
        "database_upload": not hosted,
        "agent_chat": agent_enabled(environment),
        "live_evaluation": not hosted,
    }


def hosted_route_allowed(
    method: str,
    path: str,
    environment: Mapping[str, str] | None = None,
) -> bool:
    if not is_hf_space(environment):
        return True
    normalized = (method.upper(), path.rstrip("/") or "/")
    if normalized in _HOSTED_FORBIDDEN:
        return False
    if path.startswith("/api/archive/") and "/files/" in path:
        return False
    return not any(path.startswith(prefix) for prefix in _HOSTED_FORBIDDEN_PREFIXES)


def route_denial(
    method: str,
    path: str,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Why this deployment refuses an API route, or None when it is allowed."""
    if not agent_enabled(environment) and (
        path == _AGENT_ROUTE_PREFIX or path.startswith(_AGENT_ROUTE_PREFIX + "/")
    ):
        return "agent_disabled"
    if not hosted_route_allowed(method, path, environment):
        return "local_only"
    return None
