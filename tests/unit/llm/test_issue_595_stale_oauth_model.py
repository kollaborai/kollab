"""#595: a slow OAuth model lookup must not overwrite a profile edited while it ran.

``LLMService.switch_profile`` resolves a generic ``codex`` model with a network
lookup. The lookup runs while the profile can still be edited (``/model``,
``/llm``, profile replacement), so its result must be committed only if the
profile is still the same stored object and has not been edited since the
lookup began.

The switch runs against a real ``ProfileManager``; only the OAuth token, the
codex lookup and provider initialization are faked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor_ai import profile_manager as profile_manager_module
from kollabor_ai.profile_manager import LLMProfile, ProfileManager

PROFILE = "codex-oauth"
BASE = "anthropic-base"
LOOKUP_RESULT = "gpt-5.6-luna"
NEWER = "gpt-5.4"


class _FakeTokenStorage:
    async def load_tokens(self, provider, auto_refresh=True):
        return SimpleNamespace(access_token="fresh-token", account_id="acct-1")


class _PendingLookup:
    """Stands in for query_codex_models and holds it open until released."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, access_token, account_id=None):
        self.started.set()
        await self.release.wait()
        return [LOOKUP_RESULT]


@pytest.fixture
def manager(monkeypatch):
    # Keep the developer's config files and any local .kollab/ out of the manager.
    monkeypatch.setattr(
        profile_manager_module, "get_global_config_path_candidates", lambda: []
    )
    monkeypatch.setattr(
        # the manager reads another folder's local layer by passing its path (#972)
        profile_manager_module,
        "get_local_config_path_candidates",
        lambda project_path=None: [],
    )
    pm = ProfileManager()
    assert pm.add_profile(
        LLMProfile(name=BASE, provider="anthropic", model="claude-sonnet-4", api_key="k")
    )
    assert pm.add_profile(
        LLMProfile(
            name=PROFILE,
            provider="openai_responses",
            model="codex",
            auth_type="oauth",
            api_key="stale-token",
            base_url="https://chatgpt.example/backend-api/codex",
        )
    )
    assert pm.set_active_profile(BASE, persist=False)
    return pm


@pytest.fixture
def lookup():
    pending = _PendingLookup()
    with (
        patch("kollabor_ai.oauth.OAuthTokenStorage", _FakeTokenStorage),
        patch("kollabor_ai.oauth.openai_oauth.query_codex_models", pending),
        patch(
            "kollabor.llm.llm_coordinator.create_config_from_profile",
            return_value={},
        ),
    ):
        yield pending


@pytest.fixture
def reinit_calls():
    """Model name the provider was re-initialized with, one entry per call."""
    return []


@pytest.fixture
def coord(manager, reinit_calls):
    coord = MagicMock()
    coord._provider_lock = asyncio.Lock()
    coord.profile_manager = manager
    coord.event_bus = None

    async def reinitialize_provider(profile):
        reinit_calls.append(profile.model)
        return True

    coord.api_service.reinitialize_provider = AsyncMock(
        side_effect=reinitialize_provider
    )
    coord._provider_registry.get_provider = AsyncMock(
        return_value=SimpleNamespace(provider_name="openai_responses", model=LOOKUP_RESULT)
    )
    return coord


async def _start_switch(coord, lookup):
    """Start a switch and return once its codex lookup is pending."""
    task = asyncio.create_task(
        LLMService.switch_profile(coord, PROFILE, persist=False)
    )
    await asyncio.wait_for(lookup.started.wait(), timeout=5)
    return task


