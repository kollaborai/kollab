import asyncio
from types import SimpleNamespace

from kollabor.panels.llm import LlmPanel


class _Profile:
    def __init__(self, name, provider):
        self.name = name
        self.provider = provider

    def get_provider(self):
        return self.provider

    def get_effort_provider(self):
        return self.provider

    def get_model(self):
        return "model"

    def get_api_key(self):
        return "configured"

    def get_endpoint(self):
        return "https://provider.invalid/v1"


class _Manager:
    def __init__(self, profiles):
        self.profiles = profiles
        self.loadouts = [
            SimpleNamespace(
                name=f"{profile.name}-model",
                provider_profile=profile.name,
                model="model",
                implicit=True,
                temperature=None,
                effort="",
                max_tokens=None,
                description="",
            )
            for profile in profiles
        ]
        self.loadouts.append(
            SimpleNamespace(
                name="saved-anthropic",
                provider_profile="anthropic-cloud",
                model="model",
                implicit=False,
                temperature=None,
                effort="",
                max_tokens=None,
                description="",
            )
        )

    def list_loadouts(self):
        return self.loadouts

    def provider_profiles(self):
        return self.profiles

    def get_default(self):
        return None

    def get(self, name):
        return next((row for row in self.loadouts if row.name == name), None)


class _Profiles:
    def __init__(self, profiles):
        self.profiles = {profile.name: profile for profile in profiles}
        self.removed_provider = None

    def get_active_profile(self):
        return self.profiles["openai"]

    def get_profile(self, name):
        return self.profiles.get(name)

    def remove_provider_profiles(self, provider):
        self.removed_provider = provider
        return [name for name, p in self.profiles.items() if p.provider == provider]


def _setup(monkeypatch, *, empty_anthropic=False):
    profiles = [
        _Profile("anthropic-cloud", "anthropic"),
        _Profile("anthropic-local", "anthropic"),
        _Profile("openai", "openai"),
    ]
    pm = _Profiles(profiles)
    manager = _Manager(profiles)
    if empty_anthropic:
        manager.loadouts = [
            loadout
            for loadout in manager.loadouts
            if loadout.provider_profile != "anthropic-cloud" or not loadout.implicit
        ]
    monkeypatch.setattr("kollabor.panels.llm._manager", lambda ctx: manager)
    return LlmPanel(), SimpleNamespace(_profile_manager=pm), manager, pm


def test_web_loadout_provider_section_offers_named_two_step_removal(monkeypatch):
    panel, ctx, _, _ = _setup(monkeypatch)

    result = asyncio.run(panel.describe(ctx, {}))

    anthropic = next(row for row in result["rows"] if row["group"] == "Models — Anthropic")
    assert anthropic["actions"] == ["activate", "edit", "set_default", "remove_provider"]
    assert anthropic["action_labels"]["remove_provider"] == "Remove Anthropic provider"
    assert "anthropic-cloud, anthropic-local" in anthropic["action_confirmations"]["remove_provider"]
    assert "saved-anthropic" in anthropic["action_confirmations"]["remove_provider"]
    openai = next(row for row in result["rows"] if row["group"] == "Models — OpenAI")
    assert "remove_provider" in openai["actions"]


def test_web_provider_action_calls_shared_removal_function(monkeypatch):
    panel, ctx, _, pm = _setup(monkeypatch)

    result = asyncio.run(panel.act(ctx, "remove_provider", {"name": "anthropic-cloud-model"}))

    assert pm.removed_provider == "anthropic"
    assert "Removed Anthropic provider profiles" in result["message"]


def test_web_empty_provider_section_still_offers_removal(monkeypatch):
    panel, ctx, _, _ = _setup(monkeypatch, empty_anthropic=True)

    result = asyncio.run(panel.describe(ctx, {}))

    empty = next(group for group in result["empty_groups"] if group["group"] == "Models — Anthropic")
    assert empty["id"] == "anthropic-cloud"
    assert empty["actions"] == ["remove_provider"]
