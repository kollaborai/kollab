"""Voice settings and voice prompt overlay (specs/voice-mode/livekit-web/contracts/config-prompt.md).

No second store: settings are global config keys under ``voice.livekit.*``
(``ConfigService.save_key``), the API key is a protected keyring entry
(``APIKeyManager``), and the voice prompt is a ``PromptOverrideStore`` file.
Nothing here touches a normal chat's profile or system prompt: the engine only
hands the voice profile id and prompt revision to voice turns.
"""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .voice_runtime_host import RuntimeHostError
from .voice_types import SpeechReadiness

logger = logging.getLogger(__name__)

PREFIX = "voice.livekit."
KEY_NAME = "voice-livekit"  # keyring entry name
PROMPT_BUNDLE = "voice"
MAX_PROMPT_BYTES = 32 * 1024
MAX_KEY_BYTES = 8 * 1024
PERSONALITIES = ("max_gen_z", "neutral", "professional")
DEFAULTS: dict[str, Any] = {
    "provider_profile_id": None,
    "base_url": None,
    "model": None,
    "reasoning": "off",
    "stt_engine": "parakeet",
    "tts_engine": "vibevoice",
    "personality": "max_gen_z",
}
DEFAULT_PROMPT = (
    "You are speaking out loud. Answer in short natural sentences, no markdown, "
    "no lists, no code blocks. Say what you did, not how you did it."
)
PERSONALITY_LINES = {
    "max_gen_z": "Talk like a very online gen z friend: lowercase energy, slang, warm and fast.",
    "neutral": "Speak plainly and neutrally.",
    "professional": "Speak formally and precisely, like a professional assistant.",
}

Personality = Literal["max_gen_z", "neutral", "professional"]


class VoiceSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_profile_id: str | None = None
    base_url: str | None = None
    model: str | None = None
    reasoning: Literal["off", "low"] | None = None
    api_key_action: Literal["keep", "set", "clear"] = "keep"
    api_key_value: str | None = None
    stt_engine: str | None = None
    tts_engine: str | None = None


class VoicePromptPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    personality: Personality


def invalid(message: str) -> RuntimeHostError:
    return RuntimeHostError("invalid_request", 400, message)


