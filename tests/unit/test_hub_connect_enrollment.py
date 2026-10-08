from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor.commands.registry import SlashCommandRegistry
from plugins.altview.connect_altview import (
    ConnectScreenAltView,
    ConnectStatus,
    ConnectSubmission,
    PrivateCode,
)
from plugins.altview.knocks_altview import KnockScreenAltView
from plugins.hub.plugin import CODE_IN_COMMAND, HubPlugin


class _EventBus:
    def __init__(self, **services):
        self.services = services

    def get_service(self, name):
        return self.services[name]


@pytest.mark.asyncio
async def test_connect_domain_attaches_to_public_network_without_opening_enrollment():
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(hub_connect=AsyncMock(return_value="beacon: online"))
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    result = await plugin._handle_connect_command("kollabor.ai")

    assert result == "beacon: online"
    state.hub_connect.assert_awaited_once_with("kollabor.ai")
    assert view_stack.push.await_count == 0


@pytest.mark.asyncio
async def test_connect_domain_runs_public_discovery_in_owner_session():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._run_connect_command = AsyncMock(return_value="beacon: online")

    result = await plugin._handle_connect_command("kollabor.ai")

    assert result == "beacon: online"
    plugin._run_connect_command.assert_awaited_once_with("kollabor.ai")


@pytest.mark.asyncio
async def test_connect_enroll_is_removed_use_bare_connect():
    """`enroll` is a remnant of the first build (constitution section 6);
    bare /connect now opens the same private form, always against
    kollabor.ai -- there is no longer a way to point the entry form at
    another domain from the command surface."""
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)

    assert await plugin._handle_connect_command("enroll example.test") == (
        "connect: use /connect"
    )


@pytest.mark.asyncio
async def test_connect_bare_opens_private_form_and_uses_typed_attach_rpc():
    code = "ABCD-EFGH"
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_enroll=AsyncMock(
            return_value={"status": "pending", "receipt_id": "0123456789abcdef"}
        ),
        hub_connect=AsyncMock(return_value="network none"),
    )
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    result = await plugin._handle_connect_command("")

    assert result == ""
    view, view_name = view_stack.push.await_args.args
    assert view_stack.push.await_args.kwargs == {"reuse": False}
    assert view_name == "connect"
    assert view.domain == "kollabor.ai"
    submission = ConnectSubmission("kollabor.ai", PrivateCode(code))
    try:
        outcome = await view._on_submit(submission)
    finally:
        submission.code.clear()

    assert outcome.status is ConnectStatus.PENDING
    assert outcome.receipt_id == "0123456789abcdef"
    state.hub_enroll.assert_awaited_once_with("kollabor.ai", code)
    state.hub_connect.assert_awaited_once_with("status")


@pytest.mark.asyncio
async def test_connect_subcommand_remains_on_existing_rpc_and_code_is_not_command_input():
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_enroll=AsyncMock(),
        hub_connect=AsyncMock(return_value="beacon: status"),
    )
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    status = await plugin._handle_connect_command("status")
    rejected = await plugin._handle_connect_command("example.test private-code")

    assert status == "beacon: status"
    state.hub_connect.assert_awaited_once_with("status")
    assert rejected == CODE_IN_COMMAND
    state.hub_enroll.assert_not_awaited()
    assert view_stack.push.await_count == 0


@pytest.mark.asyncio
async def test_connect_offer_is_removed_use_connect_code():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)

    assert await plugin._handle_connect_command("offer example.test") == (
        "connect: use /connect code"
    )


@pytest.mark.asyncio
async def test_connect_code_opens_private_view_and_uses_typed_attach_rpc(
    monkeypatch,
):
    offer_id = "0123456789abcdef0123456789abcdef"
    code = "ABCD-EFGH"
    result = {
        "status": "offered",
        "offer_id": offer_id,
        "expires_at": "1790530000",
        "code": code,
    }
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_enrollment_offer=AsyncMock(return_value=result), hub_connect=AsyncMock()
    )
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    opened = await plugin._handle_connect_command("code example.test")

    assert opened == ""
    view, view_name = view_stack.push.await_args.args
    assert view_stack.push.await_args.kwargs == {"reuse": False}
    assert view_name == "connect-code"
    assert view.code_only is True
    assert view.domain == "example.test"
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1_000)
    await view._create()
    assert view._private_code.reveal() == code
    state.hub_enrollment_offer.assert_awaited_once_with("example.test")
    state.hub_connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_knock_screen_always_opens_a_fresh_session():
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(altview_stack_manager=view_stack)
    plugin._knocks = AsyncMock(return_value={"snapshot": {"ringing": []}})

    result = await plugin._open_knocks_screen()

    assert result == ""
    view, view_name = view_stack.push.await_args.args
    assert isinstance(view, KnockScreenAltView)
    assert view_name == "knocks"
    assert view_stack.push.await_args.kwargs == {"reuse": False}


