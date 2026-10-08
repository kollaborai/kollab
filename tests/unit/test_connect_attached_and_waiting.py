"""Bare /connect in an attached window, the join form's waiting states, used codes.

The default launch is a daemon plus an attached window, and only the daemon owns
the relay. These tests drive the whole chain (window plugin -> state service ->
RPC handlers -> daemon plugin) with fakes only at the two ends: the relay
commands/agent in the daemon, and the terminal renderer in the window.
"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.remote import RemoteStateService
from kollabor_rpc import RpcServer
from kollabor_rpc.models import RpcRequest
from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview import connect_altview
from plugins.altview.connect_altview import (
    ConnectAltView,
    ConnectOutcome,
    ConnectScreenAltView,
    ConnectScreenState,
    connect_screen_lines,
)
from plugins.hub import connect_guide
from plugins.hub import plugin as plugin_module
from plugins.hub.connect_guide import (
    JOIN_FAILURE_REASONS,
    join_failure_reason,
    post_join_line,
)
from plugins.hub.device_names import key_label
from plugins.hub.plugin import CONNECT_OWNED_ELSEWHERE, HubPlugin
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow
from plugins.hub.relay_state import RelayError

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ENROLLMENT_ID = "e" * 32
_OFFER = "0123456789abcdef0123456789abcdef"
_SYNTHETIC_CODE = "ABCD-EFGH"
_JOINED = "joined marco-home as home-server. trust: open"


class _FakeRenderer:
    def __init__(self, size=(100, 30)) -> None:
        self._size = size
        self.lines: list[tuple[int, int, str]] = []

    def get_terminal_size(self):
        return self._size

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append((x, y, _ANSI.sub("", text)))

    def text(self) -> str:
        return "\n".join(line for _, _, line in self.lines)


def _key(char: str) -> KeyPress:
    return KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)


def _named(name: str) -> KeyPress:
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


async def _settle(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def _snapshot(**overrides) -> ConnectSnapshot:
    base = dict(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="laptop-kollab",
        relay_online=True,
        requests=(
            JoinRequestRow(
                enrollment_id=_ENROLLMENT_ID,
                device="home-server",
                fingerprint="abcd…ef01",
                categories=("provider:openai:api_key",),
            ),
        ),
        local_agents=("koordinator",),
        remote_agents=("ops@home-server",),
        offline_devices=("ana-laptop",),
        knocks=1,
    )
    base.update(overrides)
    return ConnectSnapshot(**base)


class _Bus:
    def __init__(self, **services) -> None:
        self.services = services

    def get_service(self, name):
        return self.services[name]


# --------------------------------------------------------------------- #
# A rig: a daemon plugin behind real state handlers, an attached window
# --------------------------------------------------------------------- #


class _Rig:
    """Window plugin -> RemoteStateService -> RpcServer handlers -> daemon plugin."""

    def __init__(self, snapshot: ConnectSnapshot | None = None) -> None:
        self.snapshot = snapshot if snapshot is not None else _snapshot()
        self.wire_calls: list[tuple[str, dict]] = []
        self.decisions = AsyncMock()  # the daemon's issuer: decide_enrollment_request
        self.enroll_gate = asyncio.Event()
        self.enroll_result = {"status": "approved"}
        self.submitted_first = True

        daemon = HubPlugin.__new__(HubPlugin)
        daemon._cli_args = SimpleNamespace(attach=False)
        daemon._identity = SimpleNamespace(agent_id="agent-1")
        daemon._rpc_server = object()
        daemon._relay_commands = SimpleNamespace(
            connect_snapshot=AsyncMock(side_effect=lambda: self.snapshot),
            client=SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai")),
        )

        async def enroll_device(domain, code, on_submitted=None):
            if self.submitted_first and on_submitted is not None:
                on_submitted()
            await self.enroll_gate.wait()
            return self.enroll_result

        daemon._relay_agent = SimpleNamespace(
            decide_enrollment_request=self.decisions,
            enroll_device=enroll_device,
            network_name=lambda: "marco-home",
            device_name=lambda: "home-server",
            trust_level=lambda: "open",
        )
        self.daemon = daemon

        server = RpcServer()
        state = LocalStateService(None, None, event_bus=_Bus(hub_plugin=daemon))
        register_state_handlers(server, state)

        async def call(method, params=None, *, timeout=None):
            # JSON both ways, like the attach socket.
            params = json.loads(json.dumps(params or {}))
            reply = await server.handle_request(
                RpcRequest(request_id="r1", method=method, params=params)
            )
            self.wire_calls.append((method, params))
            assert reply.is_success, reply
            return json.loads(json.dumps(reply.result))

        self.remote = RemoteStateService(SimpleNamespace(call=call))
        self.stack = SimpleNamespace(push=AsyncMock())
        window = HubPlugin.__new__(HubPlugin)
        window._cli_args = SimpleNamespace(attach=True)
        window.event_bus = _Bus(
            state_service=self.remote, altview_stack_manager=self.stack
        )
        self.window = window

    def pushed(self):
        view, name = self.stack.push.await_args.args
        return view, name


# --------------------------------------------------------------------- #
# Bug A: the attached window opens the screen, data and decisions via the daemon
# --------------------------------------------------------------------- #


def test_snapshot_survives_the_json_round_trip_and_hides_the_request_id():
    snapshot = _snapshot()

    back = ConnectSnapshot.from_wire(json.loads(json.dumps(snapshot.to_wire())))

    assert back == snapshot
    assert back.requests[0].enrollment_id == _ENROLLMENT_ID
    assert _ENROLLMENT_ID not in repr(back)
    lines = connect_screen_lines(
        ConnectScreenState(snapshot=back, code_status="expired"), 100
    )
    assert _ENROLLMENT_ID not in "\n".join(lines)
    assert all(len(line) <= 100 for line in lines)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda wire: wire.update(extra="x"),
        lambda wire: wire.pop("requests"),
        lambda wire: wire.update(trust="everyone"),
        lambda wire: wire.update(relay_online="yes"),
        lambda wire: wire.update(knocks=True),
        lambda wire: wire.update(knocks=-1),
        lambda wire: wire.update(device="mac\x1b[31m"),
        lambda wire: wire.update(device="x" * 201),
        lambda wire: wire.update(local_agents="koordinator"),
        lambda wire: wire.update(remote_agents=["ok", 7]),
        lambda wire: wire.update(requests=[{"device": "x"}]),
        lambda wire: wire["requests"][0].update(enrollment_id="not an id"),
        lambda wire: wire["requests"][0].update(enrollment_id=""),
        lambda wire: wire["requests"][0].update(fingerprint="f" * 33),
        lambda wire: wire.update(requests=wire["requests"] * 65),
    ],
)
def test_snapshot_from_wire_rejects_anything_off_shape(mutate):
    wire = json.loads(json.dumps(_snapshot().to_wire()))
    mutate(wire)

    with pytest.raises(ValueError):
        ConnectSnapshot.from_wire(wire)


@pytest.mark.asyncio
async def test_attached_bare_connect_opens_the_screen_fed_by_the_daemon():
    rig = _Rig()

    assert await rig.window._handle_connect_command("") == ""

    view, name = rig.pushed()
    assert name == "connect-screen"
    assert type(view) is ConnectScreenAltView
    assert view.code_only is False
    assert view.domain == "kollabor.ai"
    loaded = await view._on_load()
    assert loaded == rig.snapshot
    assert [method for method, _ in rig.wire_calls].count(
        "state.hub_connect_snapshot"
    ) == 2


@pytest.mark.asyncio
async def test_attached_screen_renders_the_daemons_rows_without_the_request_id():
    rig = _Rig()
    await rig.window._handle_connect_command("")
    view, _ = rig.pushed()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view._refresh()
    await view.render_frame(0.0)

    text = renderer.text()

    assert "home-server wants to join" in text
    assert "device ID abcd…ef01" in text
    assert "ops@home-server" in text
    assert "ana-laptop (offline)" in text
    assert _ENROLLMENT_ID not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_attached_decision_goes_to_the_daemons_issuer_by_request_id():
    rig = _Rig()
    await rig.window._handle_connect_command("")
    view, _ = rig.pushed()
    row = rig.snapshot.requests[0]

    assert await view._on_decide(row, "accept") is None

    rig.decisions.assert_awaited_once_with(
        _ENROLLMENT_ID, decision="accept", source_agent="agent-1"
    )
    assert (
        "state.hub_connect_decide",
        {"enrollment_id": _ENROLLMENT_ID, "decision": "accept"},
    ) in rig.wire_calls


@pytest.mark.asyncio
async def test_attached_decision_carries_the_reason_it_failed():
    rig = _Rig()
    await rig.window._handle_connect_command("")
    view, _ = rig.pushed()
    row = rig.snapshot.requests[0]

    rig.decisions.side_effect = RelayError("device name 'x' is already on this network")
    assert await view._on_decide(row, "accept") == (
        "device name 'x' is already on this network"
    )
    rig.decisions.side_effect = RuntimeError("private detail")
    assert await view._on_decide(row, "reject") == "try again"


@pytest.mark.asyncio
async def test_attached_screen_accept_key_decides_on_the_daemon_and_notifies():
    rig = _Rig()
    await rig.window._handle_connect_command("")
    view, _ = rig.pushed()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view._refresh()

    await view.handle_input(_key("a"))
    await view.render_frame(0.0)

    rig.decisions.assert_awaited_once()
    text = renderer.text()
    assert "accepted home-server. it is now a trusted device on marco-home." in text
    assert _ENROLLMENT_ID not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_attached_bare_connect_on_no_network_opens_the_code_form():
    rig = _Rig(_snapshot(domain="", network="", requests=()))

    assert await rig.window._handle_connect_command("") == ""

    _view, name = rig.pushed()
    assert name == "connect"


@pytest.mark.asyncio
async def test_attached_bare_connect_falls_back_to_status_text_for_an_old_daemon():
    """A daemon that has no snapshot method can only offer its status text."""
    stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_connect=AsyncMock(return_value="network marco-home via kollabor.ai")
    )
    window = HubPlugin.__new__(HubPlugin)
    window._cli_args = SimpleNamespace(attach=True)
    window.event_bus = _Bus(state_service=state, altview_stack_manager=stack)

    assert await window._handle_connect_command("") == (
        "network marco-home via kollabor.ai"
    )

    state.hub_connect.assert_awaited_once_with("status")
    assert stack.push.await_count == 0


def _daemon_lost_the_lock(rig: _Rig) -> None:
    """The daemon's workspace relay is owned by another window (a --no-daemon one)."""
    rig.daemon._relay_commands = None
    rig.daemon._relay_agent.owner = SimpleNamespace(owner=lambda: {"pid": 4242})
    rig.daemon._relay_agent._state = lambda: SimpleNamespace(
        state=SimpleNamespace(origin="https://kollabor.ai")
    )
    rig.remote.hub_connect = AsyncMock(return_value="network marco-home via kollabor.ai")


