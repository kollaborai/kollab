"""First-time ACP model discovery stays isolated from live user sessions."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from kollabor_engine.routes import profiles as profiles_route

import kollabor_ai.loadout_manager as loadout_manager_module
from kollabor.llm import acp_discovery
from kollabor_ai import acp_options as acp_options_module


class FakeProfile:
    def __init__(self, name: str, *, command: tuple[str, ...] | None = None) -> None:
        self.name = name
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
