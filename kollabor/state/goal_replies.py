"""Request-private completion mailboxes for daemon-side goal commands."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

GOAL_REPLY_PATTERN = re.compile(r"goal-[0-9a-f]{32}\Z")


def is_goal_input(content: Any) -> bool:
    """Recognize exactly the authored text used by goal admission."""
    if isinstance(content, list):
        text = "\n".join(
            part["text"] for part in content
            if isinstance(part, dict)
            and str(part.get("type") or "").strip().lower() == "text"
            and isinstance(part.get("text"), str)
        )
    else:
        text = content if isinstance(content, str) else str(content or "")
    return re.match(r"^\s*/(?:goal|goals)(?:\s+|$)", text, re.IGNORECASE) is not None


@dataclass
class _GoalReply:
    conversation_uid: str
    task: asyncio.Task
    completed_at: float | None = None


class GoalReplyCancelled(Exception):
    """The client cancelled this goal request before admission."""


class GoalReplyMailbox:
    """Own bounded private replies without putting results on DisplayTap.

    Polling never waits on a command: attach RPC requests share one ordered
    connection, so a waiting RPC would block cancellation and other commands.
    Completed replies remain readable until expiry to tolerate a lost RPC reply.
    """

    MAX_REPLIES = 256
    RETAIN_SECONDS = 600.0
    MAX_PENDING_SECONDS = 600.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._replies: dict[str, _GoalReply] = {}
        self._cancelled: dict[str, float] = {}

    def _prune(self) -> None:
        now = self._clock()
        self._replies = {
            key: reply for key, reply in self._replies.items()
            if reply.completed_at is None or now - reply.completed_at < self.RETAIN_SECONDS
        }
        self._cancelled = {
            key: when for key, when in self._cancelled.items()
            if now - when < self.RETAIN_SECONDS
        }

    def prepare(self, key: str | None = None) -> str:
        self._prune()
        key = key or f"goal-{uuid.uuid4().hex}"
        if not isinstance(key, str) or GOAL_REPLY_PATTERN.fullmatch(key) is None:
            raise ValueError("invalid goal reply id")
        if key in self._cancelled:
            raise GoalReplyCancelled()
        if key in self._replies:
            raise ValueError("goal reply id already in use")
        if len(self._replies) >= self.MAX_REPLIES:
            raise RuntimeError("too many pending goal replies")
        return key

    def register(self, uid: str, task: asyncio.Task, key: str | None = None) -> str:
        key = self.prepare(key)
        reply = _GoalReply(uid, task)
        self._replies[key] = reply
        timeout = asyncio.get_running_loop().call_later(self.MAX_PENDING_SECONDS, task.cancel)

        def completed(done: asyncio.Task) -> None:
            timeout.cancel()
            reply.completed_at = self._clock()
            if not done.cancelled():
                done.exception()  # consume abandoned exceptions without logging details

        task.add_done_callback(completed)
        return key

    def cancel(self, key: str) -> dict[str, Any]:
        self._prune()
        if not isinstance(key, str) or GOAL_REPLY_PATTERN.fullmatch(key) is None:
            return {"cancelled": False, "reason": "invalid goal reply id"}
        reply = self._replies.get(key)
        if reply is not None:
            if reply.task.done():
                return {"cancelled": False, "reason": "goal command already completed"}
            reply.task.cancel()
            return {"cancelled": True}
        if len(self._cancelled) >= self.MAX_REPLIES and key not in self._cancelled:
            return {"cancelled": False, "reason": "goal cancellation capacity reached"}
        # A Stop can arrive before the send RPC. Fence a later admission rather
        # than falling through to cancel the unrelated current model turn.
        self._cancelled[key] = self._clock()
        return {"cancelled": True, "queued": True}

    def poll(self, key: str, current_uid: str | None) -> dict[str, Any]:
        self._prune()
        reply = self._replies.get(key)
        if reply is None:
            return {"status": "missing", "message": "goal reply expired or is unavailable"}
        if current_uid != reply.conversation_uid:
            if not reply.task.done():
                reply.task.cancel()
            return {
                "status": "complete", "conversation_uid": reply.conversation_uid, "success": False,
                "message": "Conversation changed; check the goal in its original conversation.",
            }
        if not reply.task.done():
            return {"status": "pending"}
        if reply.task.cancelled():
            return {
                "status": "complete", "conversation_uid": reply.conversation_uid, "success": False,
                "message": "Goal command cancelled; check its status before retrying.",
            }
        try:
            result = reply.task.result()
        except Exception:
            return {
                "status": "complete", "conversation_uid": reply.conversation_uid, "success": False,
                "message": "Goal command failed; check its status before retrying.",
            }
        if not isinstance(result, dict):
            return {
                "status": "complete", "conversation_uid": reply.conversation_uid, "success": False,
                "message": "Goal command returned no result.",
            }
        return {"status": "complete", "conversation_uid": reply.conversation_uid, **result}
