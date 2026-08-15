"""Tests for the messaging-bridge singleton poll lock and 409 handling.

Root cause these guard against: the hub elects a coordinator *per project*,
so two kollab sessions in different repos each elect their own coordinator and
would each poll the same Telegram bot token -> HTTP 409 Conflict, flapping
forever and flooding the log. BridgePollLock makes the inbound poll a
machine-global singleton keyed on the token; TelegramBridge.poll() raises
BridgeConflictError on 409 so the loop backs off instead of spinning.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from plugins.hub.messaging_bridge import (
    BridgeConflictError,
    BridgePollLock,
    TelegramBridge,
)

# ---------------------------------------------------------------------------
# BridgePollLock
# ---------------------------------------------------------------------------


@pytest.fixture
def poll_locks(tmp_path):
    """Own poll locks for a test and release them on every exit path."""
    locks = []

    def create(key: str) -> BridgePollLock:
        lock = BridgePollLock(key, tmp_path)
        locks.append(lock)
        return lock

    yield create

    for lock in reversed(locks):
        lock.release()


class TestBridgePollLock:
    def test_second_holder_blocked_while_first_holds(self, poll_locks):
        a = poll_locks("telegram:tok-1")
        b = poll_locks("telegram:tok-1")

        assert a.acquire() is True
        assert a.held is True
        # Same token, second process must not get the inbox.
        assert b.acquire() is False
        assert b.held is False

    def test_release_lets_standby_take_over(self, poll_locks):
        a = poll_locks("telegram:tok-1")
        b = poll_locks("telegram:tok-1")

        assert a.acquire() is True
        assert b.acquire() is False

        # Holder exits (or fails over) -> lock frees -> standby promotes.
        a.release()
        assert a.held is False
        assert b.acquire() is True
        assert b.held is True
        b.release()

    def test_different_tokens_do_not_block(self, poll_locks):
        a = poll_locks("telegram:tok-A")
        b = poll_locks("telegram:tok-B")
        # Distinct bot tokens are independent inboxes; both may poll.
        assert a.acquire() is True
        assert b.acquire() is True
        a.release()
        b.release()

    def test_acquire_is_idempotent_while_held(self, poll_locks):
        a = poll_locks("telegram:tok-1")
        assert a.acquire() is True
        # Re-acquiring our own lock is a no-op success, not a new fd leak.
        fd = a._fd
        assert a.acquire() is True
        assert a._fd is fd
        a.release()

    def test_release_is_safe_when_not_held(self, poll_locks):
        a = poll_locks("telegram:tok-1")
        # Should not raise even though nothing was acquired.
        a.release()
        assert a.held is False

    def test_lock_filename_keyed_on_token(self, poll_locks):
        a = poll_locks("telegram:tok-A")
        b = poll_locks("telegram:tok-B")
        assert a._lock_path != b._lock_path
        assert a._lock_path.name.startswith("bridge-poll-")


# ---------------------------------------------------------------------------
# TelegramBridge.poll() 409 handling
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def bridge_with_response():
    """Own fake Telegram clients and disconnect them on every exit path."""
    bridges = []

    def create(json_payload: dict) -> TelegramBridge:
        bridge = TelegramBridge(token="123:ABC", chat_id="456")
        resp = MagicMock()
        resp.json = MagicMock(return_value=json_payload)
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        bridge._client = client
        bridges.append(bridge)
        return bridge

    yield create

    for bridge in reversed(bridges):
        await bridge.disconnect()


class TestTelegramPollConflict:
    @pytest.mark.asyncio
    async def test_409_raises_bridge_conflict(self, bridge_with_response):
        bridge = bridge_with_response(
            {
                "ok": False,
                "error_code": 409,
                "description": (
                    "Conflict: terminated by other getUpdates request; "
                    "make sure that only one bot instance is running"
                ),
            }
        )
        with pytest.raises(BridgeConflictError):
            await bridge.poll()

    @pytest.mark.asyncio
    async def test_non_409_not_ok_returns_empty(self, bridge_with_response):
        # Other not-ok responses (e.g. 400) must NOT raise -- just no messages.
        bridge = bridge_with_response(
            {"ok": False, "error_code": 400, "description": "Bad Request"}
        )
        assert await bridge.poll() == []

    @pytest.mark.asyncio
    async def test_ok_empty_result_returns_empty(self, bridge_with_response):
        bridge = bridge_with_response({"ok": True, "result": []})
        assert await bridge.poll() == []
