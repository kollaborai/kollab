"""Setup panel (``/setup``) and the provider-setup functions the terminal wizard shares.

``ProviderChoice``, ``PROVIDERS`` and ``load_model_suggestions`` moved here from
``plugins/altview/setup_altview.py``; the wizard imports them back, and calls
``test_connection`` / ``create_and_activate_profile`` for its test and save steps.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import urlparse

from ..state.interface import ProfileActivationError
from .base import PanelError, error_result, make_field, ok_result, unknown_action
from .llm import short_error

logger = logging.getLogger(__name__)


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
        default_model="gpt-5.6-luna",
    ),
    ProviderChoice(
        key="openai-chatgpt",
        label="OpenAI  (sign in with ChatGPT)",
        provider="openai_responses",
        blurb="Use your ChatGPT subscription via OAuth — no API key needed.",
        default_base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.6-luna",
        needs_key=False,
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
        blurb="Ollama, LM Studio, vLLM, or any OpenAI-compatible server.",
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
        blurb="Azure OpenAI and other custom setups go in config.json.",
        advanced=True,
    ),
]


def load_model_suggestions(provider_value: str) -> List[str]:
    """Curated model names for a provider from the shared model registry."""
    try:
        from kollabor_ai.model_registry import (
            PROVIDER_MODEL_SOURCE,
            get_model_registry,
        )

        models = get_model_registry().get("models", {})
        model_provider = PROVIDER_MODEL_SOURCE.get(provider_value, provider_value)
    except Exception as exc:  # registry is best-effort
        logger.debug("setup: model registry unavailable: %s", exc)
        return []

    names = [
        name
        for name, meta in models.items()
        if isinstance(meta, dict)
        and meta.get("provider") == model_provider
        and not meta.get("retired")
    ]
    # Stable, readable order (registry dicts are insertion-ordered already).
    return names



# -- shared by the terminal wizard and the panel -------------------------------


def valid_base_url(url: str) -> bool:
    """https anywhere, plain http only for a loopback host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")


def redact_error(text: str, *secrets: str) -> str:
    """One short line of provider error text with keys and tokens removed.

    Known key patterns go through ``LoggingRedactor``; the exact secrets the
    caller typed are removed too, since a provider may echo them in any shape.
    """
    from kollabor_ai.providers.security import LoggingRedactor

    out = str(LoggingRedactor.redact(str(text)))
    for secret in secrets:
        if secret and len(secret) >= 6:
            out = out.replace(secret, "[REDACTED]")
    return short_error(out)


async def test_connection(
    provider: str, model: str, api_key: str, base_url: str, timeout: float = 25.0
) -> tuple[bool, str]:
    """Ask the provider for one word. Returns (ok, one redacted line)."""
    try:
        from kollabor_ai.providers.registry import (
            ProviderRegistry,
            create_config_from_profile,
        )

        config = create_config_from_profile(
            {
                "provider": provider or "custom",
                "model": model,
                "api_key": api_key or "",
                "base_url": base_url or "",
            }
        )
        client = await ProviderRegistry.create_provider(config)
        try:
            response = await asyncio.wait_for(
                client.call(
                    messages=[{"role": "user", "content": "Reply with the single word: OK"}]
                ),
                timeout=timeout,
            )
            text = (response.get_text_content() or "").strip()
            return True, f"connected — model replied: {text[:40] if text else '(empty response)'}"
        finally:
            try:
                await client.shutdown()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                pass
    except asyncio.TimeoutError:
        return False, f"timed out after {timeout:.0f}s — check the server address / network"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported as the test result
        message = redact_error(str(exc), api_key)
        logger.info("setup: connection test failed: %s", message)
        return False, message


test_connection.__test__ = False  # not a pytest test


def unique_profile_name(profile_manager: Any, base: str) -> str:
    existing = set(getattr(profile_manager, "_profiles", None) or {})
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


