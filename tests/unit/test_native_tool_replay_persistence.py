"""Tool display data survives actual JSONL logging and daemon resume."""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kollabor.state.local import LocalStateService
from kollabor_agent.queue_processor import _xml_tool_result_entry
from kollabor_agent.tool_executor import ToolExecutionResult
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_engine.history_xml_tools import web_history
from kollabor_events import EventBus


def manager(directory):
    config = SimpleNamespace(
        _conversations_dir=directory,
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )
    return ConversationManager(config)


async def resume(directory, session_id):
    reader = manager(directory)
    history = []
    state = LocalStateService(
        SimpleNamespace(conversation_manager=reader, conversation_history=history),
        None, event_bus=EventBus(),
    )
    result = await state.resume_conversation(session_id)
    return state, reader, history, result


@pytest.mark.asyncio
async def test_streaming_tool_use_survives_resume_without_model_calls(tmp_path):
    writer = KollaborConversationLogger(tmp_path)
    parent = await writer.log_user_message("Check the file")
    await writer.log_assistant_message("Checking", parent_uuid=parent, tool_calls=[
        {"id": "read-one", "name": "file_read", "input": {"file": "owned.txt"}},
    ], provider_reasoning={"opaque": "cannot restore unmatched native reasoning"})
    _, reader, history, _ = await resume(tmp_path, writer.session_id)
    assert history[1].metadata["xml_tool_calls"] == [
        {"id": "read-one", "name": "file_read", "input": {"file": "owned.txt"}},
    ]
    assert reader.messages[1]["metadata"] == {}
    provider = APICommunicationService.__new__(APICommunicationService)
    assert provider._prepare_messages(history) == [
        {"role": "user", "content": "Check the file"},
        {"role": "assistant", "content": "Checking"},
    ]


