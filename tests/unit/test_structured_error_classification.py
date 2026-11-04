"""Structured error classification propagates by code, never rendered text (#720).

The #549 defect class: an error message is rendered once (provider name
prefixed in, hints appended, media redacted) and then classified by matching
substrings of that text. The rendered text is display data. These tests prove
classification reads the structured snapshot — class, code, status — for every
provider path, including poisoned text that names a provider and mimics an
auth failure while the code says otherwise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kollabor_ai.providers.errors import (
    CONTEXT_OVERFLOW_CODES,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ContextLengthExceededError,
    EmptyResponseError,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    ServerError,
    TransientHTTPError,
    is_context_overflow,
    map_anthropic_error,
    map_http_status_error,
    map_httpx_error,
    map_openai_error,
    structured_error_info,
)

# === classifier: typed exceptions map without reading text ===


@pytest.mark.parametrize(
    ("exc_factory", "expected_class", "expected_retryable"),
    [
        (
            lambda: AuthenticationError("bad key", "openai"),
            "authentication",
            False,
        ),
        (
            lambda: RateLimitError("slow down", "openai", retry_after=12.0),
            "rate_limit",
            True,
        ),
        (
            lambda: ContextLengthExceededError("too long", "openai"),
            "context_length",
            False,
        ),
        (
            lambda: InvalidRequestError("bad request", "openai"),
            "invalid_request",
            False,
        ),
        (
            lambda: EmptyResponseError("empty", "openai", error_code="empty_response"),
            "empty_response",
            True,
        ),
        (lambda: APITimeoutError("timed out", "openai"), "timeout", True),
        (lambda: APIConnectionError("no route", "openai"), "connection", True),
        (lambda: ServerError("boom", "openai", status_code=503), "server", True),
        (lambda: TransientHTTPError("again", "openai", status_code=409),
         "transient_http", True),
    ],
)
def test_typed_exceptions_classify_by_class(exc_factory, expected_class, expected_retryable):
    info = structured_error_info(exc_factory())
    assert info.error_class == expected_class
    assert info.retryable is expected_retryable


def test_base_provider_error_classifies_by_typed_status():
    exc = ProviderError("opaque", "custom", error_code=None)
    exc.status_code = 502  # typed attribute, not text
    info = structured_error_info(exc)
    assert info.error_class == "server"
    assert info.retryable is True


def test_base_provider_error_unmapped_code_is_not_retryable():
    exc = ProviderError("opaque", "custom", error_code="http_error_418")
    info = structured_error_info(exc)
    assert info.error_class == "provider"
    assert info.retryable is False


# === the #549 case, per provider: provider name is inside the rendered
# message, and classification still resolves auth from the code/class ===


def _fake_openai_auth_error() -> Exception:
    """An OpenAI-SDK-shaped AuthenticationError without importing the SDK."""
    return AuthenticationError(
        "Error code: 401 - invalid api key provided",
        "openai",
        error_code="authentication_error",
        original_error=RuntimeError("sdk cause"),
    )


@pytest.mark.parametrize(
    "provider",
    ["openai", "azure_openai", "openrouter", "openai_responses"],
)
def test_auth_classifies_via_code_with_provider_name_in_text(provider):
    exc = AuthenticationError(
        "Error code: 401 - invalid api key provided",
        provider,
        error_code="authentication_error",
    )
    rendered = f"{provider}: {exc}"  # the #549 rendering: name prefixed in
    assert provider in rendered
    assert "API key" in rendered  # rendered text mentions the failure vaguely

    info = structured_error_info(exc)
    assert info.error_class == "authentication"
    assert info.retryable is False
    assert info.error_code == "authentication_error"
    assert info.provider == provider


def test_poisoned_text_does_not_fabricate_auth():
    """A 5xx whose body mentions keys must classify as server, not auth."""
    raw = "upstream 500: invalid api key cache flushed for openai profile"
    exc = ServerError(raw, "openai", status_code=500)
    # The raw body named the provider and auth words; the typed snapshot
    # still says server/retryable — the verdict never reads that text.
    assert "invalid api key" in raw.lower() and "openai" in raw.lower()

    info = structured_error_info(exc)
    assert info.error_class == "server"
    assert info.retryable is True


def test_untyped_exception_uses_main_canonical_transient_markers():
    """Main's bounded transient markers classify untyped failures (main-canonical).

    After the #720 fix-cycle alignment, an untyped exception carrying main's
    canonical transient markers (rate ``429``, server ``5xx`` /
    ``Network error``) classifies exactly like main's retry loop does — main
    is canonical for classification behavior. The markers are bounded regex
    checks at the single classifier; timeout and connection stay typed-only.
    """
    exc = RuntimeError(
        "job 429 failed after 500 attempts; Network error mentioned in a sentence"
    )
    info = structured_error_info(exc)
    assert info.retryable is True
    assert info.error_class == "rate_limit"  # rate is checked first, like main

    unmarked = RuntimeError("request timed out while streaming")
    plain = structured_error_info(unmarked)
    assert plain.retryable is False  # no timeout text fallback, like main
    assert plain.error_class == "RuntimeError"


def test_untyped_exception_with_typed_status_attribute_still_classifies():
    wrapped = RuntimeError("opaque")
    wrapped.status_code = 429  # typed attribute survives without SDK types
    info = structured_error_info(wrapped)
    assert info.error_class == "rate_limit"
    assert info.retryable is True


# === provider mappers: every provider path lands in the typed hierarchy ===


def _httpx_status_error(status: int, body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.test/v1/x")
    response = httpx.Response(
        status,
        request=request,
        headers={"content-type": "application/json"},
        json=body,
    )
    response.read()
    return httpx.HTTPStatusError(
        "Client error '401 Unauthorized' for url 'https://api.example.test/v1/x'",
        request=request,
        response=response,
    )


@pytest.mark.parametrize(
    ("provider", "mapper"),
    [
        ("anthropic", map_anthropic_error),
        ("gemini", map_httpx_error),
    ],
)
def test_httpx_auth_path_classifies_via_code(provider, mapper):
    exc = mapper(
        _httpx_status_error(
            401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}}
        ),
        provider,
    )
    assert isinstance(exc, AuthenticationError)

    rendered = f"{provider}: {exc}"
    assert provider in rendered

    info = structured_error_info(exc)
    assert info.error_class == "authentication"


def test_custom_provider_auth_via_map_http_status_error():
    exc = map_http_status_error(
        RuntimeError("401"), "custom-endpoint", 401, {}
    )
    assert isinstance(exc, AuthenticationError)
    info = structured_error_info(exc)
    assert info.error_class == "authentication"
    assert info.error_code == "authentication_error"


def test_openai_sdk_auth_error_via_map_openai_error(monkeypatch):
    """map_openai_error maps the SDK AuthenticationError type when installed."""
    import sys
    import types

    fake_openai = types.ModuleType("openai")

    class SDKAuthenticationError(Exception):
        def __init__(self, message):
            super().__init__(message)

    class SDKRateLimitError(Exception):
        def __init__(self, message):
            super().__init__(message)

    fake_openai.AuthenticationError = SDKAuthenticationError
    fake_openai.RateLimitError = SDKRateLimitError
    fake_openai.BadRequestError = type("BadRequestError", (Exception,), {})
    fake_openai.NotFoundError = type("NotFoundError", (Exception,), {})
    fake_openai.APIStatusError = type("APIStatusError", (Exception,), {})
    fake_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
    fake_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    sdk_error = SDKAuthenticationError("Error code: 401 - invalid api key provided")
    exc = map_openai_error(sdk_error, "openrouter")
    assert isinstance(exc, AuthenticationError)

    rendered = f"openrouter: {exc}"
    assert "openrouter" in rendered and "API key" in rendered

    info = structured_error_info(exc)
    assert info.error_class == "authentication"
    assert info.error_code == "authentication_error"


def test_openai_mapper_rate_limit_carries_retry_after():
    exc = RateLimitError("rate limited", "openai", retry_after=30.0)
    info = structured_error_info(exc)
    assert info.error_class == "rate_limit"
    assert info.retry_after == 30.0
    assert structured_error_info(exc).to_dict()["retry_after"] == 30.0


# === context overflow: code only, never phrase matching ===


def test_context_overflow_by_code_not_text():
    exc = EmptyResponseError("provider returned nothing", "gemini")
    exc.error_code = "context_window_exceeded"
    info = structured_error_info(exc)
    assert is_context_overflow(info) is True

    poisoned = ProviderError(
        "please mention: maximum context length exceeded in a novel",
        "openai",
        error_code="http_error_400",
    )
    assert "context length" in str(poisoned).lower()
    assert is_context_overflow(structured_error_info(poisoned)) is False


def test_context_overflow_codes_cover_the_wire_set():
    assert CONTEXT_OVERFLOW_CODES == frozenset(
        {
            "model_context_window_exceeded",
            "context_window_exceeded",
            "context_length_exceeded",
        }
    )


def test_context_overflow_typed_class_is_not_retryable():
    exc = ContextLengthExceededError("too many tokens", "anthropic")
    info = structured_error_info(exc)
    assert is_context_overflow(info)
    assert info.retryable is False


# === queue processor: the semantic error event carries structured fields ===


def test_publish_turn_error_carries_structured_fields(monkeypatch):
    from kollabor_agent import queue_processor as qp

    captured: list[dict] = []

    def fake_publish(source, event_type, **fields):
        captured.append({"type": event_type, **fields})

    monkeypatch.setattr(qp, "publish_semantic", fake_publish)

    exc = AuthenticationError("bad key", "openai", error_code="authentication_error")
    info = structured_error_info(exc)
    qp.publish_turn_error(MagicMock(), f"openai: {exc}", info)

    assert len(captured) == 1
    event = captured[0]
    assert event["type"] == "error"
    assert "openai" in event["message"]  # provider name rides the display text
    assert event["error_class"] == "authentication"
    assert event["code"] == "authentication_error"
    assert event["provider"] == "openai"
    assert event["retryable"] is False


def test_publish_turn_error_retryable_server(monkeypatch):
    from kollabor_agent import queue_processor as qp

    captured: list[dict] = []

    def fake_publish(source, event_type, **fields):
        captured.append({"type": event_type, **fields})

    monkeypatch.setattr(qp, "publish_semantic", fake_publish)

    exc = ServerError("boom", "gemini", status_code=503, retry_after=5.0)
    info = structured_error_info(exc)
    qp.publish_turn_error(MagicMock(), str(exc), info)

    event = captured[0]
    assert event["error_class"] == "server"
    assert event["retryable"] is True


# === ACP boundary: classification rides the exception, not the text ===


def _acp_runner(tmp_path):
    from kollabor.llm import acp_bridge as bridge_mod

    runner = bridge_mod.ACPRunner(
        bridge_mod.ACPProfile.builtin("claude-code", cwd=str(tmp_path))
    )
    runner.session_id = "owned-session"
    runner.generation = 1
    return runner, bridge_mod


def test_acp_auth_error_classifies_authentication(tmp_path):
    runner, bridge_mod = _acp_runner(tmp_path)
    request_error = bridge_mod.acp.RequestError(
        -32000, "authentication required", {"credentials": "secret"}
    )
    exc = runner._request_error(request_error)
    assert exc.error_class == "authentication"
    assert exc.code == -32000
    assert "claude-code" in str(exc)  # provider name prefixed for display only


def test_acp_non_auth_code_with_auth_words_in_text_is_not_authentication(tmp_path):
    """Poison direction: auth words in the message, code says otherwise."""
    runner, bridge_mod = _acp_runner(tmp_path)
    request_error = bridge_mod.acp.RequestError(
        -32001, "invalid api key mentioned in a server-side refusal", {}
    )
    exc = runner._request_error(request_error)
    # Boundary parsing only promotes auth on the auth code; this message text
    # mentioning keys with a non-auth code must not classify authentication.
    assert exc.error_class != "authentication"


def test_acp_refusal_classifies_refusal(tmp_path):
    runner, bridge_mod = _acp_runner(tmp_path)
    request_error = bridge_mod.acp.RequestError(-32001, "usage limit exceeded", {})
    exc = runner._request_error(request_error)
    assert exc.error_class == "refusal"


def test_acp_queue_full_and_images_carry_error_classes(tmp_path):
    from kollabor.llm.acp_bridge import ACPError

    full = ACPError("ACP prompt queue is full", error_class="queue_full")
    images = ACPError("This harness can't take images.", error_class="images_unsupported")
    assert full.error_class == "queue_full"
    assert images.error_class == "images_unsupported"
    # The #549 vector: text carries a provider-name-like prefix; the class
    # carries the verdict. Classification never inspects the text.
    assert structured_error_info(
        ACPError("claude-code: prompt queue is full", error_class="queue_full")
    ).error_class != "authentication"


def test_acp_admission_errors_classify_by_error_class():
    """llm_coordinator's admission switches read error_class, never str."""
    from kollabor.llm.acp_bridge import ACPError

    queue_full = ACPError("claude-code: ACP prompt queue is full", error_class="queue_full")
    images = ACPError("This harness can't take images.", error_class="images_unsupported")
    unrelated = RuntimeError("the disk is full of text")  # unclassified

    assert getattr(queue_full, "error_class", None) == "queue_full"
    assert getattr(images, "error_class", None) == "images_unsupported"
    # "full" in str(unrelated) is true; the typed check must not match it.
    assert getattr(unrelated, "error_class", None) is None


