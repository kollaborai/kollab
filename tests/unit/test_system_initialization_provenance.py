"""Initialization provenance is display metadata, never inferred from prose."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.session_manager import SessionManager
from kollabor.state.local import LocalStateService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import (
    ConversationManager,
    matches_system_initialization_provenance,
)
from kollabor_events.data_models import ConversationMessage

PROMPT = "## hub collaboration\nKeep this raw text.\n\n  Indentation and \u03bb matter.\n"


def configuration(root):
    return SimpleNamespace(
        _conversations_dir=root,
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )


def record(identifier, *, intelligence=None, role="user"):
    return {
        "type": role,
        "uuid": identifier,
        "timestamp": "2026-10-14T00:00:00Z",
        "message": {"role": role, "content": PROMPT},
        "kollabor_intelligence": intelligence,
    }


def write_records(path, *records):
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n")


@pytest.mark.asyncio
async def test_fresh_initialization_and_identical_human_text_keep_their_provenance(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    logger = KollaborConversationLogger(tmp_path)
    # Exercise the real logger without repository discovery or Git subprocesses.
    monkeypatch.setattr(logger, "_get_git_branch", lambda: "owned-test")
    monkeypatch.setattr(logger, "_get_working_directory", lambda: str(tmp_path))
    monkeypatch.setattr(logger, "_get_project_awareness", lambda: {})
    manager = ConversationManager(config, logger)
    llm = LLMService.__new__(LLMService)
    llm.conversation_manager = manager
    llm.conversation_history = []
    session = SessionManager(logger, manager, config, None, None, SimpleNamespace(build=lambda: PROMPT))

    parent = await session.initialize_conversation(llm.conversation_history, llm._add_conversation_message)
    llm._add_conversation_message(ConversationMessage(role="user", content=PROMPT))
    await logger.log_user_message(PROMPT, parent_uuid=parent)
    snapshot = await LocalStateService(llm, None).get_conversation()

    assert parent
    assert [(message.role, message.content) for message in snapshot.messages] == [
        ("system", PROMPT), ("user", PROMPT),
    ]
    assert snapshot.messages[0].metadata == {"system_initialization": True}
    assert snapshot.messages[1].metadata == {}
    logged = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
    assert [(item["type"], item["message"]["role"], item["message"]["content"]) for item in logged] == [
        ("user", "user", PROMPT), ("user", "user", PROMPT),
    ]
    assert logged[0]["kollabor_intelligence"]["user_context"]["type"] == "system_initialization"

    restored = ConversationManager(config)
    assert restored.load_session(logger.session_id)
    assert [(message["role"], message["content"]) for message in restored.messages] == [
        ("user", PROMPT), ("user", PROMPT),
    ]
    assert restored.messages[0]["metadata"] == {"system_initialization": True}
    assert restored.messages[1]["metadata"] == {}


def test_existing_typed_jsonl_initialization_keeps_marker_through_saved_base_and_tail(tmp_path):
    config = configuration(tmp_path)
    origin = tmp_path / "original.jsonl"
    write_records(
        origin,
        record("initial", intelligence={"user_context": {"type": "system_initialization"}}),
        record("human", intelligence={"user_context": {"type": "message"}}),
    )
    manager = ConversationManager(config)
    assert manager.load_session("original")
    initial, human = manager.messages
    assert initial["uuid"] == "initial" and initial["role"] == "user" and initial["content"] == PROMPT
    assert initial["metadata"] == {"system_initialization": True}
    assert human["uuid"] == "human" and human["content"] == PROMPT and human["metadata"] == {}

    assert manager.save_session("resumed")
    with (tmp_path / "resumed.jsonl").open("a") as stream:
        stream.write(json.dumps(record("later-human")) + "\n")
    restored = ConversationManager(config)
    assert restored.load_session("resumed")
    assert [message["uuid"] for message in restored.messages] == ["initial", "human", "later-human"]
    assert [message["content"] for message in restored.messages] == [PROMPT] * 3
    assert [message["role"] for message in restored.messages] == ["user"] * 3
    assert [message["metadata"] for message in restored.messages] == [{"system_initialization": True}, {}, {}]


@pytest.mark.parametrize("intelligence", [
    None, "system_initialization", [],
    {"user_context": None}, {"user_context": "system_initialization"},
    {"user_context": {"type": "system_initialization_extra"}},
    {"user_context": {"project_context_loaded": True}},
])
def test_untyped_or_malformed_provenance_never_reclassifies_user_prose(tmp_path, intelligence):
    path = tmp_path / "human.jsonl"
    write_records(path, record("human", intelligence=intelligence))
    data = ConversationManager(configuration(tmp_path))._load_from_jsonl(path)
    assert [(message["role"], message["content"], message["metadata"]) for message in data["messages"]] == [
        ("user", PROMPT, {}),
    ]


def test_initialization_user_context_on_an_assistant_does_not_mark_it(tmp_path):
    path = tmp_path / "assistant.jsonl"
    write_records(path, record(
        "assistant", role="assistant", intelligence={"user_context": {"type": "system_initialization"}},
    ))
    data = ConversationManager(configuration(tmp_path))._load_from_jsonl(path)
    assert data["messages"][0]["metadata"] == {}


def test_lost_saved_base_metadata_matches_exact_original_identity_without_mutating_inputs():
    displayed = {"role": "user", "content": PROMPT, "metadata": {}}
    saved = {"uuid": "original-init", **displayed}
    logged = record("original-init", intelligence={"user_context": {"type": "system_initialization"}})
    before = deepcopy((displayed, saved, logged))
    assert matches_system_initialization_provenance(displayed, saved, logged)
    assert (displayed, saved, logged) == before


@pytest.mark.parametrize("change", [
    "different-original-uuid", "missing-saved-uuid", "missing-original-type",
    "different-original-content", "different-displayed-content", "different-saved-content",
    "different-displayed-role", "different-saved-role", "different-original-role",
    "malformed-original-message", "malformed-saved-message",
])
def test_old_session_recovery_rejects_identity_content_role_and_provenance_mismatches(change):
    displayed = {"role": "user", "content": PROMPT, "metadata": {}}
    saved = {"uuid": "original-init", **displayed}
    logged = record("original-init", intelligence={"user_context": {"type": "system_initialization"}})
    if change == "different-original-uuid":
        logged["uuid"] = "real-human-same-text"
    elif change == "missing-saved-uuid":
        saved.pop("uuid")
    elif change == "missing-original-type":
        logged["kollabor_intelligence"] = {}
    elif change == "different-original-content":
        logged["message"]["content"] += " changed"
    elif change == "different-displayed-content":
        displayed["content"] += " changed"
    elif change == "different-saved-content":
        saved["content"] += " changed"
    elif change == "different-displayed-role":
        displayed["role"] = "assistant"
    elif change == "different-saved-role":
        saved["role"] = "assistant"
    elif change == "different-original-role":
        logged["message"]["role"] = "assistant"
    elif change == "malformed-original-message":
        logged["message"] = None
    elif change == "malformed-saved-message":
        saved = None
    assert not matches_system_initialization_provenance(displayed, saved, logged)
