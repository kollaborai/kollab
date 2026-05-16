import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage


class TestHubIdentityMailbox(unittest.TestCase):
    def test_read_mailboxes_reads_identity_mailbox_after_agent_id_changes(self) -> None:
        msg = HubMessage(
            action="message",
            from_agent="sapphire-id",
            from_identity="sapphire",
            to="koordinator",
            content="review complete",
        )

        with tempfile.TemporaryDirectory() as tmp:
            msg_dir = Path(tmp)
            with patch("plugins.hub.messenger.get_messages_dir", return_value=msg_dir):
                self.loop_run(AgentMessenger.send_to_file("koordinator", msg))

                messages = AgentMessenger.read_mailboxes(
                    ["new-koordinator-agent-id", "koordinator"]
                )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "review complete")

    def loop_run(self, coro):
        import asyncio

        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
