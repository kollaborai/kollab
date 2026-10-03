"""Panels /llm, /model and /setup against a fake daemon context."""

import json
from types import SimpleNamespace

import pytest

from kollabor.panels import PanelError, get_panel
from kollabor.panels import llm as llm_panel
from kollabor.panels import model as model_panel
from kollabor.panels import setup as setup_panel
from kollabor_ai.loadout_manager import Loadout

KEY = "sk-ant-api03-SECRETSECRETSECRETSECRET"


# -- /llm ---------------------------------------------------------------------


class FakeManager:
    def __init__(self):
        self.calls = []
        self.items = [
            Loadout("mine", "anthropic", "claude-x", temperature=0.2, effort="high"),
            Loadout("claude-x", "anthropic", "claude-x", implicit=True),
            Loadout("gpt-y", "openai", "gpt-y", implicit=True),
        ]
        self.profiles = [
            SimpleNamespace(name=n, get_provider=lambda n=n: n)
            for n in ("anthropic", "openai", "openrouter")
        ]

    def list_loadouts(self):
        return list(self.items)

    def provider_profiles(self):
        return self.profiles

    def get(self, name):
        return next((x for x in self.items if x.name == name), None)

    def resolve(self, query):
        return self.get(query), []

    def get_default(self):
        return "mine"

    def set_default(self, name, level="global"):
        self.calls.append(("set_default", name, level))
        return True

    def create(self, name, **kw):
        self.calls.append(("create", name, kw))
        return True

    def update(self, name, **kw):
        self.calls.append(("update", name, kw))
        return True

    def delete(self, name):
        self.calls.append(("delete", name))
        return True

    async def activate(self, loadout, bus):
        self.calls.append(("activate", loadout.name, bus))
        return loadout

    async def refresh_catalogs(self):
        self.calls.append(("refresh",))
        return {}


@pytest.fixture
def llm_ctx(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(llm_panel, "_manager", lambda ctx: manager)
    pm = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(name="anthropic", get_model=lambda: "claude-x"),
        get_profile=lambda name: SimpleNamespace(temperature=None),
    )
    return SimpleNamespace(_profile_manager=pm, _llm_service=None, _event_bus="bus", manager=manager)


LLM = llm_panel.PANELS["llm"]


@pytest.mark.asyncio
async def test_llm_describe_groups_current_default_and_empty_providers(llm_ctx):
    panel = await LLM.describe(llm_ctx, {})
    json.dumps(panel)
    rows = {r["id"]: r for r in panel["rows"]}
    assert rows["mine"]["group"] == "Loadouts" and rows["mine"]["badges"] == ["default"]
    assert rows["claude-x"]["group"] == "Models — Anthropic" and rows["claude-x"]["current"]
    assert not rows["gpt-y"]["current"]
    assert [g["group"] for g in panel["empty_groups"]] == ["Models — OpenRouter"]
    assert {a["id"] for a in panel["row_actions"]} == {"activate", "edit", "set_default", "delete"}


@pytest.mark.asyncio
async def test_llm_activate_delete_default_refresh_map_to_manager(llm_ctx):
    result = await LLM.act(llm_ctx, "activate", {"name": "gpt-y"})
    assert result["ok"] and result["panel"]["panel"] == "llm"
    assert ("activate", "gpt-y", "bus") in llm_ctx.manager.calls
    with pytest.raises(PanelError) as unknown:
        await LLM.act(llm_ctx, "activate", {"name": "nope"})
    assert unknown.value.status == 404
    assert (await LLM.act(llm_ctx, "delete", {"name": "mine"}))["ok"]
    refused = await LLM.act(llm_ctx, "delete", {"name": "gpt-y"})
    assert not refused["ok"] and ("delete", "gpt-y") not in llm_ctx.manager.calls
    with pytest.raises(PanelError):
        await LLM.act(llm_ctx, "set_default", {"name": "mine", "level": "everywhere"})
    await LLM.act(llm_ctx, "set_default", {"name": "mine", "level": "project"})
    await LLM.act(llm_ctx, "refresh", {})
    assert ("set_default", "mine", "project") in llm_ctx.manager.calls
    assert ("refresh",) in llm_ctx.manager.calls


