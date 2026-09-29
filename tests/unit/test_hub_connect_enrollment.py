from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor.commands.registry import SlashCommandRegistry
from plugins.altview.connect_altview import (
    ConnectOfferAltView,
    ConnectStatus,
    ConnectSubmission,
    PrivateCode,
)
from plugins.altview.contact_altview import ContactRequestAltView, ContactReviewAltView
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
        hub_connect=AsyncMock(),
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
    state.hub_connect.assert_not_awaited()


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
    assert view_name == "connect-offer"
    assert view.domain == "example.test"
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1_000)
    await view._create()
    assert view._private_code.reveal() == code
    state.hub_enrollment_offer.assert_awaited_once_with("example.test")
    state.hub_connect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "session_name"),
    [
        ("_open_contact_request_altview", "contact-request"),
        ("_open_contact_review_altview", "contact-review"),
    ],
)
async def test_private_contact_views_always_open_fresh_sessions(method, session_name):
    view_stack = SimpleNamespace(push=AsyncMock())
    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = _EventBus(altview_stack_manager=view_stack)

    result = await getattr(plugin, method)("example.test")

    assert result == ""
    _view, view_name = view_stack.push.await_args.args
    assert view_name == session_name
    assert view_stack.push.await_args.kwargs == {"reuse": False}


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
    assert registry.get_command("connect-offer") is None
    assert registry.get_command("contact-request") is None
    assert registry.get_command("contact-review") is None
    assert "connect" in integrator._plugin_classes
    assert ConnectOfferAltView().metadata.category == "internal"
    assert ContactRequestAltView("example.test").metadata.category == "internal"
    assert (
        ContactReviewAltView("example.test", lambda: [], lambda *_args: None)
        .metadata.category
        == "internal"
    )

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

    assert await command.handler("") == ""
    assert view_stack.push.await_count == 1
    _view, view_name = view_stack.push.await_args.args
    assert view_name == "connect"
