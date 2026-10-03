"""The hub's background LLM helper retries transient provider failures."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_ai.providers.errors import RateLimitError, ServerError
from plugins.hub.plugin import HubPlugin


def _reply(text):
    reply = MagicMock()
    reply.get_text_content.return_value = text
    return reply


class DreamingRetryTest(unittest.TestCase):
    def _run(self, provider):
        llm = MagicMock()
        llm.api_service._provider = provider
        bus = MagicMock()
        bus.get_service.return_value = llm
        plugin = HubPlugin.__new__(HubPlugin)
        sleep = AsyncMock()
        with patch.object(HubPlugin, "event_bus", bus, create=True):
            with patch("asyncio.sleep", sleep):
                out = asyncio.run(plugin._dreaming_llm_call("prompt"))
        return out, sleep

    def test_retries_rate_limit_and_server_errors_then_succeeds(self):
        provider = MagicMock()
        provider.call = AsyncMock(
            side_effect=[
                ServerError("boom", provider="p", status_code=503),
                RateLimitError("slow down", provider="p", retry_after=2.0),
                _reply(" insight "),
            ]
        )
        out, sleep = self._run(provider)
        self.assertEqual(out, "insight")
        self.assertEqual(provider.call.await_count, 3)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [5.0, 2.0])

    def test_non_transient_error_is_not_retried(self):
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=ValueError("bad request"))
        out, sleep = self._run(provider)
        self.assertIsNone(out)
        self.assertEqual(provider.call.await_count, 1)
        sleep.assert_not_awaited()

    def test_retries_are_bounded(self):
        provider = MagicMock()
        provider.call = AsyncMock(
            side_effect=ServerError("boom", provider="p", status_code=500)
        )
        out, sleep = self._run(provider)
        self.assertIsNone(out)
        self.assertEqual(provider.call.await_count, 4)
        self.assertEqual(sleep.await_count, 3)


if __name__ == "__main__":
    unittest.main()
