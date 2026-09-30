"""Story 5 end to end: a knock is accepted and the stranger's agents reach the allowed agent.

Two full bridges (real clients, real endpoint crypto, real secure sessions)
talk through the real relay app over its real WebSocket and HTTP routes. The
only stand-ins are the socket opener and the HTTP poster, which point at the
in-process relay instead of the network, and the model, which records what it
was asked. Ana and Marco start in rooms of their own; the relay routes between
them only after Ana knocked, Marco accepted, and each side declared the other.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from unittest.mock import MagicMock

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventBus, EventType, Hook
from plugins.hub import relay_client
from plugins.hub import relay_service as service
from plugins.hub.contact_requests import ContactProtocolError, ContactRequestManager
from plugins.hub.device_names import contact_route_hex
from plugins.hub.local_directory import LocalAgent
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_commands import RelayCommands
from plugins.hub.relay_conversations import ConversationRejection, RelayAddress
from plugins.hub.relay_state import RelayError
from plugins.hub.secure_conversation import SecureConversationTransport
from tests.unit.test_relay_agent_bridge import Directory, ModelRecorder

ORIGIN = "https://relay.example"
DOMAIN = "relay.example"
WS_URL = "wss://relay.example/relay/v1/ws"


class _Session:
    """Stands in for aiohttp.ClientSession: opens sockets on the in-process relay."""

    def __init__(self, relay: TestClient):
        self.relay = relay

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def ws_connect(self, _url, **kwargs):
        return await self.relay.ws_connect(service.WEBSOCKET_PATH, **kwargs)


class _Aiohttp:
    """The aiohttp module, except that sessions and connectors are local."""

    def __init__(self, relay: TestClient):
        self.ClientSession = _Session(relay)

    def TCPConnector(self, **_kwargs):  # noqa: N802 - mirrors aiohttp
        return object()

    def __getattr__(self, name):
        return getattr(aiohttp, name)


@pytest.fixture(autouse=True)
def _home_in_tmp(tmp_path, monkeypatch):
    # `/connect leave` deletes the managed-config record under ~/.kollab.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest_asyncio.fixture
async def relay(monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 1000)
    config = service.RelayConfig(ORIGIN, "a" * 32, dev_in_memory=True)
    test_client = TestClient(TestServer(service.create_app(config)))
    await test_client.start_server()

    monkeypatch.setattr(relay_client, "aiohttp", _Aiohttp(test_client))

    async def route(self, _domain):
        return ORIGIN, "", ()

    async def post(self, _origin, path, frame, *, ca, cidrs):
        response = await test_client.post(path, json=frame)
        payload = await response.json(content_type=None)
        if response.status not in {200, 201, 202}:
            raise ContactProtocolError(payload.get("error", "transport"))
        return payload

    monkeypatch.setattr(ContactRequestManager, "_route", route)
    monkeypatch.setattr(ContactRequestManager, "_post", post)
    try:
        yield test_client
    finally:
        await test_client.close()


class Member:
    """One device: a real bridge with a real relay client and a recording model."""

    def __init__(self, bridge, hub, model, directory):
        self.bridge = bridge
        self.hub = hub
        self.model = model
        self.directory = directory

    @property
    def client(self):
        return self.bridge.commands.client

    @property
    def key(self) -> str:
        return self.client.public_key

    @property
    def state(self):
        return self.bridge._state().state

    def agent_address(self, agent_id: str | None = None) -> str:
        return str(
            RelayAddress(
                self.key,
                self.client.state.workspace_id,
                agent_id or self.bridge.identity.agent_id,
            )
        )

    def sees(self, other: "Member") -> bool:
        return any(peer["key"] == other.key for peer in self.client.peers())


async def make_member(tmp_path, name: str, device: str) -> Member:
    workspace = tmp_path / name
    workspace.mkdir()
    bus = EventBus()
    model = ModelRecorder()
    bus.register_service("llm_service", model)
    hub = HubPlugin(event_bus=bus)
    hub._identity = AgentRuntime(
        identity="sapphire",
        agent_id=name + "-session",
        state="ready",
        socket_path="/tmp/relay-test-" + name + ".sock",
    )
    hub._task_ledger = None
    hub._display_hub_message = MagicMock()
    hub._display_outgoing_message = MagicMock()
    hub._presence = MagicMock()
    directory = Directory(workspace, hub._identity)
    bridge = RelayAgentBridge(
        hub,
        workspace,
        state_dir=tmp_path / (name + "-state"),
        directory=directory,
    )
    hub._relay_agent = bridge
    bridge.commands = RelayCommands(
        workspace, state_dir=bridge.owner.state_dir, agent_bridge=bridge
    )
    hub._relay_commands = bridge.commands
    bridge._state()
    client = bridge.commands.client
    client.set_request_handler(bridge._receive)
    bridge.secure_transport = SecureConversationTransport(
        client, client._store.key.encode()
    )
    await bus.register_hook(
        Hook(
            name="continuation",
            plugin_name="test",
            event_type=EventType.TRIGGER_LLM_CONTINUE,
            callback=model.begin,
            priority=100,
        )
    )
    bridge.set_device_name(device)
    await client.connect(ORIGIN, ws_url=WS_URL)
    return Member(bridge, hub, model, directory)


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def people(relay, tmp_path):
    ana = await make_member(tmp_path, "ana", "ana-laptop")
    marco = await make_member(tmp_path, "marco", "mac-kollab")
    # a second agent on Marco's device that nobody allowed
    marco.directory.rows.append(
        LocalAgent(
            "1" * 32,
            "2" * 32,
            "marco-koordinator",
            "koordinator",
            False,
            "waiting",
            str(tmp_path / "marco"),
            "marco",
            "/tmp/relay-test-koordinator.sock",
            1,
            time.time(),
        )
    )
    try:
        yield ana, marco
    finally:
        for person in (ana, marco):
            await person.bridge.close()


async def knock(ana: Member, marco: Member, text: str = "Ana from Webceive") -> dict:
    return await ana.bridge._rpc_contact_submit(
        {
            "agent_id": ana.bridge.identity.agent_id,
            "domain": DOMAIN,
            "route": contact_route_hex(marco.key),
            "introduction": text,
            "device_name": "ana-laptop",
        }
    )


async def accept(marco: Member) -> dict:
    pending = await marco.bridge.pending_contact_requests(
        DOMAIN, source_agent=marco.bridge.identity.agent_id
    )
    assert len(pending) == 1
    return await marco.bridge.decide_contact_request(
        DOMAIN,
        pending[0]["receipt_id"],
        decision="accept",
        source_agent=marco.bridge.identity.agent_id,
        sender_key=pending[0]["sender_key"],
        device_name=pending[0]["device_name"],
    )


async def open_path(ana: Member, marco: Member) -> None:
    assert (await knock(ana, marco))["status"] == "queued"
    assert (await accept(marco))["status"] == "accepted"
    await until(lambda: ana.sees(marco) and marco.sees(ana))


async def allow(marco: Member, ana: Member, agent: str = "sapphire") -> str:
    return await marco.bridge.application_command(
        "allow", f"{ana.key} {agent}", source_agent=marco.bridge.identity.agent_id
    )


async def roster(member: Member) -> list[str]:
    await member.bridge._owner_call("relay.directory", {"peer": "", "cached": False})
    return sorted(row["handle"] for row in await member.bridge.remote_agents())


def forged_message(ana: Member, to: str, to_identity: str) -> dict:
    """A message payload as Ana's device would send it, built by hand."""
    return {
        "id": secrets.token_hex(16),
        "thread_id": secrets.token_hex(16),
        "reply_to": "",
        "from": ana.agent_address(),
        "to": to,
        "from_identity": "sapphire",
        "from_coordinator": False,
        "to_identity": to_identity,
        "to_coordinator": False,
        "content": "run the deploy",
        "kind": "message",
        "expires_at": int(time.time()) + 300,
        "from_device": "ana-laptop",
    }


