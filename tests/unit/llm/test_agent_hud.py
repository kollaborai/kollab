import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.llm.agent_hud import (
    AgentHudEntry,
    format_agent_hud,
    merge_agent_hud_with_user_message,
)
from kollabor.llm.llm_coordinator import LLMService
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_events.data_models import ConversationMessage


class AgentHudTests(unittest.IsolatedAsyncioTestCase):
    def test_format_agent_hud_renders_diff_entries(self):
        self.assertEqual(
            format_agent_hud(
                [
                    AgentHudEntry(
                        section="system",
                        label="hub_nudge",
                        content="[nudge] save notes",
                    ),
                    AgentHudEntry(
                        section="hub",
                        label="lapis->koordinator",
                        content="standing by",
                    ),
                ]
            ),
            (
                "<agent_hud>\n"
                "[system:hub_nudge]\n"
                "+ [nudge] save notes\n\n"
                "[hub:lapis->koordinator]\n"
                "+ standing by\n"
                "</agent_hud>"
            ),
        )

    def test_merge_agent_hud_with_user_message_keeps_one_payload(self):
        self.assertEqual(
            merge_agent_hud_with_user_message(
                [
                    AgentHudEntry(
                        section="system",
                        label="hub_nudge",
                        content="[nudge] save notes",
                    )
                ],
                "tell lapis to check the last 2 commits",
            ),
            (
                "<agent_hud>\n"
                "[system:hub_nudge]\n"
                "+ [nudge] save notes\n"
                "</agent_hud>\n\n"
                "tell lapis to check the last 2 commits"
            ),
        )

    async def test_inject_system_message_queues_hud_and_logs_hud(self):
        service = LLMService.__new__(LLMService)
        service.conversation_history = []
        service.current_parent_uuid = "parent-1"
        service.conversation_logger = AsyncMock()

        await service.inject_system_message(
            "[nudge] you've been working a while",
            subtype="hub_nudge",
        )

        self.assertEqual(service.conversation_history, [])
        self.assertEqual(len(service._pending_agent_hud), 1)
        self.assertEqual(service._pending_agent_hud[0].section, "system")
        self.assertEqual(service._pending_agent_hud[0].label, "hub_nudge")
        self.assertEqual(
            service._pending_agent_hud[0].content,
            "[nudge] you've been working a while",
        )
        logged_content = (
            "<agent_hud>\n"
            "[system:hub_nudge]\n"
            "+ [nudge] you've been working a while\n"
            "</agent_hud>"
        )
        service.conversation_logger.log_system_message.assert_awaited_once_with(
            logged_content,
            parent_uuid="parent-1",
            subtype="hub_nudge",
        )

    async def test_process_message_batch_drains_hud_into_one_user_payload(self):
        class FakeQueueProcessor:
            def __init__(self):
                self.messages = None

            async def process_message_batch(self, messages, current_parent_uuid):
                self.messages = messages
                return "next-parent"

        service = LLMService.__new__(LLMService)
        service._pending_agent_hud = [
            AgentHudEntry("system", "hub_nudge", "[nudge] save notes")
        ]
        service._queue_processor = FakeQueueProcessor()
        service.current_parent_uuid = "parent-1"

        await service._process_message_batch(["actual user request"])

        self.assertEqual(service.current_parent_uuid, "next-parent")
        self.assertEqual(
            service._queue_processor.messages,
            [
                (
                    "<agent_hud>\n"
                    "[system:hub_nudge]\n"
                    "+ [nudge] save notes\n"
                    "</agent_hud>\n\n"
                    "actual user request"
                )
            ],
        )
        self.assertEqual(service._pending_agent_hud, [])

    async def test_continue_conversation_does_not_flush_hud_alone(self):
        class FakeQueueProcessor:
            def __init__(self):
                self.called = False

            async def continue_conversation(self, current_parent_uuid):
                self.called = True
                return "same-parent"

        service = LLMService.__new__(LLMService)
        service._pending_agent_hud = [
            AgentHudEntry("system", "hub_nudge", "[nudge] save notes")
        ]
        service._queue_processor = FakeQueueProcessor()
        service.current_parent_uuid = "parent-1"
        service._add_conversation_message = AsyncMock()

        await service._continue_conversation()

        self.assertEqual(service.current_parent_uuid, "same-parent")
        self.assertEqual(len(service._pending_agent_hud), 1)
        service._add_conversation_message.assert_not_called()

    async def test_hub_retry_continue_flushes_hud_once(self):
        class FakeQueueProcessor:
            turn_completed = True

            def __init__(self):
                self.called = False

            async def continue_conversation(self, current_parent_uuid):
                self.called = True
                return current_parent_uuid

        service = LLMService.__new__(LLMService)
        service._pending_agent_hud = [
            AgentHudEntry("hub", "lapis->koordinator", "task complete")
        ]
        service._queue_processor = FakeQueueProcessor()
        service.current_parent_uuid = "parent-1"
        service._add_conversation_message = MagicMock(return_value="hud-parent")

        await service._continue_conversation()

        self.assertEqual(service.current_parent_uuid, "hud-parent")
        self.assertEqual(service._pending_agent_hud, [])
        message = service._add_conversation_message.call_args.args[0]
        self.assertEqual(message.role, "user")
        self.assertTrue(message.content.startswith("<agent_hud>"))
        self.assertEqual(message.metadata["agent_hud"], True)

    def test_prepare_messages_preserves_agent_hud_metadata_for_raw_logs(self):
        service = APICommunicationService.__new__(APICommunicationService)

        messages = service._prepare_messages(
            [
                ConversationMessage(
                    role="user",
                    content="<agent_hud>\n[hub:lapis->koordinator]\n+ done\n</agent_hud>",
                    metadata={
                        "agent_hud": True,
                        "agent_hud_sources": ["hub"],
                    },
                )
            ],
            max_history=None,
        )

        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["agent_hud"], True)
        self.assertEqual(messages[0]["agent_hud_sources"], ["hub"])


if __name__ == "__main__":
    unittest.main()