@pytest.mark.asyncio
async def test_llm_edit_opens_form_and_save_validates_then_creates(llm_ctx):
    implicit = (await LLM.act(llm_ctx, "edit", {"name": "claude-x"}))["open"]
    assert implicit["kind"] == "form"
    assert (implicit["context"]["mode"], implicit["context"]["base"]) == ("create", "claude-x")
    name_field = implicit["sections"][0]["fields"][0]
    assert name_field["value"] == "claude-x-custom" and name_field["editable"]
    explicit = (await LLM.act(llm_ctx, "edit", {"name": "mine"}))["open"]
    assert explicit["context"]["mode"] == "edit"
    assert not explicit["sections"][0]["fields"][0]["editable"]

    good = {"context": implicit["context"], "name": "mine2", "temperature": 0.5, "effort": "high",
            "max_tokens": "", "description": " d "}
    assert (await LLM.act(llm_ctx, "save", good))["ok"]
    kind, name, kw = [c for c in llm_ctx.manager.calls if c[0] == "create"][0]
    assert (name, kw["provider_profile"], kw["model"], kw["effort"], kw["description"]) == (
        "mine2", "anthropic", "claude-x", "high", "d")
    for bad, field in (({"temperature": 9}, "temperature"), ({"effort": "huge"}, "effort"),
                       ({"max_tokens": "x"}, "max_tokens"), ({"name": "mine"}, "name")):
        with pytest.raises(PanelError) as err:
            await LLM.act(llm_ctx, "save", {**good, **bad})
        assert field in err.value.errors


@pytest.mark.asyncio
async def test_llm_new_from_the_active_model_saves_without_a_base_name(llm_ctx):
    form = (await LLM.act(llm_ctx, "new", {}))["open"]
    assert form["context"]["base"] == "" and form["context"]["model"] == "claude-x"
    payload = {"context": form["context"], "name": "fresh", "temperature": 0.7, "effort": "default"}
    assert (await LLM.act(llm_ctx, "save", payload))["ok"]
    _, name, kw = [c for c in llm_ctx.manager.calls if c[0] == "create"][-1]
    assert (name, kw["provider_profile"], kw["model"]) == ("fresh", "anthropic", "claude-x")
    forged = {**payload, "context": {**form["context"], "model": "x" * 300}}
    with pytest.raises(PanelError):
        await LLM.act(llm_ctx, "save", forged)


# -- /model -------------------------------------------------------------------


class FakeHandler:
    def __init__(self):
        self.calls = []

    def _build_known_models(self, provider, current):
        return [{"id": current, "note": "current"}, {"id": "m-b", "note": "via other"}]

    async def _model_effort_levels(self, profile):
        return ["low", "high"]

    async def _set_active_profile_model(self, model):
        self.calls.append(("select", model))
        return SimpleNamespace(success=model != "bad", message=f"Updated\n  Model: {model}")

    async def _handle_effort(self, level):
        self.calls.append(("effort", level))
        return SimpleNamespace(success=True, message="ok")


@pytest.fixture
def model_ctx(monkeypatch):
    handler = FakeHandler()
    monkeypatch.setattr(model_panel, "model_handler", lambda ctx: handler)

    async def catalog(profile, timeout=None):
        return [{"id": "m-c", "note": "live"}, {"id": "m-a", "note": "dup"}]

    monkeypatch.setattr(model_panel, "fetch_catalog_models", catalog)
    profile = SimpleNamespace(
        name="anthropic", get_provider=lambda: "anthropic", get_model=lambda: "m-a",
        get_effort=lambda: "high",
    )
    pm = SimpleNamespace(get_active_profile=lambda: profile)
    return SimpleNamespace(_profile_manager=pm, _llm_service=None, _event_bus=None, handler=handler)