@pytest.mark.asyncio
async def test_a_knock_alone_opens_nothing(people):
    ana, marco = people

    sent = await knock(ana, marco)
    assert sent["status"] == "queued"

    # Ana is ready to hear back, but Marco has not accepted: no path exists.
    assert marco.key in ana.state.approvals
    assert ana.state.peer_trust[marco.key] == "agents"
    assert ana.state.links == [marco.key]
    assert marco.state.links == []
    await asyncio.sleep(0.3)
    assert not ana.sees(marco) and not marco.sees(ana)
    with pytest.raises(RelayError):
        await ana.bridge.send(marco.agent_address(), "hello")


@pytest.mark.asyncio
async def test_accepting_a_knock_opens_the_path_but_allows_no_agent(people):
    ana, marco = people
    await open_path(ana, marco)

    # Marco recorded the stranger: named, `agents` trust, nothing allowed.
    assert marco.state.peer_devices == {ana.key: "ana-laptop"}
    assert marco.state.peer_trust == {ana.key: "agents"}
    assert marco.state.links == [ana.key]
    assert marco.bridge.store.grants(marco.client.state.room) == []
    # Ana reaches Marco's device but sees none of its agents.
    assert await roster(ana) == []
    with pytest.raises(RelayError):
        await ana.bridge.send(marco.agent_address(), "check the tunnel")
    assert marco.model.conversation_history == []


