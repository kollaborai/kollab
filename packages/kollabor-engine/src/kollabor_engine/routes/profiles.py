"""Profile management routes."""

import logging
import time
from dataclasses import replace
from typing import Annotated, Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException  # type: ignore
from pydantic import AfterValidator, BaseModel, Field

from kollabor_ai import APICommunicationService, LLMProfile, ProfileManager
from kollabor_ai.profile_manager import EFFORT_LEVELS, redact_profile_endpoint
from kollabor_ai.providers import (
    AuthenticationError,
    ProviderError,
    ProviderType,
    RateLimitError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profiles", tags=["profiles"])


def _normalize_effort(value: Optional[str]) -> Optional[str]:
    """Strip/lower effort; reject unknown non-empty values with a 422.

    Empty string means "model default" (the request omits effort entirely).
    Unknown values are rejected here because LLMProfile.get_effort() would
    silently drop them at request time anyway -- failing at write time makes
    the typo visible in the UI instead of a profile that never applies.
    """
    if value is None:
        return None
    value = value.strip().lower()
    if value and value not in EFFORT_LEVELS:
        raise ValueError(
            f"effort must be one of: {', '.join(EFFORT_LEVELS)} (or empty for model default)"
        )
    return value


# Validated in the request models so a bad value never reaches persistence.
EffortField = Annotated[Optional[str], AfterValidator(_normalize_effort)]


# Request/Response Models
class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1, description="Profile name")
    provider: Optional[str] = Field(
        default=None, min_length=1, description="Provider type (one of GET /profiles providers)"
    )
    model: Optional[str] = Field(default=None, description="Model name")
    copy_from: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Copy an eligible stored profile; hidden fields are copied server-side.",
    )
    api_key: str = Field(default="", description="API key (optional, can use env vars)")
    base_url: str = Field(default="", description="Custom endpoint URL")
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling temperature"
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=1, description="Max tokens to generate"
    )
    description: str = Field(default="", description="Human-readable description")
    timeout: int = Field(
        default=0, ge=0, description="Request timeout in milliseconds (0 = no timeout)"
    )
    top_p: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Nucleus sampling"
    )
    effort: EffortField = Field(
        default=None,
        description=(
            "Reasoning effort: low, medium, high, xhigh, max, ultra — or "
            "empty/None for the model default. Sent as Anthropic "
            "output_config.effort / OpenAI reasoning_effort."
        ),
    )
    streaming: bool = Field(default=True, description="Enable streaming responses")
    supports_tools: bool = Field(
        default=True, description="Enable tool/function calling"
    )
    extra_headers: Dict[str, str] = Field(
        default_factory=dict, description="Additional HTTP headers"
    )


