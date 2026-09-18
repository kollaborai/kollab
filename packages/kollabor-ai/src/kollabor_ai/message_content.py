"""Shared message-content normalization and provider serialization.

User-facing clients may submit text plus image parts.  Conversation history
must remain JSON-safe and must not retain image bytes, data URLs, or local file
paths, so pasted images are kept in a bounded in-memory store and represented
by opaque media IDs until a provider request is built.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from typing import Any, Callable, Iterable, Mapping, Optional

MessagePart = dict[str, Any]
MessageContent = str | list[MessagePart]
MediaResolver = Callable[[str], Optional[str]]

DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
_DATA_URI_PREFIX = re.compile(r"^data:(image/[a-z0-9.+-]+);base64$", re.I)
_BASE64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


class MessageContentError(ValueError):
    """Raised when a message contains an unsupported or unsafe content part."""


def _parse_image_data_url(value: str) -> tuple[str, bytes]:
    """Validate and decode an image data URL without retaining its bytes."""
    prefix, separator, encoded = value.partition(",")
    match = _DATA_URI_PREFIX.fullmatch(prefix.strip())
    if not separator or match is None:
        raise MessageContentError(
            "image content must be a base64 data URL or an HTTPS URL"
        )

    encoded = "".join(encoded.split())
    if not encoded or not _BASE64.fullmatch(encoded) or len(encoded) % 4:
        raise MessageContentError("image data URL contains invalid base64")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MessageContentError("image data URL contains invalid base64") from exc
    if not decoded:
        raise MessageContentError("image data URL is empty")
    return match.group(1).lower(), decoded


def _is_image_data_url(value: str) -> bool:
    return value.lower().startswith("data:image/")


class EphemeralImageStore:
    """Bounded, process-local storage for pasted image data URLs."""

    def __init__(
        self,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_IMAGE_BYTES,
    ) -> None:
        self.max_image_bytes = max_image_bytes
        self.max_total_bytes = max_total_bytes
        self._images: dict[str, tuple[str, bytes]] = {}
        self._total_bytes = 0

    def add_data_url(self, value: str) -> str:
        """Store a validated image data URL and return an opaque media ID."""
        if not isinstance(value, str):
            raise MessageContentError("image data must be a string")
        media_type, decoded = _parse_image_data_url(value)
        if len(decoded) > self.max_image_bytes:
            if self.max_image_bytes >= 1024 * 1024:
                limit = f"{self.max_image_bytes // (1024 * 1024)} MB"
            else:
                limit = f"{self.max_image_bytes} byte"
            raise MessageContentError(f"image exceeds the {limit} limit")
        if self._total_bytes + len(decoded) > self.max_total_bytes:
            raise MessageContentError(
                "image attachments exceed the session memory limit"
            )

        media_id = f"img_{uuid.uuid4().hex}"
        self._images[media_id] = (media_type, decoded)
        self._total_bytes += len(decoded)
        return media_id

    def resolve(self, media_id: str) -> Optional[str]:
        """Return a provider-ready data URL for a live media ID."""
        stored = self._images.get(media_id)
        if stored is None:
            return None
        media_type, decoded = stored
        encoded = base64.b64encode(decoded).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def clear(self) -> None:
        """Release all session media."""
        self._images.clear()
        self._total_bytes = 0

    def remove(self, media_id: str) -> None:
        """Release one image, used to roll back a failed normalization."""
        stored = self._images.pop(media_id, None)
        if stored is not None:
            self._total_bytes -= len(stored[1])

    @property
    def count(self) -> int:
        return len(self._images)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes


def _canonical_source(source: Mapping[str, Any]) -> MessagePart:
    kind = str(source.get("kind") or "").strip().lower()
    if kind == "managed_upload":
        media_id = source.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise MessageContentError("managed image source is missing media_id")
        result: MessagePart = {"kind": kind, "media_id": media_id}
        media_type = source.get("media_type")
        if isinstance(media_type, str) and media_type:
            result["media_type"] = media_type
        return result
    if kind == "url":
        url = source.get("url")
        if not isinstance(url, str) or not url.lower().startswith("https://"):
            raise MessageContentError("image URLs must use HTTPS")
        return {"kind": kind, "url": url}
    if kind == "provider_file":
        file_id = source.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise MessageContentError("provider image source is missing file_id")
        return {"kind": kind, "file_id": file_id}
    raise MessageContentError(f"unsupported image source kind: {kind or 'unknown'}")


def _canonical_image_part(
    part: Mapping[str, Any],
    store: EphemeralImageStore,
    created_media_ids: list[str],
) -> MessagePart:
    raw_image = part.get("image")
    source = part.get("source")

    if isinstance(source, Mapping):
        canonical_source = _canonical_source(source)
    elif isinstance(raw_image, str) and _is_image_data_url(raw_image):
        media_type, _ = _parse_image_data_url(raw_image)
        media_id = store.add_data_url(raw_image)
        created_media_ids.append(media_id)
        canonical_source = {
            "kind": "managed_upload",
            "media_id": media_id,
            "media_type": media_type,
        }
    elif isinstance(raw_image, str) and raw_image.lower().startswith("https://"):
        canonical_source = {"kind": "url", "url": raw_image}
    elif isinstance(raw_image, str) and raw_image.lower().startswith(
        ("http://", "https://")
    ):
        raise MessageContentError("image URLs must use HTTPS")
    elif isinstance(raw_image, str) and raw_image.lower().startswith("data:"):
        raise MessageContentError("image content must be a base64 image data URL")
    else:
        raise MessageContentError(
            "image part must contain a base64 image data URL or an HTTPS URL"
        )

    normalized: MessagePart = {"type": "image", "source": canonical_source}
    detail = part.get("detail")
    if detail in ("auto", "low", "high"):
        normalized["detail"] = detail
    return normalized


def normalize_message_content(
    content: Any,
    store: EphemeralImageStore,
) -> MessageContent:
    """Normalize client content into text or canonical JSON-safe parts."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise MessageContentError("message content must be text or a list of parts")

    normalized: list[MessagePart] = []
    has_image = False
    created_media_ids: list[str] = []
    try:
        for part in content:
            if not isinstance(part, Mapping):
                raise MessageContentError("message parts must be objects")
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise MessageContentError("text part is missing text")
                normalized.append({"type": "text", "text": text})
            elif part_type == "image":
                normalized.append(_canonical_image_part(part, store, created_media_ids))
                has_image = True
            else:
                raise MessageContentError(
                    f"unsupported user message part: {part_type or 'unknown'}"
                )
    except Exception:
        for media_id in created_media_ids:
            store.remove(media_id)
        raise

    if not has_image:
        return "\n".join(part["text"] for part in normalized)
    return normalized


