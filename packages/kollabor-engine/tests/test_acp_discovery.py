"""First-time ACP model discovery stays isolated from live user sessions."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
from kollabor_engine import server
from kollabor_engine.routes import profiles as profiles_route

import kollabor_ai.loadout_manager as loadout_manager_module
from kollabor.llm import acp_discovery
from kollabor_ai import acp_options as acp_options_module


class FakeProfile:
    def __init__(self, name: str, *, command: tuple[str, ...] | None = None) -> None:
        self.name = name
        self.provider = "acp"
        self.command = command or (sys.executable, "-u")
        self.cwd = os.getcwd()
        self.workspace = os.getcwd()
        self.env = {"PROBE_TOKEN": "test-secret-value"}
        self.pass_env = ("INHERITED_TOKEN",)
        self.mcp_servers = ({"name": "must-not-be-used"},)


def _options(model: str, efforts: list[str] | None) -> list[dict]:
    rows = [{
        "id": "model",
        "category": "model",
        "currentValue": model,
        "options": [{"value": "alpha"}, {"value": "beta"}, {"value": "gamma"}],
    }]
    if efforts is not None:
        rows.append({
            "id": "reasoning_effort",
            "category": "thought_level",
            "currentValue": efforts[0] if efforts else "",
            "options": [{"value": value} for value in efforts],
        })
    return rows


def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acp_discovery, "_cache", {})
    monkeypatch.setattr(acp_discovery, "_inflight", {})
    monkeypatch.setattr(acp_discovery, "_profile_semaphore", asyncio.Semaphore(2))


@pytest.mark.asyncio
async def test_real_acp_subprocess_discovers_without_prompt_or_mcp(monkeypatch, tmp_path):
    """F1: cold discovery exercises ACP's real subprocess and wire protocol."""
    _reset(monkeypatch)
    log = tmp_path / "requests.jsonl"
    fixture = tmp_path / "agent.py"
    fixture.write_text('''
import json, os, sys
log = os.environ["PROBE_LOG"]
def options(model):
    effort = ["low", "high"] if model == "alpha" else ["medium"]
    return [
        {"id": "model", "name": "Model", "type": "select", "category": "model",
         "currentValue": model, "options": [{"value": v, "name": v} for v in ["alpha", "beta"]]},
        {"id": "effort", "name": "Effort", "type": "select", "category": "thought_level",
         "currentValue": effort[0], "options": [{"value": v, "name": v} for v in effort]},
    ]
for line in sys.stdin:
    request = json.loads(line)
    with open(log, "a") as f:
        f.write(json.dumps(request) + "\\n")
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "synthetic", "configOptions": options("alpha")}
    elif method == "session/set_config_option":
        result = {"configOptions": options(request["params"]["value"])}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
''')
    profile = FakeProfile("wire-harness", command=(sys.executable, "-u", str(fixture)))
    profile.env = {"PROBE_LOG": str(log)}
    outcome = await acp_discovery.discover_profile(profile)
    assert outcome.status == "ready", outcome.message
    assert outcome.options["efforts_by_model"] == {"alpha": ["low", "high"], "beta": ["medium"]}
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["method"] for r in requests] == ["initialize", "session/new", "session/set_config_option"]
    new_session = requests[1]["params"]
    assert new_session["mcpServers"] == []
    assert new_session["cwd"] != str(tmp_path)


@pytest.mark.asyncio
async def test_discovers_efforts_per_model_without_prompt_or_mcp(monkeypatch):
    _reset(monkeypatch)
    instances = []

    class Runner:
        def __init__(self, profile, **kwargs):
            self.profile = profile
            self.kwargs = kwargs
            self.config_options = []
            self.closed = False
            self.selected = None
            instances.append(self)

        async def start(self):
            self.config_options = _options("alpha", ["low", "high"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, option_id, value):
            self.selected = (option_id, value)
            levels = {"beta": ["medium"], "gamma": ["minimal"]}.get(
                value, ["low", "high"]
            )
            self.config_options = _options(value, levels)
            return self.config_options

        async def close(self):
            self.closed = True

        async def prompt(self, *_args, **_kwargs):
            pytest.fail("discovery must never send prompts")

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    configured = FakeProfile("cold-harness")
    outcome = await acp_discovery.discover_profile(configured)

    assert outcome.status == "ready", outcome.message
    assert outcome.options == {
        "models": ["alpha", "beta", "gamma"],
        "efforts_by_model": {
            "alpha": ["low", "high"], "beta": ["medium"], "gamma": ["minimal"]
        },
        "unknown_effort_models": [],
    }
    assert instances[0].profile.mcp_servers == ()
    assert instances[0].profile.cwd != configured.cwd
    assert instances[0].profile.workspace is None
    assert instances[0].closed


@pytest.mark.asyncio
async def test_failure_and_missing_model_options_are_explicit_and_redacted(monkeypatch):
    _reset(monkeypatch)

    class FailedRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        async def start(self):
            exc = RuntimeError("raw secret test-secret-value")
            exc.error_class = "authentication"
            raise exc

        async def close(self):
            pass

    monkeypatch.setattr(acp_discovery, "ACPRunner", FailedRunner)
    failed = await acp_discovery.discover_profile(FakeProfile("auth-harness"))
    assert failed.status == "unavailable"
    assert failed.message
    assert "authentication" in failed.message.lower()
    assert "test-secret-value" not in failed.message

    class NoOptionsRunner:
        def __init__(self, *_args, **_kwargs):
            self.config_options = []

        async def start(self):
            pass

        def advertised_config_options(self):
            return self.config_options

        async def close(self):
            pass

    monkeypatch.setattr(acp_discovery, "ACPRunner", NoOptionsRunner)
    absent = await acp_discovery.discover_profile(FakeProfile("empty-harness"))
    assert absent.status == "unavailable"
    assert absent.options == {"models": [], "efforts_by_model": {}, "unknown_effort_models": []}


def test_discovery_resolution_and_option_updates_do_not_write_user_caches(monkeypatch, tmp_path):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path))
    profile = acp_discovery._configured_profile(FakeProfile("cache-isolation"))
    profile.resolve(persist_cache=False)
    assert not (tmp_path / "acp").exists()

    remembered = []
    monkeypatch.setattr(
        acp_options_module, "remember",
        lambda *args, **kwargs: remembered.append((args, kwargs)),
    )
    isolated = acp_discovery.ACPRunner(profile, remember_options=False)
    isolated.config_options = [{"id": "model", "category": "model"}]
    assert remembered == []

    normal = acp_discovery.ACPRunner(profile)
    normal.config_options = [{"id": "model", "category": "model"}]
    assert len(remembered) == 1


