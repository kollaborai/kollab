from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


def test_stream_message_written_after_compaction_snapshot_is_replayed() -> None:
    """A stream append during summarization remains after the staged snapshot."""
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_ai.conversation_manager import ConversationManager
    from kollabor_events.data_models import ConversationMessage
    from plugins.context_compaction_plugin import ContextCompactionPlugin

    old_stamp = "2026-01-01T00:00:00+00:00"
    tail_stamp = "2026-01-01T00:00:01+00:00"
    compaction_stamp = "2026-01-01T00:00:02+00:00"
    tail_content = "INTERLEAVED SYNTHETIC TAIL"

    with TemporaryDirectory() as directory:
        conversations_dir = Path(directory)
        config_values = {
            "kollabor.llm.max_context_tokens": 4000,
            "kollabor.llm.save_conversations": False,
            "plugins.context_compaction.keep_recent": 2,
            "plugins.context_compaction.max_summary_tokens": 2000,
            "plugins.context_compaction.log_compaction_events": True,
        }
        config = SimpleNamespace(
            get=lambda key, default=None: config_values.get(key, default),
            _conversations_dir=conversations_dir,
        )
        logger = KollaborConversationLogger(conversations_dir)
        logger.session_id = "synthetic-901-interleaving"
        logger.session_file = conversations_dir / f"{logger.session_id}.jsonl"
        logger._get_working_directory = lambda: "/synthetic"
        logger._get_git_branch = lambda: "synthetic"
        logger._get_session_context = lambda: {}
        logger._get_project_awareness = lambda: {}
        logger._analyze_user_context = lambda _content: {}
        logger._save_conversation_memory = lambda: None

        history = [
            ConversationMessage(role="user", content=f"SYNTHETIC OLD {index}")
            for index in range(6)
        ]
        manager = ConversationManager(config)
        service = LLMService.__new__(LLMService)
        service.conversation_manager = manager
        service.conversation_history = history
        service._is_hidden_voice_message = lambda _message: False
        service.event_bus = None

        plugin = ContextCompactionPlugin(
            "context_compaction", None, SimpleNamespace(), config
        )
        plugin._llm_service = service
        plugin._conversation_logger = logger
        plugin._set_compaction_status = lambda *_args, **_kwargs: None

        async def exercise_interleaving() -> None:
            with patch(
                "kollabor_ai.conversation_logger.utc_stamp",
                return_value=old_stamp,
            ):
                for index in range(6):
                    await logger.log_user_message(f"SYNTHETIC OLD {index}")

            summary_started = asyncio.Event()
            resume_summary = asyncio.Event()

            async def blocked_summary(*_args, **_kwargs) -> str:
                summary_started.set()
                await resume_summary.wait()
                return "SYNTHETIC COMPACTION SUMMARY. " * 10

            plugin._call_summarization_llm = blocked_summary
            compaction = asyncio.create_task(plugin._run_compaction())
            await asyncio.wait_for(summary_started.wait(), timeout=5)

            # This follows the production ordering: durable stream row first,
            # then the in-memory message, while _run_compaction awaits summary.
            with patch(
                "kollabor_ai.conversation_logger.utc_stamp",
                return_value=tail_stamp,
            ):
                await logger.log_user_message(tail_content)
            service._add_conversation_message(
                ConversationMessage(role="user", content=tail_content),
                publish_user_event=False,
            )

            resume_summary.set()
            await compaction

        with patch(
            "plugins.context_compaction_plugin.utc_stamp",
            return_value=compaction_stamp,
        ):
            asyncio.run(exercise_interleaving())

        assert manager.load_session(logger.session_id)
        contents = [message["content"] for message in manager.messages]
        assert any(
            "SYNTHETIC COMPACTION SUMMARY" in content for content in contents
        )
        summary_index = next(
            index
            for index, content in enumerate(contents)
            if "SYNTHETIC COMPACTION SUMMARY" in content
        )
        assert tail_content in contents
        assert summary_index < contents.index(tail_content)