@pytest.mark.asyncio
async def test_legacy_result_uses_explicit_owner_and_id_without_parsing_prose(tmp_path):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Run two turns")
    owners = [await writer.log_assistant_message(label, parent_uuid="root", tool_calls=[
        {"id": "reused", "name": "terminal", "input": {"command": label}},
    ]) for label in ("first", "second")]
    # Results can arrive out of record order; their parent UUID owns the card.
    for parent, text in zip(reversed(owners), ("second result", "first result")):
        await writer.log_system_message(text, parent_uuid=parent, subtype="tool_result", tool_use_id="reused")
    await writer.log_system_message("unowned", parent_uuid="missing", subtype="tool_result", tool_use_id="reused")
    reader = manager(tmp_path)
    assert reader.load_session(writer.session_id)
    batches = [row for row in reader.messages if row["metadata"].get("tool_output_batch")]
    assert [row["parent_uuid"] for row in batches] == owners
    assert [row["metadata"]["xml_tool_results"] for row in batches] == [
        [{"id": "reused", "content": "first result"}],
        [{"id": "reused", "content": "second result"}],
    ]
    assert [row["content"] for row in reader.messages] == [
        "Run two turns", "first", "Tool result: first result", "second", "Tool result: second result",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_structured_result_diff_and_error_round_trip_to_history(tmp_path, success):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Edit the file")
    parent = await writer.log_assistant_message("Editing", parent_uuid="root", tool_calls=[
        {"id": "write-one", "name": "file_edit", "input": {"file": "owned.txt"}},
    ])
    diffs = [{"path": str(tmp_path / "owned.txt"), "old_text": "before", "new_text": "after"}]
    execution = ToolExecutionResult(
        "write-one", "file_edit", success, output="saved", error="denied", execution_time=0.125,
        metadata={"diffs": diffs, "tool_output_path": "output.json", "irrelevant_private_state": "private"},
    )
    entry = _xml_tool_result_entry(execution, "saved" if success else "denied")
    # Incidental copied metadata cannot override the result's ID/status/time.
    entry["metadata"].update({"tool_call_id": "wrong", "is_error": success, "tool_execution_time": 99})
    await writer.log_system_message(
        "Original human-readable result", parent_uuid=parent, subtype="tool_result",
        tool_use_id="write-one", tool_result=entry,
    )
    persisted = json.loads(writer.session_file.read_text().splitlines()[-1])
    assert persisted["content"] == "Original human-readable result"
    assert persisted["tool_result"] == entry
    state, reader, history, result = await resume(tmp_path, writer.session_id)
    assert all("tool_calls" not in message.metadata for message in history)
    assert all(message.role != "tool" for message in history)
    provider = APICommunicationService.__new__(APICommunicationService)
    assert all("tool_call_id" not in row and "tool_calls" not in row for row in provider._prepare_messages(history))
    projected = web_history([asdict(row) for row in (await state.get_conversation()).messages])
    card = next(row for row in projected if row["role"] == "tool")
    assert card["metadata"]["tool_call_id"] == "write-one"
    assert card["metadata"]["tool_execution_time"] == 0.125
    assert card["metadata"]["diffs"] == diffs
    assert bool(card["metadata"].get("is_error")) is not success
    assert "irrelevant_private_state" not in card["metadata"]
    assert [(row["role"], row["content"]) for row in result["messages"]] == [
        ("user", "Edit the file"), ("assistant", "Editing"),
    ]
    # The automatically persisted resumed base must survive a second load.
    second = manager(tmp_path)
    assert second.load_session(result["new_session_id"])
    assert second.messages == reader.messages


@pytest.mark.asyncio
async def test_compaction_cannot_restore_a_superseded_calls_late_result(tmp_path):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Old task")
    old = await writer.log_assistant_message("Old call", parent_uuid="root", tool_calls=[
        {"id": "reused", "name": "terminal", "input": {"command": "old"}},
    ])
    await writer._append_to_jsonl({
        "type": "context_compaction", "uuid": "summary", "content": "Compacted",
        "timestamp": datetime.now(timezone.utc).isoformat(), "compaction_round": 1,
    })
    await writer.log_system_message("Old late output", parent_uuid=old, subtype="tool_result", tool_use_id="reused")
    await writer.log_user_message("New task")
    new = await writer.log_assistant_message("New call", parent_uuid="root", tool_calls=[
        {"id": "reused", "name": "terminal", "input": {"command": "new"}},
    ])
    await writer.log_system_message("New output", parent_uuid=new, subtype="tool_result", tool_use_id="reused")
    reader = manager(tmp_path)
    assert reader.load_session(writer.session_id)
    assert [row["content"] for row in reader.messages] == [
        "Compacted", "New task", "New call", "Tool result: New output",
    ]


@pytest.mark.asyncio
async def test_resumed_base_can_be_loaded_again_under_its_new_conversation_id(tmp_path):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Original question")
    await writer.log_assistant_message("Original answer", parent_uuid="root")
    original_file = writer.session_file
    original_bytes = original_file.read_bytes()
    _, _, _, restored = await resume(tmp_path, writer.session_id)
    new_id = restored["new_session_id"]
    assert new_id != writer.session_id
    reader = manager(tmp_path)
    assert reader.load_session(new_id)
    assert (tmp_path / f"{new_id}.jsonl").stat().st_mode & 0o077 == 0
    assert [message["content"] for message in reader.messages] == ["Original question", "Original answer"]
    assert original_file.read_bytes() == original_bytes
    _, _, twice, _ = await resume(tmp_path, new_id)
    assert [message.content for message in twice] == ["Original question", "Original answer"]


@pytest.mark.asyncio
async def test_saved_base_and_appended_streaming_turns_are_both_restored(tmp_path):
    base = manager(tmp_path)
    base.add_message("user", "Base question")
    base.add_message("assistant", "Base answer")
    assert base.save_session("continued")
    writer = KollaborConversationLogger(tmp_path)
    writer.reset_session("continued")
    await writer.log_user_message("Later question")
    await writer.log_assistant_message("Later answer", parent_uuid="root")
    reader = manager(tmp_path)
    assert reader.load_session("continued")
    assert [message["content"] for message in reader.messages] == [
        "Base question", "Base answer", "Later question", "Later answer",
    ]
    assert reader.conversation_metadata["message_count"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["end_turn", "cancelled", "error"])
async def test_acp_card_updates_and_stop_reason_survive_resume_as_display_only(tmp_path, stop_reason):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Harness request")
    start = {"sessionUpdate": "tool_call", "toolCallId": "vendor-one", "title": "Edit file",
             "kind": "edit", "status": "pending", "rawInput": {"path": "owned.txt", "content": "after"}}
    finish = {"sessionUpdate": "tool_call_update", "toolCallId": "vendor-one", "title": "Edited file",
              "status": "completed", "rawOutput": {"message": "saved"},
              "content": [{"type": "diff", "path": "owned.txt", "oldText": "before", "newText": "after"}]}
    calls = [{"id": "vendor-one", "name": "Edited file", "input": start["rawInput"], "status": "completed",
              "result": finish["rawOutput"], "metadata": {"acp": {**start, **finish}}}]
    display = {"provider": "acp", "acp_updates": [
        {"kind": "tool_call", "payload": start}, {"kind": "tool_call_update", "payload": finish},
        {"kind": "text", "text": "Harness answer"},
    ], "tool_calls": calls, "stop_reason": stop_reason}
    await writer.log_assistant_message(
        "Harness answer", parent_uuid="root", tool_calls=calls,
        display_metadata={**display, "credentials": "must not enter display metadata"},
    )
    record = json.loads(writer.session_file.read_text().splitlines()[-1])
    assert record["display_metadata"] == display
    state, _, history, restored = await resume(tmp_path, writer.session_id)
    assert history[1].metadata == display
    assert web_history([asdict(row) for row in (await state.get_conversation()).messages])[1]["metadata"] == display
    _, _, reopened, _ = await resume(tmp_path, restored["new_session_id"])
    assert reopened[1].metadata == display
    provider = APICommunicationService.__new__(APICommunicationService)
    assert provider._prepare_messages(reopened) == [
        {"role": "user", "content": "Harness request"},
        {"role": "assistant", "content": "Harness answer"},
    ]


@pytest.mark.asyncio
async def test_failed_resume_persistence_does_not_report_success_or_overwrite_source(tmp_path, monkeypatch):
    writer = KollaborConversationLogger(tmp_path)
    await writer.log_user_message("Keep original")
    original = writer.session_file.read_bytes()
    reader = manager(tmp_path)
    monkeypatch.setattr(reader, "save_session", lambda _: False)
    state = LocalStateService(SimpleNamespace(conversation_manager=reader, conversation_history=[]),
                              None, event_bus=EventBus())
    with pytest.raises(ValueError, match="persist resumed session"):
        await state.resume_conversation(writer.session_id)
    assert writer.session_file.read_bytes() == original


@pytest.mark.asyncio
async def test_appended_turns_restore_their_latest_usage_snapshot(tmp_path):
    base = manager(tmp_path)
    base.bind_session_stats({"messages": 1, "input_tokens": 11})
    base.add_message("user", "Base")
    assert base.save_session("continued")
    writer = KollaborConversationLogger(tmp_path)
    writer.reset_session("continued")
    await writer.log_user_message("Later")
    current = manager(tmp_path)
    assert current.load_session("continued")
    current.bind_session_stats({"messages": 2, "input_tokens": 22})
    current.save_conversation()
    counters = {}
    reader = manager(tmp_path)
    reader.bind_session_stats(counters)
    assert reader.load_session("continued")
    assert counters["messages"] == 2 and counters["input_tokens"] == 22


@pytest.mark.asyncio
@pytest.mark.parametrize("matching", ["uuid", "parent"])
async def test_old_acp_snapshot_restores_only_matching_display_metadata(tmp_path, matching):
    writer = KollaborConversationLogger(tmp_path)
    snapshot = manager(tmp_path)
    snapshot.current_session_id = writer.session_id
    parent = snapshot.add_message("user", "Snapshot question")
    await writer.log_user_message("Log question")
    payload = {"sessionUpdate": "tool_call_update", "toolCallId": "old-card", "title": "Edited file",
               "kind": "edit", "status": "completed", "rawInput": {"path": "owned.txt"},
               "rawOutput": {"saved": True},
               "content": [{"type": "diff", "path": "owned.txt", "oldText": "before", "newText": "after"}]}
    display = {"provider": "acp", "stop_reason": "cancelled",
               "acp_updates": [{"kind": "tool_call_update", "payload": payload}],
               "tool_calls": [{"id": "old-card", "name": "Edited file", "input": {"path": "owned.txt"},
                               "status": "completed", "result": {"saved": True}, "metadata": {"acp": payload}}]}
    snapshot_uuid = snapshot.add_message("assistant", "Snapshot text is not authoritative", parent_uuid=parent,
                                        metadata={**display, "private_binding": "must not copy"})
    logged_uuid = await writer.log_assistant_message("Log answer", parent_uuid=parent, tool_calls=display["tool_calls"])
    assert snapshot_uuid != logged_uuid
    if matching == "uuid":
        snapshot.messages[-1]["uuid"] = logged_uuid
        snapshot.messages[-1]["parent_uuid"] = "different-parent"
    snapshot.add_message("assistant", "Snapshot-only turn", parent_uuid="not-in-log", metadata=display)
    snapshot_path = snapshot.save_conversation()
    await writer.log_user_message("Newer log question")
    await writer.log_assistant_message("Newer log answer", parent_uuid="newer-parent")
    original_log, original_snapshot = writer.session_file.read_bytes(), snapshot_path.read_bytes()
    state, reader, history, result = await resume(tmp_path, writer.session_id)
    assert [message.content for message in history] == [
        "Log question", "Log answer", "Newer log question", "Newer log answer",
    ]
    assert history[1].metadata == display
    assert reader.messages[1]["uuid"] == logged_uuid
    assert history[-1].metadata == {}
    assert writer.session_file.read_bytes() == original_log and snapshot_path.read_bytes() == original_snapshot
    provider = APICommunicationService.__new__(APICommunicationService)
    assert all("tool_calls" not in row for row in provider._prepare_messages(history))
    _, _, reopened, _ = await resume(tmp_path, result["new_session_id"])
    assert reopened[1].metadata == display


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguity", ["snapshot", "log", "missing"])
async def test_snapshot_recovery_does_not_guess_ambiguous_or_missing_parents(tmp_path, ambiguity):
    writer = KollaborConversationLogger(tmp_path)
    snapshot = manager(tmp_path)
    snapshot.current_session_id = writer.session_id
    parent = None if ambiguity == "missing" else "shared-parent"
    display = {"provider": "acp", "stop_reason": "error", "tool_calls": [{"id": "card", "name": "Edit"}]}
    snapshot.add_message("assistant", "Identical text", parent_uuid=parent, metadata=display)
    if ambiguity == "snapshot":
        snapshot.add_message("assistant", "Identical text", parent_uuid=parent, metadata=display)
    snapshot.save_conversation()
    await writer.log_assistant_message("Identical text", parent_uuid=parent)
    if ambiguity == "log":
        await writer.log_assistant_message("Identical text", parent_uuid=parent)
    reader = manager(tmp_path)
    assert reader.load_session(writer.session_id)
    assert all(row["metadata"] == {} for row in reader.messages)


@pytest.mark.asyncio
async def test_current_log_display_metadata_wins_over_stale_snapshot(tmp_path):
    writer = KollaborConversationLogger(tmp_path)
    snapshot = manager(tmp_path)
    snapshot.current_session_id = writer.session_id
    parent = "shared-parent"
    snapshot.add_message("assistant", "Older", parent_uuid=parent,
                         metadata={"provider": "acp", "stop_reason": "end_turn", "tool_calls": [{"id": "old"}]})
    snapshot.save_conversation()
    display = {"provider": "acp", "stop_reason": "error", "acp_updates": [], "tool_calls": []}
    await writer.log_assistant_message("Current", parent_uuid=parent, display_metadata=display)
    reader = manager(tmp_path)
    assert reader.load_session(writer.session_id)
    assert reader.messages[0]["metadata"] == display
