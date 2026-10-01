"""A knock nobody answers must not leave its approval, trust, link and grant behind."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import RelayError, RelayStateStore

_KEY = SigningKey.generate().verify_key.encode().hex()
_DAY = 24 * 3600
_T0 = 1_800_000_000
_AGENT = "lapis"


class _Directory:
    def agents(self, _workspace):
        return [SimpleNamespace(agent_id="local-agent")]


def _bridge(tmp_path):
    plugin = SimpleNamespace(
        _identity=SimpleNamespace(agent_id="local-agent", identity="operator"),
        _on_message_received=AsyncMock(),
    )
    bridge = RelayAgentBridge(
        plugin,
        tmp_path,
        state_dir=tmp_path / "private-state",
        directory=_Directory(),
    )
    # A real client, as the knock itself uses it. It must read as online: the
    # sweep only judges a knock while it can see which links are live.
    client = RelayClient(tmp_path, state_dir=bridge.owner.state_dir)
    client._state = "online"
    bridge.commands = SimpleNamespace(client=client)
    bridge._wall_clock = lambda: _T0
    return bridge


def _at(bridge, seconds_after_knock):
    bridge._wall_clock = lambda: _T0 + seconds_after_knock


def _left_behind(tmp_path, bridge):
    """(approved, trusted, linked, granted, knock recorded) for the knocked key."""
    disk = RelayStateStore(tmp_path, tmp_path / "private-state").state
    granted = any(row["peer"] == _KEY for row in bridge.store.grants(disk.room))
    return (
        _KEY in disk.approvals,
        _KEY in disk.peer_trust,
        _KEY in disk.links,
        granted,
        _KEY in disk.knocks,
    )


def test_the_knock_time_is_recorded_with_what_the_knock_leaves(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)

    assert _left_behind(tmp_path, bridge) == (True, True, True, True, True)
    disk = RelayStateStore(tmp_path, tmp_path / "private-state").state
    assert disk.knocks == {_KEY: _T0}


def test_an_unanswered_knock_is_cleared_after_seven_days(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)

    _at(bridge, 7 * _DAY + 1)
    bridge.expire_knocks()

    assert _left_behind(tmp_path, bridge) == (False, False, False, False, False)
    assert _KEY not in bridge.commands.client.state.approvals


def test_a_knock_younger_than_seven_days_is_kept(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)

    _at(bridge, 6 * _DAY)
    bridge.expire_knocks()

    assert _left_behind(tmp_path, bridge) == (True, True, True, True, True)


def test_a_knock_is_not_judged_while_offline(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)
    bridge.commands.client._state = "reconnecting"

    _at(bridge, 30 * _DAY)
    bridge.expire_knocks()

    assert _left_behind(tmp_path, bridge) == (True, True, True, True, True)


def test_an_accepted_knock_with_a_live_link_never_expires(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)
    bridge.commands.client._peers[_KEY] = "a" * 32

    _at(bridge, 8 * _DAY)
    bridge.expire_knocks()

    assert _left_behind(tmp_path, bridge) == (True, True, True, True, False)


@pytest.mark.asyncio
async def test_a_peer_that_reached_us_is_an_accepted_knock(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)

    # Any request from the peer proves the directory linked both ways. This
    # one is refused for lacking a secure session, after it was noticed.
    with pytest.raises(RelayError):
        await bridge._receive(_KEY, "directory", {})

    _at(bridge, 8 * _DAY)
    bridge.expire_knocks()

    assert _left_behind(tmp_path, bridge) == (True, True, True, True, False)


def test_knocking_again_starts_the_week_over(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)
    _at(bridge, 6 * _DAY)
    bridge._bind_knocked_peer(_KEY, _AGENT)

    _at(bridge, 8 * _DAY)
    bridge.expire_knocks()
    assert _left_behind(tmp_path, bridge) == (True, True, True, True, True)

    _at(bridge, 13 * _DAY + 1)
    bridge.expire_knocks()
    assert _left_behind(tmp_path, bridge) == (False, False, False, False, False)


def test_a_knocked_device_later_joined_by_code_is_not_revoked(tmp_path):
    bridge = _bridge(tmp_path)
    bridge._bind_knocked_peer(_KEY, _AGENT)
    # A join code makes it a member: no longer a stranger, nothing to expire.
    bridge.commands.client.add_config_recipient(_KEY)

    _at(bridge, 8 * _DAY)
    bridge.expire_knocks()

    approved, _trusted, linked, _granted, recorded = _left_behind(tmp_path, bridge)
    assert approved and not linked and not recorded
