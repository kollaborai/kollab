"""External client cancellation owns one request, including its terminal event."""

import asyncio
import json
import queue

import pytest

from kollabor.llm.client_turns import ClientTurnBroker
from kollabor_tui.display_tap import DisplayTap, publish_semantic
from kollabor_tui.turn_context import current_turn


def drain(events):
    result = []
    while True:
        try:
            result.append(events.get_nowait())
        except queue.Empty:
            return result


async def eventually(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 2)


class ControlledNative:
    def __init__(self, tap, *, mesh=False):
        self.tap = tap
        self.is_processing = mesh
        self.owner = "mesh" if mesh else None
        self.admissions = []
        self.cancellations = []
        self.finishes = {}
        self.tasks = []

    async def submit_human_input(self, text, *, request_id, **kwargs):
        assert not self.is_processing
        self.admissions.append((request_id, text))
        self.owner = request_id
        self.is_processing = True
        finish = asyncio.get_running_loop().create_future()
        self.finishes[request_id] = finish

        async def run():
            reason = await finish
            self.is_processing = False
            self.owner = None
            publish_semantic(self.tap, "turn_complete", stop_reason=reason)

        self.tasks.append(asyncio.create_task(run()))
        return {"status": "queued"}

    def cancel_current_request(self):
        self.cancellations.append(self.owner)

    async def close(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class Bus:
    def __init__(self, tap):
        self.tap = tap

    def get_service(self, name):
        return self.tap if name == "display_tap" else None


@pytest.mark.asyncio
async def test_queued_cancel_releases_capacity_without_cancelling_mesh():
    tap = DisplayTap()
    events = tap.subscribe("observer")
    native = ControlledNative(tap, mesh=True)
    broker = ClientTurnBroker(native, Bus(tap))
    try:
        for index in range(10):
            assert broker.submit(str(index), f"client-{index}")["accepted"]
        await eventually(lambda: broker.waiting is not None)
        assert not broker.submit("overflow", "overflow")["accepted"]
        assert (await broker.cancel("client-7"))["cancelled"]
        assert (await broker.cancel("client-0"))["cancelled"]
        assert broker.submit("replacement-a", "replacement-a")["accepted"]
        assert broker.submit("replacement-b", "replacement-b")["accepted"]
        assert not broker.submit("overflow", "overflow")["accepted"]
        terminals = [event for event in drain(events) if event["type"] == "turn_complete"]
        assert {event["request_id"] for event in terminals} == {"client-7", "client-0"}
        assert all(event["queued"] and event["input_source"] == "acp_client" for event in terminals)
        assert native.owner == "mesh" and native.cancellations == []
        assert native.admissions == []
        assert not (await broker.cancel("client-7"))["cancelled"]
    finally:
        await broker.close()
        await native.close()
    assert broker.pending.empty()
    await asyncio.wait_for(broker.pending.join(), 1)


@pytest.mark.asyncio
async def test_active_cancel_and_late_native_completion_publish_once():
    tap = DisplayTap()
    events = tap.subscribe("observer")
    native = ControlledNative(tap)
    broker = ClientTurnBroker(native, Bus(tap))
    try:
        assert broker.submit("active", "active")["accepted"]
        await eventually(lambda: "active" in native.finishes)
        assert broker.submit("next", "next")["accepted"]
        assert (await broker.cancel("active"))["cancelled"]
        assert native.cancellations == ["active"]
        native.finishes["active"].set_result("end_turn")
        await eventually(lambda: "next" in native.finishes)
        native.finishes["next"].set_result("end_turn")
        await asyncio.wait_for(broker.worker, 2)
        terminals = [event for event in drain(events) if event["type"] == "turn_complete"]
        assert [(event["request_id"], event["stop_reason"]) for event in terminals] == [
            ("active", "cancelled"), ("next", "end_turn")]
        assert terminals[0]["queued"] is False
        assert all("_client_completion" not in event for event in tap.get_snapshot())
        json.dumps(tap.get_snapshot())
        assert native.admissions == [("active", "active"), ("next", "next")]
    finally:
        await broker.close()
        await native.close()


@pytest.mark.asyncio
async def test_cancel_during_input_hooks_never_admits_native_work():
    tap = DisplayTap()
    events = tap.subscribe("observer")
    entered = asyncio.Event()
    finished = asyncio.Event()

    class DelayedNative(ControlledNative):
        async def submit_human_input(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

    native = DelayedNative(tap)
    broker = ClientTurnBroker(native, Bus(tap))
    try:
        broker.submit("pending hooks", "pending")
        await asyncio.wait_for(entered.wait(), 2)
        await broker.cancel("pending")
        await asyncio.wait_for(finished.wait(), 2)
        await asyncio.wait_for(broker.worker, 2)
        assert native.admissions == []
        assert [event["stop_reason"] for event in drain(events) if event["type"] == "turn_complete"] == ["cancelled"]
    finally:
        await broker.close()
        await native.close()


@pytest.mark.asyncio
async def test_completed_owner_cannot_cancel_a_later_mesh_turn():
    tap = DisplayTap()
    native = ControlledNative(tap)
    broker = ClientTurnBroker(native, Bus(tap))
    try:
        broker.submit("one", "one")
        await eventually(lambda: "one" in native.finishes)
        native.finishes["one"].set_result("end_turn")
        await native.tasks[0]
        native.owner = "mesh"
        native.is_processing = True
        assert not (await broker.cancel("one"))["cancelled"]
        assert native.cancellations == []
    finally:
        await broker.close()
        await native.close()


def test_completion_gate_preserves_other_owners_and_fills_null_fields():
    tap = DisplayTap()
    events = tap.subscribe("observer")
    gate = {"request_id": "client", "turn_id": 3, "completed": False, "cancelled": False}
    token = current_turn.set({"request_id": "client", "turn_id": 3,
                              "input_source": "acp_client", "_client_completion": gate})
    try:
        tap.publish({"type": "turn_complete", "request_id": "mesh", "turn_id": 8})
        assert gate["completed"] is False
        tap.publish({"type": "turn_complete", "request_id": None, "turn_id": None})
        tap.publish({"type": "turn_complete"})
        tap.publish({"type": "token", "text": "late observation"})
    finally:
        current_turn.reset(token)
    actual = drain(events)
    assert [event["type"] for event in actual] == ["turn_complete", "turn_complete", "token"]
    assert actual[0]["request_id"] == "mesh"
    assert actual[1]["request_id"] == "client" and actual[1]["turn_id"] == 3
    assert all("_client_completion" not in event for event in actual)


def test_explicit_permission_owner_wins_over_current_context():
    tap = DisplayTap()
    events = tap.subscribe("observer")
    token = current_turn.set({"request_id": "current", "turn_id": 2})
    try:
        tap.publish({"type": "permission_denied", "request_id": None,
                     "details": {"request_id": "older", "turn_id": 1}})
    finally:
        current_turn.reset(token)
    assert events.get_nowait()["request_id"] == "older"
