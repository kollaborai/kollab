"""Profile management routes."""

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query  # type: ignore
from pydantic import AfterValidator, BaseModel, Field

from kollabor_ai import APICommunicationService, LLMProfile, ProfileManager
from kollabor_ai.profile_manager import (
    EFFORT_LEVELS,
    effort_unsupported_reason,
    redact_profile_endpoint,
    resolve_effort_provider,
)
from kollabor_ai.providers import (
    AuthenticationError,
    ProviderError,
    ProviderType,
    RateLimitError,
)

from ..workspace_folder import folder_or_400

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profiles", tags=["profiles"])
_CHOICES_FINGERPRINT_KEYS: dict[Path, bytes] = {}


def _choices_fingerprint_key() -> bytes:
    """Load or create the private, stable key used to redact ACP launch inputs."""
    from kollabor_config.private_fs import ensure_private_dir
    from kollabor_engine.auth import _token_file

    path = _token_file().with_name("model-choices-fingerprint.key")
    cached = _CHOICES_FINGERPRINT_KEYS.get(path)
    if cached is not None:
        return cached

    ensure_private_dir(path.parent)

    def read_key() -> bytes:
        if path.is_symlink():
            raise RuntimeError("The model choices fingerprint key cannot be a symlink")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    try:
        key = read_key()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:  # another engine worker published the complete key first
                key = read_key()
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    if len(key) != 32:
        raise RuntimeError("The model choices fingerprint key is invalid")
    _CHOICES_FINGERPRINT_KEYS[path] = key
    return key


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


def _folder_profile_manager(raw_workspace: str) -> ProfileManager:
    """The profiles of ``raw_workspace``: its own ``.kollab`` local layer
    overrides the shared ones, so a folder-private profile is listed and can
    run. An empty workspace is the engine's own folder (the shared manager).

    Takes the raw query value: ``folder_or_400`` resolves empty to this
    engine's cwd, and only an actually named folder gets its own manager.
    """
    if not raw_workspace.strip():
        return _get_profile_manager()
    return ProfileManager(local_folder=Path(folder_or_400(raw_workspace.strip())))


