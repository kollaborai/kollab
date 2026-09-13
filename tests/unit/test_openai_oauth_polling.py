"""OpenAI device authorization polling behavior."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from kollabor_ai.oauth import openai_oauth


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def json(self):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses):
        self.closed = False
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append((url, json, timeout))
        return self.responses.pop(0)


@pytest.fixture
def immediate_poll_sleep(monkeypatch):
    async def skip_sleep(_delay):
        return None

    monkeypatch.setattr(
        openai_oauth,
        "asyncio",
        SimpleNamespace(sleep=skip_sleep, TimeoutError=asyncio.TimeoutError),
    )


def _client_with_responses(*responses):
    client = openai_oauth.OpenAIOAuthClient()
    session = _FakeSession(responses)
    client._session = session
    return client, session


@pytest.mark.asyncio
async def test_poll_waits_for_approval_without_logging_auth_secrets(
    immediate_poll_sleep, caplog
):
    client, session = _client_with_responses(
        _FakeResponse(403, {"error": "authorization_pending"}),
        _FakeResponse(
            200,
            {
                "authorization_code": "sensitive-auth-code",
                "code_verifier": "sensitive-verifier",
            },
        ),
    )
    device = openai_oauth.DeviceCodeResponse(
        device_auth_id="device-id", user_code="ABCD-EFGH", interval=2
    )

    caplog.set_level(logging.INFO, logger="kollabor_ai.oauth.openai_oauth")
    result = await client._poll_for_auth_code(device)

    assert result.authorization_code == "sensitive-auth-code"
    assert result.code_verifier == "sensitive-verifier"
    assert len(session.calls) == 2
    assert all(call[0] == openai_oauth.DEVICE_TOKEN_URL for call in session.calls)
    assert all(call[1]["user_code"] == "ABCD-EFGH" for call in session.calls)
    assert "still pending" in caplog.text
    assert "authorization approved" in caplog.text
    assert "ABCD-EFGH" not in caplog.text
    assert "sensitive-auth-code" not in caplog.text
    assert "sensitive-verifier" not in caplog.text


@pytest.mark.asyncio
async def test_expired_403_response_fails_instead_of_polling(
    immediate_poll_sleep,
):
    client, session = _client_with_responses(
        _FakeResponse(403, {"error": "expired_token", "detail": "code expired"})
    )
    device = openai_oauth.DeviceCodeResponse(
        device_auth_id="device-id", user_code="ABCD-EFGH", interval=2
    )

    with pytest.raises(openai_oauth.OAuthError, match="Device code expired"):
        await client._poll_for_auth_code(device)

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_long_server_interval_cannot_extend_poll_timeout(monkeypatch):
    now = [100.0]
    delays = []

    def monotonic():
        return now[0]

    async def advance_sleep(delay):
        delays.append(delay)
        now[0] += delay

    monkeypatch.setattr(openai_oauth, "DEVICE_CODE_TIMEOUT", 10)
    monkeypatch.setattr(openai_oauth, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(
        openai_oauth,
        "asyncio",
        SimpleNamespace(sleep=advance_sleep, TimeoutError=asyncio.TimeoutError),
    )
    client, session = _client_with_responses()
    device = openai_oauth.DeviceCodeResponse(
        device_auth_id="device-id", user_code="ABCD-EFGH", interval=3600
    )

    with pytest.raises(openai_oauth.OAuthError, match="Timed out waiting"):
        await client._poll_for_auth_code(device)

    assert delays == [10]
    assert session.calls == []
