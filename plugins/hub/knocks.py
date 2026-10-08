"""Knocks as calls (docs/specs/agent-network-simple-flow.md, Story 5).

A knock rings the device behind a contact route for `knock_wire.RING_SECONDS`
through the directory's websocket, and the directory keeps nothing. This is the
device side: the calls this device places (ringing, then redialing), the knocks
ringing here, the missed list kept on this device, and who may knock. Only the
human answers. A stranger's text reaches the knock screen and nothing a model
reads; main-pane lines name the device only.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nacl.exceptions import CryptoError
from nacl.public import Box
from nacl.signing import SigningKey, VerifyKey

from . import knock_wire
from .device_names import (
    NAME_RE,
    contact_route_hex,
    device_key_fingerprint,
    key_label,
    short_fingerprint,
)
from .relay_state import KEY, RelayError, strict_json

logger = logging.getLogger(__name__)

RING_SECONDS = knock_wire.RING_SECONDS
MAX_RINGING = 5
MISSED_LIMIT = 20  # plugins.hub.knock_missed_limit
REDIAL_MINUTES = 60  # plugins.hub.knock_redial_minutes; 0 rings once
REDIAL_STEPS = (60, 120, 240, 480, 900)  # seconds between tries, then every 900
REJECT_MUTE_SECONDS = 60 * 60
ANSWER_TIMEOUT = 10.0
MAX_TEXT_BYTES = 2048
MAX_ROUTES = 256  # blocked routes, and routes on the contact list
MAX_FILE_BYTES = 512 * 1024
MODES = ("everyone", "contacts", "nobody")
# The knock screen reloads every couple of seconds: a load this recent means a
# human is looking at it, so the main pane stays quiet about what it shows.
SCREEN_OPEN_SECONDS = 6.0
ROUTE = re.compile(r"[0-9a-f]{16}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_KNOCK_BODY = frozenset({"v", "kind", "from", "to", "id", "device", "text", "sent_at"})
_ANSWER_BODY = frozenset({"v", "kind", "from", "to", "id", "answer", "device"})


def validate_text(value: Any) -> str:
    """A knock's text: 1 to 2048 UTF-8 bytes, no control characters but newline and tab."""
    if not isinstance(value, str):
        raise ValueError("knock text must be text")
    text = value.strip()
    if (
        not text
        or len(text.encode("utf-8")) > MAX_TEXT_BYTES
        or any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text)
    ):
        raise ValueError("knock text must be 1 to 2048 bytes of printable text")
    return text


