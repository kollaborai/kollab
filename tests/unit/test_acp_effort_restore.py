"""Loadout model and effort win over saved picks, a model that did not switch gets no effort,
and every refusal reaches the chat."""

import json
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_orchestrator import ACPOrchestrator


def _options(model_values=("haiku", "sonnet"), effort_values=("high", "xhigh", "max")):
    model = {"id": "model", "category": "model", "type": "select", "currentValue": "sonnet",
             "options": [{"value": v} for v in model_values]}
    effort = {"id": "effort", "category": "thought_level", "type": "select", "currentValue": "xhigh",
              "options": [{"value": v} for v in effort_values]}
    return [model, effort]


def _orchestrator(tmp_path, options, saved=None):
    class Runner:
        def __init__(self):
            self.calls = []

        def advertised_config_options(self):
            return [dict(o) for o in options]

        async def set_config_option(self, config_id, value):
            option = next(o for o in options if o["id"] == config_id)
            offered = [c["value"] for c in option["options"]]
            if value not in offered:
                raise RuntimeError(f"ACP config value is not advertised: {config_id}={value}")
            option["currentValue"] = value
            self.calls.append((config_id, value))
            return self.advertised_config_options()

    orch = ACPOrchestrator.__new__(ACPOrchestrator)
    orch._runner, orch._closed = Runner(), False
    orch._profile_name = "claude-code"
    orch._options_path = tmp_path / "options.json"
    if saved is not None:
        orch._options_path.write_text(json.dumps({"claude-code": saved}))
    orch.restore_notices = []
    orch.event_bus = None
    orch._binding_conversation_id = None
    return orch


LOADOUT = SimpleNamespace(name="claude-code", model="haiku", effort="max")


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [
    {"model": "sonnet", "effort": "xhigh"},
    {"effort": "xhigh", "model": "sonnet"},
])
async def test_loadout_wins_and_saved_model_and_effort_are_never_sent(tmp_path, saved):
    options = _options()
    orch = _orchestrator(tmp_path, options, saved=saved)
    notices = await orch._restore_options(LOADOUT)
    assert notices == []
    assert orch._runner.calls == [("model", "haiku"), ("effort", "max")]


@pytest.mark.asyncio
async def test_rejected_model_gets_no_effort_and_says_why(tmp_path):
    options = _options(model_values=("sonnet",))
    orch = _orchestrator(tmp_path, options)
    notices = await orch._restore_options(LOADOUT)
    assert orch._runner.calls == []
    assert any("model haiku was not applied" in n for n in notices)
    assert any("effort max was not applied because the model is not active" in n for n in notices)
    assert options[1]["currentValue"] == "xhigh"


@pytest.mark.asyncio
async def test_unoffered_effort_is_named_with_the_choices(tmp_path):
    options = _options(effort_values=("high", "xhigh"))
    orch = _orchestrator(tmp_path, options)
    notices = await orch._restore_options(LOADOUT)
    assert orch._runner.calls == [("model", "haiku")]
    assert any("effort max is not offered by this model (available: high, xhigh)" in n for n in notices)


@pytest.mark.asyncio
async def test_each_notice_is_shown_in_the_chat(tmp_path):
    options = _options(model_values=("sonnet",))
    orch = _orchestrator(tmp_path, options)
    orch.restore_notices = await orch._restore_options(LOADOUT)

    shown = []

    class Display:
        def display_system_message(self, line):
            shown.append(line)

    class Llm:
        message_display_service = Display()

    class Bus:
        def get_service(self, name):
            return Llm() if name == "llm_service" else None

    orch.event_bus = Bus()
    orch._announce_restore_notices()
    assert shown and all(line.startswith("ACP setting not applied: ") for line in shown)
    assert any("model haiku was not applied" in line for line in shown)


def test_binding_can_be_put_back_for_a_rollback(tmp_path):
    orch = _orchestrator(tmp_path, _options())
    orch._binding_conversation_id = "conv-1"
    saved = []

    class Store:
        def save(self, conversation_id, binding):
            saved.append((conversation_id, binding))

    orch._bindings = Store()
    assert orch.binding_conversation_id == "conv-1"
    orch.save_binding_for("conv-1", {"session": "prior"})
    assert saved == [("conv-1", {"session": "prior"})]
