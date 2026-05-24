"""Notification backends for the hub system.

Provides background notification loop that alerts humans when agents
have been idle/blocked longer than a configured threshold. Supports
webhook and telegram delivery channels.

Architecture mirrors the dreaming_loop in plugin.py:
- asyncio background task, 60s check interval
- config-driven thresholds
- graceful cancellation on shutdown
- self-contained, no plugin.py bloat
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class NotificationBackend(ABC):
    """Base class for notification delivery channels."""

    @abstractmethod
    async def send(self, message: str, payload: Dict[str, Any]) -> None:
        """Send a notification.

        Args:
            message: Human-readable notification text.
            payload: Structured data for the notification.

        Raises:
            Exception: If delivery fails (caller handles gracefully).
        """
        ...


class WebhookNotifier(NotificationBackend):
    """POST JSON payload to a configured webhook URL."""

    def __init__(self, url: str):
        self.url = url

    async def send(self, message: str, payload: Dict[str, Any]) -> None:
        body = {
            "text": message,
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.url, json=body)
            resp.raise_for_status()
        logger.info(f"webhook notification sent to {self.url} ({resp.status_code})")


class TelegramNotifier(NotificationBackend):
    """Send notification via Telegram Bot API."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    async def send(self, message: str, payload: Dict[str, Any]) -> None:
        body = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.api_url, json=body)
            resp.raise_for_status()
        logger.info(
            f"telegram notification sent to chat {self.chat_id} ({resp.status_code})"
        )


class HubNotifier:
    """Background loop that checks for idle agents and fires notifications.

    Usage:
        notifier = HubNotifier(config, identity_ref, last_activity_ref)
        task = asyncio.create_task(notifier.run())
        # ... later in shutdown:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    The notifier reads config keys under plugins.hub.notify_* and uses
    _last_activity_at (passed as a callable or direct attribute access)
    to determine how long the agent has been idle.
    """

    CHECK_INTERVAL = 60  # seconds between checks (matches dreaming loop)
    COOLDOWN_DEFAULT = 1800  # 30 min default cooldown before re-notifying

    def __init__(
        self,
        config: Dict[str, Any],
        get_identity: Callable[[], str],
        get_last_activity: Callable[[], float],
        get_state: Callable[[], str],
    ):
        """
        Args:
            config: Full config dict (reads plugins.hub.notify_* keys).
            get_identity: Callable returning current agent identity string.
            get_last_activity: Callable returning _last_activity_at float.
            get_state: Callable returning current agent state string.
        """
        self.config = config
        self._get_identity = get_identity
        self._get_last_activity = get_last_activity
        self._get_state = get_state

        # Track notified agents to debounce
        # key: agent_id or identity, value: timestamp of last notification
        self._notified: Dict[str, float] = {}

        # Build backend from config
        self._backend: Optional[NotificationBackend] = self._build_backend()

    def detect_channel(self) -> Optional[str]:
        """Auto-detect the notification channel from configured credentials.

        Priority:
          1. Explicit notify_channel setting
          2. Telegram if notify_telegram_token is set
          3. Webhook if notify_url is set
          4. None (no credentials configured)
        """
        hub_cfg = self.config.get("plugins", {}).get("hub", {})
        explicit = hub_cfg.get("notify_channel", "")
        if explicit:
            return explicit

        # Auto-detect from credentials
        token = hub_cfg.get("notify_telegram_token", "")
        if token:
            return "telegram"

        url = hub_cfg.get("notify_url", "")
        if url:
            return "webhook"

        return None

    def _build_backend(self) -> Optional[NotificationBackend]:
        """Construct the notification backend from config.

        Uses detect_channel() for auto-detection when no explicit
        channel is set. Falls back gracefully when no credentials
        are configured.
        """
        hub_cfg = self.config.get("plugins", {}).get("hub", {})
        channel = self.detect_channel()

        if not channel:
            logger.info("no notification channel configured (no credentials found)")
            return None

        if channel == "webhook":
            url = hub_cfg.get("notify_url", "")
            if not url:
                logger.warning("notify_channel=webhook but no notify_url set")
                return None
            return WebhookNotifier(url)

        elif channel == "telegram":
            token = hub_cfg.get("notify_telegram_token", "")
            chat_id = hub_cfg.get("notify_telegram_chat_id", "")
            if not token or not chat_id:
                logger.warning(
                    "notify_channel=telegram but token/chat_id not configured"
                )
                return None
            return TelegramNotifier(token, chat_id)

        else:
            logger.warning(f"unknown notification channel: {channel}")
            return None

    def _get_cfg(self, key: str, default: Any) -> Any:
        """Read a config value from plugins.hub.* namespace."""
        return self.config.get("plugins", {}).get("hub", {}).get(key, default)

    async def run(self) -> None:
        """Main notification loop. Runs as asyncio background task."""
        if not self._backend:
            logger.info("no notification backend configured, loop exiting")
            return

        logger.info(
            f"notification loop started (channel={self._get_cfg('notify_channel', 'webhook')}, "
            f"threshold={self._get_cfg('notify_idle_threshold', 300)}s)"
        )

        while True:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
                await self._check_and_notify()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"notification loop error: {e}")

    async def _check_and_notify(self) -> None:
        """Check idle duration and fire notification if threshold exceeded."""
        threshold = self._get_cfg("notify_idle_threshold", 300)
        identity = self._get_identity()
        last_activity = self._get_last_activity()
        state = self._get_state()

        if not identity:
            return

        now = time.time()
        idle_duration = now - last_activity

        # Only notify if idle beyond threshold
        if idle_duration < threshold:
            return

        # Don't notify if agent is actively working or booting
        if state in ("working", "booting", "connecting"):
            return

        # Debounce: don't re-notify within cooldown window
        cooldown = self._get_cfg("notify_cooldown", self.COOLDOWN_DEFAULT)
        last_notified = self._notified.get(identity, 0)
        if (now - last_notified) < cooldown:
            return

        # Build notification
        minutes_idle = int(idle_duration // 60)
        message = (
            f"Agent '{identity}' has been idle for {minutes_idle} minutes "
            f"(threshold: {threshold // 60} min). "
            f"State: {state}. May need human attention."
        )
        payload = {
            "identity": identity,
            "state": state,
            "idle_seconds": int(idle_duration),
            "idle_minutes": minutes_idle,
            "threshold_seconds": threshold,
            "timestamp": now,
        }

        # Attempt delivery
        if self._backend is None:
            return
        try:
            await self._backend.send(message, payload)
            self._notified[identity] = now
            logger.info(f"notification sent for {identity} (idle {minutes_idle}m)")
        except Exception as e:
            # Network errors should not crash the loop
            logger.warning(f"notification delivery failed for {identity}: {e}")