@pytest.mark.asyncio
async def test_model_edit_during_lookup_is_not_overwritten(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    assert manager.update_profile(PROFILE, model=NEWER)
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert manager.get_profile(PROFILE).model == NEWER
    assert reinit_calls == []
    assert manager.get_active_profile().name == BASE


@pytest.mark.asyncio
async def test_effort_edit_during_lookup_refuses_the_stale_switch(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    assert manager.update_profile(PROFILE, effort="high")
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    profile = manager.get_profile(PROFILE)
    assert ok is False
    assert profile.model == "codex"
    assert profile.effort == "high"
    assert reinit_calls == []
    assert manager.get_active_profile().name == BASE


@pytest.mark.asyncio
async def test_profile_replaced_during_lookup_refuses_the_stale_switch(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    assert manager.remove_profile(PROFILE)
    replacement = LLMProfile(
        name=PROFILE,
        provider="openai_responses",
        model=NEWER,
        auth_type="oauth",
        api_key="new-token",
        base_url="https://chatgpt.example/backend-api/codex",
    )
    assert manager.add_profile(replacement)
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert manager.get_profile(PROFILE) is replacement
    assert replacement.model == NEWER
    assert reinit_calls == []
    assert manager.get_active_profile().name == BASE


@pytest.mark.asyncio
async def test_edit_undone_during_lookup_still_refuses_the_stale_switch(
    manager, coord, lookup, reinit_calls
):
    """Same-value ABA: the model returns to ``codex``, but it was edited."""
    task = await _start_switch(coord, lookup)
    assert manager.update_profile(PROFILE, model=NEWER)
    assert manager.update_profile(PROFILE, model="codex")
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert manager.get_profile(PROFILE).model == "codex"
    assert reinit_calls == []
    assert manager.get_active_profile().name == BASE


@pytest.mark.asyncio
async def test_cancelled_lookup_commits_nothing_and_releases_the_lock(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.get_profile(PROFILE).model == "codex"
    assert reinit_calls == []
    assert not coord._provider_lock.locked()
    assert manager.get_active_profile().name == BASE


@pytest.mark.asyncio
async def test_edit_to_another_profile_does_not_block_the_switch(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    assert manager.update_profile(BASE, model="claude-opus-4")
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is True
    assert manager.get_profile(PROFILE).model == LOOKUP_RESULT
    assert reinit_calls == [LOOKUP_RESULT]
    assert manager.get_active_profile().name == PROFILE


@pytest.mark.asyncio
async def test_untouched_profile_commits_the_lookup_result(
    manager, coord, lookup, reinit_calls
):
    task = await _start_switch(coord, lookup)
    lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is True
    assert manager.get_profile(PROFILE).model == LOOKUP_RESULT
    assert reinit_calls == [LOOKUP_RESULT]
    assert manager.get_active_profile().name == PROFILE


class _PendingStep:
    """Gate one post-reinitialization await so a target can be edited mid-switch."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self):
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("suspension", ["acp_close", "provider_registry"])
@pytest.mark.parametrize("change", ["edit", "replace"])
async def test_profile_change_after_provider_reinit_rolls_back_runtime(
    manager, coord, lookup, reinit_calls, suspension, change
):
    acp_profile = LLMProfile(name="acp-session", provider="acp", model="acp-model")
    assert manager.add_profile(acp_profile)
    assert manager.set_active_profile(acp_profile.name, persist=False)
    previous_api_profile = manager.get_profile(BASE)
    coord.api_service._profile = previous_api_profile
    coord._current_provider = None

    gate = _PendingStep()
    started_profiles = []

    async def close_acp():
        if suspension == "acp_close":
            await gate()

    async def start_acp(profile):
        started_profiles.append(profile)
        return True

    coord.event_bus = SimpleNamespace(
        get_service=lambda _name: SimpleNamespace(
            close=close_acp,
            start=start_acp,
        )
    )

    registry_models = []
    runtime_provider = SimpleNamespace(
        provider_name="openai_responses", model=LOOKUP_RESULT
    )

    async def get_provider(config):
        registry_models.append(config["model"])
        if suspension == "provider_registry":
            await gate()
        return runtime_provider

    coord._provider_registry.get_provider = AsyncMock(side_effect=get_provider)

    async def reinitialize(profile):
        reinit_calls.append(profile.model)
        coord.api_service._profile = profile
        return True

    coord.api_service.reinitialize_provider = AsyncMock(side_effect=reinitialize)

    def provider_config(profile_data):
        return {"model": profile_data["model"]}

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile",
        side_effect=provider_config,
    ):
        task = await _start_switch(coord, lookup)
        lookup.release.set()
        await asyncio.wait_for(gate.started.wait(), timeout=5)

        if change == "edit":
            assert manager.update_profile(PROFILE, model=NEWER)
            current_target = manager.get_profile(PROFILE)
        else:
            assert manager.remove_profile(PROFILE)
            current_target = LLMProfile(
                name=PROFILE,
                provider="openai_responses",
                model=NEWER,
                auth_type="oauth",
                api_key="replacement-token",
                base_url="https://chatgpt.example/backend-api/codex",
            )
            assert manager.add_profile(current_target)

        gate.release.set()
        ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert manager.get_profile(PROFILE) is current_target
    assert current_target.model == NEWER
    assert manager.get_active_profile() is acp_profile
    assert coord.api_service._profile is previous_api_profile
    assert coord._current_provider is None
    assert reinit_calls == [LOOKUP_RESULT, previous_api_profile.model]
    assert started_profiles == [acp_profile]
    assert registry_models == (
        [] if suspension == "acp_close" else [LOOKUP_RESULT]
    )


@pytest.mark.asyncio
async def test_acp_close_failure_restores_the_previous_api_runtime(
    manager, coord, lookup, reinit_calls
):
    # #595 review (P2): the API runtime moves to the target before the ACP close.
    # A failed close must put the previous API runtime back, not only restart ACP.
    acp_profile = LLMProfile(name="acp-session", provider="acp", model="acp-model")
    assert manager.add_profile(acp_profile)
    assert manager.set_active_profile(acp_profile.name, persist=False)
    previous_api_profile = manager.get_profile(BASE)
    coord.api_service._profile = previous_api_profile
    coord._current_provider = None

    started_profiles = []

    async def close_acp():
        raise RuntimeError("close failed")

    async def start_acp(profile):
        started_profiles.append(profile)
        return True

    coord.event_bus = SimpleNamespace(
        get_service=lambda _name: SimpleNamespace(close=close_acp, start=start_acp)
    )

    async def reinitialize(profile):
        reinit_calls.append(profile.model)
        coord.api_service._profile = profile
        return True

    coord.api_service.reinitialize_provider = AsyncMock(side_effect=reinitialize)

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile",
        side_effect=lambda profile_data: {"model": profile_data["model"]},
    ):
        task = await _start_switch(coord, lookup)
        lookup.release.set()
        ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert manager.get_active_profile() is acp_profile
    assert coord.api_service._profile is previous_api_profile
    assert coord._current_provider is None
    assert reinit_calls == [LOOKUP_RESULT, previous_api_profile.model]
    assert started_profiles == [acp_profile]



# --- Profile-scoped OAuth tokens -------------------------------------------
#
# A profile not named ``openai-oauth`` keeps its tokens under its own name.
# ``switch_profile`` asks the default store, finds nothing and skips its guarded
# lookup; the lookup then runs inside ``APICommunicationService`` during provider
# re-initialization. These tests run that path for real: only the token store,
# the codex lookup and the provider registry are faked.

SCOPED = "work-oauth"


class _ScopedTokenStorage:
    """Tokens exist only for a profile-scoped request, as for a provisioned profile."""

    async def load_tokens(self, provider, auto_refresh=True, *, profile_name=None):
        if profile_name is None:
            return None
        return SimpleNamespace(access_token="scoped-token", account_id="acct-2")


def _scoped_profile(model="codex"):
    return LLMProfile(
        name=SCOPED,
        provider="openai_responses",
        model=model,
        auth_type="oauth",
        api_key="stale-token",
        base_url="https://chatgpt.example/backend-api/codex",
    )


@pytest.fixture
def scoped_manager(manager):
    assert manager.add_profile(_scoped_profile())
    return manager


@pytest.fixture
def scoped_lookup():
    pending = _PendingLookup()
    with (
        patch("kollabor_ai.oauth.OAuthTokenStorage", _ScopedTokenStorage),
        patch("kollabor_ai.oauth.openai_oauth.query_codex_models", pending),
        patch(
            "kollabor_ai.api_communication_service.create_config_from_profile",
            return_value={},
        ),
        patch(
            "kollabor_ai.api_communication_service.ProviderRegistry.get_provider",
            AsyncMock(
                return_value=SimpleNamespace(
                    provider_name="openai_responses", model=LOOKUP_RESULT
                )
            ),
        ),
        patch(
            "kollabor.llm.llm_coordinator.create_config_from_profile",
            return_value={},
        ),
    ):
        yield pending


@pytest.fixture
def scoped_coord(scoped_manager):
    from kollabor_ai.api_communication_service import APICommunicationService

    config = MagicMock()
    config.get = lambda key, default=None: default
    coord = MagicMock()
    coord._provider_lock = asyncio.Lock()
    coord.profile_manager = scoped_manager
    coord.event_bus = None
    # The real service: reinitialize_provider, _refresh_oauth_token and
    # _resolve_oauth_model are not stubbed.
    coord.api_service = APICommunicationService(
        config, None, scoped_manager.get_profile(BASE)
    )
    coord._provider_registry.get_provider = AsyncMock(
        return_value=SimpleNamespace(provider_name="openai_responses", model=LOOKUP_RESULT)
    )
    return coord


async def _start_scoped_switch(coord, lookup):
    task = asyncio.create_task(
        LLMService.switch_profile(coord, SCOPED, persist=False)
    )
    await asyncio.wait_for(lookup.started.wait(), timeout=5)
    return task


@pytest.mark.asyncio
async def test_scoped_token_model_edit_during_lookup_is_not_overwritten(
    scoped_manager, scoped_coord, scoped_lookup
):
    task = await _start_scoped_switch(scoped_coord, scoped_lookup)
    assert scoped_manager.update_profile(SCOPED, model=NEWER)
    scoped_lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert scoped_manager.get_profile(SCOPED).model == NEWER
    assert scoped_manager.get_active_profile().name == BASE
    # The refused switch leaves the previous profile's provider in place.
    assert scoped_coord.api_service._profile.name == BASE


@pytest.mark.asyncio
async def test_scoped_token_profile_replaced_during_lookup_refuses_the_switch(
    scoped_manager, scoped_coord, scoped_lookup
):
    task = await _start_scoped_switch(scoped_coord, scoped_lookup)
    assert scoped_manager.remove_profile(SCOPED)
    replacement = _scoped_profile(model=NEWER)
    assert scoped_manager.add_profile(replacement)
    scoped_lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is False
    assert scoped_manager.get_profile(SCOPED) is replacement
    assert replacement.model == NEWER
    assert scoped_manager.get_active_profile().name == BASE
    assert scoped_coord.api_service._profile.name == BASE


@pytest.mark.asyncio
async def test_scoped_token_untouched_profile_commits_the_lookup_result(
    scoped_manager, scoped_coord, scoped_lookup
):
    task = await _start_scoped_switch(scoped_coord, scoped_lookup)
    scoped_lookup.release.set()

    ok = await asyncio.wait_for(task, timeout=5)

    assert ok is True
    assert scoped_manager.get_profile(SCOPED).model == LOOKUP_RESULT
    assert scoped_manager.get_active_profile().name == SCOPED
    assert scoped_coord.api_service._profile.name == SCOPED


@pytest.mark.asyncio
async def test_refresh_outside_a_switch_does_not_overwrite_a_model_edit(
    scoped_manager, scoped_coord, scoped_lookup
):
    """Startup and the 401 refresh run the same lookup with no switch around it."""
    profile = scoped_manager.get_profile(SCOPED)
    service = scoped_coord.api_service
    service._profile = profile

    refresh = asyncio.create_task(service._refresh_oauth_token())
    await asyncio.wait_for(scoped_lookup.started.wait(), timeout=5)
    assert scoped_manager.update_profile(SCOPED, model=NEWER)
    scoped_lookup.release.set()
    await asyncio.wait_for(refresh, timeout=5)

    assert profile.model == NEWER
