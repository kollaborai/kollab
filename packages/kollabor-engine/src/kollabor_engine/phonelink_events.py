"""Redacted PhoneLink lifecycle events and phone diagnostics contracts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import re
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

_LOGGER = logging.getLogger(__name__)

LIFECYCLE_EVENTS = frozenset(
    {
        "invite_created",
        "invite_expired",
        "invite_opened",
        "pairing_code_shown",
        "approved",
        "denied",
        "connected",
        "disconnected",
        "last_seen_updated",
        "revoked",
        "invitation_cancelled",
        "restored_at_startup",
        "phone_diagnostic",
        "relay_socket_opened",
        "relay_socket_closed",
        "relay_reconnecting",
        "relay_error",
    }
)

PHONE_DIAGNOSTIC_EVENTS = frozenset(
    {
        "pagehide",
        "visibility_hidden",
        "visibility_visible",
        "online",
        "offline",
        "render_failure",
        "load_failure",
        "runtime_error",
        "unhandled_rejection",
        "relay_socket_opened",
        "relay_socket_closed",
        "relay_reconnecting",
        "relay_error",
    }
)

_CLIENT_KINDS = frozenset({"webui", "cli", "api"})
_METHODS = frozenset({"GET", "POST", "DELETE"})
_ROUTES = frozenset(
    {
        "/phonelink",
        "/phonelink/refresh",
        "/phonelink/phones",
        "/phonelink/phones/{identifier}/approve",
        "/phonelink/phones/{identifier}",
    }
)
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_TOKEN_LIKE_LABEL = re.compile(
    r"(?:(?<![0-9])[0-9]{6}(?![0-9])|"
    r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{24,}(?![A-Fa-f0-9])|"
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-]))"
)
_SENSITIVE_LABEL_MARKER = re.compile(
    r"\b(?:api[ _-]?key|(?:host|private|public)[ _-]?key|token|secret|password|"
    r"pairing[ _-]?code|invite|invitation|session|authorization|bearer)\b",
    re.IGNORECASE,
)
_USER_AGENTS = (
    ("Edge", re.compile(r"\bEdg(?:A|iOS)?/(\d+)", re.IGNORECASE)),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS)/(\d+)", re.IGNORECASE)),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/(\d+)", re.IGNORECASE)),
    ("Safari", re.compile(r"\bVersion/(\d+)(?:\.\d+)*.*\bSafari/", re.IGNORECASE)),
    ("Kollab", re.compile(r"\bKollab(?:-CLI)?/(\d+)", re.IGNORECASE)),
    ("curl", re.compile(r"\bcurl/(\d+)", re.IGNORECASE)),
    ("Python", re.compile(r"\bpython(?:-requests|-urllib3)?/(\d+)", re.IGNORECASE)),
)

_EVENT_FIELD_NAMES = frozenset(
    {
        "actor",
        "cause",
        "device_name",
        "network",
        "error_kind",
        "record_state",
        "phone_event",
        "close_code",
        "close_reason",
        "retry_count",
        "retry_seconds",
        "idle_seconds",
        "last_request_at",
        "last_seen_at",
    }
)
_ERROR_KINDS = frozenset(
    {"relay", "timeout", "dns", "tls", "socket", "http", "configuration", "unexpected"}
)
_CLOSE_REASONS = frozenset(
    {
        "normal",
        "going_away",
        "protocol_error",
        "unsupported_data",
        "invalid_payload",
        "policy_violation",
        "message_too_big",
        "mandatory_extension",
        "internal_error",
        "service_restart",
        "try_again_later",
        "bad_gateway",
        "tls_handshake",
        "application",
        "unknown",
    }
)
_CLOSE_REASON_BY_CODE = {
    1000: "normal",
    1001: "going_away",
    1002: "protocol_error",
    1003: "unsupported_data",
    1007: "invalid_payload",
    1008: "policy_violation",
    1009: "message_too_big",
    1010: "mandatory_extension",
    1011: "internal_error",
    1012: "service_restart",
    1013: "try_again_later",
    1014: "bad_gateway",
    1015: "tls_handshake",
}
_RECORD_STATES = frozenset({"linked", "pending", "expired"})


def opaque_ref(value: str | None) -> str | None:
    """Return a stable correlation ref without exposing the source identifier/key."""
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _user_agent_family(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    for family, pattern in _USER_AGENTS:
        match = pattern.search(value[:512])
        if match:
            return f"{family}/{match.group(1)}"
    return "other"


def _remote_addr(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).compressed
    except ValueError:
        return None


def safe_event_label(value: Any, *, max_length: int) -> str:
    """Keep useful labels while rejecting URL, credential, and code-shaped text."""
    if not isinstance(value, str):
        return "unknown"
    label = value.strip()
    if not label:
        return "unknown"
    if (
        len(label) > max_length
        or _TOKEN_LIKE_LABEL.search(label)
        or _SENSITIVE_LABEL_MARKER.search(label)
    ):
        return "redacted"
    if any(not (character.isalnum() or character in " ._-'()") for character in label):
        return "redacted"
    return label


def actor_from_request(request: Any, route: str) -> dict[str, Any]:
    """Extract useful request metadata; all raw headers are normalized at log time."""
    headers = request.headers
    raw_kind = str(headers.get("x-kollab-client", "")).lower()
    user_agent = str(headers.get("user-agent", ""))
    if raw_kind not in _CLIENT_KINDS:
        if headers.get("origin") or headers.get("sec-fetch-site"):
            raw_kind = "webui"
        elif re.search(r"\bkollab(?:-cli)?/", user_agent, re.IGNORECASE):
            raw_kind = "cli"
        else:
            raw_kind = "api"
    client = getattr(request, "client", None)
    return {
        "route": route,
        "method": str(request.method).upper(),
        "client_kind": raw_kind,
        "remote_addr": getattr(client, "host", None),
        "user_agent": user_agent,
        "web_session": headers.get("x-kollab-web-session"),
        "confirmation_shown": headers.get("x-kollab-confirmation-shown", "").lower()
        == "true",
    }


def _close_reason_for_code(code: int) -> str:
    return _CLOSE_REASON_BY_CODE.get(
        code, "application" if 3000 <= code <= 4999 else "unknown"
    )


def normalize_phone_diagnostic(payload: Any) -> dict[str, int | str] | None:
    """Validate a tiny, allowlisted phone-to-engine diagnostic message."""
    if not isinstance(payload, dict) or set(payload) - {
        "event",
        "close_code",
        "close_reason",
        "retry_count",
    }:
        return None
    event = payload.get("event")
    if not isinstance(event, str) or event not in PHONE_DIAGNOSTIC_EVENTS:
        return None
    result: dict[str, int | str] = {"event": event}
    if "close_code" in payload:
        code = payload["close_code"]
        if (
            event != "relay_socket_closed"
            or type(code) is not int
            or not 1000 <= code <= 4999
        ):
            return None
        result["close_code"] = code
    if event == "relay_socket_closed" and "close_code" in result:
        expected_reason = _close_reason_for_code(result["close_code"])
        if "close_reason" in payload and payload["close_reason"] != expected_reason:
            return None
        result["close_reason"] = expected_reason
    elif "close_reason" in payload:
        return None
    if "retry_count" in payload:
        count = payload["retry_count"]
        if (
            event != "relay_reconnecting"
            or type(count) is not int
            or not 0 <= count <= 10000
        ):
            return None
        result["retry_count"] = count
    return result


def _actor_fields(actor: Any) -> dict[str, Any] | None:
    if not isinstance(actor, Mapping):
        return None
    route = actor.get("route")
    method = actor.get("method")
    kind = actor.get("client_kind")
    session = actor.get("web_session")
    return {
        "route": route if isinstance(route, str) and route in _ROUTES else "unknown",
        "method": (
            method if isinstance(method, str) and method in _METHODS else "unknown"
        ),
        "client_kind": (
            kind if isinstance(kind, str) and kind in _CLIENT_KINDS else "api"
        ),
        "remote_addr": _remote_addr(actor.get("remote_addr")),
        "user_agent": _user_agent_family(actor.get("user_agent")),
        "web_session_ref": (
            opaque_ref(session)
            if isinstance(session, str) and len(session) <= 512
            else None
        ),
        "confirmation_shown": actor.get("confirmation_shown") is True,
    }


def _event_value(name: str, value: Any) -> Any:
    if name in {"device_name", "network"}:
        return safe_event_label(value, max_length=48 if name == "device_name" else 128)
    if name == "cause" or name == "phone_event":
        return (
            value
            if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value)
            else "unknown"
        )
    if name == "error_kind":
        return (
            value if isinstance(value, str) and value in _ERROR_KINDS else "unexpected"
        )
    if name == "record_state":
        return (
            value if isinstance(value, str) and value in _RECORD_STATES else "unknown"
        )
    if name == "close_code":
        return value if type(value) is int and 1000 <= value <= 4999 else None
    if name == "close_reason":
        return value if isinstance(value, str) and value in _CLOSE_REASONS else None
    if name == "retry_count":
        return value if type(value) is int and 0 <= value <= 10000 else None
    if name == "retry_seconds":
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return round(min(max(float(value), 0.0), 300.0), 2)
        return None
    if name == "idle_seconds":
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return round(min(max(float(value), 0.0), 86400.0), 2)
        return None
    if name in {"last_request_at", "last_seen_at"}:
        if type(value) is int and 0 <= value <= int(time.time()) + 60:
            return value
        return None
    return None


class PhoneLinkEventLog:
    """One bounded event source for INFO logs and the loopback event endpoint."""

    def __init__(self, max_events: int = 100) -> None:
        if type(max_events) is not int or not 1 <= max_events <= 1000:
            raise ValueError("max_events must be between 1 and 1000")
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._next_id = 1

    def emit(
        self,
        event: str,
        *,
        identifier: str | None = None,
        peer: str | None = None,
        actor: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(f"unsupported PhoneLink lifecycle event: {event!r}")
        phone_ref = opaque_ref(identifier)
        device_ref = opaque_ref(peer)
        record: dict[str, Any] = {
            "id": self._next_id,
            "at": int(time.time()),
            "event": event,
            "phone_ref": phone_ref or "unknown",
            "device_name": _event_value("device_name", fields.pop("device_name", None)),
            "network": _event_value("network", fields.pop("network", None)),
        }
        self._next_id += 1
        if device_ref:
            record["device_ref"] = device_ref
        for name, value in fields.items():
            if name in _EVENT_FIELD_NAMES - {"actor"}:
                normalized = _event_value(name, value)
                if normalized is not None:
                    record[name] = normalized
        normalized_actor = _actor_fields(actor)
        if normalized_actor is not None:
            record["actor"] = normalized_actor
        self._events.append(record)
        _LOGGER.info(
            "PhoneLink lifecycle %s",
            json.dumps(record, sort_keys=True, separators=(",", ":")),
        )
        return dict(record)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            dict(record, actor=dict(record["actor"]) if "actor" in record else None)
            for record in self._events
        ]
