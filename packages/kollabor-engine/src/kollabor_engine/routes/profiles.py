"""Profile management routes."""

import logging
import time
from dataclasses import replace
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException  # type: ignore
from pydantic import BaseModel, Field

from kollabor_ai import APICommunicationService, LLMProfile, ProfileManager
from kollabor_ai.providers import AuthenticationError, ProviderError, RateLimitError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profiles", tags=["profiles"])


# Request/Response Models
class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1, description="Profile name")
    provider: str = Field(
        min_length=1, description="Provider type (anthropic, openai, custom)"
    )
    model: str = Field(min_length=1, description="Model name")
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
    model: Optional[str] = Field(default=None, min_length=1)
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    description: Optional[str] = None
    timeout: Optional[int] = Field(default=None, ge=0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    streaming: Optional[bool] = None
    supports_tools: Optional[bool] = None
    extra_headers: Optional[Dict[str, str]] = None


def _get_profile_manager() -> ProfileManager:
    """Get a ProfileManager instance.

    ProfileManager reads from ~/.kollab/config.json by default.
    """
    return ProfileManager()


def _redacted_profile_dict(profile: LLMProfile) -> Dict[str, Any]:
    """Return a profile payload safe for API responses."""
    result = profile.to_dict()
    result.pop("api_key", None)
    result.pop("api_token", None)
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
    """Create a new profile."""
    pm = _get_profile_manager()

    # Check for existing profile
    if pm.get_profile(body.name):
        raise HTTPException(
            status_code=409, detail=f"Profile '{body.name}' already exists"
        )

    # Create profile
    profile = LLMProfile(
        name=body.name,
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
        base_url=body.base_url,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        description=body.description,
        timeout=body.timeout,
        top_p=body.top_p,
        streaming=body.streaming,
        supports_tools=body.supports_tools,
        extra_headers=body.extra_headers,
    )

    pm.add_profile(profile)
    pm.save_profile_values_to_config(profile)

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
            "message": str(e),
        }

    except (httpx.ConnectError, OSError) as e:
        return {
            "success": False,
            "profile": name,
            "error": "connection_failed",
            "message": f"Connection failed: {e}",
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

    except ProviderError as e:
        return {
            "success": False,
            "profile": name,
            "error": "api_error",
            "message": str(e),
        }

    except Exception as e:
        return {
            "success": False,
            "profile": name,
            "error": "internal_error",
            "message": str(e),
        }
