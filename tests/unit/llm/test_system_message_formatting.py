import unittest
from unittest.mock import AsyncMock

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.system_messages import (
    PendingSystemMessage,
    format_system_message,
    merge_system_messages_with_user_message,
)
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_events.data_models import ConversationMessage


class SystemMessageFormattingTests(unittest.IsolatedAsyncioTestCase):
    def test_format_system_message_wraps_plain_content(self):
        self.assertEqual(
            format_system_message("[nudge] save notes", subtype="hub_nudge"),
            "<sys_msg>\n[system:hub_nudge]\n[nudge] save notes\n</sys_msg>",
        )

    def test_format_system_message_preserves_existing_wrapper(self):
        wrapped = "<sys_msg>\nold format\n</sys_msg>"
        self.assertEqual(format_system_message(wrapped, subtype="hub_nudge"), wrapped)

    def test_merge_system_messages_with_user_message_keeps_one_payload(self):
        self.assertEqual(
            merge_system_messages_with_user_message(
                [
                    PendingSystemMessage(
                        subtype="hub_nudge",
                        content="[nudge] save notes",
                    ),
                    PendingSystemMessage(
                        subtype="crystal_nudge",
                        content="[crystal nudge] prior context",
                    ),
                ],
                "tell lapis to check the last 2 commits",
            ),
            (
                "<system_messages>\n"
                "[hub_nudge]\n"
                "[nudge] save notes\n\n"
                "[crystal_nudge]\n"
                "[crystal nudge] prior context\n"
                "</system_messages>\n\n"
                "tell lapis to check the last 2 commits"
            ),
        )

    async def test_inject_system_message_queues_and_logs_consistent_content(self):
        service = LLMService.__new__(LLMService)
        service.conversation_history = []
        service.current_parent_uuid = "parent-1"
        service.conversation_logger = AsyncMock()

        await service.inject_system_message(
            "[nudge] you've been working a while",
            subtype="hub_nudge",
        )

        self.assertEqual(service.conversation_history, [])
        self.assertEqual(len(service._pending_system_messages), 1)
        self.assertEqual(service._pending_system_messages[0].subtype, "hub_nudge")
        self.assertEqual(
            service._pending_system_messages[0].content,
            "[nudge] you've been working a while",
        )
        logged_content = (
            "<sys_msg>\n"
            "[system:hub_nudge]\n"
            "[nudge] you've been working a while\n"
            "</sys_msg>"
        )
        service.conversation_logger.log_system_message.assert_awaited_once_with(
            logged_content,
            parent_uuid="parent-1",
            subtype="hub_nudge",
        )

        merged = service.merge_pending_system_messages("real user message")
        self.assertEqual(
            merged,
            (
                "<system_messages>\n"
                "[hub_nudge]\n"
                "[nudge] you've been working a while\n"
                "</system_messages>\n\n"
                "real user message"
            ),
        )
        self.assertEqual(service._pending_system_messages, [])

    async def test_process_message_batch_sends_system_block_and_user_as_one_message(self):
        class FakeQueueProcessor:
            def __init__(self):
                self.messages = None

            async def process_message_batch(self, messages, current_parent_uuid):
                self.messages = messages
                return "next-parent"

        service = LLMService.__new__(LLMService)
        service._pending_system_messages = [
            PendingSystemMessage("hub_nudge", "[nudge] save notes")
        ]
        service._queue_processor = FakeQueueProcessor()
        service.current_parent_uuid = "parent-1"

        await service._process_message_batch(["actual user request"])

        self.assertEqual(service.current_parent_uuid, "next-parent")
        self.assertEqual(
            service._queue_processor.messages,
            [
                (
                    "<system_messages>\n"
                    "[hub_nudge]\n"
                    "[nudge] save notes\n"
                    "</system_messages>\n\n"
                    "actual user request"
                )
            ],
        )
        self.assertEqual(service._pending_system_messages, [])

    def test_prepare_messages_preserves_system_metadata_for_raw_logs(self):
        service = APICommunicationService.__new__(APICommunicationService)

        messages = service._prepare_messages(
            [
                ConversationMessage(
                    role="user",
                    content="<sys_msg>\n[system:hub_nudge]\nbody\n</sys_msg>",
                    metadata={"system_message": True, "subtype": "hub_nudge"},
                )
            ],
            max_history=None,
        )

        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["system_message"], True)
        self.assertEqual(messages[0]["subtype"], "hub_nudge")


if __name__ == "__main__":
    unittest.main()