# === api_communication_service: retry classification reads the snapshot ===


def _llm_service(tmp_path):
    from kollabor_ai.api_communication_service import APICommunicationService

    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.use_explicit_tool_accumulation": False,
        "kollabor.llm.max_retries": 3,
    }.get(key, default)

    profile = MagicMock()
    profile.provider = "openai"
    profile.name = "structured-error-test"
    profile.get_model.return_value = "model-not-in-catalog"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.to_dict.return_value = {
        "provider": "openai",
        "model": "model-not-in-catalog",
        "api_key": "sk-test",
    }

    service = APICommunicationService(config, tmp_path, profile)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.model = "model-not-in-catalog"
    provider.supports_vision = False
    service._provider = provider
    service._initialized = True
    service._sleep_or_cancel = AsyncMock()
    return service, provider


@pytest.mark.asyncio
async def test_retry_loop_classification_matches_main_taxonomy(tmp_path):
    """The retry loop follows main's canonical untyped classification.

    Main-classified transient markers (429/5xx/Network error) retry through
    bounded backoff exactly as main's loop does; a message with no marker
    still raises on the first failure. Goal-image retry parity lives in
    tests/unit/test_goal_image_requests.py.
    """
    service, provider = _llm_service(tmp_path)
    provider.call = AsyncMock(
        side_effect=RuntimeError(
            "attempt 429 of 500; Network error mentioned in a sentence"
        )
    )

    with pytest.raises(RuntimeError):
        await service.call_llm([{"role": "user", "content": "hi"}])

    assert provider.call.await_count == 4  # first attempt + 3 retries, like main

    service2, provider2 = _llm_service(tmp_path)
    provider2.call = AsyncMock(side_effect=RuntimeError("opaque provider failure"))

    with pytest.raises(RuntimeError):
        await service2.call_llm([{"role": "user", "content": "hi"}])

    assert provider2.call.await_count == 1


