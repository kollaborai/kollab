"""Knocks as calls, the device side (plugins/hub/knocks.py).

A fake client stands in for the directory's websocket; tickets, sealing and
the store are real. The clock is a number the tests move.
"""

import asyncio
import json
import os
import secrets
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from plugins.hub import knock_wire, knocks
from plugins.hub.device_names import contact_route_hex
from plugins.hub.knocks import KnockService, KnockStore, open_sealed, seal, validate_text
from plugins.hub.relay_state import RelayStateStore

ORIGIN = "https://relay.example"
ANA = SigningKey.generate()
ANA_HEX = ANA.verify_key.encode().hex()
ANA_ROUTE = contact_route_hex(ANA_HEX)
BOB = SigningKey.generate()
BOB_HEX = BOB.verify_key.encode().hex()
BOB_ROUTE = contact_route_hex(BOB_HEX)


class _Client:
    """What the knock service reads from the relay client, and what it sent."""

    def __init__(self):
        self._store = SimpleNamespace(key=SigningKey.generate())
        self.public_key = self._store.key.verify_key.encode().hex()
        self.state = SimpleNamespace(origin=ORIGIN, approvals=[])
        self.is_online = True
        self.lookups: dict[str, tuple[str, str]] = {}
        self.looked_up: list[str] = []
        self.knocks: list[tuple] = []
        self.answers: list[tuple] = []
        self.answer_result = "answered"
        self.handler = None

    def set_knock_handler(self, handler):
        self.handler = handler

    def status(self):
        return {"state": "online" if self.is_online else "reconnecting"}

    async def lookup(self, route):
        self.looked_up.append(route)
        return self.lookups.get(route, ("unavailable", ""))

    async def send_knock(self, to, knock_id, ticket, ciphertext):
        self.knocks.append((to, knock_id, ticket, ciphertext))

    async def send_knock_answer(self, knock_id, ticket, ciphertext):
        self.answers.append((knock_id, ticket, ciphertext))
        frame = {"type": "knock_result", "id": knock_id, "result": self.answer_result}
        asyncio.get_running_loop().create_task(self.handler(frame))


class _Clock:
    now = 1_800_000_000.0

    def __call__(self):
        return self.now


def _world(tmp_path, settings=None, **kwargs):
    client, clock, said = _Client(), _Clock(), []
    service = KnockService(
        client,
        tmp_path / "knocks.json",
        notify=said.append,
        device_name=lambda: "laptop-kollab",
        setting=lambda name, default: (settings or {}).get(name, default),
        clock=clock,
        **kwargs,
    )
    return SimpleNamespace(svc=service, client=client, clock=clock, said=said, me=client.public_key)


def _knock(world, sender=ANA, *, text="Ana from Acme", device="ana-laptop", to=None, ends=None):
    """A knock frame as the directory delivers it, from `sender` to this device."""
    knock_id = secrets.token_hex(16)
    sender_hex = sender.verify_key.encode().hex()
    now = int(world.clock.now)
    ticket = knock_wire.sign_ticket(sender, ORIGIN, to or world.me, knock_id, ends or now + 300)
    body = {"v": 1, "kind": "knock", "from": sender_hex, "to": world.me, "id": knock_id,
            "device": device, "text": text, "sent_at": now}
    return {"type": "knock", "from": sender_hex, "session": "s" * 32, "id": knock_id,
            "ticket": ticket, "ciphertext": seal(sender, world.me, body)}


def _answer(world, call_id, answer="accept", sender=BOB, device="bob-desk"):
    sender_hex = sender.verify_key.encode().hex()
    body = {"v": 1, "kind": "answer", "from": sender_hex, "to": world.me, "id": call_id,
            "answer": answer, "device": device}
    return {"type": "knock_answer", "from": sender_hex, "session": "s" * 32, "id": call_id,
            "ciphertext": seal(sender, world.me, body)}


def _answers(world, sender=ANA):
    """The answers this device sealed, opened as `sender` would."""
    return [open_sealed(sender, world.me, ciphertext)["answer"] for _id, _t, ciphertext in world.client.answers]


