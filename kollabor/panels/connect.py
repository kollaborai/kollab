"""Network panels: ``connect``, ``connect-join`` and ``connect-knocks``.

They call the same daemon ``state.*`` methods an attached terminal calls
(``hub_connect_snapshot``, ``hub_connect_decide``, ``hub_enrollment_offer``,
``hub_enroll``, ``hub_enroll_status``, ``hub_contact_pending``,
``hub_contact_decide``, ``hub_connect``), so there is no new network logic.

Join codes: an issued code leaves only in the ``new_code`` action's ``reveal``;
``describe()`` never mints one. An entered code arrives only in the
``connect-join`` action body and never reaches a message, log line or error.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from plugins.hub.connect_guide import (
    DEFAULT_DOMAIN,
    JOIN_FAILURE_REASONS,
    NO_NETWORK_LINE,
)
from plugins.hub.device_names import (
    TRUST_LEVELS,
    device_key_fingerprint,
    short_fingerprint,
    validate_device_name,
    validate_trust,
)
from plugins.hub.relay_commands import NO_NETWORK, network_label

from .base import (
    PanelError,
    error_result,
    make_field,
    make_poll,
    make_reveal,
    ok_result,
    unknown_action,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0  # same cadence as the terminal form (connect_altview._POLL_SECONDS)
_RECEIPT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_READ_ONLY_NOTE = (
    "Another window on this computer runs the network. "
    "This view shows what is known and offers no actions."
)
_UNAVAILABLE = "The agent network is unavailable in this daemon."
_SCOPE_NOTE = "This device's agent network."


def _picker(name: str, title: str, **extra: Any) -> dict:
    return {
        "panel": name,
        "kind": "picker",
        "title": title,
        "scope_note": _SCOPE_NOTE,
        "summary": [],
        "rows": [],
        "row_actions": [],
        "toolbar_actions": [],
        "controls": [],
        "empty_groups": [],
        **extra,
    }


async def _snapshot(ctx: Any) -> Optional[dict]:
    """The Connect screen's data, or None when the hub cannot supply it."""
    try:
        return await ctx.hub_connect_snapshot()
    except Exception as exc:  # noqa: BLE001 - no hub, or the relay is not ready
        logger.debug("connect: snapshot unavailable: %s", type(exc).__name__)
        return None


def _has_network(snap: dict) -> bool:
    """A device alone on a network counts as not set up (as the first-launch guide)."""
    return bool(snap["domain"]) and bool(
        snap["remote_agents"] or snap["offline_devices"] or snap["config_from"]
    )


def _failed(text: str) -> bool:
    return text.startswith(("connect:", "beacon:"))


async def _writable_snapshot(ctx: Any) -> dict:
    snap = await _snapshot(ctx)
    if snap is None:
        raise PanelError(_UNAVAILABLE, status=503)
    if snap.get("read_only"):
        raise PanelError(_READ_ONLY_NOTE, status=403)
    return snap


async def start_new_network(ctx: Any) -> tuple[bool, str]:
    """Start a network on the default directory; (ok, one line).

    The terminal's guided flow, composed from daemon methods: a device alone on
    a network keeps it, a half-set-up one is left and started fresh, and a
    network with other devices on it is never touched.
    """

    async def start(domain: str) -> bool:
        try:
            first = str(await ctx.hub_connect(domain)).splitlines()[:1]
        except Exception:  # noqa: BLE001
            return False
        return bool(first) and first[0].startswith("network ") and first[0] != NO_NETWORK

    snap = await _snapshot(ctx)
    domain = (snap or {}).get("domain") or DEFAULT_DOMAIN
    if not await start(domain):
        if snap is not None and _has_network(snap):
            return False, f"Could not start a network on {domain}."
        try:
            await ctx.hub_connect("leave")
        except Exception:  # noqa: BLE001
            logger.debug("connect: leaving a lone network failed")
        domain = DEFAULT_DOMAIN
        if not await start(domain):
            return False, f"Could not start a network on {domain}."
    return True, f"Network started on {domain}."


