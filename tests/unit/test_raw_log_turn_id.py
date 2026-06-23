"""Tests for turn_id linkage in the raw conversation log.

A user turn that hits ``stop_reason=length`` triggers auto-continuation
in queue_processor — that path issues N sequential ``call_llm`` calls
and merges the responses. Without explicit linkage in the raw log,
those N calls land as orphan entries and reconstructing a stitched
trace means guessing from timestamps.

The contract these tests pin: the caller can pass ``turn_id`` for the
initial call and ``parent_turn_id`` for follow-ups, and the resulting
raw log entries reflect both fields.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.profile_manager import LLMProfile
from kollabor_ai.providers.models import ProviderType, UnifiedResponse, UsageInfo


def _make_unified_response(text: str = "hi") -> UnifiedResponse:
    from kollabor_ai.providers.models import TextContent

    return UnifiedResponse(
        content=[TextContent(text=text)],
        usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model="test-model",
        provider=ProviderType.ANTHROPIC,
        finish_reason="end_turn",
        raw_response={"id": "msg_test"},
    )


class TurnIdLinkageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="kollab_turn_"))

        config = MagicMock()
        config.get = lambda k, default=None: {}.get(k, default)
        profile = LLMProfile(
            name="test",
            provider="anthropic",
            model="test-model",
            base_url="https://api.example.com",
            api_key="test-key",
            streaming=False,
        )
        self.svc = APICommunicationService(
            config=config, raw_conversations_dir=self.tmpdir, profile=profile
        )
        # Stub the provider so no network call is made
        self.svc._provider = MagicMock()
        self.svc._provider.call = AsyncMock(return_value=_make_unified_response())
        self.svc._provider.last_request_payload = {"messages": []}
        self.svc._provider._provider_name = "anthropic"
        self.svc.enable_streaming = False
        self.svc.set_session_id("turn-test")

    def _read_entries(self):
        raw_file = self.tmpdir / "turn-test_raw.jsonl"
        return [json.loads(line) for line in raw_file.read_text().splitlines()]

    async def test_turn_id_param_is_persisted(self):
        await self.svc.call_llm(
            [{"role": "user", "content": "hi"}],
            turn_id="abc-123",
        )
        entries = self._read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["turn_id"], "abc-123")
        self.assertIsNone(entries[0]["continuation_of"])

    async def test_parent_turn_id_marks_continuation(self):
        await self.svc.call_llm(
            [{"role": "user", "content": "hi"}],
            turn_id="parent",
        )
        await self.svc.call_llm(
            [{"role": "user", "content": "continue"}],
            turn_id="child",
            parent_turn_id="parent",
        )
        entries = self._read_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["turn_id"], "parent")
        self.assertIsNone(entries[0]["continuation_of"])
        self.assertEqual(entries[1]["turn_id"], "child")
        self.assertEqual(entries[1]["continuation_of"], "parent")

    async def test_last_turn_id_exposed_after_call(self):
        """Callers that don't pre-generate a turn_id can read it back."""
        await self.svc.call_llm([{"role": "user", "content": "hi"}])
        self.assertIsNotNone(self.svc.last_turn_id)
        # Length matches a uuid4 string ("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        self.assertEqual(len(self.svc.last_turn_id), 36)

    async def test_raw_log_accepts_string_directory(self):
        """The headless engine passes raw_conversations_dir as a string."""
        self.svc.raw_conversations_dir = str(self.tmpdir)

        await self.svc.call_llm([{"role": "user", "content": "hi"}])

        entries = self._read_entries()
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