async def _settle(world):
    await asyncio.gather(*list(world.svc._tasks))


def _act(world, action, **args):
    return world.svc.act(action, args, agent="ops")


# --- who may knock -----------------------------------------------------------


def _mode(world, mode):
    world.svc.store.mode = mode


CASES = {
    "a stranger rings": (lambda w: None, "ring"),
    "a blocked route": (lambda w: w.svc.store.blocked.update({ANA_ROUTE: ""}), "unavailable"),
    "a muted route": (lambda w: w.svc.store.muted.update({ANA_ROUTE: int(w.clock.now) + 60}), "unavailable"),
    "a lapsed mute": (lambda w: w.svc.store.muted.update({ANA_ROUTE: int(w.clock.now) - 1}), "ring"),
    "nobody": (lambda w: _mode(w, "nobody"), "unavailable"),
    "contacts only, a stranger": (lambda w: _mode(w, "contacts"), "unavailable"),
    "contacts only, on the list": (
        lambda w: (_mode(w, "contacts"), w.svc.store.contacts.update({ANA_ROUTE: False})), "ring"),
    "contacts only, an approved peer": (
        lambda w: (_mode(w, "contacts"), w.client.state.approvals.append(ANA_HEX)), "ring"),
    "expected": (lambda w: w.svc.store.contacts.update({ANA_ROUTE: True}), "accept"),
    "expected, but nobody may knock": (
        lambda w: (_mode(w, "nobody"), w.svc.store.contacts.update({ANA_ROUTE: True})), "unavailable"),
    "expected, but blocked": (
        lambda w: (w.svc.store.blocked.update({ANA_ROUTE: ""}), w.svc.store.contacts.update({ANA_ROUTE: True})),
        "unavailable"),
    "five already ringing": (lambda w: w.svc.ringing.update({str(i) * 32: None for i in range(5)}), "unavailable"),
    "the missed box is full": (lambda w: w.svc.store.missed.extend([{}] * 20), "unavailable"),
    "expected beats a full box": (
        lambda w: (w.svc.store.missed.extend([{}] * 20), w.svc.store.contacts.update({ANA_ROUTE: True})), "accept"),
}


@pytest.mark.parametrize("case", CASES)
def test_who_may_knock_is_decided_in_order(tmp_path, case):
    world = _world(tmp_path)
    setup, verdict = CASES[case]
    setup(world)
    assert world.svc._admit(ANA_ROUTE, ANA_HEX) == verdict


# --- knocks that ring here ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_knock_rings_and_only_the_screen_shows_its_text(tmp_path, monkeypatch):
    world = _world(tmp_path)
    frame = _knock(world, text="the secret plan")

    await world.svc.on_frame(frame)

    assert list(world.svc.ringing) == [frame["id"]]
    assert world.said == ["ana-laptop is knocking. /connect knocks to answer (5:00)"]
    snapshot = world.svc.snapshot()
    (row,) = snapshot["ringing"]
    assert row["device"] == "ana-laptop" and row["text"] == "the secret plan" and row["left"] == 300
    assert row["route"] == ANA_ROUTE and ANA_HEX not in json.dumps(snapshot)
    assert world.client.answers == []  # nothing is answered until the human does


@pytest.mark.asyncio
async def test_a_device_with_no_valid_name_is_an_unknown_device_on_the_main_pane(tmp_path):
    world = _world(tmp_path)

    await world.svc.on_frame(_knock(world, device="not a name!"))

    assert world.said == ["an unknown device is knocking. /connect knocks to answer (5:00)"]


@pytest.mark.asyncio
async def test_a_field_a_newer_version_adds_does_not_stop_the_ring(tmp_path):
    # Versioning: an unknown field in the frame or the sealed body is ignored.
    world = _world(tmp_path)
    frame = _knock(world)
    frame["note"] = "hi"
    body = {"v": 1, "kind": "knock", "from": ANA_HEX, "to": world.me, "id": frame["id"],
            "device": "ana-laptop", "text": "hello", "sent_at": 0, "newer": True}
    frame["ciphertext"] = seal(ANA, world.me, body)

    await world.svc.on_frame(frame)

    assert list(world.svc.ringing) == [frame["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("break_it", ["ticket to someone else", "sealed to someone else", "stale ticket",
                                      "body from someone else"])
