import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_definitions.hub import hub_msg
from tests.unit.test_relay_agent_bridge import (
    address,
    allow,
    authorize,
    bridges,
    in_turn,
)


@pytest.mark.asyncio
async def test_ready_contact_context_contains_exact_initial_message_arguments(bridges):
    members, _wire = bridges
    (sender, _sender_hub, _sender_model, _sender_bus), (
        receiver,
        _receiver_hub,
        _receiver_model,
        _receiver_bus,
    ) = members
    allow(sender, receiver)
    purpose = 'Create "field-notes.txt" with this exact body:\n“quoted” and café'
    grant = authorize(sender, receiver, purpose)

    lines = await sender.harness_context()
    text = "\n".join(lines)
    prefix = "Exact hub_msg arguments: "
    argument_lines = [line for line in lines if line.startswith(prefix)]

    assert len(argument_lines) == 1
    encoded = argument_lines[0][len(prefix) :]
    assert "\\n" in encoded and '\\"' in encoded
    arguments = json.loads(encoded)
    assert arguments == {
        "to": address(receiver),
        "kind": "message",
        "thread_id": grant["id"],
        "message": purpose,
    }
    assert argument_lines[0].endswith('"}')
    assert "The destination agent follows the instructions inside message" in text
    assert "Use kind='message', not kind='question'" in text
    assert f"Please ask {address(receiver)} to {purpose}" not in text
    assert "message=<the exact human request>" not in text

    from kollabor_agent.tool_call_contract import normalize_native_tool_call
    from kollabor_agent.tool_executor import ToolExecutor

    executor = ToolExecutor(None, _sender_bus, workspace=sender.workspace)
    _sender_bus.register_service(
        "response_parser", SimpleNamespace(register_plugin_tag=MagicMock())
    )
    _sender_bus.register_service("tool_executor", executor)
    _sender_hub._register_pipeline_tools()
    native_call = normalize_native_tool_call(
        {"id": "context-arguments", "name": "hub_msg", "input": arguments},
        plugin_handler_names=set(executor.plugin_handlers),
    )
    result = await executor.execute_tool(native_call)

    assert result.success, result.error
    task = receiver.store.task(grant["id"], room=receiver.commands.client.state.room)
    assert task is not None and task["state"] == "queued"
    assert task["payload"]["content"] == purpose


@pytest.mark.asyncio
async def test_question_guidance_is_scoped_to_the_active_receiving_task(bridges):
    members, _wire = bridges
    (sender, _sender_hub, _sender_model, _sender_bus), (
        receiver,
        _receiver_hub,
        receiver_model,
        _receiver_bus,
    ) = members
    allow(sender, receiver)
    grant = authorize(sender, receiver, "Ask the human for the exact report title.")
    sent = await sender.send(
        address(receiver),
        grant["purpose"],
        thread_id=grant["id"],
        grant_id=grant["id"],
    )
    assert sent["state"] == "queued"

    await receiver._tick()
    lines = await in_turn(receiver_model, receiver.harness_context())
    text = "\n".join(lines)

    assert (
        f"Active authenticated remote request: {grant['id']} from {address(sender)}."
        in text
    )
    assert "As the receiving agent in this active task" in text
    assert "one bounded clarification with kind='question'" in text
    assert "waits for the human-approved answer" in text
    assert "Do not use kind='question' to start remote contact" in text
    assert "Send a relay answer only after the human supplies it" in text
    assert "do not poll with hub_status, hub_capture or cron jobs" in text
    # Live run 6872e251: the receiver model guessed the thread_id from address
    # fragments; the exact arguments name the active task.
    assert (
        '"to":"' + address(sender) + '","kind":"question","thread_id":"'
        + grant["id"] + '"'
    ) in text


def test_hub_msg_tool_description_separates_initial_question_and_answer_roles():
    message = next(
        parameter for parameter in hub_msg.parameters if parameter.name == "message"
    )
    thread_id = next(
        parameter for parameter in hub_msg.parameters if parameter.name == "thread_id"
    )
    kind = next(
        parameter for parameter in hub_msg.parameters if parameter.name == "kind"
    )
    guidance = " ".join(hub_msg.key_rules)

    assert "Start a remote task with kind='message'" in hub_msg.description
    assert "Only a receiver inside an active remote task" in hub_msg.description
    assert "exact pending question" in hub_msg.description
    assert "exactly the stored human-authorized purpose" in message.description
    assert "exact human-issued grant ID" in thread_id.description
    assert "Only a receiver inside an active remote task" in kind.description
    assert "start a remote task with kind='message'" in guidance
    assert "use kind='question' only as the receiver" in guidance
