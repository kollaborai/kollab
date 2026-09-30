"""hub_cron_add can target another machine (docs/specs/agent-network-simple-flow.md, section 7).

The tag reads `to`, the native tool takes `to`, and a job aimed at an
`agent@device` fires through the router hub_msg uses. These tests drive the real
tool handlers and the real router against a fake relay bridge, and once against
the real two-bridge fixture from test_relay_agent_bridge.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_definitions.hub import hub_cron_add
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_ai.response_parser import ResponseParser
from plugins.hub.models import MessageScope
from plugins.hub.plugin import HubCronJob, HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_state import RelayError
from tests.unit.test_relay_agent_bridge import handle as roster_handle
from tests.unit.test_relay_network_trust import set_trust, warm_directory

DEVICE = "alzan-prod-home"
PEER = f"infra@{DEVICE}"
UNKNOWN_WORDING = "unknown agent@device: run /connect status to see who is online"
NOT_AUTHORIZED = "peer has no conversation grant for this agent"
REFUSED = {
    "id": "f" * 32,
    "state": "rejected",
    "reason": "not_authorized",
    "duplicate": False,
}
# Keys, receipt ids and relay: addresses never reach a human, the model or a log.
LEAK = re.compile(r"[0-9a-f]{32,}|ed25519:|relay:|receipt", re.IGNORECASE)


def _key(device: str) -> str:
    """A stable 64 hex peer key for a device name."""
    return hashlib.sha256(device.encode()).hexdigest()


def _address(handle: str) -> str:
    name, device = handle.split("@")
    return f"relay:{_key(device)}:{'9' * 32}:{name}-1"


def _plugin(
    *,
    on_roster=(),
    known=(DEVICE,),
    unnamed=(),
    online=True,
    receipt=None,
    turn=None,
    address=_address,
) -> tuple[HubPlugin, list]:
    """A plugin on a network whose roster is `on_roster` (handles), whose
    approved peers with a recorded name are `known`, and whose approved peers
    without one are `unnamed`. The relay answers a send with `receipt` (or
    raises it, when it is an exception); `online=False` is a relay that cannot
    be reached."""
    sent: list = []
    rows = []
    for handle in on_roster:
        name, device = handle.split("@")
        rows.append({"name": name, "device": device, "address": address(handle)})
    approved = {
        *(_key(device) for device in known),
        *(_key(device) for device in unnamed),
        *(_key(row["device"]) for row in rows),
    }
    network = SimpleNamespace(
        remote_agents=AsyncMock(return_value=rows if online else []),
        device_name=lambda: "mac-kollab",
        _owner_call=AsyncMock(
            return_value={"state": "online" if online else "offline"}
        ),
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(
                peer_devices={_key(device): device for device in known},
                approvals=sorted(approved),
            )
        ),
    )

    async def send(target, content, **kwargs):
        sent.append({"address": target, "content": content, **kwargs})
        if isinstance(receipt, Exception):
            raise receipt
        return receipt or {"id": "a" * 32, "state": "queued", "duplicate": False}

    relay = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: turn),
        # The real producers of the unknown-agent wording and of the answer to
        # "does this network know that device", against this roster.
        resolve_handle=partial(RelayAgentBridge.resolve_handle, network),
        device_unknown=partial(RelayAgentBridge.device_unknown, network),
        send=send,
    )
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._relay_agent = relay
    plugin._vault = None
    plugin._task_ledger = None
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()
    plugin._trace_delivery = MagicMock()  # the real one appends to ~/.kollab
    return plugin, sent


def _job(plugin, target, message="check the tunnel", job_id="abc12345") -> HubCronJob:
    job = HubCronJob(
        id=job_id,
        target=target,
        message=message,
        interval_seconds=300,
        next_fire=0,
    )
    plugin._hub_cron_jobs.append(job)
    return job


async def _add(plugin, **fields):
    return await plugin._handle_hub_cron_add_tool(
        {"id": "t1", "interval": "5m", "message": "check the tunnel", **fields}
    )


def _registered_tag():
    parser = SimpleNamespace(register_plugin_tag=MagicMock())
    executor = SimpleNamespace(register_plugin_handler=MagicMock())
    bus = MagicMock()
    bus.get_service.side_effect = {
        "response_parser": parser,
        "tool_executor": executor,
    }.get
    HubPlugin(event_bus=bus)._register_pipeline_tools()
    (call,) = [
        c
        for c in parser.register_plugin_tag.call_args_list
        if c.args[0] == "hub_cron_add"
    ]
    return call.args[1], call.args[3]


CRON_PATTERN, CRON_EXTRACT = _registered_tag()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# --- adding a job ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        '<hub_cron_add to="infra@alzan-prod-home" interval="5m">check the tunnel</hub_cron_add>',
        '<hub_cron_add interval="5m" to="infra@alzan-prod-home">check the tunnel</hub_cron_add>',
        "<hub_cron_add to='infra@alzan-prod-home' interval='5m'>check the tunnel</hub_cron_add>",
        "<hub_cron_add interval='5m' to='infra@alzan-prod-home'>check the tunnel</hub_cron_add>",
        # The roster help text used to say target="name", which nothing read.
        '<hub_cron_add target="infra@alzan-prod-home" interval="5m">check the tunnel</hub_cron_add>',
    ],
)
async def test_the_tag_carries_to_into_the_job_in_any_order_and_quote_style(text):
    plugin, _ = _plugin()
    match = CRON_PATTERN.search(text)

    result = await plugin._handle_hub_cron_add_tool({"id": "t1", **CRON_EXTRACT(match)})

    assert result.success, result.error
    (job,) = plugin._hub_cron_jobs
    assert (job.target, job.interval_seconds, job.message) == (
        PEER,
        300,
        "check the tunnel",
    )
    assert result.output == f"cron job {job.id} created: every 5m -> {PEER}"


def test_a_shuffled_cron_tag_is_run_and_hidden_not_left_on_screen():
    parser = ResponseParser()
    parser.register_plugin_tag(
        "hub_cron_add", CRON_PATTERN, "hub_cron_add", CRON_EXTRACT
    )

    parsed = parser.parse_response(
        'Scheduling. <hub_cron_add interval="1h" to="infra@alzan-prod-home">rotate logs</hub_cron_add> Done.'
    )
    tools = parser.get_all_tools(parsed)

    assert [(t["type"], t["target"], t["interval"]) for t in tools] == [
        ("hub_cron_add", PEER, "1h")
    ]
    assert "hub_cron_add" not in parsed["content"]


@pytest.mark.asyncio
async def test_the_native_call_takes_to_and_the_definition_offers_it():
    plugin, _ = _plugin()

    result = await _add(plugin, to=PEER, input={"to": PEER})

    assert result.success and plugin._hub_cron_jobs[0].target == PEER
    schema = hub_cron_add.to_json_schema()["parameters"]
    assert "to" in schema["properties"] and "to" not in schema["required"]
    assert "to" in hub_cron_add.xml_attributes
    assert any(
        'to="infra@alzan-prod-home"' in example for example in hub_cron_add.examples
    )


@pytest.mark.asyncio
async def test_without_to_the_job_targets_the_agent_itself():
    plugin, _ = _plugin()

    tag = await plugin._handle_hub_cron_add_tool(
        {
            "id": "t1",
            **CRON_EXTRACT(
                CRON_PATTERN.search('<hub_cron_add interval="5m">tick</hub_cron_add>')
            ),
        }
    )
    native = await plugin._handle_hub_cron_add_tool(
        {"id": "t2", "interval": "5m", "message": "tick"}
    )

    assert tag.success and native.success
    assert [job.target for job in plugin._hub_cron_jobs] == [
        "koordinator",
        "koordinator",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields, wording",
    [({"interval": "soon"}, "bad interval:"), ({"message": ""}, "usage:")],
)
async def test_a_job_the_scheduler_refuses_is_reported_as_a_failure(fields, wording):
    plugin, _ = _plugin()

    result = await _add(plugin, to="lapis", **fields)

    assert not result.success and result.error.startswith(wording)
    assert plugin._hub_cron_jobs == []


@pytest.mark.asyncio
async def test_a_tag_without_an_interval_is_answered_by_the_handler_not_left_on_screen():
    plugin, _ = _plugin()
    match = CRON_PATTERN.search('<hub_cron_add to="lapis">tick</hub_cron_add>')

    result = await plugin._handle_hub_cron_add_tool({"id": "t1", **CRON_EXTRACT(match)})

    assert not result.success and "interval" in result.error
    assert plugin._hub_cron_jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "infra@",
        "@alzan-prod-home",
        "a@b@c",
        "infra@alzan_prod_home",
        "infra@alzan prod home",
        "relay:" + "a" * 64 + ":" + "9" * 32 + ":infra-1",
    ],
)
async def test_a_malformed_target_is_refused_when_the_job_is_added(target):
    plugin, sent = _plugin()

    result = await _add(plugin, to=target)

    assert not result.success and "bad target" in result.error
    assert plugin._hub_cron_jobs == []
    assert LEAK.search(result.error) is None  # the refusal never echoes an address
    assert sent == []


@pytest.mark.asyncio
async def test_an_offline_remote_is_accepted_and_its_handle_normalized():
    plugin, sent = _plugin(on_roster=[], known=())

    result = await _add(plugin, to="Infra@Alzan-Prod-Home")

    assert result.success, result.error
    assert plugin._hub_cron_jobs[0].target == PEER
    assert result.output.endswith(f"-> {PEER}")
    assert sent == []  # nothing goes out until the job fires


def test_the_slash_command_checks_the_target_the_same_way():
    plugin, _ = _plugin()

    assert plugin._handle_cron_command("add infra@ 5m hello").startswith("bad target")
    assert plugin._hub_cron_jobs == []
    assert plugin._handle_cron_command(f"add {PEER.upper()} 5m hello").endswith(
        f"-> {PEER}"
    )


@pytest.mark.asyncio
async def test_a_remote_task_cannot_schedule_a_message_to_another_device():
    plugin, _ = _plugin(turn="task-1")

    refused = await _add(plugin, to=PEER)

    assert not refused.success and "remote task" in refused.error
    assert plugin._hub_cron_jobs == []
    assert (await _add(plugin, to="lapis")).success  # this guard is about the network


# --- firing a job ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_firing_to_a_local_agent_is_unchanged(caplog):
    caplog.set_level(logging.INFO, logger="plugins.hub.plugin")
    plugin, sent = _plugin()
    plugin._route_message = AsyncMock(return_value=[("lapis", "in waiting state")])
    job = _job(plugin, "lapis", "run the tests")

    assert await plugin._fire_cron_job(job) is False

    message = plugin._route_message.await_args.args[0]
    assert (message.to, message.from_identity, message.content, message.scope) == (
        "lapis",
        "hub-cron",
        "[cron abc12345] run the tests",
        MessageScope.DIRECT.value,
    )
    assert sent == [] and job.last_error == ""  # a local refusal stays quiet, as before
    plugin._display_outgoing_message.assert_not_called()
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_firing_to_the_sender_and_to_all_is_unchanged():
    plugin, _ = _plugin()
    plugin._route_message = AsyncMock(return_value=[])
    plugin._on_message_received = AsyncMock()

    await plugin._fire_cron_job(_job(plugin, "koordinator", job_id="self00001"))
    await plugin._fire_cron_job(_job(plugin, "all", job_id="all000001"))

    self_msg, all_msg = [call.args[0] for call in plugin._route_message.await_args_list]
    assert self_msg.to == "koordinator"
    plugin._on_message_received.assert_awaited_once_with(
        self_msg
    )  # skipped by the router
    assert (all_msg.to, all_msg.scope) == ("*", MessageScope.BROADCAST.value)


@pytest.mark.asyncio
async def test_firing_to_an_agent_at_device_goes_through_the_network_send(caplog):
    caplog.set_level(logging.INFO, logger="plugins.hub.plugin")
    plugin, sent = _plugin(on_roster=[PEER])
    job = _job(plugin, PEER)

    assert await plugin._fire_cron_job(job) is False

    assert [(s["address"], s["content"], s["kind"]) for s in sent] == [
        (_address(PEER), "[cron abc12345] check the tunnel", "message")
    ]
    assert job.last_error == ""
    plugin._display_outgoing_message.assert_called_once_with(
        PEER, "[cron abc12345] check the tunnel"
    )
    assert f"hub cron fired: abc12345 -> {PEER}" in caplog.text
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_a_cron_fire_and_a_hub_msg_take_the_same_send():
    plugin, sent = _plugin(on_roster=[PEER])

    await plugin._handle_hub_msg_tool({"id": "m1", "to": PEER, "content": "hello"})
    await plugin._fire_cron_job(_job(plugin, PEER))

    by_hub_msg, by_cron = sent
    assert by_cron.keys() == by_hub_msg.keys()
    assert (by_cron["address"], by_cron["kind"]) == (
        by_hub_msg["address"],
        by_hub_msg["kind"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, reason",
    [
        # The agent is not there, but the device is known (a recorded peer that
        # is offline, or a device on the roster): the job is kept.
        ({"on_roster": []}, UNKNOWN_WORDING),
        ({"on_roster": [f"ops@{DEVICE}"], "known": ()}, UNKNOWN_WORDING),
        # The relay cannot be reached: nothing can be called unknown.
        ({"on_roster": [], "known": (), "online": False}, UNKNOWN_WORDING),
        ({"on_roster": [PEER], "receipt": REFUSED}, NOT_AUTHORIZED),
        (
            {"on_roster": [PEER], "receipt": TimeoutError("took too long")},
            "send failed (TimeoutError)",
        ),
    ],
)
async def test_an_undeliverable_fire_is_logged_with_its_reason_and_the_job_is_kept(
    kwargs, reason, caplog
):
    caplog.set_level(logging.INFO, logger="plugins.hub.plugin")
    plugin, _ = _plugin(**kwargs)
    job = _job(plugin, PEER)

    assert await plugin._fire_cron_job(job) is False

    assert _warnings(caplog) == [f"hub cron abc12345 not delivered to {PEER}: {reason}"]
    assert "fired" not in caplog.text and "took too long" not in caplog.text
    assert job.last_error == reason
    assert f"last fire failed: {reason}" in plugin._cron_list()
    plugin._display_outgoing_message.assert_not_called()


@pytest.mark.asyncio
async def test_a_fire_that_gets_through_clears_the_last_failure():
    plugin, _ = _plugin(on_roster=[PEER])
    job = _job(plugin, PEER)
    job.last_error = UNKNOWN_WORDING

    await plugin._fire_cron_job(job)

    assert job.last_error == ""
    assert "last fire failed" not in plugin._cron_list()


@pytest.mark.asyncio
async def test_a_job_for_a_device_not_on_the_network_is_dropped_with_a_logged_reason(
    caplog,
):
    caplog.set_level(logging.INFO, logger="plugins.hub.plugin")
    plugin, sent = _plugin(on_roster=[], known=())
    job = _job(plugin, PEER)

    assert await plugin._fire_cron_job(job) is True

    assert _warnings(caplog) == [
        f"hub cron abc12345 dropped: device {DEVICE} is not on this network ({UNKNOWN_WORDING})"
    ]
    assert sent == []


@pytest.mark.asyncio
async def test_an_unnamed_device_that_is_online_does_not_shield_a_typo():
    """It is accounted for by its roster row, so it cannot be the missing one."""
    plugin, _ = _plugin(on_roster=["ops@other-box"], known=())

    assert await plugin._fire_cron_job(_job(plugin, "infra@nowhere")) is True


@pytest.mark.asyncio
async def test_an_unnamed_offline_device_might_be_the_target_so_the_job_is_kept(caplog):
    """A third device this one never named: its handle cannot be told from a typo."""
    plugin, _ = _plugin(on_roster=[], known=(DEVICE,), unnamed=("laptop",))
    job = _job(plugin, "infra@nowhere")

    assert await plugin._fire_cron_job(job) is False

    assert job.last_error == UNKNOWN_WORDING
    assert "dropped" not in caplog.text


@pytest.mark.asyncio
async def test_a_handle_on_this_device_is_dropped_because_the_router_cannot_resolve_it():
    plugin, _ = _plugin(on_roster=[], known=())

    assert await plugin._fire_cron_job(_job(plugin, "lapis@mac-kollab")) is True


@pytest.mark.asyncio
async def test_with_no_network_bridge_the_job_is_kept_and_says_why(caplog):
    plugin, _ = _plugin()
    plugin._relay_agent = None
    job = _job(plugin, PEER)

    assert await plugin._fire_cron_job(job) is False

    reason = "the network is not connected on this device: run /connect status"
    assert _warnings(caplog) == [f"hub cron abc12345 not delivered to {PEER}: {reason}"]


@pytest.mark.asyncio
async def test_a_device_check_that_fails_keeps_the_job():
    plugin, _ = _plugin(on_roster=[], known=())
    plugin._relay_agent.device_unknown = AsyncMock(
        side_effect=RuntimeError("unreadable")
    )

    assert await plugin._fire_cron_job(_job(plugin, PEER)) is False


@pytest.mark.asyncio
async def test_the_bridge_calls_no_device_unknown_while_it_cannot_reach_the_relay():
    def bridge(owner_call):
        return SimpleNamespace(
            _owner_call=owner_call,
            remote_agents=AsyncMock(return_value=[]),
            _state=lambda: SimpleNamespace(
                state=SimpleNamespace(peer_devices={}, approvals=[])
            ),
        )

    unreachable = (
        AsyncMock(side_effect=RelayError("workspace relay owner is starting")),
        AsyncMock(return_value={"state": "offline"}),
    )
    for owner_call in unreachable:
        assert (
            await RelayAgentBridge.device_unknown(bridge(owner_call), "gone") is False
        )

    online = bridge(AsyncMock(return_value={"state": "online"}))
    assert await RelayAgentBridge.device_unknown(online, "gone") is True


async def _one_tick(plugin, monkeypatch):
    """Run _cron_loop through exactly one 10 second beat."""
    beats = 0

    async def sleep(_seconds):
        nonlocal beats
        beats += 1
        if beats > 1:
            raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr("plugins.hub.plugin.asyncio.sleep", sleep)
        await plugin._cron_loop()


@pytest.mark.asyncio
async def test_one_tick_drops_the_dead_job_keeps_the_rest_and_does_not_retry_every_beat(
    monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="plugins.hub.plugin")
    plugin, sent = _plugin(on_roster=[PEER], known=(DEVICE,), receipt=TimeoutError("x"))
    routed = []
    real_route = plugin._route_message

    async def route(message):
        routed.append(message.to)
        return [] if message.to == "lapis" else await real_route(message)

    plugin._route_message = route
    _job(plugin, "infra@nowhere", job_id="gone0001")
    failing = _job(plugin, PEER, job_id="fail0001")
    local = _job(plugin, "lapis", job_id="local001")

    await _one_tick(plugin, monkeypatch)

    assert routed == ["infra@nowhere", PEER, "lapis"]  # a failing job stops nobody
    assert plugin._hub_cron_jobs == [failing, local]
    assert failing.last_error == "send failed (TimeoutError)"
    for job in (failing, local):
        assert (
            job.next_fire > 0
        )  # rescheduled a full interval out, not retried at the next beat
    assert [w.split(":")[0] for w in _warnings(caplog)] == [
        "hub cron gone0001 dropped",
        "hub cron fail0001 not delivered to infra@alzan-prod-home",
    ]


@pytest.mark.asyncio
async def test_a_one_shot_job_is_still_removed_after_firing(monkeypatch):
    plugin, _ = _plugin()
    plugin._route_message = AsyncMock(return_value=[])
    job = _job(plugin, "lapis")
    job.recurring = False

    await _one_tick(plugin, monkeypatch)

    assert plugin._hub_cron_jobs == []


# --- what a human, the model or a log may see -------------------------------------


@pytest.mark.asyncio
async def test_no_key_address_or_receipt_reaches_the_job_output_a_log_or_the_screen(
    caplog,
):
    caplog.set_level(logging.DEBUG, logger="plugins.hub.plugin")

    seen: list[str] = []
    for kwargs in (
        {"on_roster": [PEER]},
        {"on_roster": []},
        {"on_roster": [], "known": ()},
        {"on_roster": [PEER], "receipt": REFUSED},
        {"on_roster": [PEER], "receipt": {**REFUSED, "state": "failed"}},
        {"on_roster": [PEER], "receipt": RuntimeError("relay:" + "a" * 64)},
    ):
        plugin, _ = _plugin(**kwargs)
        added = await _add(plugin, to=PEER)
        await plugin._fire_cron_job(plugin._hub_cron_jobs[0])
        seen += [
            added.output,
            plugin._cron_list(),
            str(plugin._display_outgoing_message.call_args_list),
        ]
    seen += [record.getMessage() for record in caplog.records]

    assert len([text for text in seen if text]) >= 18  # the scan saw real text
    assert [text for text in seen if LEAK.search(text)] == []


# --- against the real bridge --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cron_fire_reaches_the_other_device_over_the_real_bridge(bridges):
    members, _ = bridges
    (left, left_hub, _, _), (right, _, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "open")
    left_hub._trace_delivery = MagicMock()
    target = await roster_handle(left, right)

    added = await _add(left_hub, to=target)
    (job,) = left_hub._hub_cron_jobs
    dropped = await left_hub._fire_cron_job(job)
    await right._tick()

    assert added.success, added.error
    assert dropped is False and job.last_error == ""
    assert (
        f"[cron {job.id}] check the tunnel"
        in right_model.conversation_history[-1].content
    )


@pytest.mark.asyncio
async def test_model_text_becomes_a_job_that_reaches_the_other_device(
    bridges, monkeypatch
):
    """The whole path with nothing stubbed but the wire: reply text, real
    parser, real executor, the job, the cron tick, the bridge, the other side."""
    members, _ = bridges
    (left, left_hub, _, left_bus), (right, _, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "open")
    left_hub._trace_delivery = MagicMock()
    parser = ResponseParser()
    executor = ToolExecutor(None, left_bus, workspace=left.workspace)
    left_bus.register_service("response_parser", parser)
    left_bus.register_service("tool_executor", executor)
    left_hub._register_pipeline_tools()
    target = await roster_handle(left, right)

    parsed = parser.parse_response(
        f"Scheduling it. <hub_cron_add interval='1h' to='{target}'>check the tunnel</hub_cron_add>"
    )
    (tool,) = parser.get_all_tools(parsed)
    result = await executor.execute_tool(tool)
    (job,) = left_hub._hub_cron_jobs
    job.next_fire = 0  # due now
    await _one_tick(left_hub, monkeypatch)
    await right._tick()

    assert result.success, result.error
    assert job.last_error == "" and job.next_fire > 0  # sent, and rescheduled
    assert result.output == f"cron job {job.id} created: every 1h -> {target}"
    assert LEAK.search(result.output) is None
    assert (
        f"[cron {job.id}] check the tunnel"
        in right_model.conversation_history[-1].content
    )


@pytest.mark.asyncio
async def test_a_device_is_gone_only_when_no_bound_name_or_roster_row_matches(bridges):
    members, _ = bridges
    (left, left_hub, _, _), (right, *_) = members
    await warm_directory(left)
    (row,) = await left.remote_agents()
    left.bind_peer_device(right.commands.client.public_key, "laptop-kollab")

    left.commands.client._state = "online"

    assert not await left_hub._cron_device_gone(row["device"])  # on the live roster
    assert not await left_hub._cron_device_gone("laptop-kollab")  # a recorded name
    assert await left_hub._cron_device_gone("never-joined")
    assert await left_hub._cron_device_gone(left.device_name())  # not another device
    left.commands.client._state = "offline"
    assert not await left_hub._cron_device_gone("never-joined")  # relay unreachable