def _safe_acp_adapter(command: Any) -> Optional[str]:
    """Return a known adapter or safe executable label, never raw arguments."""
    if not isinstance(command, (list, tuple)) or not command:
        return None

    known_packages = {
        "@agentclientprotocol/claude-agent-acp": "claude-agent-acp",
        "@agentclientprotocol/codex-acp": "codex-acp",
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
    if str(getattr(profile, "provider", "")).lower() == "acp":
        from kollabor.llm import acp_discovery

        launch = acp_discovery.launch_fingerprint(profile).encode()
        result["choices_fingerprint"] = hmac.new(
            _choices_fingerprint_key(), launch, hashlib.sha256
        ).hexdigest()
    # The key is dropped below, so the browser cannot resolve an auto profile itself.
    result["effort_provider"] = profile.get_effort_provider()
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


class _LayeredConfig:
    """A read-only dotted view (``get("kollabor.llm.loadouts")``) over a folder's user config layers."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, key: str, default=None):
        from kollabor_events.dict_utils import safe_get

        return safe_get(self._data, key, default)


def _loadout_config(workspace: str = ""):
    """The saved config as the terminal reads it when started in ``workspace``: loadouts and the default
    loadout live there. The global config, then that folder's project and local config; an empty
    ``workspace`` is the engine's own folder.

    Read from the files, not through a ConfigService: a service polls its file every second until it is
    shut down, and the open web UI lists profiles every few seconds.
    """
    from pathlib import Path

    from kollabor_config.loader import load_layered_user_config

    # Always a concrete folder: with none, the reader behaves as the terminal starting up and, on Windows,
    # claims the folder's project state directory. Listing profiles must never create one.
    folder = Path(workspace).expanduser().resolve() if workspace else Path.cwd()
    return _LayeredConfig(load_layered_user_config(folder))


def _default_loadout(pm: ProfileManager, workspace: str = "") -> Optional[Dict[str, str]]:
    """The loadout the terminal starts on (``kollabor.llm.default_loadout``) in ``workspace``.

    A web session always names a profile, which makes the terminal skip its
    default, so the form needs this to start on it (#957). The default belongs to
    the folder the session starts in: its project and local config can set their
    own, so it is read for ``workspace``, not for the engine's folder. ``None``
    when none is set or its saved name is not a loadout. The name must match
    exactly: the terminal's forgiving lookup would start on a similarly named
    loadout nobody chose once the default is deleted. Never an error, the profile
    list must still answer.
    """
    import dataclasses

    from kollabor_ai.loadout_manager import (
        LoadoutManager,
        acp_loadout_model,
        loadout_sendable_effort,
    )

    try:
        manager = LoadoutManager(pm, config=_loadout_config(workspace))
        name = manager.get_default()
        loadout = manager.get(name) if name else None
        profile = pm.get_profile(loadout.provider_profile) if loadout else None
        if loadout is None or profile is None:
            return None
        harness = profile.get_provider() == "acp"
        if harness:
            effort = loadout.effort
        else:
            # A loadout with no level keeps its profile's own, as in the terminal. The form sends what it
            # shows, and an empty level would clear the profile's saved one, so report the one that runs.
            effort = loadout_sendable_effort(pm, loadout) or dataclasses.replace(
                profile, model=loadout.model
            ).get_effort()
        return {
            "name": loadout.name,
            "profile": loadout.provider_profile,
            "provider": profile.get_provider(),
            "model": acp_loadout_model(loadout, profile) if harness else loadout.model,
            "effort": effort,
        }
    except Exception as exc:  # noqa: BLE001 - a broken config must not hide the profiles
        logger.warning("default loadout: could not read it: %s", type(exc).__name__)
        return None


# Annotated, not ``= Query(...)``: a direct call (the unit tests make them) then gets the real default "".
_Workspace = Annotated[str, Query(description="Folder whose default loadout applies; empty is the engine's own")]


@router.get("")
async def list_profiles(workspace: _Workspace = ""):
    """List all available profiles."""
    pm = _folder_profile_manager(workspace)
    workspace = folder_or_400(workspace.strip())
    profiles = pm.list_profiles()

    return {
        "profiles": [_redacted_profile_dict(p) for p in profiles],
        "active": pm.active_profile_name,
        "count": len(profiles),
        "providers": [provider.value for provider in ProviderType],
        "invalid_profiles": getattr(pm, "get_invalid_profiles", lambda: {})(),
        "default_loadout": _default_loadout(pm, workspace),
    }


_CATALOG_WAIT_SECONDS = 1.5  # a cold start waits this long, once, for the first catalog refresh


class _CatalogRefresh:
    """The one background refresh of the providers' model catalogs.

    One runs at a time; every request just makes sure one is running. A request
    waits for it only until the first one has finished since the engine started
    (a cold cache would list each profile's own model only); after that every
    answer comes from the cache at once and the refresh runs behind it.
    """

    def __init__(self) -> None:
        self.task: Optional["asyncio.Task[None]"] = None
        self.settled = False  # a refresh has finished since the engine started

    def ensure(self, manager: Any) -> "asyncio.Task[None]":
        loop = asyncio.get_running_loop()
        task = self.task
        if task is not None and not task.done() and task.get_loop() is loop:
            return task

        async def refresh() -> None:
            try:  # a cold or expired catalog lists only each profile's own model
                await asyncio.wait_for(manager.refresh_catalogs(), 8.0)
            except Exception as exc:  # noqa: BLE001 - cached and bundled models still list
                logger.warning("model picker: catalog refresh failed: %s", type(exc).__name__)
            finally:
                self.settled = True

        self.task = loop.create_task(refresh(), name="model-picker-catalogs")
        return self.task


_catalogs = _CatalogRefresh()


def _picker_manager(workspace: str = ""):
    """The picker's LoadoutManager over ``workspace``'s own profiles (empty is the engine's folder)."""
    from kollabor_ai.loadout_manager import LoadoutManager

    # no config handle: the picker lists no saved loadouts, so no layered config read happens here. Profile
    # definitions still come from the config files through ProfileManager, as GET /profiles reads them (#966);
    # a named workspace gets that folder's own manager (#972)
    return LoadoutManager(_folder_profile_manager(workspace))


async def warm_model_choices() -> None:
    """Learn the picker's choices before any page asks (engine start, #953)."""
    from kollabor.llm import acp_discovery

    if not acp_discovery.warming_enabled():
        return
    manager = _picker_manager()
    profiles = manager.provider_profiles()
    _catalogs.ensure(manager)
    acp_discovery.warm_in_background(profiles)


async def _discover_on_request(manager: Any, profiles: list) -> dict:
    """The pre-#953 route: wait for every catalog and every ACP harness probe.
    Only with KOLLAB_PICKER_WARM=0."""
    from kollabor.llm.acp_discovery import discover_profiles

    async def refresh_catalogs() -> None:
        try:  # a cold or expired catalog lists only each profile's own model
            await asyncio.wait_for(manager.refresh_catalogs(), 8.0)
        except Exception as exc:  # noqa: BLE001 - cached and bundled models still list
            logger.warning("model picker: catalog refresh failed: %s", type(exc).__name__)

    catalog_task = asyncio.create_task(refresh_catalogs())
    discovery_task = asyncio.create_task(discover_profiles(profiles))
    try:
        _catalog_result, discovered = await asyncio.gather(catalog_task, discovery_task)
    except asyncio.CancelledError:
        catalog_task.cancel()
        discovery_task.cancel()
        await asyncio.gather(catalog_task, discovery_task, return_exceptions=True)
        raise
    return discovered


@router.get("/choices")
async def list_choices(workspace: _Workspace = "", refresh: bool = False):
    """Every provider profile x model x effort to start or switch a session
    on, for the web UI's searchable model picker, for ``workspace``'s own
    profiles (empty is the engine's folder; saved loadouts are not rows, so
    none is read).

    ACP choices come from the engine's background warm cache
    (``kollabor.llm.acp_discovery``): the answer is immediate and never waits
    for a harness. A harness not learned yet reads as "loading" (``more``),
    and the page asks again. ``refresh`` (the picker's Retry) probes again the
    profiles whose list is incomplete."""
    from kollabor.llm import acp_discovery

    manager = _picker_manager(workspace)
    profiles = manager.provider_profiles()

    if acp_discovery.warming_enabled():
        refreshing = _catalogs.ensure(manager)
        scope = "" if not workspace.strip() else str(folder_or_400(workspace.strip()))
        try:
            if scope:
                acp_discovery.warm_in_background(profiles, force=refresh, scope=scope)
            else:
                acp_discovery.warm_in_background(profiles, force=refresh)
        except Exception:  # noqa: BLE001 - the answer below still comes from what is known
            logger.warning("model picker: could not start the background warm", exc_info=True)
        native = [p for p in profiles if str(getattr(p, "provider", "")).lower() != "acp"]
        if native and not _catalogs.settled:
            await asyncio.wait({refreshing}, timeout=_CATALOG_WAIT_SECONDS)
        discovered = (
            acp_discovery.known_outcomes(profiles, scope=scope)
            if scope
            else acp_discovery.known_outcomes(profiles)
        )
    else:
        discovered = await _discover_on_request(manager, profiles)

    acp_options = {name: outcome.options for name, outcome in discovered.items()}
    discovery = {
        name: {
            "status": outcome.status,
            "more": outcome.more,
            **({"message": outcome.message} if outcome.message else {}),
        }
        for name, outcome in discovered.items()
    }
    return {"choices": manager.list_choices(acp_options), "discovery": discovery}


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
        reason = effort_unsupported_reason(
            resolve_effort_provider(provider, body.api_key or "", body.base_url or ""),
            body.effort,
        )
        if reason:
            raise HTTPException(status_code=422, detail=reason)

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

    # A level the provider never sends is refused here, not stored and reported.
    # Switching to Anthropic checks the level the profile already holds too.
    reason = effort_unsupported_reason(
        resolve_effort_provider(
            target_provider,
            body.api_key if body.api_key is not None else profile.get_api_key(),
            body.base_url if body.base_url is not None else profile.get_endpoint(),
        ),
        body.effort if body.effort is not None else profile.effort,
    )
    if reason:
        raise HTTPException(status_code=422, detail=reason)

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
    try:
        ok = pm.update_profile(name, **kwargs)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
