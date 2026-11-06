"""An ACP turn's output stays in the conversation it was admitted under (#596).

Resuming another conversation swaps the live manager's session id and the logger's
file in place. A turn admitted before the swap must still write its reply (finished
or interrupted) to its own conversation, never to the one resumed meanwhile. The
conversation manager and logger are real, on a temp directory; the runner is a fake
queue whose futures the test settles. No ACP process or provider is started.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor.llm.acp_bridge import ACPAdmission, ACPResult
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager


class Bus:
    def __init__(self, llm):
        self.services = {"llm_service": llm}

    def get_service(self, name):
        return self.services.get(name)


class Config(dict):
    def __init__(self, conversations_dir):
        super().__init__()
        self._conversations_dir = conversations_dir

    def get(self, key, default=None):
        return default


class FakeRunner:
    generation = 1
    session_id = "fake-session"
    binding = None
    cancel_timeout = 0.0
    supports_image_input = False

    def __init__(self):
        self._turn = 0

    def admit(self, text, *, request_id, source, thread_id=None, origin=None, images=()):
        self._turn += 1
        future = asyncio.get_running_loop().create_future()
        return ACPAdmission(
            request_id, source, thread_id, self.generation, self._turn, text, future, dict(origin or {})
        )

    def cancel_queued(self, request_id, admission=None):
        return False


class Rig:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "conversations"
        self.logger = KollaborConversationLogger(self.dir)
        self.manager = ConversationManager(Config(self.dir), self.logger)
        self.llm = SimpleNamespace(
            conversation_manager=self.manager, conversation_logger=self.logger,
            conversation_history=[], message_display_service=None,
        )
        self.orchestrator = mod.ACPOrchestrator(Bus(self.llm))
        self.orchestrator._runner = FakeRunner()
        self.a = self.manager.current_session_id

    def switch_to_b(self, user_text):
        """What resume does: same objects, new session id and log file, B's rows."""
        self.manager.save_conversation()
        self.b = "resumed-b"
        self.manager.current_session_id = self.b
        self.manager.messages = []
        self.manager.message_index = {}
        self.logger.reset_session(self.b)
        self.manager.add_message("user", user_text)
        self.llm.conversation_history.clear()

    def assistant_texts(self, session_id):
        path = self.dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        texts = []
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            if entry.get("type") == "assistant":
                texts += [i["text"] for i in entry["message"]["content"] if i.get("type") == "text"]
        return texts

    def manager_texts(self):
        return [m["content"] for m in self.manager.messages if m["role"] == "assistant"]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return Rig(tmp_path)


def start_turn(rig, text):
    before = set(rig.orchestrator._completion_tasks)
    admission = rig.orchestrator.admit(text, request_id="req-a", source="rpc")
    (task,) = rig.orchestrator._completion_tasks - before
    return admission, task


def result(admission, text):
    return ACPResult(
        admission.generation, "fake-session", text, "end_turn", (),
        {"type": "turn_end", "stopReason": "end_turn"},
        admission.turn_id, admission.request_id, admission.source, admission.thread_id,
    )


@pytest.mark.asyncio
async def test_finished_turn_after_resume_stays_in_original_conversation(rig):
    admission, task = start_turn(rig, "prompt A")
    await asyncio.sleep(0)  # let the user-row log write land in A
    await asyncio.gather(*rig.orchestrator._history_tasks)
    rig.switch_to_b("prompt B")

    admission.future.set_result(result(admission, "Alpha"))
    await asyncio.wait_for(task, 1)

    assert "Alpha" not in rig.manager_texts()
    assert "Alpha" not in rig.assistant_texts(rig.b)
    assert "Alpha" in rig.assistant_texts(rig.a)
    assert all("Alpha" not in str(m.content) for m in rig.llm.conversation_history)


@pytest.mark.asyncio
async def test_interrupted_turn_after_resume_keeps_partial_output_in_original_conversation(rig):
    admission, task = start_turn(rig, "prompt A")
    await asyncio.gather(*rig.orchestrator._history_tasks)
    key = (admission.request_id, admission.turn_id)
    observed = mod._ObservedTurn("fake-session", admission.generation)
    observed.updates.append({"kind": "text", "text": "Alpha"})
    observed.finished.set()
    rig.orchestrator._observed_turns[key] = observed
    rig.switch_to_b("prompt B")

    admission.future.cancel()  # the old runner is retired
    await asyncio.wait_for(task, 1)

    assert "Alpha" not in rig.manager_texts()
    assert "Alpha" not in rig.assistant_texts(rig.b)
    assert "Alpha" in rig.assistant_texts(rig.a)


@pytest.mark.asyncio
async def test_turn_in_the_active_conversation_still_records_there(rig):
    admission, task = start_turn(rig, "prompt A")
    await asyncio.gather(*rig.orchestrator._history_tasks)

    admission.future.set_result(result(admission, "Alpha"))
    await asyncio.wait_for(task, 1)

    assert rig.manager_texts() == ["Alpha"]
    assert rig.assistant_texts(rig.a) == ["Alpha"]
