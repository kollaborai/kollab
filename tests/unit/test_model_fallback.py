"""Model fallback: a turn the model can't serve moves the agent to its fallback once."""

import asyncio
from types import SimpleNamespace

import pytest

import kollabor_ai.loadout_manager as loadout_module
from kollabor.llm.usage_limits import failure_kind
from kollabor_events.models import EventType
from kollabor_tui.display_tap import DisplayTap
from plugins.model_fallback_plugin import ModelFallbackPlugin


@pytest.mark.parametrize(
    "message, kind",
    [
        ("Usage limit reached for 5 hour. Your limit will reset at 14:00", "usage"),
        ("Insufficient balance or no resource package. Please recharge.", "usage"),
        ("claude-code: Claude AI usage limit reached", "usage"),
        ("codex-cli: You've hit your usage limit.", "usage"),
        ("You exceeded your current quota, please check your plan", "usage"),
        ("Rate limit exceeded. Please try again later.", "rate"),
        ("claude-code: Authentication required. Log in using this harness's CLI.", "login"),
        ("Invalid openai API key. Please check your API key.", "login"),
        ("prompt is too long: 250000 tokens > 200000 maximum", None),
        ("Request exceeds the model's context window", None),
        ("Connection reset by peer", None),
        ("ValueError at line 429", None),
        ("", None),
    ],
)
def test_failure_kind(message, kind):
    assert failure_kind(message) == kind


def test_a_native_errors_type_and_code_decide_where_its_text_cannot():
    assert failure_kind("Request failed", error_type="AuthenticationError") == "login"
    assert failure_kind("Request failed", error_code="insufficient_quota") == "usage"
    assert failure_kind("prompt is too long", error_type="AuthenticationError") is None
    assert failure_kind("Connection reset by peer", error_type="APIConnectionError") is None


class _Profile:
    def __init__(self, name, model, provider="openai"):
        self.name, self.model, self.provider = name, model, provider

    def get_model(self):
        return self.model

    def get_provider(self):
        return self.provider


class _Profiles:
    def __init__(self, *profiles, active):
        self.profiles = {p.name: p for p in profiles}
        self.active = active

    def get_active_profile(self):
        return self.profiles[self.active]

    def get_profile(self, name):
        return self.profiles.get(name)


class _Loadouts:
    """Stands in for LoadoutManager. Like it, it knows loadout names
    ("<profile>-loadout"), never bare profile names."""

    activated = []

    def __init__(self, profile_manager, config=None):
        self.pm = profile_manager

    def resolve(self, query):
        profile = self.pm.get_profile(query.removesuffix("-loadout"))
        if profile is None or not query.endswith("-loadout"):
            return None, []
        return SimpleNamespace(name=query, provider_profile=profile.name, model=profile.model), []

    async def activate(self, loadout, event_bus=None):
        _Loadouts.activated.append(loadout.name)
        self.pm.active = loadout.provider_profile
        return loadout


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, path, default=None):
        return self.values.get(path, default)

    def save_key(self, path, value, save_target=None):
        self.values[path] = value
        return True


class _Bus:
    def __init__(self, services):
        self.services, self.emitted, self.hooks = services, [], []

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service

    async def register_hook(self, hook):
        self.hooks.append(hook)

    async def fire(self, event_type):
        for hook in self.hooks:
            if hook.event_type == event_type:
                await hook.callback({}, None)

    async def emit_with_hooks(self, event_type, data, source):
        self.emitted.append((event_type, data))


async def _agent(monkeypatch, config, fallback_provider="openai", tap_at_init=True):
    monkeypatch.setattr(loadout_module, "LoadoutManager", _Loadouts)
    _Loadouts.activated = []
    tap = DisplayTap()
    profiles = _Profiles(
        _Profile("glm", "glm-5.3-flash"),
        _Profile("codex", "gpt-6.1-terra", provider=fallback_provider),
        active="glm",
    )
    bus = _Bus({
        "profile_manager": profiles,
        "hub_plugin": SimpleNamespace(_identity=SimpleNamespace(identity="lapis")),
    })
    if tap_at_init:
        bus.register_service("display_tap", tap)
    plugin = ModelFallbackPlugin("model_fallback", bus, None, _Config(config))
    await plugin.initialize()
    await plugin.register_hooks()
    bus.register_service("display_tap", tap)  # the hub starts after plugin files
    return plugin, bus, tap, profiles