def parse_model(model: type[BaseModel], payload: Any) -> BaseModel:
    """Validate without echoing submitted values (an api key must never come back)."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(sorted({".".join(map(str, e["loc"])) or "body" for e in exc.errors()}))
        raise invalid(f"Invalid voice request: {fields}.") from None


def _default_secrets() -> Any:
    from kollabor_ai.providers.security import APIKeyManager

    return APIKeyManager()


def _default_profiles() -> Callable[[str], Any]:
    from kollabor_ai.profile_manager import ProfileManager

    return ProfileManager().get_profile


def _default_config() -> Any:
    from kollabor_config.config_utils import get_global_config_path
    from kollabor_config.service import ConfigService

    return ConfigService(get_global_config_path(), fast_mode=True)


async def _default_readiness() -> SpeechReadiness:
    try:
        from kollabor_voice.client import VoiceClient
        from kollabor_voice.speech_adapter import map_readiness

        mapped = map_readiness(await VoiceClient().call("status"))
        progress = (
            mapped.bytes_done / mapped.bytes_total
            if mapped.bytes_done is not None and mapped.bytes_total
            else None
        )
        return SpeechReadiness(
            stt_state=mapped.state,
            stt_engine="parakeet",
            tts_state=mapped.state,
            tts_engine="vibevoice",
            detail=mapped.detail or None,
            progress=progress,
        )
    except Exception as exc:  # the one service owns readiness; no retry loop here
        return SpeechReadiness(
            stt_state="error",
            stt_engine="parakeet",
            tts_state="error",
            tts_engine="vibevoice",
            detail=f"Voice service unavailable: {type(exc).__name__}",
        )


class VoiceSettingsService:
    def __init__(
        self,
        *,
        config: Any = None,
        secrets: Any = None,
        profiles: Callable[[str], Any] | None = None,
        prompts: Any = None,
        readiness: Callable[[], Awaitable[SpeechReadiness]] | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._profiles = profiles
        self._prompts = prompts
        self._readiness = readiness or _default_readiness

    # --- lazy collaborators (nothing opens a file or keyring at import) ---

    def _cfg(self) -> Any:
        if self._config is None:
            self._config = _default_config()
        return self._config

    def _store(self) -> Any:
        if self._prompts is None:
            from kollabor_ai.prompt_overrides import PromptOverrideStore

            self._prompts = PromptOverrideStore()
        return self._prompts

    def _secret_store(self) -> Any:
        if self._secrets is None:
            try:
                self._secrets = _default_secrets()
            except Exception:
                raise RuntimeHostError(
                    "runtime_unavailable", 503, "The protected secret store is unavailable."
                ) from None
        return self._secrets

    def _get_profile(self, name: str) -> Any:
        if self._profiles is None:
            self._profiles = _default_profiles()
        return self._profiles(name)

    def _value(self, name: str) -> Any:
        value = self._cfg().get(PREFIX + name, DEFAULTS[name])
        return DEFAULTS[name] if value is None and DEFAULTS[name] is not None else value

    # --- settings ---

    async def _key_configured(self) -> bool:
        try:
            return bool(await self._secret_store().get_key(KEY_NAME))
        except RuntimeHostError:
            return False
        except Exception:
            logger.warning("voice api key lookup failed")
            return False

    def disclosure_profile(self, session_profile: Any = None) -> Any:
        """Profile-like object for the LLM that voice turns reach."""
        base_url = self._value("base_url")
        if base_url:
            return SimpleNamespace(provider="openai", base_url=base_url)
        pid = self._value("provider_profile_id")
        if pid:
            return self._get_profile(pid) or session_profile
        return session_profile

    async def get_settings(self) -> dict[str, Any]:
        from .voice_control import llm_disclosure

        target = self.disclosure_profile(None)
        remote = llm_disclosure(target)[0] if target is not None else False
        return {
            **{k: self._value(k) for k in DEFAULTS if k != "personality"},
            "api_key_configured": await self._key_configured(),
            "readiness": (await self._readiness()).model_dump(mode="json"),
            "llm_is_remote": remote,
        }

    async def update_settings(self, payload: Any) -> dict[str, Any]:
        patch = parse_model(VoiceSettingsPatch, payload)
        sent = patch.model_fields_set
        changes: dict[str, Any] = {}
        for name in ("provider_profile_id", "base_url", "model", "reasoning", "stt_engine", "tts_engine"):
            if name not in sent:
                continue
            value = getattr(patch, name)
            if name == "reasoning" and value is None:
                raise invalid("reasoning must be off or low.")
            if isinstance(value, str):
                value = value.strip() or None
            if value is not None and len(value) > 2048:
                raise invalid(f"{name} is too long.")
            changes[name] = value
        if changes.get("base_url"):
            from urllib.parse import urlparse

            parsed = urlparse(changes["base_url"])
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise invalid("base_url must be an http or https URL.")
        pid = changes.get("provider_profile_id")
        if pid and self._get_profile(pid) is None:
            raise invalid("provider_profile_id does not name a profile.")
        action, key = patch.api_key_action, patch.api_key_value
        if action == "set":
            if not key or not key.strip():
                raise invalid("api_key_value is required to set a key.")
            if len(key.encode()) > MAX_KEY_BYTES:
                raise invalid("api_key_value is too long.")
        elif key is not None:
            raise invalid("api_key_value is accepted only when api_key_action is set.")

        # Everything validated: write the key first, then config, so a failed
        # secret write leaves the last-good config untouched.
        if action == "set":
            try:
                await self._secret_store().store_key(KEY_NAME, key)
            except RuntimeHostError:
                raise
            except Exception:
                raise RuntimeHostError(
                    "runtime_unavailable", 503, "The protected secret store is unavailable."
                ) from None
        elif action == "clear":
            try:
                await self._secret_store().delete_key(KEY_NAME)
            except RuntimeHostError:
                raise
            except Exception:
                raise RuntimeHostError(
                    "runtime_unavailable", 503, "The protected secret store is unavailable."
                ) from None
        for name, value in changes.items():
            if not self._cfg().save_key(PREFIX + name, value, save_target="global"):
                raise RuntimeHostError(
                    "runtime_unavailable", 503, "Voice settings could not be saved."
                )
        return await self.get_settings()

    # --- prompt overlay ---

    @staticmethod
    def _prompt_key(agent_id: str | None) -> str:
        from kollabor_ai.prompt_overrides import is_valid_section_key

        if not agent_id:
            return "default"
        if is_valid_section_key(agent_id):
            return agent_id
        return "agent-" + hashlib.sha256(agent_id.encode()).hexdigest()[:16]

    def get_prompt(self, agent_id: str | None) -> dict[str, str]:
        text = self._store().get_override(PROMPT_BUNDLE, self._prompt_key(agent_id))
        return {
            "text": text if text is not None else DEFAULT_PROMPT,
            "personality": self._value("personality"),
        }

    def update_prompt(self, agent_id: str | None, payload: Any) -> dict[str, str]:
        patch = parse_model(VoicePromptPatch, payload)
        if len(patch.text.encode()) > MAX_PROMPT_BYTES:
            raise invalid("Voice prompt is larger than 32 KiB.")
        key = self._prompt_key(agent_id)
        if patch.text.strip() and patch.text != DEFAULT_PROMPT:
            self._store().save_override(PROMPT_BUNDLE, key, patch.text)
        else:
            self._store().delete_override(PROMPT_BUNDLE, key)
        if not self._cfg().save_key(PREFIX + "personality", patch.personality, save_target="global"):
            raise RuntimeHostError("runtime_unavailable", 503, "Voice prompt could not be saved.")
        return self.get_prompt(agent_id)

    def overlay(self, agent_id: str | None) -> str:
        """Additive text for voice turns only; the chat system prompt is never edited."""
        prompt = self.get_prompt(agent_id)
        return f"{prompt['text']}\n{PERSONALITY_LINES[prompt['personality']]}"

    def prompt_revision(self, agent_id: str | None) -> str:
        return hashlib.sha256(self.overlay(agent_id).encode()).hexdigest()[:12]

    def reasoning(self) -> str:
        """Reasoning effort for voice turns: off (the default) or low."""
        return "low" if self._value("reasoning") == "low" else "off"

    def voice_profile_id(self, fallback: str) -> str:
        return self._value("provider_profile_id") or fallback