async def create_and_activate_profile(
    profile_manager: Any,
    provider: ProviderChoice,
    *,
    base_url: str,
    model: str,
    api_key: str,
    state_service: Any = None,
    llm_service: Any = None,
) -> str:
    """Create a user profile for ``provider`` and make it active; returns its name.

    Never fills a provider built-in in place: an existing name gets a suffix.
    ``state_service`` (the daemon's, or the attach client's RPC bridge) activates
    through the daemon that owns the request provider. ``llm_service`` is the
    fallback when that bridge is unavailable or fails; if it exists but cannot
    switch, activation fails instead of changing only the saved active profile.
    The profile-manager-only fallback is reserved for setups without an LLM
    service.
    """
    if not profile_manager:
        raise RuntimeError("profile manager unavailable")
    name = provider.key
    if profile_manager.get_profile(name) is not None:
        name = unique_profile_name(profile_manager, provider.key)
    created = profile_manager.create_profile(
        name=name,
        base_url=base_url or "",
        model=model,
        api_key=api_key or None,
        provider=provider.provider,
        supports_tools=True,
        description=f"Created via /setup ({provider.label.strip()})",
        save_to_config=True,
    )
    if not created:
        raise RuntimeError(f"could not create profile '{name}'")

    activated = False
    if state_service is not None and hasattr(state_service, "set_active_profile"):
        try:
            await state_service.set_active_profile(name, persist=True, reload_profile=True)
            activated = True
            # Keep this process's manager aligned without writing config twice.
            try:
                profile_manager.set_active_profile(name, persist=False)
            except TypeError:
                profile_manager.set_active_profile(name)
        except ProfileActivationError as exc:
            logger.warning(
                "setup: provider activation failed (previous profile restored=%s)",
                exc.previous_profile_restored,
            )
            if exc.previous_profile_restored:
                message = f"Could not activate '{name}': the previous profile remains active."
            else:
                message = f"Could not activate '{name}': the previous profile could not be restored."
            raise RuntimeError(message) from None
        except Exception as exc:  # noqa: BLE001 - fall through to the fallbacks
            logger.warning(
                "setup: state-service activation failed: %s",
                redact_error(str(exc), api_key),
            )
    if not activated:
        if llm_service is None:
            # First-run setups without an LLM service can update the manager directly.
            profile_manager.set_active_profile(name)
        else:
            switch_profile = getattr(llm_service, "switch_profile", None)
            if not callable(switch_profile):
                raise RuntimeError(
                    f"Could not activate '{name}': no LLM profile-switch method is available."
                )
            try:
                activated = bool(
                    await asyncio.wait_for(
                        switch_profile(name, persist=True),
                        timeout=20.0,
                    )
                )
            except asyncio.TimeoutError as exc:
                logger.warning("setup: switch_profile timed out; active profile unchanged")
                raise RuntimeError(
                    f"Could not activate '{name}': switch timed out after 20 seconds."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "setup: switch_profile failed (%s); active profile unchanged",
                    type(exc).__name__,
                )
                raise RuntimeError(
                    f"Could not activate '{name}': the previous profile remains active."
                ) from exc
            if not activated:
                logger.warning("setup: switch_profile returned false; active profile unchanged")
                raise RuntimeError(f"Could not activate '{name}': LLM profile switch rejected it.")
    return name


# -- the panel ----------------------------------------------------------------

_MAX_FIELD = 4096


def _label(choice: ProviderChoice) -> str:
    return " ".join(choice.label.split())


def _selectable() -> list[ProviderChoice]:
    return [p for p in PROVIDERS if not p.advanced]


def _pick(value: str) -> Optional[ProviderChoice]:
    value = " ".join(str(value or "").split())
    for choice in _selectable():
        if value in (choice.key, _label(choice)):
            return choice
    return None