def contains_image_content(content: Any) -> bool:
    """Return whether content contains an image part."""
    return isinstance(content, list) and any(
        isinstance(part, Mapping) and part.get("type") == "image" for part in content
    )


def content_to_text(content: Any) -> str:
    """Render content for logs, terminal output, validation, and context hooks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    rendered: list[str] = []
    image_index = 0
    for part in content:
        if not isinstance(part, Mapping):
            continue
        part_type = part.get("type")
        if part_type in ("text", "reasoning"):
            text = part.get("text")
            if isinstance(text, str) and text:
                rendered.append(text)
        elif part_type == "image":
            image_index += 1
            rendered.append(f"[image{image_index}]")
    return "\n".join(rendered)


def combine_message_contents(contents: Iterable[MessageContent]) -> MessageContent:
    """Combine queued messages while retaining image parts."""
    values = list(contents)
    if not any(isinstance(value, list) for value in values):
        return "\n".join(value for value in values if isinstance(value, str))

    parts: list[MessagePart] = []
    for value in values:
        if isinstance(value, str):
            if value:
                parts.append({"type": "text", "text": value})
        else:
            parts.extend(value)
    return parts


def prepend_text(prefix: str, content: MessageContent) -> MessageContent:
    """Prepend runtime context without flattening image content."""
    if not prefix:
        return content
    if isinstance(content, str):
        return prefix + content
    return [{"type": "text", "text": prefix}, *content]


def _resolve_image_reference(
    part: Mapping[str, Any], resolver: Optional[MediaResolver]
) -> tuple[str | dict[str, str], Mapping[str, Any]]:
    source = part.get("source")
    if not isinstance(source, Mapping):
        raise MessageContentError("image part is missing its canonical source")
    kind = source.get("kind")
    if kind == "managed_upload":
        media_id = source.get("media_id")
        if not isinstance(media_id, str) or resolver is None:
            raise MessageContentError("managed image is no longer available")
        resolved = resolver(media_id)
        if not resolved:
            raise MessageContentError("managed image is no longer available")
        return resolved, source
    if kind == "url":
        url = source.get("url")
        if not isinstance(url, str) or not url.lower().startswith("https://"):
            raise MessageContentError("image URLs must use HTTPS")
        return url, source
    if kind == "provider_file":
        file_id = source.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise MessageContentError("provider image source is missing file_id")
        return {"file_id": file_id}, source
    raise MessageContentError(f"unsupported image source kind: {kind or 'unknown'}")


def serialize_openai_chat_content(
    content: MessageContent,
    resolver: Optional[MediaResolver] = None,
) -> str | list[MessagePart]:
    """Serialize canonical content for OpenAI-compatible Chat Completions."""
    if isinstance(content, str):
        return content
    output: list[MessagePart] = []
    for part in content:
        if part.get("type") == "text":
            output.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image":
            reference, source = _resolve_image_reference(part, resolver)
            if not isinstance(reference, str):
                raise MessageContentError(
                    "Chat Completions images require a URL or data URL"
                )
            image_url: MessagePart = {"url": reference}
            detail = part.get("detail")
            if detail in ("auto", "low", "high"):
                image_url["detail"] = detail
            output.append({"type": "image_url", "image_url": image_url})
        else:
            raise MessageContentError("unsupported canonical content part")
    return output


def serialize_openai_responses_content(
    content: MessageContent,
    resolver: Optional[MediaResolver] = None,
) -> str | list[MessagePart]:
    """Serialize canonical content for the OpenAI Responses API."""
    if isinstance(content, str):
        return content
    output: list[MessagePart] = []
    for part in content:
        if part.get("type") == "text":
            output.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image":
            reference, _ = _resolve_image_reference(part, resolver)
            image_part: MessagePart = {"type": "input_image"}
            if isinstance(reference, str):
                image_part["image_url"] = reference
            else:
                image_part.update(reference)
            detail = part.get("detail")
            if detail in ("auto", "low", "high"):
                image_part["detail"] = detail
            output.append(image_part)
        else:
            raise MessageContentError("unsupported canonical content part")
    return output


def serialize_anthropic_content(
    content: MessageContent,
    resolver: Optional[MediaResolver] = None,
) -> str | list[MessagePart]:
    """Serialize canonical content for Anthropic's content-block API."""
    if isinstance(content, str):
        return content
    output: list[MessagePart] = []
    for part in content:
        if part.get("type") == "text":
            output.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image":
            reference, _ = _resolve_image_reference(part, resolver)
            if not isinstance(reference, str):
                raise MessageContentError("Anthropic images require a URL or data URL")
            if _is_image_data_url(reference):
                media_type, decoded = _parse_image_data_url(reference)
                output.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(decoded).decode("ascii"),
                        },
                    }
                )
            else:
                output.append(
                    {"type": "image", "source": {"type": "url", "url": reference}}
                )
        else:
            raise MessageContentError("unsupported canonical content part")
    return output


