"""Provider → model ID catalog for the Configure LLM pickers.

Model IDs come from the embedded Pi SDK wherever the SDK targets the same API
endpoint the Squrve LLM client calls, so upgrading the pinned SDK also refreshes
the picker instead of leaving a hand-maintained list to rot.

Two providers are curated instead: the SDK reaches Alibaba through the Token
Plan MaaS host and Zhipu through Z.AI's coding-plan host, while this demo calls
DashScope and the Zhipu open platform. Their model IDs are therefore tracked
against those official catalogs rather than borrowed from the SDK.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, Sequence

# Demo provider id → Pi SDK provider id, for providers that share an endpoint.
SDK_PROVIDER_IDS: Mapping[str, str] = {
    "deepseek": "deepseek",
    "openai": "openai",
    "claude": "anthropic",
    "gemini": "google",
}

# Providers whose official endpoint differs from the SDK's; tracked by hand.
CURATED_MODELS: Mapping[str, tuple[str, ...]] = {
    # DashScope (Model Studio) text models.
    "qwen": (
        "qwen3.8-max",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.7-flash",
        "qwen3.6-plus",
        "qwen3.6-flash",
        "qwen-plus",
        "qwen-flash",
        "qwen-turbo",
    ),
    # Zhipu open platform (open.bigmodel.cn) text models.
    "zhipu": (
        "glm-5.2",
        "glm-5.1",
        "glm-5",
        "glm-5-turbo",
        "glm-4.7",
        "glm-4.7-flashx",
        "glm-4.6",
    ),
}

# Used when Node or the SDK is unavailable, so the pickers never come back
# empty. Deliberately short: the SDK is the real source for these providers.
FALLBACK_SDK_MODELS: Mapping[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "openai": ("gpt-5-mini", "gpt-4.1-mini"),
    "claude": ("claude-sonnet-4-5", "claude-haiku-4-5"),
    "gemini": ("gemini-2.5-flash", "gemini-2.0-flash"),
}

PROVIDER_ORDER: tuple[str, ...] = ("qwen", "deepseek", "zhipu", "openai", "claude", "gemini")

# The SDK lists models alphabetically, which would leave the picker defaulting
# to whatever sorts first (gpt-4, a research preview). Name the model each
# provider should open with instead; a demo runs many generations, so these are
# the fast, low-cost tiers. Ignored when the catalog no longer carries the ID.
DEFAULT_MODELS: Mapping[str, str] = {
    "qwen": "qwen3.7-flash",
    "deepseek": "deepseek-v4-flash",
    "zhipu": "glm-4.7-flashx",
    "openai": "gpt-5-mini",
    "claude": "claude-haiku-4-5",
    "gemini": "gemini-2.5-flash",
}

_CATALOG_TIMEOUT_SECONDS = 20.0
_cache: dict[str, list[str]] | None = None
_cache_lock = threading.Lock()


def _catalog_script(project_root: Path) -> Path:
    return project_root / "demo" / "pi_model_catalog.mjs"


def read_sdk_models(
    project_root: Path,
    node_binary: str = "node",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, list[str]]:
    """Ask the embedded Pi SDK for its model IDs, keyed by demo provider id.

    Returns an empty mapping when Node or the SDK is missing; callers fall back
    to `FALLBACK_SDK_MODELS` so a partial install never empties the picker.
    """
    script = _catalog_script(project_root)
    if not script.is_file() or not shutil.which(node_binary):
        return {}
    sdk_ids = list(dict.fromkeys(SDK_PROVIDER_IDS.values()))
    try:
        completed = runner(
            [node_binary, str(script), *sdk_ids],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=_CATALOG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return {}
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    resolved: dict[str, list[str]] = {}
    for provider, sdk_id in SDK_PROVIDER_IDS.items():
        models = payload.get(sdk_id)
        if isinstance(models, list):
            clean = [str(item) for item in models if isinstance(item, str) and item.strip()]
            if clean:
                resolved[provider] = clean
    return resolved


def _default_first(provider: str, models: list[str]) -> list[str]:
    preferred = DEFAULT_MODELS.get(provider)
    if not preferred or preferred not in models:
        return models
    return [preferred, *(item for item in models if item != preferred)]


def build_catalog(sdk_models: Mapping[str, Sequence[str]] | None = None) -> dict[str, list[str]]:
    """Merge SDK-sourced and curated model IDs into the demo provider order."""
    sourced = sdk_models or {}
    catalog: dict[str, list[str]] = {}
    for provider in PROVIDER_ORDER:
        if provider in CURATED_MODELS:
            models = list(CURATED_MODELS[provider])
        else:
            models = list(sourced.get(provider) or ()) or list(FALLBACK_SDK_MODELS.get(provider, ()))
        catalog[provider] = _default_first(provider, models)
    return catalog


def provider_models(project_root: Path, node_binary: str | None = None) -> dict[str, list[str]]:
    """Cached provider → model IDs. The SDK catalog is static per install."""
    global _cache
    with _cache_lock:
        if _cache is None:
            binary = node_binary or os.environ.get("PI_NODE_BINARY", "node")
            _cache = build_catalog(read_sdk_models(project_root, binary))
        return {provider: list(models) for provider, models in _cache.items()}


def reset_cache() -> None:
    """Drop the memoized catalog; used by tests and after an SDK upgrade."""
    global _cache
    with _cache_lock:
        _cache = None


# --- Live listing -----------------------------------------------------------
# Asks the provider which models a key can use. The static catalog above stays
# the fallback, so a failed call never empties the picker.

_LISTING_TIMEOUT_SECONDS = 10.0
_LISTING_CACHE_TTL_SECONDS = 300.0
_LISTING_MAX_BYTES = 2_000_000
_listing_cache: dict[tuple[str, str, str], tuple[float, list[str]]] = {}
_listing_lock = threading.Lock()


def fetch_json(url: str, headers: Mapping[str, str], timeout: float) -> object:
    """GET a provider listing; the seam tests replace with a fake."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https URLs from the table
        return json.loads(response.read(_LISTING_MAX_BYTES))


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


