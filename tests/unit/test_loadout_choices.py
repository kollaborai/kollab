"""The model picker's rows: every provider profile x model x effort (LoadoutManager.list_choices),
and the ACP harness options it offers from kollabor_ai.acp_options."""

import json
from types import SimpleNamespace

from kollabor_ai import acp_options
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.profile_manager import EFFORT_LEVELS


class _Profile:
    def __init__(self, name, provider, model="", command=(), effort=""):
        self.name, self._provider, self._model, self.command = name, provider, model, command
        self.effort = effort
        self.auth_type = "oauth"

    def get_provider(self):
        return self._provider

    def get_model(self):
        return self._model

    def get_api_key(self):
        return ""

    def get_endpoint(self):
        return ""


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _manager(*profiles, loadouts=None):
    pm = SimpleNamespace(config=None, list_profiles=lambda: list(profiles),
                         get_profile=lambda n: next((p for p in profiles if p.name == n), None))
    return LoadoutManager(pm, config=_Config({"kollabor.llm.loadouts": loadouts or {}}))


def test_each_model_comes_at_its_default_and_every_effort_level(monkeypatch):
    # Marco's ask: "codex luna max" must be one pick, not a model here and an effort elsewhere.
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6-luna"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: ["gpt-6-luna", "gpt-6-sol"])
    rows = manager.list_choices()
    assert [r["effort"] for r in rows if r["model"] == "gpt-6-luna"] == ["", *EFFORT_LEVELS]
    assert {r["model"] for r in rows} == {"gpt-6-luna", "gpt-6-sol"}
    assert all(r["profile"] == "openai-oauth" for r in rows)


def test_a_profile_with_a_saved_effort_names_it_on_every_row(monkeypatch):
    # No blank row: it would send no effort and silently run at the saved "max".
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6.1-terra", effort="max"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    assert [r["effort"] for r in manager.list_choices()] == list(EFFORT_LEVELS)


def test_a_saved_loadout_keeps_its_own_effort_and_comes_first(monkeypatch):
    loadouts = {"luna-max": {"provider_profile": "openai-oauth", "model": "gpt-6-luna", "effort": "max"}}
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6-luna"), loadouts=loadouts)
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    rows = manager.list_choices()
    assert rows[0] == {"profile": "openai-oauth", "provider": "openai_responses", "model": "gpt-6-luna",
                       "effort": "max", "loadout": "luna-max"}
    own = {"profile": "openai-oauth", "provider": "openai_responses", "model": "gpt-6-luna", "effort": ""}
    assert own in rows  # the profile's own model


def test_an_acp_harness_offers_what_its_sessions_reported():
    manager = _manager(_Profile("codex-cli", "acp", command=("codex-acp",)), _Profile("claude-code", "acp"))
    reported = {"codex-cli": {"models": ["gpt-6-luna", "gpt-6.1-terra"], "efforts": ["high", "max"]}}
    rows = manager.list_choices(reported)
    assert {"profile": "codex-cli", "provider": "acp", "model": "gpt-6-luna", "effort": "max"} in rows
    assert len([r for r in rows if r["profile"] == "codex-cli"]) == 2 * 2  # every row names its effort
    # A harness that names its own "default" effort gets no second, blank default row.
    reported["codex-cli"]["efforts"] = ["default", "max"]
    assert [r["effort"] for r in manager.list_choices(reported) if r["model"] == "gpt-6-luna"] == ["default", "max"]
    # Never started: only its own default, until a session reports its options.
    assert [r for r in rows if r["profile"] == "claude-code"] == [
        {"profile": "claude-code", "provider": "acp", "model": "", "effort": ""}
    ]


def test_a_harness_session_remembers_its_options():
    from kollabor.llm.acp_bridge import ACPRunner

    runner = ACPRunner.__new__(ACPRunner)
    runner.config_options = [{"id": "model", "options": [{"value": "opus"}]}]  # no profile yet: kept, not saved
    assert acp_options.load() == {}
    runner.profile = SimpleNamespace(name="claude-code")
    runner.config_options = []  # a fresh runner: nothing to remember
    assert acp_options.load() == {}
    runner.config_options = [
        {"id": "model", "category": "model", "options": [{"value": "opus"}, {"value": "sonnet"}]},
        {"id": "effort", "options": [{"group": "levels", "options": [{"value": "high"}, {"value": "max"}]}]},
    ]
    assert runner.config_options[0]["id"] == "model"
    assert acp_options.load() == {"claude-code": {"models": ["opus", "sonnet"], "efforts": ["high", "max"]}}
    saved = json.loads(acp_options._path().read_text())
    assert saved["claude-code"]["models"] == ["opus", "sonnet"]
