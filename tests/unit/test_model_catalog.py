"""list_provider_models: per-provider dispatch + graceful failure."""

from types import SimpleNamespace

import pytest

from kollabor_ai import model_catalog


class _Profile:
    def __init__(self, provider="", auth_type=""):
        self._provider = provider
        self.auth_type = auth_type

    def get_provider(self):
        return self._provider


@pytest.mark.asyncio
async def test_none_profile_returns_empty():
    assert await model_catalog.list_provider_models(None) == []


@pytest.mark.asyncio
async def test_anthropic_without_key_returns_empty():
    # No API key available -> graceful empty, picker still opens.
    assert await model_catalog.list_provider_models(_Profile("anthropic")) == []


@pytest.mark.asyncio
async def test_anthropic_dispatch(monkeypatch):
    class FakeProfile:
        def get_provider(self):
            return "anthropic"

        def get_api_key(self):
            return "sk-ant-xyz"

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {
                        "id": "claude-opus-4-7",
                        "display_name": "Claude Opus 4.7",
                        "type": "model",
                    },
                    {"id": "", "display_name": "No Id"},  # dropped (no id)
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            assert url == f"{model_catalog.ANTHROPIC_DEFAULT_BASE_URL}/v1/models"
            assert headers.get("x-api-key") == "sk-ant-xyz"
            return FakeResp()

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", FakeClient)

    out = await model_catalog.list_provider_models(FakeProfile())
    assert [m["id"] for m in out] == ["claude-opus-4-7"]
    assert "anthropic" in out[0]["note"]
    assert "Claude Opus 4.7" in out[0]["note"]


@pytest.mark.asyncio
async def test_oauth_dispatches_to_codex(monkeypatch):
    async def fake_tokens(*a, **k):
        return SimpleNamespace(access_token="tok", account_id="acct")

    async def fake_codex(access_token, account_id=None):
        assert access_token == "tok"
        return [
            {
                "slug": "gpt-5.6-sol",
                "context_window": 272000,
                "supported_reasoning_levels": [
                    {"effort": "low"},
                    {"effort": "max"},
                ],
            },
            {"slug": "gpt-5.5"},
            {"slug": ""},  # dropped (no slug)
        ]

    storage = SimpleNamespace(load_tokens=fake_tokens)
    monkeypatch.setattr(
        "kollabor_ai.oauth.OAuthTokenStorage", lambda: storage, raising=False
    )
    monkeypatch.setattr(
        "kollabor_ai.oauth.openai_oauth.query_codex_model_details",
        fake_codex,
        raising=False,
    )

    out = await model_catalog.list_provider_models(_Profile(auth_type="oauth"))
    ids = [m["id"] for m in out]
    assert ids == ["gpt-5.6-sol", "gpt-5.5"]  # empty slug dropped
    assert out[0]["note"] == "272K ctx • effort: low/max"
    assert out[1]["note"] == "codex"  # no metadata -> plain tag


@pytest.mark.asyncio
async def test_openai_dispatch_uses_profile_endpoint(monkeypatch):
    """A plain OpenAI key (no OAuth) still gets a catalog -- the /model gap."""

    class FakeProfile:
        def get_provider(self):
            return "openai"

        def get_api_key(self):
            return "sk-test"

        def get_endpoint(self):
            # local/custom profiles store the full chat endpoint
            return "http://localhost:1234/v1/chat/completions"

    seen = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-5.6", "owned_by": "openai"},
                    {"id": "gpt-4.1"},
                    {"id": ""},  # dropped
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization")
            return FakeResp()

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", FakeClient)

    out = await model_catalog.list_provider_models(FakeProfile())
    # /chat/completions trimmed, /models appended -- not a doubled path
    assert seen["url"] == "http://localhost:1234/v1/models"
    assert seen["auth"] == "Bearer sk-test"
    assert [m["id"] for m in out] == ["gpt-5.6", "gpt-4.1"]  # sorted desc
    assert out[0]["note"] == "openai"


def test_models_endpoint_normalization():
    cases = {
        "https://api.openai.com/v1": "https://api.openai.com/v1/models",
        "https://api.x.ai/v1/": "https://api.x.ai/v1/models",
        "http://localhost:1234/v1/chat/completions": "http://localhost:1234/v1/models",
        "": f"{model_catalog.OPENAI_DEFAULT_BASE_URL}/models",
        # a pasted Azure-style URL carries a query string; the path must not
        # land after the "?"
        "https://x.openai.azure.com/openai/deployments/d/chat/completions"
        "?api-version=2024-02-15-preview": (
            "https://x.openai.azure.com/openai/deployments/d/models"
        ),
    }
    for base, expected in cases.items():
        assert model_catalog._models_endpoint(base) == expected


@pytest.mark.asyncio
async def test_real_catalog_leads_with_chat_models(monkeypatch):
    """A provider's /models listing is not all chat models.

    Reverse-sorting the raw ids put whisper/tts/embeddings at the top of the
    picker and pushed every gpt-* row below them.
    """

    class FakeProfile:
        def get_provider(self):
            return "openai"

        def get_api_key(self):
            return "sk-test"

        def get_endpoint(self):
            return "https://api.openai.com/v1"

    ids = [
        "gpt-5.6",
        "gpt-4o",
        "gpt-4o-mini",
        "o1",
        "text-embedding-3-large",
        "text-embedding-ada-002",
        "whisper-1",
        "tts-1",
        "tts-1-hd",
        "dall-e-3",
        "omni-moderation-latest",
        "gpt-4o-transcribe",
    ]

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": i} for i in ids]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp()

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", FakeClient)

    out = [m["id"] for m in await model_catalog.list_provider_models(FakeProfile())]

    for dropped in (
        "whisper-1",
        "tts-1",
        "tts-1-hd",
        "dall-e-3",
        "text-embedding-3-large",
        "omni-moderation-latest",
        "gpt-4o-transcribe",
    ):
        assert dropped not in out, dropped
    assert out == ["o1", "gpt-5.6", "gpt-4o-mini", "gpt-4o"]


@pytest.mark.asyncio
async def test_openrouter_dispatch(monkeypatch):
    class FakeInfo:
        async def list_models(self):
            return [
                {
                    "id": "deepseek/deepseek-v3.2",
                    "context_length": 128000,
                    "supported_parameters": ["tools"],
                },
                {"id": "", "context_length": 1},  # dropped (no id)
            ]

    monkeypatch.setattr(
        "kollabor_ai.providers.openrouter_model_info.OpenRouterModelInfo",
        FakeInfo,
        raising=False,
    )

    out = await model_catalog.list_provider_models(_Profile("openrouter"))
    assert [m["id"] for m in out] == ["deepseek/deepseek-v3.2"]
    assert "128,000 ctx" in out[0]["note"]
    assert "tools" in out[0]["note"]


@pytest.mark.asyncio
async def test_fetch_failure_returns_empty(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(model_catalog, "_openai_oauth_models", boom)
    out = await model_catalog.list_provider_models(_Profile(auth_type="oauth"))
    assert out == []
