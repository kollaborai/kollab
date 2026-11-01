"""The model picker's rows: every provider profile x model x effort (LoadoutManager.list_choices),
and the ACP harness options it offers from kollabor_ai.acp_options."""

import json
from types import SimpleNamespace

import pytest

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


def _levels(rows):
    found = {}
    for row in rows:
        found.setdefault((row["profile"], row["model"]), []).append(row["effort"])
    return found


def test_a_model_without_catalog_levels_offers_the_registrys(monkeypatch):
    # Sodalite on #553 round 2: Claude Opus 4.5 takes no max, and a model can
    # think yet take no effort at all (Claude Haiku 4.5). The registry says which.
    manager = _manager(_Profile("claude", "anthropic", "claude-opus-4-6"), _Profile("box", "ollama", "llama"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: (
        ["claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-opus-4-5"] if p.name == "claude" else []))
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: [])
    levels = _levels(manager.list_choices())
    assert levels[("claude", "claude-opus-4-6")] == ["low", "medium", "high", "max"]  # no xhigh before 4.7
    assert levels[("claude", "claude-opus-5-5")] == ["low", "medium", "high", "xhigh", "max"]
    assert levels[("claude", "claude-haiku-4-5-20251001")] == [""]
    assert levels[("claude", "claude-opus-4-5")] == ["low", "medium", "high"]  # unlisted: what every effort API takes
    assert levels[("box", "llama")] == [""]  # its provider sends no effort


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


def test_an_acp_harness_offers_each_model_only_its_own_levels(monkeypatch):
    # Sodalite on #553 round 2: alpha's levels say nothing about beta, which takes
    # only low; picking beta at ultra switched the harness to beta, then failed.
    manager = _manager(_Profile("codex-cli", "acp", command=("codex-acp",)),
                       _Profile("claude-code", "acp", command=("claude-agent-acp",)),
                       _Profile("openai-oauth", "openai_responses", "gpt-6-luna"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    # Codex CLI serves the ChatGPT login's models: their catalog levels hold there too.
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: (
        [{"id": "gpt-6-luna", "efforts": ["low", "max"]}] if p.name == "openai-oauth" else []))
    reported = {
        "codex-cli": {"models": ["alpha", "beta", "gamma", "gpt-6-luna"], "efforts": ["low", "ultra"],
                      "efforts_by_model": {"alpha": ["low", "ultra"], "gamma": []}},
        "claude-code": {"models": ["opus", "claude-opus-4-6"], "efforts": []},
    }
    levels = _levels(manager.list_choices(reported))
    assert levels[("codex-cli", "alpha")] == ["low", "ultra"]
    assert levels[("codex-cli", "beta")] == [""]  # never run: the harness keeps its own effort
    assert levels[("codex-cli", "gamma")] == [""]  # ran with no effort option
    assert levels[("codex-cli", "gpt-6-luna")] == ["low", "max"]  # "codex luna max"
    assert levels[("claude-code", "claude-opus-4-6")] == ["low", "medium", "high", "max"]  # "claude code opus max"
    assert levels[("claude-code", "opus")] == [""]  # an alias: nothing says which model it is
    # Never started: only its own default, until a session reports its options.
    fresh = _manager(_Profile("pi", "acp"))
    monkeypatch.setattr(fresh, "_cached_catalog", lambda p, **_: [])
    assert fresh.list_choices({}) == [{"profile": "pi", "provider": "acp", "model": "", "effort": ""}]


def test_one_catalog_says_nothing_about_another_endpoint_or_harness(monkeypatch):
    # Iolite on #553 round 3: a second endpoint serving the same model id took the
    # ChatGPT login's levels, and so did an unrelated harness, which refused them.
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-6-luna"),
                       _Profile("router", "openrouter", "gpt-6-luna"),
                       _Profile("pi", "acp", command=("/opt/bin/pi-acp",)),
                       _Profile("codex-cli", "acp", command=("/opt/adapters/codex-acp",)))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: (
        [{"id": "gpt-6-luna", "efforts": ["low", "ultra"]}] if p.name == "openai-oauth" else []))
    reported = {"pi": {"models": ["gpt-6-luna", "other"], "efforts_by_model": {"other": ["low"]}},
                "codex-cli": {"models": ["gpt-6-luna"], "efforts_by_model": {}}}
    levels = _levels(manager.list_choices(reported))
    assert levels[("openai-oauth", "gpt-6-luna")] == ["low", "ultra"]
    assert levels[("router", "gpt-6-luna")] == ["low", "medium", "high"]  # its own catalog has none
    assert levels[("pi", "gpt-6-luna")] == [""]  # never run there, and its backend is unknown
    assert levels[("pi", "other")] == ["low"]
    assert levels[("codex-cli", "gpt-6-luna")] == ["low", "ultra"]  # the adapter, wherever it is installed


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
    # A model with no effort option is recorded as taking none, so it never borrows another's.
    runner.config_options = [
        {"id": "model", "category": "model", "currentValue": "haiku",
         "options": [{"value": "opus"}, {"value": "haiku"}]},
    ]
    assert acp_options.load()["claude-code"]["efforts_by_model"]["haiku"] == []


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


