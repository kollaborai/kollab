"""A device alone on a network counts as not set up (issue #121, Story 1).

kollab 0.10.7 made a network of one on every launch (a room on kollabor.ai, no name,
nobody else). The guided notice must still offer "Join with a code" there, "Start" must
keep that network, and a join must be allowed to replace it, never a bigger one.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import plugins.hub.plugin as hub_plugin
from plugins.altview.connect_altview import ConnectGuideAltView
from plugins.hub import enrollment_client
from plugins.hub.enrollment_client import enroll_device
from plugins.hub.enrollment_codes import generate_enrollment_code
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_commands import ConnectSnapshot
from plugins.hub.relay_state import RelayStateStore

OTHER = "c" * 64


def _write_lone_state(state_dir, **fields):
    """The state.json kollab 0.10.7 wrote on every launch (plus `fields` on top)."""
    state_dir.mkdir(mode=0o700)
    path = state_dir / "state.json"
    path.write_text(
        json.dumps(
            {
                "origin": "https://kollabor.ai",
                "enabled": True,
                "room": "a" * 64,
                "workspace_id": "b" * 32,
                "approvals": [],
                "inviter": "",
                **fields,
            }
        )
    )
    path.chmod(0o600)


def _plugin(**attrs):
    cls = next(
        value
        for value in vars(hub_plugin).values()
        if isinstance(value, type) and hasattr(value, "_on_startup_connect_guide")
    )
    plugin = object.__new__(cls)
    plugin._cli_args = SimpleNamespace(pipe=False, detached=False, query=None, hub=None, attach=None)
    plugin.__dict__.update(attrs)
    return plugin


def _lone_plugin(tmp_path, **fields):
    _write_lone_state(tmp_path / "network", **fields)
    store = RelayStateStore(tmp_path / "workspace", tmp_path / "network")
    return _plugin(_relay_commands=None, _relay_agent=SimpleNamespace(_state=lambda: store))


def _press_enter(view):
    class Pane:
        def get_terminal_size(self):
            return (80, 24)

        def clear_screen(self):
            pass

        def write_at(self, *_args, **_kwargs):
            pass

    async def run():
        await view.on_enter(Pane())
        return await view.handle_input(SimpleNamespace(name="Enter", char="", ctrl=False, modifiers={}))

    return asyncio.run(run())


# ------------------------------------------------------------ the notice


def test_lone_device_on_the_0_10_7_network_gets_the_two_choices(tmp_path):
    has_network = asyncio.run(_lone_plugin(tmp_path)._connect_has_network())
    assert has_network is False
    view = ConnectGuideAltView(has_network=has_network)
    assert _press_enter(view) is False  # not done: the choices are showing
    assert view.stage == "choices" and view.answer is None


def test_lone_device_on_a_named_network_gets_the_two_choices(tmp_path):
    plugin = _lone_plugin(tmp_path, network_name="marco-net")
    assert asyncio.run(plugin._connect_has_network()) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("approvals", [OTHER]),
        ("inviter", OTHER),
        ("peer_devices", {OTHER: "mac"}),
        ("config_recipients", [OTHER]),
        ("links", [OTHER]),
    ],
)
def test_lone_check_leaves_a_network_with_another_device_on_the_screen_path(tmp_path, field, value):
    plugin = _lone_plugin(tmp_path, network_name="marco-net")
    setattr(plugin._relay_agent._state().state, field, value)
    assert asyncio.run(plugin._connect_has_network()) is True


@pytest.mark.parametrize(
    "extra,has_network",
    [
        ({}, False),
        ({"remote_agents": ("lapis@mac",)}, True),
        ({"offline_devices": ("mac",)}, True),
        ({"config_from": "mac"}, True),
    ],
)
def test_lone_check_reads_the_attached_daemons_snapshot(extra, has_network):
    plugin = _plugin(_cli_args=SimpleNamespace(attach="koordinator"))
    plugin._attached_connect_snapshot = AsyncMock(
        return_value=ConnectSnapshot(
            network="net", domain="kollabor.ai", trust="open", device="d", relay_online=True, **extra
        )
    )
    assert asyncio.run(plugin._connect_has_network()) is has_network


# ------------------------------------------------------------ Start


def test_half_set_up_lone_device_leaves_it_and_starts_fresh():
    # A 0.10.7 room with no domain and networking off cannot be re-attached:
    # Start leaves it, as Join does, then starts a network on kollabor.ai.
    plugin = _plugin()
    plugin._relay_network_domain = lambda: ""
    plugin._start_connect_network = AsyncMock(side_effect=[False, True])
    plugin._connect_has_network = AsyncMock(return_value=False)
    plugin._attached = lambda: True
    plugin._attached_connect = AsyncMock(return_value="")
    plugin._open_connect_screen = AsyncMock(return_value="")
    assert asyncio.run(plugin._guided_new_network()) == ""
    plugin._attached_connect.assert_awaited_once_with("leave")
    plugin._open_connect_screen.assert_awaited_once_with("kollabor.ai", guide=True)


def test_start_never_leaves_a_network_with_another_device():
    plugin = _plugin()
    plugin._relay_network_domain = lambda: ""
    plugin._start_connect_network = AsyncMock(return_value=False)
    plugin._connect_has_network = AsyncMock(return_value=True)
    plugin._attached = lambda: True
    plugin._attached_connect = AsyncMock(return_value="")
    plugin._open_connect_screen = AsyncMock(return_value="")
    assert "could not start" in asyncio.run(plugin._guided_new_network())
    plugin._attached_connect.assert_not_awaited()


def test_lone_start_targets_the_devices_own_network():
    plugin = _plugin()
    plugin._relay_network_domain = lambda: "relay.example.com"
    plugin._start_connect_network = AsyncMock(return_value=True)
    plugin._open_connect_screen = AsyncMock(return_value="")
    assert asyncio.run(plugin._guided_new_network()) == ""
    plugin._start_connect_network.assert_awaited_once_with("relay.example.com")
    plugin._open_connect_screen.assert_awaited_once_with("relay.example.com", guide=True)


def test_lone_start_keeps_the_room_and_names_the_network(tmp_path, monkeypatch):
    """/connect on a lone device re-attaches in place: same room, same identity, a name."""
    _write_lone_state(tmp_path / "network")
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "network", label="lone")
    room, workspace = client.state.room, client.state.workspace_id

    async def offline(self):
        raise OSError("no relay in a unit test")

    monkeypatch.setattr(RelayClient, "_connection", offline)

    async def start():
        await client.connect("https://kollabor.ai", ws_url="wss://kollabor.ai/relay/v1/ws")
        await client.close()

    asyncio.run(start())
    assert (client.state.room, client.state.workspace_id) == (room, workspace)
    assert client.state.network_name.endswith("-net")
    assert client.state.is_alone()


# ------------------------------------------------------------ Join


async def _join_from_lone_state(tmp_path, monkeypatch, **fields):
    """enroll_device on a device whose state.json is the 0.10.7 one, `fields` on top.
    Returns (client, the drive stub, what enroll_device gave back)."""
    origin = "https://kollabor.ai"
    offer_id = "2" * 32
    code = generate_enrollment_code(offer_id)
    code_text = code.for_private_display()
    _write_lone_state(tmp_path / "destination-network")
    client = RelayClient(
        tmp_path / "destination-workspace", state_dir=tmp_path / "destination-network", label="destination"
    )
    for name, value in fields.items():
        setattr(client.state, name, value)
    discovery = SimpleNamespace(
        origin=origin,
        manifest={
            "coordinator": {"public_key": "0" * 64},
            "endpoints": {"control": origin + "/relay/v1"},
        },
    )
    commands = SimpleNamespace(
        client=client,
        _discover=AsyncMock(return_value=(discovery, "", (), False)),
        _relay_url=lambda _discovery: "wss://kollabor.ai/relay/v1/ws",
    )

    class LookupOnlyTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, _frame, **_kwargs):
            return {"offer_id": offer_id}

    monkeypatch.setattr(
        enrollment_client, "EnrollmentHTTPClient", lambda *_a, **_k: LookupOnlyTransport()
    )
    drive = AsyncMock(return_value={"status": "stubbed"})
    monkeypatch.setattr(enrollment_client, "_drive_destination_enrollment", drive)
    try:
        result = await enroll_device(commands, "kollabor.ai", code_text)
    except Exception as error:
        result = error
    finally:
        code.wipe()
    return client, drive, result


@pytest.mark.asyncio
async def test_lone_device_join_replaces_its_network(tmp_path, monkeypatch):
    client, drive, result = await _join_from_lone_state(tmp_path, monkeypatch)
    assert result == {"status": "stubbed"}
    drive.assert_awaited_once()
    assert drive.await_args.args[1]["workspace_id"] == "b" * 32  # the device's own identity
    assert client.state.origin == "" and client.state.enabled is False
    assert client.state.room != "a" * 64  # the lone room is gone


@pytest.mark.asyncio
async def test_lone_check_never_lets_a_join_replace_a_network_with_another_device(tmp_path, monkeypatch):
    client, drive, result = await _join_from_lone_state(tmp_path, monkeypatch, approvals=[OTHER])
    assert result != {"status": "stubbed"}
    drive.assert_not_awaited()
    assert client.state.origin == "https://kollabor.ai" and client.state.room == "a" * 64
