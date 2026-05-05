"""SSE event types for the Kollab Engine API."""

import json
import time
from typing import Any, Dict, Optional


def _ts() -> int:
    return int(time.time() * 1000)


def sse_event(event_type: str, session_id: str, **kwargs) -> Dict[str, Any]:
    """Build a base SSE event dict."""
    return {"type": event_type, "session_id": session_id, "ts": _ts(), **kwargs}


def token(session_id: str, text: str) -> Dict:
    return sse_event("token", session_id, text=text)


def thinking(session_id: str, text: str) -> Dict:
    return sse_event("thinking", session_id, text=text)


def tool_start(
    session_id: str,
    tool_id: str,
    tool_name: str,
    tool_type: str,
    input: Dict,
    risk_level: str = "low",
) -> Dict:
    return sse_event(
        "tool_start",
        session_id,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=tool_type,
        input=input,
        risk_level=risk_level,
    )


def permission_request(
    session_id: str,
    tool_id: str,
    tool_name: str,
    tool_type: str,
    input: Dict,
    risk_level: str,
    risk_reason: str,
) -> Dict:
    return sse_event(
        "permission_request",
        session_id,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=tool_type,
        input=input,
        risk_level=risk_level,
        risk_reason=risk_reason,
    )


def permission_granted(session_id: str, tool_id: str, scope: str) -> Dict:
    return sse_event("permission_granted", session_id, tool_id=tool_id, scope=scope)


def permission_denied(session_id: str, tool_id: str) -> Dict:
    return sse_event("permission_denied", session_id, tool_id=tool_id)


def tool_result(
    session_id: str,
    tool_id: str,
    tool_name: str,
    success: bool,
    output: str,
    error: str = "",
    execution_time: float = 0.0,
    metadata: Optional[Dict] = None,
) -> Dict:
    return sse_event(
        "tool_result",
        session_id,
        tool_id=tool_id,
        tool_name=tool_name,
        success=success,
        output=output,
        error=error,
        execution_time=execution_time,
        metadata=metadata or {},
    )


def question_gate(session_id: str, question: str, pending_tools: int = 0) -> Dict:
    return sse_event(
        "question_gate",
        session_id,
        question=question,
        pending_tools=pending_tools,
    )


def turn_complete(
    session_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    thinking_tokens: int = 0,
    tool_calls: int = 0,
    stop_reason: str = "end_turn",
) -> Dict:
    return sse_event(
        "turn_complete",
        session_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
    )


def error(session_id: str, code: str, message: str, retryable: bool = False) -> Dict:
    return sse_event(
        "error",
        session_id,
        code=code,
        message=message,
        retryable=retryable,
    )


def serialize(event: Dict) -> str:
    """Serialize event to SSE data string."""
    return json.dumps(event, ensure_ascii=False)