MODEL = model_panel.PANELS["model"]


@pytest.mark.asyncio
async def test_model_describe_merges_catalog_and_has_effort_control(model_ctx):
    panel = await MODEL.describe(model_ctx, {})
    assert [(r["id"], r["current"]) for r in panel["rows"]] == [
        ("m-a", True), ("m-b", False), ("m-c", False)]
    effort = panel["controls"][0]
    assert (effort["path"], effort["value"], effort["options"]) == (
        "effort", "high", ["default", "low", "high"])


@pytest.mark.asyncio
async def test_model_select_and_effort_call_the_terminal_functions(model_ctx):
    result = await MODEL.act(model_ctx, "select", {"model": "m-b"})
    assert result["ok"] and result["panel"]["panel"] == "model"
    await MODEL.act(model_ctx, "effort", {"level": "low"})
    await MODEL.act(model_ctx, "effort", {"value": "default"})
    assert model_ctx.handler.calls == [("select", "m-b"), ("effort", "low"), ("effort", "default")]
    failed = await MODEL.act(model_ctx, "select", {"model": "bad"})
    assert not failed["ok"] and "\n" not in failed["message"]
    for payload in ({}, {"model": "two words"}, {"model": "x" * 300}):
        with pytest.raises(PanelError):
            await MODEL.act(model_ctx, "select", payload)
    with pytest.raises(PanelError):
        await MODEL.act(model_ctx, "effort", {})
    with pytest.raises(PanelError) as unknown:
        await MODEL.act(model_ctx, "switch", {})
    assert unknown.value.status == 404


def test_model_handler_is_the_registered_one_or_an_equal_fresh_one():
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    live = ModelCommandHandler(None, None, None, None)
    registry = SimpleNamespace(get_command=lambda name: SimpleNamespace(handler=live.handle_model))
    bus = SimpleNamespace(get_service=lambda name: registry)
    ctx = SimpleNamespace(_event_bus=bus, _profile_manager="pm", _llm_service="llm")
    assert model_panel.model_handler(ctx) is live
    ctx._event_bus = None
    fresh = model_panel.model_handler(ctx)
    assert isinstance(fresh, ModelCommandHandler) and fresh.profile_manager == "pm"


def test_dedup_models_keeps_first_and_marks_current():
    out = model_panel.dedup_models([{"id": "a", "note": "n"}, {"id": "a"}, {"id": " "}, {"id": "b"}], "b")
    assert out == [{"id": "a", "note": "n", "current": False}, {"id": "b", "note": "", "current": True}]


# -- /setup -------------------------------------------------------------------


class FakeProfiles:
    def __init__(self, existing=()):
        self._profiles = {n: object() for n in existing}
        self.created = []
        self.fail = None

    def get_profile(self, name):
        return self._profiles.get(name)

    def create_profile(self, **kw):
        if self.fail:
            raise RuntimeError(self.fail)
        self.created.append(kw)
        return True

    def set_active_profile(self, name, persist=True):
        self.active = name
        return True


class FakeDaemon:
    def __init__(self, profiles):
        self._profile_manager = profiles
        self._llm_service = None
        self.activated = []

    async def set_active_profile(self, name, **kw):
        self.activated.append((name, kw))


SETUP = setup_panel.PANELS["setup"]
PAYLOAD = {"provider": "Anthropic (Claude)", "api_key": KEY, "base_url": "", "model": ""}