async def _native_turn_fails(plugin, message, **fields):
    """What the queue processor's LLM_REQUEST_ERROR hook gets; True if the turn asks again."""
    data = {"message": message, "error_type": "ProviderError", "error_code": None,
            "context_window_exceeded": False, **fields}
    await plugin._on_request_error(data, None)
    return data.get("retry") is True


async def _settle(plugin):
    for _ in range(5):
        await asyncio.sleep(0)
    if plugin._task is not None:
        await plugin._task


async def _acp_turn_fails(plugin, tap, message):
    tap.publish({"type": "error", "message": message, "generation": 1})
    tap.publish({"type": "turn_complete"})
    await _settle(plugin)


def _continues(bus):
    return [e for e, _ in bus.emitted if e == EventType.TRIGGER_LLM_CONTINUE]


@pytest.mark.asyncio
async def test_a_usage_limit_switches_once_and_runs_the_turn_again(monkeypatch):
    plugin, bus, tap, profiles = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})

    # Inside the failed turn, so its request's stream gets the new model's answer.
    assert await _native_turn_fails(plugin, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex"] and profiles.active == "codex"
    assert _continues(bus) == []  # no second turn: this one asks again
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"] == (
        "glm-5.3-flash hit its usage limit. Switched to codex and resent your message."
    )

    # The fallback fails too: no second hop, no loop.
    assert not await _native_turn_fails(plugin, "You've hit your usage limit.")
    assert _Loadouts.activated == ["codex"]


@pytest.mark.asyncio
async def test_a_reply_streamed_in_part_is_switched_but_never_asked_again(monkeypatch):
    # Morganite on #549 round 2: the retry's answer joined the failed model's
    # streamed part as one reply.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    assert not await _native_turn_fails(plugin, "Usage limit reached for 5 hour.", streamed=True)
    assert _Loadouts.activated == ["codex"]
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"] == (
        "glm-5.3-flash hit its usage limit. Switched to codex. Send your message again."
    )


class _Harness:
    """The ACP orchestrator as a switch sees it: its fence on new prompts, over
    a runner busy while a prompt runs or waits."""

    def __init__(self, *prompts, kept=0, bus=None):
        self.closing_reason, self.prompts, self.runner = "", list(prompts), self
        self.held, self.released, self.active, self.kept, self.handed = False, [], True, kept, 0
        self.bus = bus

    @property
    def busy(self):
        return bool(self.prompts)

    def hold_observations(self):
        self.held = True

    def release_observations(self, flush=True):
        self.held = False
        self.released.append((self.closing_reason, flush))  # the fence as the release saw it

    def hand_over_observations(self):
        self.held, self.handed = False, self.handed + 1
        return self.kept

    async def hand_over_to_native(self):
        self.held, self.handed = False, self.handed + 1
        if self.kept > 0:
            # The real orchestrator starts the answering turn itself (#560).
            if self.bus is not None:
                await self.bus.emit_with_hooks(
                    EventType.TRIGGER_LLM_CONTINUE,
                    {"source": "acp_handover", "content": ""},
                    "acp_orchestrator",
                )
            return True
        return False

    def admit(self, text):
        if self.closing_reason:
            raise RuntimeError(self.closing_reason)
        self.prompts.append(text)


@pytest.mark.asyncio
async def test_a_switch_the_harness_was_busy_for_runs_once_its_last_prompt_ends(monkeypatch):
    # Morganite on #549 round 2: switching closed the harness, so a prompt it
    # had already taken failed with "ACP runner closed". Labradorite on round
    # 3: the failure was then dropped, and once that prompt succeeded nothing
    # switched.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    harness = _Harness("running", "waiting")
    bus.register_service("acp_orchestrator", harness)
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert _Loadouts.activated == []
    harness.prompts.pop(0)
    tap.publish({"type": "turn_complete"})  # one ended, one still waits
    await _settle(plugin)
    assert _Loadouts.activated == []
    harness.prompts.pop(0)
    tap.publish({"type": "turn_complete"})  # the last one ended, and it succeeded
    await _settle(plugin)
    assert _Loadouts.activated == ["codex"]


