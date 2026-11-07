"""Usage ledger plugin tests (issue #723). Fake bus and config; no providers."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor_events import EventType
from plugins import usage_ledger_plugin as plugin_module
from plugins.usage_ledger_plugin import UsageLedgerPlugin


class FakeConfig:
    def __init__(self, values=None):
        self.values = {
            "plugins.usage_ledger.enabled": True,
            "plugins.usage_ledger.retention_days": 30,
            "plugins.usage_ledger.price_table": {
                "acme/known-model": {
                    "prompt_per_million": 2.0,
                    "completion_per_million": 8.0,
                }
            },
            "plugins.usage_ledger.budget_alerts": [],
            "plugins.usage_ledger.lane": "",
            "plugins.usage_ledger.pr": "",
        }
        self.values.update(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeBus:
    def __init__(self):
        self.hooks = []
        self.services = {}

    async def register_hook(self, hook):
        self.hooks.append(hook)

    def get_service(self, name):
        return self.services.get(name)

    def callback_for(self, event_type):
        return [h.callback for h in self.hooks if h.event_type == event_type]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.delenv("KOLLAB_USAGE_LANE", raising=False)
    monkeypatch.delenv("KOLLAB_USAGE_PR", raising=False)
    monkeypatch.setattr(
        plugin_module, "get_project_data_dir", lambda *a, **k: tmp_path / "proj"
    )
    return tmp_path


async def _make(config=None, *, bus=None, renderer=None, agent_name="coder"):
    bus = bus or FakeBus()
    plugin = UsageLedgerPlugin("usage_ledger", bus, renderer, FakeConfig(config))
    manager = SimpleNamespace(active_agent_name=agent_name)
    await plugin.initialize(agent_manager=manager, command_registry=None)
    await plugin.register_hooks()
    return plugin, bus


def _post_payload(**overrides):
    payload = {
        "model": "known-model",
        "provider": "acme",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "usage_source": "provider",
    }
    payload.update(overrides)
    return payload


def _ledger_rows(plugin):
    return plugin._ledger.read()


@pytest.mark.asyncio
async def test_disabled_registers_no_hooks_and_writes_nothing(tmp_path):
    bus = FakeBus()
    plugin, bus = await _make({"plugins.usage_ledger.enabled": False}, bus=bus)

    assert bus.hooks == []
    assert plugin._ledger is None
    assert not (tmp_path / "proj").exists()


@pytest.mark.asyncio
async def test_each_turn_appends_one_priced_row():
    plugin, bus = await _make()
    pre = bus.callback_for(EventType.LLM_REQUEST_PRE)[0]
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await pre({}, None)
    await post(_post_payload(), None)

    (row,) = _ledger_rows(plugin)
    assert row["agent"] == "coder"
    assert row["provider"] == "acme"
    assert row["model"] == "known-model"
    assert row["tokens_in"] == 1_000_000
    assert row["cost_usd"] == pytest.approx(2.0)
    assert row["cost_status"] == "priced"
    assert row["wall_ms"] is not None and row["wall_ms"] >= 0


@pytest.mark.asyncio
async def test_turn_without_provider_usage_is_recorded_as_missing():
    """Stale token fields from a prior turn must not leak into a no-usage row."""
    plugin, bus = await _make()
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(
        _post_payload(usage_source="none", input_tokens=999, output_tokens=7),
        None,
    )

    (row,) = _ledger_rows(plugin)
    assert row["usage_source"] == "none"
    assert row["tokens_in"] is None
    assert row["cost_usd"] is None
    assert row["cost_status"] == "no_usage"


@pytest.mark.asyncio
async def test_unknown_model_is_unpriced_not_zero():
    plugin, bus = await _make()
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(_post_payload(provider="other", model="mystery-7"), None)

    (row,) = _ledger_rows(plugin)
    assert row["cost_status"] == "unpriced"
    assert row["cost_usd"] is None
    assert row["tokens_in"] == 1_000_000


@pytest.mark.asyncio
async def test_error_clears_the_clock_so_next_turn_gets_its_own_wall_time():
    plugin, bus = await _make()
    pre = bus.callback_for(EventType.LLM_REQUEST_PRE)[0]
    err = bus.callback_for(EventType.LLM_REQUEST_ERROR)[0]
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await pre({}, None)
    await err({}, None)
    await post(_post_payload(), None)

    (row,) = _ledger_rows(plugin)
    assert row["wall_ms"] is None


@pytest.mark.asyncio
async def test_lane_and_pr_come_from_env_over_config(monkeypatch):
    monkeypatch.setenv("KOLLAB_USAGE_LANE", "issue-723")
    monkeypatch.setenv("KOLLAB_USAGE_PR", "742")
    plugin, bus = await _make({"plugins.usage_ledger.lane": "from-config"})
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(_post_payload(), None)

    (row,) = _ledger_rows(plugin)
    assert row["lane"] == "issue-723"
    assert row["pr"] == "742"


@pytest.mark.asyncio
async def test_lane_falls_back_to_config():
    plugin, bus = await _make({"plugins.usage_ledger.lane": "from-config"})
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(_post_payload(), None)

    (row,) = _ledger_rows(plugin)
    assert row["lane"] == "from-config"


@pytest.mark.asyncio
async def test_hub_identity_names_the_agent_over_the_bundle_name():
    bus = FakeBus()
    bus.services["hub_plugin"] = SimpleNamespace(
        _identity=SimpleNamespace(name="lapis")
    )
    plugin, bus = await _make(bus=bus, agent_name="coder")
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(_post_payload(), None)

    (row,) = _ledger_rows(plugin)
    assert row["agent"] == "lapis"


@pytest.mark.asyncio
async def test_recording_failure_never_breaks_the_turn(monkeypatch):
    plugin, bus = await _make()
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(plugin._ledger, "append", boom)
    data = _post_payload()

    assert await post(data, None) is data


@pytest.mark.asyncio
async def test_usage_command_text_and_json_report_rollups():
    plugin, bus = await _make({"plugins.usage_ledger.lane": "issue-723"})
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]
    await post(_post_payload(), None)
    await post(_post_payload(usage_source="none"), None)

    text = await plugin._handle_usage_command(SimpleNamespace(args=[]))
    assert "By agent" in text
    assert "coder" in text
    assert "no usage reported: 1" in text

    data = json.loads(
        await plugin._handle_usage_command(SimpleNamespace(args=["json"]))
    )
    assert data["turns"] == 2
    assert data["turns_without_usage"] == 1
    (lane_agent_model,) = data["by_lane_agent_model"]
    assert lane_agent_model["lane"] == "issue-723"
    assert lane_agent_model["agent"] == "coder"
    assert lane_agent_model["model_key"] == "acme/known-model"
    assert lane_agent_model["tokens_in"] == 1_000_000


@pytest.mark.asyncio
async def test_usage_command_when_off_says_so():
    plugin, _ = await _make({"plugins.usage_ledger.enabled": False})
    text = await plugin._handle_usage_command(SimpleNamespace(args=[]))
    assert "off" in text


@pytest.mark.asyncio
async def test_budget_alert_is_shown_once_and_recorded():
    renderer = MagicMock()
    renderer.pipe_mode = False
    rules = [
        {"scope": "lane", "name": "issue-723", "threshold_usd": 1.0, "window_hours": 24}
    ]
    plugin, bus = await _make(
        {
            "plugins.usage_ledger.lane": "issue-723",
            "plugins.usage_ledger.budget_alerts": rules,
        },
        renderer=renderer,
    )
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    await post(_post_payload(), None)  # $2.00 crosses the $1.00 threshold
    await post(_post_payload(), None)  # same window: must stay silent

    display = renderer.message_coordinator.display_message_sequence
    assert display.call_count == 1
    (messages,) = display.call_args.args
    kind, text, meta = messages[0]
    assert kind == "system"
    assert "issue-723" in text
    assert meta == {"display_type": "warning"}

    alerts = [r for r in _ledger_rows(plugin) if r["kind"] == "budget_alert"]
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_alert_display_failure_does_not_raise():
    renderer = MagicMock()
    renderer.pipe_mode = False
    renderer.message_coordinator.display_message_sequence.side_effect = RuntimeError
    rules = [
        {"scope": "agent", "name": "coder", "threshold_usd": 1.0, "window_hours": 24}
    ]
    plugin, bus = await _make(
        {"plugins.usage_ledger.budget_alerts": rules}, renderer=renderer
    )
    post = bus.callback_for(EventType.LLM_REQUEST_POST)[0]

    data = _post_payload()
    assert await post(data, None) is data
