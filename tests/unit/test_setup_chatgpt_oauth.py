"""Public setup-panel coverage for the ChatGPT device-code path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.panels.setup import SetupPanel


class FakeOAuthClient:
    def __init__(self, outcome: str = "waiting") -> None:
        self.outcome = outcome
        self.release = asyncio.Event()
        self.closed = False

    async def _request_device_code(self):
        return SimpleNamespace(
            device_auth_id="device-secret",
            user_code="ABCD-EFGH",
            interval=1,
        )

    async def _poll_for_auth_code(self, _device):
        await self.release.wait()
        if self.outcome != "success":
            from kollabor_ai.oauth.openai_oauth import OAuthError

            raise OAuthError(self.outcome)
        return SimpleNamespace(authorization_code="auth-secret", code_verifier="verifier")

    async def _exchange_code(self, _code, _verifier):
        return SimpleNamespace(
            access_token="access-secret",
            refresh_token="refresh-secret",
            account_id="account-123",
            expires_at=9999999999,
        )

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_setup_panel_offers_chatgpt_sign_in_and_oauth_steps():
    panel = await SetupPanel().describe(SimpleNamespace(), {})
    provider = panel["steps"][0]["fields"][0]

    assert "OpenAI (sign in with ChatGPT)" in provider["options"]
    assert [step["id"] for step in panel["steps"]] == [
        "provider",
        "api_key",
        "endpoint",
        "oauth_sign_in",
        "model",
        "oauth_review",
    ]
    assert {"oauth_start", "oauth_poll", "oauth_cancel"}.issubset(
        {action["id"] for action in panel["actions"]}
    )
    assert "Run /login in a terminal" not in str(panel)
    assert "Azure and advanced setups" in str(panel)


@pytest.mark.asyncio
async def test_oauth_start_returns_code_and_poll_reports_waiting(monkeypatch):
    client = FakeOAuthClient()
    monkeypatch.setattr(
        "kollabor_ai.oauth.openai_oauth.OpenAIOAuthClient", lambda: client
    )
    panel = SetupPanel()

    started = await panel.act(SimpleNamespace(), "oauth_start", {})

    assert started["ok"]
    assert started["verification_url"] == "https://auth.openai.com/codex/device"
    assert started["user_code"] == "ABCD-EFGH"
    assert started["status"] == "waiting"
    assert started["poll"]["action"] == "oauth_poll"
    assert "device-secret" not in str(started)

    waiting = await panel.act(
        SimpleNamespace(), "oauth_poll", {"flow_id": started["flow_id"]}
    )
    assert waiting["status"] == "waiting"

    cancelled = await panel.act(
        SimpleNamespace(), "oauth_cancel", {"flow_id": started["flow_id"]}
    )
    assert cancelled["ok"]
    assert client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["Device code expired", "Authorization denied", "network unavailable"])
async def test_oauth_poll_exposes_terminal_failures(monkeypatch, outcome):
    client = FakeOAuthClient(outcome)
    monkeypatch.setattr(
        "kollabor_ai.oauth.openai_oauth.OpenAIOAuthClient", lambda: client
    )
    panel = SetupPanel()
    started = await panel.act(SimpleNamespace(), "oauth_start", {})
    client.release.set()

    for _ in range(20):
        result = await panel.act(
            SimpleNamespace(), "oauth_poll", {"flow_id": started["flow_id"]}
        )
        if result["status"] != "waiting":
            break
        await asyncio.sleep(0)

    assert result["status"] == "failed"
    assert outcome.lower() in result["message"].lower()
    assert "access-secret" not in str(result)
