"""Review round 1 on #559, finding 1: the route refuses a level the provider never sends.

An Anthropic profile used to store ``none``/``minimal`` through the route (create,
update, switching provider, copy) and report it as active. The route refuses it
when it is written; OpenAI-shaped profiles still take both.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile, ProfileManager

NEW_LEVELS = ("none", "minimal")


class _Manager(ProfileManager):
    def __init__(self):
        self._profiles = {
            "oa": LLMProfile(name="oa", provider="openai", model="gpt-5.6"),
            "an": LLMProfile(
                name="an",
                provider="anthropic",
                model="claude-x",
                api_key="test-key",
                auth_type="api_key",
            ),
        }
        self._invalid_profile_errors = {}
        self._active_profile_name = "oa"

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True

    def add_profile(self, profile, save_to_config=False):
        if profile.name in self._profiles:
            return False
        self._profiles[profile.name] = profile
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
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_create_refuses_unsent_level_for_anthropic(client, manager, level):
    response = await client.post(
        "/profiles",
        json={
            "name": "new-an",
            "provider": "anthropic",
            "model": "claude-x",
            "effort": level,
        },
    )
    assert response.status_code == 422
    assert manager.get_profile("new-an") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_update_refuses_unsent_level_for_anthropic(client, manager, level):
    response = await client.put("/profiles/an", json={"effort": level})
    assert response.status_code == 422
    assert manager.get_profile("an").effort == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_update_refuses_level_when_switching_to_anthropic(client, manager, level):
    response = await client.put(
        "/profiles/oa", json={"provider": "anthropic", "effort": level}
    )
    assert response.status_code == 422
    assert manager.get_profile("oa").provider == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_update_still_accepts_level_for_openai(client, manager, level):
    response = await client.put("/profiles/oa", json={"effort": level})
    assert response.status_code == 200
    assert manager.get_profile("oa").effort == level


@pytest.mark.asyncio
async def test_copy_refuses_unsent_level_for_anthropic(client, manager):
    response = await client.post(
        "/profiles", json={"name": "an-copy", "copy_from": "an", "effort": "minimal"}
    )
    assert response.status_code == 422
    assert manager.get_profile("an-copy") is None


# Review round 3: provider "auto" that resolves to Anthropic (key sk-ant-...)
# follows the same rule as a profile that names Anthropic.


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_create_refuses_unsent_level_for_auto_resolving_to_anthropic(
    client, manager, level
):
    response = await client.post(
        "/profiles",
        json={
            "name": "new-auto",
            "provider": "auto",
            "model": "claude-x",
            "api_key": "sk-ant-test-key",
            "effort": level,
        },
    )
    assert response.status_code == 422
    assert manager.get_profile("new-auto") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_update_refuses_unsent_level_for_auto_resolving_to_anthropic(
    client, manager, level
):
    manager._profiles["auto-an"] = LLMProfile(
        name="auto-an", provider="auto", model="claude-x", api_key="sk-ant-test-key"
    )
    response = await client.put("/profiles/auto-an", json={"effort": level})
    assert response.status_code == 422
    assert manager.get_profile("auto-an").effort == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("level", NEW_LEVELS)
async def test_create_still_accepts_level_for_auto_resolving_to_openai(
    client, manager, level
):
    response = await client.post(
        "/profiles",
        json={
            "name": "new-auto-oa",
            "provider": "auto",
            "model": "gpt-5.6",
            "api_key": "sk-test-key",
            "effort": level,
        },
    )
    assert response.status_code in (200, 201)
    assert manager.get_profile("new-auto-oa").effort == level
