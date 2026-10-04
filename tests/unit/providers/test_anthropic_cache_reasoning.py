"""Anthropic request shape for prompt caching and signed-thinking continuity.

Docs relied on (not live-verified; no Anthropic credentials on this machine):
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- https://platform.claude.com/docs/en/build-with-claude/thinking
"""

import asyncio
import json
import unittest

from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.anthropic_reasoning import (
    ThinkingStreamCollector,
    build_reasoning,
    request_fingerprint,
)
from kollabor_ai.providers.models import AnthropicConfig

MODEL = "claude-opus-5-5"
THINK = {"type": "thinking", "thinking": "plan it", "signature": "sig-1"}
REDACTED = {"type": "redacted_thinking", "data": "enc-2"}
TOOLS = [
    {
        "name": "a",
        "description": "",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "b",
        "description": "",
        "parameters": {"type": "object", "properties": {}},
    },
]
CC = {"type": "ephemeral"}


def make_provider(model=MODEL):
    config = AnthropicConfig(api_key="test-key", model=model, max_tokens=64)
    provider = AnthropicProvider(config)
    provider._initialized = True
    return provider


def marks(obj):
    """Count cache_control markers in a request fragment."""
    if isinstance(obj, dict):
        return ("cache_control" in obj) + sum(marks(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(marks(v) for v in obj)
    return 0


def tool_turn(reasoning):
    """assistant tool call carrying `reasoning`, plus its tool result."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "a", "arguments": "{}"},
                }
            ],
            "provider_reasoning": reasoning,
        },
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
    ]


class CacheBreakpointTests(unittest.TestCase):
    def setUp(self):
        self.p = make_provider()
        self.msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
        ]

    def test_tools_system_and_last_two_user_turns_use_exactly_four(self):
        before = json.dumps(self.msgs)
        req = self.p._prepare_request(self.msgs, TOOLS)
        self.assertEqual(marks(req), 4)
        self.assertEqual(req["tools"][-1]["cache_control"], CC)
        self.assertNotIn("cache_control", req["tools"][0])
        self.assertEqual(req["system"][0]["cache_control"], CC)
        users = [m for m in req["messages"] if m["role"] == "user"]
        self.assertEqual(
            users[-1]["content"], [{"type": "text", "text": "e", "cache_control": CC}]
        )
        self.assertEqual(
            users[-2]["content"], [{"type": "text", "text": "c", "cache_control": CC}]
        )
        self.assertEqual(users[0]["content"], "a")
        self.assertEqual(json.dumps(self.msgs), before, "caller's messages mutated")

    def test_previous_requests_write_is_read_by_the_next_one(self):
        first = self.p._prepare_request(self.msgs, TOOLS)
        grown = self.msgs + [
            {"role": "assistant", "content": "f"},
            {"role": "user", "content": "g"},
        ]
        second = self.p._prepare_request(grown, TOOLS)

        def marked_texts(req):
            return [
                b["text"]
                for m in req["messages"]
                if isinstance(m["content"], list)
                for b in m["content"]
                if "cache_control" in b
            ]

        self.assertEqual(marked_texts(first), ["c", "e"])
        self.assertEqual(marked_texts(second), ["e", "g"])  # "e" is where first wrote

    def test_respects_markers_already_on_the_request(self):
        request = {
            "system": [{"type": "text", "text": "s", "cache_control": CC}],
            "tools": [{"name": "t", "cache_control": CC}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "x", "cache_control": CC}],
                },
                {"role": "user", "content": "y"},
            ],
        }
        self.p._place_message_breakpoints(request)
        self.assertEqual(marks(request), 4)

    def test_empty_text_is_never_marked(self):
        req = self.p._prepare_request(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "r"},
                {"role": "user", "content": ""},
            ]
        )
        self.assertEqual(marks(req), 1)  # only "q"; the empty turn stays bare
        self.assertEqual(req["messages"][-1]["content"], "")

    def test_leading_system_goes_to_system_mid_conversation_stays_in_place(self):
        turn1 = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        turn2 = turn1 + [
            {"role": "assistant", "content": "yo"},
            {"role": "system", "content": "roster: lapis"},
            {"role": "user", "content": "next"},
        ]
        r1 = self.p._prepare_request(turn1)
        r2 = self.p._prepare_request(turn2)
        self.assertEqual(r1["system"], r2["system"])  # prefix byte-stable
        flat = json.dumps(r2["messages"])
        self.assertIn("<sys_msg>roster: lapis</sys_msg>", flat)
        two = self.p._prepare_request(
            [
                {"role": "system", "content": "A"},
                {"role": "system", "content": "B"},
                {"role": "user", "content": "x"},
            ]
        )
        self.assertEqual(two["system"][0]["text"], "A\n\nB")


class ThinkingReplayTests(unittest.TestCase):
    def setUp(self):
        self.p = make_provider()
        self.first = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]
        self.req_a = self.p._prepare_request(self.first, TOOLS)
        self.reasoning = build_reasoning(
            [THINK, REDACTED], MODEL, lambda: request_fingerprint(self.req_a)
        )

    def _second(self, first=None, provider=None, tools=TOOLS):
        return (provider or self.p)._prepare_request(
            (first or self.first) + tool_turn(self.reasoning), tools
        )

    def test_signed_blocks_lead_the_matching_turn_and_nothing_leaks(self):
        req = self._second()
        assistant = next(m for m in req["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["content"][:2], [THINK, REDACTED])
        self.assertEqual(assistant["content"][2]["type"], "tool_use")
        wire = json.dumps(req)
        self.assertNotIn("provider_reasoning", wire)
        self.assertNotIn("_reasoning", wire)
        self.assertNotIn("prefix_sha256", wire)

    def test_plain_assistant_turn_gets_them_too(self):
        msgs = self.first + [
            {
                "role": "assistant",
                "content": "answer",
                "provider_reasoning": self.reasoning,
            },
            {"role": "user", "content": "more"},
        ]
        req = self.p._prepare_request(msgs, TOOLS)
        assistant = next(m for m in req["messages"] if m["role"] == "assistant")
        self.assertEqual(
            assistant["content"], [THINK, REDACTED, {"type": "text", "text": "answer"}]
        )

    def test_dropped_when_model_provider_or_prefix_differ(self):
        edited_user = [self.first[0], {"role": "user", "content": "q (edited)"}]
        edited_system = [{"role": "system", "content": "other"}, self.first[1]]
        cases = {
            "model": self._second(provider=make_provider("claude-sonnet-5-5")),
            "edited message": self._second(first=edited_user),
            "edited system": self._second(first=edited_system),
            "edited tools": self._second(tools=TOOLS[:1]),
        }
        for name, req in cases.items():
            assistant = next(m for m in req["messages"] if m["role"] == "assistant")
            self.assertEqual(assistant["content"][0]["type"], "tool_use", name)
            self.assertNotIn("provider_reasoning", json.dumps(req), name)
        foreign = dict(self.reasoning, provider="openrouter")
        req = self.p._prepare_request(self.first + tool_turn(foreign), TOOLS)
        assistant = next(m for m in req["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["type"], "tool_use")
        self.assertNotIn("provider_reasoning", json.dumps(req))

    def test_unfingerprinted_reasoning_is_not_replayed(self):
        bare = {k: v for k, v in self.reasoning.items() if k != "prefix_sha256"}
        req = self.p._prepare_request(self.first + tool_turn(bare), TOOLS)
        assistant = next(m for m in req["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["type"], "tool_use")


class _Response:
    status_code = 200
    headers: dict = {}

    def __init__(self, body=None, lines=()):
        self._body, self._lines = body, lines

    def json(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, response):
        self.response = response

    async def post(self, url, headers=None, json=None):
        return self.response

    def stream(self, method, url, headers=None, json=None):
        return _Stream(self.response)


def sse(events):
    return [f"data: {json.dumps(e)}" for e in events]


STREAM = [
    {
        "type": "message_start",
        "message": {
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1000,
                "output_tokens": 1,
            }
        },
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "hel"},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "lo"},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "signature_delta", "signature": "sig-"},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "signature_delta", "signature": "9"},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "redacted_thinking", "data": "enc-2"},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "content_block_start",
        "index": 2,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "text_delta", "text": "hi"},
    },
    {"type": "content_block_stop", "index": 2},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 5},
    },
    {"type": "message_stop"},
]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.p = make_provider()
        self.msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]

    def test_stream_emits_signed_blocks_once_on_the_final_chunk(self):
        self.p._client = _Client(_Response(lines=sse(STREAM)))

        async def run():
            return [c async for c in self.p.stream(self.msgs, TOOLS)]

        chunks = asyncio.run(run())
        carrying = [c for c in chunks if c.provider_reasoning]
        self.assertEqual(len(carrying), 1)
        self.assertTrue(carrying[0].is_final)
        reasoning = carrying[0].provider_reasoning
        self.assertEqual(reasoning["provider"], "anthropic")
        self.assertEqual(reasoning["model"], MODEL)
        self.assertEqual(
            reasoning["items"],
            [{"type": "thinking", "thinking": "hello", "signature": "sig-9"}, REDACTED],
        )
        self.assertEqual(
            reasoning["prefix_sha256"], request_fingerprint(self.p.last_request_payload)
        )
        usage = [c.usage for c in chunks if c.usage]
        self.assertEqual(
            (
                usage[0].prompt_tokens,
                usage[0].cache_creation_tokens,
                usage[0].cache_read_tokens,
            ),
            (1110, 100, 1000),
        )
        self.assertEqual(usage[-1].completion_tokens, 5)

    def test_non_stream_returns_signed_blocks_and_usage(self):
        body = {
            "content": [
                dict(THINK),
                REDACTED,
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 7,
                "cache_read_input_tokens": 90,
                "output_tokens": 4,
            },
        }
        self.p._client = _Client(_Response(body=body))
        result = asyncio.run(self.p.call(self.msgs, TOOLS))
        self.assertEqual(result.provider_reasoning["items"], [THINK, REDACTED])
        self.assertEqual(
            result.provider_reasoning["prefix_sha256"],
            request_fingerprint(self.p.last_request_payload),
        )
        self.assertEqual(
            (
                result.usage.prompt_tokens,
                result.usage.cache_creation_tokens,
                result.usage.cache_read_tokens,
            ),
            (100, 7, 90),
        )

    def test_no_thinking_means_no_reasoning(self):
        collector = ThinkingStreamCollector()
        collector.feed(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
        self.assertIsNone(collector.reasoning(MODEL, lambda: "fp"))

    def test_captured_reasoning_round_trips_into_the_next_request(self):
        self.p._client = _Client(_Response(lines=sse(STREAM)))

        async def run():
            return [c async for c in self.p.stream(self.msgs, TOOLS)]

        reasoning = next(
            c.provider_reasoning for c in asyncio.run(run()) if c.provider_reasoning
        )
        nxt = self.p._prepare_request(self.msgs + tool_turn(reasoning), TOOLS)
        assistant = next(m for m in nxt["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["signature"], "sig-9")
        self.assertEqual(assistant["content"][1], REDACTED)


if __name__ == "__main__":
    unittest.main()