async def _rendered(rig: _Rig, size=(100, 30)):
    view, name = rig.pushed()
    renderer = _FakeRenderer(size)
    await view.on_enter(renderer)
    await view.render_frame(0.0)
    text = renderer.text()
    await view.on_complete()
    return view, name, text


@pytest.mark.asyncio
async def test_attached_bare_connect_in_a_daemon_that_is_not_the_owner_opens_the_read_only_screen():
    rig = _Rig()
    _daemon_lost_the_lock(rig)

    assert await rig.window._handle_connect_command("") == ""

    view, name, text = await _rendered(rig)
    assert name == "connect-screen" and type(view) is ConnectScreenAltView
    assert "network      marco-home  via kollabor.ai   trust: open" in text
    assert "this device  home-server" in text
    assert CONNECT_OWNED_ELSEWHERE in text
    assert "join code" not in text and "requests" not in text and "online" not in text
    rig.remote.hub_connect.assert_not_awaited()  # not the status text
    methods = [method for method, _ in rig.wire_calls]
    assert methods == ["state.hub_connect_snapshot"]  # asked once, nothing else


@pytest.mark.asyncio
async def test_attached_connect_code_in_a_daemon_that_is_not_the_owner_offers_no_code():
    rig = _Rig()
    _daemon_lost_the_lock(rig)

    assert await rig.window._handle_connect_command("code") == ""

    view, name, text = await _rendered(rig)
    assert name == "connect-code" and view.code_only
    assert CONNECT_OWNED_ELSEWHERE in text
    assert "join code" not in text and "creating" not in text and "press c" not in text
    assert "state.hub_enrollment_offer" not in [m for m, _ in rig.wire_calls]


