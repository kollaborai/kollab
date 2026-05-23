"""Tests for notification pipeline — HubNotifier, backends, and auto-detection.

Covers: TelegramNotifier, WebhookNotifier, HubNotifier channel detection,
backend construction, idle threshold logic, cooldown debouncing, and
the full notification loop.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from plugins.hub.notifier import (
    HubNotifier,
    TelegramNotifier,
    WebhookNotifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> dict:
    """Build a config dict with hub notification settings."""
    hub_cfg = {
        "notify_enabled": True,
        "notify_idle_threshold": 300,
        "notify_cooldown": 1800,
    }
    hub_cfg.update(overrides)
    return {"plugins": {"hub": hub_cfg}}


def _make_notifier(
    config: dict | None = None,
    identity: str = "lapis",
    last_activity: float | None = None,
    state: str = "idle",
) -> HubNotifier:
    """Create a HubNotifier with sensible defaults."""
    if config is None:
        config = _make_config()
    if last_activity is None:
        last_activity = time.time() - 600  # 10 min ago by default
    return HubNotifier(
        config=config,
        get_identity=lambda: identity,
        get_last_activity=lambda: last_activity,
        get_state=lambda: state,
    )


# ---------------------------------------------------------------------------
# TelegramNotifier tests
# ---------------------------------------------------------------------------


class TestTelegramNotifier:
    @pytest.mark.asyncio
    async def test_send_calls_telegram_api(self):
        notifier = TelegramNotifier(token="123:ABC", chat_id="456")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await notifier.send("test message", {"key": "value"})

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://api.telegram.org/bot123:ABC/sendMessage"
            body = call_args[1]["json"]
            assert body["chat_id"] == "456"
            assert body["text"] == "test message"
            assert body["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_raises_on_http_error(self):
        notifier = TelegramNotifier(token="123:ABC", chat_id="456")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await notifier.send("test", {})

    def test_api_url_format(self):
        notifier = TelegramNotifier(token="tok:en", chat_id="chat1")
        assert notifier.api_url == "https://api.telegram.org/bottok:en/sendMessage"


# ---------------------------------------------------------------------------
# WebhookNotifier tests
# ---------------------------------------------------------------------------


class TestWebhookNotifier:
    @pytest.mark.asyncio
    async def test_send_posts_json(self):
        notifier = WebhookNotifier(url="https://hooks.example.com/notify")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await notifier.send("alert!", {"level": "high"})

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://hooks.example.com/notify"
            body = call_args[1]["json"]
            assert body["text"] == "alert!"
            assert body["payload"]["level"] == "high"

    @pytest.mark.asyncio
    async def test_send_raises_on_http_error(self):
        notifier = WebhookNotifier(url="https://hooks.example.com/notify")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.ConnectError):
                await notifier.send("test", {})


# ---------------------------------------------------------------------------
# Channel auto-detection tests
# ---------------------------------------------------------------------------


class TestDetectChannel:
    def test_explicit_channel_wins(self):
        config = _make_config(notify_channel="telegram", notify_telegram_token="tok")
        notifier = _make_notifier(config)
        assert notifier.detect_channel() == "telegram"

    def test_telegram_auto_detected_from_token(self):
        config = _make_config(notify_telegram_token="123:ABC", notify_telegram_chat_id="456")
        notifier = _make_notifier(config)
        assert notifier.detect_channel() == "telegram"

    def test_webhook_auto_detected_from_url(self):
        config = _make_config(notify_url="https://hooks.example.com/notify")
        notifier = _make_notifier(config)
        assert notifier.detect_channel() == "webhook"

    def test_telegram_priority_over_webhook(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_url="https://hooks.example.com/notify",
        )
        notifier = _make_notifier(config)
        assert notifier.detect_channel() == "telegram"

    def test_no_credentials_returns_none(self):
        config = _make_config()
        notifier = _make_notifier(config)
        assert notifier.detect_channel() is None

    def test_empty_explicit_returns_none(self):
        config = _make_config(notify_channel="")
        notifier = _make_notifier(config)
        assert notifier.detect_channel() is None


# ---------------------------------------------------------------------------
# Backend construction tests
# ---------------------------------------------------------------------------


class TestBuildBackend:
    def test_telegram_backend_built(self):
        config = _make_config(
            notify_channel="telegram",
            notify_telegram_token="123:ABC",
            notify_telegram_chat_id="456",
        )
        notifier = _make_notifier(config)
        assert isinstance(notifier._backend, TelegramNotifier)
        assert notifier._backend.token == "123:ABC"
        assert notifier._backend.chat_id == "456"

    def test_webhook_backend_built(self):
        config = _make_config(
            notify_channel="webhook",
            notify_url="https://hooks.example.com/notify",
        )
        notifier = _make_notifier(config)
        assert isinstance(notifier._backend, WebhookNotifier)
        assert notifier._backend.url == "https://hooks.example.com/notify"

    def test_no_channel_no_backend(self):
        config = _make_config()
        notifier = _make_notifier(config)
        assert notifier._backend is None

    def test_telegram_missing_token_no_backend(self):
        config = _make_config(
            notify_channel="telegram",
            notify_telegram_token="",
            notify_telegram_chat_id="456",
        )
        notifier = _make_notifier(config)
        assert notifier._backend is None

    def test_webhook_missing_url_no_backend(self):
        config = _make_config(notify_channel="webhook", notify_url="")
        notifier = _make_notifier(config)
        assert notifier._backend is None

    def test_unknown_channel_no_backend(self):
        config = _make_config(notify_channel="carrier_pigeon")
        notifier = _make_notifier(config)
        assert notifier._backend is None

    def test_auto_detect_telegram_backend(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
        )
        notifier = _make_notifier(config)
        assert isinstance(notifier._backend, TelegramNotifier)


# ---------------------------------------------------------------------------
# Idle threshold and notification logic tests
# ---------------------------------------------------------------------------


class TestCheckAndNotify:
    @pytest.mark.asyncio
    async def test_notifies_when_idle_beyond_threshold(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,  # 10 min idle
            state="idle",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()

        mock_backend.send.assert_called_once()
        call_args = mock_backend.send.call_args
        assert "lapis" in call_args[0][0]
        assert call_args[0][1]["identity"] == "lapis"
        assert call_args[0][1]["idle_minutes"] >= 10

    @pytest.mark.asyncio
    async def test_skips_when_not_idle_enough(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 60,  # only 1 min idle
            state="idle",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()
        mock_backend.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_working(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="working",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()
        mock_backend.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_booting(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="booting",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()
        mock_backend.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_connecting(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="connecting",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()
        mock_backend.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_identity(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
        )
        notifier = _make_notifier(
            config,
            identity="",
            last_activity=time.time() - 600,
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        await notifier._check_and_notify()
        mock_backend.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_backend(self):
        config = _make_config(notify_idle_threshold=300)
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
        )
        # _backend is None because no credentials configured
        await notifier._check_and_notify()
        # Should not crash


# ---------------------------------------------------------------------------
# Cooldown / debounce tests
# ---------------------------------------------------------------------------


class TestCooldownDebounce:
    @pytest.mark.asyncio
    async def test_debounces_within_cooldown(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
            notify_cooldown=1800,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="idle",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        # First notification fires
        await notifier._check_and_notify()
        assert mock_backend.send.call_count == 1

        # Second notification within cooldown is suppressed
        await notifier._check_and_notify()
        assert mock_backend.send.call_count == 1

    @pytest.mark.asyncio
    async def test_fires_again_after_cooldown(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=300,
            notify_cooldown=100,
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="idle",
        )
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        # First notification
        await notifier._check_and_notify()
        assert mock_backend.send.call_count == 1

        # Simulate cooldown expiring
        notifier._notified["lapis"] = time.time() - 200  # 200s ago > 100s cooldown

        # Second notification fires
        await notifier._check_and_notify()
        assert mock_backend.send.call_count == 2


# ---------------------------------------------------------------------------
# Notification loop tests
# ---------------------------------------------------------------------------


class TestNotificationLoop:
    @pytest.mark.asyncio
    async def test_loop_exits_when_no_backend(self):
        config = _make_config()
        notifier = _make_notifier(config)
        # No backend configured, loop should exit immediately
        await notifier.run()
        # If we get here, the loop exited cleanly (no infinite hang)

    @pytest.mark.asyncio
    async def test_loop_cancels_cleanly(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
        )
        notifier = _make_notifier(config)
        mock_backend = AsyncMock()
        notifier._backend = mock_backend

        # Start the loop and cancel after a short delay
        task = asyncio.create_task(notifier.run())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Loop cancelled cleanly — no crash

    @pytest.mark.asyncio
    async def test_loop_survives_backend_error(self):
        config = _make_config(
            notify_telegram_token="tok",
            notify_telegram_chat_id="chat",
            notify_idle_threshold=1,  # very low threshold
        )
        notifier = _make_notifier(
            config,
            last_activity=time.time() - 600,
            state="idle",
        )
        mock_backend = AsyncMock()
        mock_backend.send.side_effect = Exception("network error")
        notifier._backend = mock_backend

        # _check_and_notify should not crash on backend error
        await notifier._check_and_notify()
        # The exception is caught and logged, not re-raised


# ---------------------------------------------------------------------------
# Config reading tests
# ---------------------------------------------------------------------------


class TestConfigReading:
    def test_reads_threshold(self):
        config = _make_config(notify_idle_threshold=600)
        notifier = _make_notifier(config)
        assert notifier._get_cfg("notify_idle_threshold", 300) == 600

    def test_reads_cooldown(self):
        config = _make_config(notify_cooldown=3600)
        notifier = _make_notifier(config)
        assert notifier._get_cfg("notify_cooldown", 1800) == 3600

    def test_defaults_when_missing(self):
        config = {"plugins": {"hub": {}}}
        notifier = _make_notifier(config)
        assert notifier._get_cfg("notify_idle_threshold", 300) == 300
        assert notifier._get_cfg("notify_cooldown", 1800) == 1800

    def test_handles_empty_config(self):
        notifier = _make_notifier({})
        assert notifier._get_cfg("notify_idle_threshold", 300) == 300
