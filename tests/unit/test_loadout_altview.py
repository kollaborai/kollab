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


def test_loadout_form_saves_the_compaction_threshold(monkeypatch):
    import asyncio

    class _RecordingManager(_Manager):
        def __init__(self):
            self.updates = []

        def update(self, name, **fields):
            self.updates.append((name, fields))
            return True

        def get(self, name):
            return None

    monkeypatch.setattr(loadout_altview, "_model_registry_info", lambda model: {})
    manager = _RecordingManager()
    base = _base_loadout()
    base.compaction_threshold_k = 500
    view = loadout_altview.LoadoutFormAltView()
    view.set_context(
        manager=manager,
        profile_manager=_ProfileManager("https://api.openai.com/v1"),
        base=base,
        mode="edit",
    )
    view._build_widgets()
    asyncio.run(view._run_save("gpt-5.6-luna"))
    assert manager.updates[0][1]["compaction_threshold_k"] == 500


def test_loadout_form_refuses_oversized_compaction_threshold(monkeypatch):
    import asyncio

    monkeypatch.setattr(loadout_altview, "_model_registry_info", lambda model: {})
    view = loadout_altview.LoadoutFormAltView()
    view.set_context(
        manager=_Manager(),
        profile_manager=_ProfileManager("https://api.openai.com/v1"),
        base=_base_loadout(),
        mode="edit",
    )
    view._build_widgets()
    view._compaction_widget.set_value("9" * 5000)

    asyncio.run(view._run_save("gpt-5.6-luna"))

    assert view._stage == "edit"
    assert view._error == "Compaction Threshold must be a whole number of at least 1."
