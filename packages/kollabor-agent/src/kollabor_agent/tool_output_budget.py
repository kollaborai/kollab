"""Bound tool output without throwing away the complete result.

Tool output has two different pressure points: one result can be too large, or
several individually acceptable results can overflow the same model request.
This module owns the lossless side of that boundary. The model receives a
bounded preview and a path; the exact output is kept in a managed ``.output``
artifact.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

TOOL_OUTPUT_PATH_KEY = "tool_output_path"
TOOL_OUTPUT_RAW_CHARS_KEY = "tool_output_raw_chars"
TOOL_OUTPUT_RAW_BYTES_KEY = "tool_output_raw_bytes"
TOOL_OUTPUT_SPILLED_KEY = "tool_output_spilled"
TOOL_OUTPUT_SPILL_REASON_KEY = "tool_output_spill_reason"


def preview_text(text: str, max_chars: int) -> str:
    """Return a head preview, preferring a complete line at the boundary."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    head = text[:max_chars]
    newline = head.rfind("\n")
    if newline > max_chars // 2:
        head = head[:newline]
    return head


def _bounded_omission(limit: int, subject: str) -> str:
    """Return an explicit omission notice that never exceeds ``limit``."""
    if limit <= 0:
        return ""
    notice = f"[{subject} omitted; complete output is in artifact metadata]"
    return notice[:limit]


def _safe_name(value: Any, fallback: str) -> str:
    text = str(value or fallback)
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return (text or fallback)[:80]


def _result_text_attribute(result: Any) -> str:
    return "output" if bool(getattr(result, "success", False)) else "error"


def _result_metadata(result: Any) -> dict[str, Any]:
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        setattr(result, "metadata", metadata)
    return metadata


def _as_positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


