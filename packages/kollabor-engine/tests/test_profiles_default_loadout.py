"""GET /profiles carries the default loadout, so New Session can start on it (#957).

The terminal applies ``kollabor.llm.default_loadout`` when no profile is named. A web
session always names one, so without this the form could only show the active profile's
own saved model, which is not what the person set as the default.
"""

import asyncio
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile, ProfileManager


class _Manager(ProfileManager):
    """Real profile and loadout logic, no config file."""

    def __init__(self, effort=""):
        self._profiles = {
            "oauth": LLMProfile(name="oauth", provider="openai_responses", model="gpt-5.6", effort=effort),
        }
        self._active_profile_name = "oauth"
        self._invalid_profile_errors = {}


class _Config:
    """The saved config the engine reads loadouts from: dotted keys over a dict."""

    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        node = self.values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def shutdown(self):
        """What ConfigService has and the engine calls when a request is done with it."""


def _llm(**extra):
    return {
        "kollabor": {
            "llm": {
                "loadouts": {
                    "luna-max": {"provider_profile": "oauth", "model": "gpt-6-luna", "effort": "max"},
                },
                **extra,
            }
        }
    }


@pytest_asyncio.fixture
async def get_profiles(monkeypatch):
    import kollabor_engine.routes.profiles as profile_routes
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async def get(config, profile_effort=""):
        monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: _Manager(profile_effort))
        # the engine reads saved loadouts through ConfigService, as GET /profiles/choices does
        monkeypatch.setattr("kollabor_config.ConfigService", lambda *args, **kwargs: _Config(config))
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            return await client.get("/profiles")

    return get


@pytest.mark.asyncio
async def test_the_default_loadout_is_listed_with_the_profile_it_runs_on(get_profiles):
    response = await get_profiles(_llm(default_loadout={"name": "luna-max", "level": "global"}))

    assert response.status_code == 200
    body = response.json()
    # not the profile's own saved model (gpt-5.6): the loadout's model and effort
    assert body["default_loadout"] == {
        "name": "luna-max",
        "profile": "oauth",
        "provider": "openai_responses",
        "model": "gpt-6-luna",
        "effort": "max",
    }
    assert body["active"] == "oauth"


@pytest.mark.asyncio
async def test_no_default_loadout_lists_none(get_profiles):
    body = (await get_profiles(_llm())).json()

    assert body["default_loadout"] is None
    assert [profile["name"] for profile in body["profiles"]] == ["oauth"]


@pytest.mark.asyncio
async def test_a_default_that_names_nothing_saved_lists_none_and_the_profiles_still_answer(get_profiles):
    response = await get_profiles(_llm(default_loadout={"name": "gone", "level": "global"}))

    assert response.status_code == 200
    assert response.json()["default_loadout"] is None


@pytest.mark.asyncio
async def test_a_deleted_default_never_picks_a_loadout_with_a_similar_name(get_profiles):
    # "luna" was the default and is gone; "luna-max" remains. A lookup that accepts a unique substring
    # would start New Session on a loadout nobody chose, so the saved name must match exactly.
    response = await get_profiles(_llm(default_loadout={"name": "luna", "level": "global"}))

    assert response.status_code == 200
    assert response.json()["default_loadout"] is None


def _luna_without_effort(**extra):
    config = _llm(**extra)
    config["kollabor"]["llm"]["loadouts"] = {"luna": {"provider_profile": "oauth", "model": "gpt-6-luna"}}
    return config


@pytest.mark.asyncio
async def test_a_default_loadout_with_no_effort_keeps_the_profiles_own_effort(get_profiles):
    # The terminal only overrides the effort when the loadout sets one; the web form sends what it shows,
    # and an empty level would clear the profile's saved one ("model default").
    config = _luna_without_effort(default_loadout={"name": "luna", "level": "global"})

    body = (await get_profiles(config, profile_effort="high")).json()

    assert body["default_loadout"]["model"] == "gpt-6-luna"
    assert body["default_loadout"]["effort"] == "high"


@pytest.mark.asyncio
async def test_a_default_loadout_effort_wins_over_the_profiles_and_none_stays_none(get_profiles):
    set_on_loadout = _llm(default_loadout={"name": "luna-max", "level": "global"})
    assert (await get_profiles(set_on_loadout, profile_effort="low")).json()["default_loadout"]["effort"] == "max"

    neither = _luna_without_effort(default_loadout={"name": "luna", "level": "global"})
    assert (await get_profiles(neither)).json()["default_loadout"]["effort"] == ""


def _pollers():
    """Config services still polling their file on this loop."""
    return [task for task in asyncio.all_tasks() if task.get_name() == "config-mtime-poll" and not task.done()]


@pytest.fixture
def saved_default(monkeypatch, tmp_path):
    """A real saved config under a scratch HOME: the real ConfigService runs, the developer's file is not touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".kollab").mkdir()
    saved = _llm(default_loadout={"name": "luna-max", "level": "global"})
    (tmp_path / ".kollab" / "config.json").write_text(json.dumps(saved))
    return tmp_path


async def _list_profiles_five_times():
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        responses = [await client.get("/profiles") for _ in range(5)]
        await asyncio.sleep(0.05)  # a cancelled task ends on the loop's next turns
    return responses


@pytest.mark.asyncio
async def test_listing_profiles_leaves_no_config_poller_running(monkeypatch, saved_default):
    # A ConfigService polls its file every second until it is shut down. The open web UI lists profiles
    # every few seconds, so a poller left behind by each request piles up for as long as the tab stays open.
    import kollabor_engine.routes.profiles as profile_routes

    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: _Manager())

    responses = await _list_profiles_five_times()

    assert [response.status_code for response in responses] == [200] * 5
    assert responses[-1].json()["default_loadout"]["name"] == "luna-max"
    assert _pollers() == []


@pytest.mark.asyncio
async def test_a_default_that_cannot_be_read_leaves_no_config_poller_running(monkeypatch, saved_default):
    # The lookup is allowed to fail (the profile list must still answer), and a failed one is the path
    # that is easiest to forget to clean up after.
    import kollabor_engine.routes.profiles as profile_routes

    class Broken(_Manager):
        def get_profile(self, name):
            raise RuntimeError("profile store unreadable")

    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: Broken())

    responses = await _list_profiles_five_times()

    assert [response.status_code for response in responses] == [200] * 5
    assert responses[-1].json()["default_loadout"] is None
    assert _pollers() == []
