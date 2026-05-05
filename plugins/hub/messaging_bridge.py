"""Platform-agnostic messaging bridge for the hub system.

Provides a reusable interface for bidirectional communication between
kollabor agents and external messaging platforms (Telegram, WhatsApp,
Slack, Discord, Signal, SMS, etc).

Outgoing: agent notifications, departure alerts, task completions.
Incoming: human commands from phone -> hub messages to agents.

Architecture:
- MessagingBridge ABC defines the contract.
- TelegramBridge implementation uses httpx (async) and mirrors
  telemux patterns: bot token + chat_id, getUpdates long-polling
  with offset tracking, sendMessage with HTML parse mode.
- IncomingMessage is a lightweight dataclass for platform-agnostic
  inbound payloads.
- BridgeManager coordinates multiple bridges (future multi-platform).

Config keys (under plugins.hub.*):
  bridge_enabled        bool   enable/disable the bridge loop
  bridge_platform       str    "telegram" (default), future: whatsapp, slack, etc
  bridge_token          str    bot token (telegram) or API key
  bridge_chat_id        str    chat/channel identifier
  bridge_poll_interval  int    seconds between polls for incoming (default 2)
  bridge_target_agent   str    identity to route incoming msgs to ("" = self)
"""

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """Platform-agnostic incoming message."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    platform: str = ""
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    timestamp: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)


class MessagingBridge(ABC):
    """Platform-agnostic messaging bridge.

    Implementations handle the specifics of each platform (auth,
    API format, polling vs webhooks) while exposing a uniform
    interface to the hub.
    """

    @abstractmethod
    async def send(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Send a message to the platform.

        Args:
            text: Message text (HTML supported where platform allows).
            metadata: Optional structured data (ignored by most platforms).

        Returns:
            True if delivery succeeded.
        """
        ...

    @abstractmethod
    async def poll(self) -> List[IncomingMessage]:
        """Poll for new incoming messages since last check.

        Returns a list of new messages (empty if none). Implementations
        must track their own offset/cursor to avoid returning duplicates.
        """
        ...

    @abstractmethod
    async def connect(self) -> bool:
        """Initialize connection to the platform.

        Returns True if the connection is healthy (e.g. bot token is valid).
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Clean shutdown. Release resources, close connections."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Platform name (telegram, whatsapp, slack, etc)."""
        ...


