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
from unittest.mock import patch

import pytest

from kollabor.cli import _handle_cli_hub
from kollabor_config.provisioned_state import ProvisionedStateFile
from kollabor_events.data_models import ConversationMessage
from plugins.altview.connect_altview import ConnectScreenState, connect_screen_lines
from plugins.hub.contact_requests import PendingContactRequest, PrivateMessage
from plugins.hub.device_names import key_label
from plugins.hub.dns.discovery import DiscoveryResult
from plugins.hub.plugin import CONNECT_OWNED_ELSEWHERE, HubPlugin, format_connect_help
from plugins.hub.relay_commands import RelayCommands

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
        _row("infra", "alzan-prod-home", ONLINE_NAMED),
        _row("ops", key_label(ONLINE_UNNAMED), ONLINE_UNNAMED, "working", "rotating logs"),
    ]


def _bridge():
    async def remote_agents():
        return _rows()

    joins = [
        SimpleNamespace(
            enrollment_id="e" * 32,
            device_name="ana-laptop",
            device_key_fingerprint="4d04" + "0" * 56 + "9f2e",
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
        device_name=lambda: "mac-kollab",
        remote_agents=remote_agents,
        effective_trust=lambda key: "open" if key == ONLINE_UNNAMED else "agents",
        plugin=SimpleNamespace(
            _presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")
        ),
        identity=SimpleNamespace(agent_id="k1"),
        _enrollment_issuer=None,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(
                peer_devices={ONLINE_NAMED: "alzan-prod-home", OFFLINE_NAMED: "laptop-kollab"},
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

    async def pending_knocks(_domain):
        return [PendingContactRequest("c" * 32, "d" * 64, 0, PrivateMessage("hi"), "stranger")]

    commands.pending_contact_requests = pending_knocks
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
        device_name=lambda: "mac-kollab",
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
    assert "infra@alzan-prod-home" in status
    assert "1 waiting" in surfaces["connect screen 100 False"]
    assert "mac-kollab" in surfaces["kollab --hub status"]
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
