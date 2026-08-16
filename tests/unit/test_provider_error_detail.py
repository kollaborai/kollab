"""Provider error bodies must reach the message.

``str(httpx.HTTPStatusError)`` is only ``"Client error '400 Bad Request' for
url ..."`` -- it never says why. Discarding the response body turned every 4xx
into an unactionable status line (observed live: a Gemini 400 whose real cause
was stated in the body and thrown away), and left the 400 handler's
context-length check matching against a string that could never contain it.
"""

import httpx
import pytest

from kollabor_ai.providers.errors import (
    ContextLengthExceededError,
    InvalidRequestError,
    _response_detail,
    map_httpx_error,
)


def _status_error(
    status: int, *, json_body=None, text_body=None, url="https://api.test/v1/chat"
):
    request = httpx.Request("POST", url)
    if json_body is not None:
        response = httpx.Response(status, json=json_body, request=request)
    else:
        response = httpx.Response(status, text=text_body or "", request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected a status error")


# -- detail extraction ------------------------------------------------------


def test_google_style_error_message_is_extracted():
    detail = _response_detail(
        _status_error(
            400, json_body={"error": {"message": "API key not valid."}}
        ).response
    )
    assert detail == "API key not valid."


def test_google_status_used_when_message_absent():
    detail = _response_detail(
        _status_error(400, json_body={"error": {"status": "INVALID_ARGUMENT"}}).response
    )
    assert detail == "INVALID_ARGUMENT"


def test_string_error_field_is_extracted():
    detail = _response_detail(
        _status_error(400, json_body={"error": "model not found"}).response
    )
    assert detail == "model not found"


@pytest.mark.parametrize("key", ["message", "detail", "error_description"])
def test_top_level_message_keys_are_extracted(key):
    detail = _response_detail(_status_error(400, json_body={key: "bad thing"}).response)
    assert detail == "bad thing"


def test_plain_text_body_is_used():
    detail = _response_detail(
        _status_error(400, text_body="  upstream exploded  ").response
    )
    assert detail == "upstream exploded"


def test_empty_body_yields_empty_detail():
    assert _response_detail(_status_error(400, text_body="").response) == ""


def test_detail_is_length_bounded():
    detail = _response_detail(
        _status_error(400, json_body={"error": {"message": "x" * 5000}}).response
    )
    assert len(detail) == 500


def test_unreadable_response_never_raises():
    class _Broken:
        def json(self):
            raise ValueError("nope")

        @property
        def text(self):
            raise ValueError("also nope")

    assert _response_detail(_Broken()) == ""


# -- wired into the mapper --------------------------------------------------


def test_mapped_error_includes_provider_explanation():
    error = map_httpx_error(
        _status_error(
            400, json_body={"error": {"message": "Unknown field: temperature"}}
        ),
        "gemini",
    )
    assert isinstance(error, InvalidRequestError)
    assert "Unknown field: temperature" in error.message


def test_context_length_is_classified_from_the_body():
    """Previously unreachable: the check ran against httpx's generic string."""
    error = map_httpx_error(
        _status_error(
            400,
            json_body={
                "error": {"message": "This model's maximum context_length is 8192"}
            },
        ),
        "openai",
    )
    assert isinstance(error, ContextLengthExceededError)


def test_generic_400_without_context_hint_stays_invalid_request():
    error = map_httpx_error(
        _status_error(400, json_body={"error": {"message": "bad tool schema"}}),
        "openai",
    )
    assert isinstance(error, InvalidRequestError)
    assert not isinstance(error, ContextLengthExceededError)


def test_credentials_in_the_url_are_still_redacted():
    error = map_httpx_error(
        _status_error(
            400,
            json_body={"error": {"message": "API key not valid."}},
            url="https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=AIzaTOPSECRETVALUE",
        ),
        "gemini",
    )
    assert "AIzaTOPSECRETVALUE" not in error.safe_message
    assert "[REDACTED]" in error.safe_message
    assert "API key not valid." in error.safe_message


def test_body_detail_does_not_leak_a_bearer_token():
    error = map_httpx_error(
        _status_error(
            401,
            json_body={
                "error": {
                    "message": "bad creds: Bearer sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                }
            },
        ),
        "openai",
    )
    assert "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in error.safe_message


def test_timeout_and_transport_errors_are_unaffected():
    request = httpx.Request("POST", "https://api.test/v1/chat")
    timeout = map_httpx_error(httpx.ReadTimeout("slow", request=request), "openai")
    assert timeout.error_code == "timeout"

    transport = map_httpx_error(
        httpx.ConnectError("refused", request=request), "openai"
    )
    assert transport.error_code == "connection_error"


def test_retry_after_header_still_parsed_with_detail_present():
    request = httpx.Request("POST", "https://api.test/v1/chat")
    response = httpx.Response(
        429,
        json={"error": {"message": "slow down"}},
        headers={"retry-after": "7"},
        request=request,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error = map_httpx_error(exc, "openai")
    assert error.retry_after == 7
    assert "slow down" in error.message


# -- streaming responses ----------------------------------------------------


def test_unread_streaming_response_yields_no_detail():
    """Why the Gemini fix needed aread(): an unread body is unreachable.

    httpx raises ResponseNotRead when .json()/.text is touched on a streaming
    response that has not been read, so the detail silently comes back empty
    and the user sees only the bare status line.
    """
    request = httpx.Request("POST", "https://api.test/v1/chat")
    response = httpx.Response(
        400,
        json={"error": {"message": "API key not valid."}},
        request=request,
    )
    # Simulate httpx's streaming state: body present on the wire, not read.
    response.is_stream_consumed = False
    del response._content

    assert _response_detail(response) == ""


def test_read_streaming_response_yields_detail():
    request = httpx.Request("POST", "https://api.test/v1/chat")
    response = httpx.Response(
        400,
        json={"error": {"message": "API key not valid."}},
        request=request,
    )
    response.read()
    assert _response_detail(response) == "API key not valid."
