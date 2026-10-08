"""The hub snapshot carries this computer's name and remote agent rows to the web UI."""

import asyncio

from kollabor.state.snapshots import HubSnapshot
from plugins.hub.plugin import HubPlugin

ROW = {"name": "lapis", "device": "prod-box", "handle": "lapis@prod-box", "state": "idle"}


def test_device_and_remote_rows_survive_the_state_rpc():
    snapshot = HubSnapshot(device="devbox", remote=[ROW])

    assert HubSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_daemon_without_the_fields_reports_no_network():
    snapshot = HubSnapshot.from_dict({"my_identity": "lapis"})

    assert (snapshot.device, snapshot.remote) == ("", [])


class FakeRelay:
    def __init__(self, state):
        self.state = state

    async def _owner_call(self, method, params):
        if self.state is None:
            raise RuntimeError("relay owner down")
        assert method == "relay.status"
        return {"state": self.state}


class FakeHub:
    """Just the network surface of the hub plugin, with the real method bound."""

    network_agent_rows = HubPlugin.network_agent_rows

    def __init__(self, relay=None, on_network=True):
        self._relay_agent = relay
        self._on_network = on_network

    def _relay_network_domain(self):
        return "net.example" if self._on_network else ""

    def _relay_device_name(self):
        return "devbox-kollab"

    async def _refresh_remote_agent_rows(self):
        return [ROW]


def network_rows(hub):
    return asyncio.run(hub.network_agent_rows())


def test_off_a_network_there_is_no_name_and_no_rows():
    assert network_rows(FakeHub(FakeRelay("online"), on_network=False)) == ("", [])
    assert network_rows(FakeHub(relay=None)) == ("", [])


def test_an_offline_relay_keeps_the_name_but_shows_no_remote_rows():
    assert network_rows(FakeHub(FakeRelay("offline"))) == ("devbox-kollab", [])


def test_a_relay_that_cannot_answer_shows_no_remote_rows():
    assert network_rows(FakeHub(FakeRelay(None))) == ("devbox-kollab", [])


def test_an_online_relay_shows_its_remote_rows():
    assert network_rows(FakeHub(FakeRelay("online"))) == ("devbox-kollab", [ROW])
