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
    join_check_text,
)
from plugins.hub.device_names import (
    TRUST_LEVELS,
    validate_device_name,
    validate_trust,
)
from plugins.hub.network_control import ONE_COUNTING_LINE
from plugins.hub.network_halt import CONFIRM_REFUSAL, CONFIRMED_ACTIONS
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
    "This view shows what is known; only Halt and Resume works here."
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
        contact_route = snap.get("contact_route", "")
        panel["summary"] = [
            {"label": "Network", "value": network_label(snap["network"], snap["domain"])},
            {"label": "Trust", "value": snap["trust"]},
            {"label": "This Device", "value": snap["device"] or "Unnamed"},
            {
                "label": "Knocks Address",
                "value": contact_route or "Unavailable",
                **({"copyable": True} if contact_route else {}),
            },
        ]
        try:
            halt_snap, halt_names = await _halt_data(ctx)
            panel["summary"].append({"label": "Network State", "value": halt_state_line(halt_snap, halt_names)})
        except Exception as exc:  # noqa: BLE001 - an older daemon or no halt control yet
            logger.debug("connect: halt state unavailable: %s", type(exc).__name__)
        # before the read-only return: read-only chats reach Halt and Resume too (R3-F8)
        halt_action = {"id": "halt", "label": "Halt and Resume"}
        panel["toolbar_actions"] = [halt_action]
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
            halt_action,
        ]
        panel["controls"] = [
            make_field(
                "level", "dropdown", "Trust", action="trust",
                value=snap["trust"], options=list(TRUST_LEVELS),
                help="open: every device on this network can message and open your agents, and approve "
                "their tools. agents: only the agents you allow. manual: nothing without /connect authorize.",
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
                return error_result(offer.get("reason") or "Could not create a join code.")
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
        if action == "halt":
            return ok_result(open=await PANELS["connect-halt"].describe(ctx, {}))
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
            text = " ".join(
                t for t in (outcome.get("detail"), outcome.get("note"), join_check_text(outcome)) if t
            )
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


# ----------------------------------------------------------------- halt
# Spec: docs/specs/network-sync-and-control.md "TUI and Web UI". Both panels
# use only ctx.hub_halt_snapshot(), ctx.hub_halt_act() and ctx.hub_halt_members(),
# so the network owner, the halt listener (halt.sock) and the engine's
# session-less route can all serve them.

HALT_UNAVAILABLE = "The halt screen is unavailable right now."
ONE_MACHINE_NOTE = "Halt needs a second machine that can confirm a resume."
NOT_COUNTING_NOTE = "This machine does not count yet, so it cannot confirm a resume or a revocation."
PROPOSED_LINE = (
    "Resume proposed. A person on another machine confirms it: "
    "/connect resume there, or Settings → Network → Confirm Resume."
)


def _when(seconds: Any) -> str:
    try:
        return time.strftime("%b %d %H:%M", time.localtime(int(seconds)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "an unknown time"


class _Names:
    """Device names for machine and folder keys, from ``hub_halt_members()``."""

    def __init__(self, members: list[dict]):
        self.by_machine: dict[str, str] = {}
        self.by_folder: dict[str, str] = {}
        for row in members:
            name = str(row.get("name") or "")
            if row.get("machine"):
                self.by_machine.setdefault(row["machine"], name)
            if row.get("folder"):
                self.by_folder[row["folder"]] = name
        self.members = members

    def machine(self, key: str) -> str:
        return self.by_machine.get(key) or (key[:8] if key else "another machine")

    def folder(self, key: str) -> str:
        return self.by_folder.get(key) or (key[:8] if key else "another device")


def halt_state_line(snap: dict, names: _Names) -> str:
    """``Running`` or ``Halted by <device> at <time> (their clock): <reason>``."""
    active = sorted(snap.get("active") or [], key=lambda o: o.get("issued_at", 0))
    if not active:
        # F4-3: a network that cannot lift a halt says so, never shows nothing
        return "Running" if snap.get("halt_allowed", True) else f"Running. {ONE_COUNTING_LINE}"
    newest = active[-1]
    line = f"Halted by {names.machine(newest.get('machine', ''))} at {_when(newest.get('issued_at'))} (their clock)"
    reason = _quote(newest.get("reason") or "")
    if reason:
        line += f": {reason}"
    if len(active) > 1:
        line += f" (and {len(active) - 1} more)"
    return line


async def _halt_data(ctx: Any) -> tuple[dict, _Names]:
    snap = await ctx.hub_halt_snapshot()
    members = await ctx.hub_halt_members()
    if not isinstance(snap, dict) or not isinstance(members, list):
        raise ValueError("bad halt snapshot")
    return snap, _Names([m for m in members if isinstance(m, dict)])


class ConnectHaltPanel:
    """Halt and Resume: active halts, resume proposals, revocations, machines."""

    name = "connect-halt"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict) -> dict:
        panel = _picker(self.name, "Halt and Resume")
        try:
            snap, names = await _halt_data(ctx)
        except Exception as exc:  # noqa: BLE001 - no network owner and no listener
            logger.debug("connect-halt: snapshot unavailable: %s", type(exc).__name__)
            panel["notice"] = HALT_UNAVAILABLE
            return panel
        me = snap.get("me", "")
        counts = bool(snap.get("counts"))
        halted = bool(snap.get("active"))
        online = [names.folder(k) for k in snap.get("members_online") or []]
        panel["summary"] = [
            {"label": "Network State", "value": halt_state_line(snap, names)},
            {"label": "This Machine", "value": "Counts" if counts else "Does not count yet"},
            {"label": "Online Now", "value": ", ".join(online) or "None"},
        ]
        counting = {m["machine"] for m in names.members if m.get("counts") and m.get("machine")}
        notes: list[str] = []
        if not snap.get("halt_allowed"):
            notes.append(ONE_MACHINE_NOTE)
        if not counts:
            notes.append(NOT_COUNTING_NOTE)
        rows: list[dict] = []
        reasons = {o["hash"]: o.get("reason") or "no reason given" for o in snap.get("active") or []}
        own_open = False
        for prop in snap.get("proposals") or []:
            signers = [m for m in prop.get("signers") or [] if m]
            mine = prop.get("machine") == me
            own_open = own_open or mine
            lifts = "; ".join(_quote(reasons.get(h, h[:8])) for h in prop.get("lifts") or []) or "nothing"
            signed = ", ".join(
                f"{names.machine(m)} ({'counts' if m in counting else 'does not count'})" for m in signers
            )
            detail = [f"Lifts: {lifts}", f"Signed: {signed}"]
            detail += [
                f"{names.machine(m)}'s confirmation does not count: "
                f"{names.machine(m)} does not count on this network."
                for m in signers[1:] if m not in counting
            ]
            if mine:
                detail.append(f"Waiting for a second machine to confirm. Online: {', '.join(online) or 'none'}.")
            actions = []
            if mine:
                actions.append("withdraw")
            elif counts and me not in signers:
                actions.append("confirm_resume")
            rows.append({
                "id": f"proposal:{prop['hash']}",
                "label": f"Resume proposed by {names.machine(prop.get('machine', ''))}",
                "detail": " · ".join(detail),
                "group": "Resume Proposals",
                "actions": actions,
            })
        signers_of = snap.get("revocation_signers") or {}
        for key in snap.get("pending_revocations") or []:
            by = signers_of.get(key) or []
            who = ", ".join(names.machine(m) for m in by) or "another machine"
            rows.append({
                "id": f"revoke:{key}",
                "label": f"{names.folder(key)} revoked by {who}, waiting for a second machine",
                "detail": "",
                "group": "Pending Revocations",
                "actions": ["confirm_revoke"] if counts and me not in by else [],
            })
        my_name = names.by_machine.get(me, "")
        for machine in snap.get("unconfirmed_machines") or []:
            row = next((m for m in names.members if m.get("machine") == machine), {})
            founder = names.by_machine.get(snap.get("founder", ""), "")
            if row.get("chain_unknown") or not row.get("chain"):
                chain_text = "Chain unknown"
            else:
                chain_text = " ← ".join(
                    [names.machine(machine)]
                    + [f"{n} (founder)" if n and n == founder else n for n in row["chain"]]
                )
            outside = my_name not in (row.get("chain") or [])
            rows.append({
                "id": f"machine:{machine}",
                "label": names.machine(machine),
                "detail": chain_text,
                "group": "Unconfirmed Machines",
                "actions": ["confirm_machine"] if counts and machine != me and outside else [],
            })
        # one row per founder a person could accept, each with its own key (two candidates need a pick)
        for key in dict.fromkeys([snap.get("founder_unaccepted") or "", *(snap.get("founder_candidates") or [])]):
            if not key:
                continue
            rows.append({
                "id": f"founder:{key}",
                "label": f"Founder {names.machine(key)}",
                "detail": f"fingerprint {key[:16]}",
                "group": "Founder",
                "actions": ["confirm_founder"],
            })
        if snap.get("bootstrap_open"):
            for m in names.members:
                if m.get("machine") and m["machine"] != me and not m.get("revoked"):
                    rows.append({
                        "id": f"bootstrap:{m['machine']}",
                        "label": m.get("name") or m["machine"][:8],
                        "detail": "The second machine that counts, so a resume can be confirmed.",
                        "group": "Bootstrap Machine",
                        "actions": ["make_bootstrap"],
                    })
        for row in rows:
            row.setdefault("current", False)
            row.setdefault("badges", [])
        panel["rows"] = rows
        panel["row_actions"] = [
            {"id": "confirm_resume", "label": "Confirm Resume", "confirm": True},
            {"id": "withdraw", "label": "Withdraw", "confirm": True},
            {"id": "confirm_revoke", "label": "Confirm Revoke", "confirm": True},
            {"id": "confirm_machine", "label": "Confirm Machine", "confirm": True},
            {"id": "confirm_founder", "label": "Confirm Founder", "confirm": True},
            {"id": "make_bootstrap", "label": "Make Bootstrap Machine", "confirm": True},
        ]
        toolbar = []
        if snap.get("halt_allowed"):
            toolbar.append({"id": "halt_new", "label": "Halt Network…"})
        if halted and not own_open:
            toolbar.append({"id": "propose_resume", "label": "Propose Resume", "confirm": True})
        if not halted and counts and not snap.get("opted_out"):
            toolbar.append({"id": "no_count", "label": "Do Not Count Here", "confirm": True})
        if not halted and snap.get("opted_out"):
            toolbar.append({"id": "count", "label": "Count Here"})
        if halted and not snap.get("others_online_since_halt"):
            toolbar.append({"id": "resume_local", "label": "Resume Here Only", "confirm": True})
        panel["toolbar_actions"] = toolbar
        if not rows and not halted:
            notes.append("No halt, proposal or revocation is waiting.")
        panel["notice"] = " ".join(notes)
        return panel

    _ROW_ACTIONS = {
        "proposal": ("confirm_resume", "withdraw"),
        "revoke": ("confirm_revoke",),
        "machine": ("confirm_machine",),
        "founder": ("confirm_founder",),
        "bootstrap": ("make_bootstrap",),
    }
    _PAYLOAD_KEY = {
        "proposal": "hash", "revoke": "key", "machine": "machine", "bootstrap": "machine", "founder": "founder",
    }
    _TOOLBAR = ("propose_resume", "no_count", "count", "resume_local")

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action == "halt_new":
            return ok_result(open=await PANELS["connect-halt-new"].describe(ctx, {}))
        if action in self._TOOLBAR:
            args: dict = {}
        else:
            kind, _, key = str(payload.get("id") or "").partition(":")
            if action not in self._ROW_ACTIONS.get(kind, ()):
                raise unknown_action(self.name, action)
            args = {self._PAYLOAD_KEY[kind]: key} if kind in self._PAYLOAD_KEY else {}
        if action in CONFIRMED_ACTIONS:
            if payload.get("confirmed") is not True:  # the client sends it after the person confirms
                return error_result(CONFIRM_REFUSAL)
            args["confirmed"] = True
        try:
            outcome = await ctx.hub_halt_act(action, args)
        except Exception as exc:  # noqa: BLE001 - halt errors are operator-safe text
            return error_result(_sentence(str(exc)), panel=await self.describe(ctx, {}))
        return ok_result(_halt_message(action, outcome or {}), panel=await self.describe(ctx, {}))


def _sentence(text: str) -> str:
    text = text.strip() or "Try again"
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _halt_message(action: str, outcome: dict) -> str:
    if action == "propose_resume":
        return PROPOSED_LINE
    if action == "confirm_resume":
        lifted = outcome.get("lifted") or []
        if lifted:
            return f"Resume confirmed: lifts {len(lifted)} halt{'s' if len(lifted) != 1 else ''}."
        return "Confirmation recorded. Waiting for a second machine that counts."
    if action == "resume_local":
        return str(outcome.get("line") or "Lifted on this machine only.")
    return {
        "withdraw": "Proposal withdrawn.",
        "confirm_revoke": "Revocation confirmed.",
        "confirm_machine": "Machine confirmed.",
        "confirm_founder": "Founder accepted.",
        "make_bootstrap": "Bootstrap machine picked.",
        "no_count": "This machine no longer counts here.",
        "count": "This machine counts here again.",
        "halt": "Network halted.",
    }.get(action, "Done.")


class ConnectHaltNewPanel:
    """Halt Network…: a reason and, optionally, one device to revoke with it."""

    name = "connect-halt-new"
    kind = "form"

    async def describe(self, ctx: Any, params: dict) -> dict:
        try:
            snap, names = await _halt_data(ctx)
        except Exception:  # noqa: BLE001
            snap, names = {}, _Names([])
        options = ["none"] + [
            m["folder"] for m in names.members
            if m.get("folder") and m.get("machine") != snap.get("me") and not m.get("revoked")
        ]
        labels = {"none": "None", **{k: names.folder(k) for k in options[1:]}}
        form = {
            "panel": self.name,
            "kind": self.kind,
            "title": "Halt Network",
            "scope_note": (
                "Stops every agent on every machine of this network "
                "until a person on two machines resumes it."
            ),
            "sections": [
                {
                    "id": "halt",
                    "title": "Halt Network",
                    "fields": [
                        make_field("reason", "text_input", "Reason", value="",
                                   help="Shown on every machine with the halt."),
                        make_field("revoke", "dropdown", "Also Revoke", value="none", options=options,
                                   option_labels=labels,
                                   help="A device to remove from the network with this halt."),
                    ],
                }
            ],
            "actions": [{"id": "halt", "label": "Halt Network", "confirm": True}],
            "save_targets": {},
            # live without edits (FormPanel enables the button when a context is set)
            "context": {"network": snap.get("network", "")},
        }
        if snap and not snap.get("halt_allowed"):
            form["notice"] = ONE_MACHINE_NOTE
        return form

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action != "halt":
            raise unknown_action(self.name, action)
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
        reason = " ".join(str(changes.get("reason") or "").split())[:200]
        revoke = str(changes.get("revoke") or "none")
        args: dict = {"reason": reason}
        if revoke != "none":
            args["revoke"] = [revoke]
        if payload.get("confirmed") is not True:  # the client sends it after the person confirms
            return error_result(CONFIRM_REFUSAL)
        args["confirmed"] = True
        try:
            await ctx.hub_halt_act("halt", args)
        except Exception as exc:  # noqa: BLE001
            return error_result(_sentence(str(exc)))
        return ok_result("Network halted.", open=await PANELS["connect-halt"].describe(ctx, {}))


PANELS = {
    panel.name: panel
    for panel in (
        ConnectPanel(), ConnectJoinPanel(), ConnectKnocksPanel(), ConnectHaltPanel(), ConnectHaltNewPanel()
    )
}
