"""Network panels: ``connect``, ``connect-join`` and ``connect-knocks``.

They call the same daemon ``state.*`` methods an attached terminal calls
(``hub_connect_snapshot``, ``hub_connect_decide``, ``hub_enrollment_offer``,
``hub_enroll``, ``hub_enroll_status``, ``hub_knocks``, ``hub_connect``), so
there is no new network logic.

Join codes: an issued code leaves only in the ``new_code`` action's ``reveal``;
``describe()`` never mints one. An entered code arrives only in the
``connect-join`` action body and never reaches a message, log line or error.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from plugins.hub.connect_guide import (
    DEFAULT_DOMAIN,
    JOIN_FAILURE_REASONS,
    NO_NETWORK_LINE,
)
from plugins.hub.device_names import (
    TRUST_LEVELS,
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
            panel["read_only"] = True  # the engine asks the folder's network owner instead
            return panel
        panel["summary"].append(
            {"label": "Connection", "value": "Connected" if snap["relay_online"] else "Offline"}
        )
        if snap["config_from"]:
            panel["summary"].append({"label": "Config From", "value": snap["config_from"]})
        panel["summary"] += [
            {"label": "Agents On This Device", "value": names(snap["local_agents"])},
            {"label": "Agents On Other Devices", "value": names(snap["remote_agents"])},
            {"label": "Offline Devices", "value": names(snap["offline_devices"])},
        ]
        if snap["knocks"]:
            panel["summary"].append({"label": "Knocks", "value": f"{snap['knocks']} waiting"})
        panel["rows"] = [
            {
                "id": row["enrollment_id"],
                "label": row["device"] or "Unknown Device",
                "detail": f"device ID {row['fingerprint']}",
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
            {"id": "knocks", "label": "Knocks"},
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
        # a wizard action sends {"values": {path: value}, "step": id}
        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        domain = str(values.get("domain") or DEFAULT_DOMAIN).strip()
        code = str(values.get("code") or "").strip()
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


def _quote(text: str) -> str:
    text = " ".join(str(text).split())
    text = "".join(c for c in text if c.isprintable())
    return text[:197] + "…" if len(text) > 200 else text


def _clock(seconds: int) -> str:
    return f"{max(0, seconds) // 60}:{max(0, seconds) % 60:02d}"


_KNOCK_ROW_ACTIONS = {
    "ringing": ["accept", "reject", "block"],
    "call": ["stop"],
    "missed": ["block", "delete"],
    "blocked": ["unblock"],
    "contact": ["unexpect"],
}


class ConnectKnocksPanel:
    """Knocks: ringing, the calls this device placed, missed, blocked, contacts.

    The same snapshot and actions as the terminal's knock screen
    (plugins/hub/knocks.py). Row ids are `<kind>:<knock id or route>`.
    """

    name = "connect-knocks"
    kind = "picker"

    async def _snapshot(self, ctx: Any) -> dict:
        result = await ctx.hub_knocks("list", {})
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        if not isinstance(snapshot, dict):
            raise ValueError((result or {}).get("text") or "knocks unavailable")
        return snapshot

    async def describe(self, ctx: Any, params: dict) -> dict:
        panel = _picker(self.name, "Knocks")
        try:
            snap = await self._snapshot(ctx)
        except Exception as exc:  # noqa: BLE001
            logger.debug("connect: knocks unavailable: %s", type(exc).__name__)
            panel["notice"] = "Knocks are unavailable right now."
            return panel
        who = snap["mode"].title()
        if snap["mode_until"]:
            who += " (for now)"
        panel["summary"] = [
            {"label": "Missed", "value": f"{len(snap['missed'])} of {snap['missed_limit']}"},
            {"label": "Who May Knock", "value": who},
        ]
        if not snap["online"]:
            panel["notice"] = "This device is offline: knocks cannot reach it."
        rows = []
        for row in snap["ringing"]:
            rows.append(("ringing", row["id"], row["device"], f"device ID {row['fingerprint']}"
                         f" · {_clock(row['left'])} left · \"{_quote(row['text'])}\"", "Ringing"))
        for row in snap["calls"]:
            state = (f"Ringing {_clock(row['left'])}" if row["state"] == "ringing"
                     else f"Redialing, next in {_clock(row['left'])}")
            rows.append(("call", row["route"], row["target"], state, "Knocking"))
        for row in snap["missed"]:
            when = time.strftime("%b %d %H:%M", time.localtime(row["at"]))
            rows.append(("missed", row["id"], row["device"], f"device ID {row['fingerprint']}"
                         f" · {when} · \"{_quote(row['text'])}\"", "Missed"))
        for row in snap["blocked"]:
            rows.append(("blocked", row["route"], row["route"], row["device"], "Blocked"))
        for row in snap["contacts"]:
            rows.append(("contact", row["route"], row["route"],
                         "First knock accepted" if row["expected"] else "", "Contacts"))
        panel["rows"] = [
            {
                "id": f"{kind}:{key}",
                "label": label or "Unknown Device",
                "detail": detail,
                "group": group,
                "current": False,
                "badges": [],
                "actions": _KNOCK_ROW_ACTIONS[kind],
            }
            for kind, key, label, detail, group in rows
        ]
        panel["row_actions"] = [
            {"id": "accept", "label": "Accept"},
            {"id": "reject", "label": "Reject", "confirm": True},
            {"id": "block", "label": "Block", "confirm": True},
            {"id": "stop", "label": "Stop"},
            {"id": "delete", "label": "Delete"},
            {"id": "unblock", "label": "Unblock"},
            {"id": "unexpect", "label": "Remove"},
        ]
        if snap["missed"]:
            panel["toolbar_actions"] = [{"id": "clear", "label": "Clear Missed"}]
        panel["controls"] = [
            make_field(
                "mode", "dropdown", "Who May Knock", action="mode",
                value=snap["mode"], options=["everyone", "contacts", "nobody"],
            ),
            make_field(
                "expect_route", "text_input", "Expect A Knock From", action="expect", value="",
                help="A contact route (domain/c/16 hex): its first knock is accepted.",
            ),
            make_field(
                "block_route", "text_input", "Block A Route", action="block_route", value="",
                help="A contact route (domain/c/16 hex): its knocks are refused.",
            ),
        ]
        if not rows:
            panel["notice"] = panel.get("notice") or "No knocks."
        return panel

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action in ("mode", "clear", "expect", "block_route"):
            args: dict = {}
            if action == "mode":
                args = {"mode": str(payload.get("mode") or ""), "minutes": 0}
            elif action in ("expect", "block_route"):
                field = "expect_route" if action == "expect" else "block_route"
                route = str(payload.get(field) or "").strip()
                match = re.fullmatch(r"(?:[^\s/]+/c/)?([0-9a-fA-F]{16})", route)
                if match is None:
                    return error_result("That is not a contact route.", {field: "domain/c/16 hex"})
                args = {"route": match.group(1).lower()}
        elif action in ("accept", "reject", "block", "stop", "delete", "unblock", "unexpect"):
            kind, _, key = str(payload.get("id") or "").partition(":")
            if action not in _KNOCK_ROW_ACTIONS.get(kind, ()):
                raise PanelError("that knock is gone", status=404)
            args = {"id": key} if kind in ("ringing", "missed") else {"route": key}
        else:
            raise unknown_action(self.name, action)
        result = await ctx.hub_knocks(action, args)
        text = result.get("text") if isinstance(result, dict) else None
        fresh = await self.describe(ctx, {})
        if not isinstance(text, str) or text.startswith("connect:"):
            reason = (text or "").removeprefix("connect:").strip() or "Try again."
            return error_result(reason[:1].upper() + reason[1:], panel=fresh)
        return ok_result(text[:1].upper() + text[1:] + ".", panel=fresh)


PANELS = {
    panel.name: panel
    for panel in (ConnectPanel(), ConnectJoinPanel(), ConnectKnocksPanel())
}
