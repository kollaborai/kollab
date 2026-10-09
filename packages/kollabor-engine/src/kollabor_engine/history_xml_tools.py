"""Shape XML-tool turns in the engine's history mirror for the web UI.

The daemon keeps each reply as the model wrote it, XML tool tags included, so
the model sees its own calls on later turns. Next to it, display-only metadata
that providers never receive: ``display_content`` (the reply without its tags)
and ``xml_tool_calls`` on the reply, ``xml_tool_results`` (one per call) on the
batched results message. This rewrites the mirror into the shape the web
history renders for native tools: the clean text, tool cards, and one
role="tool" row per result. The daemon's conversation is untouched.
"""

from typing import Any, Dict, List


def _entries(metadata: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    raw = metadata.get(key)
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _metadata(message: Dict[str, Any]) -> Dict[str, Any]:
    metadata = message.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _native_call(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    """One call in the wire shape the web history's restoredCalls reads."""
    input_value = entry.get("input")
    return {
        "id": str(entry.get("id") or f"xml_{index}"),
        "type": "function",
        "function": {
            "name": str(entry.get("name") or "tool"),
            "arguments": input_value if isinstance(input_value, dict) else {},
        },
    }


def _tool_row(entry: Dict[str, Any], index: int, timestamp: Any) -> Dict[str, Any]:
    """One role="tool" history row; the web UI puts it on its call's card."""
    metadata: Dict[str, Any] = {"tool_call_id": str(entry.get("id") or f"xml_{index}")}
    if entry.get("is_error"):
        metadata["is_error"] = True
    if entry.get("tool_execution_time") is not None:
        metadata["tool_execution_time"] = entry["tool_execution_time"]
    return {
        "role": "tool",
        "content": str(entry.get("content") or ""),
        "timestamp": timestamp,
        "metadata": metadata,
        "thinking": None,
    }


def web_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The history with XML tool turns in the native shape.

    A history with no XML tool turn comes back as the same list. A batched
    results message written before the daemon kept ``xml_tool_results`` stays
    as it was.
    """
    keys = ("xml_tool_calls", "xml_tool_results", "display_content")
    if not any(key in _metadata(message) for message in history for key in keys):
        return history

    shaped: List[Dict[str, Any]] = []
    for message in history:
        metadata = _metadata(message)
        calls = _entries(metadata, "xml_tool_calls")
        results = _entries(metadata, "xml_tool_results")
        if calls:
            metadata = dict(metadata)
            native = list(metadata.get("tool_calls") or [])
            native.extend(_native_call(entry, len(native) + i) for i, entry in enumerate(calls))
            metadata["tool_calls"] = native
            metadata.pop("xml_tool_calls", None)
            message = dict(message, metadata=metadata)
            if "display_content" in metadata:
                message["content"] = str(metadata.pop("display_content") or "")
            shaped.append(message)
        elif results and metadata.get("tool_output_batch"):
            timestamp = message.get("timestamp", "")
            shaped.extend(_tool_row(entry, i, timestamp) for i, entry in enumerate(results))
        elif "display_content" in metadata:
            # A reply whose only tags echoed its native calls: clean text only.
            metadata = dict(metadata)
            content = str(metadata.pop("display_content") or "")
            shaped.append(dict(message, metadata=metadata, content=content))
        else:
            shaped.append(message)
    return shaped