class UpdateProfileRequest(BaseModel):
    new_name: Optional[str] = Field(
        default=None, min_length=1, description="New profile name for rename"
    )
    provider: Optional[str] = Field(default=None, min_length=1)
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    description: Optional[str] = None
    timeout: Optional[int] = Field(default=None, ge=0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    effort: EffortField = None
    streaming: Optional[bool] = None
    supports_tools: Optional[bool] = None
    extra_headers: Optional[Dict[str, str]] = None


def _get_profile_manager() -> ProfileManager:
    """Get a ProfileManager instance.

    ProfileManager reads from ~/.kollab/config.json by default.
    """
    return ProfileManager()


def _safe_acp_adapter(command: Any) -> Optional[str]:
    """Return a known adapter or safe executable label, never raw arguments."""
    if not isinstance(command, (list, tuple)) or not command:
        return None

    known_packages = {
        "@agentclientprotocol/claude-agent-acp": "claude-agent-acp",
        "@agentclientprotocol/codex-acp": "codex-acp",
        "@google/gemini-cli": "gemini-cli",
    }
    for part in command:
        if not isinstance(part, str):
            continue
        for package, label in known_packages.items():
            if part == package or part.startswith(f"{package}@"):
                return label

    executable = command[0]
    if not isinstance(executable, str) or not executable:
        return None
    basename = executable.replace(chr(92), "/").rsplit("/", 1)[-1]
    safe_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
    sensitive_words = ("token", "secret", "password", "credential", "apikey")
    if (
        not basename
        or not all(character in safe_characters for character in basename)
        or any(word in basename.lower() for word in sensitive_words)
    ):
        return "ACP adapter configured"
    return basename


def _redacted_profile_dict(profile: LLMProfile) -> Dict[str, Any]:
    """Return profile metadata safe for list/get/create/update API responses."""
    result = profile.to_dict()
    for key in (
        "api_key",
        "api_token",
        "access_token",
        "refresh_token",
        "oauth_token",
        "client_secret",
        "secret",
        "password",
        "token",
        "authorization",
        "cookie",
        "api_key_from_env",
    ):
        result.pop(key, None)

    env = result.pop("env", None)
    if isinstance(env, dict):
        result["env_keys"] = sorted(str(key) for key in env)

    extra_headers = result.pop("extra_headers", None)
    if isinstance(extra_headers, dict):
        result["extra_header_names"] = sorted(
            str(key) for key in extra_headers
        )

    command = result.pop("command", None)
    if str(result.get("provider", "")).lower() == "acp":
        result["acp_adapter"] = _safe_acp_adapter(command)

    base_url, has_hidden_data = redact_profile_endpoint(result.get("base_url"))
    if "base_url" in result:
        result["base_url"] = base_url
    result["base_url_has_hidden_data"] = has_hidden_data

    # Runtime paths, pass-through variable names, and install text are not
    # needed by the browser; copies preserve them server-side via copy_from.
    for key in ("cwd", "workspace", "pass_env", "install_guidance"):
        result.pop(key, None)
    return result


class _DictConfig:
    """Small config adapter for profile connectivity checks."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)


@router.get("")
async def list_profiles():
    """List all available profiles."""
    pm = _get_profile_manager()
    profiles = pm.list_profiles()

    return {
        "profiles": [_redacted_profile_dict(p) for p in profiles],
        "active": pm.active_profile_name,
        "count": len(profiles),
        "providers": [provider.value for provider in ProviderType],
        "invalid_profiles": getattr(pm, "get_invalid_profiles", lambda: {})(),
    }


@router.get("/{name}")
async def get_profile(name: str):
    """Get detailed info for a specific profile."""
    pm = _get_profile_manager()
    profile = pm.get_profile(name)

    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")

    result = _redacted_profile_dict(profile)
    result["env_var_hints"] = {
        k: {"name": v.name, "is_set": v.is_set}
        for k, v in profile.get_env_var_hints().items()
    }
    result["is_active"] = pm.is_active(name)

    return result


@router.post("", status_code=201)
async def create_profile(body: CreateProfileRequest):
    """Create a new profile or copy every stored setting from another profile."""
    pm = _get_profile_manager()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Profile name cannot be blank")

    if pm.get_profile(name):
        raise HTTPException(status_code=409, detail=f"Profile {name!r} already exists")

    if body.copy_from:
        overrides = {
            key: value
            for key, value in body.model_dump(
                exclude_unset=True, exclude={"name", "copy_from"}
            ).items()
            if value is not None
        }
        if not str(overrides.get("api_key", "")).strip():
            overrides.pop("api_key", None)
        try:
            profile = pm.duplicate_profile(
                body.copy_from, name, overrides=overrides, save_to_config=True
            )
        except ValueError as exc:
            detail = str(exc)
            status_code = (
                409
                if "already exists" in detail
                else 404
                if "not found" in detail
                else 422
            )
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        provider = (body.provider or "").strip()
        model = (body.model or "").strip()
        if not provider:
            raise HTTPException(status_code=422, detail="Provider is required")
        if provider == "acp":
            raise HTTPException(
                status_code=422,
                detail=(
                    "ACP profiles require a non-empty command. Create the profile "
                    "with its command in config, or copy a valid ACP profile."
                ),
            )
        if not model:
            raise HTTPException(status_code=422, detail="Model is required")

        profile = LLMProfile(
            name=name,
            provider=provider,
            model=model,
            api_key=body.api_key,
            base_url=body.base_url,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            description=body.description,
            timeout=body.timeout,
            top_p=body.top_p,
            effort=body.effort or "",
            streaming=body.streaming,
            supports_tools=body.supports_tools,
            extra_headers=body.extra_headers,
        )

        if not pm.add_profile(profile, save_to_config=True):
            if pm.get_profile(name) is not None:
                raise HTTPException(
                    status_code=409, detail=f"Profile {name!r} already exists"
                )
            raise HTTPException(
                status_code=500,
                detail=f"Profile {name!r} could not be saved to config",
            )

    result = _redacted_profile_dict(profile)
    result["created"] = True
    return result


@router.put("/{name}")
async def update_profile(name: str, body: UpdateProfileRequest):
    """Update an existing profile. Can also rename via new_name field."""
    pm = _get_profile_manager()

    # Get existing profile
    profile = pm.get_profile(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")

    target_provider = body.provider or profile.provider
    target_model = body.model if body.model is not None else profile.model
    if target_provider == "acp" and not profile.command:
        raise HTTPException(
            status_code=422,
            detail="ACP profiles require a non-empty command; configure the command first",
        )
    if target_provider != "acp" and not str(target_model or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A model is required for non-ACP profiles",
        )

    # Build kwargs for fields that were provided
    kwargs: Dict[str, Any] = {}
    if body.provider is not None:
        kwargs["provider"] = body.provider
    if body.model is not None:
        kwargs["model"] = body.model
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.temperature is not None:
        kwargs["temperature"] = body.temperature
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    if body.description is not None:
        kwargs["description"] = body.description
    if body.supports_tools is not None:
        kwargs["supports_tools"] = body.supports_tools
    if body.timeout is not None:
        kwargs["timeout"] = body.timeout
    if body.top_p is not None:
        kwargs["top_p"] = body.top_p
    if body.effort is not None:
        # "" clears the stored effort (back to model default) -- mirrors
        # ProfileManager.update_profile's documented ""-clears contract.
        kwargs["effort"] = body.effort
    if body.streaming is not None:
        kwargs["streaming"] = body.streaming
    if body.extra_headers is not None:
        kwargs["extra_headers"] = body.extra_headers

    # Handle rename
    if body.new_name:
        kwargs["new_name"] = body.new_name

    # Update (include save_to_config)
    kwargs["save_to_config"] = True
    ok = pm.update_profile(name, **kwargs)  # type: ignore[arg-type]

    if not ok:
        # Likely a rename collision
        if body.new_name:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot rename: profile '{body.new_name}' already exists",
            )
        raise HTTPException(status_code=400, detail="Update failed")

    # Get updated profile (name may have changed)
    updated_name = body.new_name or name
    updated_profile = pm.get_profile(updated_name)
    assert updated_profile is not None

    result = _redacted_profile_dict(updated_profile)
    result["updated"] = True

    return result


@router.delete("/{name}")
async def delete_profile(name: str):
    """Delete a profile. Cannot delete built-in or active profile."""
    pm = _get_profile_manager()

    # Check if profile exists first
    profile = pm.get_profile(name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")

    # Check built-in
    if name in pm.DEFAULT_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete built-in profile: {name}",
        )

    # Check active
    if name == pm.active_profile_name:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete active profile: {name}",
        )

    ok = pm.delete_profile(name)

    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete profile")

    return {"deleted": True, "name": name}


@router.post("/{name}/test")
async def test_profile(name: str):
    """Test a profile's API key and connectivity."""
    pm = _get_profile_manager()
    profile = pm.get_profile(name)

    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")

    start = time.monotonic()
    provider = profile.get_provider()
    warning = None

    try:
        if provider == "anthropic":
            test_profile = replace(profile, max_tokens=1, streaming=False)
            api_service = APICommunicationService(
                config=_DictConfig({"kollabor.llm.enable_streaming": False}),
                raw_conversations_dir=None,
                profile=test_profile,
            )
            try:
                ok = await api_service.initialize()
                if not ok:
                    raise RuntimeError(
                        api_service._provider_error or "Provider failed to initialize"
                    )
                await api_service.call_llm(
                    conversation_history=[{"role": "user", "content": "Hi"}],
                )
                message = "API key is valid"
            finally:
                await api_service.shutdown()

        elif provider == "openai":
            # List models to verify connection
            base_url = profile.get_endpoint() or "https://api.openai.com/v1"
            headers = {"Authorization": f"Bearer {profile.get_api_key()}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
                resp.raise_for_status()
            message = "API key is valid"

        else:
            # For custom/other providers, just verify the URL is reachable
            base_url = profile.get_endpoint() or "http://localhost:1234/v1"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(base_url)
            message = "Connected (model validation skipped)"
            warning = "Custom provider does not support model verification"

        latency = (time.monotonic() - start) * 1000

        result = {
            "success": True,
            "profile": name,
            "provider": provider,
            "message": message,
            "model": profile.get_model(),
            "latency_ms": round(latency, 1),
        }

        if warning:
            result["warning"] = warning

        return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {
                "success": False,
                "profile": name,
                "error": "authentication_failed",
                "message": "Invalid API key",
            }
        if e.response.status_code == 429:
            return {
                "success": False,
                "profile": name,
                "error": "rate_limited",
                "message": "Rate limited - try again later",
            }
        return {
            "success": False,
            "profile": name,
            "error": "api_error",
            "message": f"Provider returned HTTP {e.response.status_code}",
        }

    except (httpx.ConnectError, OSError):
        return {
            "success": False,
            "profile": name,
            "error": "connection_failed",
            "message": "Connection failed. Check the profile endpoint and network.",
        }

    except AuthenticationError:
        return {
            "success": False,
            "profile": name,
            "error": "authentication_failed",
            "message": "Invalid API key",
        }

    except RateLimitError:
        return {
            "success": False,
            "profile": name,
            "error": "rate_limited",
            "message": "Rate limited - try again later",
        }

    except ProviderError:
        return {
            "success": False,
            "profile": name,
            "error": "api_error",
            "message": (
                "Provider test failed. Verify the profile's endpoint, credentials, "
                "and model, then retry."
            ),
        }

    except Exception as e:
        logger.warning(
            "Profile test failed for %s (%s)", name, type(e).__name__
        )
        return {
            "success": False,
            "profile": name,
            "error": "internal_error",
            "message": (
                "The profile test failed unexpectedly. Check the endpoint "
                "configuration and application logs."
            ),
        }