@pytest.mark.asyncio
async def test_a_switch_keeps_peer_messages_for_the_harness_it_starts(monkeypatch):
    # Tanzanite on #549 round 5: the queued prompt's completion handed a peer
    # message to the failing harness, and the new harness's start tried to send
    # it behind the switch's fence; nothing sent it once the fence came down.
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}, fallback_provider="acp"
    )
    harness = _Harness("running")
    bus.register_service("acp_orchestrator", harness)
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert harness.held and _Loadouts.activated == []  # busy: the switch waits, and keeps them
    harness.prompts.pop(0)
    tap.publish({"type": "turn_complete"})  # the queued prompt succeeded
    await _settle(plugin)
    assert _Loadouts.activated == ["codex"]
    assert not harness.held and harness.released == [("", True)]  # once, after the fence came down


@pytest.mark.asyncio
async def test_a_native_fallback_answers_the_peer_messages_kept_for_it(monkeypatch):
    # Selenite on #549 round 6: activating a native profile closed the harness,
    # and nothing answered the messages it had kept for the switch.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    harness = _Harness(kept=2, bus=bus)
    bus.register_service("acp_orchestrator", harness)
    activate = _Loadouts.activate

    async def native(self, loadout, event_bus=None):
        harness.active = False  # a native profile closes the harness
        return await activate(self, loadout, event_bus)

    monkeypatch.setattr(_Loadouts, "activate", native)
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert _Loadouts.activated == ["codex"] and harness.handed == 1 and harness.released == []
    assert len(_continues(bus)) == 1  # one native turn answers them


@pytest.mark.asyncio
async def test_a_failed_switch_after_closing_acp_keeps_its_observations(monkeypatch):
    # Round 7: closing ACP is not proof that native profile activation worked.
    plugin, bus, tap, profiles = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}
    )
    harness = _Harness(kept=1)
    bus.register_service("acp_orchestrator", harness)

    async def closes_then_fails(self, loadout, event_bus=None):
        harness.active = False
        return None

    monkeypatch.setattr(_Loadouts, "activate", closes_then_fails)
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")

    assert profiles.active == "glm"
    assert harness.handed == 0 and harness.released == [("", False)]
    assert not _continues(bus)


@pytest.mark.asyncio
async def test_native_handover_does_not_wake_after_history_write_failure(monkeypatch):
    # Round 7: only the orchestrator can tell whether its history is ready.
    plugin, bus, _, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}
    )
    harness = _Harness(kept=1)
    harness.active = False

    async def no_history(self):
        self.held = False
        return False

    harness.hand_over_to_native = no_history.__get__(harness)
    await plugin._hand_over(harness, moved=True)

    assert harness.handed == 0
    assert not _continues(bus)


