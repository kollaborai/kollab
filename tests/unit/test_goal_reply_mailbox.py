"""The private goal reply boundary never waits or crosses conversations."""

import asyncio

import pytest

from kollabor.state.goal_replies import GoalReplyCancelled, GoalReplyMailbox, is_goal_input


@pytest.mark.asyncio
async def test_replies_are_isolated_repeatable_and_bound_to_conversation():
    mailbox = GoalReplyMailbox()
    first, second = asyncio.get_running_loop().create_future(), asyncio.get_running_loop().create_future()
    async def wait(future):
        return await future
    aid = mailbox.register("a", asyncio.create_task(wait(first)))
    bid = mailbox.register("a", asyncio.create_task(wait(second)))
    assert aid != bid and mailbox.poll(aid, "a") == {"status": "pending"}
    second.set_result({"success": True, "message": "private B"})
    await asyncio.sleep(0)
    assert mailbox.poll(aid, "a") == {"status": "pending"}
    assert mailbox.poll(bid, "a")["message"] == "private B"
    assert mailbox.poll(bid, "a")["message"] == "private B"
    assert "private B" not in repr(mailbox.poll(bid, "other"))
    first.set_result({"success": True, "message": "private A"})
    await asyncio.sleep(0)
    assert mailbox.poll(aid, "a")["message"] == "private A"


@pytest.mark.asyncio
async def test_cancel_and_failure_give_a_safe_terminal_result():
    mailbox = GoalReplyMailbox()
    async def blocked():
        await asyncio.Event().wait()
    task = asyncio.create_task(blocked())
    key = mailbox.register("a", task)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert mailbox.poll(key, "a")["status"] == "complete"
    assert "cancelled" in mailbox.poll(key, "a")["message"]
    async def broken():
        raise RuntimeError("private exception")
    task = asyncio.create_task(broken())
    key = mailbox.register("a", task)
    await asyncio.gather(task, return_exceptions=True)
    assert "private exception" not in repr(mailbox.poll(key, "a"))


def test_private_history_recording_does_not_publish_goal_recovery_id():
    from types import SimpleNamespace
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor_tui.display_tap import DisplayTap

    tap = DisplayTap()
    llm = LLMService.__new__(LLMService)
    llm.event_bus = SimpleNamespace(get_service=lambda name: tap if name == "display_tap" else None)
    llm.conversation_manager = None
    llm.conversation_history = []
    llm._queue_processor = None
    llm._add_conversation_message("user", "/goal retry private-id inspect", publish_user_event=False)
    assert llm.conversation_history[0].content.endswith("private-id inspect")
    assert tap.get_snapshot() == []
    llm._add_conversation_message("user", "ordinary message")
    assert tap.get_snapshot()[0]["text"] == "ordinary message"


def test_old_client_detection_uses_authored_goal_text():
    assert is_goal_input([{"type": "image", "image": "data:image/png;base64,aA=="},
                          {"type": " TEXT ", "text": " /GOALS\tpending"}])
    assert not is_goal_input("/goalkeeper")
    assert not is_goal_input([{"type": "image", "image": "/goal show"}])


@pytest.mark.asyncio
async def test_reply_and_early_cancel_capacity_expire_without_losing_live_tasks():
    now = [0.0]
    mailbox = GoalReplyMailbox(clock=lambda: now[0])
    mailbox.MAX_REPLIES = 1
    key = "goal-" + "a" * 32
    assert mailbox.cancel(key)["queued"]
    with pytest.raises(GoalReplyCancelled):
        mailbox.prepare(key)
    assert not mailbox.cancel("goal-" + "b" * 32)["cancelled"]
    now[0] += mailbox.RETAIN_SECONDS
    assert mailbox.prepare(key) == key
    task = asyncio.create_task(asyncio.Event().wait())
    mailbox.register("a", task, key)
    now[0] += mailbox.RETAIN_SECONDS * 2
    assert mailbox.poll(key, "a")["status"] == "pending"
    with pytest.raises(RuntimeError, match="too many"):
        mailbox.prepare()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # completion callback records the retention start
    now[0] += mailbox.RETAIN_SECONDS
    assert mailbox.poll(key, "a")["status"] == "missing"
