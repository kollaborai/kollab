"""#1459: relay-version errors shown in a web chat use page wording."""

from types import SimpleNamespace

import pytest

from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_state import RelayError


@pytest.mark.asyncio
async def test_outdated_relay_error_uses_page_copy_only_for_web_sessions(monkeypatch):
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

    monkeypatch.setenv("KOLLAB_WEBUI_SESSION", "1")
    web_rejection = await hub._route_message(message)
    assert web_rejection == [
        (
            "helper@relay-test",
            "This relay requires a newer engine version. Update the engine, then try again.",
        )
    ]
    assert "kollab --" not in web_rejection[0][1]

    monkeypatch.delenv("KOLLAB_WEBUI_SESSION")
    cli_rejection = await hub._route_message(message)
    assert cli_rejection == [
        (
            "helper@relay-test",
            "this version of kollab is too old for relay.test; run kollab --upgrade",
        )
    ]
