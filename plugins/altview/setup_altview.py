"""Guided provider setup wizard as an AltView plugin.

Walks a brand-new user through configuring their first LLM provider:

    1. provider   pick a provider from a list
    2. api_key    paste the API key (masked echo)
    3. base_url   confirm / edit the endpoint (prefilled with the default)
    4. model      pick a model (curated from bundles/data/models.json) or type one
    5. review     confirm the summary; optionally run a live connection test
    6. saving     create the profile (persisted to config) and activate it
    7. done       success summary

The `/setup` command pushes this view onto the AltView stack, awaits it,
then reads the result attributes. The wizard performs the profile creation
and activation itself so the success screen and the optional connection test
reflect real behavior.

OpenAI's ChatGPT sign-in (OAuth) is delegated back to the existing `/login`
flow: selecting it sets ``result_launch_oauth`` and exits. Azure / fully
custom setups are routed to manual config.json editing via ``result_advanced``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


# -- provider catalogue -------------------------------------------------------


@dataclass
class ProviderChoice:
    """A selectable provider in the setup wizard."""

    key: str  # short internal id, also the default profile name
    label: str  # menu label
    provider: str  # ProviderType value stored on the profile
    blurb: str = ""  # one-line description shown under the list
    default_base_url: str = ""
    key_hint: str = ""  # example key format
    key_url: str = ""  # where to get a key
    default_model: str = ""
    needs_key: bool = True  # api key required to continue
    base_url_required: bool = False  # endpoint must be non-empty (custom)
    base_url_advanced: bool = True  # endpoint is optional/prefilled (press enter)
    oauth: bool = False  # delegate to /login instead of asking for a key
    advanced: bool = False  # route to manual config editing instead of finishing here


PROVIDERS: List[ProviderChoice] = [
    ProviderChoice(
        key="anthropic",
        label="Anthropic  (Claude)",
        provider="anthropic",
        blurb="Claude models. Key starts with sk-ant-.",
        default_base_url="https://api.anthropic.com",
        key_hint="sk-ant-...",
        key_url="https://console.anthropic.com/settings/keys",
        default_model="claude-sonnet-5",
    ),
    ProviderChoice(
        key="openai",
        label="OpenAI  (API key)",
        provider="openai",
        blurb="GPT models via an API key. Key starts with sk- or sk-proj-.",
        default_base_url="https://api.openai.com/v1",
        key_hint="sk-... / sk-proj-...",
        key_url="https://platform.openai.com/api-keys",
        default_model="gpt-5.6",
    ),
    ProviderChoice(
        key="openai-chatgpt",
        label="OpenAI  (sign in with ChatGPT)",
        provider="openai_responses",
        blurb="Use your ChatGPT subscription via OAuth — no API key needed.",
        oauth=True,
    ),
    ProviderChoice(
        key="gemini",
        label="Google Gemini",
        provider="gemini",
        blurb="Gemini models. Key from Google AI Studio.",
        default_base_url="https://generativelanguage.googleapis.com",
        key_hint="AIza...",
        key_url="https://aistudio.google.com/app/apikey",
        default_model="gemini-3.6-flash",
    ),
    ProviderChoice(
        key="openrouter",
        label="OpenRouter  (100+ models)",
        provider="openrouter",
        blurb="One key, many models. Model names look like vendor/model.",
        default_base_url="https://openrouter.ai/api/v1",
        key_hint="sk-or-...",
        key_url="https://openrouter.ai/settings/keys",
        default_model="",
    ),
    ProviderChoice(
        key="local",
        label="Custom / Local  (OpenAI-compatible)",
        provider="custom",
        blurb="Ollama, LM Studio, vLLM, or any OpenAI-compatible endpoint.",
        default_base_url="http://localhost:1234/v1/chat/completions",
        key_hint="(optional for local servers)",
        key_url="",
        default_model="",
        needs_key=False,
        base_url_required=True,
        base_url_advanced=False,
    ),
    ProviderChoice(
        key="advanced",
        label="Azure / Advanced  ->  manual config",
        provider="",
        blurb="Azure OpenAI and fully-custom endpoints are configured in config.json.",
        advanced=True,
    ),
]


# wizard stages
STAGE_PROVIDER = "provider"
STAGE_API_KEY = "api_key"
STAGE_BASE_URL = "base_url"
STAGE_MODEL = "model"
STAGE_MODEL_CUSTOM = "model_custom"
STAGE_REVIEW = "review"
STAGE_TESTING = "testing"
STAGE_SAVING = "saving"
STAGE_DONE = "done"
STAGE_ERROR = "error"

# sentinel row appended to every model list
_CUSTOM_MODEL_ROW = "✎  enter a model name..."

# step labels for the progress strip
_STEPS = ["Provider", "API key", "Endpoint", "Model", "Confirm"]


def _mask_key(key: str) -> str:
    """Mask an API key, showing only the first 3 and last 4 chars."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:3] + "•" * (len(key) - 7) + key[-4:]


