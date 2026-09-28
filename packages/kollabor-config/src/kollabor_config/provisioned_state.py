"""Private durable state installed by an authorized device enrollment.

The state file is deliberately separate from user-edited config and provider
OAuth files.  A provisioning install publishes one complete record with an
atomic same-directory replace, so a crash cannot expose only part of a bundle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

from .config_utils import get_config_directory

try:  # pragma: no cover - Windows uses msvcrt below
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - only imported on Windows
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

STATE_VERSION = 1
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_INSTALLS = 1024
MAX_NETWORKS = 64
MAX_SKILLS = 32
MAX_SECRET_BYTES = 8192

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PROFILE_FIELDS = frozenset(
    {
        "name",
        "provider",
        "model",
        "auth_type",
        "base_url",
        "temperature",
        "max_tokens",
        "context_window",
        "top_p",
        "effort",
        "organization",
        "api_version",
        "azure_endpoint",
        "deployment_id",
        "http_referer",
        "x_title",
        "project_id",
        "location",
        "store_responses",
    }
)
_PROFILE_OVERRIDE_FIELDS = frozenset(
    {
        "model",
        "temperature",
        "max_tokens",
        "timeout",
        "description",
        "extra_headers",
        "base_url",
        "top_p",
        "effort",
        "streaming",
        "supports_tools",
        "context_window",
        "organization",
        "api_version",
        "azure_endpoint",
        "deployment_id",
        "http_referer",
        "x_title",
        "project_id",
        "location",
        "store_responses",
    }
)
_PROVIDERS = frozenset(
    {
        "openai",
        "anthropic",
        "azure_openai",
        "custom",
        "openrouter",
        "openai_responses",
        "gemini",
    }
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_VALID_API_CATEGORIES = frozenset(f"provider:{name}:api_key" for name in _PROVIDERS)
_UNSET = object()


class ProvisionedStateError(ValueError):
    """Private provisioned state is unavailable, unsafe, or malformed."""


def default_provisioned_state_path() -> Path:
    """Return the user-owned private state path used by runtime consumers."""
    return get_config_directory() / "private" / "provisioned-state.json"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvisionedStateError("provisioned state has duplicate fields")
        result[key] = value
    return result


def _valid_text(value: Any, maximum: int, pattern: re.Pattern[str] | None = None) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not any(
            ord(char) < 0x20
            or ord(char) == 0x7F
            or 0xD800 <= ord(char) <= 0xDFFF
            for char in value
        )
        and (pattern is None or pattern.fullmatch(value) is not None)
    )


def _validate_profile(profile: Any) -> None:
    if not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS:
        raise ProvisionedStateError("provisioned profile has an invalid shape")
    if (
        not _valid_text(profile["name"], 64)
        or not isinstance(profile["provider"], str)
        or profile["provider"] not in _PROVIDERS
        or not _valid_text(profile["model"], 128)
        or not isinstance(profile["auth_type"], str)
        or profile["auth_type"] not in {"api_key", "oauth"}
    ):
        raise ProvisionedStateError("provisioned profile has invalid identity fields")
    text_limits = {
        "base_url": 2048,
        "effort": 32,
        "organization": 256,
        "api_version": 128,
        "azure_endpoint": 2048,
        "deployment_id": 128,
        "http_referer": 2048,
        "x_title": 256,
        "project_id": 256,
        "location": 128,
    }
    for key, maximum in text_limits.items():
        value = profile[key]
        if value is not None and not _valid_text(value, maximum):
            raise ProvisionedStateError("provisioned profile has invalid text fields")
    temperature = profile["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise ProvisionedStateError("provisioned profile temperature is invalid")
    for key in ("max_tokens", "context_window"):
        value = profile[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 2_000_000
        ):
            raise ProvisionedStateError("provisioned profile token limits are invalid")
    top_p = profile["top_p"]
    if top_p is not None and (
        isinstance(top_p, bool)
        or not isinstance(top_p, (int, float))
        or not math.isfinite(top_p)
        or not 0 <= top_p <= 1
    ):
        raise ProvisionedStateError("provisioned profile top_p is invalid")
    if profile["store_responses"] is not None and not isinstance(
        profile["store_responses"], bool
    ):
        raise ProvisionedStateError("provisioned profile has invalid boolean fields")


def _validate_record(enrollment_id: Any, record: Any) -> None:
    if not _valid_text(enrollment_id, 256, _IDENTIFIER):
        raise ProvisionedStateError("provisioned enrollment ID is invalid")
    expected_fields = {
        "revision",
        "digest",
        "profile",
        "networks",
        "settings",
        "credentials",
        "oauth_tokens_override",
        "oauth_tokens_disabled",
        "profile_overrides",
        "profile_disabled",
        "api_key_override",
        "api_key_disabled",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise ProvisionedStateError("provisioned install record has an invalid shape")
    revision = record["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ProvisionedStateError("provisioned revision is invalid")
    if not _valid_text(record["digest"], 64, _DIGEST):
        raise ProvisionedStateError("provisioned digest is invalid")

    profile = record["profile"]
    if profile is not None:
        _validate_profile(profile)
    profile_overrides = record["profile_overrides"]
    if not isinstance(profile_overrides, dict) or not set(
        profile_overrides
    ) <= _PROFILE_OVERRIDE_FIELDS:
        raise ProvisionedStateError("provisioned profile overrides are invalid")
    _validate_profile_overrides(profile_overrides)
    if profile is None and (profile_overrides or record["profile_disabled"]):
        raise ProvisionedStateError("provisioned profile overrides have no profile")
    if not isinstance(record["profile_disabled"], bool):
        raise ProvisionedStateError("provisioned profile state is invalid")

    networks = record["networks"]
    if not isinstance(networks, list) or len(networks) > MAX_NETWORKS:
        raise ProvisionedStateError("provisioned networks are invalid")
    network_ids: set[str] = set()
    for network in networks:
        if not isinstance(network, dict) or set(network) != {
            "network_id",
            "discovery_domain",
        }:
            raise ProvisionedStateError("provisioned network has an invalid shape")
        network_id = network["network_id"]
        domain = network["discovery_domain"]
        if (
            not _valid_text(network_id, 256, _IDENTIFIER)
            or not _valid_text(
                domain,
                253,
                re.compile(
                    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
                ),
            )
            or network_id in network_ids
        ):
            raise ProvisionedStateError("provisioned network fields are invalid")
        network_ids.add(network_id)

    settings = record["settings"]
    if not isinstance(settings, dict) or set(settings) != {
        "default_agent",
        "active_skills",
    }:
        raise ProvisionedStateError("provisioned settings have an invalid shape")
    agent = settings["default_agent"]
    if agent is not None and not _valid_text(agent, 64, _SAFE_NAME):
        raise ProvisionedStateError("provisioned default agent is invalid")
    skills = settings["active_skills"]
    if (
        not isinstance(skills, list)
        or len(skills) > MAX_SKILLS
        or any(not _valid_text(skill, 64, _SAFE_NAME) for skill in skills)
        or len(set(skills)) != len(skills)
    ):
        raise ProvisionedStateError("provisioned active skills are invalid")
    if skills and agent is None:
        raise ProvisionedStateError("provisioned skills have no default agent")

    credentials = record["credentials"]
    if not isinstance(credentials, list) or len(credentials) > 1:
        raise ProvisionedStateError("provisioned credentials are invalid")
    if (profile is None) != (not credentials):
        raise ProvisionedStateError("provisioned profile credentials are incomplete")
    for credential in credentials:
        if not isinstance(credential, dict) or set(credential) != {
            "category",
            "profile_name",
            "secret",
        }:
            raise ProvisionedStateError("provisioned credential has an invalid shape")
        category = credential["category"]
        profile_name = credential["profile_name"]
        if (
            not isinstance(category, str)
            or category not in _VALID_API_CATEGORIES | {"provider:openai:oauth_tokens"}
            or not isinstance(profile_name, str)
            or not _valid_text(profile_name, 64)
        ):
            raise ProvisionedStateError("provisioned credential identity is invalid")
        secret = credential["secret"]
        if category == "provider:openai:oauth_tokens":
            if not isinstance(secret, dict) or not {
                "access_token",
                "refresh_token",
                "expires_at",
            } <= set(secret) or set(secret) - {
                "access_token",
                "refresh_token",
                "expires_at",
                "account_id",
            }:
                raise ProvisionedStateError("provisioned OAuth token set is invalid")
            for token_key in ("access_token", "refresh_token"):
                token = secret[token_key]
                if not _valid_text(token, MAX_SECRET_BYTES):
                    raise ProvisionedStateError("provisioned OAuth token is invalid")
            expiry = secret["expires_at"]
            if (
                isinstance(expiry, bool)
                or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry)
                or expiry <= 0
            ):
                raise ProvisionedStateError("provisioned OAuth expiry is invalid")
            account_id = secret.get("account_id")
            if account_id is not None and not _valid_text(account_id, 256):
                raise ProvisionedStateError("provisioned OAuth account ID is invalid")
        elif not _valid_text(secret, MAX_SECRET_BYTES):
            raise ProvisionedStateError("provisioned API key is invalid")
        if profile is None or profile_name != profile["name"]:
            raise ProvisionedStateError("provisioned credential profile is mismatched")
        if category == "provider:openai:oauth_tokens":
            if profile["provider"] != "openai_responses" or profile["auth_type"] != "oauth":
                raise ProvisionedStateError("provisioned OAuth profile is mismatched")
        elif (
            category != f"provider:{profile['provider']}:api_key"
            or profile["auth_type"] != "api_key"
        ):
            raise ProvisionedStateError("provisioned API key profile is mismatched")

    override = record["oauth_tokens_override"]
    if override is not None:
        _validate_oauth_tokens(override)
        if (
            profile is None
            or profile["provider"] != "openai_responses"
            or profile["auth_type"] != "oauth"
        ):
            raise ProvisionedStateError("provisioned OAuth override has no OAuth profile")
    if not isinstance(record["oauth_tokens_disabled"], bool):
        raise ProvisionedStateError("provisioned OAuth state is invalid")
    if record["oauth_tokens_disabled"] and override is not None:
        raise ProvisionedStateError("disabled provisioned OAuth tokens have an override")

    api_key_override = record["api_key_override"]
    if api_key_override is not None and not _valid_text(
        api_key_override, MAX_SECRET_BYTES
    ):
        raise ProvisionedStateError("provisioned API key override is invalid")
    if not isinstance(record["api_key_disabled"], bool):
        raise ProvisionedStateError("provisioned API key state is invalid")
    if record["api_key_disabled"] and api_key_override is not None:
        raise ProvisionedStateError("disabled provisioned API key has an override")
    if api_key_override is not None or record["api_key_disabled"]:
        if profile is None or profile["auth_type"] != "api_key":
            raise ProvisionedStateError("provisioned API key override has no API profile")


def _validate_profile_overrides(overrides: dict[str, Any]) -> None:
    text_fields = {
        "model": 128,
        "description": 1024,
        "base_url": 2048,
        "effort": 32,
        "organization": 256,
        "api_version": 128,
        "azure_endpoint": 2048,
        "deployment_id": 128,
        "http_referer": 2048,
        "x_title": 256,
        "project_id": 256,
        "location": 128,
    }
    for key, maximum in text_fields.items():
        if key not in overrides:
            continue
        value = overrides[key]
        if (value is None or value == "") and key != "model":
            continue
        if not _valid_text(value, maximum):
            raise ProvisionedStateError("provisioned profile text override is invalid")
    for key, minimum, maximum in (
        ("temperature", 0, 2),
        ("top_p", 0, 1),
    ):
        value = overrides.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise ProvisionedStateError("provisioned profile numeric override is invalid")
    for key in ("max_tokens", "context_window"):
        value = overrides.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 2_000_000
        ):
            raise ProvisionedStateError("provisioned profile token override is invalid")
    timeout = overrides.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 0 <= timeout <= 86_400
    ):
        raise ProvisionedStateError("provisioned profile timeout override is invalid")
    for key in ("streaming", "supports_tools", "store_responses"):
        value = overrides.get(key)
        if value is not None and not isinstance(value, bool):
            raise ProvisionedStateError("provisioned profile boolean override is invalid")
    headers = overrides.get("extra_headers")
    if headers is not None:
        if not isinstance(headers, dict) or len(headers) > 32:
            raise ProvisionedStateError("provisioned profile headers are invalid")
        for key, value in headers.items():
            if not _valid_text(key, 128) or not _valid_text(value, 2048):
                raise ProvisionedStateError("provisioned profile headers are invalid")


def _validate_oauth_tokens(tokens: Any) -> None:
    if not isinstance(tokens, dict) or not {
        "access_token",
        "refresh_token",
        "expires_at",
    } <= set(tokens) or set(tokens) - {
        "access_token",
        "refresh_token",
        "expires_at",
        "account_id",
    }:
        raise ProvisionedStateError("provisioned OAuth token set is invalid")
    for token_key in ("access_token", "refresh_token"):
        if not _valid_text(tokens[token_key], MAX_SECRET_BYTES):
            raise ProvisionedStateError("provisioned OAuth token is invalid")
    expiry = tokens["expires_at"]
    if (
        isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not math.isfinite(expiry)
        or expiry <= 0
    ):
        raise ProvisionedStateError("provisioned OAuth expiry is invalid")
    account_id = tokens.get("account_id")
    if account_id is not None and not _valid_text(account_id, 256):
        raise ProvisionedStateError("provisioned OAuth account ID is invalid")


def _validate_state(state: Any) -> None:
    if not isinstance(state, dict) or set(state) != {"version", "installs"}:
        raise ProvisionedStateError("provisioned state has an invalid shape")
    if type(state["version"]) is not int or state["version"] != STATE_VERSION:
        raise ProvisionedStateError("provisioned state version is invalid")
    installs = state["installs"]
    if not isinstance(installs, dict) or len(installs) > MAX_INSTALLS:
        raise ProvisionedStateError("provisioned install count is invalid")
    for enrollment_id, record in installs.items():
        if isinstance(record, dict):
            record.setdefault("profile_overrides", {})
            record.setdefault("profile_disabled", False)
            record.setdefault("api_key_override", None)
            record.setdefault("api_key_disabled", False)
        _validate_record(enrollment_id, record)
        content = {
            key: record[key]
            for key in ("profile", "networks", "settings", "credentials")
        }
        digest = hashlib.sha256(
            json.dumps(
                content,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        if digest != record["digest"]:
            raise ProvisionedStateError("provisioned install digest does not match")


def _check_private_file(info: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProvisionedStateError(f"{label} must be a single-link regular file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ProvisionedStateError(f"{label} must be owned by the current user")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
        raise ProvisionedStateError(f"{label} must have mode 0600")


class ProvisionedStateFile:
    """Read and atomically edit bounded private enrollment state."""

    _registry_guard = threading.Lock()
    _thread_locks: dict[str, threading.RLock] = {}

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        requested = default_provisioned_state_path() if path is None else Path(path)
        requested = requested.expanduser()
        if requested.name in {"", ".", ".."}:
            raise ValueError("state path must name a file")
        # Keep the lexical parent so _ensure_private_parent can reject a
        # symlink instead of silently resolving it and chmodding its target.
        self.path = Path(os.path.abspath(requested.parent)) / requested.name
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        lock_key = str(self.lock_path)
        with self._registry_guard:
            self._thread_lock = self._thread_locks.setdefault(lock_key, threading.RLock())

    def read(self) -> dict[str, Any]:
        """Read a validated detached snapshot; a missing file is empty state."""
        self._ensure_private_parent()
        state = self._read_unlocked()
        return deepcopy(state)

    def get_profile_oauth_tokens(self, profile_name: str) -> dict[str, Any] | None:
        """Return scoped OAuth tokens without falling back to global login."""
        state = self.read()
        match: dict[str, Any] | None = None
        for record in state["installs"].values():
            profile = record["profile"]
            if (
                profile is None
                or record["profile_disabled"]
                or profile["name"] != profile_name
            ):
                continue
            if match is not None:
                raise ProvisionedStateError("provisioned OAuth profile is ambiguous")
            match = record
        if match is None or match["oauth_tokens_disabled"]:
            return None
        override = match["oauth_tokens_override"]
        if override is not None:
            return deepcopy(override)
        for credential in match["credentials"]:
            if credential["category"] == "provider:openai:oauth_tokens":
                return deepcopy(credential["secret"])
        return None

    def get_network_preferences(self) -> dict[str, str]:
        """Return the installed network ID to discovery-domain mapping."""
        preferences: dict[str, str] = {}
        state = self.read()
        for enrollment_id in sorted(state["installs"]):
            for network in state["installs"][enrollment_id]["networks"]:
                network_id = network["network_id"]
                domain = network["discovery_domain"]
                previous = preferences.get(network_id)
                if previous is not None and previous != domain:
                    raise ProvisionedStateError(
                        "provisioned network preferences conflict"
                    )
                preferences[network_id] = domain
        return preferences

    def update_profile_oauth_tokens(
        self,
        profile_name: str,
        tokens: dict[str, Any] | None,
        *,
        disabled: bool = False,
    ) -> bool:
        """Store refresh results beside their signed install record."""
        if tokens is not None:
            _validate_oauth_tokens(tokens)
        if not isinstance(disabled, bool) or (disabled and tokens is not None):
            raise ProvisionedStateError("provisioned OAuth update is invalid")
        with self.edit() as state:
            matches = [
                record
                for record in state["installs"].values()
                if record["profile"] is not None
                and not record["profile_disabled"]
                and record["profile"]["name"] == profile_name
                and record["profile"]["auth_type"] == "oauth"
            ]
            if len(matches) != 1:
                return False
            matches[0]["oauth_tokens_override"] = deepcopy(tokens)
            matches[0]["oauth_tokens_disabled"] = disabled
        return True

    def update_profile_preferences(
        self,
        profile_name: str,
        preferences: dict[str, Any],
        *,
        api_key_update: str | object = _UNSET,
    ) -> bool:
        """Persist user edits beside, rather than over, the signed install."""
        if (
            not _valid_text(profile_name, 64)
            or not isinstance(preferences, dict)
            or not set(preferences) <= _PROFILE_OVERRIDE_FIELDS
        ):
            raise ProvisionedStateError("provisioned profile update is invalid")
        _validate_profile_overrides(preferences)
        if api_key_update is not _UNSET and (
            not isinstance(api_key_update, str)
            or (api_key_update and not _valid_text(api_key_update, MAX_SECRET_BYTES))
        ):
            raise ProvisionedStateError("provisioned API key update is invalid")
        with self.edit() as state:
            matches = [
                record
                for record in state["installs"].values()
                if record["profile"] is not None
                and not record["profile_disabled"]
                and record["profile"]["name"] == profile_name
            ]
            if len(matches) != 1:
                return False
            record = matches[0]
            record["profile_overrides"] = deepcopy(preferences)
            if api_key_update is not _UNSET:
                if record["profile"]["auth_type"] != "api_key":
                    raise ProvisionedStateError(
                        "provisioned profile does not accept an API key update"
                    )
                if api_key_update:
                    record["api_key_override"] = api_key_update
                    record["api_key_disabled"] = False
                else:
                    record["api_key_override"] = None
                    record["api_key_disabled"] = True
        return True

    def disable_profile(self, profile_name: str) -> bool:
        """Hide a provisioned profile without deleting its enrollment record."""
        if not _valid_text(profile_name, 64):
            raise ProvisionedStateError("provisioned profile name is invalid")
        with self.edit() as state:
            matches = [
                record
                for record in state["installs"].values()
                if record["profile"] is not None
                and not record["profile_disabled"]
                and record["profile"]["name"] == profile_name
            ]
            if len(matches) != 1:
                return False
            record = matches[0]
            record["profile_disabled"] = True
            record["profile_overrides"] = {}
            record["api_key_override"] = None
            record["api_key_disabled"] = (
                record["profile"]["auth_type"] == "api_key"
            )
            record["oauth_tokens_override"] = None
            record["oauth_tokens_disabled"] = True
        return True

    @contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        """Lock the file, yield a private copy, and publish only on success."""
        with self._locked_file():
            state = self._read_unlocked()
            editable = deepcopy(state)
            yield editable
            _validate_state(editable)
            self._write_unlocked(editable)

    @contextmanager
    def _locked_file(self) -> Iterator[None]:
        self._thread_lock.acquire()
        fd: int | None = None
        locked = False
        try:
            self._ensure_private_parent()
            flags = (
                os.O_CREAT
                | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            fd = os.open(self.lock_path, flags, 0o600)
            _check_private_file(os.fstat(fd), "provisioned state lock")
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            elif msvcrt is not None:  # pragma: no cover
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:  # pragma: no cover
                raise ProvisionedStateError("no supported file-lock backend")
            yield
        except ProvisionedStateError:
            raise
        except OSError as exc:
            raise ProvisionedStateError("cannot lock provisioned state") from exc
        finally:
            try:
                if fd is not None:
                    try:
                        if locked and fcntl is not None:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        elif locked and msvcrt is not None:  # pragma: no cover
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    finally:
                        os.close(fd)
            finally:
                self._thread_lock.release()

    def _ensure_private_parent(self) -> None:
        try:
            parent_info = self.path.parent.lstat()
        except FileNotFoundError:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                parent_info = self.path.parent.lstat()
            except OSError as exc:
                raise ProvisionedStateError(
                    "cannot create provisioned state parent"
                ) from exc
        except OSError as exc:
            raise ProvisionedStateError("cannot inspect provisioned state parent") from exc
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise ProvisionedStateError("provisioned state parent must be a directory")
        info = parent_info
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ProvisionedStateError("provisioned state parent must be user-owned")
        if os.name == "posix":
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise ProvisionedStateError("provisioned state parent must have mode 0700")

    def _read_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return {"version": STATE_VERSION, "installs": {}}
        except OSError as exc:
            raise ProvisionedStateError("cannot open provisioned state") from exc
        try:
            info = os.fstat(fd)
            _check_private_file(info, "provisioned state")
            if info.st_size > MAX_STATE_BYTES:
                raise ProvisionedStateError("provisioned state exceeds its read byte limit")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise ProvisionedStateError("provisioned state exceeds its read byte limit")
        finally:
            os.close(fd)
        try:
            state = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ProvisionedStateError("provisioned state contains a non-finite value")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvisionedStateError("provisioned state JSON is malformed") from exc
        _validate_state(state)
        return state

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        _validate_state(state)
        serialized = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(serialized) > MAX_STATE_BYTES:
            raise ProvisionedStateError("provisioned state exceeds its write byte limit")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise


__all__ = [
    "ProvisionedStateError",
    "ProvisionedStateFile",
    "default_provisioned_state_path",
]