def test_a_level_the_model_does_not_take_is_never_sent(monkeypatch):
    # Sodalite on #553 round 2: a blank row on llama-4-scout kept the earlier
    # pick's effort, and the request still carried reasoning_effort=high.
    from kollabor_ai.profile_manager import LLMProfile

    for name in ("KOLLAB_MODEL", "KOLLAB_EFFORT", "KOLLAB_P_MODEL", "KOLLAB_P_EFFORT"):
        monkeypatch.delenv(name, raising=False)

    def effort(provider, model, level):
        return LLMProfile(name="p", provider=provider, model=model, effort=level).get_effort()

    assert effort("custom", "llama-4-scout", "high") == ""  # does not reason
    assert effort("anthropic", "claude-haiku-4-5", "high") == ""  # reasons, takes no effort
    assert effort("anthropic", "claude-opus-4-6", "xhigh") == ""  # no xhigh before Opus 4.7
    assert effort("anthropic", "claude-opus-4-6", "max") == "max"
    assert effort("openai_responses", "gpt-6-luna", "ultra") == "ultra"  # the registry does not say: as set
    # Iolite on #553 round 3: an unlisted Claude model got max (a 400 on Opus 4.5),
    # and a provider that sends no effort reported the saved one.
    assert effort("anthropic", "claude-opus-4-5", "max") == ""
    assert effort("anthropic", "claude-opus-4-5", "high") == "high"
    assert effort("gemini", "gemini-2.5-pro", "high") == ""
    assert effort("acp", "", "high") == ""  # a harness keeps its own


@pytest.mark.asyncio
async def test_the_model_tab_offers_the_levels_the_picker_does():
    # The Model tab and /model effort listed all six levels for any non-Codex model.
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    handler = ModelCommandHandler.__new__(ModelCommandHandler)

    async def levels(model):
        return await handler._model_effort_levels(_Profile("p", "anthropic", model))

    assert await levels("claude-opus-4-6") == ["low", "medium", "high", "max"]
    assert await levels("claude-haiku-4-5") == []
    assert await levels("claude-opus-4-5") == ["low", "medium", "high"]


@pytest.mark.asyncio
async def test_the_picker_fetches_a_cold_catalog(monkeypatch):
    # Sodalite on #553 round 2: with a cold cache a local endpoint listed only its
    # configured model until the Loadouts tab fetched the catalog.
    from kollabor_engine.routes import profiles

    import kollabor_config

    calls = []

    async def refresh(self):
        calls.append("refresh")
        raise RuntimeError("endpoint down")  # what is cached still lists

    def list_choices(self, options):
        calls.append("list")
        return [{"profile": "local"}]

    monkeypatch.setattr(LoadoutManager, "refresh_catalogs", refresh)
    monkeypatch.setattr(LoadoutManager, "list_choices", list_choices)
    monkeypatch.setattr(profiles, "_get_profile_manager", lambda: SimpleNamespace(config=None))
    monkeypatch.setattr(kollabor_config, "ConfigService", lambda *a, **k: _Config({}))
    assert await profiles.list_choices() == {"choices": [{"profile": "local"}]}
    assert calls == ["refresh", "list"]


@pytest.mark.asyncio
async def test_an_acp_session_reports_the_harness_effort(monkeypatch):
    # Sodalite on #553 round 2: the snapshot read effort from the profile, so the
    # web UI's 3 s poll put the old effort back after a harness pick.
    from kollabor.state.local import LocalStateService

    options = [
        {"id": "model", "category": "model", "currentValue": "alpha"},
        {"id": "thought_level", "category": "thought_level", "currentValue": "ultra"},
    ]
    acp = SimpleNamespace(active=True, advertised_config_options=lambda: options)
    state = LocalStateService.__new__(LocalStateService)
    state._event_bus = SimpleNamespace(get_service=lambda name: acp if name == "acp_orchestrator" else None)
    state._profile_manager = SimpleNamespace(get_active_profile=lambda: SimpleNamespace(name="codex-cli"))
    def profile_snapshot(profile, is_active=False):
        return SimpleNamespace(model="", effort="high")  # what the profile says

    monkeypatch.setattr(state, "_profile_to_snapshot", profile_snapshot)
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("alpha", "ultra")
    options.pop()  # a harness with no effort option reports none, not the profile's
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("alpha", "")
    # Iolite on #553 round 3: one that names no model still reports its effort.
    options[:] = [{"id": "thought_level", "category": "thought_level", "currentValue": "ultra"}]
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("", "ultra")
    acp.active = False  # a native turn: the profile's own
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("", "high")