@pytest.mark.asyncio
async def test_the_knock_screen_says_why_instead_of_opening_empty():
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(altview_stack_manager=view_stack)
    old_daemon = "connect: the attached daemon needs an update for knocks"

    for answer, said in (
        ({"text": old_daemon}, old_daemon),
        ({"text": "something else"}, "connect: knocks are unavailable right now"),
    ):
        plugin._knocks = AsyncMock(return_value=answer)
        assert await plugin._open_knocks_screen() == said
    view_stack.push.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_code_is_rejected_from_command_text_and_code_domain_is_bounded():
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(hub_enrollment_offer=AsyncMock(), hub_connect=AsyncMock())
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    pasted = await plugin._handle_connect_command(
        "ABCD-EFGH"
    )
    pasted_to_enroll = await plugin._handle_connect_command(
        "enroll ABCD-EFGH"
    )
    pasted_to_code = await plugin._handle_connect_command(
        "code ABCD-EFGH"
    )
    malformed_code = await plugin._handle_connect_command("code example.test extra")

    assert pasted == CODE_IN_COMMAND
    assert pasted_to_enroll == CODE_IN_COMMAND
    assert pasted_to_code == CODE_IN_COMMAND
    assert malformed_code == "connect: use /connect code [domain]"
    state.hub_enrollment_offer.assert_not_awaited()
    assert view_stack.push.await_count == 0


@pytest.mark.asyncio
async def test_altview_discovery_then_hub_registration_keeps_connect_with_hub():
    registry = SlashCommandRegistry()
    integrator = AltViewCommandIntegrator(
        command_registry=registry,
        event_bus=None,
        terminal_renderer=None,
    )

    assert integrator.discover_and_register_plugins(
        Path(__file__).resolve().parents[2] / "plugins"
    ) > 0
    assert registry.get_command("connect") is None
    assert registry.get_command("connect-screen") is None
    assert registry.get_command("connect-code") is None
    assert registry.get_command("knocks") is None
    assert "connect" in integrator._plugin_classes
    assert ConnectScreenAltView().metadata.category == "internal"
    assert KnockScreenAltView().metadata.category == "internal"

    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_connect=AsyncMock(return_value="beacon: status"),
        hub_enroll=AsyncMock(),
    )
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.name = "hub"
    plugin.command_registry = registry
    plugin.event_bus = _EventBus(
        altview_stack_manager=view_stack,
        state_service=state,
    )
    plugin._cli_args = SimpleNamespace(attach=True)

    plugin._register_commands()
    command = registry.get_command("connect")

    assert command is not None
    assert command.plugin_name == "hub"
    assert await command.handler("status") == "beacon: status"
    state.hub_connect.assert_awaited_once_with("status")
    assert view_stack.push.await_count == 0

    state.hub_connect.return_value = "network none"
    assert await command.handler("") == ""
    assert view_stack.push.await_count == 1
    _view, view_name = view_stack.push.await_args.args
    assert view_name == "connect"


def _connected_plugin(*, attach=False, origin="https://kollabor.ai", **services):
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(**services)
    plugin._cli_args = SimpleNamespace(attach=attach)
    plugin._relay_commands = SimpleNamespace(
        client=SimpleNamespace(state=SimpleNamespace(origin=origin)),
        connect_snapshot=AsyncMock(return_value="snapshot"),
    )
    plugin._relay_agent = SimpleNamespace(decide_enrollment_request=AsyncMock())
    plugin._identity = SimpleNamespace(agent_id="agent-1")
    return plugin


@pytest.mark.asyncio
async def test_bare_connect_on_a_network_opens_the_screen_not_the_form():
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = _connected_plugin(altview_stack_manager=view_stack)

    assert await plugin._handle_connect_command("") == ""

    view, view_name = view_stack.push.await_args.args
    assert view_name == "connect-screen"
    assert view_stack.push.await_args.kwargs == {"reuse": False}
    assert type(view).__name__ == "ConnectScreenAltView"
    assert view.code_only is False
    assert view.domain == "kollabor.ai"
    assert await view._on_load() == "snapshot"