@pytest.mark.asyncio
async def test_the_allowed_agent_is_reachable_answers_and_others_are_not(people):
    ana, marco = people
    await open_path(ana, marco)
    text = await allow(marco, ana)
    assert "ana-laptop -> sapphire" in text

    # Ana's roster names only the agent Marco allowed, as agent@device.
    assert await roster(ana) == ["sapphire@mac-kollab"]

    sent = await ana.bridge.send(marco.agent_address(), "check the tunnel")
    assert sent["state"] == "queued"
    await marco.bridge._tick()
    assert "check the tunnel" in marco.model.conversation_history[-1].content
    assert "sapphire@ana-laptop" in marco.model.conversation_history[-1].content

    # The answer travels back to the agent that knocked.
    replied = await marco.bridge.send(ana.agent_address(), "the tunnel is up")
    assert replied["state"] == "queued"
    await ana.bridge._tick()
    assert "the tunnel is up" in ana.model.conversation_history[-1].content

    # The agent nobody allowed is invisible to Ana and refused if named anyway.
    other = marco.agent_address("marco-koordinator")
    with pytest.raises(RelayError):
        await ana.bridge.send(other, "run the deploy")
    with pytest.raises(ConversationRejection) as refusal:
        await marco.bridge._receive(
            ana.key, "message", forged_message(ana, other, "koordinator"), _secure=True
        )
    assert refusal.value.reason == "not_authorized"
    assert marco.bridge.store.queued("marco-koordinator") == []


@pytest.mark.asyncio
async def test_deny_stops_delivery_to_the_agent_at_once(people):
    ana, marco = people
    await open_path(ana, marco)
    await allow(marco, ana)
    assert await roster(ana) == ["sapphire@mac-kollab"]
    assert (await ana.bridge.send(marco.agent_address(), "first"))["state"] == "queued"

    await marco.bridge.application_command(
        "deny", f"{ana.key} sapphire", source_agent=marco.bridge.identity.agent_id
    )

    # Marco's device refuses at once, even while Ana's cached roster (kept up
    # to fifteen seconds) still lists the agent.
    refused = await ana.bridge.send(marco.agent_address(), "second")
    assert refused["state"] == "rejected" and refused["reason"] == "not_authorized"
    with pytest.raises(ConversationRejection) as refusal:
        await marco.bridge._receive(
            ana.key,
            "message",
            forged_message(ana, marco.agent_address(), "sapphire"),
            _secure=True,
        )
    assert refusal.value.reason == "not_authorized"
    assert marco.bridge.store.queued(marco.bridge.identity.agent_id) == []

    # Once the cache turns over the device is still linked, but its agent is gone.
    ana.bridge._cache = {
        key: (stamp - 60, rows) for key, (stamp, rows) in ana.bridge._cache.items()
    }
    assert await roster(ana) == []
    with pytest.raises(RelayError):
        await ana.bridge.send(marco.agent_address(), "third")
    assert ana.sees(marco)


