"""Characterize issue #901's staged-compaction streaming reload gap.

This is an intentionally synthetic reproduction, not the desired persistence
contract: with no later complete saved base, the staged in-memory history has
all four markers, while the streaming JSONL reload exposes the mocked
summary without those markers. A future fix should change these negative
assertions to the desired retention contract.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "packages/kollabor-ai/src"))

from kollabor_ai.conversation_logger import (  # noqa: E402
    KollaborConversationLogger,
)
from kollabor_ai.conversation_manager import ConversationManager  # noqa: E402
from plugins.context_compaction_plugin import (  # noqa: E402
    ContextCompactionPlugin,
)


@dataclass
class _Message:
    role: str = "user"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str | None = None


async def _exercise_compaction_reload(conversations_dir: Path) -> None:
    logger = KollaborConversationLogger(conversations_dir)
    logger.reset_session("synthetic")

    objective = "OBJECTIVE-CANARY: finish synthetic context handoff audit"
    task = "[hub channel: koordinator -> worker]\nTask: NEXT-CANARY: inspect rebirth handoff."
    recent_user = "TAIL-CANARY: last user decision before compaction"
    recent_assistant = "TAIL-ASSISTANT-CANARY: current status before compaction"
    canaries = (
        "OBJECTIVE-CANARY",
        "NEXT-CANARY",
        "TAIL-CANARY",
        "TAIL-ASSISTANT-CANARY",
    )
    provider_summary = "Synthetic summary intentionally omits all handoff canaries. " * 4

    first_timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)
    records = (
        ("u-objective", "user", objective),
        ("u-task", "user", task),
        ("u-tail", "user", recent_user),
        ("a-tail", "assistant", recent_assistant),
    )
    objective_timestamp = first_timestamp.isoformat()
    for offset, (uuid, role, content) in enumerate(records):
        await logger._append_to_jsonl(
            {
                "type": role,
                "uuid": uuid,
                "timestamp": (first_timestamp + timedelta(seconds=offset)).isoformat(),
                "message": {"role": role, "content": content},
            }
        )

    card = SimpleNamespace(id="task-synthetic", status="active", assignee="worker")

    class _Ledger:
        def get_active_for(self, identity: str) -> list[Any]:
            return [card] if identity == "worker" else []

        def get_all(self, status: str | None = None) -> list[Any]:
            return []

        def get_open(self) -> list[Any]:
            return []

    hub = SimpleNamespace(
        _task_ledger=_Ledger(),
        _identity=SimpleNamespace(identity="worker"),
    )
    event_bus = SimpleNamespace(get_service=lambda name: hub if name == "hub_plugin" else None)
    config_values = {
        "plugins.context_compaction.keep_recent": 2,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": True,
    }
    config = SimpleNamespace(get=lambda key, default=None: config_values.get(key, default))
    plugin = ContextCompactionPlugin("context_compaction", event_bus, SimpleNamespace(), config)
    history = [
        _Message("system", "synthetic system"),
        _Message(
            "user",
            objective,
            {
                "operator_inputs": [
                    {
                        "source": "user",
                        "timestamp": objective_timestamp,
                        "text": objective,
                    }
                ]
            },
            objective_timestamp,
        ),
        _Message(
            "user",
            task,
            {
                "hub_message": True,
                "hub_is_intended": True,
                "task_id": "task-synthetic",
                "hub_from": "koordinator",
            },
        ),
        _Message("user", recent_user),
        _Message("assistant", recent_assistant),
    ]
    plugin._llm_service = SimpleNamespace(conversation_history=history)
    plugin._conversation_logger = logger
    plugin._call_summarization_llm = AsyncMock(return_value=provider_summary)

    await plugin._run_compaction()

    staged_history = plugin._pending_compaction
    assert staged_history is not None
    staged_text = "\n".join(message.content for message in staged_history)
    assert all(marker in staged_text for marker in canaries)

    manager = ConversationManager(
        SimpleNamespace(
            _conversations_dir=conversations_dir,
            get=lambda _key, default=None: default,
        )
    )
    assert manager.load_session("synthetic")
    restored_text = "\n".join(message.get("content", "") for message in manager.messages)

    # This scenario has no later save_session() base to recover the old tail.
    written_records = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
    assert not any(isinstance(record.get("messages"), list) for record in written_records)
    assert "Synthetic summary intentionally omits all handoff canaries." in restored_text

    # Check each marker independently: a single all(...) check could conceal
    # which staged state was lost during streaming-only reload.
    for marker in canaries:
        assert marker not in restored_text


def test_streaming_reload_drops_staged_compaction_markers_without_saved_base(
    tmp_path: Path,
) -> None:
    """Reproduce the current issue using only temporary synthetic session data."""
    asyncio.run(_exercise_compaction_reload(tmp_path / "conversations"))
