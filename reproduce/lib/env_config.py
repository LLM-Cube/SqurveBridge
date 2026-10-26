"""Load repo-root ``.env`` and resolve reproduce config ``api_key`` values."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from reproduce.lib.paths import PROJECT_ROOT

PLACEHOLDER_KEYS = {"", None, "your_api_key_here", "your_zhipu_key_here"}

PROVIDER_ENV_VARS = {
    "qwen": "QWEN_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

_ENV_REF_PATTERN = re.compile(r"^\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}$")


def load_dotenv(path: Path | None = None) -> bool:
    """Load KEY=VALUE pairs from ``.env`` into ``os.environ`` (no overwrite)."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.is_file():
        return False

    loaded = False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded


def resolve_env_reference(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _ENV_REF_PATTERN.match(value.strip())
    if not match:
        return value
    return os.environ.get(match.group(1), "")


def resolve_api_key(provider: str, configured: Any) -> str | None:
    """Return a usable API key for *provider*, preferring explicit config then ``.env``."""
    value = resolve_env_reference(configured)
    if isinstance(value, str) and value.strip() and value not in PLACEHOLDER_KEYS:
        return value.strip()

    env_name = PROVIDER_ENV_VARS.get(provider)
    if env_name:
        env_value = os.environ.get(env_name, "").strip()
        if env_value and env_value not in PLACEHOLDER_KEYS:
            return env_value
    return None


REDACTED_PLACEHOLDER = "__redacted__"


def redact_config_secrets(config: dict) -> dict:
    """Return a copy of *config* that is safe to persist to disk.

    Resolved ``api_key`` literals are replaced with ``${ENV:<VAR>}`` references
    (or a fixed placeholder for unknown providers) so runtime configs and score
    bundles never contain plaintext credentials. The in-memory config used for
    execution is left untouched.
    """
    redacted = dict(config)
    api_keys = dict(redacted.get("api_key") or {})
    for provider, value in list(api_keys.items()):
        if value in PLACEHOLDER_KEYS:
            continue
        if isinstance(value, str) and _ENV_REF_PATTERN.match(value.strip()):
            continue
        env_name = PROVIDER_ENV_VARS.get(provider)
        api_keys[provider] = f"${{ENV:{env_name}}}" if env_name else REDACTED_PLACEHOLDER
    if api_keys:
        redacted["api_key"] = api_keys
    return redacted


def resolve_config_api_keys(config: dict) -> dict:
    """Return a shallow copy of *config* with ``api_key`` filled from ``.env`` when needed."""
    load_dotenv()
    resolved = dict(config)
    api_keys = dict(resolved.get("api_key") or {})
    for provider, configured in list(api_keys.items()):
        key = resolve_api_key(provider, configured)
        if key:
            api_keys[provider] = key
    resolved["api_key"] = api_keys
    return resolved


def apply_runtime_llm_overrides(config: dict) -> dict:
    """Apply ``SQURVE_LLM_PROVIDER`` / ``SQURVE_LLM_MODEL`` onto a reproduce config.

    Demo Board jobs inherit these from the API process. Without this override,
    reproduce templates keep their baked-in ``llm.use`` (often ``qwen``) even
    when the Demo provider is DeepSeek or another configured backend.
    """
    load_dotenv()
    provider = (os.environ.get("SQURVE_LLM_PROVIDER") or "").strip()
    model = (os.environ.get("SQURVE_LLM_MODEL") or "").strip()
    base_url = (os.environ.get("SQURVE_LLM_BASE_URL") or "").strip()
    if not provider and not model and not base_url:
        return config

    resolved = dict(config)
    llm = dict(resolved.get("llm") or {})
    api_keys = dict(resolved.get("api_key") or {})

    if provider:
        if provider not in PROVIDER_ENV_VARS:
            raise ValueError(f"Unsupported SQURVE_LLM_PROVIDER: {provider}")
        llm["use"] = provider
        env_name = PROVIDER_ENV_VARS[provider]
        if provider not in api_keys or not resolve_api_key(provider, api_keys.get(provider)):
            api_keys[provider] = f"${{ENV:{env_name}}}"
        resolved["api_key"] = api_keys

    if model:
        llm["model_name"] = model

    if base_url:
        llm["base_url"] = base_url

    resolved["llm"] = llm
    return resolved


def prepare_runtime_llm_config(config: dict) -> dict:
    """Apply Demo LLM overrides, then resolve ``api_key`` values from the environment."""
    return resolve_config_api_keys(apply_runtime_llm_overrides(config))


def api_key_ready(config: dict) -> tuple[bool, str | None]:
    """Check whether the active ``llm.use`` provider has a non-placeholder key."""
    load_dotenv()
    provider = (config.get("llm") or {}).get("use")
    if not provider:
        return False, "llm.use is missing"
    key = resolve_api_key(provider, (config.get("api_key") or {}).get(provider))
    if not key:
        env_hint = PROVIDER_ENV_VARS.get(provider, f"{provider.upper()}_API_KEY")
        return False, (
            f"{provider} api_key is placeholder; set reproduce config api_key.{provider} "
            f"or {env_hint} in repo-root .env"
        )
    return True, None
