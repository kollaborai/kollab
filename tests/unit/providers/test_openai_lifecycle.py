"""OpenAI and Azure shutdown must close the installed async SDK client."""

import socket
import warnings

import pytest

from kollabor_ai.providers.azure_provider import AzureOpenAIProvider
from kollabor_ai.providers.models import AzureOpenAIConfig, OpenAIConfig, ProviderType
from kollabor_ai.providers.openai_provider import OpenAIProvider


@pytest.fixture(params=["openai", "azure_openai"])
def provider(request, monkeypatch):
    def refuse_network(*args, **kwargs):
        raise AssertionError("Provider lifecycle tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.delenv("OPENAI_ORG_ID", raising=False)
    monkeypatch.delenv("OPENAI_PROJECT_ID", raising=False)
    if request.param == "openai":
        return OpenAIProvider(
            OpenAIConfig(
                provider=ProviderType.OPENAI,
                api_key="owned-dummy-not-a-real-key",
                model="owned-lifecycle",
                base_url="http://127.0.0.1:9/v1",
            )
        )
    return AzureOpenAIProvider(
        AzureOpenAIConfig(
            provider=ProviderType.AZURE_OPENAI,
            api_key="owned-dummy-azure-key-000000000000",
            model="owned-lifecycle",
            azure_endpoint="https://127.0.0.1:9",
            deployment_id="owned",
        )
    )


@pytest.mark.asyncio
async def test_shutdown_closes_real_sdk_client_before_dropping_ownership(provider):
    await provider.initialize()
    client = provider._client
    assert client is not None and not client.is_closed()

    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", RuntimeWarning)
            await provider.shutdown()
        assert client.is_closed()
        assert provider._client is None and provider.is_shutdown
        assert not [warning for warning in captured if "was never awaited" in str(warning.message)]

        await provider.shutdown()
        assert client.is_closed() and provider._client is None
    finally:
        # Retain ownership even when the regression leaves the SDK client open.
        await client.close()


@pytest.mark.asyncio
async def test_shutdown_before_initialize_is_safe_and_idempotent(provider):
    assert provider._client is None
    assert not provider.is_initialized and not provider.is_shutdown
    await provider.shutdown()
    await provider.shutdown()
    assert provider._client is None
    assert provider.is_shutdown and not provider.is_initialized