async def test_a_knock_that_does_not_verify_is_dropped_without_a_word(tmp_path, break_it):
    world = _world(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()
    if break_it == "ticket to someone else":
        frame = _knock(world, to=other)
    elif break_it == "stale ticket":
        frame = _knock(world, ends=int(world.clock.now) - 600)
    else:
        frame = _knock(world)
    if break_it == "sealed to someone else":
        frame["ciphertext"] = seal(ANA, other, {"v": 1})
    elif break_it == "body from someone else":
        body = {"v": 1, "kind": "knock", "from": BOB_HEX, "to": world.me, "id": frame["id"],
                "device": "x", "text": "x", "sent_at": 0}
        frame["ciphertext"] = seal(ANA, world.me, body)

    await world.svc.on_frame(frame)

    assert world.svc.ringing == {} and world.client.answers == [] and world.said == []


@pytest.mark.asyncio
async def test_a_refused_knock_gets_no_answer_and_rings_out_at_the_knocker(tmp_path):
    world = _world(tmp_path)
    world.svc.store.blocked[ANA_ROUTE] = ""

    await world.svc.on_frame(_knock(world))

    assert world.client.answers == []  # silence: a refusal reads like nobody home
    assert world.svc.ringing == {} and world.said == []


@pytest.mark.asyncio
async def test_an_expected_knock_is_accepted_once_then_rings_like_any_other(tmp_path):
    bound = []
    world = _world(tmp_path, bind_incoming=lambda key, device: bound.append((key, device)) or (lambda: None))
    world.svc.store.contacts[ANA_ROUTE] = True

    await world.svc.on_frame(_knock(world))

    assert bound == [(ANA_HEX, "ana-laptop")] and _answers(world) == ["accept"]
    assert world.said == [
        "ana-laptop knocked and was accepted, as expected. "
        "/connect allow ana-laptop <agent> lets it message one of your agents"
    ]
    assert world.svc.store.contacts == {ANA_ROUTE: False}  # still a contact, no longer expected
    second = _knock(world)
    await world.svc.on_frame(second)
    assert list(world.svc.ringing) == [second["id"]] and len(bound) == 1


@pytest.mark.asyncio
async def test_a_knock_again_replaces_the_one_still_ringing(tmp_path):
    world = _world(tmp_path)
    first, second = _knock(world), _knock(world, text="me again")

    await world.svc.on_frame(first)
    await world.svc.on_frame(second)

    assert list(world.svc.ringing) == [second["id"]]


@pytest.mark.asyncio
async def test_a_knock_nobody_answers_is_missed_kept_on_disk_and_said_once(tmp_path):
    world = _world(tmp_path)
    await world.svc.on_frame(_knock(world))
    world.said.clear()

    world.clock.now += 301
    await world.svc.tick()
    await world.svc.tick()

    assert world.svc.ringing == {}
    assert [row["device"] for row in world.svc.store.missed] == ["ana-laptop"]
    assert world.said == ["missed a knock from ana-laptop. /connect knocks to see it"]
    assert KnockStore(tmp_path / "knocks.json").missed == world.svc.store.missed
    assert os.stat(tmp_path / "knocks.json").st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_a_full_missed_box_turns_the_next_knock_away(tmp_path):
    world = _world(tmp_path, {"plugins.hub.knock_missed_limit": 1})
    await world.svc.on_frame(_knock(world))
    world.clock.now += 301
    await world.svc.tick()

    await world.svc.on_frame(_knock(world, BOB, device="bob-desk"))

    assert world.svc.ringing == {} and world.client.answers == []
    assert world.svc.snapshot()["missed_limit"] == 1


@pytest.mark.asyncio
async def test_the_main_pane_stays_quiet_while_the_knock_screen_is_open(tmp_path, monkeypatch):
    world = _world(tmp_path)
    world.svc.snapshot()  # the screen's poll

    await world.svc.on_frame(_knock(world))
    assert world.said == []

    monkeypatch.setattr(knocks, "SCREEN_OPEN_SECONDS", 0.0)  # the screen closed
    await world.svc.on_frame(_knock(world, BOB, device="bob-desk"))
    assert world.said == ["bob-desk is knocking. /connect knocks to answer (5:00)"]


# --- the human's answers -----------------------------------------------------


@pytest.mark.asyncio
async def test_reject_mutes_the_route_for_an_hour_and_is_never_answered(tmp_path):
    world = _world(tmp_path)
    frame = _knock(world)
    await world.svc.on_frame(frame)

    assert await _act(world, "reject", id=frame["id"]) == "rejected ana-laptop"
    assert world.client.answers == []
    assert world.svc.store.muted == {ANA_ROUTE: int(world.clock.now) + 3600}

    await world.svc.on_frame(_knock(world))  # within the hour
    assert world.svc.ringing == {} and world.client.answers == []

    world.clock.now += 3600
    await world.svc.tick()
    assert world.svc.store.muted == {}
    await world.svc.on_frame(_knock(world))
    assert len(world.svc.ringing) == 1


@pytest.mark.asyncio
async def test_block_delete_clear_and_the_route_lists(tmp_path):
    world = _world(tmp_path)
    ringing = _knock(world)
    await world.svc.on_frame(ringing)
    assert await _act(world, "block", id=ringing["id"]) == "blocked ana-laptop"
    assert world.svc.store.blocked == {ANA_ROUTE: "ana-laptop"} and world.client.answers == []
    assert await _act(world, "unblock", route=ANA_ROUTE) == f"unblocked {ANA_ROUTE}"
    assert await _act(world, "unblock", route=ANA_ROUTE) == f"connect: {ANA_ROUTE} is not blocked"

    for sender, device in ((ANA, "ana-laptop"), (BOB, "bob-desk")):
        await world.svc.on_frame(_knock(world, sender, device=device))
    world.clock.now += 301
    await world.svc.tick()
    newest, oldest = world.svc.store.missed
    assert await _act(world, "delete", id=oldest["id"]) == f"deleted the knock from {oldest['device']}"
    assert await _act(world, "delete", id=oldest["id"]) == "connect: that knock is gone"
    assert await _act(world, "block", id=newest["id"]) == f"blocked {newest['device']}"
    assert world.svc.store.missed == []

    assert await _act(world, "block_route", route=BOB_ROUTE) == f"blocked {BOB_ROUTE}"
    assert await _act(world, "expect", route=ANA_ROUTE) == (
        f"expecting a knock from {ANA_ROUTE}: its first knock is accepted"
    )
    assert await _act(world, "unexpect", route=ANA_ROUTE) == f"removed {ANA_ROUTE} from contacts"
    assert await _act(world, "expect", route="ANA") == "connect: that is not a contact route"
    assert await _act(world, "clear") == "missed knocks cleared"
    assert await _act(world, "accept", id="f" * 32) == "connect: that knock is no longer ringing"
    assert await _act(world, "launch") == "connect: unknown knock action"
    assert json.loads((tmp_path / "knocks.json").read_text())["blocked"] == {BOB_ROUTE: "bob-desk"}


@pytest.mark.asyncio
async def test_who_may_knock_for_a_while_lapses_back_to_everyone(tmp_path):
    world = _world(tmp_path)

    assert await _act(world, "mode", mode="contacts", minutes=30) == "only contacts may knock for 30m"
    assert world.svc.store.mode_until == int(world.clock.now) + 1800
    assert await _act(world, "mode", mode="sometimes", minutes=0) == (
        "connect: use /connect knocks everyone|contacts|nobody [for 30m|2h]"
    )
    world.clock.now += 1800
    await world.svc.tick()

    assert (world.svc.store.mode, world.svc.store.mode_until) == ("everyone", 0)
    assert await _act(world, "mode", mode="nobody", minutes=0) == "nobody may knock"
    assert KnockStore(tmp_path / "knocks.json").mode == "nobody"


# --- calls this device places ------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_rings_with_a_signed_ticket_and_a_sealed_body(tmp_path):
    world = _world(tmp_path)
    world.client.lookups[BOB_ROUTE] = ("found", BOB_HEX)

    line = await _act(world, "knock", domain="RELAY.example", route=BOB_ROUTE, text="hi bob")

    assert line == f"knocking on relay.example/c/{BOB_ROUTE}, rings for 5:00"
    ((to, knock_id, ticket, ciphertext),) = world.client.knocks
    assert to == BOB_HEX and ticket["id"] == knock_id
    knock_wire.check_ticket(ticket, ORIGIN, now=world.clock.now)
    body = open_sealed(BOB, world.me, ciphertext)
    assert (body["text"], body["device"], body["to"]) == ("hi bob", "laptop-kollab", BOB_HEX)
    assert world.svc.snapshot()["calls"] == [
        {"route": BOB_ROUTE, "target": f"relay.example/c/{BOB_ROUTE}", "state": "ringing", "left": 300}
    ]


@pytest.mark.asyncio
async def test_an_accept_binds_the_reply_path_and_ends_the_call(tmp_path):
    bound, declared = [], []

    async def links_changed():
        declared.append(True)

    world = _world(tmp_path, bind_outgoing=lambda key, agent: bound.append((key, agent)),
                   links_changed=links_changed)
    world.client.lookups[BOB_ROUTE] = ("found", BOB_HEX)
    await _act(world, "knock", domain="relay.example", route=BOB_ROUTE, text="hi")
    call_id = world.client.knocks[0][1]

    await world.svc.on_frame(_answer(world, call_id, sender=SigningKey.generate()))  # not Bob
    assert bound == [] and BOB_ROUTE in world.svc.calls

    await world.svc.on_frame(_answer(world, call_id))

    assert bound == [(BOB_HEX, "ops")] and declared == [True]
    assert world.said == ["bob-desk accepted. Its agents appear once it allows them."]
    assert world.svc.calls == {}


@pytest.mark.asyncio
async def test_a_call_redials_on_the_ladder_then_gives_up_after_the_window(tmp_path):
    world = _world(tmp_path)  # Bob is not online: every lookup is unavailable

    line = await _act(world, "knock", domain="relay.example", route=BOB_ROUTE, text="hi")

    assert line == (
        f"relay.example/c/{BOB_ROUTE} unavailable. redialing for 1h, next in 1:00. /connect knocks to stop"
    )
    assert world.svc.snapshot()["calls"][0]["state"] == "redialing"
    waits = []
    while BOB_ROUTE in world.svc.calls:
        call = world.svc.calls[BOB_ROUTE]
        waits.append(int(call.next_try - world.clock.now))
        world.clock.now = call.next_try
        await world.svc.tick()
        await _settle(world)
    assert waits == [60, 120, 240, 480, 900, 900, 900]
    assert len(world.client.looked_up) == 8
    assert world.said == [f"no answer from relay.example/c/{BOB_ROUTE} after 1h. try again later"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup, settings, line, kept",
    [
        (("busy", ""), {}, "{t} busy. redialing for 1h, next in 2:00. /connect knocks to stop", True),
        (("unavailable", ""), {"plugins.hub.knock_redial_minutes": 0}, "{t} unavailable. try again later", False),
        (("outdated", ""), {}, "connect: relay.example needs an update to carry knocks", False),
    ],
)
async def test_what_a_failed_first_try_says(tmp_path, lookup, settings, line, kept):
    world = _world(tmp_path, settings)
    world.client.lookups[BOB_ROUTE] = lookup

    said = await _act(world, "knock", domain="relay.example", route=BOB_ROUTE, text="hi")

    assert said == line.format(t=f"relay.example/c/{BOB_ROUTE}")
    assert (BOB_ROUTE in world.svc.calls) is kept


@pytest.mark.asyncio
async def test_a_call_that_rings_out_or_is_turned_away_redials(tmp_path):
    world = _world(tmp_path)
    world.client.lookups[BOB_ROUTE] = ("found", BOB_HEX)
    await _act(world, "knock", domain="relay.example", route=BOB_ROUTE, text="hi")

    world.clock.now += 305
    await world.svc.tick()
    assert world.said == [
        f"relay.example/c/{BOB_ROUTE} unavailable. redialing for 1h, next in 1:00. /connect knocks to stop"
    ]

    world.clock.now = world.svc.calls[BOB_ROUTE].next_try
    await world.svc.tick()
    await _settle(world)
    call_id = world.client.knocks[-1][1]
    await world.svc.on_frame(_answer(world, call_id, "unavailable"))
    assert world.svc.calls[BOB_ROUTE].tries == 2 and len(world.said) == 1  # later failures are quiet

    assert await _act(world, "stop", route=BOB_ROUTE) == f"stopped knocking on relay.example/c/{BOB_ROUTE}"
    assert world.svc.calls == {}


@pytest.mark.asyncio
async def test_a_knock_only_goes_where_it_can(tmp_path):
    world = _world(tmp_path)
    knock = {"domain": "relay.example", "route": BOB_ROUTE, "text": "hi"}

    assert await _act(world, "knock", **knock | {"domain": "elsewhere.example"}) == (
        "connect: this device knocks through relay.example; knock a route on relay.example"
    )
    assert await _act(world, "knock", **knock | {"route": contact_route_hex(world.me)}) == (
        "connect: that is this device's own contact route"
    )
    with pytest.raises(ValueError):
        await _act(world, "knock", **knock | {"route": "not-a-route"})
    world.client.is_online = False
    assert await _act(world, "knock", **knock) == (
        "connect: this device is offline; it knocks through its network's directory"
    )
    assert world.client.looked_up == [] and world.svc.calls == {}


# --- the pieces ----------------------------------------------------------------


def test_knock_text_is_one_to_2048_bytes_of_printable_text():
    assert validate_text("  hi there\nline two\tok  ") == "hi there\nline two\tok"
    for bad in ("", "   ", "a\x00b", "a\x1bb", "a\x7fb", "x" * 2049, 7):
        with pytest.raises(ValueError):
            validate_text(bad)
    assert validate_text("日" * 682) == "日" * 682  # 2046 bytes


def test_only_the_other_device_opens_what_is_sealed_to_it():
    sealed = seal(ANA, BOB_HEX, {"v": 1, "text": "hi"})

    assert open_sealed(BOB, ANA_HEX, sealed) == {"v": 1, "text": "hi"}
    for key, sender, ciphertext in (
        (SigningKey.generate(), ANA_HEX, sealed),
        (BOB, BOB_HEX, sealed),
        (BOB, ANA_HEX, sealed[:-4] + "AAAA"),
        (BOB, ANA_HEX, "not base64!"),
        (BOB, ANA_HEX, 42),
    ):
        with pytest.raises(ValueError):
            open_sealed(key, sender, ciphertext)
    with pytest.raises(ValueError, match="too large"):
        seal(ANA, BOB_HEX, {"text": "x" * 7000})


@pytest.mark.parametrize("content", [b"{not json", b'{"v": 1}', b'{"v": 2, "mode": "everyone"}'])
def test_a_knock_file_that_cannot_be_read_starts_over_empty(tmp_path, caplog, content):
    (tmp_path / "knocks.json").write_bytes(content)

    store = KnockStore(tmp_path / "knocks.json")

    assert (store.mode, store.blocked, store.missed) == ("everyone", {}, [])
    assert "could not be read" in caplog.text


def test_a_relay_state_from_the_old_knocks_loads_without_them(tmp_path):
    store = RelayStateStore(tmp_path, tmp_path / "state")
    data = json.loads(store.state_path.read_text())
    data |= {"knocks": {"a" * 32: {"key": "b" * 64}}, "knock_requests": []}
    store.state_path.write_text(json.dumps(data))

    again = RelayStateStore(tmp_path, tmp_path / "state")
    again.save()

    assert not {"knocks", "knock_requests"} & set(json.loads(again.state_path.read_text()))