def clock_text(seconds: float) -> str:
    """`4:58`: minutes and seconds left, for a ringing or redialing knock."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def duration_text(seconds: int) -> str:
    """`1h`, `45m`, `1h30m`: how long a redial window or a setting lasts."""
    hours, minutes = divmod(max(0, int(seconds)) // 60, 60)
    return (f"{hours}h" if hours else "") + (f"{minutes}m" if minutes or not hours else "")


def _box(signing_key: SigningKey, other_key: str) -> Box:
    return Box(
        signing_key.to_curve25519_private_key(),
        VerifyKey(bytes.fromhex(other_key)).to_curve25519_public_key(),
    )


def seal(signing_key: SigningKey, recipient_key: str, body: dict[str, Any]) -> str:
    """Encrypt a knock or answer body to the other device; only it can open it."""
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sealed = base64.b64encode(_box(signing_key, recipient_key).encrypt(raw)).decode("ascii")
    if len(sealed) > knock_wire.MAX_CIPHERTEXT_CHARS:
        raise ValueError("knock is too large")
    return sealed


def open_sealed(signing_key: SigningKey, sender_key: str, ciphertext: Any) -> dict[str, Any]:
    """Decrypt a body only `sender_key` could have sealed to this device."""
    if not isinstance(ciphertext, str) or len(ciphertext) > knock_wire.MAX_CIPHERTEXT_CHARS:
        raise ValueError("invalid knock ciphertext")
    try:
        raw = _box(signing_key, sender_key).decrypt(base64.b64decode(ciphertext, validate=True))
    except (CryptoError, ValueError, binascii.Error) as exc:
        raise ValueError("invalid knock ciphertext") from exc
    return strict_json(raw, limit=knock_wire.MAX_CIPHERTEXT_CHARS)


def _device(value: Any, key: str) -> str:
    return value if isinstance(value, str) and NAME_RE.fullmatch(value) else key_label(key)


def _fingerprint(key: str) -> str:
    return short_fingerprint(device_key_fingerprint(key))


def _spoken(device: str, key: str) -> str:
    """A device name fit for the main pane: a real one, never its hex stand-in."""
    return "an unknown device" if device == key_label(key) else device


class KnockStore:
    """What this device keeps about knocks: who may knock, and the missed list.

    One private file beside the device key. A file that cannot be read starts
    over empty rather than stopping the network.
    """

    def __init__(self, path: Path):
        self.path = path
        self._reset()
        try:
            self._load()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            logger.warning("knock settings could not be read; starting with defaults")
            self._reset()

    def _reset(self) -> None:
        self.mode = "everyone"
        self.mode_until = 0  # epoch seconds; 0 keeps the mode
        self.blocked: dict[str, str] = {}  # route -> device name when known
        self.contacts: dict[str, bool] = {}  # route -> its next knock is accepted unasked
        self.muted: dict[str, int] = {}  # route -> until (a reject mutes it)
        self.missed: list[dict[str, Any]] = []  # newest first

    def _load(self) -> None:
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            data = strict_json(stream.read(MAX_FILE_BYTES + 1), limit=MAX_FILE_BYTES)
        if set(data) != {"v", "mode", "mode_until", "blocked", "contacts", "muted", "missed"}:
            raise ValueError("unexpected knock settings")
        if data["v"] != 1 or data["mode"] not in MODES or type(data["mode_until"]) is not int:
            raise ValueError("unexpected knock settings")
        blocked, contacts, muted = data["blocked"], data["contacts"], data["muted"]
        if (
            len(blocked) > MAX_ROUTES
            or len(contacts) > MAX_ROUTES
            or len(muted) > MAX_ROUTES
            or not all(ROUTE.fullmatch(r) and NAME_RE.fullmatch(n or "a") for r, n in blocked.items())
            or not all(ROUTE.fullmatch(r) and type(a) is bool for r, a in contacts.items())
            or not all(ROUTE.fullmatch(r) and type(u) is int for r, u in muted.items())
        ):
            raise ValueError("unexpected knock settings")
        missed = []
        for row in data["missed"][:100]:
            if set(row) != {"id", "key", "device", "text", "at"} or not (
                _ID.fullmatch(row["id"]) and KEY.fullmatch(row["key"]) and type(row["at"]) is int
            ):
                raise ValueError("unexpected missed knock")
            missed.append({**row, "device": _device(row["device"], row["key"]), "text": validate_text(row["text"])})
        self.mode, self.mode_until = data["mode"], data["mode_until"]
        self.blocked, self.contacts, self.muted, self.missed = dict(blocked), dict(contacts), dict(muted), missed

    def save(self) -> None:
        data = {
            "v": 1,
            "mode": self.mode,
            "mode_until": self.mode_until,
            "blocked": self.blocked,
            "contacts": self.contacts,
            "muted": self.muted,
            "missed": self.missed,
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".knocks-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:  # mkstemp creates it 0600
                json.dump(data, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


@dataclass
class Ringing:
    """A knock ringing on this device. Its repr names the device only."""

    id: str = field(repr=False)
    key: str = field(repr=False)
    device: str
    text: str = field(repr=False)
    ticket: dict[str, Any] = field(repr=False)
    ends: float


@dataclass
class Call:
    """A knock this device placed: ringing on the other side, or waiting to redial."""

    domain: str
    route: str
    text: str = field(repr=False)
    agent: str
    first: float
    key: str = field(default="", repr=False)
    id: str = field(default="", repr=False)  # the ringing try's knock id
    ends: float = 0.0  # when the ringing try rings out
    next_try: float = 0.0  # when to redial
    tries: int = 0
    dialing: bool = False

    @property
    def target(self) -> str:
        return f"{self.domain}/c/{self.route}"


def _noop(*_args, **_kwargs):
    return None


class KnockService:
    """The device side of knocks. Runs in the process that owns the relay connection."""

    def __init__(
        self,
        client,
        path: Path,
        *,
        notify: Callable[[str], None] = _noop,
        device_name: Callable[[], str] = lambda: "",
        bind_incoming: Callable[[str, str], Callable[[], None]] | None = None,
        bind_outgoing: Callable[[str, str], None] = _noop,
        links_changed: Callable[[], Any] = _noop,
        setting: Callable[[str, Any], Any] = lambda _name, default: default,
        clock: Callable[[], float] = time.time,
    ):
        self.client = client
        self.store = KnockStore(path)
        self.ringing: dict[str, Ringing] = {}
        self.calls: dict[str, Call] = {}  # by route: one call per route
        self._notify = notify
        self._device_name = device_name
        self._bind_incoming = bind_incoming or (lambda _key, _device: _noop)
        self._bind_outgoing = bind_outgoing
        self._links_changed = links_changed
        self._setting = setting
        self._clock = clock
        self._answers: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._screen_seen = float("-inf")  # time.monotonic() of the last screen load
        client.set_knock_handler(self.on_frame)

    # --- settings and small helpers ------------------------------------------

    def _limit(self, name: str, default: int, top: int) -> int:
        value = self._setting(f"plugins.hub.{name}", default)
        return value if type(value) is int and 0 <= value <= top else default

    def missed_limit(self) -> int:
        return max(1, self._limit("knock_missed_limit", MISSED_LIMIT, 100))

    def redial_seconds(self) -> int:
        return self._limit("knock_redial_minutes", REDIAL_MINUTES, 24 * 60) * 60

    def domain(self) -> str:
        return (self.client.state.origin or "").removeprefix("https://").lower()

    def online(self) -> bool:
        return self.client.status().get("state") == "online"

    @property
    def _key(self) -> SigningKey:
        return self.client._store.key

    def mode(self) -> str:
        store = self.store
        if store.mode_until and self._clock() >= store.mode_until:
            store.mode, store.mode_until = "everyone", 0
            store.save()
        return store.mode

    def waiting(self) -> int:
        """Knocks for the human: ringing now plus missed."""
        return len(self.ringing) + len(self.store.missed)

    def _say(self, line: str, *, shown_on_screen: bool = False) -> None:
        if shown_on_screen and time.monotonic() - self._screen_seen < SCREEN_OPEN_SECONDS:
            return
        if line:
            try:
                self._notify(line)
            except Exception:
                logger.warning("knock notice display failed")

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- frames from the directory ---------------------------------------------

    async def on_frame(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "knock":
            await self._incoming(frame)
        elif kind == "knock_answer":
            await self._answered(frame)
        elif kind == "knock_result":
            self._result(frame)

    def _admit(self, route: str, key: str) -> str:
        """What a knock from `route` gets: `ring`, `accept` or `unavailable`."""
        store, now = self.store, self._clock()
        if route in store.blocked or store.muted.get(route, 0) > now:
            return "unavailable"
        mode = self.mode()
        known = route in store.contacts or key in self.client.state.approvals
        if mode == "nobody" or (mode == "contacts" and not known):
            return "unavailable"
        if store.contacts.get(route):
            return "accept"
        if len(self.ringing) >= MAX_RINGING or len(store.missed) >= self.missed_limit():
            return "unavailable"
        return "ring"

    async def _incoming(self, frame: dict[str, Any]) -> None:
        me = self.client.public_key
        try:
            # Frames and sealed bodies are read tolerantly: a field a newer
            # version adds is ignored (docs/specs/agent-public-beacon.md#versioning).
            if not frame.keys() >= {"type", "from", "session", "id", "ticket", "ciphertext"}:
                raise ValueError("unexpected knock frame")
            sender, knock_id = frame["from"], frame["id"]
            if not (isinstance(sender, str) and KEY.fullmatch(sender)) or not (
                isinstance(knock_id, str) and _ID.fullmatch(knock_id)
            ):
                raise ValueError("unexpected knock frame")
            ticket = knock_wire.check_ticket(
                frame["ticket"], self.client.state.origin, now=self._clock()
            )
            if (ticket["from"], ticket["to"], ticket["id"]) != (sender, me, knock_id):
                raise ValueError("the ticket does not bind this knock")
            body = open_sealed(self._key, sender, frame["ciphertext"])
            if (
                not body.keys() >= _KNOCK_BODY
                or type(body["v"]) is not int
                or body["v"] != 1
                or (body["kind"], body["from"], body["to"], body["id"])
                != ("knock", sender, me, knock_id)
            ):
                raise ValueError("unexpected knock body")
            text = validate_text(body["text"])
        except ValueError:
            logger.debug("dropped a knock that did not verify")
            return
        if knock_id in self.ringing:
            return
        for other in [row for row in self.ringing.values() if row.key == sender]:
            del self.ringing[other.id]  # knocked again: the new knock replaces it
        now = self._clock()
        entry = Ringing(
            knock_id,
            sender,
            _device(body["device"], sender),
            text,
            ticket,
            min(ticket["ends"], now + RING_SECONDS),
        )
        route = contact_route_hex(sender)
        verdict = self._admit(route, sender)
        if verdict == "ring":
            self.ringing[knock_id] = entry
            self._say(
                f"{_spoken(entry.device, entry.key)} is knocking. /connect knocks to answer "
                f"({clock_text(entry.ends - now)})",
                shown_on_screen=True,
            )
        elif verdict == "accept":
            self.store.contacts[route] = False  # the first knock only
            self.store.save()
            self._say(await self._accept(entry, expected=True))
        # A refused knock gets no answer: it rings out on the knocker's side
        # like one nobody picked up, so a refusal reads like a busy afternoon.

    async def _send_answer(self, entry: Ringing, answer: str) -> str:
        """Seal and send an answer; the directory says `answered`, `unavailable` or `busy`.

        Only an accept is ever sent: every other answer is silence.
        """
        body = {
            "v": 1,
            "kind": "answer",
            "from": self.client.public_key,
            "to": entry.key,
            "id": entry.id,
            "answer": answer,
            "device": self._device_name() or "",
        }
        future = asyncio.get_running_loop().create_future()
        self._answers[entry.id] = future
        try:
            await self.client.send_knock_answer(
                entry.id, entry.ticket, seal(self._key, entry.key, body)
            )
            async with asyncio.timeout(ANSWER_TIMEOUT):
                return await future
        except (RelayError, OSError, TimeoutError, ValueError):
            return "busy"
        finally:
            self._answers.pop(entry.id, None)

    async def _accept(self, entry: Ringing, *, expected: bool = False) -> str:
        try:
            undo = self._bind_incoming(entry.key, entry.device)
        except RelayError as exc:  # the knock keeps ringing
            if getattr(exc, "code", "") == "name_taken":
                return (
                    f"connect: a device named {entry.device} is already on this network; "
                    "it must pick another name (/connect name) and knock again"
                )
            return f"connect: {exc}"
        result = await self._send_answer(entry, "accept")
        if result != "answered":
            undo()
            if result == "busy":  # the knock keeps ringing: try again
                return f"connect: the accept to {entry.device} could not be sent; try again"
            self.ringing.pop(entry.id, None)
            return f"connect: {entry.device} hung up before the accept reached it"
        self.ringing.pop(entry.id, None)
        try:
            await self._links_changed()
        except Exception:
            logger.debug("links not declared after an accept; the refresh retries")
        how = f"{entry.device} knocked and was accepted, as expected" if expected else f"accepted {entry.device}"
        return f"{how}. /connect allow {entry.device} <agent> lets it message one of your agents"

    def _result(self, frame: dict[str, Any]) -> None:
        knock_id, result = frame.get("id"), frame.get("result")
        if not frame.keys() >= {"type", "id", "result"} or not isinstance(result, str):
            return
        future = self._answers.get(knock_id)
        if future is not None:
            if not future.done():
                future.set_result(result)
            return
        call = self._call(knock_id)
        if call is not None:
            self._say(self._failed(call, result))

    def _call(self, knock_id: Any) -> Call | None:
        return next((c for c in self.calls.values() if c.id and c.id == knock_id), None)

    async def _answered(self, frame: dict[str, Any]) -> None:
        if not frame.keys() >= {"type", "from", "session", "id", "ciphertext"}:
            return
        call = self._call(frame["id"])
        if call is None or frame["from"] != call.key:
            return
        me = self.client.public_key
        try:
            body = open_sealed(self._key, call.key, frame["ciphertext"])
            if (
                not body.keys() >= _ANSWER_BODY
                or type(body["v"]) is not int
                or body["v"] != 1
                or (body["kind"], body["from"], body["to"], body["id"])
                != ("answer", call.key, me, call.id)
                or body["answer"] not in ("accept", "unavailable")
            ):
                raise ValueError("unexpected answer body")
        except ValueError:
            logger.debug("dropped a knock answer that did not verify")
            return
        if body["answer"] != "accept":
            self._say(self._failed(call, "unavailable"))
            return
        self.calls.pop(call.route, None)
        device = _device(body["device"], call.key)
        try:
            self._bind_outgoing(call.key, call.agent)
            await self._links_changed()
        except Exception:
            logger.warning("a knock was accepted, but the reply path could not be prepared")
            self._say(f"connect: {device} accepted, but this device could not record it")
            return
        self._say(f"{device} accepted. Its agents appear once it allows them.")

    # --- calls this device places ----------------------------------------------

    async def knock(self, domain: str, route: str, text: str, agent: str) -> str:
        """Place a call to `domain/c/route`; the line says how it went."""
        text = validate_text(text)
        domain, own = domain.lower(), self.domain()
        if not ROUTE.fullmatch(route):
            raise ValueError("not a contact route")
        if not self.online():
            return "connect: this device is offline; it knocks through its network's directory"
        if domain != own:
            return f"connect: this device knocks through {own}; knock a route on {own}"
        if route == contact_route_hex(self.client.public_key):
            return "connect: that is this device's own contact route"
        call = Call(domain, route, text, agent, first=self._clock())
        self.calls[route] = call
        result = await self._dial(call)
        if result == "ringing":
            return f"knocking on {call.target}, rings for {clock_text(RING_SECONDS)}"
        return self._failed(call, result)

    async def _dial(self, call: Call) -> str:
        """One try: find the device behind the route and ring it."""
        call.dialing = True
        try:
            result, key = await self.client.lookup(call.route)
            if result != "found" or self.calls.get(call.route) is not call:
                return result  # a stopped call ends quietly in _failed
            call.key, call.id = key, secrets.token_hex(16)
            ends = int(self._clock()) + RING_SECONDS
            body = {
                "v": 1,
                "kind": "knock",
                "from": self.client.public_key,
                "to": key,
                "id": call.id,
                "device": self._device_name() or "",
                "text": call.text,
                "sent_at": int(self._clock()),
            }
            ticket = knock_wire.sign_ticket(
                self._key, self.client.state.origin, key, call.id, ends
            )
            await self.client.send_knock(key, call.id, ticket, seal(self._key, key, body))
            call.ends, call.next_try = ends, 0.0
            return "ringing"
        except (RelayError, OSError, TimeoutError):
            return "unavailable"
        finally:
            call.dialing = False

    def _failed(self, call: Call, result: str) -> str:
        """A try did not connect: schedule the redial, or end the call. Returns the line."""
        if self.calls.get(call.route) is not call:
            return ""
        call.id, call.ends = "", 0.0
        if result in ("outdated", "refused"):
            del self.calls[call.route]
            if result == "outdated":
                return f"connect: {call.domain} needs an update to carry knocks"
            return f"connect: {call.domain} refused the knock; check this computer's clock"
        word = "busy" if result == "busy" else "unavailable"
        window, now = self.redial_seconds(), self._clock()
        call.tries += 1
        step = REDIAL_STEPS[min(call.tries - 1 + (word == "busy"), len(REDIAL_STEPS) - 1)]
        if not window:
            del self.calls[call.route]
            return f"{call.target} {word}. try again later"
        if now + step > call.first + window:
            del self.calls[call.route]
            return f"no answer from {call.target} after {duration_text(window)}. try again later"
        call.next_try = now + step
        if call.tries > 1:
            return ""
        return (
            f"{call.target} {word}. redialing for {duration_text(window)}, "
            f"next in {clock_text(step)}. /connect knocks to stop"
        )

    async def _redial(self, call: Call) -> None:
        result = await self._dial(call)
        if result != "ringing":
            self._say(self._failed(call, result))

    async def tick(self) -> None:
        """Ring out, redial, and let timed settings lapse. Called about once a second."""
        now = self._clock()
        for entry in [row for row in self.ringing.values() if row.ends <= now]:
            del self.ringing[entry.id]
            if len(self.store.missed) < self.missed_limit():
                self.store.missed.insert(
                    0,
                    {
                        "id": entry.id,
                        "key": entry.key,
                        "device": entry.device,
                        "text": entry.text,
                        "at": int(now),
                    },
                )
                self.store.save()
            self._say(
                f"missed a knock from {_spoken(entry.device, entry.key)}. /connect knocks to see it",
                shown_on_screen=True,
            )
        for call in list(self.calls.values()):
            if call.dialing:
                continue
            if call.ends and now >= call.ends + 5:  # rang out on the other side
                self._say(self._failed(call, "unavailable"))
            elif call.next_try and now >= call.next_try:
                call.dialing = True
                self._spawn(self._redial(call))
        lapsed = [route for route, until in self.store.muted.items() if until <= now]
        for route in lapsed:
            del self.store.muted[route]
        if lapsed:
            self.store.save()
        self.mode()

    # --- what the human sees and does ------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The knock screen: names, short fingerprints and routes, never keys."""
        self._screen_seen = time.monotonic()
        now, domain = self._clock(), self.domain()
        mode = self.mode()

        def row(entry: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": entry["id"],
                "device": entry["device"],
                "fingerprint": _fingerprint(entry["key"]),
                "route": contact_route_hex(entry["key"]),
                "text": entry["text"],
            }

        return {
            "online": self.online(),
            "domain": domain,
            "mode": mode,
            "mode_until": self.store.mode_until,
            "ringing": [
                {**row(vars(entry)), "left": max(0, int(entry.ends - now))}
                for entry in sorted(self.ringing.values(), key=lambda e: -e.ends)
            ],
            "missed": [{**row(entry), "at": entry["at"]} for entry in self.store.missed],
            "missed_limit": self.missed_limit(),
            "calls": [
                {
                    "route": call.route,
                    "target": call.target,
                    "state": "ringing" if call.ends else "redialing",
                    "left": max(0, int((call.ends or call.next_try) - now)),
                }
                for call in self.calls.values()
            ],
            "blocked": [{"route": r, "device": d} for r, d in sorted(self.store.blocked.items())],
            "contacts": [
                {"route": r, "expected": a} for r, a in sorted(self.store.contacts.items())
            ],
        }

    def _missed(self, knock_id: Any) -> dict[str, Any] | None:
        return next((row for row in self.store.missed if row["id"] == knock_id), None)

    async def act(self, action: str, args: dict[str, Any], *, agent: str) -> str:
        """One human action from the knock screen, a command or the web UI."""
        store = self.store
        if action == "knock":
            return await self.knock(args["domain"], args["route"], args["text"], agent)
        if action in ("accept", "reject"):
            entry = self.ringing.get(args.get("id"))
            if entry is None:
                return "connect: that knock is no longer ringing"
            if action == "accept":
                return await self._accept(entry)
            del self.ringing[entry.id]
            store.muted[contact_route_hex(entry.key)] = int(self._clock()) + REJECT_MUTE_SECONDS
            store.save()
            return f"rejected {entry.device}"
        if action == "block":
            entry = self.ringing.pop(args.get("id"), None)
            missed = self._missed(args.get("id"))
            if entry is None and missed is None:
                return "connect: that knock is gone"
            key, device = (entry.key, entry.device) if entry else (missed["key"], missed["device"])
            if len(store.blocked) >= MAX_ROUTES:
                return f"connect: {MAX_ROUTES} routes are blocked already; unblock one first"
            store.blocked[contact_route_hex(key)] = device
            store.missed = [row for row in store.missed if row["key"] != key]
            store.save()
            return f"blocked {device}"
        if action == "delete":
            missed = self._missed(args.get("id"))
            if missed is None:
                return "connect: that knock is gone"
            store.missed.remove(missed)
            store.save()
            return f"deleted the knock from {missed['device']}"
        if action == "clear":
            store.missed = []
            store.save()
            return "missed knocks cleared"
        if action == "knock_back":
            missed = self._missed(args.get("id"))
            if missed is None:
                return "connect: that knock is gone"
            return await self.knock(self.domain(), contact_route_hex(missed["key"]), args["text"], agent)
        if action == "stop":
            call = self.calls.pop(args.get("route"), None)
            return f"stopped knocking on {call.target}" if call else "connect: no knock to stop"
        if action == "mode":
            mode, minutes = args.get("mode"), args.get("minutes", 0)
            if mode not in MODES or type(minutes) is not int or not 0 <= minutes <= 7 * 24 * 60:
                return "connect: use /connect knocks everyone|contacts|nobody [for 30m|2h]"
            store.mode = mode
            store.mode_until = int(self._clock()) + minutes * 60 if minutes and mode != "everyone" else 0
            store.save()
            who = {"everyone": "everyone", "contacts": "only contacts", "nobody": "nobody"}[mode]
            until = f" for {duration_text(minutes * 60)}" if store.mode_until else ""
            return f"{who} may knock{until}"
        if action not in ("block_route", "unblock", "expect", "unexpect"):
            return "connect: unknown knock action"
        route = args.get("route")
        if not isinstance(route, str) or not ROUTE.fullmatch(route):
            return "connect: that is not a contact route"
        if action == "block_route":
            if route not in store.blocked and len(store.blocked) >= MAX_ROUTES:
                return f"connect: {MAX_ROUTES} routes are blocked already; unblock one first"
            store.blocked.setdefault(route, "")
            store.missed = [row for row in store.missed if contact_route_hex(row["key"]) != route]
            store.save()
            return f"blocked {route}"
        if action == "unblock":
            if store.blocked.pop(route, None) is None:
                return f"connect: {route} is not blocked"
            store.save()
            return f"unblocked {route}"
        if action == "expect":
            if route not in store.contacts and len(store.contacts) >= MAX_ROUTES:
                return f"connect: the contact list holds {MAX_ROUTES} routes; remove one first"
            store.contacts[route] = True
            store.save()
            return f"expecting a knock from {route}: its first knock is accepted"
        if action == "unexpect":
            if store.contacts.pop(route, None) is None:
                return f"connect: {route} is not on the contact list"
            store.save()
        return f"removed {route} from contacts"