def test_staged_objective_task_and_recent_tail_survive_reload() -> None:
    """The JSONL checkpoint must restore the complete staged snapshot."""
    from datetime import datetime

    from kollabor.llm.llm_coordinator import LLMService
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_ai.conversation_manager import ConversationManager
    from kollabor_events.data_models import ConversationMessage
    from plugins.context_compaction_plugin import (
        END_OPEN_STATE_MARKER,
        OPEN_STATE_MARKER,
        ContextCompactionPlugin,
    )

    old_stamp = "2026-01-01T00:00:00+00:00"
    compaction_stamp = "2026-01-01T00:00:10+00:00"
    task_id = "synthetic-live-task-901"
    objective = "SYNTHETIC_ACTIVE_OBJECTIVE_CANARY"
    operator_words = "SYNTHETIC_OPERATOR_WORDS_CANARY"
    task_body = "SYNTHETIC_LIVE_TASK_BODY_CANARY"
    recent_user = "SYNTHETIC_RECENT_USER_CANARY"
    recent_assistant = "SYNTHETIC_RECENT_ASSISTANT_CANARY"
    summary = (
        "Synthetic model summary deliberately omits independently staged content. " * 3
        + f"\n{OPEN_STATE_MARKER}\n"
        + f"#901 Active objective: {objective}.\n"
        + "#999999 unsupported state must be sanitized.\n"
        + END_OPEN_STATE_MARKER
    )

    class Ledger:
        def get_active_for(self, identity):
            assert identity == "synthetic-agent"
            return [SimpleNamespace(id=task_id, status="active", assignee=identity)]

        def get_all(self, status=None):
            return []

        def get_open(self):
            return []

    hub_plugin = SimpleNamespace(
        _task_ledger=Ledger(),
        _identity=SimpleNamespace(identity="synthetic-agent"),
    )
    event_bus = SimpleNamespace(
        get_service=lambda name: hub_plugin if name == "hub_plugin" else None
    )

    with TemporaryDirectory() as directory:
        conversations_dir = Path(directory)
        config_values = {
            "kollabor.llm.max_context_tokens": 4000,
            "kollabor.llm.save_conversations": False,
            "plugins.context_compaction.keep_recent": 2,
            "plugins.context_compaction.max_summary_tokens": 2000,
            "plugins.context_compaction.log_compaction_events": True,
        }
        config = SimpleNamespace(
            get=lambda key, default=None: config_values.get(key, default),
            _conversations_dir=conversations_dir,
        )
        logger = KollaborConversationLogger(conversations_dir)
        logger.session_id = "synthetic-901-staged"
        logger.session_file = conversations_dir / f"{logger.session_id}.jsonl"

        objective_message = ConversationMessage(
            role="user",
            content=f"Current objective: {objective}; source anchor #901.",
            timestamp=datetime.fromisoformat(old_stamp),
            metadata={
                "operator_inputs": [
                    {
                        "source": "user",
                        "timestamp": old_stamp,
                        "text": operator_words,
                    }
                ]
            },
        )
        task_message = ConversationMessage(
            role="user",
            content=(
                "[hub channel: coordinator -> synthetic-agent]\n"
                f"Assignment: {task_body}. Implement and report back."
            ),
            timestamp=datetime.fromisoformat(old_stamp),
            metadata={
                "hub_message": True,
                "hub_is_intended": True,
                "hub_task_id": task_id,
                "task_id": task_id,
            },
        )
        history = [
            ConversationMessage(
                role="system",
                content="Synthetic system prompt.",
                timestamp=datetime.fromisoformat(old_stamp),
            ),
            objective_message,
            task_message,
            ConversationMessage(
                role="assistant",
                content="Synthetic old assistant context.",
                timestamp=datetime.fromisoformat(old_stamp),
            ),
            ConversationMessage(
                role="user",
                content="Synthetic old user context mentions #901.",
                timestamp=datetime.fromisoformat(old_stamp),
            ),
            ConversationMessage(
                role="assistant",
                content="Synthetic old assistant context continued.",
                timestamp=datetime.fromisoformat(old_stamp),
            ),
            ConversationMessage(
                role="user",
                content=recent_user,
                timestamp=datetime.fromisoformat(old_stamp),
            ),
            ConversationMessage(
                role="assistant",
                content=recent_assistant,
                timestamp=datetime.fromisoformat(old_stamp),
            ),
        ]

        async def seed_stream() -> None:
            for index, message in enumerate(history[1:], 1):
                await logger._append_to_jsonl(
                    {
                        "type": message.role,
                        "uuid": f"synthetic-{index}",
                        "timestamp": old_stamp,
                        "message": {
                            "role": message.role,
                            "content": message.content,
                        },
                    }
                )

        manager = ConversationManager(config)
        service = LLMService.__new__(LLMService)
        service.conversation_manager = manager
        service.conversation_history = history
        service._is_hidden_voice_message = lambda _message: False
        service.event_bus = event_bus

        plugin = ContextCompactionPlugin(
            "context_compaction", event_bus, SimpleNamespace(), config
        )
        plugin._llm_service = service
        plugin._conversation_logger = logger
        plugin._set_compaction_status = lambda *_args, **_kwargs: None
        plugin._call_summarization_llm = lambda *_args, **_kwargs: _resolved(summary)

        async def run_compaction() -> None:
            await seed_stream()
            await plugin._run_compaction()

        with patch(
            "plugins.context_compaction_plugin.utc_stamp",
            return_value=compaction_stamp,
        ):
            asyncio.run(run_compaction())

        staged = plugin._pending_compaction
        assert staged is not None
        staged_content = "\n".join(str(message.content) for message in staged)
        assert objective in staged_content
        assert operator_words in staged_content
        assert task_body in staged_content
        assert recent_user in staged_content
        assert recent_assistant in staged_content
        assert "#901 Active objective" in staged_content
        assert "#999999 unsupported state" not in staged_content

        assert manager.load_session(logger.session_id)
        restored = manager.messages
        restored_content = [str(message["content"]) for message in restored]
        joined_restored = "\n".join(restored_content)
        assert objective in joined_restored
        assert operator_words in joined_restored
        assert "#901 Active objective" in joined_restored
        assert "#999999 unsupported state" not in joined_restored
        assert task_body in joined_restored
        assert recent_user in joined_restored
        assert recent_assistant in joined_restored

        summary_index = next(
            index for index, content in enumerate(restored_content)
            if "Synthetic model summary" in content
        )
        task_index = next(
            index for index, content in enumerate(restored_content)
            if task_body in content
        )
        recent_user_index = next(
            index for index, content in enumerate(restored_content)
            if recent_user in content
        )
        recent_assistant_index = next(
            index for index, content in enumerate(restored_content)
            if recent_assistant in content
        )
        assert summary_index < task_index < recent_user_index < recent_assistant_index


async def _resolved(value: str) -> str:
    return value
