"""The setup wizard persists and activates the shared ChatGPT OAuth profile."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.panels.setup import SetupPanel


class FakeOAuthClient:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def _request_device_code(self):
        return SimpleNamespace(device_auth_id="device", user_code="ABCD-EFGH", interval=1)

    async def _poll_for_auth_code(self, _device):
        await self.release.wait()
        return SimpleNamespace(authorization_code="auth", code_verifier="verifier")

    async def _exchange_code(self, _code, _verifier):
        return SimpleNamespace(
            access_token="access-secret",
            refresh_token="refresh-secret",
            account_id="account-123",
            expires_at=9999999999,
        )

    async def close(self):
        return None


class FakeProfileManager:
    def __init__(self) -> None:
        self._profiles = {}
        self.active = None

    def get_profile(self, name):
        return self._profiles.get(name)

    def set_active_profile(self, name, persist=True):
        self.active = name


class FakeSetupContext:
    def __init__(self):
        self._profile_manager = FakeProfileManager()
        self.active = None

    async def set_active_profile(self, name, persist=True, reload_profile=True):
        self.active = name
        self._profile_manager.set_active_profile(name, persist=persist)


@pytest.mark.asyncio
async def test_oauth_success_stores_tokens_and_activates_login_profile(monkeypatch):
    from kollabor_ai.profile_manager import LLMProfile

    client = FakeOAuthClient()
    stored = []

    class FakeStorage:
        async def store_tokens(self, provider, tokens):
            stored.append((provider, tokens.access_token))

    monkeypatch.setattr(
        "kollabor_ai.oauth.openai_oauth.OpenAIOAuthClient", lambda: client
    )
    monkeypatch.setattr("kollabor_ai.oauth.OAuthTokenStorage", FakeStorage)
    context = FakeSetupContext()
    panel = SetupPanel()
    started = await panel.act(context, "oauth_start", {})
    client.release.set()

    for _ in range(20):
        signed_in = await panel.act(
            context, "oauth_poll", {"flow_id": started["flow_id"]}
        )
        if signed_in["status"] == "signed_in":
            break
        await asyncio.sleep(0)

    assert signed_in["status"] == "signed_in"
    assert signed_in["panel"]["context"]["oauth_status"] == "signed_in"
    assert signed_in["panel"]["context"]["account"] == "account-123"

    result = await panel.act(
        context,
        "finish",
        {
            "values": {
                "provider": "OpenAI (sign in with ChatGPT)",
                "oauth_model": "gpt-5.6-luna",
            },
            "oauth_flow_id": started["flow_id"],
        },
    )

    profile = context._profile_manager.get_profile("openai-oauth")
    assert result["ok"]
    assert context.active == context._profile_manager.active == "openai-oauth"
    assert isinstance(profile, LLMProfile)
    assert profile.provider == "openai_responses"
    assert profile.model == "gpt-5.6-luna"
    assert profile.auth_type == "oauth"
    assert profile.api_key == "access-secret"
    assert stored == [("openai", "access-secret")]
    assert "access-secret" not in str(result)