def _load_model_suggestions(provider_value: str) -> List[str]:
    """Curated model names for a provider from the shared model registry."""
    try:
        from kollabor_ai.model_registry import get_model_registry

        models = get_model_registry().get("models", {})
    except Exception as exc:  # registry is best-effort
        logger.debug("setup: model registry unavailable: %s", exc)
        return []

    names = [
        name
        for name, meta in models.items()
        if isinstance(meta, dict)
        and meta.get("provider") == provider_value
        and not meta.get("retired")
    ]
    # Stable, readable order (registry dicts are insertion-ordered already).
    return names


class SetupAltView(AltView):
    """Fullscreen guided wizard for configuring a new LLM provider."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="setup",
            description="Guided provider setup wizard",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[SET]",
            aliases=["onboard", "wizard"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 12.0
        self.render_on_timer = True

        # injected services (set_context)
        self._profile_manager: Any = None
        self._llm_service: Any = None
        self._event_bus: Any = None

        # wizard state
        self._stage = STAGE_PROVIDER
        self._provider_index = 0
        self._provider: Optional[ProviderChoice] = None
        self._api_key = ""
        self._base_url = ""
        self._model = ""
        self._model_options: List[str] = []
        self._model_index = 0
        self._model_custom = ""
        self._input_error = ""

        # test / save feedback
        self._test_ok: Optional[bool] = None
        self._test_msg = ""
        self._save_error = ""
        self._done_summary_lines: List[str] = []

        # spinner
        self._spinner = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._last_spin = 0.0

        # results read by the command handler
        self.result_cancelled = False
        self.result_launch_oauth = False
        self.result_advanced = False
        self.result_saved = False
        self.result_profile_name = ""
        self.result_summary = ""

    # -- context injection ---------------------------------------------------

    def set_context(
        self,
        profile_manager: Any = None,
        llm_service: Any = None,
        event_bus: Any = None,
    ) -> None:
        self._profile_manager = profile_manager
        self._llm_service = llm_service
        self._event_bus = event_bus

    # -- lifecycle -----------------------------------------------------------

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._stage = STAGE_PROVIDER
        logger.info("SetupAltView: entered")

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False
        if self.result_cancelled or self.result_launch_oauth or self.result_advanced:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()
        self._renderer.clear_screen()
        self._render_header(width, theme)
        self._render_steps(width, theme)
        self._render_body(width, height, theme)
        self._render_footer(width, height, theme)
        return True

    async def on_complete(self) -> None:
        await super().on_complete()

    # -- input ---------------------------------------------------------------

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name
        char = key_press.char or ""

        # transient stages ignore input
        if self._stage in (STAGE_TESTING, STAGE_SAVING):
            return False

        if self._stage == STAGE_DONE:
            return True  # any key exits

        if self._stage == STAGE_ERROR:
            # any key returns to review so the user can retry or edit
            self._stage = STAGE_REVIEW
            return False

        if self._stage == STAGE_PROVIDER:
            return self._handle_provider_input(name, char)
        if self._stage == STAGE_API_KEY:
            return self._handle_api_key_input(name, char)
        if self._stage == STAGE_BASE_URL:
            return self._handle_base_url_input(name, char)
        if self._stage == STAGE_MODEL:
            return self._handle_model_input(name, char)
        if self._stage == STAGE_MODEL_CUSTOM:
            return self._handle_model_custom_input(name, char)
        if self._stage == STAGE_REVIEW:
            return self._handle_review_input(name, char)
        return False

    def _is_escape(self, name: str, char: str) -> bool:
        return name == "Escape" or char == "\x1b"

    def _is_enter(self, name: str, char: str) -> bool:
        return name == "Enter" or char in ("\r", "\n")

    def _is_printable(self, char: str) -> bool:
        return bool(char) and len(char) == 1 and 32 <= ord(char) <= 126

    def _handle_provider_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            self.result_cancelled = True
            return True
        if name == "ArrowUp":
            self._provider_index = max(0, self._provider_index - 1)
            return False
        if name == "ArrowDown":
            self._provider_index = min(len(PROVIDERS) - 1, self._provider_index + 1)
            return False
        if self._is_enter(name, char):
            self._select_provider(PROVIDERS[self._provider_index])
            # OAuth / advanced selections exit immediately; others advance stage.
            return self.result_launch_oauth or self.result_advanced
        return False

    def _select_provider(self, choice: ProviderChoice) -> None:
        self._provider = choice
        self._input_error = ""
        if choice.oauth:
            self.result_launch_oauth = True
            return
        if choice.advanced:
            self.result_advanced = True
            return

        # seed fields from the choice
        self._api_key = ""
        self._base_url = choice.default_base_url
        self._model = ""
        self._model_custom = ""
        self._model_options = _load_model_suggestions(choice.provider)
        # default-highlight the recommended model if present
        self._model_index = 0
        if choice.default_model and choice.default_model in self._model_options:
            self._model_index = self._model_options.index(choice.default_model)
        self._stage = STAGE_API_KEY

    def _handle_api_key_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            self._stage = STAGE_PROVIDER
            return False
        if name in ("Backspace", "Delete"):
            self._api_key = self._api_key[:-1]
            self._input_error = ""
            return False
        if self._is_enter(name, char):
            provider = self._provider
            if provider and provider.needs_key and not self._api_key.strip():
                self._input_error = "API key is required for this provider."
                return False
            self._input_error = ""
            self._stage = STAGE_BASE_URL
            return False
        if self._is_printable(char):
            self._api_key += char
            self._input_error = ""
        return False

    def _handle_base_url_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            self._stage = STAGE_API_KEY
            return False
        if name in ("Backspace", "Delete"):
            self._base_url = self._base_url[:-1]
            self._input_error = ""
            return False
        if self._is_enter(name, char):
            url = self._base_url.strip()
            provider = self._provider
            if provider and provider.base_url_required and not url:
                self._input_error = "Endpoint URL is required for a custom provider."
                return False
            if url and not self._valid_url(url):
                self._input_error = (
                    "Endpoint must start with https:// (or be localhost)."
                )
                return False
            self._base_url = url
            self._input_error = ""
            self._enter_model_stage()
            return False
        if self._is_printable(char):
            self._base_url += char
            self._input_error = ""
        return False

    def _valid_url(self, url: str) -> bool:
        if "localhost" in url or "127.0.0.1" in url:
            return True
        return url.startswith("https://")

    def _enter_model_stage(self) -> None:
        if self._model_options:
            self._stage = STAGE_MODEL
        else:
            # no curated suggestions (openrouter/local) -> straight to typing
            self._model_custom = self._provider.default_model if self._provider else ""
            self._stage = STAGE_MODEL_CUSTOM

    def _handle_model_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            self._stage = STAGE_BASE_URL
            return False
        total = len(self._model_options) + 1  # + custom row
        if name == "ArrowUp":
            self._model_index = max(0, self._model_index - 1)
            return False
        if name == "ArrowDown":
            self._model_index = min(total - 1, self._model_index + 1)
            return False
        if self._is_enter(name, char):
            if self._model_index >= len(self._model_options):
                # custom entry row
                self._model_custom = ""
                self._stage = STAGE_MODEL_CUSTOM
                return False
            self._model = self._model_options[self._model_index]
            self._enter_review()
            return False
        return False

    def _handle_model_custom_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            # back to the list if we have one, else base url
            self._stage = STAGE_MODEL if self._model_options else STAGE_BASE_URL
            return False
        if name in ("Backspace", "Delete"):
            self._model_custom = self._model_custom[:-1]
            self._input_error = ""
            return False
        if self._is_enter(name, char):
            model = self._model_custom.strip()
            if not model:
                self._input_error = "Model name is required."
                return False
            self._model = model
            self._input_error = ""
            self._enter_review()
            return False
        if self._is_printable(char):
            self._model_custom += char
            self._input_error = ""
        return False

    def _enter_review(self) -> None:
        self._input_error = ""
        self._stage = STAGE_REVIEW

    def _handle_review_input(self, name: str, char: str) -> bool:
        if self._is_escape(name, char):
            # step back into model selection
            self._stage = STAGE_MODEL if self._model_options else STAGE_MODEL_CUSTOM
            return False
        if char.lower() == "t":
            self._start_test()
            return False
        if char.lower() == "b":
            self._stage = STAGE_MODEL if self._model_options else STAGE_MODEL_CUSTOM
            return False
        if self._is_enter(name, char):
            self._start_save()
            return False
        return False

    # -- connection test -----------------------------------------------------

    def _start_test(self) -> None:
        self._stage = STAGE_TESTING
        self._test_ok = None
        self._test_msg = ""
        self.spawn_background_task(self._run_test(), name="setup-test")

    async def _run_test(self) -> None:
        try:
            from kollabor_ai.providers.registry import (
                ProviderRegistry,
                create_config_from_profile,
            )

            profile_dict = {
                "provider": self._provider.provider if self._provider else "custom",
                "model": self._model,
                "api_key": self._api_key or "",
                "base_url": self._base_url or "",
            }
            config = create_config_from_profile(profile_dict)
            provider = await ProviderRegistry.create_provider(config)
            try:
                response = await asyncio.wait_for(
                    provider.call(
                        messages=[
                            {
                                "role": "user",
                                "content": "Reply with the single word: OK",
                            }
                        ]
                    ),
                    timeout=25.0,
                )
                text = (response.get_text_content() or "").strip()
                self._test_ok = True
                snippet = text[:40] if text else "(empty response)"
                self._test_msg = f"connected — model replied: {snippet}"
            finally:
                try:
                    await provider.shutdown()
                except Exception:
                    pass
        except asyncio.TimeoutError:
            self._test_ok = False
            self._test_msg = "timed out after 25s — check endpoint / network"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._test_ok = False
            self._test_msg = self._short_error(str(exc))
            logger.info("setup: connection test failed: %s", exc)
        finally:
            if self._stage == STAGE_TESTING:
                self._stage = STAGE_REVIEW

    def _short_error(self, msg: str) -> str:
        msg = " ".join(msg.split())
        if len(msg) > 120:
            msg = msg[:117] + "..."
        return msg or "unknown error"

    # -- save + activate -----------------------------------------------------

    def _start_save(self) -> None:
        self._stage = STAGE_SAVING
        self._save_error = ""
        self.spawn_background_task(self._run_save(), name="setup-save")

    async def _run_save(self) -> None:
        try:
            if not self._profile_manager:
                raise RuntimeError("profile manager unavailable")
            provider = self._provider
            assert provider is not None
            pm = self._profile_manager

            # /setup always creates a user profile. Never fill or mutate a
            # provider-specific built-in/template in place; if the provider
            # name already exists, mint a suffixed profile instead.
            name = provider.key
            existing = pm.get_profile(name)
            if existing is not None:
                name = self._unique_profile_name(provider.key)
            created = pm.create_profile(
                name=name,
                base_url=self._base_url or "",
                model=self._model,
                api_key=self._api_key or None,
                provider=provider.provider,
                supports_tools=True,
                description=f"Created via /setup ({provider.label.strip()})",
                save_to_config=True,
            )
            if not created:
                raise RuntimeError(f"could not create profile '{name}'")

            # Activate through the state service first. In attach mode this is
            # the RPC bridge to the daemon's ProfileManager; using only the
            # client-side LLM coordinator leaves the daemon with the previous
            # profile until restart, so /loadout and the status widget lag.
            activated = False
            state_service = None
            if self._event_bus and hasattr(self._event_bus, "get_service"):
                state_service = self._event_bus.get_service("state_service")
            if state_service and hasattr(state_service, "set_active_profile"):
                try:
                    await state_service.set_active_profile(
                        name, persist=True, reload_profile=True
                    )
                    activated = True
                    # Keep the client-side manager aligned for local widgets
                    # and code paths that still read it directly. Do not write
                    # config a second time; the state service already did.
                    try:
                        self._profile_manager.set_active_profile(name, persist=False)
                    except TypeError:
                        self._profile_manager.set_active_profile(name)
                except Exception as exc:
                    logger.warning("setup: state-service activation failed: %s", exc)

            # Fallback for legacy/non-RPC wiring: prefer the coordinator
            # (reinitializes the provider).
            # Bound it with a timeout — provider init can make a network call,
            # and a slow/unreachable endpoint must not freeze the wizard on
            # "Saving...". The profile is already persisted above, and
            # set_active_profile below guarantees activation regardless.
            llm = self._llm_service
            if not activated and llm and hasattr(llm, "switch_profile"):
                try:
                    activated = bool(
                        await asyncio.wait_for(
                            llm.switch_profile(name, persist=True), timeout=20.0
                        )
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "setup: switch_profile timed out; activating without reinit"
                    )
                except Exception as exc:
                    logger.warning("setup: switch_profile failed: %s", exc)
            if not activated:
                self._profile_manager.set_active_profile(name)

            self.result_saved = True
            self.result_profile_name = name

            endpoint = (
                self._base_url or provider.default_base_url or "(provider default)"
            )
            self._done_summary_lines = [
                f"profile:  {name}",
                f"provider: {provider.provider}",
                f"model:    {self._model}",
                f"endpoint: {endpoint}",
                "saved to config.json and set active.",
            ]
            self.result_summary = (
                f"provider ready — profile '{name}' ({provider.provider}) "
                f"model {self._model}, active now."
            )
            self._stage = STAGE_DONE
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._save_error = self._short_error(str(exc))
            self._input_error = ""
            logger.error("setup: save failed: %s", exc)
            self._stage = STAGE_ERROR

    def _unique_profile_name(self, base: str) -> str:
        existing = set()
        pm = self._profile_manager
        if pm is not None and hasattr(pm, "_profiles"):
            existing = set(pm._profiles.keys())
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    # -- rendering -----------------------------------------------------------

    def _render_header(self, width: int, theme: Any) -> None:
        r = self._renderer
        title = "  Provider Setup  "
        r.write_at(0, 0, solid_fg(str(C["half_bottom"]) * width, theme.primary[0]), "")
        r.write_at(
            0,
            1,
            solid(title.ljust(width), theme.primary[0], theme.text_dark, width),
            "",
        )

    def _current_step_index(self) -> int:
        mapping = {
            STAGE_PROVIDER: 0,
            STAGE_API_KEY: 1,
            STAGE_BASE_URL: 2,
            STAGE_MODEL: 3,
            STAGE_MODEL_CUSTOM: 3,
            STAGE_REVIEW: 4,
            STAGE_TESTING: 4,
            STAGE_SAVING: 4,
            STAGE_DONE: 4,
            STAGE_ERROR: 4,
        }
        return mapping.get(self._stage, 0)

    def _render_steps(self, width: int, theme: Any) -> None:
        r = self._renderer
        current = self._current_step_index()
        cells: List[str] = []
        for idx, label in enumerate(_STEPS):
            marker = "•"
            if idx < current:
                marker = "✓"  # done
            prefix = ">" if idx == current else " "
            cells.append(f"{prefix}{marker} {idx + 1}.{label}")
        line = "  ".join(cells)
        r.write_at(2, 3, line[: width - 2], "")

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        r = self._renderer
        footer_y = height - 2
        r.write_at(
            0, footer_y, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), ""
        )
        hints = {
            STAGE_PROVIDER: " up/down move | enter select | esc cancel",
            STAGE_API_KEY: " type/paste key | enter next | esc back",
            STAGE_BASE_URL: " edit endpoint | enter accept | esc back",
            STAGE_MODEL: " up/down move | enter select | esc back",
            STAGE_MODEL_CUSTOM: " type model | enter accept | esc back",
            STAGE_REVIEW: " enter save & activate | t test connection | esc back",
            STAGE_TESTING: " testing connection...",
            STAGE_SAVING: " saving...",
            STAGE_DONE: " any key to finish",
            STAGE_ERROR: " any key to go back",
        }
        text = hints.get(self._stage, " esc cancel")
        r.write_at(
            0,
            footer_y + 1,
            solid(text.ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )

    def _render_body(self, width: int, height: int, theme: Any) -> None:
        top = 5
        avail = max(4, height - top - 3)
        if self._stage == STAGE_PROVIDER:
            self._render_provider_stage(top, width, avail, theme)
        elif self._stage == STAGE_API_KEY:
            self._render_api_key_stage(top, width, theme)
        elif self._stage == STAGE_BASE_URL:
            self._render_base_url_stage(top, width, theme)
        elif self._stage == STAGE_MODEL:
            self._render_model_stage(top, width, avail, theme)
        elif self._stage == STAGE_MODEL_CUSTOM:
            self._render_model_custom_stage(top, width, theme)
        elif self._stage == STAGE_REVIEW:
            self._render_review_stage(top, width, theme)
        elif self._stage == STAGE_TESTING:
            self._render_spinner_stage(top, width, theme, "Testing connection")
        elif self._stage == STAGE_SAVING:
            self._render_spinner_stage(top, width, theme, "Saving profile")
        elif self._stage == STAGE_DONE:
            self._render_done_stage(top, width, theme)
        elif self._stage == STAGE_ERROR:
            self._render_error_stage(top, width, theme)

    def _write(self, x: int, y: int, text: str, width: int) -> None:
        self._renderer.write_at(x, y, text[: max(0, width - x)], "")

    def _render_provider_stage(
        self, top: int, width: int, avail: int, theme: Any
    ) -> None:
        self._write(2, top, "Which provider do you want to use?", width)
        y = top + 2
        list_h = max(1, avail - 3)
        for idx, choice in enumerate(PROVIDERS[:list_h]):
            selected = idx == self._provider_index
            cursor = ">" if selected else " "
            label = f"{cursor} {choice.label}"
            bg = theme.primary[0] if selected else theme.dark[0]
            fg = theme.text_dark if selected else theme.text
            self._renderer.write_at(
                2,
                y + idx,
                solid(label.ljust(width - 4), bg, fg, width - 4),
                "",
            )
        # blurb for the highlighted provider
        choice = PROVIDERS[self._provider_index]
        blurb_y = y + min(len(PROVIDERS), list_h) + 1
        self._write(2, blurb_y, choice.blurb, width)
        if choice.key_url:
            self._write(2, blurb_y + 1, f"get a key: {choice.key_url}", width)

    def _render_field_stage(
        self,
        top: int,
        width: int,
        theme: Any,
        prompt: str,
        value_display: str,
        hint_lines: List[str],
    ) -> None:
        provider = self._provider
        label = provider.label.strip() if provider else ""
        self._write(2, top, f"{label}", width)
        self._write(2, top + 2, prompt, width)
        # input box
        box_w = min(max(24, len(value_display) + 4), width - 4)
        boxed = f" {value_display}_ "
        self._renderer.write_at(
            2,
            top + 3,
            solid(boxed.ljust(box_w), theme.dark[0], theme.text, box_w),
            "",
        )
        y = top + 5
        for line in hint_lines:
            self._write(2, y, line, width)
            y += 1
        if self._input_error:
            self._renderer.write_at(
                2,
                y + 1,
                solid_fg(f"! {self._input_error}"[: width - 4], theme.error[0]),
                "",
            )

    def _render_api_key_stage(self, top: int, width: int, theme: Any) -> None:
        provider = self._provider
        hint = []
        if provider:
            if provider.key_hint:
                hint.append(f"format: {provider.key_hint}")
            if provider.key_url:
                hint.append(f"get a key: {provider.key_url}")
            if not provider.needs_key:
                hint.append("(optional — press enter to skip for a local server)")
        hint.append("the key is stored in config.json on this machine only.")
        self._render_field_stage(
            top,
            width,
            theme,
            "Paste your API key:",
            _mask_key(self._api_key),
            hint,
        )

    def _render_base_url_stage(self, top: int, width: int, theme: Any) -> None:
        provider = self._provider
        hints = []
        if provider and provider.base_url_advanced:
            hints.append("press enter to accept the default endpoint.")
        if provider and provider.base_url_required:
            hints.append("example: http://localhost:1234/v1/chat/completions")
        hints.append("must be https:// (localhost is allowed).")
        self._render_field_stage(
            top,
            width,
            theme,
            "Endpoint URL:",
            self._base_url,
            hints,
        )

    def _render_model_stage(self, top: int, width: int, avail: int, theme: Any) -> None:
        provider = self._provider
        label = provider.label.strip() if provider else ""
        self._write(2, top, f"{label} — choose a model:", width)
        y = top + 2
        rows = list(self._model_options) + [_CUSTOM_MODEL_ROW]
        list_h = max(1, avail - 3)
        # simple scroll window around the selection
        start = max(0, min(self._model_index - list_h + 1, len(rows) - list_h))
        start = max(0, start)
        visible = rows[start : start + list_h]
        for offset, model in enumerate(visible):
            idx = start + offset
            selected = idx == self._model_index
            cursor = ">" if selected else " "
            suffix = ""
            if provider and provider.default_model and model == provider.default_model:
                suffix = "  (recommended)"
            text = f"{cursor} {model}{suffix}"
            bg = theme.primary[0] if selected else theme.dark[0]
            fg = theme.text_dark if selected else theme.text
            self._renderer.write_at(
                2,
                y + offset,
                solid(text.ljust(width - 4), bg, fg, width - 4),
                "",
            )

    def _render_model_custom_stage(self, top: int, width: int, theme: Any) -> None:
        hints = ["type the exact model id your provider expects."]
        provider = self._provider
        if provider and provider.provider == "openrouter":
            hints.append("format: vendor/model — browse https://openrouter.ai/models")
        self._render_field_stage(
            top,
            width,
            theme,
            "Model name:",
            self._model_custom,
            hints,
        )

    def _render_review_stage(self, top: int, width: int, theme: Any) -> None:
        provider = self._provider
        self._write(2, top, "Review — everything look right?", width)
        endpoint = (
            self._base_url
            or (provider.default_base_url if provider else "")
            or "(provider default)"
        )
        rows = [
            ("provider", provider.provider if provider else ""),
            ("model", self._model),
            ("endpoint", endpoint),
            ("api key", _mask_key(self._api_key) or "(none)"),
        ]
        y = top + 2
        for key, val in rows:
            self._write(2, y, f"{key:<10} {val}", width)
            y += 1

        y += 1
        if self._test_ok is True:
            self._renderer.write_at(
                2, y, solid_fg(f"✓ {self._test_msg}"[: width - 4], theme.success[0]), ""
            )
        elif self._test_ok is False:
            self._renderer.write_at(
                2,
                y,
                solid_fg(
                    f"✗ test failed: {self._test_msg}"[: width - 4], theme.error[0]
                ),
                "",
            )
        else:
            self._write(
                2, y, "tip: press t to test the connection before saving.", width
            )

    def _render_spinner_stage(
        self, top: int, width: int, theme: Any, label: str
    ) -> None:
        now = time.monotonic()
        if now - self._last_spin > 0.12:
            self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner)
            self._last_spin = now
        spin = self._spinner[self._spinner_idx]
        self._write(2, top + 1, f"{spin} {label}...", width)

    def _render_done_stage(self, top: int, width: int, theme: Any) -> None:
        self._renderer.write_at(
            2, top, solid_fg("✓ Provider ready", theme.success[0]), ""
        )
        y = top + 2
        for line in self._done_summary_lines:
            self._write(2, y, line, width)
            y += 1
        self._write(
            2,
            y + 1,
            "you can now send a message. switch provider or model with /llm.",
            width,
        )

    def _render_error_stage(self, top: int, width: int, theme: Any) -> None:
        self._renderer.write_at(
            2, top, solid_fg("✗ Setup could not finish", theme.error[0]), ""
        )
        self._write(2, top + 2, self._save_error or "unknown error", width)
        self._write(
            2, top + 4, "press any key to go back and adjust your settings.", width
        )
