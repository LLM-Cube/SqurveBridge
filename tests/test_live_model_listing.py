"""Live provider model listing behind Configure LLM, tested through the HTTP API."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from demo import model_catalog
from demo.session_auth import SessionCredentialRegistry, SqlCredential

SECRET = "listing-secret-for-tests"
SESSION_VALUE = "session-held-value"


class FakeProviders:
    """Stands in for provider HTTP: maps a URL to a JSON body or an exception."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        outcome = self.responses.get(url)
        if outcome is None:
            raise OSError("unreachable")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def openai_style(*ids):
    return {"data": [{"id": model_id} for model_id in ids]}


class LiveModelListingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api_server = api_server

    def setUp(self):
        self.api_server._sql_credentials = SessionCredentialRegistry()
        model_catalog.reset_listing_cache()
        self.client = self.api_server.app.test_client()

    def tearDown(self):
        model_catalog.reset_listing_cache()

    def list_models(self, fetcher, **payload):
        body = {"provider": "deepseek", "api_key": SECRET, **payload}
        with patch.object(model_catalog, "fetch_json", fetcher):
            return self.client.post("/api/llm/models", json=body)

    def test_live_list_replaces_the_static_catalog(self):
        fetcher = FakeProviders({
            "https://api.deepseek.com/models": openai_style("deepseek-flash", "deepseek-v4-pro"),
        })
        response = self.list_models(fetcher)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["models_source"], "live")
        self.assertEqual(response.json["models"], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(response.json["default_model"], "deepseek-flash")
        self.assertEqual(fetcher.calls[0]["headers"]["Authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_openai_style_providers_drop_non_chat_models(self):
        fetcher = FakeProviders({
            "https://api.openai.com/v1/models": openai_style(
                "gpt-5-mini", "text-embedding-3-large", "whisper-1", "dall-e-3", "gpt-4.1-mini",
            ),
        })
        response = self.list_models(fetcher, provider="openai")
        self.assertEqual(response.json["models"], ["gpt-5-mini", "gpt-4.1-mini"])

    def test_zhipu_lists_from_the_open_platform(self):
        fetcher = FakeProviders({
            "https://open.bigmodel.cn/api/paas/v4/models": openai_style("glm-5.2", "glm-4.7"),
        })
        response = self.list_models(fetcher, provider="zhipu")
        self.assertEqual(response.json["models"], ["glm-5.2", "glm-4.7"])

    def test_qwen_lists_from_the_selected_region(self):
        fetcher = FakeProviders({
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models": openai_style("qwen3.7-plus"),
        })
        response = self.list_models(fetcher, provider="qwen", endpoint_id="international")
        self.assertEqual(response.json["models"], ["qwen3.7-plus"])
        self.assertEqual(response.json["models_source"], "live")

    def test_qwen_workspace_endpoint_requires_a_valid_workspace(self):
        fetcher = FakeProviders()
        response = self.list_models(
            fetcher, provider="qwen", endpoint_id="china_workspace", workspace_id="../attacker",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fetcher.calls, [])

    def test_claude_lists_with_anthropic_auth_headers(self):
        fetcher = FakeProviders({
            "https://api.anthropic.com/v1/models?limit=1000": openai_style("claude-haiku-4-5", "claude-sonnet-4-5"),
        })
        response = self.list_models(fetcher, provider="claude")
        self.assertEqual(response.json["models"], ["claude-haiku-4-5", "claude-sonnet-4-5"])
        headers = fetcher.calls[0]["headers"]
        self.assertEqual(headers["x-api-key"], SECRET)
        self.assertIn("anthropic-version", headers)
        self.assertNotIn("Authorization", headers)

    def test_gemini_keeps_generate_content_models_without_the_prefix(self):
        fetcher = FakeProviders({
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000": {
                "models": [
                    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                ],
            },
        })
        response = self.list_models(fetcher, provider="gemini")
        self.assertEqual(response.json["models"], ["gemini-2.5-flash"])
        self.assertEqual(fetcher.calls[0]["headers"]["x-goog-api-key"], SECRET)

    def test_provider_failure_returns_the_static_catalog(self):
        fetcher = FakeProviders({"https://api.deepseek.com/models": OSError(f"boom {SECRET}")})
        response = self.list_models(fetcher)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["models_source"], "fallback")
        self.assertEqual(response.json["models_error"], "provider_unreachable")
        self.assertIn("deepseek-v4-flash", response.json["models"])
        self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_empty_or_malformed_listing_falls_back(self):
        for body in ({}, {"data": []}, {"data": "nope"}, [1, 2]):
            with self.subTest(body=body):
                model_catalog.reset_listing_cache()
                fetcher = FakeProviders({"https://api.deepseek.com/models": body})
                response = self.list_models(fetcher)
                self.assertEqual(response.json["models_source"], "fallback")
                self.assertTrue(response.json["models"])

    def test_repeat_requests_use_the_cache_until_refresh(self):
        fetcher = FakeProviders({"https://api.deepseek.com/models": openai_style("deepseek-flash")})
        self.list_models(fetcher)
        self.list_models(fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.list_models(fetcher, refresh=True)
        self.assertEqual(len(fetcher.calls), 2)

    def test_different_keys_never_share_a_cached_list(self):
        url = "https://api.deepseek.com/models"
        self.list_models(FakeProviders({url: openai_style("only-for-a")}), api_key="key-a")
        fetcher_b = FakeProviders({url: openai_style("only-for-b")})
        response = self.list_models(fetcher_b, api_key="key-b")
        self.assertEqual(response.json["models"], ["only-for-b"])
        self.assertEqual(len(fetcher_b.calls), 1)

    def test_unknown_provider_is_rejected(self):
        response = self.list_models(FakeProviders(), provider="nope")
        self.assertEqual(response.status_code, 400)

    def test_local_mode_falls_back_to_the_environment_key(self):
        fetcher = FakeProviders({"https://api.deepseek.com/models": openai_style("deepseek-flash")})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}, clear=False), \
                patch.object(model_catalog, "fetch_json", fetcher):
            response = self.client.post("/api/llm/models", json={"provider": "deepseek"})
        self.assertEqual(response.json["models_source"], "live")
        self.assertEqual(fetcher.calls[0]["headers"]["Authorization"], "Bearer env-key")

    def test_no_key_anywhere_is_a_client_error(self):
        env = {name: "" for name in ("DEEPSEEK_API_KEY",)}
        with patch.dict(os.environ, env, clear=False), \
                patch.object(self.api_server, "load_dotenv", lambda *_a, **_k: None):
            response = self.client.post("/api/llm/models", json={"provider": "deepseek"})
        self.assertEqual(response.status_code, 400)

    def test_hosted_mode_uses_the_session_key_and_ignores_the_environment(self):
        fetcher = FakeProviders({"https://api.deepseek.com/models": openai_style("deepseek-flash")})
        hosted = {"SQURVE_DEPLOYMENT_TARGET": "hf-space", "DEEPSEEK_API_KEY": "server-env-key"}
        with patch.dict(os.environ, hosted, clear=False), patch.object(model_catalog, "fetch_json", fetcher):
            anonymous = self.client.post("/api/llm/models", json={"provider": "deepseek"})
            self.assertEqual(anonymous.status_code, 400)
            self.assertEqual(fetcher.calls, [])
            self.client.set_cookie("squrve_session", "session-1")
            self.api_server._sql_credentials.put(
                "session-1", SqlCredential("deepseek", "deepseek-flash", SESSION_VALUE),
            )
            response = self.client.post("/api/llm/models", json={"provider": "deepseek"})
        self.assertEqual(response.json["models_source"], "live")
        self.assertEqual(fetcher.calls[0]["headers"]["Authorization"], f"Bearer {SESSION_VALUE}")

    def test_hosted_mode_rate_limits_each_session(self):
        fetcher = FakeProviders({"https://api.deepseek.com/models": openai_style("deepseek-flash")})
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False), \
                patch.object(model_catalog, "fetch_json", fetcher):
            self.client.set_cookie("squrve_session", "session-2")
            statuses = [
                self.client.post(
                    "/api/llm/models", json={"provider": "deepseek", "api_key": SECRET, "refresh": True},
                ).status_code
                for _ in range(40)
            ]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[-1], 429)

    def test_hosted_mode_rejects_cross_origin_requests(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.post(
                "/api/llm/models",
                json={"provider": "deepseek", "api_key": SECRET},
                headers={"Origin": "https://evil.example"},
            )
        self.assertEqual(response.status_code, 403)

    def test_capabilities_keep_their_shape(self):
        response = self.client.get("/api/capabilities")
        provider = next(item for item in response.json["llm_providers"] if item["id"] == "deepseek")
        for field in ("id", "models", "default_model"):
            self.assertIn(field, provider)
        self.assertEqual(provider["models_source"], "catalog")

    def test_hosted_capabilities_also_mark_the_built_in_list(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.get("/api/sql-auth")
        self.assertTrue(all(item["models_source"] == "catalog" for item in response.json["providers"]))

    def test_failures_are_reported_with_distinct_codes(self):
        import urllib.error

        def http_error(code):
            return urllib.error.HTTPError("https://api.deepseek.com/models", code, "x", {}, None)

        cases = {
            "credential_rejected": http_error(401),
            "provider_rate_limited": http_error(429),
            "provider_timeout": TimeoutError("slow"),
            "provider_unreachable": OSError("down"),
            "empty_listing": None,
        }
        for code, outcome in cases.items():
            with self.subTest(code=code):
                model_catalog.reset_listing_cache()
                body = openai_style() if outcome is None else outcome
                response = self.list_models(FakeProviders({"https://api.deepseek.com/models": body}))
                self.assertEqual(response.json["models_source"], "fallback")
                self.assertEqual(response.json["models_error"], code)

    def test_default_model_is_listed_first_when_the_provider_still_lists_it(self):
        fetcher = FakeProviders({
            "https://api.deepseek.com/models": openai_style("deepseek-v4-pro", "deepseek-v4-flash"),
        })
        response = self.list_models(fetcher)
        self.assertEqual(response.json["models"][0], "deepseek-v4-flash")
        self.assertEqual(response.json["default_model"], "deepseek-v4-flash")

    def test_claude_listing_asks_for_the_full_page(self):
        fetcher = FakeProviders({"https://api.anthropic.com/v1/models?limit=1000": openai_style("claude-haiku-4-5")})
        response = self.list_models(fetcher, provider="claude")
        self.assertEqual(response.json["models"], ["claude-haiku-4-5"])

    def test_listing_never_follows_redirects(self):
        import urllib.request

        handlers = [type(h) for h in model_catalog.listing_opener().handlers]
        self.assertNotIn(urllib.request.HTTPRedirectHandler, handlers)

    def test_listing_cache_is_bounded(self):
        url = "https://api.deepseek.com/models"
        for index in range(model_catalog.LISTING_CACHE_MAX_ENTRIES + 20):
            self.list_models(FakeProviders({url: openai_style("m")}), api_key=f"key-{index}")
        self.assertLessEqual(model_catalog.listing_cache_size(), model_catalog.LISTING_CACHE_MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