@pytest.mark.asyncio
async def test_attached_connect_code_in_a_daemon_that_owns_the_relay_is_unchanged():
    rig = _Rig()

    assert await rig.window._handle_connect_command("code") == ""

    view, name = rig.pushed()
    assert name == "connect-code" and view.code_only and view._note == ""
    assert view._on_create is not None
    # One snapshot to learn the daemon runs the relay; the code comes from it.
    assert [m for m, _ in rig.wire_calls] == ["state.hub_connect_snapshot"]


@pytest.mark.asyncio
async def test_attached_connect_code_does_not_wait_on_a_slow_daemon_snapshot(monkeypatch):
    rig = _Rig()
    monkeypatch.setattr(plugin_module, "_CONNECT_OWNER_CHECK_SECONDS", 0.01)

    async def slow():
        await asyncio.sleep(5)

    rig.remote.hub_connect_snapshot = slow

    assert await rig.window._handle_connect_command("code") == ""

    view, name = rig.pushed()
    assert name == "connect-code" and view._note == ""  # a busy daemon runs the relay


@pytest.mark.asyncio
async def test_attached_bare_connect_in_a_daemon_that_is_not_the_owner_and_has_no_network():
    rig = _Rig()
    _daemon_lost_the_lock(rig)
    rig.daemon._relay_agent._state = lambda: SimpleNamespace(
        state=SimpleNamespace(origin="")
    )

    assert await rig.window._handle_connect_command("") == ""

    _view, name, text = await _rendered(rig)
    assert name == "connect-screen"
    assert "network      none" in text and CONNECT_OWNED_ELSEWHERE in text


@pytest.mark.asyncio
async def test_a_daemon_with_no_relay_bridge_at_all_still_falls_back_to_status_text():
    rig = _Rig()
    rig.daemon._relay_commands = None  # not the owner, and no other owner either
    rig.remote.hub_connect = AsyncMock(return_value="network marco-home via kollabor.ai")

    assert await rig.window._handle_connect_command("") == (
        "network marco-home via kollabor.ai"
    )

    assert rig.stack.push.await_count == 0


def test_a_snapshot_from_a_daemon_that_predates_read_only_is_a_normal_one():
    wire = _snapshot().to_wire()
    del wire["read_only"]

    assert ConnectSnapshot.from_wire(wire).read_only is False
    assert ConnectSnapshot.from_wire({**wire, "read_only": True}).read_only is True
    with pytest.raises(ValueError):
        ConnectSnapshot.from_wire({**wire, "read_only": "yes"})


@pytest.mark.asyncio
async def test_a_hostile_snapshot_reply_is_dropped_and_the_screen_keeps_its_rows():
    rig = _Rig()
    await rig.window._handle_connect_command("")
    view, _ = rig.pushed()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view._refresh()
    good = view._snapshot

    async def hostile():
        return {"network": "x", "trust": "open"}

    rig.remote.hub_connect_snapshot = hostile
    await view._refresh()

    assert view._snapshot == good
    await view.on_complete()


@pytest.mark.asyncio
async def test_decide_handler_rejects_bad_ids_and_decisions_before_the_daemon():
    state = SimpleNamespace(hub_connect_decide=AsyncMock(return_value=""))
    server = RpcServer()
    register_state_handlers(server, state)

    async def call(**params):
        reply = await server.handle_request(
            RpcRequest(request_id="r", method="state.hub_connect_decide", params=params)
        )
        return reply.result

    assert await call(enrollment_id="bad id!", decision="accept") == {
        "error": "invalid connect decision"
    }
    assert await call(enrollment_id=_ENROLLMENT_ID, decision="delete") == {
        "error": "invalid connect decision"
    }
    assert await call(enrollment_id=_ENROLLMENT_ID, decision="accept", extra=1) == {
        "error": "invalid connect decision"
    }
    state.hub_connect_decide.assert_not_awaited()
    assert await call(enrollment_id=_ENROLLMENT_ID, decision="reject") == {"reason": ""}
    state.hub_connect_decide.assert_awaited_once_with(_ENROLLMENT_ID, "reject")


@pytest.mark.asyncio
async def test_remote_decision_reason_is_printable_and_bounded():
    reply = {"reason": "bad\x1b[31m " + "x" * 400}
    remote = RemoteStateService(SimpleNamespace(call=AsyncMock(return_value=reply)))

    reason = await remote.hub_connect_decide(_ENROLLMENT_ID, "accept")

    assert "\x1b" not in reason
    assert len(reason) == 200


# --------------------------------------------------------------------- #
# Bug A, second symptom: the joining window gets the full joined line
# --------------------------------------------------------------------- #


async def _typed_form(rig: _Rig, monkeypatch):
    monkeypatch.setattr(connect_altview, "_POLL_SECONDS", 0.0)
    assert await rig.window._handle_connect_command("") == ""
    view, name = rig.pushed()
    assert name == "connect"
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    for char in _SYNTHETIC_CODE.replace("-", ""):
        await view.handle_input(_key(char))
    return view, renderer


@pytest.mark.asyncio
async def test_attached_join_shows_waiting_at_once_then_the_full_joined_line(
    monkeypatch,
):
    rig = _Rig(_snapshot(domain="", network="", requests=()))
    view, renderer = await _typed_form(rig, monkeypatch)

    await view.handle_input(_named("Enter"))  # returns once the daemon says pending
    await view.render_frame(0.0)

    assert (
        "request sent to kollabor.ai; waiting for approval on another device"
        in renderer.text()
    )
    assert "could not submit" not in renderer.text()
    assert rig.enroll_gate.is_set() is False

    rig.enroll_gate.set()  # the other device accepts
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert _JOINED in renderer.text()
    assert "waiting for approval" not in renderer.text()
    methods = [method for method, _ in rig.wire_calls]
    assert methods[-1] == "state.hub_enroll_status"
    assert _SYNTHETIC_CODE not in renderer.text()
    await view.on_complete()


