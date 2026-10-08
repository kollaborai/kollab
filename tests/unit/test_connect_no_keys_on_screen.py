"""Keys, receipts and relay: addresses never reach a screen a human reads.

docs/specs/agent-network-simple-flow.md sections 5, 6 and 13. One state holds
everything that used to leak (provisioned network preferences, approved peers
with and without recorded names, a pending join request, a pending knock); the
status text, the Connect screen, the hub context, hub_status and the
`kollab --hub status` network section are built from it and scanned.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kollabor.cli import _handle_cli_hub
from kollabor_config.provisioned_state import ProvisionedStateFile
from kollabor_events.data_models import ConversationMessage
from plugins.altview.connect_altview import ConnectScreenState, connect_screen_lines
from plugins.hub.device_names import key_label
from plugins.hub.dns.discovery import DiscoveryResult
from plugins.hub.plugin import CONNECT_OWNED_ELSEWHERE, HubPlugin, format_connect_help
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_commands import RelayCommands

from .test_relay_agent_bridge import (  # noqa: F401 - bridges is a fixture
    address,
    allow,
    authorize,
    bridges,
    handle,
    in_turn,
)

LEAK = re.compile(r"[0-9a-f]{32,}|ed25519:|relay:|receipt", re.IGNORECASE)

# The three ids the old `/connect status` printed for a provisioned host.
PREFERENCES = {
    "ed25519:" + "1" * 64: "kollabor.ai",
    "origin:" + "2" * 64: "kollabor.ai",
    "room:" + "3" * 64: "kollabor.ai",
}

ONLINE_NAMED = "a" * 64
ONLINE_UNNAMED = "b" * 64
OFFLINE_NAMED = "c" * 64
OFFLINE_UNNAMED = "d" * 64


def _row(name: str, device: str, key: str, state: str = "idle", task: str = "") -> dict:
    return {
        "name": name,
        "device": device,
        "handle": f"{name}@{device}",
        "state": state,
        "task": task,
        "address": f"relay:{key}:{'9' * 32}:{name}-1",
        "online": True,
        "is_coordinator": False,
        "workspace_id": "9" * 32,
    }


def _rows() -> list[dict]:
    return [
        _row("infra", "home-server", ONLINE_NAMED),
        _row("ops", key_label(ONLINE_UNNAMED), ONLINE_UNNAMED, "working", "rotating logs"),
    ]


def _bridge():
    async def remote_agents():
        return _rows()

    joins = [
        SimpleNamespace(
            enrollment_id="e" * 32,
            device_name="ana-laptop",
            device_key_fingerprint="abcd" + "0" * 56 + "ef01",
            credential_categories=("provider:openai:api_key",),
        ),
        SimpleNamespace(
            enrollment_id="f" * 32,
            device_name="",
            device_key_fingerprint="7a7a" + "0" * 56 + "1b1b",
            credential_categories=(),
        ),
    ]
    return SimpleNamespace(
        trust_level=lambda: "agents",
        device_name=lambda: "laptop-kollab",
        remote_agents=remote_agents,
        effective_trust=lambda key: "open" if key == ONLINE_UNNAMED else "agents",
        plugin=SimpleNamespace(
            _presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")
        ),
        identity=SimpleNamespace(agent_id="k1"),
        _enrollment_issuer=None,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(
                peer_devices={ONLINE_NAMED: "home-server", OFFLINE_NAMED: "laptop-kollab"},
                peer_trust={},
            )
        ),
        pending_enrollment_requests=lambda *, source_agent: joins,
    )


def _commands(tmp_path) -> RelayCommands:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commands = RelayCommands(
        workspace, state_dir=tmp_path / "relay-state", agent_bridge=_bridge()
    )
    commands.client.state.origin = "https://kollabor.ai"
    commands.client._store.save()
    commands.client._session_id = "e" * 32
    commands.client._state = "online"
    commands.client.state.approvals = [
        ONLINE_NAMED,
        ONLINE_UNNAMED,
        OFFLINE_NAMED,
        OFFLINE_UNNAMED,
    ]
    from plugins.hub.knocks import Ringing

    commands.knocks.ringing["c" * 32] = Ringing("c" * 32, "d" * 64, "stranger", "hi", {}, 9e9)
    return commands


def _hub() -> HubPlugin:
    async def remote_agents():
        return _rows()

    async def harness_context(*_args, **_kwargs):
        return []

    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(
        identity="lapis",
        agent_id="l1",
        is_coordinator=False,
        state="idle",
        current_task="",
        project="",
    )
    hub._roster = []
    hub.config = None
    hub._task_ledger = None
    hub._change_feed = None
    hub._work_queue = None
    hub._presence = SimpleNamespace(get_cached_agents=lambda: [])
    hub._relay_agent = SimpleNamespace(
        remote_agents=remote_agents,
        trust_level=lambda: "agents",
        device_name=lambda: "laptop-kollab",
        harness_context=harness_context,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={OFFLINE_NAMED: "laptop-kollab"})
        ),
        commands=SimpleNamespace(
            client=SimpleNamespace(
                state=SimpleNamespace(approvals=[ONLINE_NAMED, OFFLINE_NAMED, OFFLINE_UNNAMED]),
                status=lambda: {"state": "online"},
                peers=lambda: [],
            )
        ),
    )
    hub._relay_commands = SimpleNamespace(
        client=SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai"))
    )
    history = [ConversationMessage(role="system", content="base prompt")]
    llm_service = SimpleNamespace(conversation_history=history)
    hub.event_bus = SimpleNamespace(
        get_service=lambda name: llm_service if name == "llm_service" else None
    )
    return hub


@pytest.mark.asyncio
async def test_no_key_receipt_or_relay_address_on_any_connect_surface(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        ProvisionedStateFile, "get_network_preferences", lambda self: dict(PREFERENCES)
    )
    commands = _commands(tmp_path)
    snapshot = await commands.connect_snapshot()
    surfaces = {"connect status": await commands.format_status()}
    for width in (40, 100):
        for code_only in (False, True):
            state = ConnectScreenState(
                snapshot=snapshot,
                code="ABCD-2345",
                code_remaining=250,
                code_status="active",
                code_only=code_only,
            )
            surfaces[f"connect screen {width} {code_only}"] = "\n".join(
                connect_screen_lines(state, width)
            )
            surfaces[f"connect screen, second window {width} {code_only}"] = "\n".join(
                connect_screen_lines(replace(state, note=CONNECT_OWNED_ELSEWHERE), width)
            )
    surfaces["connect help"] = format_connect_help(show_all=True)
    # `/connect <domain>` on a directory that advertises no relay prints the
    # verified discovery summary, whose publisher id is a key.
    discovered = DiscoveryResult(
        "kollabor.ai",
        "https://kollabor.ai",
        "https://kollabor.ai/.well-known/agent-keys.json",
        "ed25519:" + "5" * 64,
        {
            "coordinator": {"designation": "kollabor", "protocols": []},
            "endpoints": {},
            "discovery": {"roles": []},
        },
    )
    surfaces["connect domain, no relay"] = await commands._attach(discovered, "", ())

    hub = _hub()
    await hub._inject_roster_context({})
    history = hub.event_bus.get_service("llm_service").conversation_history
    surfaces["hub context"] = history[0].content
    hub_status = await hub._handle_hub_status_tool({"id": "t1"})
    hub_agents = await hub._handle_hub_agents_tool({"id": "t2"})
    surfaces["hub_status"], surfaces["hub_agents"] = hub_status.output, hub_agents.output

    (tmp_path / "koordinator.json").write_text(
        json.dumps(
            {
                "identity": "koordinator",
                "pid": os.getpid(),
                "last_heartbeat": __import__("time").time(),
                "socket_path": str(tmp_path / "koordinator.sock"),
                "is_coordinator": True,
            }
        )
    )

    async def network_status(_socket_path):
        return {"type": "network_status", **await hub._handle_network_status_request()}

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_status", network_status
    ):
        await _handle_cli_hub(["status"])
    surfaces["kollab --hub status"] = capsys.readouterr().out

    # The fixtures really reach the screens, so a clean scan is not vacuous.
    status = surfaces["connect status"]
    assert "ana-laptop wants to join" in status
    assert "unknown device wants to join" in status
    assert "laptop-kollab (offline)" in status
    assert f"{key_label(OFFLINE_UNNAMED)} (offline)" in status
    assert "infra@home-server" in status
    assert "1 waiting" in surfaces["connect screen 100 False"]
    assert "laptop-kollab" in surfaces["kollab --hub status"]
    offline_line = next(
        line for line in surfaces["hub context"].splitlines() if line.startswith("offline devices:")
    )
    assert "laptop-kollab" in offline_line and key_label(OFFLINE_UNNAMED) in offline_line

    leaks = {
        name: match.group(0)
        for name, text in surfaces.items()
        if (match := LEAK.search(text))
    }
    assert leaks == {}


@pytest.mark.asyncio
async def test_manual_trust_commands_print_numbers_never_ids_or_relay_addresses(bridges):  # noqa: F811
    members, _ = bridges
    (left, *_), (right, *_) = members
    allow(left, right)
    target = await handle(left, right)

    surfaces = {"authorize": await left.command(f"authorize {target} Create proof.txt")}
    surfaces["withdraw"] = await left.command("withdraw 1")
    surfaces["send"] = await left.command(f"send {target} Create proof.txt")
    await right._tick()
    surfaces["task"] = await left.command(f"task {target} 2")
    surfaces["cancel"] = await left.command(f"cancel {target} 2")
    # The refusals print what the human typed, or nothing of the ids.
    surfaces["stale withdraw"] = await left.command("withdraw 99")
    surfaces["stale task"] = await left.command(f"task {target} 99")
    surfaces["stale answer"] = await left.command("answer 99 hi")
    surfaces["hex id"] = await left.command("withdraw " + "a" * 32)
    for usage in ("withdraw", "answer 1", f"task {target}", f"cancel {target}"):
        surfaces["usage " + usage] = await left.command(usage)

    # Non-vacuous: the numbers really are what the screens say.
    assert "request 1;" in surfaces["authorize"]
    assert surfaces["withdraw"].startswith("request 1 withdrawn")
    assert surfaces["send"].startswith(f"request 2 to {target}: ")
    assert surfaces["task"].startswith(f"request 2 on {target}: ")
    assert surfaces["cancel"].startswith(f"request 2 on {target}: ")
    leaks = {name: text for name, text in surfaces.items() if LEAK.search(text)}
    assert leaks == {}


@pytest.mark.asyncio
async def test_manual_trust_events_show_name_and_number_never_ids_or_relay_addresses(
    bridges,  # noqa: F811
):
    members, _ = bridges
    (left, left_hub, *_), (right, right_hub, right_model, _) = members
    allow(left, right)
    grant = authorize(left, right, "Create proof.txt")
    surfaces = {
        "tool result, start": (
            await left_hub._handle_hub_msg_tool(
                {
                    "id": "start",
                    "to": address(right),
                    "content": "Create proof.txt",
                    "thread_id": grant["id"],
                }
            )
        ).output
    }
    await right._tick()
    # The remote task lands on the far screen under the sender's name.
    for index, call in enumerate(right_hub._display_hub_message.call_args_list):
        message = call.args[0]
        surfaces[f"far sender {index}"] = (
            message.metadata.get("display_from") or message.from_identity
        )

    question = await in_turn(
        right_model,
        right_hub._handle_hub_msg_tool(
            {
                "id": "question",
                "to": right.active.record["payload"]["from"],
                "kind": "question",
                "content": "Which existing directory should I use?",
            }
        ),
    )
    surfaces["tool result, question"] = question.output
    payload = left.store.event(question.metadata["relay_receipt"]["id"])["payload"]
    left_hub._render_hub_box = MagicMock()
    HubPlugin._display_hub_message(left_hub, left._correlated_event_message(payload))
    sender, to, content = left_hub._render_hub_box.call_args.args
    surfaces["question box"] = f"{sender} -> {to}\n{content}"
    number = left.number("question", payload["id"])
    surfaces["answer"] = await left.command(f"answer {number} Use the workspace root.")

    assert surfaces["tool result, start"].startswith("remote request 1: ")
    assert surfaces["far sender 0"].count("@") == 1
    assert f"(answer with /connect answer {number} <text>)" in surfaces["question box"]
    assert surfaces["answer"].startswith(f"question {number} answered: ")
    leaks = {name: text for name, text in surfaces.items() if LEAK.search(text)}
    assert leaks == {}


def _joins(commands) -> list:
    return commands.agent_bridge.pending_enrollment_requests(source_agent="k1")


@pytest.mark.asyncio
async def test_arrivals_print_one_named_line_each_and_never_again(tmp_path):
    commands = _commands(tmp_path)
    lines = await commands.new_arrivals()
    assert len(lines) == 2
    assert set(lines) == {
        "ana-laptop wants to join kollabor.ai. /connect to review",
        "an unknown device wants to join kollabor.ai. /connect to review",
    }
    for line in lines:
        assert not LEAK.search(line)
        assert not re.search(r"abcd|ef01|7a7a|1b1b", line)
    assert await commands.new_arrivals() == []
    assert await commands.new_arrivals() == []


@pytest.mark.asyncio
async def test_no_notice_while_the_connect_screen_polls_and_none_after_it_closes(
    tmp_path, monkeypatch
):
    commands = _commands(tmp_path)
    await commands.connect_snapshot()  # the Connect screen's poll
    assert await commands.new_arrivals() == []
    monkeypatch.setattr("plugins.hub.relay_commands.SCREEN_OPEN_SECONDS", 0.0)
    assert await commands.new_arrivals() == []  # seen on screen, never announced later
    _joins(commands).append(
        SimpleNamespace(
            enrollment_id="9" * 32,
            device_name="ben-desktop",
            device_key_fingerprint="9" * 64,
            credential_categories=(),
        )
    )
    assert await commands.new_arrivals() == [
        "ben-desktop wants to join kollabor.ai. /connect to review"
    ]


@pytest.mark.asyncio
async def test_a_request_that_can_no_longer_be_decided_is_not_announced(tmp_path):
    commands = _commands(tmp_path)
    for row in _joins(commands):
        row.decision_available = False
    assert [line for line in await commands.new_arrivals() if "join" in line] == []


def test_a_knock_without_a_sealed_name_is_an_unknown_device_never_its_hex():
    from plugins.hub.knocks import _spoken

    key = "d" * 64
    assert _spoken(key_label(key), key) == "an unknown device"
    assert _spoken("ana-laptop", key) == "ana-laptop"


@pytest.mark.asyncio
async def test_the_relay_agent_shows_each_arrival_once_through_the_plugin(tmp_path):
    shown = []
    agent = SimpleNamespace(
        commands=_commands(tmp_path),
        plugin=SimpleNamespace(show_network_notice=shown.append),
    )
    await RelayAgentBridge._announce_arrivals(agent)
    await RelayAgentBridge._announce_arrivals(agent)
    assert len(shown) == 2


def test_show_network_notice_is_one_system_line_on_the_message_coordinator():
    shown = []
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=shown.append)
    )
    hub = HubPlugin.__new__(HubPlugin)
    hub.event_bus = SimpleNamespace(
        get_service=lambda name: renderer if name == "renderer" else None
    )
    hub.show_network_notice("ana-laptop is knocking. /connect knocks to answer (4:58)")
    assert shown == [
        [("system", "ana-laptop is knocking. /connect knocks to answer (4:58)", {"display_type": "info"})]
    ]