@pytest.mark.asyncio
async def test_shutdown_settles_an_inflight_fallback_before_returning(monkeypatch):
    # Round 7: shutdown must not let a gated activation change profiles later.
    plugin, bus, tap, profiles = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}
    )
    harness = _Harness(kept=1)
    bus.register_service("acp_orchestrator", harness)
    entered, release = asyncio.Event(), asyncio.Event()
    activate = _Loadouts.activate

    async def gated(self, loadout, event_bus=None):
        entered.set()
        await release.wait()
        return await activate(self, loadout, event_bus)

    monkeypatch.setattr(_Loadouts, "activate", gated)
    try:
        tap.publish({
            "type": "error",
            "message": "claude-code: Claude AI usage limit reached",
            "generation": 1,
        })
        tap.publish({"type": "turn_complete"})
        await asyncio.wait_for(entered.wait(), timeout=1)
        task = plugin._task

        await plugin.shutdown()

        assert task.done()
        assert profiles.active == "glm"
        assert harness.handed == 0 and harness.released == [("", False)]
        assert not _continues(bus)
    finally:
        release.set()
        if plugin._task is not None:
            await asyncio.gather(plugin._task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_switch_that_moves_nothing_sends_the_kept_messages_nowhere(monkeypatch):
    # Selenite on #549 round 6: the fallback was out of usage too, so nothing
    # switched, and the kept messages went straight back to the harness that
    # had just failed them, to fail again in a loop.
    plugin, bus, tap, profiles = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    profiles.active = "codex"  # it already runs the fallback
    harness = _Harness(kept=1)
    bus.register_service("acp_orchestrator", harness)
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert _Loadouts.activated == [] and harness.released == [("", False)] and not _continues(bus)


@pytest.mark.asyncio
async def test_a_switch_waits_only_for_the_harness_that_failed(monkeypatch):
    # Prehnite on #549 round 4: a profile picked by hand while a switch waited
    # was switched away at its next good turn, under the old harness's error.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    bus.register_service("acp_orchestrator", _Harness("running"))
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert _Loadouts.activated == []  # busy: the switch waits
    failed = bus.services["acp_orchestrator"]
    bus.register_service("acp_orchestrator", _Harness())  # the operator's pick, a new harness
    tap.publish({"type": "turn_complete"})  # its turn succeeded
    await _settle(plugin)
    assert _Loadouts.activated == [] and plugin._pending is None
    assert not failed.held and failed.released == [("", True)]  # the operator's pick takes them


@pytest.mark.asyncio
async def test_no_prompt_is_taken_between_the_idle_check_and_the_switch(monkeypatch):
    # Labradorite on #549 round 3: the idle check was a snapshot, so a prompt
    # taken after it failed with "ACP runner closed" when the switch closed
    # the harness.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    harness, refused = _Harness(), []
    bus.register_service("acp_orchestrator", harness)
    activate = _Loadouts.activate

    async def activating(self, loadout, event_bus=None):
        try:  # a prompt that arrives while the harness closes and the new one starts
            harness.admit("during the switch")
        except RuntimeError as exc:
            refused.append(str(exc))
        return await activate(self, loadout, event_bus)

    monkeypatch.setattr(_Loadouts, "activate", activating)
    tap.publish({"type": "error", "message": "claude-code: Claude AI usage limit reached", "generation": 1})
    tap.publish({"type": "turn_complete"})
    # Already on its way: it lands after the failed turn ended, before the switch's step.
    asyncio.get_running_loop().call_soon(harness.admit, "on its way")
    await _settle(plugin)
    assert _Loadouts.activated == [] and harness.prompts == ["on its way"]  # the switch waits for it
    harness.prompts.clear()
    tap.publish({"type": "turn_complete"})
    await _settle(plugin)
    assert _Loadouts.activated == ["codex"] and harness.prompts == []
    assert refused == ["Switching to codex. Send your message again once it has switched."]
    assert harness.closing_reason == ""  # the new harness takes prompts again


def test_the_orchestrator_refuses_prompts_while_a_switch_closes_it():
    from kollabor.llm.acp_orchestrator import ACPError, ACPOrchestrator
    from kollabor.llm.llm_coordinator import _acp_unavailable_result

    acp = ACPOrchestrator.__new__(ACPOrchestrator)
    acp._runner, acp._closed, acp.startup_error = SimpleNamespace(), False, None
    acp.closing_reason = "Switching to codex. Send your message again once it has switched."
    with pytest.raises(ACPError, match="Switching to codex"):
        acp.admit("hello", request_id="r1", source="rpc")
    # What a web client or a peer that sent the prompt is told.
    assert _acp_unavailable_result(acp)["message"] == acp.closing_reason
    acp.closing_reason = ""
    assert "message" not in _acp_unavailable_result(acp)


@pytest.mark.asyncio
async def test_a_runner_is_busy_while_a_prompt_runs_or_waits():
    from kollabor.llm.acp_bridge import ACPRunner

    runner = ACPRunner.__new__(ACPRunner)
    runner._active_future, runner._queue = None, asyncio.Queue()
    assert runner.busy is False
    runner._active_future = asyncio.get_running_loop().create_future()
    assert runner.busy is True
    runner._active_future.set_result(None)
    assert runner.busy is False
    runner._queue.put_nowait(object())  # admitted, waiting its turn
    assert runner.busy is True


@pytest.mark.asyncio
async def test_other_errors_and_unset_fallbacks_never_switch(monkeypatch):
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    assert not await _native_turn_fails(plugin, "Connection reset by peer")
    assert not await _native_turn_fails(plugin, "Usage limit reached.", context_window_exceeded=True)
    # A slash command's error is no failed turn, whatever it says.
    tap.publish({"type": "error", "message": "Rate limit exceeded."})
    tap.publish({"type": "turn_complete"})
    await asyncio.sleep(0)
    assert _Loadouts.activated == []

    plugin, bus, tap, _ = await _agent(monkeypatch, {
        "plugins.model_fallback.loadout": "codex",
        "plugins.model_fallback.agents.lapis": "off",
    })
    assert not await _native_turn_fails(plugin, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == [] and plugin.choice() == ""


@pytest.mark.asyncio
async def test_a_native_turn_moved_to_an_acp_harness_asks_for_the_message(monkeypatch):
    # The review of 01b359ed: the provider's own text, "Invalid openai API
    # key", never matched "invalid api key", so no fallback ran.
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}, fallback_provider="acp"
    )
    assert not await _native_turn_fails(
        plugin, "Invalid openai API key. Please check your API key.", error_type="AuthenticationError"
    )
    assert _Loadouts.activated == ["codex"] and _continues(bus) == []
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"] == (
        "glm-5.3-flash needs a new login. Switched to codex. Send your message again."
    )