@pytest.mark.asyncio
async def test_setup_describe_never_carries_a_key_and_hides_oauth_choice():
    panel = await SETUP.describe(FakeDaemon(FakeProfiles()), {})
    options = panel["steps"][0]["fields"][0]["options"]
    assert "Anthropic (Claude)" in options and not any("ChatGPT" in o for o in options)
    key = panel["steps"][1]["fields"][0]
    assert key["secret"] and key["value"] is None and key["is_set"] is False
    assert KEY not in json.dumps(panel)
    assert [a["id"] for a in panel["actions"]] == ["test", "finish"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,field",
    [
        ({**PAYLOAD, "api_key": ""}, "api_key"),
        ({**PAYLOAD, "provider": "OpenAI (sign in with ChatGPT)"}, "provider"),
        ({**PAYLOAD, "provider": "nope"}, "provider"),
        ({**PAYLOAD, "base_url": "http://evil.example.com/?localhost"}, "base_url"),
        ({**PAYLOAD, "model": "bad\x00model"}, "model"),
        ({**PAYLOAD, "provider": "local", "api_key": "", "base_url": "http://example.com/v1"}, "base_url"),
        ({**PAYLOAD, "provider": "openrouter", "model": ""}, "model"),
    ],
)
async def test_setup_validation_errors_are_per_field(payload, field):
    for action in ("test", "finish"):
        with pytest.raises(PanelError) as err:
            await SETUP.act(FakeDaemon(FakeProfiles()), action, payload)
        assert field in err.value.errors and KEY not in str(err.value.errors)


@pytest.mark.asyncio
async def test_setup_test_outcome_is_ok_false_without_errors(monkeypatch):
    seen = []

    async def fake(provider, model, api_key, base_url, timeout=25.0):
        seen.append((provider, model, base_url))
        return False, "401 unauthorized"

    monkeypatch.setattr(setup_panel, "test_connection", fake)
    result = await SETUP.act(FakeDaemon(FakeProfiles()), "test", PAYLOAD)
    assert result["ok"] is False and result["errors"] == {} and result["message"] == "401 unauthorized"
    assert seen[0][0] == "anthropic" and seen[0][1] and seen[0][2].startswith("https://")


@pytest.mark.asyncio
async def test_setup_finish_creates_profile_and_activates_through_the_daemon():
    profiles = FakeProfiles(existing=["anthropic"])
    daemon = FakeDaemon(profiles)
    result = await SETUP.act(daemon, "finish", PAYLOAD)
    assert result["ok"] and "anthropic-2" in result["message"]
    created = profiles.created[0]
    assert (created["name"], created["provider"], created["save_to_config"]) == (
        "anthropic-2", "anthropic", True)
    assert daemon.activated == [("anthropic-2", {"persist": True, "reload_profile": True})]
    assert KEY not in json.dumps(result)


@pytest.mark.asyncio
async def test_setup_errors_never_echo_the_key(monkeypatch):
    profiles = FakeProfiles()
    profiles.fail = f"rejected key {KEY}"
    result = await SETUP.act(FakeDaemon(profiles), "finish", PAYLOAD)
    assert not result["ok"] and KEY not in json.dumps(result) and "REDACTED" in result["message"]

    class Boom:
        @staticmethod
        async def create_provider(config):
            raise RuntimeError(f"401 for {KEY} (Gemini AIzaSyDUMMYKEYDUMMYKEY12345)")

    import kollabor_ai.providers.registry as registry

    monkeypatch.setattr(registry, "ProviderRegistry", Boom)
    ok, message = await setup_panel.test_connection("anthropic", "m", KEY, "https://api.anthropic.com")
    assert not ok and KEY not in message and "REDACTED" in message


def test_valid_base_url_and_panel_registry():
    ok = ["https://api.openai.com/v1", "http://localhost:1234/v1", "http://127.0.0.1:8080"]
    bad = ["http://example.com", "ftp://x", "http://evil.com/?localhost", "", "https://"]
    assert all(setup_panel.valid_base_url(u) for u in ok)
    assert not any(setup_panel.valid_base_url(u) for u in bad)
    assert get_panel("llm") is LLM and get_panel("model") is MODEL and get_panel("setup") is SETUP