@pytest.mark.asyncio
async def test_retry_loop_still_retries_typed_rate_limit(tmp_path):
    service, provider = _llm_service(tmp_path)
    provider.call = AsyncMock(
        side_effect=RateLimitError("rate limited", "openai", retry_after=0.0)
    )

    with pytest.raises(RateLimitError):
        await service.call_llm([{"role": "user", "content": "hi"}])

    assert provider.call.await_count == 4  # first attempt + 3 retries


@pytest.mark.asyncio
async def test_typed_provider_error_survives_the_retry_boundary(tmp_path):
    """The final raise keeps the typed class instead of rewrapping it (#720)."""
    service, provider = _llm_service(tmp_path)
    provider.call = AsyncMock(
        side_effect=AuthenticationError(
            "Error code: 401 - invalid api key", "openai",
            error_code="authentication_error",
        )
    )

    with pytest.raises(AuthenticationError):
        await service.call_llm([{"role": "user", "content": "hi"}])


# === timeout retry wrapper: typed-only classification (#720) ===
# The wrapper retries the identical provider request once on a timeout.
# That retry duplicates side effects, so it must fire on typed timeouts
# only: an untyped exception whose rendered text says "timed out" is a
# sentence, not a signal (#720).


def test_timeout_classification_reads_type_not_text():
    from kollabor_agent.queue_processor import (
        _is_timeout_exception as is_timeout,
    )

    # The probe: untyped exception whose message mimics a timeout.
    assert is_timeout(RuntimeError("request timed out")) is False
    assert is_timeout(RuntimeError("provider gave up: request timeout hit")) is False
    # Typed timeouts classify by class name / structured class.
    assert is_timeout(APITimeoutError("gave up", "openai")) is True
    assert is_timeout(TimeoutError("wall clock")) is True


