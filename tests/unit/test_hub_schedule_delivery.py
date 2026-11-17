"""Schedule delivery, stage 2 (#612, PR #653): start, queue, new instance.

When a schedule fires on the device its agent runs on, delivery picks one of
three paths: the agent is not running and is started (the prompt is its
initial message, relaunched through kollab_argv()), a busy agent either gets
the message queued behind its current turn (when_busy=queue) or a second
instance of the same identity runs just this fire (when_busy=new_instance),
and an idle agent simply gets the message. Every path carries the schedule
id so replies and run records line up. These tests fake the probes (sockets,
presence states, process starts); the tmux spec covers one real start.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.schedule_delivery import (  # noqa: E402
    DeliveryProbes,
    deliver,
)
from plugins.hub.schedules import ScheduleRow  # noqa: E402


def _row(**overrides) -> ScheduleRow:
    base = dict(
        id="s1",
        agents=["lapis"],
        prompt="check the queue",
        interval_seconds=600.0,
        anchor=1000.0,
    )
    base.update(overrides)
    return ScheduleRow(**base)


class Fakes:
    """Programmable probes: who is online, who is busy, what got launched."""

    def __init__(self, sockets=None, states=None, online_after_start=True):
        self.sockets = dict(sockets or {})  # agent -> socket path
        self.states = dict(states or {})  # agent -> presence state
        self.online_after_start = online_after_start
        self.launches = []  # LaunchSpec records
        self.sends = []  # (socket, content, metadata)

    def probes(self) -> DeliveryProbes:
        async def send(socket, content, metadata):
            self.sends.append((socket, content, metadata))
            return True

        def launch(spec):
            self.launches.append(spec)
            return self.online_after_start

        return DeliveryProbes(
            find_socket=lambda agent: self.sockets.get(agent),
            state_of=lambda agent: self.states.get(agent, "idle"),
            launch=launch,
            send=send,
        )


# ------------------------------------------------------------- not running


@pytest.mark.asyncio
async def test_not_running_agent_is_started_with_the_prompt():
    fakes = Fakes()  # no socket for lapis
    ok, error = await deliver(_row(), "lapis", 1600.0, 1600.1, fakes.probes())

    assert ok, error
    assert len(fakes.launches) == 1
    spec = fakes.launches[0]
    assert spec.identity == "lapis"
    assert spec.initial_message  # the prompt is the first message
    assert "[schedule s1]" in spec.initial_message
    assert "check the queue" in spec.initial_message
    assert spec.exit_after_turn is False  # a kept agent stays up
    # Nothing to send: the start itself delivers.
    assert fakes.sends == []


@pytest.mark.asyncio
async def test_launch_uses_kollab_argv_detached_as():
    fakes = Fakes()
    await deliver(_row(), "lapis", 1600.0, 1600.1, fakes.probes())
    spec = fakes.launches[0]
    argv = spec.argv
    assert "--detached" in argv
    assert argv[argv.index("--as") + 1] == "lapis"
    # The binary this kollab runs from, never whichever PATH finds first.
    assert str(argv[0]).endswith("kollab") or "kollab" in str(argv[0])


@pytest.mark.asyncio
async def test_launch_failure_reports_and_does_not_send():
    fakes = Fakes(online_after_start=False)
    ok, error = await deliver(_row(), "lapis", 1600.0, 1600.1, fakes.probes())

    assert not ok
    assert "online" in error
    assert fakes.sends == []


# ---------------------------------------------------------- running, idle


@pytest.mark.asyncio
async def test_idle_agent_gets_the_message_on_its_socket():
    fakes = Fakes(
        sockets={"lapis": "/tmp/lapis.sock"}, states={"lapis": "idle"}
    )
    ok, error = await deliver(_row(), "lapis", 1600.0, 1600.1, fakes.probes())

    assert ok, error
    assert fakes.launches == []
    socket, content, metadata = fakes.sends[0]
    assert socket == "/tmp/lapis.sock"
    assert "[schedule s1]" in content
    assert "check the queue" in content
    assert metadata.get("schedule_id") == "s1"
    # Manual wake: content heuristics may not park a schedule fire, and a
    # busy target queues it behind the current turn.
    assert metadata.get("manual_wake") is True


# --------------------------------------------------------- running, busy


@pytest.mark.asyncio
async def test_busy_agent_queue_sends_the_message():
    fakes = Fakes(
        sockets={"lapis": "/tmp/lapis.sock"}, states={"lapis": "working"}
    )
    row = _row(when_busy="queue")
    ok, error = await deliver(row, "lapis", 1600.0, 1600.1, fakes.probes())

    assert ok, error
    assert fakes.launches == []
    assert len(fakes.sends) == 1  # the target buffers it for after the turn


@pytest.mark.asyncio
async def test_busy_agent_new_instance_launches_a_run_copy():
    fakes = Fakes(
        sockets={"lapis": "/tmp/lapis.sock"}, states={"lapis": "working"}
    )
    row = _row(when_busy="new_instance")
    ok, error = await deliver(row, "lapis", 1600.0, 1600.1, fakes.probes())

    assert ok, error
    assert fakes.sends == []  # the original chat is left alone
    spec = fakes.launches[0]
    assert spec.identity == "lapis"
    assert "[schedule s1]" in spec.initial_message
    assert spec.exit_after_turn is True  # exists only for this run


# ------------------------------------------------------------- misc shapes


@pytest.mark.asyncio
async def test_agent_at_this_device_is_treated_locally():
    fakes = Fakes(sockets={"lapis": "/tmp/lapis.sock"})
    ok, error = await deliver(
        _row(), "lapis@this-mac", 1600.0, 1600.1, fakes.probes()
    )
    assert ok, error
    assert fakes.sends[0][0] == "/tmp/lapis.sock"


@pytest.mark.asyncio
async def test_send_refusal_is_reported():
    fakes = Fakes(sockets={"lapis": "/tmp/lapis.sock"})

    async def refuse(socket, content, metadata):
        return False

    probes = fakes.probes()
    probes.send = refuse
    ok, error = await deliver(_row(), "lapis", 1600.0, 1600.1, probes)

    assert not ok
    assert error
