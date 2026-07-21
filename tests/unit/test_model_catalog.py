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
            assert url == model_catalog.ANTHROPIC_MODELS_ENDPOINT
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
        return ["gpt-5-codex", "gpt-5", ""]

    storage = SimpleNamespace(load_tokens=fake_tokens)
    monkeypatch.setattr(
        "kollabor_ai.oauth.OAuthTokenStorage", lambda: storage, raising=False
    )
    monkeypatch.setattr(
        "kollabor_ai.oauth.openai_oauth.query_codex_models", fake_codex, raising=False
    )

    out = await model_catalog.list_provider_models(_Profile(auth_type="oauth"))
    ids = [m["id"] for m in out]
    assert ids == ["gpt-5-codex", "gpt-5"]  # empty id dropped
    assert all(m["note"] == "codex" for m in out)


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
