"""Tests for the hub email bridge: new mail wakes an agent.

The bridge runs against the scripted IMAP server in
tests/tmux/lib/fake_imap.py, so the real ``imaplib`` client (IDLE, UID
SEARCH, BODY.PEEK fetch) is exercised end to end. No real mailbox, keyring
or ~/.kollab is touched.
"""

import asyncio
import imaplib
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.hub import email_bridge
from plugins.hub.email_bridge import EmailBridge, body_text
from plugins.hub.messaging_bridge import IncomingMessage

_FAKE = Path(__file__).resolve().parents[1] / "tmux" / "lib" / "fake_imap.py"
_spec = importlib.util.spec_from_file_location("fake_imap", _FAKE)
fake_imap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_imap)
_mail = fake_imap.mail


@pytest.fixture
def imap_server(monkeypatch):
    servers = []

    def start(gmail: bool = True):
        server = fake_imap.start(gmail)
        box = server.box
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        port = server.server_address[1]
        monkeypatch.setattr(
            email_bridge.imaplib,
            "IMAP4_SSL",
            lambda host, timeout=None: imaplib.IMAP4("127.0.0.1", port, timeout=timeout),
        )
        return box

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _bridge(search: str = "") -> EmailBridge:
    return EmailBridge("me@example.com", "app-pass", host="imap.example.com", search=search)


@pytest.mark.asyncio
async def test_old_mail_never_wakes_and_inbox_opens_read_only(imap_server):
    box = imap_server()
    bridge = _bridge()
    assert await bridge.connect() is True
    try:
        assert any(c.upper().startswith("EXAMINE") for c in box.commands)  # readonly select
        task = asyncio.create_task(bridge.poll())
        await asyncio.to_thread(box.idling.wait, 5)
        assert not task.done()  # nothing new: it waits in IDLE
        box.deliver(_mail("Game night invite", "Want to come Thursday?"))
        found = await asyncio.wait_for(task, 5)
    finally:
        await bridge.disconnect()
    assert [m.sender_id for m in found] == ["3"]
    text = found[0].text
    assert "Subject: Game night invite" in text
    assert "Kelly <kelly@example.com>" in text
    assert "Want to come Thursday?" in text
    assert "untrusted content" in text
    assert any("BODY.PEEK[]" in c for c in box.commands)  # stays unread


@pytest.mark.asyncio
async def test_mail_between_polls_is_found_without_idle(imap_server):
    box = imap_server()
    bridge = _bridge()
    await bridge.connect()
    try:
        box.deliver(_mail("Missed ding", "arrived while not idling"))
        box.arrived.clear()
        found = await asyncio.wait_for(bridge.poll(), 5)
        assert [m.sender_id for m in found] == ["3"]
        assert not any(c.upper() == "IDLE" for c in box.commands)
    finally:
        await bridge.disconnect()


@pytest.mark.asyncio
async def test_gmail_filter_uses_x_gm_raw(imap_server):
    box = imap_server(gmail=True)
    bridge = _bridge(search="category:primary")
    await bridge.connect()
    try:
        box.deliver(_mail("hi", "body"))
        box.arrived.clear()
        found = await asyncio.wait_for(bridge.poll(), 5)
    finally:
        await bridge.disconnect()
    assert len(found) == 1
    assert any('X-GM-RAW "category:primary"' in c for c in box.commands)


@pytest.mark.asyncio
async def test_filter_ignored_on_non_gmail_server(imap_server):
    box = imap_server(gmail=False)
    bridge = _bridge(search="category:primary")
    await bridge.connect()
    try:
        box.deliver(_mail("hi", "body"))
        box.arrived.clear()
        found = await asyncio.wait_for(bridge.poll(), 5)
    finally:
        await bridge.disconnect()
    assert len(found) == 1
    assert not any("X-GM-RAW" in c for c in box.commands)


@pytest.mark.asyncio
async def test_disconnect_ends_a_poll_blocked_in_idle(imap_server):
    box = imap_server()
    bridge = _bridge()
    await bridge.connect()
    worker = asyncio.get_running_loop().run_in_executor(None, bridge._poll_sync)
    await asyncio.to_thread(box.idling.wait, 5)
    started = time.monotonic()
    await bridge.disconnect()
    # The worker thread must not sit out the rest of its IDLE.
    assert await asyncio.wait_for(worker, 3) == []
    assert time.monotonic() - started < 3
    assert await bridge.poll() == []


@pytest.mark.asyncio
async def test_bad_login_reports_failure(monkeypatch):
    def refuse(host, timeout=None):
        raise ConnectionRefusedError("no server")

    monkeypatch.setattr(email_bridge.imaplib, "IMAP4_SSL", refuse)
    assert await _bridge().connect() is False


