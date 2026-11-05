"""The JSONL append is bound to the session it captured (#676 review P2).

``reset_session`` repoints ``session_file`` without taking the write lock,
and ``rewrite_voice_turn`` holds that lock across a ``to_thread`` — so an
append that queued behind a rewrite can wake up after the session was
replaced. Opening whatever ``session_file`` points at THEN would land the
old-session row (its ``sessionId`` was stamped at call time) inside the
new session's file — the reproduced defect on 4f5e3b32. The append binds
the file at call time and validates inside the lock before opening
anything.
"""

import asyncio
import json

from kollabor_ai.conversation_logger import KollaborConversationLogger


def test_append_queued_behind_rewrite_does_not_write_into_replaced_session(
    tmp_path,
) -> None:
    async def scenario() -> None:
        logger = KollaborConversationLogger(tmp_path)
        old_file = logger.session_file

        rewrite_running = asyncio.Event()
        release_rewrite = asyncio.Event()

        async def hold_lock_like_rewrite():
            # rewrite_voice_turn holds the write lock across its to_thread
            # rewrite; the duration is what opens the window.
            async with logger._write_lock:
                rewrite_running.set()
                await release_rewrite.wait()

        rewriter = asyncio.create_task(hold_lock_like_rewrite())
        await rewrite_running.wait()

        # The append stamps sessionId from the CURRENT session, then queues
        # behind the rewrite for the lock.
        append_task = asyncio.create_task(
            logger.log_user_message("queued mention")
        )
        await asyncio.sleep(0)

        # The session is replaced while the append is still waiting on the
        # lock (reset_session takes no lock).
        logger.reset_session("new-session-id")
        new_file = logger.session_file
        assert new_file != old_file

        release_rewrite.set()
        await rewriter
        await append_task

        # The old-session row must NOT land in the new session's file.
        new_rows = (
            [json.loads(line) for line in new_file.read_text().splitlines()]
            if new_file.exists()
            else []
        )
        assert new_rows == []

        # Control: a fresh append inside the new session writes normally.
        await logger.log_user_message("fresh turn")
        fresh_rows = [
            json.loads(line) for line in new_file.read_text().splitlines()
        ]
        assert fresh_rows[0]["message"]["content"] == "fresh turn"
        assert fresh_rows[0]["sessionId"] == "new-session-id"

    asyncio.run(scenario())


def test_append_without_reset_is_unchanged(tmp_path) -> None:
    """The binding must not disturb the ordinary path (same file at wake)."""
    async def scenario() -> None:
        logger = KollaborConversationLogger(tmp_path)
        await logger.log_user_message("ordinary turn")
        rows = [
            json.loads(line) for line in logger.session_file.read_text().splitlines()
        ]
        assert rows[0]["message"]["content"] == "ordinary turn"
        assert rows[0]["sessionId"] == logger.session_id

    asyncio.run(scenario())
