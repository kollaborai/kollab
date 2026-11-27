"""#1459: relay-version errors shown in a web chat use page wording."""

from types import SimpleNamespace

import pytest

from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_state import RelayError


@pytest.mark.asyncio
async def test_outdated_relay_error_uses_page_copy_only_for_web_requests():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="browser-agent", agent_id="browser-image")
    hub._relay_agent = SimpleNamespace(
        resolve_handle=lambda _handle: (_ for _ in ()).throw(
            RelayError(
                "this version of kollab is too old for relay.test; run kollab --upgrade"
            )
        ),
        send=lambda *_args, **_kwargs: None,
    )
    message = HubMessage(
        action="message",
        from_identity="browser-agent",
        to="helper@relay-test",
        content="hi",
        scope=MessageScope.DIRECT.value,
    )

    web_rejection = await hub._route_message(message, input_source="webui")
    assert web_rejection == [
        (
            "helper@relay-test",
            "This engine version cannot connect to this relay. The message was not sent.",
        )
    ]
    assert "kollab --" not in web_rejection[0][1]

    cli_rejection = await hub._route_message(message)
    assert cli_rejection == [
        (
            "helper@relay-test",
            "this version of kollab is too old for relay.test; run kollab --upgrade",
        )
    ]


@pytest.mark.asyncio
async def test_hub_msg_from_the_web_ui_gets_page_copy_for_relay_errors():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="browser-agent", agent_id="browser-image")
    seen = []

    async def route(message, *, input_source=None):
        seen.append(input_source)
        return []

    hub._route_message = route
    hub._resolve_scope = lambda _target: MessageScope.DIRECT.value
    assert await hub._handle_msg_command("helper@relay-test hi", input_source="webui") == "sent to helper@relay-test"
    assert await hub._handle_msg_command("helper@relay-test hi") == "sent to helper@relay-test"
    assert seen == ["webui", None]
