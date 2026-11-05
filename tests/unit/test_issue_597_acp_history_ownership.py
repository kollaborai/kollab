"""Repeated caller request IDs keep their own history parent and completion (#597).

Synthetic admissions and hand-resolved results only: no ACP process, harness or
provider is started. The runner is a fake queue whose futures the test settles.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor.llm.acp_bridge import ACPAdmission, ACPResult


class Bus:
    def __init__(self, llm):
        self.services = {"llm_service": llm}

    def get_service(self, name):
        return self.services.get(name)


class Manager:
    """The part of ConversationManager the orchestrator writes through."""

    def __init__(self):
        self.messages = []
        self.fail_on_text = None

    def add_message(self, role, content, parent_uuid=None, metadata=None):
        if content == self.fail_on_text:
            raise OSError("history write failed")
        row = {"uuid": str(uuid.uuid4()), "role": role, "content": content, "parent": parent_uuid}
        self.messages.append(row)
        return row["uuid"]

    def save_conversation(self):
        pass

    def row(self, role, content):
        return next(m for m in self.messages if m["role"] == role and m["content"] == content)


class FakeRunner:
    """Queues admissions in order; the test decides when each future resolves."""

    generation = 1
    session_id = "fake-session"
    binding = None
    cancel_timeout = 0.0
    supports_image_input = False

    def __init__(self):
        self.queue = []
        self._turn = 0

    def admit(self, text, *, request_id, source, thread_id=None, origin=None, images=()):
        self._turn += 1
        future = asyncio.get_running_loop().create_future()
        admission = ACPAdmission(
            request_id, source, thread_id, self.generation, self._turn, text, future, dict(origin or {})
        )
        self.queue.append(admission)
        return admission

    def cancel_queued(self, request_id, admission=None):
        for queued in list(self.queue):
            matched = queued is admission if admission is not None else queued.request_id == request_id
            if matched:
                self.queue = [item for item in self.queue if item is not queued]
                if not queued.future.done():
                    queued.future.cancel()
                return True
        return False


def reply(admission, text):
    return ACPResult(
        admission.generation, "fake-session", text, "end_turn", (),
        {"type": "turn_end", "stopReason": "end_turn"},
        admission.turn_id, admission.request_id, admission.source, admission.thread_id,
    )


@pytest.fixture
def rig():
    manager = Manager()
    llm = SimpleNamespace(
        conversation_manager=manager, conversation_logger=None,
        conversation_history=[], message_display_service=None,
    )
    orchestrator = mod.ACPOrchestrator(Bus(llm))
    orchestrator._runner = FakeRunner()
    return orchestrator, manager


def start_turn(orchestrator, text, **kwargs):
    """Admit one turn and return it with the completion task that admission created."""
    before = set(orchestrator._completion_tasks)
    admission = orchestrator.admit(text, **kwargs)
    (task,) = orchestrator._completion_tasks - before
    return admission, task


async def settle(task):
    await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_repeated_request_ids_keep_each_reply_under_its_own_user_row(rig):
    orchestrator, manager = rig
    first, first_task = start_turn(orchestrator, "first input", request_id="same", source="rpc")
    second, second_task = start_turn(orchestrator, "second input", request_id="same", source="rpc")

    first.future.set_result(reply(first, "reply to first"))
    await settle(first_task)
    second.future.set_result(reply(second, "reply to second"))
    await settle(second_task)

    first_user = manager.row("user", "first input")
    second_user = manager.row("user", "second input")
    assert manager.row("assistant", "reply to first")["parent"] == first_user["uuid"]
    assert manager.row("assistant", "reply to second")["parent"] == second_user["uuid"]


@pytest.mark.asyncio
async def test_out_of_order_completion_keeps_each_history_parent(rig):
    orchestrator, manager = rig
    first, first_task = start_turn(orchestrator, "first input", request_id="same", source="rpc")
    second, second_task = start_turn(orchestrator, "second input", request_id="same", source="rpc")

    second.future.set_result(reply(second, "reply to second"))
    await settle(second_task)
    first.future.set_result(reply(first, "reply to first"))
    await settle(first_task)

    assert manager.row("assistant", "reply to first")["parent"] == manager.row("user", "first input")["uuid"]
    assert manager.row("assistant", "reply to second")["parent"] == manager.row("user", "second input")["uuid"]


@pytest.mark.asyncio
async def test_failed_second_admission_does_not_cancel_the_queued_first(rig):
    orchestrator, manager = rig
    first, first_task = start_turn(orchestrator, "first input", request_id="same", source="rpc")
    manager.fail_on_text = "second input"
    second, second_task = start_turn(orchestrator, "second input", request_id="same", source="rpc")
    await settle(second_task)

    # The rejected admission must leave the earlier queued admission in place.
    assert first in orchestrator._runner.queue
    assert second.future.done() and second.future.exception() is not None

    first.future.set_result(reply(first, "reply to first"))
    await settle(first_task)
    assert manager.row("assistant", "reply to first")["parent"] == manager.row("user", "first input")["uuid"]