@pytest.mark.asyncio
async def test_attached_join_rejected_shows_the_rejected_line(monkeypatch):
    rig = _Rig(_snapshot(domain="", network="", requests=()))
    rig.enroll_result = {"status": "rejected"}
    view, renderer = await _typed_form(rig, monkeypatch)
    await view.handle_input(_named("Enter"))

    rig.enroll_gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert "join request rejected" in renderer.text()
    await view.on_complete()


@pytest.mark.asyncio
async def test_attached_join_failure_after_submit_is_not_called_a_send_failure(
    monkeypatch,
):
    rig = _Rig(_snapshot(domain="", network="", requests=()))
    rig.enroll_result = {"error": "unavailable"}
    view, renderer = await _typed_form(rig, monkeypatch)
    await view.handle_input(_named("Enter"))

    rig.enroll_gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert "did not complete" in renderer.text()
    assert "could not submit" not in renderer.text()
    # Why, from the daemon, on the form; and never the code.
    assert join_failure_reason("unavailable") in renderer.text()
    assert _SYNTHETIC_CODE not in renderer.text()
    await view.on_complete()


@pytest.mark.asyncio
async def test_join_that_fails_before_the_request_is_sent_says_it_was_not_sent(
    monkeypatch,
):
    rig = _Rig(_snapshot(domain="", network="", requests=()))
    rig.submitted_first = False  # never reaches the relay
    rig.enroll_result = {"error": "unavailable"}
    rig.enroll_gate.set()
    view, renderer = await _typed_form(rig, monkeypatch)

    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert "could not submit the join request" in renderer.text()
    assert "waiting for approval" not in renderer.text()
    await view.on_complete()


@pytest.mark.asyncio
async def test_attached_join_status_of_an_unknown_receipt_raises_not_fails():
    rig = _Rig()

    with pytest.raises(ValueError):
        await rig.remote.hub_enroll_status("0123456789abcdef")


@pytest.mark.asyncio
async def test_attached_join_form_shows_the_line_with_the_primarys_name_from_the_daemon(
    monkeypatch,
):
    """The daemon knows the issuer; the window's form must say so, not the stand-in."""
    rig = _Rig(_snapshot(domain="", network="", requests=()))
    monkeypatch.setattr("kollabor_config.managed_config.read_managed_config", lambda: None)
    rig.daemon._relay_agent._state = lambda: SimpleNamespace(
        state=SimpleNamespace(inviter=_ISSUER, peer_devices={_ISSUER: "laptop-kollab"})
    )
    said: list[str] = []
    rig.daemon.show_network_notice = said.append
    view, renderer = await _typed_form(rig, monkeypatch)

    await view.handle_input(_named("Enter"))
    rig.enroll_gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert _JOINED in renderer.text()
    assert "Settings arrive sealed from laptop-kollab" in renderer.text()
    assert "the device that issued the code" not in renderer.text()
    assert said == [post_join_line("laptop-kollab")]  # the daemon's main pane, once
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_join_that_finishes_before_pending_still_carries_the_joined_line():
    """An approval that is already decided when the daemon answers `hub_enroll`."""
    rig = _Rig()
    rig.enroll_gate.set()
    rig.submitted_first = False  # no submit signal: the daemon returns the final result

    result = await rig.remote.hub_enroll("kollabor.ai", _SYNTHETIC_CODE)

    assert result == {"status": "approved", "detail": _JOINED, "note": ""}


@pytest.mark.asyncio
async def test_status_handler_rejects_a_bad_receipt_and_passes_only_bounded_results():
    server = RpcServer()
    state = SimpleNamespace(
        hub_enroll_status=AsyncMock(
            return_value={"status": "approved", "detail": "joined x", "extra": "y"}
        )
    )
    register_state_handlers(server, state)

    async def call(**params):
        reply = await server.handle_request(
            RpcRequest(request_id="r", method="state.hub_enroll_status", params=params)
        )
        return reply.result

    assert await call(receipt_id="") == {"error": "invalid connect request status"}
    assert await call(receipt_id="r1", extra=1) == {
        "error": "invalid connect request status"
    }
    assert await call(receipt_id="r1") == {"status": "approved", "detail": "joined x"}
    state.hub_enroll_status.return_value = {"status": "approved", "detail": "a\x1bb"}
    assert await call(receipt_id="r1") == {"status": "approved"}
    state.hub_enroll_status.return_value = {"status": "bogus"}
    assert await call(receipt_id="r1") == {
        "error": "connect request status is unavailable"
    }


# --------------------------------------------------------------------- #
# Bug B: the form's own states (callbacks only, no plugin)
# --------------------------------------------------------------------- #


async def _form(on_submit, on_wait, monkeypatch, *, size=(100, 30)):
    monkeypatch.setattr(connect_altview, "_POLL_SECONDS", 0.0)
    view = ConnectAltView(on_submit=on_submit, on_wait=on_wait)
    renderer = _FakeRenderer(size)
    await view.on_enter(renderer)
    for char in _SYNTHETIC_CODE.replace("-", ""):
        await view.handle_input(_key(char))
    await view.handle_input(_named("Enter"))
    return view, renderer


@pytest.mark.asyncio
async def test_pending_shows_the_waiting_line_and_keeps_polling_until_an_answer(
    monkeypatch,
):
    answers = [ConnectOutcome.pending("r-1"), ConnectOutcome.pending("r-1")]
    polls = []

    async def on_submit(_submission):
        return ConnectOutcome.pending("r-1")

    async def on_wait(receipt, domain):
        polls.append((receipt, domain))
        if answers:
            return answers.pop(0)
        return ConnectOutcome.approved(_JOINED)

    view, renderer = await _form(on_submit, on_wait, monkeypatch)
    await view.render_frame(0.0)
    assert "request sent to kollabor.ai; waiting for approval on another device" in (
        renderer.text()
    )
    assert "esc close   /connect status" in renderer.text()

    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert polls[0] == ("r-1", "kollabor.ai")
    assert len(polls) == 3
    assert _JOINED in renderer.text()
    assert "enter/esc close" in renderer.text()
    assert "r-1" not in renderer.text()