def _values(payload: dict) -> tuple[ProviderChoice, str, str, str]:
    """Validated (provider, api_key, base_url, model) or PanelError with errors."""
    errors: dict[str, str] = {}
    # a wizard action sends {"values": {path: value}, "step": id}
    values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    choice = _pick(values.get("provider"))
    if choice is None:
        errors["provider"] = "Choose a provider from the list."
    api_key = str(values.get("api_key") or "").strip()
    base_url = str(values.get("base_url") or "").strip()
    model = str(values.get("model") or "").strip()
    oauth_model = str(values.get("oauth_model") or "").strip()
    for key, value in (
        ("api_key", api_key),
        ("base_url", base_url),
        ("model", model),
        ("oauth_model", oauth_model),
    ):
        if len(value) > _MAX_FIELD or not value.isprintable():
            errors[key] = "That value is not valid."
    if choice is not None:
        base_url = base_url or choice.default_base_url
        if choice.oauth:
            model = oauth_model or model or choice.default_model
        else:
            model = model or choice.default_model
        if choice.needs_key and not api_key:
            errors.setdefault("api_key", "An API key is required.")
        if choice.base_url_required and not base_url:
            errors.setdefault("base_url", "A server address is required.")
        elif base_url and not valid_base_url(base_url):
            errors.setdefault("base_url", "Use an https:// URL (http only for localhost).")
        if not model:
            errors.setdefault("model", "Enter a model name.")
    if errors:
        raise PanelError("Check the highlighted fields.", errors=errors)
    assert choice is not None
    return choice, api_key, base_url, model