class ConnectPanel:
    """``/connect``: this device's network, trust, agents and join requests."""

    name = "connect"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict) -> dict:
        snap = await _snapshot(ctx)
        panel = _picker(self.name, "Network")
        if snap is None:
            panel["notice"] = _UNAVAILABLE
            return panel
        if not snap["domain"]:
            panel["notice"] = NO_NETWORK_LINE
            panel["toolbar_actions"] = [
                {"id": "new_network", "label": "Start a New Network"},
                {"id": "join", "label": "Join With a Code"},
            ]
            return panel
        names = lambda items: ", ".join(items) or "None"  # noqa: E731
        panel["summary"] = [
            {"label": "Network", "value": network_label(snap["network"], snap["domain"])},
            {"label": "Trust", "value": snap["trust"]},
            {"label": "This Device", "value": snap["device"] or "Unnamed"},
        ]
        if snap.get("read_only"):
            panel["notice"] = _READ_ONLY_NOTE
            return panel
        panel["summary"].append(
            {"label": "Relay", "value": "Online" if snap["relay_online"] else "Offline"}
        )
        if snap["config_from"]:
            panel["summary"].append({"label": "Config From", "value": snap["config_from"]})
        panel["summary"] += [
            {"label": "Agents On This Device", "value": names(snap["local_agents"])},
            {"label": "Agents On Other Devices", "value": names(snap["remote_agents"])},
            {"label": "Offline Devices", "value": names(snap["offline_devices"])},
        ]
        if snap["knocks"]:
            panel["summary"].append(
                {"label": "Introductions", "value": f"{snap['knocks']} waiting"}
            )
        panel["rows"] = [
            {
                "id": row["enrollment_id"],
                "label": row["device"] or "Unknown Device",
                "detail": f"fingerprint {row['fingerprint']}",
                "group": "Join Requests",
                "current": False,
                "badges": [],
            }
            for row in snap["requests"]
        ]
        panel["row_actions"] = [
            {"id": "accept", "label": "Accept", "payload_key": "enrollment_id"},
            {"id": "reject", "label": "Reject", "confirm": True, "payload_key": "enrollment_id"},
        ]
        panel["toolbar_actions"] = [
            {"id": "new_code", "label": "New Join Code"},
            {"id": "knocks", "label": "Review Introductions"},
        ]
        panel["controls"] = [
            make_field(
                "level", "dropdown", "Trust", action="trust",
                value=snap["trust"], options=list(TRUST_LEVELS),
            ),
            make_field(
                "name", "text_input", "This Device's Name", action="rename", value=snap["device"],
                help="1-63 characters: a-z, 0-9 and dashes.",
            ),
        ]
        return panel

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action in ("accept", "reject"):
            return await self._decide(ctx, action, payload)
        if action == "new_code":
            snap = await _writable_snapshot(ctx)
            offer = await ctx.hub_enrollment_offer(snap["domain"] or DEFAULT_DOMAIN)
            if offer.get("status") != "offered":
                return error_result("Could not create a join code.")
            expires = float(offer["expires_at"])
            return ok_result(
                "One-device join code created.",
                reveal=make_reveal("Join Code", offer["code"], expires, "active"),
                panel=await self.describe(ctx, {}),
            )
        if action == "new_network":
            ok, message = await start_new_network(ctx)
            fresh = await self.describe(ctx, {})
            return ok_result(message, panel=fresh) if ok else error_result(message, panel=fresh)
        if action in ("rename", "trust"):
            return await self._set(ctx, action, payload)
        if action == "join":
            return ok_result(open=await PANELS["connect-join"].describe(ctx, {}))
        if action == "knocks":
            return ok_result(open=await PANELS["connect-knocks"].describe(ctx, {}))
        raise unknown_action(self.name, action)

    async def _decide(self, ctx: Any, decision: str, payload: dict) -> dict:
        request_id = str(payload.get("enrollment_id") or payload.get("id") or "")
        snap = await _writable_snapshot(ctx)
        row = next((r for r in snap["requests"] if r["enrollment_id"] == request_id), None)
        if row is None:
            raise PanelError("that join request is no longer pending", status=404)
        reason = await ctx.hub_connect_decide(request_id, decision)
        who = row["device"] or "that device"
        fresh = await self.describe(ctx, {})
        if reason:
            return error_result(f"Could not {decision} {who}: {reason}", panel=fresh)
        verb = "Accepted" if decision == "accept" else "Rejected"
        return ok_result(f"{verb} {who}.", panel=fresh)

    async def _set(self, ctx: Any, action: str, payload: dict) -> dict:
        field = "name" if action == "rename" else "level"  # the control's path
        value = str(payload.get(field, payload.get("value", payload.get(action, "")))).strip()
        try:
            checked = validate_device_name(value) if action == "rename" else validate_trust(value)
        except ValueError as exc:
            raise PanelError(str(exc), errors={field: str(exc)}) from exc
        text = str(await ctx.hub_connect(f"{'name' if action == 'rename' else 'trust'} {checked}"))
        fresh = await self.describe(ctx, {})
        message = text.splitlines()[0] if text else ""
        return error_result(message, panel=fresh) if _failed(text) else ok_result(message, panel=fresh)