@pytest.mark.asyncio
async def test_a_wait_that_cannot_be_polled_says_still_waiting_and_how_to_check(
    monkeypatch,
):
    calls = {"n": 0}

    async def on_submit(_submission):
        return ConnectOutcome.pending("r-2")

    async def on_wait(receipt, domain):
        calls["n"] += 1
        if calls["n"] < 3:
            raise asyncio.TimeoutError()
        return ConnectOutcome.approved(_JOINED)

    view, renderer = await _form(on_submit, on_wait, monkeypatch)
    for _ in range(3):
        await asyncio.sleep(0)
    await view.render_frame(0.0)

    text = renderer.text()
    assert (
        "still waiting for approval on another device; check with /connect status"
        in text
    )
    assert "could not submit" not in text
    assert "did not complete" not in text
    assert view.outcome == ConnectOutcome.pending("r-2")

    await asyncio.sleep(0.05)  # the daemon answers after all
    await view.render_frame(0.0)
    assert _JOINED in renderer.text()
    assert "still waiting" not in renderer.text()


@pytest.mark.asyncio
async def test_esc_closes_the_waiting_form_and_stops_polling(monkeypatch):
    polls = []

    async def on_submit(_submission):
        return ConnectOutcome.pending("r-3")

    async def on_wait(receipt, domain):
        polls.append(receipt)
        return ConnectOutcome.pending("r-3")

    view, _renderer = await _form(on_submit, on_wait, monkeypatch)
    await asyncio.sleep(0.01)
    assert polls

    assert await view.handle_input(_named("Escape")) is True
    await view.on_complete()
    seen = len(polls)
    await asyncio.sleep(0.02)

    assert len(polls) == seen
    assert view.background_tasks == []


@pytest.mark.asyncio
async def test_pending_without_a_wait_callback_is_still_the_last_word(monkeypatch):
    async def on_submit(_submission):
        return ConnectOutcome.pending("r-4")

    view, renderer = await _form(on_submit, None, monkeypatch)
    await view.render_frame(0.0)

    assert "waiting for approval on another device" in renderer.text()
    assert "enter/esc close" in renderer.text()
    assert view.background_tasks == []


@pytest.mark.asyncio
async def test_waiting_form_fits_eighty_columns_and_never_shows_the_code(monkeypatch):
    calls = {"n": 0}

    async def on_submit(_submission):
        return ConnectOutcome.pending("r-5")

    async def on_wait(receipt, domain):
        calls["n"] += 1
        raise RuntimeError("no")

    view, renderer = await _form(on_submit, on_wait, monkeypatch, size=(80, 24))
    await asyncio.sleep(0.01)
    await view.render_frame(0.0)

    for x, _y, line in renderer.lines:
        assert x + len(line) <= 80
    assert _SYNTHETIC_CODE not in renderer.text()
    assert _SYNTHETIC_CODE.replace("-", "") not in renderer.text()
    await view.on_complete()


# --------------------------------------------------------------------- #
# Bug B, daemon side: start returns after the submit; status never waits
# --------------------------------------------------------------------- #


def _daemon(enroll):
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="agent-1")
    plugin._rpc_server = object()
    plugin._relay_commands = SimpleNamespace(
        client=SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai"))
    )
    plugin._relay_agent = SimpleNamespace(
        enroll_device=enroll,
        network_name=lambda: "marco-home",
        device_name=lambda: "home-server",
        trust_level=lambda: "open",
    )
    return plugin


@pytest.mark.asyncio
async def test_start_returns_pending_once_the_request_is_submitted_and_status_follows():
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    plugin = _daemon(enroll)

    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)

    assert started["status"] == "pending"
    receipt = started["receipt_id"]
    assert await plugin._connect_enrollment_status(receipt) == {
        "status": "pending",
        "receipt_id": receipt,
    }
    gate.set()
    await _settle()
    assert await plugin._connect_enrollment_status(receipt) == {
        "status": "approved",
        "detail": _JOINED,
        "note": "",  # this device has not learned the issuer's name yet
    }
    # It can be read again by a window that missed the first answer.
    assert (await plugin._connect_enrollment_status(receipt))["status"] == "approved"


@pytest.mark.asyncio
async def test_start_returns_the_final_result_when_it_fails_before_any_submit():
    async def enroll(domain, code, on_submitted=None):
        return {"error": "unavailable"}

    plugin = _daemon(enroll)

    assert await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE) == {
        "status": "failed",
        "reason": join_failure_reason("unavailable"),
    }
    assert not plugin.__dict__["_connect_joins"]


@pytest.mark.asyncio
async def test_start_swallows_code_bearing_exceptions(caplog):
    async def enroll(domain, code, on_submitted=None):
        raise RuntimeError(f"relay refused {code}")

    plugin = _daemon(enroll)

    with caplog.at_level("WARNING"):
        result = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)

    assert result == {"status": "failed", "reason": join_failure_reason("internal")}
    assert _SYNTHETIC_CODE not in repr(result)
    # The one log line names the exception type, never its message.
    assert "RuntimeError" in caplog.text
    assert _SYNTHETIC_CODE not in caplog.text


def test_no_failure_reason_can_carry_a_code_a_key_or_an_id():
    for reason in [*JOIN_FAILURE_REASONS.values(), join_failure_reason("anything")]:
        assert reason.isprintable() and len(reason) <= 200
        assert not re.search(r"[0-9a-f]{32}", reason)
        assert not re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", reason)
    assert join_failure_reason(None) == join_failure_reason("not-a-code")


@pytest.mark.asyncio
async def test_a_failed_join_reads_back_as_failed_and_an_unknown_receipt_raises():
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"error": "unavailable"}

    plugin = _daemon(enroll)
    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    gate.set()
    await _settle()

    assert await plugin._connect_enrollment_status(started["receipt_id"]) == {
        "status": "failed",
        "reason": join_failure_reason("unavailable"),
    }
    with pytest.raises(ValueError):
        await plugin._connect_enrollment_status("0000000000000000")


@pytest.mark.asyncio
async def test_a_rejected_join_reads_back_as_rejected():
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "rejected"}

    plugin = _daemon(enroll)
    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    gate.set()
    await _settle()

    assert await plugin._connect_enrollment_status(started["receipt_id"]) == {
        "status": "rejected"
    }


