"""Private managed storage for hosted image-generation results.

The provider may return image bytes as a base64 string.  This module is the
only boundary that decodes those bytes.  Callers receive an opaque media ID;
paths, raw bytes, and base64 are never part of the returned content model.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .providers.models import GeneratedImageContent

DEFAULT_MAX_GENERATED_IMAGE_BYTES = 50 * 1024 * 1024
_MEDIA_ID_RE = re.compile(r"^img_[A-Za-z0-9_-]{16,64}$")
_MEDIA_TYPE_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class GeneratedImageArtifactError(ValueError):
    """Raised when a generated image cannot be safely persisted."""


@dataclass(frozen=True)
class _StoredGeneratedImage:
    content: GeneratedImageContent
    path: Path
    byte_count: int
    sha256: str


def redact_generated_image_data(value: Any) -> Any:
    """Copy a payload while replacing hosted image result data.

    The redaction is deliberately scoped to ``image_generation_call`` items so
    unrelated provider payloads keep their existing shape.
    """

    def redact(node: Any, in_image_call: bool = False) -> Any:
        if isinstance(node, list):
            return [redact(item, in_image_call) for item in node]
        if isinstance(node, tuple):
            return tuple(redact(item, in_image_call) for item in node)
        if not isinstance(node, dict):
            return node

        is_image_call = in_image_call or node.get("type") == "image_generation_call"
        output = {}
        for key, item in node.items():
            if is_image_call and key in {"result", "data", "b64_json", "base64"}:
                output[key] = "[generated image data redacted]"
            else:
                output[key] = redact(item, is_image_call)
        return output

    return redact(value)


def _safe_metadata_text(value: Any, max_length: int = 1000) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:max_length] or None


def _safe_provider_reference(value: Any) -> Optional[str]:
    cleaned = _safe_metadata_text(value, 256)
    if cleaned and not any(marker in cleaned for marker in ("/", "\\", ":")):
        return cleaned
    return None


def _decode_base64_result(result: Any, max_bytes: int) -> bytes:
    if isinstance(result, dict):
        for key in ("data", "b64_json", "base64"):
            if isinstance(result.get(key), str):
                result = result[key]
                break

    if not isinstance(result, str) or not result:
        raise GeneratedImageArtifactError("generated image result is empty")

    encoded = result
    if result.startswith("data:"):
        prefix, separator, encoded = result.partition(",")
        if not separator or not prefix.lower().startswith("data:image/"):
            raise GeneratedImageArtifactError("generated image data URL is invalid")

    encoded = "".join(encoded.split())
    if not encoded:
        raise GeneratedImageArtifactError("generated image result is empty")
    if len(encoded) > ((max_bytes + 2) * 4 // 3) + 4:
        raise GeneratedImageArtifactError("generated image exceeds the size limit")

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GeneratedImageArtifactError(
            "generated image result is invalid base64"
        ) from exc

    if not decoded:
        raise GeneratedImageArtifactError("generated image result is empty")
    if len(decoded) > max_bytes:
        raise GeneratedImageArtifactError("generated image exceeds the size limit")
    return decoded


def _jpeg_dimensions(data: bytes) -> tuple[Optional[int], Optional[int]]:
    index = 2
    sof_markers = set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8))
    sof_markers |= set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0))
    while index + 9 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9):
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width or None, height or None
        index += segment_length
    return None, None


def _image_metadata(data: bytes) -> tuple[str, Optional[int], Optional[int]]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise GeneratedImageArtifactError("generated PNG signature is invalid")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return "image/png", width or None, height or None

    if data.startswith(b"\xff\xd8\xff"):
        width, height = _jpeg_dimensions(data)
        return "image/jpeg", width, height

    if data.startswith(b"RIFF") and len(data) >= 16 and data[8:12] == b"WEBP":
        width = height = None
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        elif chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = 1 + (bits & 0x3FFF)
            height = 1 + ((bits >> 14) & 0x3FFF)
        return "image/webp", width, height

    raise GeneratedImageArtifactError("generated image format is unsupported")


class GeneratedImageArtifactStore:
    """Store generated images under a private, session-scoped directory."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_GENERATED_IMAGE_BYTES,
    ) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
        self._artifacts: dict[str, _StoredGeneratedImage] = {}
        self._ensure_private_root()

    def _ensure_private_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def write_base64(
        self,
        result: Any,
        *,
        revised_prompt: Any = None,
        provider_reference: Any = None,
    ) -> GeneratedImageContent:
        """Decode, validate, and atomically persist one generated image."""

        from .providers.models import GeneratedImageContent

        data = _decode_base64_result(result, self.max_bytes)
        media_type, width, height = _image_metadata(data)
        media_id = f"img_{secrets.token_urlsafe(24)}"
        suffix = _MEDIA_TYPE_SUFFIX[media_type]
        final_path = self.root / f"{media_id}{suffix}"
        temporary_path = self.root / f".{media_id}.part"

        try:
            with temporary_path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, final_path)
            os.chmod(final_path, 0o600)
        except (OSError, ValueError) as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise GeneratedImageArtifactError(
                "generated image could not be persisted"
            ) from exc

        content = GeneratedImageContent(
            media_id=media_id,
            media_type=media_type,
            width=width,
            height=height,
            revised_prompt=_safe_metadata_text(revised_prompt),
            provider_reference=_safe_provider_reference(provider_reference),
        )
        self._artifacts[media_id] = _StoredGeneratedImage(
            content=content,
            path=final_path,
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        return content

    def _stored(self, media_id: str) -> Optional[_StoredGeneratedImage]:
        if not isinstance(media_id, str) or not _MEDIA_ID_RE.fullmatch(media_id):
            return None
        return self._artifacts.get(media_id)

    def open_media(self, media_id: str) -> bool:
        """Open a stored image using the host's default image application."""

        stored = self._stored(media_id)
        if stored is None or not stored.path.is_file():
            return False

        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(stored.path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            elif sys.platform.startswith("win"):
                os.startfile(str(stored.path))  # type: ignore[attr-defined]
            else:
                opener = shutil.which("xdg-open")
                if not opener:
                    return False
                subprocess.Popen(
                    [opener, str(stored.path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError:
            return False
        return True

    def _path_for_testing(self, media_id: str) -> Optional[Path]:
        """Return an internal path for tests; never include it in user output."""
        stored = self._stored(media_id)
        return stored.path if stored else None