class ToolOutputArtifactStore:
    """Persist complete tool output and render model-safe pointers."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()

    def write_output(self, text: str, *, tool_id: Any, tool_type: Any) -> Path:
        """Write ``text`` to a private, collision-resistant ``.output`` file."""
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            logger.debug(
                "Could not tighten tool-output directory permissions", exc_info=True
            )

        prefix = f"{_safe_name(tool_type, 'tool')}-{_safe_name(tool_id, 'call')}"
        for _ in range(3):
            path = self.root / f"{prefix}-{uuid.uuid4().hex[:12]}.output"
            try:
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(text)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    logger.debug(
                        "Could not tighten tool-output file permissions", exc_info=True
                    )
                return path
            except FileExistsError:
                continue
        raise FileExistsError(
            f"Could not allocate a unique tool-output artifact in {self.root}"
        )

    @staticmethod
    def pointer(path: Path | str, preview: str = "", reason: str = "") -> str:
        """Build the bounded text that is sent to the model."""
        lines = [f"[tool output saved to .output file: {path}]"]
        if reason:
            lines.append(f"[spill reason: {reason}]")
        if preview:
            lines.extend(["", "Preview:", preview])
        return "\n".join(lines)

    @staticmethod
    def existing_path(result: Any) -> Optional[Path]:
        value = _result_metadata(result).get(TOOL_OUTPUT_PATH_KEY)
        if not value:
            return None
        path = Path(str(value)).expanduser()
        return path if path.exists() else None

    def spill_result(
        self,
        result: Any,
        *,
        reason: str,
        preview_chars: int,
        text: Optional[str] = None,
    ) -> Optional[Path]:
        """Spill a result and replace its model-visible value with a pointer.

        Write failures are contained: the result becomes a bounded warning so a
        filesystem problem cannot turn into a failed LLM turn.
        """
        attribute = _result_text_attribute(result)
        original = getattr(result, attribute, "") if text is None else text
        if original is None:
            original = ""
        original = str(original)
        metadata = _result_metadata(result)
        raw_chars = _as_positive_int(
            metadata.get(TOOL_OUTPUT_RAW_CHARS_KEY), len(original)
        )
        raw_bytes = len(original.encode("utf-8", errors="replace"))
        metadata[TOOL_OUTPUT_RAW_CHARS_KEY] = raw_chars
        metadata[TOOL_OUTPUT_RAW_BYTES_KEY] = raw_bytes
        metadata[TOOL_OUTPUT_SPILL_REASON_KEY] = reason

        path = self.existing_path(result)
        try:
            if path is None:
                path = self.write_output(
                    original,
                    tool_id=getattr(result, "tool_id", "call"),
                    tool_type=getattr(result, "tool_type", "tool"),
                )
                metadata[TOOL_OUTPUT_PATH_KEY] = str(path)
            metadata[TOOL_OUTPUT_SPILLED_KEY] = True
            setattr(
                result,
                attribute,
                self.pointer(
                    path,
                    preview_text(original, preview_chars),
                    reason=reason,
                ),
            )
            return path
        except Exception as exc:
            metadata["tool_output_spill_failed"] = str(exc)
            logger.warning(
                "Could not save tool output for %s:%s: %s",
                getattr(result, "tool_type", "tool"),
                getattr(result, "tool_id", "call"),
                exc,
            )
            bounded = preview_text(original, max(1, preview_chars))
            setattr(
                result,
                attribute,
                bounded + "\n\n[tool output exceeded the inline budget, but the full "
                "output could not be saved.]",
            )
            return None

    def prepare_result(
        self,
        result: Any,
        *,
        max_chars: int,
        preview_chars: int,
    ) -> bool:
        """Apply the per-result threshold. Return whether it is spilled."""
        attribute = _result_text_attribute(result)
        text = getattr(result, attribute, "") or ""
        if not isinstance(text, str):
            text = str(text)
            setattr(result, attribute, text)

        # A producer such as file_read may already have saved the exact output.
        if self.existing_path(result) is not None:
            return bool(_result_metadata(result).get(TOOL_OUTPUT_SPILLED_KEY, True))
        if max_chars <= 0 or len(text) <= max_chars:
            return False
        return (
            self.spill_result(
                result,
                reason="per_result",
                preview_chars=preview_chars,
            )
            is not None
        )


@dataclass
class ToolOutputPackStats:
    """Telemetry for one completed tool batch."""

    result_count: int = 0
    raw_chars: int = 0
    model_chars: int = 0
    spilled_count: int = 0
    batch_limit_chars: Optional[int] = None
    remaining_chars: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_count": self.result_count,
            "raw_chars": self.raw_chars,
            "model_chars": self.model_chars,
            "spilled_count": self.spilled_count,
            "batch_limit_chars": self.batch_limit_chars,
            "remaining_chars": self.remaining_chars,
        }


def pack_tool_results(
    results: Iterable[Any],
    store: ToolOutputArtifactStore,
    *,
    max_result_chars: int,
    preview_chars: int,
    batch_limit_chars: Optional[int],
) -> ToolOutputPackStats:
    """Make a complete result batch fit while preserving every result envelope."""
    result_list = list(results)
    stats = ToolOutputPackStats(
        result_count=len(result_list),
        batch_limit_chars=batch_limit_chars,
        remaining_chars=batch_limit_chars,
    )

    for result in result_list:
        store.prepare_result(
            result,
            max_chars=max_result_chars,
            preview_chars=preview_chars,
        )

    remaining = batch_limit_chars
    for result in result_list:
        attribute = _result_text_attribute(result)
        visible = str(getattr(result, attribute, "") or "")
        metadata = _result_metadata(result)
        raw_chars = _as_positive_int(
            metadata.get(TOOL_OUTPUT_RAW_CHARS_KEY), len(visible)
        )
        stats.raw_chars += raw_chars

        if remaining is not None and len(visible) > remaining:
            path = store.existing_path(result)
            if path is None:
                # The result was individually valid but did not fit the shared
                # batch budget. Save the unmodified value before shrinking it.
                path = store.spill_result(
                    result,
                    reason="aggregate",
                    preview_chars=preview_chars,
                    text=visible,
                )
            if path is not None:
                pointer_only = store.pointer(path, reason="aggregate")
                if len(pointer_only) <= remaining:
                    available_preview = remaining - len(pointer_only)
                    pointer_with_preview = store.pointer(
                        path,
                        preview_text(visible, available_preview),
                        reason="aggregate",
                    )
                    replacement = (
                        pointer_with_preview
                        if len(pointer_with_preview) <= remaining
                        else pointer_only
                    )
                else:
                    replacement = _bounded_omission(remaining, "tool output")
                setattr(result, attribute, replacement)
                metadata[TOOL_OUTPUT_SPILL_REASON_KEY] = "aggregate"
                stats.spilled_count += 1
            else:
                # ``spill_result`` already installed a bounded fallback.
                setattr(
                    result,
                    attribute,
                    preview_text(
                        str(getattr(result, attribute, "")), max(0, remaining)
                    ),
                )

        visible = str(getattr(result, attribute, "") or "")
        stats.model_chars += len(visible)
        if remaining is not None:
            remaining = max(0, remaining - len(visible))

    stats.remaining_chars = remaining
    return stats


def _message_metadata(message: Any) -> dict[str, Any]:
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        return metadata
    if isinstance(message, dict):
        value = message.get("metadata")
        if isinstance(value, dict):
            return value
        value = {}
        message["metadata"] = value
        return value
    metadata = {}
    setattr(message, "metadata", metadata)
    return metadata


def _message_role(message: Any) -> str:
    return str(
        getattr(message, "role", None)
        if not isinstance(message, dict)
        else message.get("role", "")
    )


def _message_content(message: Any) -> str:
    value = (
        getattr(message, "content", "")
        if not isinstance(message, dict)
        else message.get("content", "")
    )
    return str(value or "")


def _set_message_content(message: Any, content: str) -> None:
    if isinstance(message, dict):
        message["content"] = content
    else:
        message.content = content


def _is_tool_history_message(message: Any) -> bool:
    metadata = _message_metadata(message)
    return bool(
        (_message_role(message) == "tool" and metadata.get("tool_call_id"))
        or metadata.get("tool_output_batch")
    )


def pack_tool_history_messages(
    messages: Iterable[Any],
    store: ToolOutputArtifactStore,
    *,
    max_chars: Optional[int],
    preview_chars: int,
) -> ToolOutputPackStats:
    """Pack older tool messages so consecutive turns share one budget.

    Oldest tool results are spilled first. Native tool messages remain in place,
    so their assistant call owners and ``tool_call_id`` metadata stay intact.
    XML batches are marked as one history item by QueueProcessor and are treated
    as one lossless artifact when they need to be reduced.
    """
    candidates = [message for message in messages if _is_tool_history_message(message)]
    visible_total = sum(len(_message_content(message)) for message in candidates)
    stats = ToolOutputPackStats(
        result_count=len(candidates),
        raw_chars=visible_total,
        model_chars=visible_total,
        batch_limit_chars=max_chars,
        remaining_chars=(
            max(0, max_chars - visible_total) if max_chars is not None else None
        ),
    )
    if max_chars is None:
        return stats

    for message in candidates:
        if visible_total <= max_chars:
            break
        original = _message_content(message)
        metadata = _message_metadata(message)
        allowed_for_replacement = max(0, max_chars - (visible_total - len(original)))
        path_value = metadata.get(TOOL_OUTPUT_PATH_KEY)
        path = Path(str(path_value)).expanduser() if path_value else None
        if path is None or not path.exists():
            try:
                path = store.write_output(
                    original,
                    tool_id=metadata.get("tool_call_id", "history"),
                    tool_type="tool-history",
                )
                metadata[TOOL_OUTPUT_PATH_KEY] = str(path)
            except Exception as exc:
                logger.warning("Could not save historical tool output: %s", exc)
                replacement = _bounded_omission(
                    allowed_for_replacement, "historical tool output"
                )
                _set_message_content(
                    message,
                    replacement,
                )
                visible_total -= len(original) - len(_message_content(message))
                continue

        pointer_only = store.pointer(path, reason="history")
        if allowed_for_replacement >= len(pointer_only):
            preview_budget = max(0, allowed_for_replacement - len(pointer_only))
            with_preview = store.pointer(
                path,
                preview_text(original, min(preview_chars, preview_budget)),
                reason="history",
            )
            replacement = (
                with_preview
                if len(with_preview) <= allowed_for_replacement
                else pointer_only
            )
        else:
            replacement = _bounded_omission(
                allowed_for_replacement, "historical tool output"
            )
        metadata[TOOL_OUTPUT_SPILLED_KEY] = True
        metadata[TOOL_OUTPUT_SPILL_REASON_KEY] = "history"
        metadata[TOOL_OUTPUT_RAW_CHARS_KEY] = len(original)
        metadata[TOOL_OUTPUT_RAW_BYTES_KEY] = len(
            original.encode("utf-8", errors="replace")
        )
        _set_message_content(message, replacement)
        visible_total -= len(original) - len(replacement)
        stats.spilled_count += 1

    stats.model_chars = visible_total
    stats.remaining_chars = max(0, max_chars - visible_total)
    return stats


def build_tool_output_store(
    config: Any, conversation_logger: Any
) -> ToolOutputArtifactStore:
    """Resolve a project/session-scoped artifact directory."""
    configured: Any = None
    if config is not None and hasattr(config, "get"):
        try:
            configured = config.get("kollabor.llm.tool_output_dir", None)
        except Exception:
            configured = None

    conversations_dir = getattr(conversation_logger, "conversations_dir", None)
    session_id = getattr(conversation_logger, "session_id", None)
    if isinstance(session_id, str) and session_id:
        session_name = _safe_name(session_id, "session")
    else:
        session_name = "session"

    if isinstance(configured, (str, Path)) and str(configured).strip():
        root = Path(str(configured)).expanduser()
    elif isinstance(conversations_dir, (str, Path)):
        root = Path(conversations_dir).expanduser() / "tool_outputs"
    else:
        root = Path(tempfile.gettempdir()) / "kollab-tool-output"

    return ToolOutputArtifactStore(root / session_name)
