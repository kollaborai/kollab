from types import SimpleNamespace

import plugins.altview.loadout_altview as loadout_altview


class _Profile:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def get_endpoint(self) -> str:
        return self.endpoint

    def get_provider(self) -> str:
        return "openai_responses"


class _ProfileManager:
    def __init__(self, endpoint: str):
        self.profile = _Profile(endpoint)

    def get_profile(self, name: str) -> _Profile:
        return self.profile


class _Manager:
    def list_loadouts(self):
        return []


def _base_loadout():
    return SimpleNamespace(
        name="gpt-5.6-luna",
        provider_profile="openai-oauth",
        model="gpt-5.6-luna",
        temperature=None,
        effort="max",
        max_tokens=None,
        description="",
        implicit=True,
    )


def test_chatgpt_codex_profile_is_detected():
    assert loadout_altview._is_chatgpt_codex_profile(
        _ProfileManager("https://chatgpt.com/backend-api/codex"),
        "openai-oauth",
    )
    assert not loadout_altview._is_chatgpt_codex_profile(
        _ProfileManager("https://api.openai.com/v1"),
        "openai-api",
    )


def test_loadout_form_shows_backend_default_for_chatgpt_oauth(monkeypatch):
    monkeypatch.setattr(loadout_altview, "_model_registry_info", lambda model: {})
    view = loadout_altview.LoadoutFormAltView()
    view.set_context(
        manager=_Manager(),
        profile_manager=_ProfileManager("https://chatgpt.com/backend-api/codex"),
        base=_base_loadout(),
    )

    view._build_widgets()

    assert view._max_tokens_supported is False
    assert type(view._max_tokens_widget).__name__ == "LabelWidget"
    assert view._max_tokens_widget.get_value() == "backend default (ChatGPT OAuth)"


def test_loadout_form_keeps_editable_budget_for_public_responses(monkeypatch):
    monkeypatch.setattr(loadout_altview, "_model_registry_info", lambda model: {})
    view = loadout_altview.LoadoutFormAltView()
    view.set_context(
        manager=_Manager(),
        profile_manager=_ProfileManager("https://api.openai.com/v1"),
        base=_base_loadout(),
    )

    view._build_widgets()

    assert view._max_tokens_supported is True
    assert type(view._max_tokens_widget).__name__ == "TextInputWidget"
    assert view._max_tokens_widget.get_pending_value() == "16384"
