"""A second window in a workspace opens the Connect screen too (issue #121, Story 1).

One window per workspace owns the relay. Another `--no-daemon` window reads the
network from the shared state file, but a join code, the requests and the
decisions all live in the owner, so its screen says so in one line and offers
none of them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from plugins.altview.connect_altview import (
    ConnectScreenAltView,
    ConnectScreenState,
    connect_screen_lines,
)
from plugins.hub.device_names import display_width
from plugins.hub.plugin import CONNECT_OWNED_ELSEWHERE, HubPlugin
from plugins.hub.relay_owner import WorkspaceRelayOwner
from tests.unit.test_connect_screen import _FakeRenderer, _key, _named, _snapshot

NOTE = " " + CONNECT_OWNED_ELSEWHERE
STATUS = "network kollabor.ai  trust: open\nthis device mac-kollab"


def _read_only(snapshot, **overrides) -> ConnectScreenState:
    return ConnectScreenState(
        snapshot=snapshot, note=CONNECT_OWNED_ELSEWHERE, **overrides
    )


# --------------------------------------------------------------------- #
# The pure line builder
# --------------------------------------------------------------------- #


def test_the_screen_of_a_window_that_cannot_act_says_so_in_one_line():
    lines = connect_screen_lines(_read_only(_snapshot()), 80)

    assert lines == [
        " Connect",
        " network      marco-home  via kollabor.ai   trust: open",
        " this device  mac-kollab",
        "",
        NOTE,
        "",
        " esc close",
    ]


def test_a_window_on_no_network_says_none_and_still_names_its_device():
    lines = connect_screen_lines(_read_only(_snapshot(network="", domain="")), 80)

    assert " network      none" in lines
    assert " this device  mac-kollab" in lines
    assert NOTE in lines


def test_the_code_screen_of_a_window_that_cannot_act_has_no_code_row():
    lines = connect_screen_lines(_read_only(None, code_only=True), 80)

    assert lines == [" Connect code", NOTE, "", " esc close"]


@pytest.mark.parametrize("width", [60, 80, 120])
def test_nothing_that_would_fail_is_offered_and_every_line_fits(width):
    lines = connect_screen_lines(_read_only(_snapshot()), width)

    assert all(display_width(line) <= width for line in lines)
    text = "\n".join(lines).lower()
    for word in (
        "join code",
        "requests",
        "online",
        "knocks",
        "new code",
        "accept",
        "reject",
    ):
        assert word not in text, word
    assert ("esc close" in text) and ("another window" in text)


# --------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_read_only_view_creates_no_code_and_polls_and_decides_nothing():
    on_create, on_load, on_decide = AsyncMock(), AsyncMock(), AsyncMock()
    view = ConnectScreenAltView(
        "kollabor.ai",
        on_create=on_create,
        on_load=on_load,
        on_decide=on_decide,
        snapshot=_snapshot(),
        note=CONNECT_OWNED_ELSEWHERE,
    )
    renderer = _FakeRenderer((80, 30))
    await view.on_enter(renderer)
    for _ in range(12):
        await asyncio.sleep(0)

    await view.render_frame(0.0)
    for char in "car":  # c would make a code, a and r would decide a request
        assert await view.handle_input(_key(char)) is False
    assert await view.handle_input(_named("ArrowDown")) is False

    on_create.assert_not_called()
    on_load.assert_not_called()
    on_decide.assert_not_called()
    assert view.background_tasks == []
    text = renderer.text()
    assert "network      marco-home  via kollabor.ai   trust: open" in text
    assert CONNECT_OWNED_ELSEWHERE in text
    assert await view.handle_input(_named("Escape")) is True
    assert await view.handle_input(_named("Enter")) is True
    await view.on_complete()


# --------------------------------------------------------------------- #
# /connect in the window
# --------------------------------------------------------------------- #


def _window(
    *, origin="https://kollabor.ai", owned_elsewhere=True, attached=False, owns=False
):
    """A window's hub plugin with a fake stack manager to catch what it opens."""
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=attached)
    plugin._relay_commands = (
        SimpleNamespace(client=SimpleNamespace(state=SimpleNamespace(origin=origin)))
        if owns
        else None
    )
    plugin._relay_agent = SimpleNamespace(
        _state=lambda: SimpleNamespace(state=SimpleNamespace(origin=origin)),
        owner=SimpleNamespace(owner=lambda: {"pid": 4242} if owned_elsewhere else None),
        device_name=lambda: "mac-kollab",
        trust_level=lambda: "agents",
    )
    stack = SimpleNamespace(push=AsyncMock())
    plugin.event_bus = SimpleNamespace(get_service=lambda name: stack)
    plugin._run_connect_command = AsyncMock(return_value=STATUS)
    plugin._open_connect_altview = AsyncMock(return_value="")
    return plugin, stack