@pytest.mark.asyncio
async def test_the_bridge_hands_on_submitted_to_the_enrollment_client(monkeypatch):
    """`on_submitted` reaches the enrollment client through the owner path."""
    from plugins.hub import relay_agent

    seen = {}

    async def fake_enroll(commands, domain, code, *, on_submitted=None):
        seen["on_submitted"] = on_submitted
        return {"status": "approved"}

    import plugins.hub.enrollment_client as enrollment_client

    monkeypatch.setattr(enrollment_client, "enroll_device", fake_enroll)
    bridge = relay_agent.RelayAgentBridge.__new__(relay_agent.RelayAgentBridge)
    bridge.commands = object()
    bridge._local_agent = lambda _agent_id: None
    bridge._require_human_network_context = lambda _message: None
    marker = object()

    result = await bridge._rpc_enroll_device(
        {"agent_id": "a", "domain": "kollabor.ai", "code": _SYNTHETIC_CODE},
        on_submitted=marker,
    )

    assert result == {"status": "approved"}
    assert seen["on_submitted"] is marker


@pytest.mark.asyncio
async def test_no_daemon_join_shows_waiting_then_the_full_joined_line(monkeypatch):
    """The same form, in the one process that owns the relay (`--no-daemon`)."""
    monkeypatch.setattr(connect_altview, "_POLL_SECONDS", 0.0)
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    plugin = _daemon(enroll)
    plugin._cli_args = SimpleNamespace(attach=False)
    stack = SimpleNamespace(push=AsyncMock())
    plugin.event_bus = _Bus(altview_stack_manager=stack)
    await plugin._open_connect_altview("kollabor.ai")
    view, _name = stack.push.await_args.args
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    for char in _SYNTHETIC_CODE.replace("-", ""):
        await view.handle_input(_key(char))

    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)
    assert "request sent to kollabor.ai; waiting for approval" in renderer.text()

    gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert _JOINED in renderer.text()
    await view.on_complete()


# --------------------------------------------------------------------- #
# The post-join line names the primary and reaches the main pane once
# --------------------------------------------------------------------- #

_ISSUER = "ab" * 32


def _joined_daemon(enroll, monkeypatch, names):
    """A daemon that joined by code: `names` is its peer_devices (the issuer's name once bound)."""
    monkeypatch.setattr("kollabor_config.managed_config.read_managed_config", lambda: None)
    plugin = _daemon(enroll)
    plugin._relay_agent._state = lambda: SimpleNamespace(
        state=SimpleNamespace(inviter=_ISSUER, peer_devices=names)
    )
    said: list[str] = []
    plugin.show_network_notice = said.append
    return plugin, said


@pytest.mark.asyncio
async def test_the_join_line_names_the_issuer_and_reaches_the_main_pane_once(monkeypatch):
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    plugin, said = _joined_daemon(enroll, monkeypatch, {_ISSUER: "laptop-kollab"})
    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    gate.set()
    await _settle()

    named = post_join_line("laptop-kollab")
    assert said == [named]  # nobody had the form open, and the main pane still got it
    for _ in range(3):  # a form that polls again changes nothing
        assert (await plugin._connect_enrollment_status(started["receipt_id"]))["note"] == named
    assert said == [named]


@pytest.mark.asyncio
async def test_the_join_line_waits_for_the_issuers_name_then_says_it_once(monkeypatch):
    monkeypatch.setattr(connect_guide, "JOIN_LINE_POLL", 0.0)
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    names = {_ISSUER: key_label(_ISSUER)}  # a key label is a stand-in, not a name
    plugin, said = _joined_daemon(enroll, monkeypatch, names)
    started = await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    gate.set()
    await _settle()

    assert said == []  # not the stand-in: the name may still come
    status = await plugin._connect_enrollment_status(started["receipt_id"])
    assert status["status"] == "approved" and status["note"] == ""
    names[_ISSUER] = "laptop-kollab"  # the first directory refresh or sealed config named it
    await _settle()
    assert said == [post_join_line("laptop-kollab")]


@pytest.mark.asyncio
async def test_the_join_line_falls_back_when_no_name_arrives_in_time(monkeypatch):
    monkeypatch.setattr(connect_guide, "JOIN_LINE_POLL", 0.0)
    monkeypatch.setattr(connect_guide, "JOIN_LINE_PATIENCE", 0.05)
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    plugin, said = _joined_daemon(enroll, monkeypatch, {})
    await plugin._run_connect_enrollment("kollabor.ai", _SYNTHETIC_CODE)
    gate.set()
    await asyncio.sleep(0.2)

    assert said == [post_join_line("")]