@pytest.mark.asyncio
async def test_retry_wrapper_does_not_retry_untyped_timeout_wording():
    """Bismuth's probe: RuntimeError('request timed out') must surface, not retry."""
    from kollabor_agent.queue_processor import QueueProcessor

    class _Harness:
        def __init__(self):
            self._goal_scoped_call_llm = AsyncMock(
                side_effect=RuntimeError("request timed out")
            )

    harness = _Harness()
    with pytest.raises(RuntimeError):
        await QueueProcessor._call_llm_retry_once_on_timeout(harness)
    assert harness._goal_scoped_call_llm.await_count == 1


@pytest.mark.asyncio
async def test_retry_wrapper_still_retries_typed_timeout_once():
    """Typed timeouts keep the one identical retry (#452 contract)."""
    from kollabor_agent.queue_processor import QueueProcessor

    class _Harness:
        def __init__(self):
            self.calls = 0

        async def _goal_scoped_call_llm(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise APITimeoutError("first attempt hit the wall", "openai")
            return "recovered"

    harness = _Harness()
    result = await QueueProcessor._call_llm_retry_once_on_timeout(harness)
    assert result == "recovered"
    assert harness.calls == 2


def test_publish_turn_error_forwards_cause_to_event(monkeypatch):
    """Issue #720 mandates the original cause end to end: info carries the
    safe type name, the semantic event must carry it too (bismuth's probe:
    info.to_dict() had cause, the event omitted it)."""
    from kollabor_agent import queue_processor as qp

    captured: list[dict] = []

    def fake_publish(source, event_type, **fields):
        captured.append({"type": event_type, **fields})

    monkeypatch.setattr(qp, "publish_semantic", fake_publish)

    exc = AuthenticationError(
        "bad key",
        "openai",
        error_code="authentication_error",
        original_error=RuntimeError("sdk cause"),
    )
    info = structured_error_info(exc)
    assert info.cause == "RuntimeError"  # safe type name only
    assert info.to_dict()["cause"] == "RuntimeError"  # never message text

    qp.publish_turn_error(MagicMock(), f"openai: {exc}", info)

    event = captured[0]
    assert event["cause"] == "RuntimeError"


# === round 2, finding A: the run-status surface carries the structured error ===
# With no message state to persist, the route's only run-level error surface
# was controller.add_error(display text). The wire's message-finish chunk is
# the run-status record: it must carry the structured fields too (#720).


@pytest.mark.asyncio
async def test_run_finish_chunk_carries_structured_error():
    from kollabor_engine.routes.messages import _assistant_protocol_stream

    async def empty_stream():
        return
        yield  # pragma: no cover - makes this an async generator

    completion = {
        "completed": True,
        "usage": {
            "inputTokens": 1, "outputTokens": 2, "stopReason": "error",
        },
        "error": {
            "message": "openai: bad key",
            "error_class": "authentication",
            "code": "authentication_error",
            "provider": "openai",
            "retryable": False,
            "cause": "AuthenticationError",
        },
    }
    chunks = []
    async for chunk in _assistant_protocol_stream(empty_stream(), completion):
        chunks.append(chunk)
    finish = [c for c in chunks if getattr(c, "type", "") == "message-finish"][-1]
    assert getattr(finish, "finishReason", None) == "error"
    error = getattr(finish, "error", None)
    assert error is not None, "message-finish must carry the structured error"
    assert error["error_class"] == "authentication"
    assert error["cause"] == "AuthenticationError"
    assert error["code"] == "authentication_error"
    assert error["provider"] == "openai"
    assert error["retryable"] is False


# === round 2, finding B: the ACP error type carries the safe cause ===
# Same floor rule as everywhere else: the original exception's TYPE NAME,
# never its message. It must survive into ACPError and the published
# error event / end frame (bismuth's probe: code/class present, cause
# dropped on the floor).


def test_acp_error_stores_safe_cause():
    from kollabor.llm.acp_bridge import ACPError

    exc = ACPError(
        "harness request failed", code=-32000,
        error_class="authentication", cause="RequestError",
    )
    assert exc.cause == "RequestError"


def test_request_error_forwards_safe_cause():
    import acp as acp_sdk
    from types import SimpleNamespace
    from kollabor.llm.acp_bridge import ACPRunner

    fake_self = SimpleNamespace(
        profile=SimpleNamespace(name="codex", LOGIN_GUIDANCE={}),
        _select_answer=lambda updates: "",
    )
    acp_error = ACPRunner._request_error(
        fake_self, acp_sdk.RequestError(code=-32000, message="invalid api key"),
    )
    assert acp_error.error_class == "authentication"
    assert acp_error.cause == "RequestError"
    assert "invalid api key" not in (acp_error.cause or "")


# === round 2, finding C: API format mismatch is a typed classification ===
# The custom provider's parse boundaries raise a typed error when the
# endpoint returns a non-object payload (bare string body, or an SSE data
# line that is not an object). The queue no longer matches the rendered
# AttributeError text to select the mismatch message — it reads the
# structured class.


def test_structured_error_info_classifies_api_format_mismatch():
    from kollabor_ai.providers.errors import APIFormatMismatchError

    exc = APIFormatMismatchError("not an object", "custom")
    info = structured_error_info(exc)
    assert info.error_class == "api_format_mismatch"
    assert info.error_code == "api_format_mismatch"
    assert info.retryable is False
    assert info.provider == "custom"


def test_custom_provider_rejects_non_object_json_body():
    from unittest.mock import patch
    from kollabor_ai.providers.custom_provider import CustomProvider
    from kollabor_ai.providers.errors import APIFormatMismatchError

    class _Resp:
        status = 200
        headers = {}

        async def json(self):
            return "just a string body"

        async def text(self):
            return '"just a string body"'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Session:
        def post(self, *args, **kwargs):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    def _client_session(*args, **kwargs):
        return _Session()

    provider = CustomProvider.__new__(CustomProvider)
    provider.base_url = "http://localhost:1"
    provider._provider_name = "custom"
    provider.provider_type = "custom"
    provider.model = "test-model"
    provider.resolve_media = False
    provider.config = MagicMock(api_key=None, model="test-model", timeout=5)
    with patch("kollabor_ai.providers.custom_provider.aiohttp.ClientSession", _client_session):
        with pytest.raises(APIFormatMismatchError):
            import asyncio
            asyncio.run(provider._make_request(messages=[], tools=None, stream=False))


def test_custom_provider_rejects_non_object_sse_data_line():
    from kollabor_ai.providers.custom_provider import _coerce_sse_object
    from kollabor_ai.providers.errors import APIFormatMismatchError

    with pytest.raises(APIFormatMismatchError):
        _coerce_sse_object("bare string payload", "custom")
    assert _coerce_sse_object({"choices": []}, "custom") == {"choices": []}