class ConnectJoinPanel:
    """Join a network with a code issued on another device."""

    name = "connect-join"
    kind = "wizard"

    async def describe(self, ctx: Any, params: dict) -> dict:
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": "Join a Network",
            "steps": [
                {
                    "id": "domain",
                    "title": "Network",
                    "fields": [
                        make_field(
                            "domain", "text_input", "Network Domain",
                            value=DEFAULT_DOMAIN, placeholder=DEFAULT_DOMAIN,
                        )
                    ],
                },
                {
                    "id": "code",
                    "title": "Join Code",
                    "fields": [
                        make_field(
                            "code", "text_input", "Join Code", secret=True,
                            help="Type the code shown on the other computer.",
                        )
                    ],
                },
            ],
            "actions": [{"id": "finish", "label": "Join"}],
        }

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action == "finish":
            return await self._finish(ctx, payload)
        if action == "join_status":
            return await self._status(ctx, payload)
        raise unknown_action(self.name, action)

    async def _finish(self, ctx: Any, payload: dict) -> dict:
        domain = str(payload.get("domain") or DEFAULT_DOMAIN).strip()
        code = str(payload.get("code") or "").strip()
        errors = {}
        if not domain or len(domain) > 253 or not domain.isprintable():
            errors["domain"] = "Enter a valid network domain."
        if not code or len(code) > 4096 or not code.isprintable():
            errors["code"] = "Enter the join code."
        if errors:
            raise PanelError("Check the highlighted fields.", errors=errors)
        try:
            outcome = await ctx.hub_enroll(domain, code)
        except Exception:  # noqa: BLE001 - never let the code reach a message or log
            outcome = {"error": "connect request could not be submitted"}
        if outcome.get("error"):
            return error_result("The join request could not be submitted.")
        return self._outcome(outcome, domain)

    async def _status(self, ctx: Any, payload: dict) -> dict:
        receipt = str(payload.get("receipt_id") or "")
        domain = str(payload.get("domain") or DEFAULT_DOMAIN)
        if not _RECEIPT_ID_RE.match(receipt):
            raise PanelError("invalid receipt", errors={"receipt_id": "Unknown request."})
        try:
            outcome = await ctx.hub_enroll_status(receipt)
        except Exception:  # noqa: BLE001 - a failed poll says nothing about the request
            return ok_result(
                "Still waiting for approval on another device; check /connect status.",
                poll=make_poll("join_status", {"receipt_id": receipt, "domain": domain}, POLL_SECONDS),
            )
        return self._outcome(outcome, domain)

    @staticmethod
    def _outcome(outcome: dict, domain: str) -> dict:
        status = outcome.get("status")
        if status == "pending":
            receipt = outcome.get("receipt_id", "")
            return ok_result(
                "Waiting for approval on the other device.",
                poll=make_poll("join_status", {"receipt_id": receipt, "domain": domain}, POLL_SECONDS),
            )
        if status == "approved":
            text = " ".join(t for t in (outcome.get("detail"), outcome.get("note")) if t)
            return ok_result(text or f"Joined {domain}.")
        if status == "rejected":
            return error_result("The other device rejected this join request.")
        reason = outcome.get("reason") or ""
        return error_result(JOIN_FAILURE_REASONS.get(reason, reason) or "The join request failed.")


