import base64

import pytest

from kollabor_ai.message_content import (
    EphemeralImageStore,
    MessageContentError,
    combine_message_contents,
    content_to_text,
    normalize_message_content,
    prepend_text,
    redact_media_data,
    serialize_anthropic_content,
    serialize_gemini_parts,
    serialize_openai_chat_content,
    serialize_openai_responses_content,
)

PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


def test_normalize_pasted_image_keeps_bytes_out_of_history():
    store = EphemeralImageStore()

    normalized = normalize_message_content(
        [
            {"type": "text", "text": "read this"},
            {"type": "image", "image": PNG_DATA_URL},
            {"type": "text", "text": "then explain it"},
            {"type": "image", "image": "https://example.com/diagram.png"},
        ],
        store,
    )

    assert isinstance(normalized, list)
    assert normalized[0] == {"type": "text", "text": "read this"}
    assert normalized[1]["source"] == {
        "kind": "managed_upload",
        "media_id": normalized[1]["source"]["media_id"],
        "media_type": "image/png",
    }
    assert "image" not in normalized[1]
    assert normalized[3] == {
        "type": "image",
        "source": {"kind": "url", "url": "https://example.com/diagram.png"},
    }
    assert store.count == 1
    assert store.resolve(normalized[1]["source"]["media_id"]) == PNG_DATA_URL
    assert (
        content_to_text(normalized) == "read this\n[image1]\nthen explain it\n[image2]"
    )


def test_provider_serializers_resolve_canonical_image_at_wire_boundary():
    store = EphemeralImageStore()
    content = normalize_message_content(
        [{"type": "text", "text": "inspect"}, {"type": "image", "image": PNG_DATA_URL}],
        store,
    )

    assert serialize_openai_chat_content(content, store.resolve) == [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
    ]
    assert serialize_openai_responses_content(content, store.resolve) == [
        {"type": "input_text", "text": "inspect"},
        {"type": "input_image", "image_url": PNG_DATA_URL},
    ]
    assert serialize_anthropic_content(content, store.resolve) == [
        {"type": "text", "text": "inspect"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"),
            },
        },
    ]
    assert serialize_gemini_parts(content, store.resolve) == [
        {"text": "inspect"},
        {"inline_data": {"mime_type": "image/png", "data": "iVBORw0KGgo="}},
    ]


def test_image_content_composition_and_redaction_are_safe():
    store = EphemeralImageStore()
    normalized = normalize_message_content(
        [{"type": "image", "image": PNG_DATA_URL}], store
    )

    combined = combine_message_contents(["before", normalized])
    prefixed = prepend_text("context: ", combined)
    assert content_to_text(prefixed) == "context: \nbefore\n[image1]"
    assert redact_media_data({"content": PNG_DATA_URL}) == {
        "content": "[image data redacted]"
    }
    assert redact_media_data({"content": normalized}) == {"content": normalized}


@pytest.mark.parametrize(
    "content, message",
    [
        (
            [{"type": "image", "image": "data:text/plain;base64,SGk="}],
            "base64 image data URL",
        ),
        (
            [{"type": "image", "image": "http://example.com/image.png"}],
            "must use HTTPS",
        ),
        ([{"type": "image", "image": "not-an-image"}], "base64 image data URL"),
    ],
)
def test_normalize_rejects_unsafe_image_sources(content, message):
    with pytest.raises(MessageContentError, match=message):
        normalize_message_content(content, EphemeralImageStore())


def test_normalize_enforces_per_image_limit():
    with pytest.raises(MessageContentError, match="1 byte limit"):
        normalize_message_content(
            [{"type": "image", "image": "data:image/png;base64,AAE="}],
            EphemeralImageStore(max_image_bytes=1),
        )


def test_failed_normalization_rolls_back_new_images():
    store = EphemeralImageStore()
    normalize_message_content([{"type": "image", "image": PNG_DATA_URL}], store)
    before = (store.count, store.total_bytes)

    with pytest.raises(MessageContentError, match="unsupported user message part"):
        normalize_message_content(
            [
                {"type": "image", "image": PNG_DATA_URL},
                {"type": "audio", "data": "not-supported"},
            ],
            store,
        )

    assert (store.count, store.total_bytes) == before
