"""Profiles route: per-profile reasoning effort and copying a profile.

The request layer already carries effort (ProviderConfig.effort -> Anthropic
output_config.effort / OpenAI reasoning_effort) and LLMProfile persists it;
these tests guard the HTTP surface added in profiles.py plus the contract
that a stored profile's effort reaches the provider config on switch. A copy
(POST with copy_from) starts from the stored profile, since the page never
sees its key or the settings the form has no field for.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile, ProfileManager
from kollabor_ai.providers.registry import create_config_from_profile


class _Manager(ProfileManager):
    """Real create/update/copy logic with an observable config-save stub."""

    def __init__(self):
        self._profiles = {
            "edit-me": LLMProfile(name="edit-me", provider="openai", model="gpt-4"),
            "pinned": LLMProfile(
                name="pinned",
                provider="anthropic",
                model="claude-x",
                api_key="test-key",
                auth_type="api_key",
                effort="max",
                timeout=600,
                extra_headers={"x-team": "a"},
            ),
        }
        self._invalid_profile_errors = {}
        self._active_profile_name = "other"
        self.persisted_profiles = set()

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        self.persisted_profiles.add(profile.name)
        return True

    def add_profile(self, profile, save_to_config=False):
        if profile.name in self._profiles:
            return False
        self._profiles[profile.name] = profile
        if save_to_config and not self.save_profile_values_to_config(profile):
            self._profiles.pop(profile.name, None)
            return False
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
async def test_copy_keeps_saved_key_and_unshown_settings(client, manager):
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
    assert "test-key" not in response.text
    copy = manager.get_profile("pinned-copy")
    assert copy.api_key == "test-key"
    assert copy.auth_type == "api_key"
    assert (copy.timeout, copy.extra_headers) == (600, {"x-team": "a"})
    assert (copy.model, copy.effort) == ("claude-y", "")
    assert "pinned-copy" in manager.persisted_profiles
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
            "effort": "high",
            "copy_from": "pinned",
        },
    )

    assert response.status_code == 201
    copy = manager.get_profile("pinned-2")
    assert copy.api_key == "new-key"
    assert copy.effort == "high"
    assert "pinned-2" in manager.persisted_profiles


@pytest.mark.asyncio
async def test_copy_rejects_unknown_effort_with_422(client, manager):
    response = await client.post(
        "/profiles",
        json={
            "name": "invalid-copy",
            "provider": "anthropic",
            "model": "claude-y",
            "effort": "turbo",
            "copy_from": "pinned",
        },
    )

    assert response.status_code == 422
    assert "effort must be one of" in response.text
    assert manager.get_profile("invalid-copy") is None


@pytest.mark.parametrize(
    ("source_effort", "overrides"),
    [("max", {"effort": "turbo"}), ("turbo", {})],
)
def test_duplicate_profile_rejects_unsupported_effort(
    manager, source_effort, overrides
):
    manager.get_profile("pinned").effort = source_effort

    with pytest.raises(ValueError, match="unsupported reasoning effort"):
        manager.duplicate_profile(
            "pinned", "invalid-copy", overrides=overrides, save_to_config=True
        )

    assert manager.get_profile("invalid-copy") is None
    assert "invalid-copy" not in manager.persisted_profiles


def test_duplicate_profile_normalizes_valid_effort_override(manager):
    copy = manager.duplicate_profile(
        "pinned", "normalized-copy", overrides={"effort": " HIGH "}, save_to_config=False
    )

    assert copy.effort == "high"
    assert copy.get_effort() == "high"


@pytest.mark.asyncio
async def test_copy_does_not_inherit_source_specific_effort_env(
    client, manager, monkeypatch
):
    manager.add_profile(
        LLMProfile(
            name="anthropic",
            provider="anthropic",
            model="claude-x",
            effort="medium",
        )
    )
    monkeypatch.setenv("KOLLAB_ANTHROPIC_EFFORT", "high")
    monkeypatch.delenv("KOLLAB_TEAM_ANTHRO_EFFORT", raising=False)
    monkeypatch.delenv("KOLLAB_EFFORT", raising=False)

    response = await client.post(
        "/profiles",
        json={
            "name": "team-anthro",
            "provider": "anthropic",
            "model": "claude-y",
            "effort": "low",
            "copy_from": "anthropic",
        },
    )

    assert response.status_code == 201
    source = manager.get_profile("anthropic")
    copied = manager.get_profile("team-anthro")
    assert source.get_effort() == "high"
    assert copied.effort == "low"
    assert copied.get_effort() == "low"


@pytest.mark.asyncio
async def test_copy_of_a_missing_profile_is_404(client):
    response = await client.post(
        "/profiles",
        json={"name": "x", "provider": "openai", "model": "gpt-4", "copy_from": "gone"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type,is_provisioned", [("oauth", False), ("api_key", True)])
async def test_copy_rejects_oauth_and_provisioned_credentials(
    client, manager, auth_type, is_provisioned
):
    manager.add_profile(
        LLMProfile(
            name="held",
            provider="openai",
            model="gpt-4",
            api_key="held-secret",
            auth_type=auth_type,
            is_provisioned=is_provisioned,
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

    assert response.status_code == 422
    assert "cannot be copied" in response.text or "instead of copying" in response.text
    assert manager.get_profile("held-copy") is None


@pytest.mark.asyncio
async def test_copy_of_env_key_uses_target_env_and_does_not_copy_secret(
    client, manager, monkeypatch
):
    manager.add_profile(
        LLMProfile(
            name="held",
            provider="openai",
            model="gpt-4",
            api_key="held-secret",
            api_key_from_env=True,
        )
    )
    target = LLMProfile(name="held-copy", provider="openai", model="gpt-4")
    target_env = target._get_env_key("API_KEY")
    monkeypatch.setenv(target_env, "target-secret")
    monkeypatch.delenv("KOLLAB_API_KEY", raising=False)

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
    assert copy.api_key_from_env
    assert copy.get_api_key() == "target-secret"
    assert "held-secret" not in response.text
    assert "held-copy" in manager.persisted_profiles


@pytest.mark.asyncio
async def test_copy_of_env_key_requires_target_env(client, manager, monkeypatch):
    manager.add_profile(
        LLMProfile(
            name="held",
            provider="openai",
            model="gpt-4",
            api_key="held-secret",
            api_key_from_env=True,
        )
    )
    target = LLMProfile(name="held-copy", provider="openai", model="gpt-4")
    target_env = target._get_env_key("API_KEY")
    monkeypatch.delenv(target_env, raising=False)
    monkeypatch.delenv("KOLLAB_API_KEY", raising=False)

    response = await client.post(
        "/profiles",
        json={
            "name": "held-copy",
            "provider": "openai",
            "model": "gpt-4",
            "copy_from": "held",
        },
    )

    assert response.status_code == 422
    assert target_env in response.text
    assert manager.get_profile("held-copy") is None