def serialize_gemini_parts(
    content: MessageContent,
    resolver: Optional[MediaResolver] = None,
) -> list[MessagePart]:
    """Serialize canonical content for Gemini ``contents[].parts``."""
    if isinstance(content, str):
        return [{"text": content}]
    output: list[MessagePart] = []
    for part in content:
        if part.get("type") == "text":
            output.append({"text": str(part.get("text") or "")})
        elif part.get("type") == "image":
            reference, source = _resolve_image_reference(part, resolver)
            if isinstance(reference, str) and _is_image_data_url(reference):
                media_type, decoded = _parse_image_data_url(reference)
                output.append(
                    {
                        "inline_data": {
                            "mime_type": media_type,
                            "data": base64.b64encode(decoded).decode("ascii"),
                        }
                    }
                )
            elif source.get("kind") == "provider_file" and isinstance(reference, dict):
                output.append(
                    {
                        "file_data": {
                            "file_uri": reference["file_id"],
                            "mime_type": source.get("media_type", "image/*"),
                        }
                    }
                )
            else:
                raise MessageContentError(
                    "Gemini images require a pasted data URL or provider file"
                )
        else:
            raise MessageContentError("unsupported canonical content part")
    return output


def redact_media_data(value: Any) -> Any:
    """Copy a payload while replacing provider-ready image data URLs."""
    if isinstance(value, str):
        return "[image data redacted]" if _is_image_data_url(value) else value
    if isinstance(value, list):
        return [redact_media_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_media_data(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_media_data(item) for key, item in value.items()}
    return value
