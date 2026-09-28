"""Filesystem transaction adapter for device provisioning bundles."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import fields
from pathlib import Path
from typing import Any

from kollabor_config.config_utils import (
    get_global_config_path_candidates,
    get_local_config_path_candidates,
)
from kollabor_config.provisioned_state import ProvisionedStateFile

from .provisioning import (
    InstalledRevision,
    OpenAIOAuthCredential,
    ProfilePreferences,
    ProvisioningError,
    ProvisioningPayload,
    ProvisioningTransaction,
)


class FilesystemProvisioningStore:
    """Atomically persist complete installs in Kollab's private state file.

    Existing config profiles and global provider OAuth tokens are never
    rewritten.  Runtime managers read this store as an overlay after commit.
    """

    def __init__(self, state_file: ProvisionedStateFile | None = None) -> None:
        self._state_file = state_file or ProvisionedStateFile()

    def begin(self) -> ProvisioningTransaction:
        editor = self._state_file.edit()
        state = editor.__enter__()
        return _FilesystemProvisioningTransaction(editor, state)


class _FilesystemProvisioningTransaction:
    def __init__(
        self,
        editor: AbstractContextManager[dict[str, Any]],
        state: dict[str, Any],
    ) -> None:
        self._editor = editor
        self._state = state
        self._enrollment_id: str | None = None
        self._staged_payload: dict[str, Any] | None = None
        self._staged_revision: tuple[int, str] | None = None
        self._closed = False

    def installed_revision(self, enrollment_id: str) -> InstalledRevision | None:
        self._require_open()
        self._enrollment_id = enrollment_id
        record = self._state["installs"].get(enrollment_id)
        if record is None:
            return None
        return InstalledRevision(record["revision"], record["digest"])

    def ensure_targets_available(self, payload: ProvisioningPayload) -> None:
        self._require_open()
        if self._enrollment_id is None:
            raise ProvisioningError("storage_failure")
        profile_name = payload.profile.name if payload.profile is not None else None
        if profile_name is not None and _configured_profile_exists(profile_name):
            raise ProvisioningError("storage_conflict")

        network_domains: dict[str, str] = {}
        defaults: set[str] = set()
        skills: set[tuple[str, ...]] = set()
        for owner, record in self._state["installs"].items():
            if owner == self._enrollment_id:
                continue
            existing_profile = record["profile"]
            if (
                profile_name is not None
                and existing_profile is not None
                and existing_profile["name"] == profile_name
            ):
                raise ProvisioningError("storage_conflict")
            for network in record["networks"]:
                network_id = network["network_id"]
                prior_domain = network_domains.get(network_id)
                if prior_domain is not None and prior_domain != network["discovery_domain"]:
                    raise ProvisioningError("storage_conflict")
                network_domains[network_id] = network["discovery_domain"]
            settings = record["settings"]
            if settings["default_agent"] is not None:
                defaults.add(settings["default_agent"])
            if settings["active_skills"]:
                skills.add(tuple(settings["active_skills"]))

        for network in payload.networks:
            prior_domain = network_domains.get(network.network_id)
            if prior_domain is not None and prior_domain != network.discovery_domain:
                raise ProvisioningError("storage_conflict")
        if payload.settings.default_agent is not None and defaults - {
            payload.settings.default_agent
        }:
            raise ProvisioningError("storage_conflict")
        if payload.settings.active_skills and skills - {payload.settings.active_skills}:
            raise ProvisioningError("storage_conflict")

    def stage_payload(self, payload: ProvisioningPayload) -> None:
        self._require_open()
        if self._enrollment_id is None:
            raise ProvisioningError("storage_failure")
        self._staged_payload = _payload_to_record(payload)

    def stage_revision(self, enrollment_id: str, revision: int, digest: str) -> None:
        self._require_open()
        if enrollment_id != self._enrollment_id:
            raise ProvisioningError("storage_failure")
        self._staged_revision = (revision, digest)

    def commit(self) -> None:
        self._require_open()
        if (
            self._enrollment_id is None
            or self._staged_payload is None
            or self._staged_revision is None
        ):
            raise ProvisioningError("storage_failure")
        revision, digest = self._staged_revision
        self._state["installs"][self._enrollment_id] = {
            **self._staged_payload,
            "revision": revision,
            "digest": digest,
            "profile_overrides": {},
            "profile_disabled": False,
            "api_key_override": None,
            "api_key_disabled": False,
            "oauth_tokens_override": None,
            "oauth_tokens_disabled": False,
        }
        self._closed = True
        self._editor.__exit__(None, None, None)

    def rollback(self) -> None:
        if self._closed:
            return
        self._closed = True
        error = RuntimeError("provisioning transaction rolled back")
        self._editor.__exit__(RuntimeError, error, error.__traceback__)

    def _require_open(self) -> None:
        if self._closed:
            raise ProvisioningError("storage_failure")


def _configured_profile_exists(name: str) -> bool:
    """Fail closed on profile-name collisions in merged global/local config."""
    from kollabor_ai.profile_manager import ProfileManager

    reserved = set(ProfileManager.DEFAULT_PROFILES) | {
        item["profile_name"] for item in ProfileManager.PROVIDER_ENV_MAP
    }
    reserved.add("openai-oauth")
    if name in reserved:
        return True

    seen: set[Path] = set()
    for path in (*get_global_config_path_candidates(), *get_local_config_path_candidates()):
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ProvisioningError("storage_failure") from None
        if not isinstance(data, dict):
            raise ProvisioningError("storage_failure")
        kollabor = data.get("kollabor", {})
        if not isinstance(kollabor, dict):
            raise ProvisioningError("storage_failure")
        llm = kollabor.get("llm", {})
        if not isinstance(llm, dict):
            raise ProvisioningError("storage_failure")
        profiles = llm.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ProvisioningError("storage_failure")
        if name in profiles:
            return True
    return False


def _payload_to_record(payload: ProvisioningPayload) -> dict[str, Any]:
    profile: dict[str, Any] | None = None
    if payload.profile is not None:
        profile = {
            item.name: getattr(payload.profile, item.name)
            for item in fields(ProfilePreferences)
        }

    credentials: list[dict[str, Any]] = []
    for credential in payload.credentials:
        secret: str | dict[str, Any]
        if isinstance(credential.secret, OpenAIOAuthCredential):
            secret = {
                "access_token": credential.secret.access_token,
                "refresh_token": credential.secret.refresh_token,
                "expires_at": credential.secret.expires_at,
            }
            if credential.secret.account_id:
                secret["account_id"] = credential.secret.account_id
        else:
            secret = credential.secret
        credentials.append(
            {
                "category": credential.category,
                "profile_name": credential.profile_name,
                "secret": secret,
            }
        )

    return {
        "profile": profile,
        "networks": [
            {
                "network_id": network.network_id,
                "discovery_domain": network.discovery_domain,
            }
            for network in payload.networks
        ],
        "settings": {
            "default_agent": payload.settings.default_agent,
            "active_skills": list(payload.settings.active_skills),
        },
        "credentials": credentials,
    }


__all__ = ["FilesystemProvisioningStore"]