@pytest.mark.asyncio
async def test_bare_connect_off_a_network_still_opens_the_code_form():
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = _connected_plugin(origin="", altview_stack_manager=view_stack)

    assert await plugin._handle_connect_command("") == ""

    _view, view_name = view_stack.push.await_args.args
    assert view_name == "connect"


@pytest.mark.asyncio
async def test_bare_connect_attached_to_a_daemon_without_a_snapshot_prints_status_text():
    """Only a daemon too old to send a Connect snapshot falls back to text; a
    current one feeds the screen (tests/unit/test_connect_attached_and_waiting.py)."""
    view_stack = SimpleNamespace(push=AsyncMock())
    state = SimpleNamespace(
        hub_connect=AsyncMock(return_value="network marco-home via kollabor.ai")
    )
    plugin = _connected_plugin(
        attach=True, altview_stack_manager=view_stack, state_service=state
    )

    assert await plugin._handle_connect_command("") == (
        "network marco-home via kollabor.ai"
    )

    state.hub_connect.assert_awaited_once_with("status")
    assert view_stack.push.await_count == 0


@pytest.mark.asyncio
async def test_connect_code_on_a_network_defaults_to_that_network_and_shows_only_the_code():
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = _connected_plugin(
        origin="https://example.org", altview_stack_manager=view_stack
    )

    assert await plugin._handle_connect_command("code") == ""

    view, view_name = view_stack.push.await_args.args
    assert view_name == "connect-code"
    assert view.code_only is True
    assert view.domain == "example.org"
    assert view._on_load is None and view._on_decide is None


@pytest.mark.asyncio
async def test_screen_decisions_go_to_the_issuer_and_report_why_they_failed():
    from plugins.hub.relay_commands import JoinRequestRow
    from plugins.hub.relay_state import RelayError

    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = _connected_plugin(altview_stack_manager=view_stack)
    await plugin._handle_connect_command("")
    view, _ = view_stack.push.await_args.args
    row = JoinRequestRow("a" * 32, "home-server", "abcd…ef01")
    decide = plugin._relay_agent.decide_enrollment_request

    assert await view._on_decide(row, "accept") is None
    decide.assert_awaited_once_with("a" * 32, decision="accept", source_agent="agent-1")

    decide.side_effect = RelayError("device name 'x' is already on this network")
    assert await view._on_decide(row, "accept") == (
        "device name 'x' is already on this network"
    )
    decide.side_effect = RuntimeError("private detail")
    assert await view._on_decide(row, "reject") == "try again"


async def _form_view(plugin):
    """Open the code form the way bare /connect does and hand back the view."""
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin.event_bus = _EventBus(altview_stack_manager=view_stack)
    await plugin._open_connect_altview("kollabor.ai")
    view, _name = view_stack.push.await_args.args
    return view


@pytest.mark.asyncio
async def test_the_form_starts_a_network_when_no_code_is_entered():
    """First device: nobody has a code yet, so an empty code starts the network."""
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._run_connect_command = AsyncMock(
        return_value="network kollabor.ai  trust: open\nthis device laptop-kollab"
    )
    view = await _form_view(plugin)

    assert await view._on_attach("kollabor.ai") is True
    plugin._run_connect_command.assert_awaited_once_with("kollabor.ai")

    plugin._run_connect_command.return_value = "connect: relay discovery is unavailable"
    assert await view._on_attach("kollabor.ai") is False
    plugin._run_connect_command.return_value = "network none\ncontact route none"
    assert await view._on_attach("kollabor.ai") is False
    assert await view._on_attach("not a domain") is False


@pytest.mark.asyncio
async def test_an_approved_join_reports_the_network_device_and_trust():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._run_connect_enrollment = AsyncMock(return_value={"status": "approved"})
    plugin._relay_agent = SimpleNamespace(
        network_name=lambda: "marco-home",
        device_name=lambda: "home-server",
        trust_level=lambda: "open",
    )
    plugin._relay_commands = SimpleNamespace(
        client=SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai"))
    )
    view = await _form_view(plugin)
    submission = ConnectSubmission("kollabor.ai", PrivateCode("ABCD-EFGH"))
    try:
        outcome = await view._on_submit(submission)
    finally:
        submission.code.clear()

    assert outcome.status.value == "approved"  # the altview module may be reloaded
    assert outcome.detail == "joined marco-home as home-server. trust: open"
