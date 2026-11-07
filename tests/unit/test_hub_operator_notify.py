"""Regression tests for hub operator push notifications (issue #626)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.notifier import NtfyDeliveryError, NtfyNotifier
from plugins.hub.plugin import HubPlugin

DUMMY_SERVER = "https://ntfy.example.invalid"
DUMMY_TOPIC = "dummy-topic"
DUMMY_TOKEN = "dummy-token"


def _client_factory(transport: httpx.AsyncBaseTransport):
    return lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)


def _notifier(transport, **kwargs):
    return NtfyNotifier(
        server=DUMMY_SERVER,
        topic=DUMMY_TOPIC,
        token=DUMMY_TOKEN,
        client_factory=_client_factory(transport),
        **kwargs,
    )


def _hub_plugin() -> HubPlugin:
    plugin = HubPlugin(event_bus=MagicMock(), config={"plugins": {"hub": {}}})
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._relay_agent = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None), active=None
    )
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = [
        SimpleNamespace(identity="lapis")
    ]
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    plugin._route_message = AsyncMock(return_value=[])
    plugin._deliver_operator_notification = AsyncMock()
    return plugin


def test_hub_msg_xml_parser_extracts_notify_kind():
    plugin = _hub_plugin()
    response_parser = MagicMock()
    tool_executor = MagicMock()
    services = {
        "response_parser": response_parser,
        "tool_executor": tool_executor,
    }
    plugin.event_bus.get_service.side_effect = services.get

    plugin._register_pipeline_tools()

    registration = next(
        call
        for call in response_parser.register_plugin_tag.call_args_list
        if call.args[0] == "hub_msg"
    )
    pattern, extract = registration.args[1], registration.args[3]
    match = re.search(
        pattern,
        '<hub_msg to="lapis" notify_kind="decision" kind="message">ready</hub_msg>',
    )
    assert match is not None
    data = extract(match)
    assert data["target"] == "lapis"
    assert data["kind"] == "message"
    assert data["notify_kind"] == "decision"
    assert data["content"] == "ready"


def test_fallback_config_is_read_from_runtime_path(tmp_path):
    fallback = tmp_path / "config.json"
    fallback.write_text(
        json.dumps(
            {
                "ntfy_server": DUMMY_SERVER,
                "ntfy_topic": DUMMY_TOPIC,
            }
        ),
        encoding="utf-8",
    )

    notifier = NtfyNotifier.from_config({}, fallback_path=fallback)

    assert notifier is not None
    assert notifier.server == DUMMY_SERVER
    assert notifier.topic == DUMMY_TOPIC


def test_invalid_utf8_fallback_is_ignored_without_exposing_path(tmp_path, caplog):
    fallback = tmp_path / "config.json"
    fallback.write_bytes(b"\xff")

    assert NtfyNotifier.from_config({}, fallback_path=fallback) is None
    assert "UnicodeDecodeError" in caplog.text
    assert str(fallback) not in caplog.text


def test_hub_ntfy_config_overrides_fallback_and_supplies_optional_token(tmp_path):
    fallback = tmp_path / "config.json"
    fallback.write_text(
        json.dumps(
            {
                "ntfy_server": "https://fallback.example.invalid",
                "ntfy_topic": "fallback-topic",
            }
        ),
        encoding="utf-8",
    )
    config = {
        "plugins": {
            "hub": {
                "notify": {
                    "ntfy": {
                        "server": DUMMY_SERVER,
                        "topic": DUMMY_TOPIC,
                        "token": DUMMY_TOKEN,
                        "priority_map": {"decision": 4, "blocker": 5, "done": 2},
                    }
                }
            }
        }
    }

    notifier = NtfyNotifier.from_config(config, fallback_path=fallback)

    assert notifier is not None
    assert notifier.server == DUMMY_SERVER
    assert notifier.topic == DUMMY_TOPIC
    assert notifier.token == DUMMY_TOKEN
    assert notifier.priority_map["decision"] == 4
    assert notifier.priority_map["blocker"] == 5
    assert notifier.priority_map["done"] == 2


@pytest.mark.asyncio
async def test_send_uses_kind_priority_tags_title_and_trimmed_body():
    requests = []
    transport = httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(200))
    )
    notifier = _notifier(transport)

    await notifier.send(
        "  need a decision\n",
        {
            "sender": "lapis",
            "notify_kind": "decision",
            "title": "Kollab · lapis · test-host",
        },
    )

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{DUMMY_SERVER}/{DUMMY_TOPIC}"
    assert request.content.decode() == "need a decision"
    assert request.headers["Title"] == "Kollab · lapis · test-host"
    assert request.headers["Priority"] == "5"
    assert request.headers["Tags"] == "decision"
    assert request.headers["Authorization"] == f"Bearer {DUMMY_TOKEN}"


def test_relay_kind_is_not_an_ntfy_classification():
    notifier = _notifier(httpx.MockTransport(lambda request: httpx.Response(200)))

    headers = notifier._headers({"kind": "decision"}, "lapis")

    assert headers["Priority"] == "3"
    assert "Tags" not in headers


@pytest.mark.asyncio
async def test_same_sender_and_text_are_deduped_for_ten_minutes():
    requests = []
    transport = httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(200))
    )
    now = [10.0]
    notifier = _notifier(transport, monotonic=lambda: now[0])
    payload = {"sender": "lapis", "notify_kind": "blocker"}

    await notifier.send("same blocker", payload)
    await notifier.send(" same blocker ", payload)
    assert len(requests) == 1

    await notifier.send("same blocker", {**payload, "sender": "zircon"})
    assert len(requests) == 2

    now[0] += 601
    await notifier.send("same blocker", payload)
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_transient_http_error_retries_with_exponential_backoff():
    statuses = iter([503, 200])
    requests = []
    transport = httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(next(statuses)))
    )
    delays = []

    async def sleep(delay):
        delays.append(delay)

    notifier = _notifier(transport, sleep=sleep, backoff_seconds=0.25)
    await notifier.send("retry me", {"sender": "lapis"})

    assert len(requests) == 2
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_permanent_http_error_is_safe_and_does_not_retry(caplog):
    requests = []
    transport = httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(401))
    )
    notifier = _notifier(transport)

    with pytest.raises(NtfyDeliveryError) as exc_info:
        await notifier.send("do not expose config", {"sender": "lapis"})

    assert len(requests) == 1
    error_text = str(exc_info.value)
    logged_text = " ".join(record.getMessage() for record in caplog.records)
    for secret in (DUMMY_SERVER, DUMMY_TOPIC, DUMMY_TOKEN):
        assert secret not in error_text
        assert secret not in logged_text


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["operator", "BoSs"])
async def test_operator_alias_routes_unclassified_message_to_ntfy(target):
    plugin = _hub_plugin()
    dispatch_failure = MagicMock()
    plugin._record_dispatch_failure = dispatch_failure

    result = await plugin._handle_hub_msg_tool(
        {"id": "tool-1", "target": target, "content": "test"}
    )

    assert result.success
    assert "not online" not in result.output
    plugin._deliver_operator_notification.assert_awaited_once_with("test", "message")
    plugin._route_message.assert_not_awaited()
    dispatch_failure.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "runtime_name", "target"),
    [
        (
            {"plugins": {"hub": {"user_name": "configured-owner"}}},
            "system-owner",
            "CONFIGURED-OWNER",
        ),
        (
            {"plugins": {"hub": {"user_name": ""}}},
            "runtime-owner",
            "RUNTIME-OWNER",
        ),
    ],
)
async def test_configured_or_runtime_user_name_routes_to_ntfy(
    config, runtime_name, target, monkeypatch
):
    plugin = _hub_plugin()
    plugin.config = config
    monkeypatch.setenv("USER", runtime_name)
    dispatch_failure = MagicMock()
    plugin._record_dispatch_failure = dispatch_failure

    result = await plugin._handle_hub_msg_tool(
        {"id": "tool-1", "target": target, "content": "test"}
    )

    assert result.success
    plugin._deliver_operator_notification.assert_awaited_once_with("test", "message")
    plugin._route_message.assert_not_awaited()
    dispatch_failure.assert_not_called()


@pytest.mark.asyncio
async def test_agent_flag_notifies_operator_after_normal_delivery():
    plugin = _hub_plugin()
    queued = MagicMock()
    plugin._queue_operator_notification = queued

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-2",
            "target": "lapis",
            "notify_kind": "blocker",
            "content": "The test runner is blocked.",
        }
    )

    assert result.success
    plugin._route_message.assert_awaited_once()
    queued.assert_called_once_with("The test runner is blocked.", "blocker")


@pytest.mark.asyncio
async def test_dispatch_failure_queues_blocker_but_queued_delivery_does_not():
    plugin = _hub_plugin()
    queued = MagicMock()
    plugin._queue_operator_notification = queued

    plugin._record_dispatch_failure("lapis", "not online, no match")
    queued.assert_called_once_with(
        "Hub dispatch to lapis failed: not online, no match", "blocker"
    )

    queued.reset_mock()
    plugin._record_dispatch_failure("lapis", "offline, queued", queued=True)
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_rejection_routes_blocker_to_operator():
    plugin = _hub_plugin()
    plugin._route_message = AsyncMock(return_value=[("ghost", "not online, no match")])
    queued = MagicMock()
    plugin._queue_operator_notification = queued

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-rejected",
            "target": "ghost",
            "notify_kind": "decision",
            "content": "The rollout is blocked.",
        }
    )

    assert not result.success
    queued.assert_called_once_with(
        "Hub dispatch to ghost failed: not online, no match", "blocker"
    )


@pytest.mark.asyncio
async def test_notify_kind_does_not_escape_manual_relay_turn():
    plugin = _hub_plugin()
    plugin._relay_agent._turn.get = lambda: {"request_id": "opaque"}
    plugin._relay_agent.active = SimpleNamespace(
        record={"payload": {"from": "requester"}}
    )

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-3",
            "target": "operator",
            "notify_kind": "blocker",
            "content": "restricted message",
        }
    )

    assert not result.success
    plugin._deliver_operator_notification.assert_not_awaited()
    plugin._route_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_relay_question_never_notifies_operator():
    plugin = _hub_plugin()
    plugin._relay_agent._turn.get = lambda: {"request_id": "opaque"}
    plugin._relay_agent.active = SimpleNamespace(
        record={"payload": {"from": "requester"}}
    )
    queued = MagicMock()
    plugin._queue_operator_notification = queued

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-5",
            "target": "operator",
            "kind": "question",
            "notify_kind": "decision",
            "content": "May I proceed?",
        }
    )

    assert result.success
    plugin._route_message.assert_awaited_once()
    plugin._deliver_operator_notification.assert_not_awaited()
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_relay_kind_cannot_be_used_as_notification_kind():
    plugin = _hub_plugin()

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-6",
            "target": "operator",
            "kind": "decision",
            "notify_kind": "blocker",
            "content": "This must not use the relay kind namespace.",
        }
    )

    assert not result.success
    assert "kind must be" in result.error
    plugin._deliver_operator_notification.assert_not_awaited()
    plugin._route_message.assert_not_awaited()


def test_notify_channel_accepts_ntfy():
    plugin = _hub_plugin()
    plugin.config = MagicMock()

    result = plugin._notify_set_channel("ntfy")

    assert result == "notification channel set to ntfy"
    plugin.config.save_key.assert_called_once_with("plugins.hub.notify_channel", "ntfy")


@pytest.mark.asyncio
async def test_notify_test_sends_via_mocked_ntfy_backend(monkeypatch):
    plugin = _hub_plugin()
    plugin.config = {
        "plugins": {
            "hub": {
                "notify_channel": "ntfy",
                "notify": {"ntfy": {"server": DUMMY_SERVER, "topic": DUMMY_TOPIC}},
            }
        }
    }
    backend = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(
        NtfyNotifier,
        "from_config",
        classmethod(lambda cls, *_args, **_kwargs: backend),
    )

    result = await plugin._notify_test()

    assert result == "test notification sent via ntfy"
    backend.send.assert_awaited_once_with(
        "[test] agent 'koordinator' notification test",
        {"test": True, "identity": "koordinator", "notify_kind": "message"},
    )


@pytest.mark.asyncio
async def test_notify_test_redacts_ntfy_backend_error(monkeypatch):
    plugin = _hub_plugin()
    plugin.config = {
        "plugins": {
            "hub": {
                "notify_channel": "ntfy",
                "notify": {"ntfy": {"server": DUMMY_SERVER, "topic": DUMMY_TOPIC}},
            }
        }
    }
    backend = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError(DUMMY_TOKEN)))
    monkeypatch.setattr(
        NtfyNotifier,
        "from_config",
        classmethod(lambda cls, *_args, **_kwargs: backend),
    )

    result = await plugin._notify_test()

    assert result == "test notification failed via ntfy (RuntimeError)"
    assert DUMMY_TOKEN not in result


def test_notify_status_does_not_expose_ntfy_credentials():
    plugin = _hub_plugin()
    plugin.config = {
        "plugins": {
            "hub": {
                "notify": {
                    "ntfy": {
                        "server": DUMMY_SERVER,
                        "topic": DUMMY_TOPIC,
                        "token": DUMMY_TOKEN,
                    }
                }
            }
        }
    }

    status = plugin._notify_status()

    assert "operator route: ntfy configured" in status
    for secret in (DUMMY_SERVER, DUMMY_TOPIC, DUMMY_TOKEN):
        assert secret not in status


@pytest.mark.asyncio
async def test_operator_click_url_is_included_only_from_explicit_template():
    plugin = _hub_plugin()
    del plugin._deliver_operator_notification
    backend = SimpleNamespace(send=AsyncMock())
    plugin._get_operator_ntfy_notifier = lambda: backend
    plugin._operator_ntfy_click_template = (
        "https://webui.example.invalid/agents/{identity}"
    )

    sent = await plugin._deliver_operator_notification("  open this  ", "done")

    assert sent
    backend.send.assert_awaited_once()
    body, payload = backend.send.await_args.args
    assert body == "open this"
    assert payload["notify_kind"] == "done"
    assert payload["click_url"] == ("https://webui.example.invalid/agents/koordinator")


@pytest.mark.asyncio
async def test_invalid_click_template_does_not_block_notification():
    plugin = _hub_plugin()
    del plugin._deliver_operator_notification
    backend = SimpleNamespace(send=AsyncMock())
    plugin._get_operator_ntfy_notifier = lambda: backend
    plugin._operator_ntfy_click_template = "{identity.attribute}"

    sent = await plugin._deliver_operator_notification("test", "message")

    assert sent
    backend.send.assert_awaited_once_with(
        "test", {"sender": "koordinator", "notify_kind": "message"}
    )


@pytest.mark.asyncio
async def test_invalid_notify_kind_is_rejected_without_sending():
    plugin = _hub_plugin()

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "tool-4",
            "target": "operator",
            "notify_kind": "urgent",
            "content": "unknown classification",
        }
    )

    assert not result.success
    assert "notify_kind" in result.error
    plugin._deliver_operator_notification.assert_not_awaited()
    plugin._route_message.assert_not_awaited()