class TelegramBridge(MessagingBridge):
    """Telegram Bot API bridge.

    Mirrors telemux patterns:
    - sendMessage with HTML parse_mode
    - getUpdates with long-polling (timeout=30) and offset tracking
    - Retry with exponential backoff on network errors
    - chat_id + user_id validation on incoming messages

    Uses httpx (async) instead of requests (sync) since kollabor
    is fully async.
    """

    MAX_RETRIES = 3
    LONG_POLL_TIMEOUT = 30  # telegram long-polling timeout

    def __init__(self, token: str, chat_id: str, user_id: str = ""):
        self._token = token
        self._chat_id = chat_id
        self._user_id = user_id  # optional: restrict to specific user
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._offset = 0  # getUpdates offset (telemux pattern)
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False

    @property
    def name(self) -> str:
        return "telegram"

    async def connect(self) -> bool:
        """Validate bot token via getMe, init httpx client."""
        try:
            self._client = httpx.AsyncClient(timeout=self.LONG_POLL_TIMEOUT + 5)
            resp = await self._client.get(f"{self._base_url}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_name = data["result"].get("first_name", "unknown")
                bot_user = data["result"].get("username", "unknown")
                logger.info(f"telegram bridge connected: @{bot_user} ({bot_name})")
                self._connected = True
                return True
            else:
                logger.error(f"telegram getMe failed: {data}")
                return False
        except Exception as e:
            logger.error(f"telegram connect failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Close httpx client."""
        self._connected = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("telegram bridge disconnected")

    async def send(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Send message via sendMessage. Retries with backoff (telemux pattern)."""
        if not self._client:
            logger.warning("telegram bridge not connected, cannot send")
            return False

        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/sendMessage",
                    json=payload,
                    timeout=10.0,
                )
                resp.raise_for_status()
                logger.info(f"telegram message sent ({resp.status_code})")
                return True
            except httpx.TimeoutException:
                logger.warning(
                    f"telegram send timeout (attempt {attempt + 1}/{self.MAX_RETRIES})"
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)
            except httpx.HTTPStatusError as e:
                logger.error(f"telegram send HTTP error: {e.response.status_code}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    return False
            except Exception as e:
                logger.error(f"telegram send error: {e}")
                return False

        return False

    async def poll(self) -> List[IncomingMessage]:
        """Poll for new messages via getUpdates (telemux pattern).

        Uses long-polling with offset tracking. Only returns messages
        from the authorized chat_id (and optionally user_id).
        """
        if not self._client:
            return []

        params = {
            "offset": self._offset,
            "timeout": self.LONG_POLL_TIMEOUT,
        }

        try:
            resp = await self._client.get(
                f"{self._base_url}/getUpdates",
                params=params,
                timeout=self.LONG_POLL_TIMEOUT + 5,
            )
            data = resp.json()

            if not data.get("ok"):
                logger.warning(f"telegram getUpdates not ok: {data}")
                return []

            results = []
            for update in data.get("result", []):
                update_id = update["update_id"]
                # Advance offset past this update (telemux pattern)
                self._offset = update_id + 1

                msg = update.get("message")
                if not msg:
                    continue

                text = msg.get("text", "")

                # Voice/audio message support -- transcribe with whisper
                if not text:
                    voice = msg.get("voice") or msg.get("audio")
                    if voice:
                        file_id = voice.get("file_id", "")
                        if file_id:
                            text = await self._transcribe_voice(file_id)

                if not text:
                    continue

                from_user = msg.get("from", {})
                incoming_chat_id = str(msg.get("chat", {}).get("id", ""))
                from_user_id = str(from_user.get("id", ""))

                # Security: validate chat_id (telemux pattern)
                if incoming_chat_id != self._chat_id:
                    logger.warning(f"telegram: unauthorized chat_id {incoming_chat_id}")
                    continue

                # Security: validate user_id if configured
                if self._user_id and from_user_id != self._user_id:
                    logger.warning(f"telegram: unauthorized user_id {from_user_id}")
                    continue

                results.append(
                    IncomingMessage(
                        platform="telegram",
                        sender_id=from_user_id,
                        sender_name=from_user.get("first_name", "unknown"),
                        text=text,
                        timestamp=msg.get("date", time.time()),
                        raw=update,
                    )
                )

            return results

        except httpx.TimeoutException:
            # Normal for long-polling -- no new messages
            return []
        except Exception as e:
            logger.warning(f"telegram poll error: {e}")
            return []

    async def _transcribe_voice(self, file_id: str) -> str:
        """Download voice file from telegram and transcribe with local whisper."""
        import asyncio
        import os
        import tempfile

        if not self._client:
            return "[voice message - bridge not connected]"

        try:
            # Get file path from telegram
            resp = await self._client.get(
                f"{self._base_url}/getFile",
                params={"file_id": file_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return "[voice message - transcription failed]"

            file_path = data["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"

            # Download the audio file
            audio_resp = await self._client.get(download_url, timeout=30.0)
            audio_resp.raise_for_status()

            # Save to temp file (telegram voice is usually .ogg opus)
            suffix = "." + file_path.split(".")[-1] if "." in file_path else ".ogg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_resp.content)
                tmp_path = f.name

            # Transcribe with local whisper CLI (non-blocking)
            proc = await asyncio.create_subprocess_exec(
                "whisper",
                tmp_path,
                "--model",
                "base",
                "--language",
                "en",
                "--output_format",
                "txt",
                "--output_dir",
                "/tmp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            result_stdout = stdout_bytes.decode("utf-8", errors="replace")

            # Read the transcription -- whisper writes to /tmp/<basename>.txt
            basename = os.path.basename(tmp_path).rsplit(".", 1)[0]
            txt_path = f"/tmp/{basename}.txt"
            transcription = ""
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    transcription = f.read().strip()
                os.unlink(txt_path)
            if not transcription:
                # Fallback: parse stdout, skip whisper debug lines
                for line in result_stdout.split("\n"):
                    line = line.strip()
                    if (
                        line
                        and not line.startswith("Detecting")
                        and not line.startswith("[")
                    ):
                        transcription = line
                        break
            if not transcription:
                transcription = "[voice message - whisper returned empty]"

            # Clean up temp audio file
            os.unlink(tmp_path)

            logger.info(f"Voice transcribed: {transcription[:80]}")
            return f"[voice] {transcription}"

        except asyncio.TimeoutError:
            logger.error("Voice transcription timed out (60s)")
            return "[voice message - transcription timed out]"
        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            return "[voice message - transcription failed]"


class BridgeManager:
    """Manages bridge lifecycle and provides factory method.

    Usage:
        manager = BridgeManager()
        bridge = manager.create("telegram", token="...", chat_id="...")
        if await bridge.connect():
            await bridge.send("hello from kollabor")
            messages = await bridge.poll()
    """

    _registry: Dict[str, type["MessagingBridge"]] = {
        "telegram": TelegramBridge,
    }

    @classmethod
    def register(cls, platform: str, bridge_class: type) -> None:
        """Register a bridge implementation for a platform."""
        cls._registry[platform] = bridge_class

    @classmethod
    def create(cls, platform: str, **kwargs) -> Optional[MessagingBridge]:
        """Create a bridge for the given platform.

        Args:
            platform: Platform name (telegram, whatsapp, slack, etc).
            **kwargs: Platform-specific config (token, chat_id, etc).

        Returns:
            Bridge instance or None if platform is unknown.
        """
        bridge_cls = cls._registry.get(platform)
        if not bridge_cls:
            logger.error(
                f"unknown bridge platform: {platform} "
                f"(available: {', '.join(cls._registry.keys())})"
            )
            return None
        return bridge_cls(**kwargs)

    @classmethod
    def available_platforms(cls) -> List[str]:
        """List registered platform names."""
        return list(cls._registry.keys())
