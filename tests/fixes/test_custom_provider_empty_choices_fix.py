"""Regression check for CustomProvider._stream_impl IndexError.

An OpenAI-compatible local server (LM Studio et al.) can send a trailing
SSE chunk whose "choices" key is present but an EMPTY list (this is the
usage trailer produced by our own stream_options.include_usage request,
and it's also what an empty/timed-out-mid-stream response looks like).
`data.get("choices", [{}])[0]` only falls back to the default when the key
is MISSING, not when it's an empty list, so that chunk crashed with
IndexError: list index out of range. See custom_provider.py `_stream_impl`.
"""

import pytest

from kollabor_ai.providers.custom_provider import CustomConfig, CustomProvider
from kollabor_ai.providers.errors import EmptyResponseError
from kollabor_ai.providers.models import ProviderType, TextDelta


class _FakeContent:
    """Minimal stand-in for aiohttp's StreamReader: async-iterates byte lines."""

    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for line in self._lines:
            yield line


class _FakeResponse:
    status = 200

    def __init__(self, lines):
        self.content = _FakeContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def post(self, *args, **kwargs):
        return _FakeResponse(self._lines)


@pytest.mark.asyncio
async def test_stream_survives_trailing_empty_choices_chunk(monkeypatch):
    # A content chunk followed by the empty/partial-response shape that used
    # to crash: "choices" present but [] (our own request sets
    # stream_options.include_usage, which produces exactly this trailer).
    sse_lines = [
        b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n',
        b'data: {"choices": [], "usage": {"prompt_tokens": 5, '
        b'"completion_tokens": 1, "total_tokens": 6}}\n',
        b"data: [DONE]\n",
    ]

    config = CustomConfig(
        provider=ProviderType.CUSTOM,
        api_key="unused",
        model="test-model",
        base_url="http://localhost:1234/v1",
    )
    provider = CustomProvider(config)

    monkeypatch.setattr(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        lambda *a, **kw: _FakeSession(sse_lines),
    )

    results = [
        item
        async for item in provider._stream_impl(messages=[{"role": "user", "content": "hi"}])
    ]

    # Would have raised IndexError before the fix; getting here is the check.
    assert any(
        isinstance(r.delta, TextDelta) and r.delta.content == "Hi" for r in results
    )
    assert any(r.usage is not None and r.usage.total_tokens == 6 for r in results)


# ---------------------------------------------------------------------------
# Non-streaming call() path: same failure class, different entry point.
# ---------------------------------------------------------------------------


class _FakeCallResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeCallSession:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def post(self, *args, **kwargs):
        return _FakeCallResponse(self._payload)


def _make_provider():
    config = CustomConfig(
        provider=ProviderType.CUSTOM,
        api_key="unused",
        model="test-model",
        base_url="http://localhost:1234/v1",
    )
    return CustomProvider(config)


@pytest.mark.asyncio
async def test_call_empty_choices_list_raises_empty_response_error(monkeypatch):
    # "choices" present but an EMPTY list (empty/aborted response from a local
    # server or gateway). Used to crash with a raw IndexError on [0].
    monkeypatch.setattr(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        lambda *a, **kw: _FakeCallSession({"choices": []}),
    )
    provider = _make_provider()
    with pytest.raises(EmptyResponseError) as exc_info:
        await provider.call(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.error_code == "empty_response"


@pytest.mark.asyncio
async def test_call_missing_choices_key_raises_empty_response_error(monkeypatch):
    # "choices" key missing entirely (proxy/gateway malformation).
    monkeypatch.setattr(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        lambda *a, **kw: _FakeCallSession({"usage": {}}),
    )
    provider = _make_provider()
    with pytest.raises(EmptyResponseError):
        await provider.call(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_call_null_content_and_missing_message_key_raises_clean_error(
    monkeypatch,
):
    # Valid shape with "content": null and a missing "message" key must not
    # raise KeyError/ValidationError — the guards are choice.get("message", {})
    # and the empty-content check, which surface a clean EmptyResponseError.
    monkeypatch.setattr(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        lambda *a, **kw: _FakeCallSession(
            {"choices": [{"finish_reason": "stop"}], "usage": {}}
        ),
    )
    provider = _make_provider()
    with pytest.raises(EmptyResponseError) as exc_info:
        await provider.call(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.error_code == "empty_response"