class SetupPanel:
    """``/setup``: configure a new provider profile. Nothing is kept between calls."""

    name = "setup"
    kind = "wizard"

    def __init__(self) -> None:
        self._oauth_flows: dict[str, dict[str, Any]] = {}

    async def _complete_oauth(self, flow_id: str) -> None:
        flow = self._oauth_flows[flow_id]
        client = flow["client"]
        try:
            auth = await client._poll_for_auth_code(flow["device"])
            flow["tokens"] = await client._exchange_code(
                auth.authorization_code, auth.code_verifier
            )
            flow["status"] = "signed_in"
            flow["message"] = "Signed in to ChatGPT."
            flow["account"] = flow["tokens"].account_id or "ChatGPT account"
        except asyncio.CancelledError:
            flow["status"] = "cancelled"
            raise
        except Exception as exc:  # surfaced as a short status, never credentials
            text = str(exc)
            lowered = text.lower()
            if "expir" in lowered or "timed out waiting for authorization" in lowered:
                flow["status"] = "expired"
                flow["message"] = redact_error(text)
            elif "denied" in lowered:
                flow["status"] = "denied"
                flow["message"] = redact_error(text)
            elif any(word in lowered for word in ("network", "connection")) or isinstance(exc, asyncio.TimeoutError):
                flow["status"] = "network_error"
                flow["message"] = f"Network error: {redact_error(text)}"
            else:
                flow["status"] = "failed"
                flow["message"] = redact_error(text)
            logger.warning("setup: ChatGPT sign-in failed (%s)", type(exc).__name__)
        finally:
            try:
                await client.close()
            except Exception:
                pass

    def _oauth_context(self, flow_id: str | None) -> dict[str, Any]:
        flow = self._oauth_flows.get(flow_id or "")
        if not flow:
            return {}
        return {
            "oauth_flow_id": flow_id,
            "verification_url": flow.get("verification_url"),
            "user_code": flow.get("user_code"),
            "interval": flow.get("interval"),
            "expires_in": flow.get("expires_in"),
            "oauth_status": flow.get("status", "waiting"),
            "oauth_message": flow.get("message", "Waiting for sign-in…"),
            "account": flow.get("account", ""),
        }

    async def _oauth_start(self, ctx: Any, payload: dict | None = None) -> dict:
        from kollabor_ai.oauth.openai_oauth import (
            DEVICE_CODE_TIMEOUT,
            DEVICE_VERIFY_URL,
            OpenAIOAuthClient,
        )

        previous_flow = str((payload or {}).get("flow_id") or "")
        if previous_flow:
            await self._oauth_cancel({"flow_id": previous_flow})

        client = OpenAIOAuthClient()
        try:
            device = await client._request_device_code()
        except Exception:
            await client.close()
            return {
                "ok": False,
                "message": "Network error while starting sign-in. Check your connection and try again.",
                "panel": await self.describe(
                    ctx,
                    {
                        "oauth_status": "network_error",
                        "oauth_message": "Network error while starting sign-in. Check your connection and try again.",
                    },
                ),
            }

        now = time.monotonic()
        for old_id, old in list(self._oauth_flows.items()):
            if old.get("task") is None or old["task"].done():
                if now - old.get("created_at", now) > 900:
                    self._oauth_flows.pop(old_id, None)
        if len(self._oauth_flows) >= 16:
            await client.close()
            return {"ok": False, "message": "Too many sign-in attempts are active. Cancel one and try again."}

        flow_id = uuid.uuid4().hex
        flow = {
            "client": client,
            "device": device,
            "status": "waiting",
            "message": "Waiting for sign-in…",
            "verification_url": DEVICE_VERIFY_URL,
            "user_code": device.user_code,
            "interval": max(1, device.interval),
            "expires_in": DEVICE_CODE_TIMEOUT,
            "created_at": now,
        }
        client.status_callback = lambda status, message: flow.update(
            status=status, message=message
        )
        self._oauth_flows[flow_id] = flow
        flow["task"] = asyncio.create_task(self._complete_oauth(flow_id))
        return {
            "ok": True,
            "message": "Waiting for sign-in…",
            "flow_id": flow_id,
            "verification_url": DEVICE_VERIFY_URL,
            "user_code": device.user_code,
            "interval": max(1, device.interval),
            "expires_in": DEVICE_CODE_TIMEOUT,
            "status": "waiting",
            "poll": {"action": "oauth_poll", "payload": {"flow_id": flow_id}, "every_s": max(1, device.interval)},
            "panel": await self.describe(ctx, {"flow_id": flow_id}),
        }

    async def _oauth_poll(self, ctx: Any, payload: dict) -> dict:
        flow_id = str(payload.get("flow_id") or "")
        flow = self._oauth_flows.get(flow_id)
        if flow is None:
            return {"ok": False, "message": "Sign-in expired. Start again."}
        status = flow.get("status", "waiting")
        pending = status in ("waiting", "network_error")
        result = {
            "ok": status in ("waiting", "signed_in"),
            "status": "waiting" if status == "waiting" else ("signed_in" if status == "signed_in" else "failed"),
            "message": flow.get("message", "Waiting for sign-in…"),
            "panel": await self.describe(ctx, {"flow_id": flow_id}),
        }
        if pending:
            result["poll"] = {
                "action": "oauth_poll",
                "payload": {"flow_id": flow_id},
                "every_s": max(1, flow["device"].interval),
            }
        return result

    async def _oauth_cancel(self, payload: dict) -> dict:
        flow_id = str(payload.get("flow_id") or "")
        flow = self._oauth_flows.pop(flow_id, None)
        if flow:
            task = flow.get("task")
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            try:
                await flow["client"].close()
            except Exception:
                pass
        return {"ok": True, "message": "Sign-in cancelled."}

    async def describe(self, ctx: Any, params: dict) -> dict:
        choices = _selectable()
        oauth_models = load_model_suggestions("openai_responses")
        steps = [
            {
                "id": "provider",
                "title": "Provider",
                "fields": [
                    make_field(
                        "provider", "dropdown", "Provider",
                        value=_label(choices[0]), options=[_label(p) for p in choices],
                    ),
                    make_field(
                        "oauth_note", "label", "Azure / Advanced",
                        value="Azure and advanced setups are configured in config.json.",
                    ),
                ],
            },
            {
                "id": "api_key",
                "title": "API Key",
                "fields": [
                    make_field(
                        "api_key", "text_input", "API Key", secret=True,
                        help="Stored in your provider profile. Not needed for local servers.",
                    )
                ],
            },
            {
                "id": "endpoint",
                "title": "Server",
                "fields": [
                    make_field(
                        "base_url", "text_input", "Server Address",
                        placeholder="Leave empty for the provider default",
                    )
                ],
            },
            {
                "id": "oauth_sign_in",
                "title": "Sign In",
                "fields": [],
                "actions": [
                    {"id": "oauth_start", "label": "Try Again"},
                    {"id": "oauth_cancel", "label": "Cancel"},
                ],
            },
            {
                "id": "model",
                "title": "Model",
                "fields": [
                    make_field(
                        "model", "text_input", "Model",
                        placeholder="Leave empty for the suggested model",
                    ),
                    make_field(
                        "oauth_model", "dropdown", "Model",
                        value=oauth_models[0] if oauth_models else "gpt-5.6-luna",
                        options=oauth_models,
                    ),
                ],
            },
            {
                "id": "oauth_review",
                "title": "Review",
                "fields": [
                    make_field(
                        "oauth_review_note", "label", "ChatGPT",
                        value="Your ChatGPT sign-in and selected model will be saved and activated.",
                    )
                ],
            },
        ]
        context = self._oauth_context(params.get("flow_id"))
        if not context and params.get("oauth_status"):
            context = {
                "oauth_status": params["oauth_status"],
                "oauth_message": params.get("oauth_message", ""),
            }
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": "Setup",
            "steps": steps,
            "context": context,
            "providers": [
                {
                    "value": _label(p),
                    "blurb": p.blurb,
                    "default_base_url": p.default_base_url,
                    "default_model": p.default_model,
                    "key_hint": p.key_hint,
                    "key_url": p.key_url,
                    "needs_key": p.needs_key,
                    "base_url_required": p.base_url_required,
                    "models": load_model_suggestions(p.provider),
                }
                for p in choices
            ],
            "actions": [
                {"id": "test", "label": "Test Connection"},
                {"id": "finish", "label": "Save and Activate"},
                {"id": "oauth_start", "label": "Start Sign-In"},
                {"id": "oauth_poll", "label": "Check Sign-In"},
                {"id": "oauth_cancel", "label": "Cancel"},
            ],
        }

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        if action == "oauth_start":
            return await self._oauth_start(ctx, payload)
        if action == "oauth_poll":
            return await self._oauth_poll(ctx, payload)
        if action == "oauth_cancel":
            return await self._oauth_cancel(payload)
        if action not in ("test", "finish"):
            raise unknown_action(self.name, action)
        choice, api_key, base_url, model = _values(payload)
        if action == "test":
            if choice.oauth:
                return error_result("Test Connection is unavailable before ChatGPT sign-in.")
            ok, message = await test_connection(choice.provider, model, api_key, base_url)
            return ok_result(message) if ok else error_result(message)
        if choice.oauth:
            flow_id = str(payload.get("oauth_flow_id") or "")
            flow = self._oauth_flows.get(flow_id)
            if not flow or flow.get("status") != "signed_in" or not flow.get("tokens"):
                return error_result("Sign in with ChatGPT before saving this profile.")
            from kollabor_ai.oauth import OAuthTokenStorage
            from kollabor_ai.oauth.profile import save_openai_oauth_profile

            tokens = flow["tokens"]
            await OAuthTokenStorage().store_tokens("openai", tokens)
            name = save_openai_oauth_profile(ctx._profile_manager, tokens, model)
            if hasattr(ctx, "set_active_profile"):
                await ctx.set_active_profile(name, persist=True, reload_profile=True)
            else:
                ctx._profile_manager.set_active_profile(name, persist=True)
            self._oauth_flows.pop(flow_id, None)
            return ok_result(
                f"Provider ready: profile '{name}' (openai_responses), model {model}, active now.",
                panel=await self.describe(ctx, {}),
            )
        try:
            name = await create_and_activate_profile(
                ctx._profile_manager,
                choice,
                base_url=base_url,
                model=model,
                api_key=api_key,
                state_service=ctx,
                llm_service=getattr(ctx, "_llm_service", None),
            )
        except Exception as exc:  # noqa: BLE001 - shown to the person, redacted
            logger.error("setup: save failed: %s", redact_error(str(exc), api_key))
            return error_result(redact_error(str(exc), api_key))
        return ok_result(
            f"Provider ready: profile '{name}' ({choice.provider}), model {model}, active now.",
            panel=await self.describe(ctx, {}),
        )


PANELS = {SetupPanel.name: SetupPanel()}