def _intro(text: str) -> str:
    text = " ".join(str(text).split())
    text = "".join(c for c in text if c.isprintable())
    return text[:197] + "…" if len(text) > 200 else text


class ConnectKnocksPanel:
    """Introductions other devices sent to this one; allow or deny each."""

    name = "connect-knocks"
    kind = "picker"

    async def _pending(self, ctx: Any) -> list[dict]:
        rows = await ctx.hub_contact_pending("")
        if not isinstance(rows, list):
            raise ValueError("invalid pending list")
        return [
            r for r in rows
            if isinstance(r, dict)
            and re.fullmatch(r"[0-9a-f]{32}", str(r.get("receipt_id", "")))
            and re.fullmatch(r"[0-9a-f]{64}", str(r.get("sender_key", "")))
        ]

    async def describe(self, ctx: Any, params: dict) -> dict:
        panel = _picker(self.name, "Introductions")
        try:
            rows = await self._pending(ctx)
        except Exception as exc:  # noqa: BLE001
            logger.debug("connect: knocks unavailable: %s", type(exc).__name__)
            panel["notice"] = "Introductions are unavailable right now."
            return panel
        panel["rows"] = [
            {
                "id": r["receipt_id"],
                "label": r.get("device_name") or "Unknown Device",
                "detail": f"fingerprint {short_fingerprint(device_key_fingerprint(r['sender_key']))}"
                f" · \"{_intro(r.get('introduction', ''))}\"",
                "group": "Introductions",
                "current": False,
                "badges": [],
            }
            for r in rows
        ]
        panel["row_actions"] = [
            {"id": "allow", "label": "Allow", "payload_key": "receipt_id"},
            {"id": "deny", "label": "Deny", "confirm": True, "payload_key": "receipt_id"},
        ]
        if not rows:
            panel["notice"] = "No introductions waiting."
        return panel

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action not in ("allow", "deny"):
            raise unknown_action(self.name, action)
        receipt = str(payload.get("receipt_id") or payload.get("id") or "")
        try:
            rows = await self._pending(ctx)
        except Exception as exc:  # noqa: BLE001
            raise PanelError("Introductions are unavailable right now.", status=503) from exc
        row = next((r for r in rows if r["receipt_id"] == receipt), None)
        if row is None:
            raise PanelError("that introduction is no longer pending", status=404)
        decision = "accept" if action == "allow" else "reject"
        who = row.get("device_name") or "that device"
        reason = await ctx.hub_contact_decide(
            "", receipt, decision, row["sender_key"], row.get("device_name") or ""
        )
        fresh = await self.describe(ctx, {})
        if reason:
            return error_result(f"Could not {action} {who}: {reason}", panel=fresh)
        return ok_result(
            f"Allowed {who}." if action == "allow" else f"Denied {who}.", panel=fresh
        )


PANELS = {
    panel.name: panel
    for panel in (ConnectPanel(), ConnectJoinPanel(), ConnectKnocksPanel())
}
