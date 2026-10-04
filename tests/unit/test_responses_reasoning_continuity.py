"""Reasoning continuity and Codex cache routing for the Responses provider.

Covers the shared provider_reasoning contract end to end: capture from the
stream, history plumbing in the API service and queue processor, and
re-emission in the request body.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kollabor_agent.queue_processor import (
    _assistant_history_usage_metadata,
    _last_provider_reasoning,
)
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.models import (
    OpenAIResponsesConfig,
    ProviderType,
    StreamingResponse,
    TextDelta,
)
from kollabor_ai.providers.openai_responses_provider import (
    OpenAIResponsesProvider,
    _codex_session_headers,
)
from kollabor_ai.providers.openai_responses_transformer import (
    OpenAIResponsesTransformer,
)

CODEX_URL = "https://chatgpt.com/backend-api/codex"
REASONING_ITEM = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [],
    "encrypted_content": "ENC-1",
}


def _provider(model="gpt-5.4", base_url=None, store=False):
    return OpenAIResponsesProvider(
        OpenAIResponsesConfig(
            provider=ProviderType.OPENAI_RESPONSES,
            api_key="sk-test",
            model=model,
            base_url=base_url,
            store_responses=store,
        )
    )


def _reasoning(model="gpt-5.4", items=(REASONING_ITEM,), phase=None):
    data = {
        "provider": ProviderType.OPENAI_RESPONSES.value,
        "model": model,
        "items": [dict(item) for item in items],
    }
    if phase:
        data["phase"] = phase
    return data


class _Raw:
    def __init__(self, *blocks):
        self._blocks = blocks

    async def aiter_bytes(self):
        for block in self._blocks:
            yield block


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


async def _drain(stream, provider=None):
    return [c async for c in (provider or _provider())._parse_sse_stream(stream)]


# --- capture -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_codex_stream_hands_back_reasoning_from_item_done_events():
    """The Codex backend ends with an empty output; done events are the only copy."""
    stream = _Raw(
        _sse(
            "response.output_item.done",
            {"item": {**REASONING_ITEM, "status": "completed"}},
        ),
        _sse(
            "response.output_item.done",
            {
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "checking"}],
                }
            },
        ),
        _sse(
            "response.output_item.done",
            {
                "item": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "shell",
                    "arguments": "{}",
                }
            },
        ),
        _sse("response.completed", {"response": {"output": [], "usage": {}}}),
    )

    chunks = await _drain(stream)

    final = chunks[-1]
    assert final.is_final
    assert final.provider_reasoning == {
        "provider": "openai_responses",
        "model": "gpt-5.4",
        "items": [
            {
                "type": "reasoning",
                "summary": [],
                "id": "rs_1",
                "encrypted_content": "ENC-1",
            }
        ],
        "phase": "commentary",
    }
    # Capture-only events must not reach the consumer as empty text deltas.
    assert all(c.is_final or c.raw_chunk is not None for c in chunks)
    assert not any(c.provider_reasoning for c in chunks[:-1])


@pytest.mark.asyncio
async def test_public_api_stream_reads_reasoning_from_the_final_payload():
    stream = _Raw(
        _sse("response.output_text.delta", {"delta": "hi"}),
        _sse(
            "response.completed",
            {"response": {"output": [REASONING_ITEM], "usage": {}}},
        ),
    )

    final = (await _drain(stream))[-1]

    assert [i["encrypted_content"] for i in final.provider_reasoning["items"]] == [
        "ENC-1"
    ]


@pytest.mark.asyncio
async def test_stream_without_reasoning_leaves_the_field_empty():
    stream = _Raw(
        _sse("response.output_text.delta", {"delta": "hi"}),
        _sse("response.completed", {"response": {"output": [], "usage": {}}}),
    )

    assert (await _drain(stream))[-1].provider_reasoning is None


def test_transform_response_sets_provider_reasoning():
    response = OpenAIResponsesTransformer.transform_response(
        {
            "output": [
                REASONING_ITEM,
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            ],
            "usage": {},
        },
        "gpt-5.4",
    )

    assert response.provider_reasoning["phase"] == "final_answer"
    assert response.provider_reasoning["items"][0]["id"] == "rs_1"


# --- re-emission -------------------------------------------------------------


def _tool_turn_messages(reasoning):
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
            "provider_reasoning": reasoning,
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]


def test_reasoning_goes_back_before_the_items_of_its_turn():
    request = _provider()._prepare_request(
        _tool_turn_messages(_reasoning(phase="commentary")), None, stream=True
    )

    assert request["input"] == [
        {"role": "user", "content": "go"},
        {
            "type": "reasoning",
            "summary": [],
            "id": "rs_1",
            "encrypted_content": "ENC-1",
        },
        {"role": "assistant", "content": "checking", "phase": "commentary"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "shell",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
    ]


def test_reasoning_goes_back_before_a_plain_assistant_answer():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "hello",
            "provider_reasoning": _reasoning(),
        },
        {"role": "user", "content": "again"},
    ]

    items = _provider()._prepare_request(messages, None, stream=True)["input"]

    assert [i.get("type") or i["role"] for i in items] == [
        "user",
        "reasoning",
        "assistant",
        "user",
    ]


@pytest.mark.parametrize(
    "reasoning",
    [
        _reasoning(model="gpt-5.5"),
        {**_reasoning(), "provider": "anthropic"},
        None,
    ],
    ids=["model-switch", "provider-switch", "none"],
)
def test_foreign_or_missing_reasoning_is_not_sent(reasoning):
    request = _provider()._prepare_request(
        _tool_turn_messages(reasoning), None, stream=True
    )

    assert "reasoning" not in [i.get("type") for i in request["input"]]
    assert "provider_reasoning" not in json.dumps(request)


def test_id_only_reasoning_needs_server_storage():
    id_only = _reasoning(items=[{"type": "reasoning", "id": "rs_9", "summary": []}])

    stateless = _provider(store=False)._prepare_request(
        _tool_turn_messages(id_only), None, stream=False
    )
    stored = _provider(store=True)._prepare_request(
        _tool_turn_messages(id_only), None, stream=False
    )

    assert "reasoning" not in [i.get("type") for i in stateless["input"]]
    assert [i["id"] for i in stored["input"] if i.get("type") == "reasoning"] == [
        "rs_9"
    ]


def test_encrypted_reasoning_is_requested_on_the_codex_backend_only():
    messages = [{"role": "user", "content": "hi"}]

    codex = _provider(base_url=CODEX_URL)._prepare_request(messages, None, stream=True)
    public = _provider()._prepare_request(messages, None, stream=False)

    assert codex["include"] == ["reasoning.encrypted_content"]
    assert "include" not in public


# --- Codex session headers ---------------------------------------------------


def test_session_headers_follow_the_codex_cli_names():
    uuid_id = "123e4567-e89b-12d3-a456-426614174000"

    assert _codex_session_headers(uuid_id) == {
        "session-id": uuid_id,
        "thread-id": uuid_id,
        "x-client-request-id": uuid_id,
    }
    assert _codex_session_headers(None) == {}


def test_non_uuid_session_ids_map_to_one_stable_uuid():
    first = _codex_session_headers("session_2026-10-03_abc")
    again = _codex_session_headers("session_2026-10-03_abc")
    other = _codex_session_headers("session_2026-10-03_xyz")

    assert first == again
    assert first["session-id"] != other["session-id"]
    assert len(first["session-id"]) == 36


def test_only_the_codex_backend_gets_session_headers():
    codex = _provider(base_url=CODEX_URL)
    public = _provider()

    codex.set_session_context("sess-1")
    public.set_session_context("sess-1")

    assert set(codex._session_headers) == {
        "session-id",
        "thread-id",
        "x-client-request-id",
    }
    assert public._session_headers == {}


# --- history plumbing --------------------------------------------------------


class _FakeProvider:
    supports_hosted_image_generation = False

    def __init__(self, *chunks):
        self.chunks = chunks
        self.session = "unset"

    def set_session_context(self, session_id):
        self.session = session_id

    async def stream(self, messages, tools=None, **kwargs):
        for chunk in self.chunks:
            yield chunk


def _service(provider, tmp_path):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": True,
        "kollabor.llm.use_explicit_tool_accumulation": True,
        "kollabor.llm.debug_tool_stream_path": False,
    }.get(key, default)
    profile = MagicMock()
    profile.provider = "openai_responses"
    profile.name = "continuity-test"
    profile.get_model.return_value = "gpt-5.4"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.to_dict.return_value = {
        "provider": "openai_responses",
        "model": "gpt-5.4",
        "api_key": "sk-test",
    }
    service = APICommunicationService(config, Path(tmp_path), profile)
    service._provider = provider
    service._initialized = True
    return service


@pytest.mark.asyncio
async def test_service_keeps_the_streamed_reasoning_until_the_next_call(tmp_path):
    reasoning = _reasoning()
    provider = _FakeProvider(
        StreamingResponse(delta=TextDelta(content="hi")),
        StreamingResponse(
            delta=TextDelta(content=""),
            is_final=True,
            finish_reason="stop",
            provider_reasoning=reasoning,
        ),
    )
    service = _service(provider, tmp_path)

    await service._call_provider_stream([{"role": "user", "content": "go"}])
    assert service.get_last_provider_reasoning() == reasoning

    provider.chunks = (StreamingResponse(delta=TextDelta(content="again")),)
    await service._call_provider_stream([{"role": "user", "content": "go"}])
    assert service.get_last_provider_reasoning() is None


def test_service_pushes_the_session_id_to_the_provider(tmp_path):
    provider = _FakeProvider()
    service = _service(provider, tmp_path)

    service.set_session_id("sess-42")

    assert provider.session == "sess-42"


def test_prepare_messages_copies_reasoning_from_history_metadata(tmp_path):
    from kollabor_events.data_models import ConversationMessage

    service = _service(_FakeProvider(), tmp_path)
    reasoning = _reasoning()
    history = [
        ConversationMessage(role="user", content="go"),
        ConversationMessage(
            role="assistant",
            content="hi",
            metadata={"provider_reasoning": reasoning},
        ),
    ]

    messages = service._prepare_messages(history)

    assert "provider_reasoning" not in messages[0]
    assert messages[1]["provider_reasoning"] == reasoning


def test_history_metadata_carries_reasoning_only_when_there_is_some():
    stats = {"input_tokens": 1, "output_tokens": 2}
    reasoning = _reasoning()

    with_reasoning = _assistant_history_usage_metadata(stats, 0.5, reasoning)
    without = _assistant_history_usage_metadata(stats, 0.5)

    assert with_reasoning["provider_reasoning"] == reasoning
    assert with_reasoning["usage"]["output_tokens"] == 2
    assert "provider_reasoning" not in without


def test_last_provider_reasoning_ignores_non_dict_answers():
    service = MagicMock()
    service.get_last_provider_reasoning.return_value = MagicMock()
    assert _last_provider_reasoning(service) is None

    service.get_last_provider_reasoning.return_value = None
    assert _last_provider_reasoning(service) is None

    service.get_last_provider_reasoning.return_value = _reasoning()
    assert _last_provider_reasoning(service) == _reasoning()


@pytest.mark.asyncio
async def test_conversation_log_keeps_provider_reasoning_verbatim(tmp_path):
    from kollabor_ai.conversation_logger import KollaborConversationLogger

    logger = KollaborConversationLogger(tmp_path)
    await logger.initialize()
    reasoning = {**_reasoning(), "prefix_sha256": "abc123", "extra": {"k": [1]}}

    await logger.log_assistant_message("hi", parent_uuid="root", provider_reasoning=reasoning)
    await logger.log_assistant_message("plain", parent_uuid="root")

    records = [
        json.loads(line)
        for path in tmp_path.glob("*.jsonl")
        for line in path.read_text().splitlines()
        if '"assistant"' in line
    ]
    assert [r.get("provider_reasoning") for r in records[-2:]] == [reasoning, None]


@pytest.mark.asyncio
async def test_resume_restores_provider_reasoning_on_text_turns_only(tmp_path):
    """Logged reasoning comes back on /resume, verbatim, for text-only turns.

    Resume rebuilds history without tool_calls, so a tool turn's reasoning
    could not be replayed in its original shape and must not come back.
    """
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_ai.conversation_manager import ConversationManager

    log = KollaborConversationLogger(tmp_path)
    await log.initialize()
    reasoning = {**_reasoning(), "prefix_sha256": "abc123"}
    await log.log_assistant_message("text turn", parent_uuid="root", provider_reasoning=reasoning)
    await log.log_assistant_message(
        "tool turn",
        parent_uuid="root",
        tool_calls=[{"id": "call_1", "name": "terminal", "input": {"command": "ls"}}],
        provider_reasoning=reasoning,
    )

    class _Cfg:
        _conversations_dir = tmp_path

        def get(self, key, default=None):
            return default

    manager = ConversationManager(_Cfg())
    assert manager.load_session(log.session_id)
    by_text = {m["content"]: m.get("metadata") or {} for m in manager.messages}
    assert by_text["text turn"].get("provider_reasoning") == reasoning
    assert "provider_reasoning" not in by_text["tool turn"]
