import asyncio
from types import SimpleNamespace

from kollabor_tui.key_parser import KeyPress
from plugins.altview.loadout_altview import LoadoutListAltView


def _key(char):
    return KeyPress(name=char, code=ord(char), char=char)


class _Profile:
    def __init__(self, name, provider):
        self.name = name
        self.provider = provider

    def get_provider(self):
        return self.provider


class _Profiles:
    def __init__(self):
        self.profiles = [
            _Profile("anthropic-cloud", "anthropic"),
            _Profile("openai", "openai"),
        ]
        self.removed = []

    def get_profile(self, name):
        return next((profile for profile in self.profiles if profile.name == name), None)

    def remove_provider_profiles(self, provider):
        self.removed.append(provider)
        names = [p.name for p in self.profiles if p.provider == provider]
        self.profiles = [p for p in self.profiles if p.provider != provider]
        return names


class _Manager:
    def __init__(self, profiles):
        self.profiles = profiles
        self.loadouts = [
            self._loadout("anthropic-model", "anthropic-cloud", implicit=True),
            self._loadout("openai-model", "openai", implicit=True),
            self._loadout("saved-anthropic", "anthropic-cloud", implicit=False),
        ]

    @staticmethod
    def _loadout(name, profile, implicit):
        return SimpleNamespace(
            name=name,
            provider_profile=profile,
            model="model",
            implicit=implicit,
            temperature=None,
            effort="",
            max_tokens=None,
            description="",
        )

    def list_loadouts(self):
        configured = {profile.name for profile in self.profiles.profiles}
        return [
            loadout
            for loadout in self.loadouts
            if not loadout.implicit or loadout.provider_profile in configured
        ]

    def provider_profiles(self):
        return list(self.profiles.profiles)


def _view():
    profiles = _Profiles()
    manager = _Manager(profiles)
    view = LoadoutListAltView()
    view.set_context(manager=manager, profile_manager=profiles, event_bus=None)
    view._refresh()
    view._selected = next(
        index for index, item in enumerate(view._items) if item.name == "anthropic-model"
    )
    return view, manager, profiles


def test_d_on_provider_model_arms_named_removal_then_cancels_on_other_key():
    view, _, profiles = _view()

    asyncio.run(view.handle_input(_key("d")))
    assert "Anthropic provider" in view._note
    assert "anthropic-cloud" in view._note
    assert "saved-anthropic" in view._note
    asyncio.run(view.handle_input(_key("x")))

    assert profiles.removed == []
    assert view._delete_armed is None


def test_second_d_removes_provider_and_refreshes_picker():
    view, manager, profiles = _view()

    asyncio.run(view.handle_input(_key("d")))
    asyncio.run(view.handle_input(_key("D")))

    assert profiles.removed == ["anthropic"]
    assert all(
        not item.implicit or item.provider_profile != "anthropic-cloud"
        for item in view._items
    )
    assert any(item.provider_profile == "openai" for item in view._items)