async def _rendered(stack) -> tuple[ConnectScreenAltView, str, str]:
    view, name = stack.push.await_args.args
    renderer = _FakeRenderer((80, 30))
    await view.on_enter(renderer)
    await view.render_frame(0.0)
    text = renderer.text()
    await view.on_complete()
    return view, name, text


@pytest.mark.asyncio
async def test_bare_connect_in_a_second_window_opens_the_read_only_screen():
    plugin, stack = _window()

    assert await plugin._handle_connect_command("") == ""

    view, name, text = await _rendered(stack)
    assert name == "connect-screen" and type(view) is ConnectScreenAltView
    assert "network      kollabor.ai   trust: agents" in text
    assert "this device  mac-kollab" in text
    assert CONNECT_OWNED_ELSEWHERE in text
    assert "join code" not in text and "requests" not in text and "online" not in text
    plugin._open_connect_altview.assert_not_awaited()  # no code form either


@pytest.mark.asyncio
async def test_bare_connect_in_a_second_window_on_no_network_says_none_not_the_code_form():
    plugin, stack = _window(origin="")
    plugin._run_connect_command.return_value = "network none\ncontact route none"

    assert await plugin._handle_connect_command("") == ""

    _view, name, text = await _rendered(stack)
    assert name == "connect-screen"
    assert "network      none" in text
    assert CONNECT_OWNED_ELSEWHERE in text
    plugin._open_connect_altview.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", ["code", "code example.org"])
async def test_connect_code_in_a_second_window_offers_no_code(args):
    plugin, stack = _window()

    assert await plugin._handle_connect_command(args) == ""

    view, name, text = await _rendered(stack)
    assert name == "connect-code" and view.code_only
    assert CONNECT_OWNED_ELSEWHERE in text
    assert "join code" not in text and "creating" not in text and "press c" not in text


@pytest.mark.asyncio
async def test_the_window_that_owns_the_relay_still_opens_the_full_screen():
    plugin, stack = _window(owned_elsewhere=False, owns=True)

    assert await plugin._handle_connect_command("") == ""

    view, name = stack.push.await_args.args
    assert name == "connect-screen"
    assert view._note == "" and view._on_create and view._on_load and view._on_decide
    plugin._run_connect_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_status_with_no_owner_anywhere_is_still_shown_as_text():
    plugin, stack = _window(owned_elsewhere=False)
    error = "beacon: private connection state is unavailable or invalid"
    plugin._run_connect_command.return_value = error

    assert await plugin._handle_connect_command("") == error

    stack.push.assert_not_awaited()


def test_an_attached_window_never_counts_as_a_second_window():
    plugin, _stack = _window(attached=True)

    assert plugin._relay_owned_elsewhere() is False


def test_a_lock_probe_that_fails_means_not_a_second_window():
    plugin, _stack = _window()
    plugin._relay_agent.owner.owner = Mock(side_effect=OSError("lock"))

    assert plugin._relay_owned_elsewhere() is False


def test_owned_elsewhere_follows_the_workspaces_real_lock(tmp_path):
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    owner = WorkspaceRelayOwner(workspace, state)
    assert owner.acquire("/tmp/kollab-test.sock", "agent-1")
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._relay_commands = None
    plugin._relay_agent = SimpleNamespace(owner=WorkspaceRelayOwner(workspace, state))

    assert plugin._relay_owned_elsewhere() is True
    owner.release()
    assert plugin._relay_owned_elsewhere() is False