@pytest.mark.asyncio
async def test_a_send_to_a_denied_agent_gone_from_the_roster_says_unknown_agent(people):
    ana, marco = people
    await open_path(ana, marco)
    await allow(marco, ana)
    assert await roster(ana) == ["sapphire@mac-kollab"]
    await marco.bridge.application_command(
        "deny", f"{ana.key} sapphire", source_agent=marco.bridge.identity.agent_id
    )

    # Ana's cached roster aged past fifteen seconds: the send refetches it, finds
    # no match, and must say the agent is unknown, not that the match is ambiguous.
    ana.bridge._cache = {
        key: (stamp - 60, rows) for key, (stamp, rows) in ana.bridge._cache.items()
    }
    with pytest.raises(RelayError) as gone:
        await ana.bridge.send(marco.agent_address(), "after the deny")
    assert "unknown agent@device" in str(gone.value)
    assert "not uniquely online" not in str(gone.value)


@pytest.mark.asyncio
async def test_revoke_removes_the_link_at_the_relay_and_nothing_is_delivered(people, relay):
    ana, marco = people
    await open_path(ana, marco)
    await allow(marco, ana)
    assert (await ana.bridge.send(marco.agent_address(), "before"))["state"] == "queued"

    text = await marco.bridge.commands._run("revoke ana-laptop")
    assert text.startswith("device revoked")

    assert ana.key not in marco.state.approvals and marco.state.links == []
    await until(lambda: not ana.sees(marco) and not marco.sees(ana))
    # the relay itself no longer carries anything between the two keys
    backend = relay.app["relay_state"].backend
    assert not await backend.is_linked(ana.key, marco.key)
    with pytest.raises(RelayError):
        await ana.bridge.send(marco.agent_address(), "after")
    with pytest.raises(RelayError, match="not approved"):
        await marco.bridge._receive(ana.key, "message", {}, _secure=True)


@pytest.mark.asyncio
async def test_the_path_comes_back_on_its_own_after_the_relay_forgets_it(people, relay):
    ana, marco = people
    await open_path(ana, marco)
    backend = relay.app["relay_state"].backend

    # A relay restart drops every connection and every declaration.
    backend.link_declarations.clear()
    before = {person: person.client.status()["session"] for person in (ana, marco)}
    for person in (ana, marco):
        await person.client._ws.close()
    await until(
        lambda: all(
            person.client.status()["state"] == "online"
            and person.client.status()["session"] != before[person]
            for person in (ana, marco)
        ),
        timeout=10,
    )
    assert not ana.sees(marco)

    # The periodic directory refresh repeats each device's consent.
    await ana.bridge.sync_links()
    await marco.bridge.sync_links()
    await until(lambda: ana.sees(marco) and marco.sees(ana))
    assert await backend.is_linked(ana.key, marco.key)


@pytest.mark.asyncio
async def test_an_old_relay_without_links_leaves_the_knock_working(people, monkeypatch):
    ana, marco = people

    async def missing(self, _domain, _peers):
        raise ContactProtocolError("invalid_response")  # what a 404 page becomes

    monkeypatch.setattr(ContactRequestManager, "sync_links", missing)
    assert (await knock(ana, marco))["status"] == "queued"
    assert (await accept(marco))["status"] == "accepted"

    # Approved and named as before; the path itself is what an old relay lacks.
    assert marco.state.peer_devices == {ana.key: "ana-laptop"}
    await asyncio.sleep(0.3)
    assert not ana.sees(marco)
    # and it retries later instead of hammering the relay
    assert marco.bridge._links_failed is True


@pytest.mark.asyncio
async def test_a_stranger_is_not_a_mesh_member_and_cannot_forward(people):
    ana, marco = people
    await open_path(ana, marco)

    for method, secure in (("peer.exchange", True), ("peer.forward", False)):
        with pytest.raises(RelayError, match="not part of this network"):
            await marco.bridge._receive(ana.key, method, {}, _secure=secure)