_NON_CHAT_MARKERS = (
    "embed", "whisper", "tts", "dall-e", "moderation", "image", "audio",
    "realtime", "transcribe", "rerank", "speech", "video",
)


def _openai_style_ids(payload: object) -> list[str]:
    items = payload.get("data") if isinstance(payload, dict) else None
    ids = [item["id"] for item in items or [] if isinstance(item, dict) and isinstance(item.get("id"), str)]
    return [model for model in ids if not any(marker in model.lower() for marker in _NON_CHAT_MARKERS)]


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}


def _gemini_headers(api_key: str) -> dict[str, str]:
    return {"x-goog-api-key": api_key}


def _gemini_ids(payload: object) -> list[str]:
    items = payload.get("models") if isinstance(payload, dict) else None
    ids = []
    for item in items or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        if "generateContent" in (item.get("supportedGenerationMethods") or ()):
            ids.append(item["name"].removeprefix("models/"))
    return ids


# provider → (listing URL, auth headers, response parser). Qwen's URL depends on
# the selected region, so callers pass `url=` for it.
LISTING_SOURCES: Mapping[str, tuple[str, Callable[[str], dict[str, str]], Callable[[object], list[str]]]] = {
    "deepseek": ("https://api.deepseek.com/models", _bearer, _openai_style_ids),
    "openai": ("https://api.openai.com/v1/models", _bearer, _openai_style_ids),
    "qwen": ("", _bearer, _openai_style_ids),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4/models", _bearer, _openai_style_ids),
    "claude": ("https://api.anthropic.com/v1/models", _anthropic_headers, _openai_style_ids),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
        _gemini_headers,
        _gemini_ids,
    ),
}


def _key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def reset_listing_cache() -> None:
    with _listing_lock:
        _listing_cache.clear()


def list_provider_models(
    provider: str,
    api_key: str,
    *,
    fallback: Sequence[str],
    url: str | None = None,
    refresh: bool = False,
) -> dict[str, object]:
    """Live model IDs for one provider and key, or `fallback` with an error code."""
    source = LISTING_SOURCES.get(provider)
    if source is None:
        return {"models": list(fallback), "models_source": "fallback", "models_error": "listing_unsupported"}
    default_url, auth, parse = source
    url = url or default_url
    if not url:
        return {"models": list(fallback), "models_source": "fallback", "models_error": "listing_unsupported"}
    cache_key = (provider, url, _key_fingerprint(api_key))
    now = time.monotonic()
    with _listing_lock:
        cached = _listing_cache.get(cache_key)
    if cached and not refresh and now - cached[0] < _LISTING_CACHE_TTL_SECONDS:
        return {"models": list(cached[1]), "models_source": "live", "models_error": None}
    try:
        models = list(dict.fromkeys(parse(fetch_json(url, auth(api_key), _LISTING_TIMEOUT_SECONDS))))
    except Exception:  # noqa: BLE001 - any provider failure degrades to the fallback
        models = []
    if not models:
        return {"models": list(fallback), "models_source": "fallback", "models_error": "provider_unreachable"}
    with _listing_lock:
        _listing_cache[cache_key] = (now, models)
    return {"models": models, "models_source": "live", "models_error": None}