@pytest.mark.asyncio
async def test_the_form_shows_no_stand_in_while_the_issuers_name_is_still_coming(monkeypatch):
    monkeypatch.setattr(connect_altview, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(connect_guide, "JOIN_LINE_POLL", 0.0)
    gate = asyncio.Event()

    async def enroll(domain, code, on_submitted=None):
        on_submitted()
        await gate.wait()
        return {"status": "approved"}

    names: dict[str, str] = {}
    plugin, said = _joined_daemon(enroll, monkeypatch, names)
    plugin._cli_args = SimpleNamespace(attach=False)
    stack = SimpleNamespace(push=AsyncMock())
    plugin.event_bus = _Bus(altview_stack_manager=stack)
    await plugin._open_connect_altview("kollabor.ai")
    view, _name = stack.push.await_args.args
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    for char in _SYNTHETIC_CODE.replace("-", ""):
        await view.handle_input(_key(char))
    await view.handle_input(_named("Enter"))
    gate.set()
    await asyncio.sleep(0.05)
    await view.render_frame(0.0)

    assert _JOINED in renderer.text()
    assert "the device that issued the code" not in renderer.text()
    names[_ISSUER] = "laptop-kollab"
    await _settle()
    assert said == [post_join_line("laptop-kollab")]
    await view.on_complete()


def test_the_approved_result_carries_the_note_so_a_window_never_guesses_it():
    """A window reads the note from the daemon; before, the RPC dropped it and the form
    always said the stand-in, whatever the daemon knew."""
    from kollabor.state.interface import enrollment_result

    named = {
        "status": "approved",
        "detail": _JOINED,
        "note": post_join_line("laptop-kollab"),
    }
    assert enrollment_result(named) == named
    # "" is an answer too: the daemon is still waiting for the name
    assert enrollment_result({"status": "approved", "note": ""}) == {
        "status": "approved",
        "note": "",
    }
    for bad in ("x" * 201, "bad\x1b[31m", 5, None):
        assert enrollment_result({"status": "approved", "note": bad}) == {"status": "approved"}


# --------------------------------------------------------------------- #
# Bug C: a used code stops showing on the accepting device
# --------------------------------------------------------------------- #


async def _screen(snapshot, *, decide=None):
    async def on_create(_domain):
        return {
            "status": "offered",
            "offer_id": _OFFER,
            "expires_at": "9999999999",
            "code": "7QK4-M2XP",
        }

    async def on_load():
        return snapshot

    async def on_decide(row, decision):
        return await decide(row, decision) if decide else None

    view = ConnectScreenAltView(
        "kollabor.ai", on_create=on_create, on_load=on_load, on_decide=on_decide
    )
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _settle()
    return view, renderer


@pytest.mark.asyncio
async def test_accepting_a_request_retires_the_code_and_says_how_to_get_another():
    view, renderer = await _screen(_snapshot())
    await view.render_frame(0.0)
    assert "7QK4-M2XP" in renderer.text()

    await view.handle_input(_key("a"))
    await view.render_frame(0.0)

    text = renderer.text()
    assert "7QK4-M2XP" not in text
    assert "join code    used   press c for a new code" in text
    assert "c new code" in text
    assert view._private_code is None
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_used_code_stays_retired_across_refreshes_until_a_new_one_is_asked_for():
    view, renderer = await _screen(_snapshot())
    await view.handle_input(_key("a"))

    await view._refresh()
    await view.render_frame(0.0)
    assert "used   press c for a new code" in renderer.text()

    await view.handle_input(_key("c"))
    await _settle()
    await view.render_frame(0.0)

    assert "7QK4-M2XP" in renderer.text()
    assert "used" not in renderer.text().split("join code")[1].splitlines()[0]
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_refused_accept_keeps_the_code_on_screen():
    async def refuse(_row, _decision):
        return "try again"

    view, renderer = await _screen(_snapshot(), decide=refuse)

    await view.handle_input(_key("a"))
    await view.render_frame(0.0)

    assert "7QK4-M2XP" in renderer.text()
    assert "could not accept home-server: try again" in renderer.text()
    await view.on_complete()


def test_used_code_line_uses_the_expired_line_style_and_fits_eighty_columns():
    state = ConnectScreenState(
        snapshot=_snapshot(), code="", code_status="used", code_remaining=0
    )

    lines = connect_screen_lines(state, 80)

    assert " join code    used   press c for a new code" in lines
    assert all(len(line) <= 80 for line in lines)


# --------------------------------------------------------------------- #
# Story 5 in the default launch: a stranger knocks and the owner answers,
# both from an attached window, with the daemon holding the relay
# --------------------------------------------------------------------- #

_ROUTE = "8f3a2c1d9e4b5061"
_INTRO = "Ana from Acme. Can your ops agent review a nginx config?"
_KNOCK_ID = "b" * 32
_SENDER = "c" * 64
_NO_DAEMON_SUPPORT = "connect: the kollab on this computer needs an update for knocks"
_KNOCK_SCREEN = {
    "online": True, "domain": "agents.acme.com", "mode": "everyone", "mode_until": 0,
    "ringing": [{"id": _KNOCK_ID, "device": "ana-laptop", "fingerprint": "ab12…ef01",
                 "route": "0f1e2d3c4b5a6978", "text": _INTRO, "left": 290}],
    "calls": [], "missed": [], "missed_limit": 20, "blocked": [], "contacts": [],
}


def _relay_with_knocks(rig: _Rig, answers: dict | None = None) -> list:
    """The daemon's relay end: every knock action it was asked, by whom."""
    asked = []

    async def knocks(action, args, *, source_agent):
        asked.append((action, args, source_agent))
        if action == "list":
            return {"snapshot": _KNOCK_SCREEN}
        return {"text": (answers or {}).get(action, f"{action} done")}

    rig.daemon._relay_agent.knocks = knocks
    rig.daemon._relay_agent.command = AsyncMock(return_value="allowed koordinator for ana-laptop")
    return asked


def _daemon_without_knock_rpcs(rig: _Rig) -> None:
    from kollabor_rpc import RpcMethodNotFound

    real_call = rig.remote._rpc.call

    async def old_daemon(method, params=None, *, timeout=None):
        if method == "state.hub_knocks":
            raise RpcMethodNotFound("method not found")
        return await real_call(method, params, timeout=timeout)

    rig.remote._rpc = SimpleNamespace(call=old_daemon)


async def _open_knocks(rig: _Rig):
    assert await rig.window._handle_connect_command("knocks") == ""
    view, name = rig.pushed()
    assert name == "knocks"
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    for _ in range(50):  # the screen's first load runs on its poll task
        if view._snapshot is not None or view._error:
            break
        await asyncio.sleep(0.01)
    await view.render_frame(0.0)
    return view, renderer


@pytest.mark.asyncio
async def test_attached_knock_is_placed_by_the_daemon_and_prints_its_answer():
    rig = _Rig()
    ringing = f"knocking on kollabor.ai/c/{_ROUTE}, rings for 5:00"
    asked = _relay_with_knocks(rig, {"knock": ringing})

    result = await rig.window._handle_connect_command(f'knock kollabor.ai/c/{_ROUTE} "{_INTRO}"')

    assert result == ringing
    knock = {"domain": "kollabor.ai", "route": _ROUTE, "text": _INTRO}
    assert asked == [("knock", knock, "agent-1")]
    assert rig.wire_calls == [("state.hub_knocks", {"action": "knock", "args": knock})]


@pytest.mark.asyncio
async def test_attached_knock_prints_the_daemons_refusal():
    rig = _Rig()
    refusal = "connect: this device knocks through agents.acme.com; knock a route on agents.acme.com"
    _relay_with_knocks(rig, {"knock": refusal})

    result = await rig.window._handle_connect_command(f'knock kollabor.ai/c/{_ROUTE} "{_INTRO}"')

    assert result == refusal


@pytest.mark.asyncio
async def test_attached_knock_usage_errors_never_reach_the_daemon():
    rig = _Rig()
    _relay_with_knocks(rig)

    assert await rig.window._handle_connect_command("knock") == 'connect: use /connect knock <route> "text"'
    assert await rig.window._handle_connect_command("knocks sometimes") == (
        "connect: use /connect knocks [everyone|contacts|nobody] [for 30m|2h|1d]"
    )
    assert await rig.window._handle_connect_command("block ana-laptop") == "connect: use /connect block <route>"
    assert rig.wire_calls == []


@pytest.mark.asyncio
async def test_attached_knock_and_knocks_keep_a_plain_refusal_on_an_old_daemon():
    rig = _Rig()
    _daemon_without_knock_rpcs(rig)

    knock = await rig.window._handle_connect_command(f'knock kollabor.ai/c/{_ROUTE} "{_INTRO}"')
    knocks = await rig.window._handle_connect_command("knocks")

    assert knock == _NO_DAEMON_SUPPORT
    assert knocks == _NO_DAEMON_SUPPORT
    rig.stack.push.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_knocks_opens_the_screen_with_the_daemons_knocks():
    rig = _Rig()
    asked = _relay_with_knocks(rig)

    view, renderer = await _open_knocks(rig)

    text = renderer.text()
    assert "ana-laptop" in text and "device ID ab12…ef01" in text
    assert "Ana from Acme." in text
    assert _SENDER not in text and _KNOCK_ID not in text and "relay:" not in text
    assert asked[0] == ("list", {}, "agent-1")
    assert rig.wire_calls[0] == ("state.hub_knocks", {"action": "list", "args": {}})
    await view.on_complete()


@pytest.mark.asyncio
@pytest.mark.parametrize("key, action", [("a", "accept"), ("r", "reject"), ("b", "block")])
async def test_attached_knock_screen_answers_on_the_daemon(key, action):
    rig = _Rig()
    said = {
        "accept": "accepted ana-laptop. /connect allow ana-laptop <agent> lets it message one of your agents",
        "reject": "rejected ana-laptop",
        "block": "blocked ana-laptop",
    }
    asked = _relay_with_knocks(rig, said)
    view, renderer = await _open_knocks(rig)

    await view.handle_input(_key(key))
    await view.render_frame(0.0)

    assert (action, {"id": _KNOCK_ID}, "agent-1") in asked
    assert said[action] in " ".join(renderer.text().split())
    await view.on_complete()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command, action, args",
    [
        ("knocks contacts for 2h", "mode", {"mode": "contacts", "minutes": 120}),
        ("knocks nobody", "mode", {"mode": "nobody", "minutes": 0}),
        (f"expect kollabor.ai/c/{_ROUTE}", "expect", {"route": _ROUTE}),
        (f"block {_ROUTE.upper()}", "block_route", {"route": _ROUTE}),
        (f"unblock {_ROUTE}", "unblock", {"route": _ROUTE}),
    ],
)
async def test_attached_knock_settings_go_to_the_daemon(command, action, args):
    rig = _Rig()
    asked = _relay_with_knocks(rig)

    assert await rig.window._handle_connect_command(command) == f"{action} done"
    assert asked == [(action, args, "agent-1")]