def test_html_only_mail_is_stripped_to_text():
    from email import message_from_bytes
    from email.policy import default

    raw = _mail("x", "<html><style>p{}</style><p>See you&nbsp;<b>Thursday</b></p></html>", html=True)
    text = body_text(message_from_bytes(raw, policy=default))
    assert "Thursday" in text
    assert "<" not in text and "p{}" not in text


def test_long_body_is_capped():
    from email import message_from_bytes
    from email.policy import default

    text = body_text(message_from_bytes(_mail("x", "a" * 5000), policy=default))
    assert len(text) < email_bridge.BODY_LIMIT + 10


# --- hub wiring --------------------------------------------------------------


def _plugin(identity: str, coordinator: bool, config: dict):
    from plugins.hub.plugin import HubPlugin

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity=identity, is_coordinator=coordinator)
    plugin.config = SimpleNamespace(get=lambda key, default=None: config.get(key, default))
    return plugin


@pytest.mark.parametrize(
    "target, identity, coordinator, runs",
    [
        ("lapis", "lapis", False, True),
        ("lapis", "ruby", True, False),
        ("", "ruby", True, True),
        ("", "lapis", False, False),
    ],
)
def test_watcher_runs_on_the_target_agent_only(target, identity, coordinator, runs):
    plugin = _plugin(
        identity,
        coordinator,
        {"plugins.hub.email_enabled": True, "plugins.hub.email_target_agent": target},
    )
    assert plugin._runs_email_watch() is runs


def test_watcher_off_when_disabled():
    plugin = _plugin("lapis", True, {"plugins.hub.email_enabled": False})
    assert plugin._runs_email_watch() is False


@pytest.mark.asyncio
async def test_new_mail_is_a_direct_message_to_the_agent(caplog):
    plugin = _plugin("lapis", False, {})
    plugin._on_message_received = AsyncMock()
    with caplog.at_level("INFO"):
        await plugin._handle_bridge_incoming(
            IncomingMessage(platform="email", sender_id="7", sender_name="email", text="New email"), "lapis"
        )
    assert "uid 7" in caplog.text and "New email" not in caplog.text
    hub_msg = plugin._on_message_received.await_args.args[0]
    assert hub_msg.to == "lapis"
    assert hub_msg.scope == "direct"
    assert hub_msg.from_agent == "bridge:email"
    assert hub_msg.content == "New email"


# --- messaging bridge (Telegram) target --------------------------------------


@pytest.mark.parametrize(
    "target, identity, coordinator, runs",
    [
        ("lapis", "lapis", False, True),
        ("lapis", "ruby", True, False),
        ("", "ruby", True, True),
        ("", "lapis", False, False),
    ],
)
def test_bridge_runs_on_the_target_agent_only(target, identity, coordinator, runs):
    plugin = _plugin(
        identity,
        coordinator,
        {"plugins.hub.bridge_enabled": True, "plugins.hub.bridge_target_agent": target},
    )
    assert plugin._runs_inbox("bridge") is runs


@pytest.mark.asyncio
async def test_bridge_message_for_another_agent_is_routed_to_it():
    # A bridge turned on with /hub bridge on in ruby, target lapis: ruby's own
    # handler only shows a message for lapis as observed, so it must be sent on.
    plugin = _plugin("ruby", True, {})
    plugin._on_message_received = AsyncMock()
    plugin._route_message = AsyncMock(return_value=[])
    await plugin._handle_bridge_incoming(
        IncomingMessage(platform="telegram", sender_id="7", sender_name="Marco", text="ping"), "lapis"
    )
    plugin._on_message_received.assert_not_awaited()
    hub_msg = plugin._route_message.await_args.args[0]
    assert (hub_msg.to, hub_msg.scope, hub_msg.content) == ("lapis", "direct", "ping")


def _history_plugin(history):
    plugin = _plugin("lapis", False, {})
    llm = SimpleNamespace(conversation_history=history)
    plugin.event_bus = SimpleNamespace(get_service=lambda name: llm if name == "llm_service" else None)
    return plugin


def _user(metadata=None):
    return SimpleNamespace(role="user", metadata=metadata)


@pytest.mark.parametrize(
    "history, answers",
    [
        ([_user({"bridge_platform": "telegram"})], True),
        ([_user({"bridge_platform": "telegram"}), SimpleNamespace(role="assistant", metadata=None)], True),
        ([_user({"bridge_platform": "telegram"}), _user({"hub": True})], False),
        ([_user()], False),
        ([], False),
    ],
)
def test_answers_bridge_message_reads_the_newest_user_message(history, answers):
    assert _history_plugin(history)._answers_bridge_message() is answers
