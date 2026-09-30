"""A network broadcast reaches the network, never an accepted stranger's agents."""

from types import SimpleNamespace

import pytest

from plugins.hub.plugin import HubPlugin

MEMBER = "relay:" + "a" * 64 + ":" + "b" * 32 + ":infra-1"
STRANGER = "relay:" + "c" * 64 + ":" + "d" * 32 + ":ops-1"


def _row(name, device, address):
    return {
        "name": name,
        "device": device,
        "handle": f"{name}@{device}",
        "state": "idle",
        "address": address,
        "online": True,
        "is_coordinator": False,
        "workspace_id": address.split(":")[2],
    }


async def _empty(*_args, **_kwargs):
    return []


@pytest.mark.asyncio
async def test_a_network_broadcast_skips_agents_of_accepted_strangers():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    hub._presence = SimpleNamespace(get_cached_agents=lambda: [])
    hub._route_message = _empty
    sent = []

    async def fake_send(address, content, **kwargs):
        sent.append(address)
        return {"id": "x", "state": "delivered"}

    hub._relay_agent = SimpleNamespace(
        remote_agents=lambda: [
            _row("infra", "alzan-prod-home", MEMBER),
            _row("ops", "mac-kollab", STRANGER),
        ],
        trust_level=lambda: "open",
        resolve_handle=lambda handle: {
            "infra@alzan-prod-home": MEMBER,
            "ops@mac-kollab": STRANGER,
        }[handle],
        send=fake_send,
        is_stranger=lambda address: address == STRANGER,
    )

    result = await hub._handle_broadcast_command("shipped phase B", scope="network")

    assert sent == [MEMBER]
    assert "1 network agent(s)" in result