@pytest.mark.asyncio
async def test_attached_allow_after_an_accept_goes_to_the_daemon_by_name():
    rig = _Rig()
    _relay_with_knocks(rig)

    result = await rig.window._handle_connect_command("allow ana-laptop koordinator")

    assert result == "allowed koordinator for ana-laptop"
    assert rig.wire_calls == [
        ("state.hub_connect", {"command": "allow ana-laptop koordinator"})
    ]


@pytest.mark.asyncio
async def test_the_knock_handler_rejects_bad_shapes_before_the_daemon():
    state = SimpleNamespace(hub_knocks=AsyncMock(return_value={"text": "knocking"}))
    server = RpcServer()
    register_state_handlers(server, state)

    async def call(**params):
        reply = await server.handle_request(
            RpcRequest(request_id="r", method="state.hub_knocks", params=params)
        )
        return reply.result

    knock = {"action": "knock", "args": {"domain": "kollabor.ai", "route": _ROUTE, "text": _INTRO}}
    for bad in (
        {**knock, "extra": 1},
        {"action": "knock"},
        {**knock, "action": ["knock"]},
        {**knock, "action": ""},
        {**knock, "action": "k" * 33},
        {**knock, "args": "text"},
        {**knock, "args": {"text": "x" * 9000}},
    ):
        assert await call(**bad) == {"error": "invalid knock request"}
    state.hub_knocks.assert_not_awaited()

    assert await call(**knock) == {"text": "knocking"}
    state.hub_knocks.assert_awaited_once_with("knock", knock["args"])
    for answer in ({"text": "x", "extra": 1}, ["text"], RuntimeError("relay down")):
        state.hub_knocks = AsyncMock(side_effect=answer) if isinstance(answer, Exception) else AsyncMock(
            return_value=answer
        )
        server = RpcServer()
        register_state_handlers(server, state)
        assert await call(**knock) == {"error": "knocks are unavailable"}


@pytest.mark.asyncio
async def test_remote_knock_answers_are_printable_bounded_and_errors_raise():
    reply = {"text": "sent\x1b[31m " + "x" * 400}
    rpc = AsyncMock(return_value=reply)
    remote = RemoteStateService(SimpleNamespace(call=rpc))

    answer = await remote.hub_knocks("knock", {"domain": "kollabor.ai", "route": _ROUTE, "text": _INTRO})

    assert "\x1b" not in answer["text"] and len(answer["text"]) == 200
    assert rpc.await_args.kwargs["timeout"] >= 45  # a knock waits on the directory
    rpc.return_value = {"snapshot": _KNOCK_SCREEN}
    assert await remote.hub_knocks("list", {}) == {"snapshot": _KNOCK_SCREEN}
    for bad in ({"error": "knocks are unavailable"}, {"text": 7}, {"snapshot": []}, "text"):
        rpc.return_value = bad
        with pytest.raises(ValueError):
            await remote.hub_knocks("list", {})