@pytest.mark.asyncio
async def test_a_knock_binds_the_other_device_only_on_the_directory_it_went_to(people):
    ana, marco = people

    sent = await ana.bridge._rpc_contact_submit(
        {
            "agent_id": ana.bridge.identity.agent_id,
            "domain": "elsewhere.example",  # not the directory Ana is connected to
            "route": contact_route_hex(marco.key),
            "introduction": "hello from another directory",
            "device_name": "ana-laptop",
        }
    )

    assert sent["status"] == "queued"
    assert ana.state.approvals == [] and ana.state.links == [] and ana.state.peer_trust == {}


@pytest.mark.asyncio
async def test_knocking_a_device_that_is_already_a_peer_changes_nothing(people):
    ana, marco = people
    await open_path(ana, marco)
    before = (ana.state.approvals, ana.state.links, ana.state.peer_trust)

    assert (await knock(ana, marco, "one more time"))["status"] == "queued"

    assert (ana.state.approvals, ana.state.links, ana.state.peer_trust) == before


@pytest.mark.asyncio
async def test_a_failed_bind_after_a_sent_knock_leaves_no_half_state(people, monkeypatch):
    ana, marco = people

    def broken(*_args, **_kwargs):
        raise RelayError("local peer approval capacity reached", "capacity")

    monkeypatch.setattr(ana.bridge.store, "grant", broken)

    sent = await knock(ana, marco)

    assert sent["status"] == "queued"  # the knock itself went out
    assert ana.state.approvals == [] and ana.state.links == []
    assert ana.state.peer_trust == {}


@pytest.mark.asyncio
async def test_leaving_withdraws_the_links_before_it_goes_offline(people, relay):
    ana, marco = people
    await open_path(ana, marco)
    backend = relay.app["relay_state"].backend

    text = await ana.bridge.commands._run("leave")

    assert text.startswith("left the network")
    assert await backend.linked_keys(ana.key) == []
    assert backend.link_declarations[ana.key][1] == frozenset()
    await until(lambda: not marco.sees(ana))


@pytest.mark.asyncio
async def test_links_are_declared_once_and_again_only_on_change_or_a_new_session(
    people, monkeypatch
):
    ana, marco = people
    posted = []
    real = ContactRequestManager.sync_links

    async def counting(self, domain, peers):
        posted.append(sorted(peers))
        return await real(self, domain, peers)

    monkeypatch.setattr(ContactRequestManager, "sync_links", counting)
    await knock(ana, marco)
    assert posted == [[marco.key]]

    for _ in range(3):
        await ana.bridge.sync_links()  # nothing changed: no traffic
    assert posted == [[marco.key]]

    await ana.bridge.sync_links(force=True)
    assert posted == [[marco.key], [marco.key]]

    ana.bridge._links_declared = None  # a new relay session looks like this
    await ana.bridge.sync_links()
    assert len(posted) == 3

    # a device with no strangers says nothing at all
    await marco.bridge.sync_links()
    assert len(posted) == 3


@pytest.mark.asyncio
async def test_a_failed_declaration_backs_off_and_a_forced_one_retries(people, monkeypatch):
    ana, marco = people
    attempts = []

    async def failing(self, _domain, peers):
        attempts.append(peers)
        raise ContactProtocolError("rate_limited")

    monkeypatch.setattr(ContactRequestManager, "sync_links", failing)
    await knock(ana, marco)
    assert len(attempts) == 1 and ana.bridge._links_failed is True

    await ana.bridge.sync_links()  # inside the back-off window
    assert len(attempts) == 1

    await ana.bridge.sync_links(force=True)  # a human action retries at once
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_nothing_is_declared_while_the_relay_is_unreachable(people, monkeypatch):
    ana, marco = people
    await open_path(ana, marco)
    posted = []

    async def counting(self, _domain, peers):
        posted.append(peers)

    monkeypatch.setattr(ContactRequestManager, "sync_links", counting)
    ana.client._state = "reconnecting"

    await ana.bridge.sync_links(force=True)

    assert posted == []
