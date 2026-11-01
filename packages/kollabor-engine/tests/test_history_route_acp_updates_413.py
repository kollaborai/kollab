"""Issue #482: a 472 KB acp_updates reply must page, never 413.

One Claude ACP reply stores every streaming update in metadata["acp_updates"]
(acp_orchestrator.py). The /history handler maps HistoryPageTooLarge to
HTTP 413, which the web UI shows as "Couldn't load older messages". The
route here is the same handler FastAPI serves; before the fix the first
page alone raised because the truncated message kept the full metadata.
"""

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from kollabor_engine.history_paging import HISTORY_MAX_PAGE_BYTES
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.server import get_session_registry
from kollabor_engine.session import EngineSession


def _wire_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


class SilentState:
    def get_permission_state(self):
        return SimpleNamespace(approval_mode="CONFIRM_ALL")

    def get_active_profile(self):
        return SimpleNamespace(name="claude-code", model="claude", provider="acp")

    def get_system_info(self):
        return SimpleNamespace(cwd="/nowhere")

    async def get_conversation(self, since=None, anchor=None):
        await asyncio.sleep(3600)


def _daemon(state):
    events: asyncio.Queue = asyncio.Queue()
    return SimpleNamespace(
        alive=True,
        pid=0,
        identity="marco",
        agent_name="",
        state=state,
        subscribe=lambda: events,
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )


@pytest.fixture
def registry():
    sessions = get_session_registry()
    before = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(before)


@pytest.fixture
def conversations(tmp_path, monkeypatch):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    import kollabor_config.config_utils as config_utils

    monkeypatch.setattr(
        config_utils, "get_config_directory", lambda: workspace.parent
    )
    encoded = config_utils.encode_project_path(pathlib.Path(workspace))
    projects_dir = workspace.parent / "projects" / encoded
    (projects_dir / "conversations").mkdir(parents=True)
    return projects_dir / "conversations", str(workspace)


def _write_jsonl(path, messages):
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(
                json.dumps(
                    {
                        "type": message["role"],
                        "message": message,
                        "timestamp": message.get("timestamp", ""),
                        "sessionId": path.stem,
                    }
                )
                + "\n"
            )


def _acp_reply(updates):
    return {
        "role": "assistant",
        "content": "done",
        "timestamp": "t2",
        "metadata": {
            "provider": "acp",
            "profile": "claude-code",
            "acp_updates": updates,
            "stop_reason": "end_turn",
        },
    }


def _seed(conversations_dir, messages, session_id="acp-472"):
    _write_jsonl(conversations_dir / f"{session_id}.jsonl", messages)


def _session(registry, workspace_root, session_id="acp-472"):
    session = EngineSession(
        session_id=session_id, profile=None, workspace=workspace_root
    )
    session.conversation_id = session_id
    session.created_at = None
    session.daemon = _daemon(SilentState())
    session._event_task = None
    registry[session.session_id] = session
    return session


def test_472kb_acp_updates_history_pages_to_the_top_without_413(
    registry, conversations
):
    conversations_dir, workspace_root = conversations
    updates = [{"kind": "text", "text": "x" * 118}] * 4_000
    assert _wire_size(updates) > 471_000
    messages = []
    for index in range(60):
        if index % 29 == 3:
            messages.append(_acp_reply(updates))
        else:
            messages.append(
                {
                    "role": "user" if index % 2 else "assistant",
                    "content": f"turn {index}",
                    "timestamp": f"t{index}",
                    "metadata": {},
                }
            )
    _seed(conversations_dir, messages)
    session = _session(registry, workspace_root)

    async def walk():
        cursor = None
        pages = 0
        seen_ref = None
        while True:
            page = await sessions_route.get_history(
                session.session_id, before=cursor
            )
            pages += 1
            assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
            for message in page["history"]:
                metadata = message.get("metadata") or {}
                if "acp_updates_dropped" in metadata:
                    seen_ref = metadata.get("history_output_ref")
            if not page["has_more_older"]:
                return pages, seen_ref
            cursor = page["older_cursor"]

    pages, seen_ref = asyncio.run(asyncio.wait_for(walk(), timeout=10))
    assert pages > 1
    assert seen_ref  # the giant reply served as preview + output reference


def test_472kb_acp_updates_first_page_drops_updates_keeps_provider(registry, conversations):
    conversations_dir, workspace_root = conversations
    updates = [{"kind": "text", "text": "x" * 118}] * 4_000
    _seed(conversations_dir, [_acp_reply(updates)])
    session = _session(registry, workspace_root)

    page = asyncio.run(
        asyncio.wait_for(sessions_route.get_history(session.session_id), timeout=5)
    )

    reply = page["history"][-1]
    assert "acp_updates" not in reply["metadata"]
    assert reply["metadata"]["acp_updates_dropped"] == 4_000
    assert reply["metadata"]["provider"] == "acp"
    assert reply["content"].startswith("done")
    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES


def test_small_acp_updates_survive_the_route(registry, conversations):
    conversations_dir, workspace_root = conversations
    _seed(
        conversations_dir,
        [_acp_reply([{"kind": "text", "text": "hello"}])],
    )
    session = _session(registry, workspace_root)

    page = asyncio.run(
        asyncio.wait_for(sessions_route.get_history(session.session_id), timeout=5)
    )

    reply = page["history"][-1]
    assert reply["metadata"]["acp_updates"] == [{"kind": "text", "text": "hello"}]
    assert "acp_updates_dropped" not in reply["metadata"]
