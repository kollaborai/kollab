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
import hashlib
import json
import logging
import socket
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote, urlsplit

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


DEFAULT_NTFY_PRIORITY_MAP = {
    "message": 3,
    "decision": 5,
    "blocker": 5,
    "done": 3,
}
NTFY_NOTIFICATION_KINDS = frozenset({"decision", "blocker", "done"})
NTFY_DEDUPE_WINDOW_SECONDS = 600.0
NTFY_DEDUPE_MAX_ENTRIES = 512
NTFY_RETRY_ATTEMPTS = 3
NTFY_TIMEOUT_SECONDS = 3.0
NTFY_RETRY_BACKOFF_SECONDS = 0.5


def default_ntfy_config_path() -> Path:
    """Return the local fallback config path at runtime, not import time."""
    return Path.home() / ".config" / "marco-notify" / "config.json"


def load_ntfy_settings(
    config: Any, fallback_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Resolve hub ntfy settings, falling back to local Marco Notify defaults.

    Only the server and topic are read from the local fallback file. Tokens and
    priority overrides are sourced from the Kollab hub config. Values are never
    logged; parse and I/O errors identify only their exception type.
    """
    try:
        plugins = config.get("plugins", {}) if config is not None else {}
        hub_cfg = plugins.get("hub", {}) if isinstance(plugins, dict) else {}
    except AttributeError, TypeError:
        hub_cfg = {}
    if not isinstance(hub_cfg, dict):
        hub_cfg = {}

    notify_cfg = hub_cfg.get("notify", {})
    ntfy_cfg = notify_cfg.get("ntfy", {}) if isinstance(notify_cfg, dict) else {}
    if not isinstance(ntfy_cfg, dict):
        ntfy_cfg = {}

    server = str(ntfy_cfg.get("server") or "").strip()
    topic = str(ntfy_cfg.get("topic") or "").strip()
    token = str(ntfy_cfg.get("token") or "").strip()

    if not server or not topic:
        path = (
            Path(fallback_path)
            if fallback_path is not None
            else default_ntfy_config_path()
        )
        try:
            fallback = json.loads(path.expanduser().read_text(encoding="utf-8"))
        except FileNotFoundError:
            fallback = {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            logger.warning(
                "could not read local ntfy defaults (%s)", type(exc).__name__
            )
            fallback = {}
        if isinstance(fallback, dict):
            server = server or str(fallback.get("ntfy_server") or "").strip()
            topic = topic or str(fallback.get("ntfy_topic") or "").strip()

    priority_map = dict(DEFAULT_NTFY_PRIORITY_MAP)
    configured_priorities = ntfy_cfg.get("priority_map", {})
    if isinstance(configured_priorities, dict):
        for kind, value in configured_priorities.items():
            normalized_kind = str(kind).strip().lower()
            try:
                priority = int(value)
            except TypeError, ValueError:
                continue
            if normalized_kind in priority_map and 1 <= priority <= 5:
                priority_map[normalized_kind] = priority
            else:
                logger.warning("ignored invalid ntfy priority mapping")

    return {
        "server": server,
        "topic": topic,
        "token": token,
        "priority_map": priority_map,
        "webui_agent_url_template": str(
            ntfy_cfg.get("webui_agent_url_template") or ""
        ).strip(),
    }


class NtfyDeliveryError(RuntimeError):
    """A secret-safe ntfy failure that never includes request URLs or headers."""


class NtfyNotifier(NotificationBackend):
    """Deliver notifications to an ntfy topic with bounded retry and dedupe."""

    def __init__(
        self,
        server: str,
        topic: str,
        token: str = "",
        priority_map: Optional[Dict[str, int]] = None,
        *,
        client_factory: Optional[Callable[..., Any]] = None,
        sleep: Optional[Callable[[float], Any]] = None,
        monotonic: Optional[Callable[[], float]] = None,
        timeout: float = NTFY_TIMEOUT_SECONDS,
        max_attempts: int = NTFY_RETRY_ATTEMPTS,
        backoff_seconds: float = NTFY_RETRY_BACKOFF_SECONDS,
        dedupe_window_seconds: float = NTFY_DEDUPE_WINDOW_SECONDS,
        max_dedupe_entries: int = NTFY_DEDUPE_MAX_ENTRIES,
    ):
        self.server = self._validate_server(server)
        self.topic = self._validate_topic(topic)
        self.token = str(token or "").strip()
        if "\r" in self.token or "\n" in self.token:
            raise ValueError("ntfy token must not contain newlines")

        self.priority_map = dict(DEFAULT_NTFY_PRIORITY_MAP)
        for kind, value in (priority_map or {}).items():
            normalized_kind = str(kind).strip().lower()
            try:
                priority = int(value)
            except TypeError, ValueError:
                continue
            if normalized_kind in self.priority_map and 1 <= priority <= 5:
                self.priority_map[normalized_kind] = priority

        self._url = f"{self.server}/{quote(self.topic, safe='-._')}"
        self._client_factory = client_factory or httpx.AsyncClient
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.dedupe_window_seconds = max(0.0, float(dedupe_window_seconds))
        self.max_dedupe_entries = max(1, int(max_dedupe_entries))
        self._delivered: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._inflight: set[tuple[str, str]] = set()

    @classmethod
    def from_config(
        cls, config: Any, fallback_path: Optional[Path] = None
    ) -> Optional["NtfyNotifier"]:
        """Build a notifier from hub config plus runtime fallback values."""
        settings = load_ntfy_settings(config, fallback_path=fallback_path)
        return cls.from_settings(settings)

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> Optional["NtfyNotifier"]:
        """Build from resolved settings without rereading fallback files."""
        if not settings.get("server") or not settings.get("topic"):
            return None
        try:
            return cls(
                server=settings["server"],
                topic=settings["topic"],
                token=settings.get("token", ""),
                priority_map=settings.get("priority_map", {}),
            )
        except (TypeError, ValueError) as exc:
            logger.warning("invalid ntfy configuration (%s)", type(exc).__name__)
            return None

    @staticmethod
    def _validate_server(server: str) -> str:
        value = str(server or "").strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            valid_host = bool(parsed.hostname)
        except ValueError:
            valid_host = False
            parsed = None
        if (
            parsed is None
            or parsed.scheme not in ("http", "https")
            or not valid_host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "ntfy server must be an http(s) URL without credentials, query, or fragment"
            )
        return value

    @staticmethod
    def _validate_topic(topic: str) -> str:
        value = str(topic or "").strip()
        if (
            not value
            or "/" in value
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            raise ValueError("ntfy topic must be a non-empty path segment")
        return value

    def _headers(self, payload: Dict[str, Any], sender: str) -> Dict[str, Any]:
        kind = str(payload.get("notify_kind") or "message")
        kind = kind.strip().lower()
        if kind not in NTFY_NOTIFICATION_KINDS:
            kind = "message"

        title = payload.get("title") or f"Kollab · {sender} · {socket.gethostname()}"
        title = " ".join(str(title).split())[:256]
        headers: Dict[str, Any] = {
            # ntfy supports UTF-8 headers; pass bytes because httpx only accepts
            # ASCII for string-valued headers.
            "Title": title.encode("utf-8"),
            "Priority": str(self.priority_map.get(kind, self.priority_map["message"])),
        }
        if kind in NTFY_NOTIFICATION_KINDS:
            headers["Tags"] = kind
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        click_url = payload.get("click_url") or payload.get("click")
        if click_url:
            try:
                parsed_click = urlsplit(str(click_url))
            except ValueError:
                parsed_click = None
            if (
                parsed_click is not None
                and parsed_click.scheme in ("http", "https")
                and parsed_click.netloc
                and not any(char in str(click_url) for char in "\r\n")
            ):
                headers["Click"] = str(click_url)
        return headers

    def _dedupe_key(self, sender: str, message: str) -> tuple[str, str]:
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        return sender.casefold(), digest

    def _is_recent_duplicate(self, key: tuple[str, str], now: float) -> bool:
        expired = [
            old_key
            for old_key, sent_at in self._delivered.items()
            if now - sent_at >= self.dedupe_window_seconds
        ]
        for old_key in expired:
            self._delivered.pop(old_key, None)
        return key in self._delivered or key in self._inflight

    async def _post_with_retry(self, message: str, headers: Dict[str, Any]) -> None:
        try:
            async with self._client_factory(timeout=self.timeout) as client:
                for attempt in range(1, self.max_attempts + 1):
                    try:
                        response = await client.post(
                            self._url, content=message.encode("utf-8"), headers=headers
                        )
                    except httpx.HTTPError as exc:
                        if attempt < self.max_attempts:
                            delay = self.backoff_seconds * (2 ** (attempt - 1))
                            logger.warning(
                                "ntfy request failed (%s); retry %d/%d",
                                type(exc).__name__,
                                attempt,
                                self.max_attempts,
                            )
                            await self._sleep(delay)
                            continue
                        raise NtfyDeliveryError(
                            f"ntfy request failed after {attempt} attempts ({type(exc).__name__})"
                        ) from None

                    status = int(response.status_code)
                    if 200 <= status < 300:
                        return
                    retryable = status == 429 or 500 <= status < 600
                    if retryable and attempt < self.max_attempts:
                        delay = self.backoff_seconds * (2 ** (attempt - 1))
                        logger.warning(
                            "ntfy returned HTTP %d; retry %d/%d",
                            status,
                            attempt,
                            self.max_attempts,
                        )
                        await self._sleep(delay)
                        continue
                    raise NtfyDeliveryError(f"ntfy request failed (HTTP {status})")
        except NtfyDeliveryError:
            raise
        except httpx.HTTPError as exc:
            raise NtfyDeliveryError(
                f"ntfy request failed ({type(exc).__name__})"
            ) from None

    async def send(self, message: str, payload: Dict[str, Any]) -> None:
        body = str(message or "").strip()
        if not body:
            return
        sender = str(
            payload.get("sender") or payload.get("identity") or "unknown"
        ).strip()
        sender = sender or "unknown"
        key = self._dedupe_key(sender, body)
        now = self._monotonic()
        if self._is_recent_duplicate(key, now):
            logger.info("duplicate ntfy notification suppressed")
            return

        self._inflight.add(key)
        try:
            await self._post_with_retry(body, self._headers(payload, sender))
        finally:
            self._inflight.discard(key)

        self._delivered[key] = now
        self._delivered.move_to_end(key)
        while len(self._delivered) > self.max_dedupe_entries:
            self._delivered.popitem(last=False)


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
          4. ntfy if hub settings or local fallback config are available
          5. None (no credentials configured)
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

        if NtfyNotifier.from_config(self.config) is not None:
            return "ntfy"

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

        elif channel == "ntfy":
            backend = NtfyNotifier.from_config(self.config)
            if backend is None:
                logger.warning(
                    "notify_channel=ntfy but server/topic are not configured"
                )
            return backend

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
