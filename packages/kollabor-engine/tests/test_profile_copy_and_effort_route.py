"""Profiles route: per-profile reasoning effort and copying a profile.

The request layer already carries effort (ProviderConfig.effort -> Anthropic
output_config.effort / OpenAI reasoning_effort) and LLMProfile persists it;
these tests guard the HTTP surface added in profiles.py plus the contract
that a stored profile's effort reaches the provider config on switch. A copy
(POST with copy_from) starts from the stored profile, since the page never
sees its key or the settings the form has no field for.
"""

from dataclasses import replace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile, ProfileManager
from kollabor_ai.providers.registry import create_config_from_profile


class _Manager(ProfileManager):
    """Real create/update logic, no config file."""

    def __init__(self):
        self._profiles = {
            "edit-me": LLMProfile(name="edit-me", provider="openai", model="gpt-4"),
            "pinned": LLMProfile(
                name="pinned",
                provider="anthropic",
                model="claude-x",
                api_key="test-key",
                effort="max",
            ),
        }
        self._active_profile_name = "other"

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True

    def add_profile(self, profile, persist=False):
        self._profiles[profile.name] = profile

    def save_to_config(self, *args, **kwargs):
        return True


@pytest.fixture
def manager(monkeypatch):
    import kollabor_engine.routes.profiles as profile_routes

    mgr = _Manager()
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: mgr)
    return mgr


@pytest_asyncio.fixture
async def client(manager):
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_persists_normalized_effort(client, manager):
    response = await client.post(
        "/profiles",
        json={
            "name": "coordinator",
            "provider": "anthropic",
            "model": "claude-x",
            "effort": " MAX ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["effort"] == "max"
    stored = manager.get_profile("coordinator")
    assert stored is not None and stored.effort == "max"


@pytest.mark.asyncio
async def test_create_rejects_unknown_effort_with_422(client):
    response = await client.post(
        "/profiles",
        json={
            "name": "bad",
            "provider": "anthropic",
            "model": "claude-x",
            "effort": "maximum",
        },
    )

    assert response.status_code == 422
    assert "effort must be one of" in response.text


@pytest.mark.asyncio
async def test_create_without_effort_omits_it(client, manager):
    response = await client.post(
        "/profiles",
        json={"name": "plain", "provider": "anthropic", "model": "claude-x"},
    )

    assert response.status_code == 201
    assert response.json().get("effort", "") == ""
    stored = manager.get_profile("plain")
    assert stored is not None and stored.effort == ""


@pytest.mark.asyncio
async def test_update_sets_effort(client, manager):
    response = await client.put("/profiles/edit-me", json={"effort": "High"})

    assert response.status_code == 200
    assert response.json()["effort"] == "high"
    assert manager.get_profile("edit-me").effort == "high"


@pytest.mark.asyncio
async def test_update_empty_string_clears_effort(client, manager):
    response = await client.put("/profiles/pinned", json={"effort": ""})

    assert response.status_code == 200
    assert "effort" not in response.json()
    assert manager.get_profile("pinned").effort == ""


@pytest.mark.asyncio
async def test_update_without_effort_leaves_it_untouched(client, manager):
    response = await client.put("/profiles/pinned", json={"temperature": 0.3})

    assert response.status_code == 200
    assert response.json()["effort"] == "max"
    assert manager.get_profile("pinned").effort == "max"


@pytest.mark.asyncio
async def test_profile_effort_reaches_provider_config_on_switch(manager):
    """Activation contract: switch_profile builds provider config from
    profile.to_dict(); the stored effort must survive that translation."""
    profile = manager.get_profile("pinned")
    config = create_config_from_profile(profile.to_dict())

    assert config.effort == "max"


@pytest.mark.asyncio
async def test_copy_keeps_the_key_sign_in_and_unshown_settings(client, manager):
    manager.add_profile(
        replace(
            manager.get_profile("pinned"),
            timeout=600,
            extra_headers={"x-team": "a"},
            auth_type="oauth",
        )
    )

    response = await client.post(
        "/profiles",
        json={
            "name": "pinned-copy",
            "provider": "anthropic",
            "model": "claude-y",
            "effort": "",
            "copy_from": "pinned",
        },
    )

    assert response.status_code == 201
    assert "api_key" not in response.json()
    copy = manager.get_profile("pinned-copy")
    assert copy.api_key == "test-key"
    assert copy.auth_type == "oauth"
    assert (copy.timeout, copy.extra_headers) == (600, {"x-team": "a"})
    assert (copy.model, copy.effort) == ("claude-y", "")
    assert manager.get_profile("pinned").model == "claude-x"


@pytest.mark.asyncio
async def test_copy_with_a_typed_key_uses_it(client, manager):
    response = await client.post(
        "/profiles",
        json={
            "name": "pinned-2",
            "provider": "anthropic",
            "model": "claude-x",
            "api_key": "new-key",
            "copy_from": "pinned",
        },
    )

    assert response.status_code == 201
    assert manager.get_profile("pinned-2").api_key == "new-key"


@pytest.mark.asyncio
async def test_copy_of_a_missing_profile_is_404(client):
    response = await client.post(
        "/profiles",
        json={"name": "x", "provider": "openai", "model": "gpt-4", "copy_from": "gone"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["is_provisioned", "api_key_from_env"])
async def test_copy_never_moves_an_enrolled_or_environment_key(client, manager, flag):
    manager.add_profile(
        LLMProfile(
            name="held",
            provider="openai",
            model="gpt-4",
            api_key="held-secret",
            **{flag: True},
        )
    )

    response = await client.post(
        "/profiles",
        json={
            "name": "held-copy",
            "provider": "openai",
            "model": "gpt-4",
            "copy_from": "held",
        },
    )

    assert response.status_code == 201
    copy = manager.get_profile("held-copy")
    assert copy.api_key == ""
    assert not copy.is_provisioned and not copy.api_key_from_env
