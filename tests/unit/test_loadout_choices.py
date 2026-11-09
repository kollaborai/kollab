"""The model picker's rows: every provider profile x model x effort (LoadoutManager.list_choices),
and the ACP harness options it offers from kollabor_ai.acp_options."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_each_model_comes_at_its_default_and_every_level_its_catalog_names(monkeypatch):
    # Marco's ask: "codex luna max" is one pick. Sodalite on #553: no level a request
    # would 400 on (gpt-6-luna stops at max). Kunzite on #553 round 4: the blank row
    # is the model's own default, and picking it clears an earlier level.
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
        "gpt-6-luna": ["", "low", "medium", "high", "xhigh", "max"],
        "gpt-6.1-terra": ["", "low", "ultra"],
        # Aragonite on #553 round 5: neither the registry nor its catalog names a level, so none is guessed.
        "gpt-6-sol": [""],
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
    assert levels[("claude", "claude-opus-4-6")] == ["", "low", "medium", "high", "max"]  # no xhigh before 4.7
    assert levels[("claude", "claude-opus-5-5")] == ["", "low", "medium", "high", "xhigh", "max"]
    assert levels[("claude", "claude-haiku-4-5-20251001")] == [""]
    # Kunzite on #553 round 4: some Claude models take no effort (Sonnet 4.5), so
    # one the registry does not list runs at its own default.
    assert levels[("claude", "claude-opus-4-5")] == [""]
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


def test_an_acp_harness_offers_only_efforts_observed_for_each_model(monkeypatch):
    # F4: never reuse one harness model's efforts for another unobserved model.
    # An observed model's effort list says nothing about another model,
    # even when that other model appears in the same harness's model list.
    manager = _manager(_Profile("codex-cli", "acp", command=("codex-acp",)),
                       _Profile("claude-code", "acp", command=("claude-agent-acp",)),
                       _Profile("openai-oauth", "openai_responses", "gpt-6-luna"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: (
        [{"id": "gpt-6-luna", "efforts": ["low", "max"]}] if p.name == "openai-oauth" else []))
    reported = {
        "codex-cli": {"models": ["alpha", "beta", "gamma", "gpt-6-luna"], "efforts": ["low", "max"],
                      "efforts_by_model": {"alpha": ["low", "ultra"], "gamma": []}},
        "claude-code": {"models": ["opus", "claude-opus-4-6"], "efforts": ["default", "low", "max"]},
    }
    levels = _levels(manager.list_choices(reported))
    assert levels[("codex-cli", "alpha")] == ["low", "ultra"]  # what it reported for alpha
    assert levels[("codex-cli", "beta")] == [""]  # unobserved: another model does not establish capability
    assert levels[("codex-cli", "gamma")] == [""]  # ran with no effort option
    assert levels[("codex-cli", "gpt-6-luna")] == [""]  # no effort metadata for this model
    # A harness-level list without model-specific evidence cannot supply levels.
    assert levels[("claude-code", "opus")] == levels[("claude-code", "claude-opus-4-6")] == [""]
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
    assert levels[("openai-oauth", "gpt-6-luna")] == ["", "low", "ultra"]
    assert levels[("router", "gpt-6-luna")] == [""]  # its own catalog has none, and none is guessed
    assert levels[("pi", "gpt-6-luna")] == [""]  # the harness reported no levels for it
    assert levels[("pi", "other")] == ["low"]
    assert levels[("codex-cli", "gpt-6-luna")] == [""]  # never the ChatGPT login's: its login could differ


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
    # F1: unsupported Anthropic model/effort combinations must not reach requests.
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
    # Kunzite on #553 round 4: an unlisted Claude model may take none (Sonnet 4.5).
    assert effort("anthropic", "claude-opus-4-5", "high") == ""
    assert effort("anthropic", "claude-sonnet-4-5-20250929", "high") == ""
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
    assert await levels("claude-opus-4-5") == []


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
    monkeypatch.setattr(LoadoutManager, "provider_profiles", lambda self: [])
    monkeypatch.setattr(profiles, "_get_profile_manager", lambda: SimpleNamespace(config=None))
    monkeypatch.setattr(kollabor_config, "ConfigService", lambda *a, **k: _Config({}))
    assert await profiles.list_choices() == {"choices": [{"profile": "local"}], "discovery": {}}
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
        return SimpleNamespace(model="saved-model", effort="high")  # what the profile says

    monkeypatch.setattr(state, "_profile_to_snapshot", profile_snapshot)
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("alpha", "ultra")
    options.pop()  # a harness with no effort option reports none, not the profile's
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("alpha", "")
    # Iolite on #553 round 3: one that names no model still reports its effort.
    options[:] = [{"id": "thought_level", "category": "thought_level", "currentValue": "ultra"}]
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("", "ultra")  # Kunzite on round 4: never the saved model
    # Kunzite on #553 round 4: an option found by its id, whatever its category.
    options[:] = [{"id": "model", "category": "_custom", "currentValue": "actual-harness-model"}]
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("actual-harness-model", "")
    # F5: id-only effort_level must survive the live snapshot too.
    options.append({"id": "effort_level", "currentValue": "high"})
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("actual-harness-model", "high")
    acp.active = False  # a native turn: the profile's own
    snapshot = await state.get_active_profile()
    assert (snapshot.model, snapshot.effort) == ("saved-model", "high")


def test_the_picker_offers_only_levels_its_request_carries(monkeypatch):
    # F3: an unknown endpoint gets no guessed level; picker and request agree.
    # Aragonite on #553 round 5: a catalog listed gpt-4.1 at high, which its request
    # then dropped, and a custom endpoint's unknown model got guessed levels.
    from kollabor_ai.profile_manager import LLMProfile, accepted_efforts

    for name in ("KOLLAB_MODEL", "KOLLAB_EFFORT", "KOLLAB_P_MODEL", "KOLLAB_P_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-4.1"), _Profile("box", "custom", "my-model"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: [])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: (
        [{"id": "gpt-4.1", "efforts": ["high"]}] if p.name == "openai-oauth" else []))
    levels = _levels(manager.list_choices())
    assert levels[("openai-oauth", "gpt-4.1")] == [""]  # the registry says it does not reason
    assert accepted_efforts("openai_responses", "gpt-4.1") == []
    assert LLMProfile(name="p", provider="openai_responses", model="gpt-4.1", effort="high").get_effort() == ""
    assert levels[("box", "my-model")] == [""]  # nothing names a level for it


@pytest.mark.asyncio
async def test_each_chatgpt_login_lists_its_own_models(monkeypatch):
    # F2: each OAuth profile must fetch the catalog for its own login.
    # Aragonite on #553 round 5: every OAuth profile's catalog was fetched with
    # the default login, so a second account listed the first one's models.
    import kollabor_ai.oauth as oauth
    import kollabor_ai.oauth.openai_oauth as openai_oauth
    from kollabor_ai import model_catalog

    asked = []

    class Storage:
        async def load_tokens(self, provider, auto_refresh=False, profile_name=None):
            asked.append(profile_name)
            return SimpleNamespace(access_token=f"token-{profile_name}", account_id="a")

    async def details(token, account):
        return [{"slug": f"model-for-{token}"}]

    monkeypatch.setattr(oauth, "OAuthTokenStorage", Storage)
    monkeypatch.setattr(openai_oauth, "query_codex_model_details", details)
    first = await model_catalog._openai_oauth_models(SimpleNamespace(name="openai-oauth"))
    second = await model_catalog._openai_oauth_models(SimpleNamespace(name="work-oauth"))
    assert asked == [None, "work-oauth"]  # the default login, then that profile's own
    assert [m["id"] for m in first] == ["model-for-token-None"]
    assert [m["id"] for m in second] == ["model-for-token-work-oauth"]


@pytest.mark.asyncio
async def test_switching_to_a_harness_answers_with_its_own_model_and_effort(monkeypatch):
    # F5: activation must return the live harness snapshot.
    # Aragonite on #553 round 5: the switch answered with the profile's saved model
    # and no effort, and the session mirrored that until the next read.
    from kollabor.state.local import LocalStateService

    options = [{"id": "model", "category": "model", "currentValue": "live-model"},
               {"id": "thought_level", "category": "thought_level", "currentValue": "max"}]
    harness = SimpleNamespace(active=True, advertised_config_options=lambda: options,
                              close=AsyncMock(), start=AsyncMock())
    profile = SimpleNamespace(name="codex-cli", get_provider=lambda: "acp")
    state = LocalStateService.__new__(LocalStateService)
    state._event_bus = SimpleNamespace(get_service=lambda name: harness if name == "acp_orchestrator" else None)
    state._llm_service = None
    state._profile_activation_generation = 0
    state._profile_manager = SimpleNamespace(
        get_profile=lambda name: profile, get_active_profile=lambda: profile, set_active_profile=lambda name: True)

    class Snapshot(SimpleNamespace):
        def to_dict(self):
            return dict(vars(self))

    monkeypatch.setattr(state, "_profile_to_snapshot",
                        lambda p, is_active=False: Snapshot(model="saved-model", effort=""))
    snapshot = await state.set_active_profile("codex-cli")
    assert (snapshot.model, snapshot.effort) == ("live-model", "max")
    harness.start.assert_awaited_once_with(profile)


def test_a_catalog_that_lists_no_levels_means_none_and_no_row_offers_one_a_request_drops(monkeypatch):
    # Kunzite on #553 round 4: an empty catalog list read as "unknown" and got
    # low/medium/high; the request path now carries the advertised none/minimal levels.
    manager = _manager(_Profile("openai-oauth", "openai_responses", "gpt-5.1"))
    monkeypatch.setattr(manager, "models_for_profile", lambda p: ["gpt-5.2"])
    monkeypatch.setattr(manager, "_cached_catalog", lambda p, **_: [
        {"id": "gpt-5.1", "efforts": []}, {"id": "gpt-5.2", "efforts": ["none", "minimal", "high"]}])
    levels = _levels(manager.list_choices())
    assert levels[("openai-oauth", "gpt-5.1")] == [""]
    assert levels[("openai-oauth", "gpt-5.2")] == ["", "none", "minimal", "high"]


@pytest.mark.asyncio
async def test_a_default_pick_reaches_the_daemon_as_a_reset():
    # F6: native default resets the prior effort; frontend model-choice tests
    # separately verify ACP rows do not claim an unsettable default.
    # Kunzite on #553 round 4: the client dropped an empty effort, and so did the
    # daemon's handler, so a pick of the default kept the earlier level.
    from kollabor.state.handlers import register_state_handlers
    from kollabor.state.remote import RemoteStateService

    sent = []

    async def call(method, payload, timeout=None):
        sent.append(payload)
        return {"name": payload["name"]}

    remote = RemoteStateService.__new__(RemoteStateService)
    remote._rpc, remote._timeout = SimpleNamespace(call=call), 1.0
    await remote.set_active_profile("claude", effort="")
    await remote.set_active_profile("claude")
    assert [p.get("effort", "omitted") for p in sent] == ["", "omitted"]

    handlers, got = {}, []

    async def set_active_profile(name, **kwargs):
        got.append(kwargs["effort"])
        return SimpleNamespace(to_dict=lambda: {"name": name})

    register_state_handlers(SimpleNamespace(register=lambda method, handler: handlers.setdefault(method, handler)),
                            SimpleNamespace(set_active_profile=set_active_profile))
    await handlers["state.set_active_profile"]({"name": "claude", "effort": ""})
    await handlers["state.set_active_profile"]({"name": "claude"})
    assert got == ["", None]


@pytest.mark.asyncio
async def test_a_list_fetched_as_one_login_never_reaches_the_next(tmp_path, monkeypatch):
    # Ametrine on #553 round 10: account A's fetch had answered, B signed in
    # while another provider's fetch still ran, and the list the manager kept
    # for the listing showed A's models as B's. The manager keeps none now.
    from kollabor_ai import model_catalog
    from kollabor_ai.oauth import token_storage

    (tmp_path / "oauth").mkdir()
    login = tmp_path / "oauth" / "openai.json"
    login.write_text(json.dumps({"account_id": "acct-a"}), encoding="utf-8")
    monkeypatch.setattr(token_storage, "get_config_directory_candidates", lambda: [tmp_path])
    monkeypatch.setattr(model_catalog, "_cache_file", lambda: tmp_path / "model_catalog.cache")
    monkeypatch.setattr(model_catalog, "_MEMORY_CACHE", {})
    manager = _manager(
        _Profile("openai-oauth", "openai_responses", "gpt-6-luna"), _Profile("router", "openrouter", "some/model")
    )

    async def fetch(profile, **_):
        if profile.name == "router":  # B signs in while this one runs
            login.write_text(json.dumps({"account_id": "acct-b"}), encoding="utf-8")
            return []
        model_catalog._store(profile, [{"id": "a-only"}], "acct-a")
        return [{"id": "a-only"}]

    monkeypatch.setattr(model_catalog, "get_provider_models", fetch)
    await manager.refresh_catalogs()
    assert "a-only" not in {row["model"] for row in manager.list_choices()}
    login.write_text(json.dumps({"account_id": "acct-a"}), encoding="utf-8")  # A signs back in
    assert "a-only" in {row["model"] for row in manager.list_choices()}