@pytest.mark.asyncio
async def test_an_acp_turn_is_never_run_again_on_its_fallback(monkeypatch):
    # The review of 01b359ed: the switch came after the turn's completion, so
    # the turn it ran again answered no stream that was still listening.
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    await _acp_turn_fails(plugin, tap, "claude-code: Claude AI usage limit reached")
    assert _Loadouts.activated == ["codex"] and _continues(bus) == []
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"].endswith("Switched to codex. Send your message again.")


@pytest.mark.asyncio
async def test_an_acp_turn_moved_to_an_acp_harness_asks_for_the_message(monkeypatch):
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.agents.lapis": "codex"}, fallback_provider="acp"
    )
    tap.publish({"type": "error", "message": "claude-code: Claude AI usage limit reached", "generation": 2})
    tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    await plugin._task
    assert _Loadouts.activated == ["codex"] and _continues(bus) == []
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"].endswith("Switched to codex. Send your message again.")


@pytest.mark.asyncio
async def test_set_choice_saves_this_agents_setting(monkeypatch):
    plugin, _, _, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    assert plugin.set_choice("glm")
    assert plugin.config.values["plugins.model_fallback.agents.lapis"] == "glm"
    assert plugin.choice() == "glm"


@pytest.mark.asyncio
async def test_a_tap_the_hub_creates_after_this_plugin_is_watched_from_startup(monkeypatch):
    # Discovery starts *_plugin.py files before plugin packages like hub/, so
    # the tap is not there at initialize. An ACP turn has no request-error hook.
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}, tap_at_init=False
    )
    await bus.fire(EventType.SYSTEM_STARTUP)
    tap.publish({"type": "error", "message": "Usage limit reached for 5 hour.", "generation": 1})
    tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    if plugin._task is not None:
        await plugin._task
    assert _Loadouts.activated == ["codex"]


@pytest.mark.asyncio
async def test_a_tap_the_hub_replaces_is_followed(monkeypatch):
    # The hub can initialize twice; turns then publish to its second tap.
    plugin, bus, first, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    second = DisplayTap()
    bus.register_service("display_tap", second)
    await bus.fire(EventType.SYSTEM_STARTUP)
    await _acp_turn_fails(plugin, second, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex"]


@pytest.mark.asyncio
async def test_a_replaced_tap_and_a_shut_down_plugin_switch_nothing(monkeypatch):
    # The review of 01b359ed: each read of self._on_tap_event is a new bound
    # method, so remove_listener matched none. The old tap, and the current
    # one after shutdown, still switched the agent.
    plugin, bus, first, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    second = DisplayTap()
    bus.register_service("display_tap", second)
    await bus.fire(EventType.SYSTEM_STARTUP)
    await _acp_turn_fails(plugin, first, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == []
    await plugin.shutdown()
    await _acp_turn_fails(plugin, second, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == []



@pytest.mark.asyncio
async def test_a_fallback_names_a_loadout_or_a_profile(monkeypatch):
    # `openai-oauth` is a provider profile, not a loadout: it failed live with
    # "not found" until profiles resolved the way `kollab --llm` reads them.
    plugin, bus, tap, profiles = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex-loadout"})
    assert await _native_turn_fails(plugin, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex-loadout"] and profiles.active == "codex"
    assert plugin.resolve("codex").provider_profile == "codex"  # a bare profile name
    assert plugin.resolve("nonesuch") is None