@pytest.mark.asyncio
async def test_cold_start_longer_than_three_seconds_is_still_bounded_and_succeeds(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(acp_discovery, "_STARTUP_TIMEOUT_SECONDS", 4.0)

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.profile = profile
            self.config_options = []
            self.closed = False

        async def start(self):
            await asyncio.sleep(3.1)
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            self.config_options = _options(value, ["high"])
            return self.config_options

        async def close(self):
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    result = await acp_discovery.discover_profile(FakeProfile("cold-start"))
    assert result.status == "ready", result.message


@pytest.mark.asyncio
async def test_ignored_model_selection_never_attributes_efforts_to_requested_model(monkeypatch):
    _reset(monkeypatch)
    instances = []

    class Runner:
        def __init__(self, *_args, **_kwargs):
            self.config_options = []
            self.closed = False
            instances.append(self)

        async def start(self):
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            # Simulate a harness that silently clamps beta back to alpha.
            selected = "alpha" if value == "beta" else value
            self.config_options = _options(selected, ["high"])
            return self.config_options

        async def close(self):
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    result = await acp_discovery.discover_profile(FakeProfile("clamping"))
    assert result.status == "partial"
    assert "beta" not in result.options["efforts_by_model"]
    assert "beta" in result.options["unknown_effort_models"]
    assert all(instance.closed for instance in instances)


@pytest.mark.asyncio
async def test_operation_timeout_closes_uncertain_runner_before_restart(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(acp_discovery, "_STARTUP_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(acp_discovery, "_OPERATION_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(acp_discovery, "_PROBE_TIMEOUT_SECONDS", 1.0)
    instances = []

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.profile = profile
            self.config_options = []
            self.closed = False
            instances.append(self)

        async def start(self):
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            if value == "beta":
                await asyncio.Future()
            self.config_options = _options(value, ["high"])
            return self.config_options

        async def close(self):
            assert os.path.isdir(self.profile.cwd)
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    result = await acp_discovery.discover_profile(FakeProfile("switch-timeout"))
    assert result.status == "partial"
    assert "beta" in result.options["unknown_effort_models"]
    assert len(instances) == 2
    assert instances[0].closed
    assert instances[1].closed


@pytest.mark.asyncio
async def test_progressive_batches_advance_models_and_stop_automatic_continuation_at_bound(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(acp_discovery, "_MODELS_PER_REQUEST", 3)
    monkeypatch.setattr(acp_discovery, "_MAX_AUTOMATIC_ROUNDS", 3)
    offered = [f"model-{number}" for number in range(10)]

    def options(model: str) -> list[dict]:
        return [
            {
                "id": "model",
                "category": "model",
                "currentValue": model,
                "options": [{"value": value} for value in offered],
            },
            {
                "id": "effort",
                "category": "effort",
                "currentValue": "low",
                "options": [{"value": model}],
            },
        ]

    class Runner:
        def __init__(self, *_args, **_kwargs):
            self.config_options = []
            self.closed = False

        async def start(self):
            self.config_options = options(offered[0])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            self.config_options = options(value)
            return self.config_options

        async def close(self):
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    profile = FakeProfile("many-models")
    first = await acp_discovery.discover_profile(profile)
    assert first.more
    assert first.options["models"] == offered
    assert len(first.options["efforts_by_model"]) == 3

    second = await acp_discovery.discover_profile(profile)
    assert second.more
    assert len(second.options["efforts_by_model"]) == 6
    assert list(second.options["efforts_by_model"])[3:] == offered[3:6]

    third = await acp_discovery.discover_profile(profile)
    assert not third.more
    assert third.status == "partial"
    assert len(third.options["efforts_by_model"]) == 9
    assert third.options["unknown_effort_models"] == ["model-9"]

    # A manual retry after automatic continuation stops continues from the saved cursor.
    fourth = await acp_discovery.discover_profile(profile)
    assert fourth.status == "ready"
    assert len(fourth.options["efforts_by_model"]) == 10


@pytest.mark.asyncio
async def test_hard_model_cap_is_explicit_and_does_not_claim_more_progress(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(acp_discovery, "_MODELS_PER_REQUEST", 64)
    monkeypatch.setattr(acp_discovery, "_MAX_AUTOMATIC_ROUNDS", 1)
    offered = [f"model-{number}" for number in range(65)]

    def options(model: str) -> list[dict]:
        return [
            {
                "id": "model",
                "category": "model",
                "currentValue": model,
                "options": [{"value": value} for value in offered],
            },
            {"id": "effort", "category": "effort", "options": []},
        ]

    class Runner:
        def __init__(self, *_args, **_kwargs):
            self.config_options = []

        async def start(self):
            self.config_options = options(offered[0])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            self.config_options = options(value)
            return self.config_options

        async def close(self):
            return None

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    outcome = await acp_discovery.discover_profile(FakeProfile("over-cap"))
    assert outcome.status == "partial"
    assert len(outcome.options["models"]) == 64
    assert len(outcome.options["efforts_by_model"]) == 64
    assert not outcome.more
    assert outcome.message == "The harness advertised more model choices than discovery can inspect."


@pytest.mark.asyncio
async def test_failed_model_switch_is_unknown_and_restart_prevents_late_state_leak(monkeypatch):
    _reset(monkeypatch)
    instances = []

    class Runner:
        def __init__(self, *_args, **_kwargs):
            self.config_options = []
            self.closed = False
            instances.append(self)

        async def start(self):
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            if value == "beta":
                raise TimeoutError("late response")
            self.config_options = _options(value, ["high"])
            return self.config_options

        async def close(self):
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    result = await acp_discovery.discover_profile(FakeProfile("harness"))

    assert result.status == "partial", result.message
    assert result.options["efforts_by_model"] == {"alpha": ["low"], "gamma": ["high"]}
    assert result.options["unknown_effort_models"] == ["beta"]
    assert len(instances) >= 2
    assert all(instance.closed for instance in instances)


@pytest.mark.asyncio
async def test_same_profile_requests_are_single_flight_and_cache_does_not_cross_profiles(monkeypatch):
    _reset(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.profile = profile
            self.config_options = []
            self.closed = False
            calls.append(profile.name)

        async def start(self):
            started.set()
            await release.wait()
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            self.config_options = _options(value, ["medium"] if value == "beta" else ["high"])
            return self.config_options

        async def close(self):
            self.closed = True

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    first = asyncio.create_task(acp_discovery.discover_profile(FakeProfile("same")))
    await started.wait()
    second = asyncio.create_task(acp_discovery.discover_profile(FakeProfile("same")))
    release.set()
    one, two = await asyncio.gather(first, second)
    assert one == two
    assert calls == ["same"]

    other = await acp_discovery.discover_profile(FakeProfile("different"))
    assert other.status == "ready"
    assert calls == ["same", "different"]
    changed_context = FakeProfile("same")
    changed_context.env["PROBE_TOKEN"] = "another-context"
    changed = await acp_discovery.discover_profile(changed_context)
    assert changed.status == "ready"
    assert calls == ["same", "different", "same"]


@pytest.mark.asyncio
async def test_cancellation_closes_runner_before_probe_slot_is_reused(monkeypatch):
    _reset(monkeypatch)
    started = asyncio.Event()
    closed = asyncio.Event()

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.profile = profile
            self.config_options = []

        async def start(self):
            started.set()
            await asyncio.Future()

        def advertised_config_options(self):
            return self.config_options

        async def close(self):
            assert os.path.isdir(self.profile.cwd)
            closed.set()

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    task = asyncio.create_task(acp_discovery.discover_profile(FakeProfile("cancel")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_cancelling_one_shared_waiter_does_not_cancel_the_other(monkeypatch):
    _reset(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.profile = profile
            self.config_options = []

        async def start(self):
            started.set()
            await release.wait()
            self.config_options = _options("alpha", ["low"])

        def advertised_config_options(self):
            return self.config_options

        async def set_config_option(self, _option_id, value):
            self.config_options = _options(value, ["high"])
            return self.config_options

        async def close(self):
            assert os.path.isdir(self.profile.cwd)
            closed.set()

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    first = asyncio.create_task(acp_discovery.discover_profile(FakeProfile("shared-cancel")))
    await started.wait()
    second = asyncio.create_task(acp_discovery.discover_profile(FakeProfile("shared-cancel")))
    async with asyncio.timeout(4.0):
        while not any(flight.waiters == 2 for flight in acp_discovery._inflight.values()):
            await asyncio.sleep(0.01)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not closed.is_set()

    release.set()
    result = await second
    assert result.status == "ready"
    assert closed.is_set()


@pytest.mark.asyncio
async def test_choices_route_returns_discovery_status_and_uses_only_fresh_options(monkeypatch):
    """The off switch (KOLLAB_PICKER_WARM=0) keeps the pre-#953 route: it waits for discovery."""
    monkeypatch.setenv("KOLLAB_PICKER_WARM", "0")
    profile = SimpleNamespace(name="cold-acp", provider="acp")
    fresh = acp_discovery.ACPDiscoveryOutcome(
        options={
            "models": ["alpha", "beta"],
            "efforts_by_model": {"alpha": ["low"]},
            "unknown_effort_models": ["beta"],
        },
        status="partial",
        message="Loading more model options.",
        more=True,
    )
    received = {}

    class FakeManager:
        def __init__(self, _profile_manager, *, config=None):
            self.config = config

        def provider_profiles(self):
            return [profile]

        async def refresh_catalogs(self):
            return None

        def list_choices(self, options):
            received.update(options)
            return [{"profile": "cold-acp", "model": "alpha", "effort": "low"}]

    async def discover(_profiles):
        return {"cold-acp": fresh}

    monkeypatch.setattr(profiles_route, "_get_profile_manager", lambda: object())
    monkeypatch.setattr(loadout_manager_module, "LoadoutManager", FakeManager)
    # the route passes no config handle to LoadoutManager; this stub keeps a stray read off the real config (#966)
    empty_config = SimpleNamespace(get=lambda key, default=None: default)
    monkeypatch.setattr(
        profiles_route, "_loadout_config", lambda workspace="": empty_config
    )
    monkeypatch.setattr(acp_discovery, "discover_profiles", discover)

    response = await profiles_route.list_choices()
    assert response == {
        "choices": [{"profile": "cold-acp", "model": "alpha", "effort": "low"}],
        "discovery": {
            "cold-acp": {
                "status": "partial",
                "more": True,
                "message": "Loading more model options.",
            }
        },
    }
    assert received == {"cold-acp": fresh.options}


# ---------------------------------------------------------------------------
# Background warm cache (#953): the picker's choices are discovered once in the
# background and served from memory, so a page never waits for a harness.
# ---------------------------------------------------------------------------

MODELS3 = ["alpha", "beta", "gamma"]
LEVELS3 = {"alpha": ["low", "high"], "beta": ["medium"], "gamma": ["minimal"]}


class Clock:
    """Stands in for acp_discovery._now; a test moves time by hand."""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest_asyncio.fixture
async def warm(monkeypatch):
    """Fresh warm-cache state, the picker warm switched on, a hand-moved clock;
    whatever the test left running is stopped afterwards."""
    _reset(monkeypatch)
    monkeypatch.setattr(acp_discovery, "_warm", {}, raising=False)
    monkeypatch.setattr(acp_discovery, "_warm_tasks", {}, raising=False)
    clock = Clock()
    monkeypatch.setattr(acp_discovery, "_now", clock, raising=False)
    monkeypatch.setenv("KOLLAB_PICKER_WARM", "1")
    catalogs = getattr(profiles_route, "_CatalogRefresh", None)
    if catalogs is not None:
        monkeypatch.setattr(profiles_route, "_catalogs", catalogs())
    yield clock
    stop = getattr(acp_discovery, "stop_warming", None)
    if stop is not None:
        await stop()
    task = getattr(getattr(profiles_route, "_catalogs", None), "task", None)
    if task is not None:
        task.cancel()


@pytest.mark.asyncio
async def test_known_outcomes_marks_a_retrying_incomplete_profile_as_loading(warm):
    profile = FakeProfile("retrying")
    fingerprint = acp_discovery.launch_fingerprint(profile)
    cached = acp_discovery._outcome(
        "partial", models=["alpha"], efforts_by_model={"alpha": []},
        unknown_effort_models=["alpha"], message="retry this", more=False,
    )
    acp_discovery._warm[profile.name] = acp_discovery._Warm(
        fingerprint=fingerprint, outcome=cached, tried=warm.value, failures=1,
    )
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    acp_discovery._warm_tasks[profile.name] = acp_discovery._WarmTask(task, fingerprint)

    outcome = acp_discovery.known_outcomes([profile])[profile.name]

    assert outcome.options == cached.options
    assert outcome.more is True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _harness(monkeypatch, models, efforts=None):
    """Patch ACPRunner with a scripted harness; return its dials and counters."""
    h = SimpleNamespace(
        models=list(models),
        efforts=dict(efforts or {}),  # model -> levels, read at call time
        gates={},  # profile name -> Event that start() waits for
        fail_start={},  # profile name -> exception start() raises
        fail_switch={},  # profile name -> models whose switch is refused
        starts={},  # profile name -> times start() ran
        switched={},  # profile name -> models switched to, in order
        closes=0,
        at_start=None,  # called as at_start(profile name) at every start()
    )
    h.total = lambda: sum(h.starts.values())

    def advertise(current):
        levels = h.efforts.get(current, ["low"])
        return [
            {"id": "model", "category": "model", "currentValue": current,
             "options": [{"value": m} for m in h.models]},
            {"id": "effort", "category": "thought_level", "currentValue": levels[0],
             "options": [{"value": level} for level in levels]},
        ]

    class Runner:
        def __init__(self, profile, **_kwargs):
            self.name = profile.name
            self.options = []

        async def start(self):
            h.starts[self.name] = h.starts.get(self.name, 0) + 1
            if h.at_start:
                h.at_start(self.name)
            gate = h.gates.get(self.name)
            if gate is not None:
                await gate.wait()
            if self.name in h.fail_start:
                raise h.fail_start[self.name]
            self.options = advertise(h.models[0])

        def advertised_config_options(self):
            return self.options

        async def set_config_option(self, _option_id, value):
            h.switched.setdefault(self.name, []).append(value)
            if value in h.fail_switch.get(self.name, ()):
                raise RuntimeError("the harness refused the switch")
            self.options = advertise(value)
            return self.options

        async def close(self):
            h.closes += 1

        async def prompt(self, *_args, **_kwargs):
            pytest.fail("discovery must never send prompts")

    monkeypatch.setattr(acp_discovery, "ACPRunner", Runner)
    return h


def _warmable(name: str, **env: str) -> FakeProfile:
    profile = FakeProfile(name)
    profile.env = {"PROBE_TOKEN": "test-secret-value", **env}
    return profile


async def _until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _idle() -> bool:
    return not acp_discovery._warm_tasks


async def _warmed(*profiles) -> None:
    acp_discovery.warm_in_background(list(profiles))
    await _until(_idle)


def _stub_route(monkeypatch, profiles, *, refresh_catalogs=None) -> None:
    """The route's collaborators, faked: the profile list, catalogs and row builder."""

    class FakeManager:
        def __init__(self, _profile_manager, *, config=None):
            self.config = config

        def provider_profiles(self):
            return list(profiles)

        async def refresh_catalogs(self):
            if refresh_catalogs is not None:
                await refresh_catalogs()

        def list_choices(self, options):
            return [
                {"profile": name, "model": model, "effort": effort}
                for name, reported in options.items()
                for model in (reported.get("models") or [""])
                for effort in (reported.get("efforts_by_model", {}).get(model) or [""])
            ]

    monkeypatch.setattr(profiles_route, "_get_profile_manager", lambda: object())
    monkeypatch.setattr(loadout_manager_module, "LoadoutManager", FakeManager)


@pytest.mark.asyncio
async def test_choices_route_never_waits_for_a_harness(warm, monkeypatch):
    """A page asks while the harness is still starting: the answer is at once, and the
    background warm fills the next one. (Before #953 this call waited for the harness.)"""
    harness = _harness(monkeypatch, ["alpha", "beta"], {"alpha": ["low", "high"], "beta": ["medium"]})
    harness.gates["route-acp"] = asyncio.Event()
    _stub_route(monkeypatch, [_warmable("route-acp")])

    first = await asyncio.wait_for(profiles_route.list_choices(), 0.5)
    assert first["discovery"]["route-acp"] == {
        "status": "partial", "more": True, "message": "Loading more model options.",
    }
    assert first["choices"] == [{"profile": "route-acp", "model": "", "effort": ""}]

    harness.gates["route-acp"].set()
    await _until(_idle)
    second = await asyncio.wait_for(profiles_route.list_choices(), 0.5)
    assert second["discovery"]["route-acp"] == {"status": "ready", "more": False}
    assert second["choices"] == [
        {"profile": "route-acp", "model": "alpha", "effort": "low"},
        {"profile": "route-acp", "model": "alpha", "effort": "high"},
        {"profile": "route-acp", "model": "beta", "effort": "medium"},
    ]


@pytest.mark.asyncio
async def test_known_outcomes_answers_at_once_and_the_warm_fills_it(warm, monkeypatch):
    models = [f"m{i}" for i in range(8)]
    harness = _harness(monkeypatch, models)
    profile = _warmable("cold-warm")

    pending = acp_discovery.known_outcomes([profile])["cold-warm"]
    assert (pending.status, pending.more, pending.options["models"]) == ("partial", True, [])
    assert harness.total() == 0  # reading never launches a harness

    await _warmed(profile)
    done = acp_discovery.known_outcomes([profile])["cold-warm"]
    assert done.status == "ready" and not done.more
    assert done.options["models"] == models
    assert done.options["efforts_by_model"] == {m: ["low"] for m in models}
    assert harness.total() == 2  # eight models, six per probe


@pytest.mark.asyncio
async def test_a_fresh_ready_result_launches_nothing_again(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("fresh")
    await _warmed(profile)
    launched = harness.total()
    for seconds in (0, 1, 100, 400):  # 501 s in all: still inside the 10 min window
        warm.advance(seconds)
        acp_discovery.warm_in_background([profile])
        assert _idle()
    assert harness.total() == launched


@pytest.mark.asyncio
async def test_a_stale_result_refreshes_behind_the_old_list(warm, monkeypatch):
    models = [f"m{i}" for i in range(8)]
    harness = _harness(monkeypatch, models, {m: ["low"] for m in models})
    profile = _warmable("stale")
    await _warmed(profile)
    old = acp_discovery.known_outcomes([profile])["stale"]
    assert old.status == "ready"

    harness.efforts = {m: ["high"] for m in models}  # the account changed; only a new probe sees it
    seen = []
    harness.at_start = lambda _name: seen.append(acp_discovery.known_outcomes([profile])["stale"])
    warm.advance(601)
    await _warmed(profile)

    assert len(seen) == 2  # two probes of six; the old list showed through both
    assert all(outcome == old for outcome in seen)
    new = acp_discovery.known_outcomes([profile])["stale"]
    assert new.status == "ready"
    assert new.options["efforts_by_model"] == {m: ["high"] for m in models}


@pytest.mark.asyncio
async def test_an_hour_of_idle_still_answers_with_the_full_list(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("idle")
    await _warmed(profile)
    old = acp_discovery.known_outcomes([profile])["idle"]
    launched = harness.total()

    harness.gates["idle"] = asyncio.Event()  # hold the refresh so the reading is unambiguous
    warm.advance(3600)
    assert acp_discovery.known_outcomes([profile])["idle"] == old  # nothing hides a list by age
    acp_discovery.warm_in_background([profile])
    acp_discovery.warm_in_background([profile])
    await _until(lambda: harness.total() == launched + 1)
    assert len(acp_discovery._warm_tasks) == 1  # one refresh, however many pages asked
    assert acp_discovery.known_outcomes([profile])["idle"] == old
    harness.gates["idle"].set()
    await _until(_idle)
    assert harness.total() == launched + 1


@pytest.mark.asyncio
async def test_failed_refreshes_keep_the_old_list_back_off_and_give_up_after_half_an_hour(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("flaky")
    await _warmed(profile)
    old = acp_discovery.known_outcomes([profile])["flaky"]
    harness.fail_start["flaky"] = RuntimeError("cannot start")

    async def ask(after: float) -> bool:
        """Move time on and ask once; True when that launched a probe."""
        before = harness.total()
        warm.advance(after)
        acp_discovery.warm_in_background([profile])
        await _until(_idle)
        return harness.total() > before

    assert await ask(601) is True  # stale: the first refresh fails
    assert acp_discovery.known_outcomes([profile])["flaky"] == old
    assert await ask(14) is False  # backoff 15 s
    assert await ask(2) is True
    assert await ask(59) is False  # backoff 60 s
    assert await ask(2) is True
    assert await ask(299) is False  # backoff 300 s
    assert await ask(2) is True
    assert acp_discovery.known_outcomes([profile])["flaky"] == old  # still the last good list
    assert await ask(1800) is True  # half an hour since the first failed refresh
    assert acp_discovery.known_outcomes([profile])["flaky"].status == "unavailable"


class _AuthError(Exception):
    error_class = "authentication"


@pytest.mark.asyncio
async def test_a_login_failure_replaces_the_old_list_at_once(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("login")
    await _warmed(profile)
    harness.fail_start["login"] = _AuthError("signed out")
    warm.advance(601)
    await _warmed(profile)
    shown = acp_discovery.known_outcomes([profile])["login"]
    assert shown.status == "unavailable"
    assert "authentication" in (shown.message or "")


@pytest.mark.asyncio
async def test_a_changed_launch_context_drops_the_old_list_and_warms_again(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    first, other = _warmable("ctx-a"), _warmable("ctx-b")
    await _warmed(first, other)
    changed = _warmable("ctx-a", ACCOUNT="someone-else")

    shown = acp_discovery.known_outcomes([changed, other])
    assert shown["ctx-a"].more and not shown["ctx-a"].options["models"]  # another account's list is not shown
    assert shown["ctx-b"].status == "ready"  # a neighbour is untouched

    launched = dict(harness.starts)
    await _warmed(changed, other)
    assert harness.starts["ctx-a"] == launched["ctx-a"] + 1
    assert harness.starts["ctx-b"] == launched["ctx-b"]
    assert acp_discovery.known_outcomes([changed])["ctx-a"].status == "ready"


def test_launch_fingerprint_hides_values_and_follows_the_launch_inputs(monkeypatch):
    fingerprint = acp_discovery.launch_fingerprint
    base = _warmable("fp")
    assert fingerprint(base) == fingerprint(_warmable("fp"))
    assert fingerprint(base) != fingerprint(_warmable("fp", ACCOUNT="b"))
    assert "test-secret-value" not in fingerprint(base)
    monkeypatch.setenv("INHERITED_TOKEN", "one")
    one = fingerprint(base)
    monkeypatch.setenv("INHERITED_TOKEN", "two")
    assert fingerprint(base) != one  # a passed-through variable is part of the account


def test_profile_response_does_not_publish_the_unkeyed_launch_fingerprint(monkeypatch):
    from kollabor_engine import auth

    monkeypatch.setattr(auth, "get_token", lambda: "private-engine-token")
    profile = FakeProfile("response-fingerprint")
    profile.to_dict = lambda: {
        "name": profile.name,
        "provider": profile.provider,
        "command": list(profile.command),
        "env": profile.env,
        "cwd": profile.cwd,
        "workspace": profile.workspace,
        "pass_env": profile.pass_env,
    }
    profile.get_effort_provider = lambda: "acp"
    result = profiles_route._redacted_profile_dict(profile)

    assert result["choices_fingerprint"] != acp_discovery.launch_fingerprint(profile)


@pytest.mark.asyncio
async def test_retry_probes_only_incomplete_profiles_and_only_the_models_that_failed(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    good, bad = _warmable("retry-good"), _warmable("retry-bad")
    harness.fail_switch["retry-bad"] = {"beta"}
    await _warmed(good, bad)
    shown = acp_discovery.known_outcomes([good, bad])
    assert shown["retry-good"].status == "ready"
    assert shown["retry-bad"].status == "partial" and not shown["retry-bad"].more
    assert shown["retry-bad"].options["unknown_effort_models"] == ["beta"]

    harness.fail_switch.clear()  # the harness works now
    launched = dict(harness.starts)
    switched = len(harness.switched["retry-bad"])
    acp_discovery.warm_in_background([good, bad])  # inside the backoff window: nothing runs
    assert _idle()
    acp_discovery.warm_in_background([good, bad], force=True)  # Retry
    await _until(_idle)
    assert harness.starts["retry-good"] == launched["retry-good"]  # a ready profile is left alone
    assert harness.starts["retry-bad"] == launched["retry-bad"] + 1
    assert harness.switched["retry-bad"][switched:] == ["beta"]  # only the model that failed is asked again
    fixed = acp_discovery.known_outcomes([good, bad])["retry-bad"]
    assert fixed.status == "ready" and set(fixed.options["efforts_by_model"]) == set(MODELS3)


@pytest.mark.asyncio
async def test_a_profile_that_vanishes_is_forgotten_and_never_launched_again(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    keep, drop = _warmable("keep"), _warmable("drop")
    await _warmed(keep, drop)
    harness.gates["drop"] = asyncio.Event()
    warm.advance(601)
    acp_discovery.warm_in_background([keep, drop])  # both are stale; drop's refresh hangs
    await _until(lambda: harness.starts["drop"] == 2)

    acp_discovery.warm_in_background([keep])  # the user deleted "drop"
    assert "drop" not in acp_discovery._warm_tasks and "drop" not in acp_discovery._warm
    await _until(_idle)
    warm.advance(3600)
    await _warmed(keep)
    assert harness.starts["drop"] == 2  # never launched again
    assert acp_discovery.known_outcomes([drop])["drop"].more  # a deleted profile is a cold one


@pytest.mark.asyncio
async def test_a_slow_harness_does_not_hold_back_a_fast_one(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    slow, fast = _warmable("slow"), _warmable("fast")
    harness.gates["slow"] = asyncio.Event()
    acp_discovery.warm_in_background([slow, fast])
    await _until(lambda: acp_discovery.known_outcomes([fast])["fast"].status == "ready")
    assert acp_discovery.known_outcomes([slow])["slow"].more  # still loading
    harness.gates["slow"].set()
    await _until(_idle)
    assert acp_discovery.known_outcomes([slow])["slow"].status == "ready"


@pytest.mark.asyncio
async def test_stop_warming_cancels_the_probes_and_a_later_warm_starts_over(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("stop")
    harness.gates["stop"] = asyncio.Event()
    acp_discovery.warm_in_background([profile])
    await _until(lambda: harness.starts.get("stop"))
    await acp_discovery.stop_warming()
    assert _idle()
    assert harness.closes == 1  # the half-started harness was closed
    harness.gates["stop"].set()
    await _warmed(profile)
    assert acp_discovery.known_outcomes([profile])["stop"].status == "ready"


@pytest.mark.asyncio
async def test_a_harness_that_always_fails_one_model_is_retried_gently_and_never_loses_rows(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    harness.fail_switch["part"] = {"beta"}
    profile = _warmable("part")
    await _warmed(profile)
    first = acp_discovery.known_outcomes([profile])["part"]
    assert first.status == "partial" and not first.more
    kept = {"alpha": ["low", "high"], "gamma": ["minimal"]}
    assert first.options["efforts_by_model"] == kept

    async def open_page(after: float) -> int:
        """Move time on, the page asks (every open and poll does); return the probes that caused."""
        before = harness.total()
        warm.advance(after)
        acp_discovery.warm_in_background([profile])
        await _until(_idle)
        # whatever happens behind it, the rows the page can see never shrink
        assert acp_discovery.known_outcomes([profile])["part"].options["efforts_by_model"] == kept
        return harness.total() - before

    assert [await open_page(s) for s in (1, 5, 8)] == [0, 0, 0]  # 14 s: inside the 15 s backoff
    switched = len(harness.switched["part"])
    assert await open_page(2) == 1  # 16 s: one retry...
    assert harness.switched["part"][switched:] == ["beta"]  # ...of the model that failed only
    assert await open_page(30) == 0  # the backoff is 60 s now
    switched = len(harness.switched["part"])
    assert await open_page(31) == 1  # the 30 s memory is gone; the warm record still narrows the retry
    assert harness.switched["part"][switched:] == ["beta"]
    assert await open_page(299) == 0  # then 300 s
    assert await open_page(2) == 1


@pytest.mark.asyncio
async def test_fresh_probes_even_when_a_complete_result_is_cached(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    profile = _warmable("fresh-flag")
    assert (await acp_discovery.discover_profile(profile)).status == "ready"
    assert harness.total() == 1
    await acp_discovery.discover_profile(profile)
    assert harness.total() == 1  # a complete result is served from memory for 30 s
    assert (await acp_discovery.discover_profile(profile, fresh=True)).status == "ready"
    assert harness.total() == 2  # fresh skips that memory


@pytest.mark.asyncio
async def test_a_seed_narrows_a_probe_when_nothing_is_cached(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    harness.fail_switch["seeded"] = {"beta"}
    profile = _warmable("seeded")
    first = await acp_discovery.discover_profile(profile)
    assert first.status == "partial" and first.options["unknown_effort_models"] == ["beta"]

    warm.advance(31)  # the 30 s memory is gone
    harness.fail_switch.clear()
    switched = len(harness.switched["seeded"])
    second = await acp_discovery.discover_profile(profile, seed=first)
    assert harness.switched["seeded"][switched:] == ["beta"]  # only the model that failed
    assert second.status == "ready"


def test_the_off_switch_reads_the_environment(monkeypatch):
    monkeypatch.delenv("KOLLAB_PICKER_WARM", raising=False)
    assert acp_discovery.warming_enabled() is True
    for value in ("0", "false", "No", "OFF"):
        monkeypatch.setenv("KOLLAB_PICKER_WARM", value)
        assert acp_discovery.warming_enabled() is False
    monkeypatch.setenv("KOLLAB_PICKER_WARM", "1")
    assert acp_discovery.warming_enabled() is True


@pytest.mark.asyncio
async def test_choices_route_forwards_retry_and_survives_a_failing_warm(warm, monkeypatch):
    seen = []
    monkeypatch.setattr(acp_discovery, "warm_in_background", lambda _profiles, *, force=False: seen.append(force))
    _stub_route(monkeypatch, [_warmable("fwd-acp")])
    await profiles_route.list_choices()
    await profiles_route.list_choices(refresh=True)
    assert seen == [False, True]

    def explode(_profiles, *, force=False):
        raise RuntimeError("warm broke")

    monkeypatch.setattr(acp_discovery, "warm_in_background", explode)
    response = await profiles_route.list_choices()
    assert "fwd-acp" in response["discovery"]  # the picker still answers


@pytest.mark.asyncio
async def test_choices_route_waits_for_the_first_catalog_refresh_only(warm, monkeypatch):
    release = asyncio.Event()

    async def slow_catalogs():
        await release.wait()

    _stub_route(monkeypatch, [SimpleNamespace(name="native", provider="openai")], refresh_catalogs=slow_catalogs)
    monkeypatch.setattr(profiles_route, "_CATALOG_WAIT_SECONDS", 0.2)
    loop = asyncio.get_running_loop()

    started = loop.time()
    await profiles_route.list_choices()
    assert loop.time() - started >= 0.15  # no refresh has settled yet: it waits, up to the cap
    release.set()
    await _until(lambda: profiles_route._catalogs.settled)

    release.clear()  # the next refresh hangs again
    started = loop.time()
    await profiles_route.list_choices()
    assert loop.time() - started < 0.1  # one has settled: every answer comes from the cache


@pytest.mark.asyncio
async def test_choices_route_never_waits_for_catalogs_when_every_profile_is_a_harness(warm, monkeypatch):
    async def hung_catalogs():
        await asyncio.Event().wait()

    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    _stub_route(monkeypatch, [_warmable("only-acp")], refresh_catalogs=hung_catalogs)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await profiles_route.list_choices()
    assert loop.time() - started < 0.1
    await _until(_idle)
    assert harness.total() == 1


@pytest.mark.asyncio
async def test_startup_warm_fills_the_cache_before_any_page_asks(warm, monkeypatch):
    harness = _harness(monkeypatch, ["alpha", "beta"], {"alpha": ["low"], "beta": ["high"]})
    _stub_route(monkeypatch, [_warmable("boot-acp")])
    await profiles_route.warm_model_choices()  # what `serve` runs at start
    await _until(_idle)
    launched = harness.total()

    response = await asyncio.wait_for(profiles_route.list_choices(), 0.5)
    assert response["discovery"]["boot-acp"] == {"status": "ready", "more": False}
    assert harness.total() == launched  # the first page request launched nothing


@pytest.mark.asyncio
async def test_the_off_switch_stops_the_startup_warm(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    _stub_route(monkeypatch, [_warmable("off-acp")])
    monkeypatch.setenv("KOLLAB_PICKER_WARM", "0")
    await profiles_route.warm_model_choices()
    await asyncio.sleep(0.05)
    assert harness.total() == 0 and _idle()


@pytest.mark.asyncio
async def test_serve_starts_the_warm_and_shutdown_stops_it(monkeypatch):
    warmed, stopped = [], []

    async def fake_warm():
        warmed.append(True)

    async def fake_stop():
        stopped.append(True)

    monkeypatch.setattr(profiles_route, "warm_model_choices", fake_warm, raising=False)
    monkeypatch.setattr(acp_discovery, "stop_warming", fake_stop, raising=False)

    built_by_a_test = SimpleNamespace(state=SimpleNamespace(handover_store=None))
    async with server._engine_lifespan(built_by_a_test):
        await asyncio.sleep(0.02)
    assert warmed == []  # an app a test builds never warms: no real harness is launched

    stopped.clear()
    served = SimpleNamespace(state=SimpleNamespace(handover_store=None, warm_model_choices=True))
    async with server._engine_lifespan(served):
        await _until(lambda: warmed == [True])
    assert stopped == [True]


@pytest.mark.asyncio
async def test_warming_one_workspace_preserves_another_workspace_cache(warm, monkeypatch):
    harness = _harness(monkeypatch, MODELS3, LEVELS3)
    folder_a = _warmable("shared")
    folder_b = _warmable("shared")

    acp_discovery.warm_in_background([folder_a], scope="/work/a")
    await _until(_idle)
    acp_discovery.warm_in_background([folder_b], scope="/work/b")
    await _until(_idle)

    assert acp_discovery.known_outcomes([folder_a], scope="/work/a")["shared"].status == "ready"
    assert acp_discovery.known_outcomes([folder_b], scope="/work/b")["shared"].status == "ready"
    assert harness.starts["shared"] == 2
