from __future__ import annotations

import os
from collections.abc import Mapping


DEPLOYMENT_TARGET_ENV = "SQURVE_DEPLOYMENT_TARGET"
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


def deployment_features(environment: Mapping[str, str] | None = None) -> dict[str, bool]:
    hosted = is_hf_space(environment)
    return {
        "live_sql": True,
        "sql_execution": True,
        "recorded_evidence": True,
        "provider_configuration": not hosted,
        "session_sql_auth": hosted,
        "database_upload": not hosted,
        "agent_chat": True,
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
