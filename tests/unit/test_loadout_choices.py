"""The model picker's rows: every provider profile x model x effort (LoadoutManager.list_choices),
and the ACP harness options it offers from kollabor_ai.acp_options."""

import json
from types import SimpleNamespace

from kollabor_ai import acp_options
from kollabor_ai.loadout_manager import LoadoutManager


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


def test_each_model_comes_at_every_level_its_catalog_names_and_never_blank(monkeypatch):
    # Marco's ask: "codex luna max" is one pick. Sodalite on #553: no level a request
    # would 400 on (gpt-6-luna stops at max), and no blank row, which would keep an
    # earlier pick instead of the default.
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6-luna"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: ["gpt-6-luna", "gpt-6.1-terra", "gpt-6-sol"])
    # Levels come from the catalog however old it is: the 1 h list cache would drop them.
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, max_age=3600: [
        {"id": "gpt-6-luna", "note": "272K ctx • effort: low/medium/high/xhigh/max"},  # a cache from before `efforts`
        {"id": "gpt-6.1-terra", "efforts": ["low", "ultra"]},
    ] if max_age == float("inf") else [])
    levels = {m: [r["effort"] for r in manager.list_choices() if r["model"] == m]
              for m in ("gpt-6-luna", "gpt-6.1-terra", "gpt-6-sol")}
    assert levels == {
        "gpt-6-luna": ["low", "medium", "high", "xhigh", "max"],
        "gpt-6.1-terra": ["low", "ultra"],
        "gpt-6-sol": ["low", "medium", "high"],  # not in the catalog: what every OpenAI-style API takes
    }


def test_a_provider_without_catalog_levels_offers_its_own(monkeypatch):
    manager = _manager(_Profile("claude", "anthropic", "claude-opus-5-5"), _Profile("box", "ollama", "llama"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: [])
    rows = manager.list_choices()
    assert [r["effort"] for r in rows if r["profile"] == "claude"] == ["low", "medium", "high", "max"]
    assert [r["effort"] for r in rows if r["profile"] == "box"] == [""]  # its provider sends no effort


def test_a_model_that_does_not_reason_takes_no_effort(monkeypatch):
    import kollabor_ai.model_registry as registry

    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-4o"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: [])
    monkeypatch.setattr(registry, "_best_match", lambda model, field: False if field == "supports_thinking" else None)
    assert [r["effort"] for r in manager.list_choices()] == [""]


def test_saved_loadouts_are_not_rows(monkeypatch):
    # Activating one saves to its provider profile, which every session on it
    # shares; a pick changes one session (Sodalite on #553).
    loadouts = {"luna-max": {"provider_profile": "openai-oauth", "model": "gpt-6-luna", "effort": "max"}}
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6-luna"), loadouts=loadouts)
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: [])
    assert all("loadout" not in r for r in manager.list_choices())


def test_an_acp_harness_offers_what_its_sessions_reported():
    manager = _manager(_Profile("codex-cli", "acp", command=("codex-acp",)), _Profile("claude-code", "acp"))
    reported = {"codex-cli": {"models": ["gpt-6-luna", "gpt-6.1-terra"], "efforts": ["high", "max"]}}
    rows = manager.list_choices(reported)
    assert {"profile": "codex-cli", "provider": "acp", "model": "gpt-6-luna", "effort": "max"} in rows
    assert len([r for r in rows if r["profile"] == "codex-cli"]) == 2 * 2  # every row names its effort
    # A model that has run offers its own levels; one that has not, the last reported.
    reported["codex-cli"]["efforts_by_model"] = {"gpt-6-luna": ["low"]}
    rows = manager.list_choices(reported)
    assert [r["effort"] for r in rows if r["model"] == "gpt-6-luna"] == ["low"]
    assert [r["effort"] for r in rows if r["model"] == "gpt-6.1-terra"] == ["high", "max"]
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
        {"id": "model", "category": "model", "currentValue": "opus",
         "options": [{"value": "opus"}, {"value": "sonnet"}]},
        {"id": "effort", "options": [{"group": "levels", "options": [{"value": "high"}, {"value": "max"}]}]},
    ]
    assert runner.config_options[0]["id"] == "model"
    assert acp_options.load() == {"claude-code": {
        "models": ["opus", "sonnet"], "efforts": ["high", "max"], "efforts_by_model": {"opus": ["high", "max"]},
    }}
    # The next session runs sonnet, which offers other levels: opus keeps its own.
    runner.config_options = [
        {"id": "model", "category": "model", "currentValue": "sonnet",
         "options": [{"value": "opus"}, {"value": "sonnet"}]},
        {"id": "effort", "options": [{"value": "low"}]},
    ]
    saved = json.loads(acp_options._path().read_text())
    assert saved["claude-code"]["efforts_by_model"] == {"opus": ["high", "max"], "sonnet": ["low"]}


def test_harnesses_that_report_at_once_keep_every_entry():
    # Sodalite on #553: an unlocked read-modify-write with one shared scratch
    # file lost entries and left malformed JSON.
    import threading

    options = [{"id": "model", "category": "model", "options": [{"value": "m"}]}]
    threads = [threading.Thread(target=acp_options.remember, args=(f"harness-{i}", options)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(acp_options.load()) == sorted(f"harness-{i}" for i in range(16))
    assert not list(acp_options._path().parent.glob("*.tmp"))
