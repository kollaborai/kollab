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

from plugins.hub.messaging_bridge import (
    BridgeConflictError,
    BridgePollLock,
    TelegramBridge,
)

# ---------------------------------------------------------------------------
# BridgePollLock
# ---------------------------------------------------------------------------


class TestBridgePollLock:
    def test_second_holder_blocked_while_first_holds(self, tmp_path):
        a = BridgePollLock("telegram:tok-1", tmp_path)
        b = BridgePollLock("telegram:tok-1", tmp_path)

        assert a.acquire() is True
        assert a.held is True
        # Same token, second process must not get the inbox.
        assert b.acquire() is False
        assert b.held is False

    def test_release_lets_standby_take_over(self, tmp_path):
        a = BridgePollLock("telegram:tok-1", tmp_path)
        b = BridgePollLock("telegram:tok-1", tmp_path)

        assert a.acquire() is True
        assert b.acquire() is False

        # Holder exits (or fails over) -> lock frees -> standby promotes.
        a.release()
        assert a.held is False
        assert b.acquire() is True
        assert b.held is True
        b.release()

    def test_different_tokens_do_not_block(self, tmp_path):
        a = BridgePollLock("telegram:tok-A", tmp_path)
        b = BridgePollLock("telegram:tok-B", tmp_path)
        # Distinct bot tokens are independent inboxes; both may poll.
        assert a.acquire() is True
        assert b.acquire() is True
        a.release()
        b.release()

    def test_acquire_is_idempotent_while_held(self, tmp_path):
        a = BridgePollLock("telegram:tok-1", tmp_path)
        assert a.acquire() is True
        # Re-acquiring our own lock is a no-op success, not a new fd leak.
        fd = a._fd
        assert a.acquire() is True
        assert a._fd is fd
        a.release()

    def test_release_is_safe_when_not_held(self, tmp_path):
        a = BridgePollLock("telegram:tok-1", tmp_path)
        # Should not raise even though nothing was acquired.
        a.release()
        assert a.held is False

    def test_lock_filename_keyed_on_token(self, tmp_path):
        a = BridgePollLock("telegram:tok-A", tmp_path)
        b = BridgePollLock("telegram:tok-B", tmp_path)
        assert a._lock_path != b._lock_path
        assert a._lock_path.name.startswith("bridge-poll-")


# ---------------------------------------------------------------------------
# TelegramBridge.poll() 409 handling
# ---------------------------------------------------------------------------


def _bridge_with_response(json_payload: dict) -> TelegramBridge:
    """Build a TelegramBridge whose client returns the given getUpdates JSON."""
    bridge = TelegramBridge(token="123:ABC", chat_id="456")
    resp = MagicMock()
    resp.json = MagicMock(return_value=json_payload)
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    bridge._client = client
    return bridge


class TestTelegramPollConflict:
    @pytest.mark.asyncio
    async def test_409_raises_bridge_conflict(self):
        bridge = _bridge_with_response(
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
    async def test_non_409_not_ok_returns_empty(self):
        # Other not-ok responses (e.g. 400) must NOT raise -- just no messages.
        bridge = _bridge_with_response(
            {"ok": False, "error_code": 400, "description": "Bad Request"}
        )
        assert await bridge.poll() == []

    @pytest.mark.asyncio
    async def test_ok_empty_result_returns_empty(self):
        bridge = _bridge_with_response({"ok": True, "result": []})
        assert await bridge.poll() == []
