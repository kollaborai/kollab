"""Strict, device-bound provisioning bundles for delegated enrollment.

The module validates the signed bundle and coordinates an all-or-nothing local
store transaction.  It does not persist profiles or credentials itself: the
application supplies a store adapter that implements the transaction contract
below using Kollab's existing profile and credential stores.

Secret values are accepted only by the local seal/install helpers.  They are
never included in receipts or error text.  Python cannot promise cryptographic
erasure of immutable strings or JSON decoder buffers; mutable temporary buffers
are cleared on a best-effort basis.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from nacl.exceptions import CryptoError
from nacl.public import SealedBox
from nacl.signing import SigningKey, VerifyKey

from .dns.private_directory import public_key_id

_BUNDLE_DOMAIN = b"kollab-device-provisioning-bundle/1\0"
_BUNDLE_TYPE = "kollab.provisioning.bundle"
_BUNDLE_VERSION = 1
_MAX_BUNDLE_BYTES = 20 * 1024
# libsodium crypto_box_SEALBYTES (32-byte ephemeral public key + 16-byte MAC).
_SEALED_BOX_OVERHEAD = 48
_MAX_PLAINTEXT_BYTES = _MAX_BUNDLE_BYTES - _SEALED_BOX_OVERHEAD
_MAX_BUNDLE_TTL_SECONDS = 15 * 60
_MAX_CLOCK_SKEW_SECONDS = 30
# Keep the protocol count aligned with EnrollmentDelegationStore's 64-network cap;
# the ciphertext byte limit remains the tighter bound for unusually long ids.
_MAX_NETWORKS = 64
_MAX_CREDENTIALS = 1
_MAX_SECRET_BYTES = 8192
_MAX_COLLECTION_ITEMS = 32

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}\Z")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_API_KEY_PROVIDERS = frozenset(
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
# An OAuth login is never a credential a device receives: two devices sharing
# one refresh token sign each other out, so each runs its own /login
# (docs/specs/agent-network-simple-flow.md sections 3 and 9).
_ALLOWED_CREDENTIAL_CATEGORIES = frozenset(
    f"provider:{name}:api_key" for name in _API_KEY_PROVIDERS
)


class ProvisioningError(ValueError):
    """Fixed, secret-free provisioning failure code."""

    _CODES = frozenset(
        {
            "invalid_input",
            "invalid_bundle",
            "invalid_signature",
            "wrong_issuer",
            "wrong_recipient",
            "wrong_enrollment",
            "wrong_scope",
            "unauthorized_profile",
            "unauthorized_credential",
            "expired",
            "replayed",
            "storage_conflict",
            "storage_failure",
            "too_large",
        }
    )

    def __init__(self, code: str):
        self.code = code if code in self._CODES else "invalid_input"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ProfilePreferences:
    """Allowlisted profile preferences; excludes secrets and executable fields."""

    name: str
    provider: str
    model: str
    auth_type: str = "api_key"
    base_url: str | None = None
    temperature: float = 0.7
    max_tokens: int = 16384
    context_window: int = 200000
    top_p: float | None = None
    effort: str | None = None
    organization: str | None = None
    api_version: str | None = None
    azure_endpoint: str | None = None
    deployment_id: str | None = None
    http_referer: str | None = None
    x_title: str | None = None
    project_id: str | None = None
    location: str | None = None
    store_responses: bool | None = None


@dataclass(frozen=True, slots=True)
class NetworkPreferences:
    """One selected network's discovery domain; contains no key material."""

    network_id: str
    discovery_domain: str


@dataclass(frozen=True, slots=True)
class SafeAgentSettings:
    """References to already-installed agents and skills, never their code."""

    default_agent: str | None = None
    active_skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningCredential:
    """A single profile-bound credential; ``secret`` never appears in repr."""

    category: str
    profile_name: str
    secret: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ProvisioningCredential("
            f"category={self.category!r}, profile_name={self.profile_name!r}, "
            "secret=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class ProvisioningPayload:
    """Versioned content accepted by this boundary."""

    profile: ProfilePreferences | None
    networks: tuple[NetworkPreferences, ...]
    settings: SafeAgentSettings = SafeAgentSettings()
    credentials: tuple[ProvisioningCredential, ...] = ()


@dataclass(frozen=True, slots=True)
class ProvisioningScope:
    """Scope copied from the active, durable human delegation."""

    enrollment_id: str
    network_ids: tuple[str, ...]
    audience: str
    profile_name: str | None
    allowed_credential_categories: frozenset[str] = frozenset()
    allowed_agent_names: frozenset[str] = frozenset()
    allowed_skill_names: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningExpectation:
    """Recipient-side trusted issuer key and independently expected scope."""

    issuer_public_key: bytes = field(repr=False)
    scope: ProvisioningScope

    def __repr__(self) -> str:
        return f"ProvisioningExpectation(scope={self.scope!r}, issuer=<redacted>)"


@dataclass(frozen=True, slots=True)
class InstalledRevision:
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class InstallReceipt:
    """Secret-free local-install result; this is not a joined/complete ack."""

    status: str
    enrollment_id: str
    revision: int
    digest: str


class ProvisioningTransaction(Protocol):
    """Atomic private-store transaction required from the application adapter.

    The transaction lock must cover conflict checks through commit.  Staging
    calls must remain invisible until commit; commit must publish the profile,
    network settings, credential(s), and revision/digest record atomically.
    The adapter must use user-owned 0700 directories and 0600 files or Kollab's
    supported secure storage. Existing unrelated profiles/credentials must
    never be overwritten. On any exception, rollback must restore the
    pre-transaction state.
    """

    def installed_revision(self, enrollment_id: str) -> InstalledRevision | None:
        """Read the durable replay record while holding the transaction lock."""

    def ensure_targets_available(self, payload: ProvisioningPayload) -> None:
        """Reject collisions with any unrelated existing profile or credential."""

    def stage_payload(self, payload: ProvisioningPayload) -> None:
        """Stage allowlisted non-secret config and secret credentials privately."""

    def stage_revision(self, enrollment_id: str, revision: int, digest: str) -> None:
        """Stage the replay record in the same atomic transaction."""

    def commit(self) -> None:
        """Atomically publish every staged item or raise without partial writes."""

    def rollback(self) -> None:
        """Discard staged items or restore pre-commit state after a failed commit."""


class ProvisioningStore(Protocol):
    def begin(self) -> ProvisioningTransaction:
        """Start a serialized private-store transaction."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
    )


def _text(value: Any, maximum: int, *, pattern: re.Pattern[str] | None = None) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not any(
            ord(char) < 0x20 or ord(char) == 0x7F or 0xD800 <= ord(char) <= 0xDFFF
            for char in value
        )
        and (pattern is None or pattern.fullmatch(value) is not None)
    )


def _is_local_endpoint(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _validate_scope(scope: ProvisioningScope) -> tuple[str, ...]:
    if not isinstance(scope, ProvisioningScope):
        raise ProvisioningError("invalid_input")
    if not _text(scope.enrollment_id, 64, pattern=_IDENTIFIER):
        raise ProvisioningError("invalid_input")
    if not _text(scope.audience, 128, pattern=_IDENTIFIER):
        raise ProvisioningError("invalid_input")
    if (
        not isinstance(scope.network_ids, tuple)
        or not 1 <= len(scope.network_ids) <= _MAX_NETWORKS
        or any(not _text(item, 256, pattern=_IDENTIFIER) for item in scope.network_ids)
        or len(set(scope.network_ids)) != len(scope.network_ids)
    ):
        raise ProvisioningError("invalid_input")
    if scope.profile_name is not None and not _text(
        scope.profile_name, 64, pattern=_PROFILE_NAME
    ):
        raise ProvisioningError("invalid_input")
    if (
        not isinstance(scope.allowed_credential_categories, frozenset)
        or not scope.allowed_credential_categories <= _ALLOWED_CREDENTIAL_CATEGORIES
    ):
        raise ProvisioningError("invalid_input")
    for names in (scope.allowed_agent_names, scope.allowed_skill_names):
        if (
            not isinstance(names, frozenset)
            or len(names) > _MAX_COLLECTION_ITEMS
            or any(not _text(name, 64, pattern=_SAFE_NAME) for name in names)
        ):
            raise ProvisioningError("invalid_input")
    return tuple(sorted(scope.network_ids))


def _validate_profile(profile: ProfilePreferences) -> dict[str, Any]:
    if not isinstance(profile, ProfilePreferences):
        raise ProvisioningError("invalid_bundle")
    if (
        not _text(profile.name, 64, pattern=_PROFILE_NAME)
        or not isinstance(profile.provider, str)
        or profile.provider not in _API_KEY_PROVIDERS
        or not _text(profile.model, 128, pattern=_MODEL_NAME)
        or profile.auth_type != "api_key"
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.provider == "custom" and profile.base_url is None:
        raise ProvisioningError("invalid_bundle")
    if profile.base_url is not None:
        if not _text(profile.base_url, 2048):
            raise ProvisioningError("invalid_bundle")
        try:
            parsed = urlsplit(profile.base_url)
            # Accessing .port validates malformed or out-of-range port syntax.
            _ = parsed.port
        except ValueError:
            raise ProvisioningError("invalid_bundle") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or _is_local_endpoint(parsed.hostname)
        ):
            raise ProvisioningError("invalid_bundle")
    if (
        isinstance(profile.temperature, bool)
        or not isinstance(profile.temperature, (int, float))
        or not math.isfinite(profile.temperature)
        or not 0 <= profile.temperature <= 2
        or isinstance(profile.max_tokens, bool)
        or not isinstance(profile.max_tokens, int)
        or not 1 <= profile.max_tokens <= 2_000_000
        or isinstance(profile.context_window, bool)
        or not isinstance(profile.context_window, int)
        or not 1 <= profile.context_window <= 2_000_000
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.top_p is not None and (
        isinstance(profile.top_p, bool)
        or not isinstance(profile.top_p, (int, float))
        or not math.isfinite(profile.top_p)
        or not 0 <= profile.top_p <= 1
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.effort is not None and not _text(profile.effort, 32, pattern=_SAFE_NAME):
        raise ProvisioningError("invalid_bundle")
    if profile.organization is not None and (
        profile.provider != "openai"
        or not _text(profile.organization, 256, pattern=_IDENTIFIER)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.api_version is not None and (
        profile.provider not in {"anthropic", "azure_openai"}
        or not _text(profile.api_version, 64, pattern=_MODEL_NAME)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.azure_endpoint is not None:
        if profile.provider != "azure_openai" or not _text(
            profile.azure_endpoint, 2048
        ):
            raise ProvisioningError("invalid_bundle")
        try:
            endpoint = urlsplit(profile.azure_endpoint)
            _ = endpoint.port
        except ValueError:
            raise ProvisioningError("invalid_bundle") from None
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
            or _is_local_endpoint(endpoint.hostname)
        ):
            raise ProvisioningError("invalid_bundle")
    if profile.provider == "azure_openai" and profile.azure_endpoint is None:
        raise ProvisioningError("invalid_bundle")
    if profile.deployment_id is not None and (
        profile.provider != "azure_openai"
        or not _text(profile.deployment_id, 128, pattern=_MODEL_NAME)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.http_referer is not None:
        if profile.provider != "openrouter" or not _text(profile.http_referer, 2048):
            raise ProvisioningError("invalid_bundle")
        try:
            referer = urlsplit(profile.http_referer)
            _ = referer.port
        except ValueError:
            raise ProvisioningError("invalid_bundle") from None
        if (
            referer.scheme != "https"
            or not referer.hostname
            or referer.username is not None
            or referer.password is not None
            or referer.query
            or referer.fragment
        ):
            raise ProvisioningError("invalid_bundle")
    if profile.x_title is not None and (
        profile.provider != "openrouter" or not _text(profile.x_title, 256)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.project_id is not None and (
        profile.provider != "gemini"
        or not _text(profile.project_id, 256, pattern=_IDENTIFIER)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.location is not None and (
        profile.provider != "gemini"
        or not _text(profile.location, 128, pattern=_MODEL_NAME)
    ):
        raise ProvisioningError("invalid_bundle")
    if profile.store_responses is not None and (
        profile.provider != "openai_responses"
        or not isinstance(profile.store_responses, bool)
    ):
        raise ProvisioningError("invalid_bundle")
    return {
        "name": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "auth_type": profile.auth_type,
        "base_url": profile.base_url,
        "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
        "context_window": profile.context_window,
        "top_p": profile.top_p,
        "effort": profile.effort,
        "organization": profile.organization,
        "api_version": profile.api_version,
        "azure_endpoint": profile.azure_endpoint,
        "deployment_id": profile.deployment_id,
        "http_referer": profile.http_referer,
        "x_title": profile.x_title,
        "project_id": profile.project_id,
        "location": profile.location,
        "store_responses": profile.store_responses,
    }


def _validate_secret_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.encode("utf-8")) <= _MAX_SECRET_BYTES
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        and value == value.strip()
    )


def _credential_to_dict(
    credential: ProvisioningCredential, profile: ProfilePreferences
) -> dict[str, Any]:
    if (
        not isinstance(credential, ProvisioningCredential)
        or credential.profile_name != profile.name
        or credential.category != f"provider:{profile.provider}:api_key"
        or profile.auth_type != "api_key"
        or not _validate_secret_text(credential.secret)
    ):
        raise ProvisioningError("unauthorized_credential")
    return {
        "category": credential.category,
        "profile_name": credential.profile_name,
        "secret": credential.secret,
    }


def _validate_payload(
    payload: ProvisioningPayload, scope: ProvisioningScope
) -> dict[str, Any]:
    expected_networks = _validate_scope(scope)
    if not isinstance(payload, ProvisioningPayload):
        raise ProvisioningError("invalid_bundle")
    if (
        not isinstance(payload.networks, tuple)
        or len(payload.networks) != len(expected_networks)
        or any(
            not isinstance(network, NetworkPreferences) for network in payload.networks
        )
    ):
        raise ProvisioningError("wrong_scope")
    networks: list[dict[str, str]] = []
    network_ids: list[str] = []
    for network in payload.networks:
        if (
            not _text(network.network_id, 256, pattern=_IDENTIFIER)
            or not _text(network.discovery_domain, 253, pattern=_DOMAIN)
            or network.discovery_domain.lower() != network.discovery_domain
        ):
            raise ProvisioningError("invalid_bundle")
        network_ids.append(network.network_id)
        networks.append(
            {
                "network_id": network.network_id,
                "discovery_domain": network.discovery_domain,
            }
        )
    if tuple(sorted(network_ids)) != expected_networks or len(set(network_ids)) != len(
        network_ids
    ):
        raise ProvisioningError("wrong_scope")
    networks.sort(key=lambda network: network["network_id"])

    profile_data: dict[str, Any] | None = None
    if payload.profile is not None:
        profile_data = _validate_profile(payload.profile)
        if scope.profile_name != payload.profile.name:
            raise ProvisioningError("unauthorized_profile")
    elif scope.profile_name is not None:
        raise ProvisioningError("unauthorized_profile")

    if not isinstance(payload.settings, SafeAgentSettings):
        raise ProvisioningError("invalid_bundle")
    default_agent = payload.settings.default_agent
    if default_agent is not None and (
        not _text(default_agent, 64, pattern=_SAFE_NAME)
        or default_agent not in scope.allowed_agent_names
    ):
        raise ProvisioningError("wrong_scope")
    skills = payload.settings.active_skills
    if (
        not isinstance(skills, tuple)
        or len(skills) > _MAX_COLLECTION_ITEMS
        or any(
            not _text(skill, 64, pattern=_SAFE_NAME)
            or skill not in scope.allowed_skill_names
            for skill in skills
        )
        or len(set(skills)) != len(skills)
    ):
        raise ProvisioningError("wrong_scope")
    if skills and default_agent is None:
        raise ProvisioningError("wrong_scope")

    if (
        not isinstance(payload.credentials, tuple)
        or len(payload.credentials) > _MAX_CREDENTIALS
    ):
        raise ProvisioningError("invalid_bundle")
    credentials: list[dict[str, Any]] = []
    for credential in payload.credentials:
        if (
            not isinstance(credential, ProvisioningCredential)
            or not isinstance(credential.category, str)
            or credential.category not in scope.allowed_credential_categories
        ):
            raise ProvisioningError("unauthorized_credential")
        if profile_data is None:
            raise ProvisioningError("unauthorized_credential")
        credentials.append(_credential_to_dict(credential, payload.profile))
    if profile_data is None and payload.credentials:
        raise ProvisioningError("unauthorized_credential")
    if profile_data is not None:
        required_category = f"provider:{payload.profile.provider}:api_key"
        if len(credentials) != 1 or credentials[0]["category"] != required_category:
            raise ProvisioningError("unauthorized_credential")
        if required_category not in scope.allowed_credential_categories:
            raise ProvisioningError("unauthorized_credential")

    return {
        "profile": profile_data,
        "networks": networks,
        "settings": {
            "default_agent": default_agent,
            "active_skills": list(skills),
        },
        "credentials": credentials,
    }


def seal_provisioning_bundle(
    payload: ProvisioningPayload,
    *,
    scope: ProvisioningScope,
    issuer_key: SigningKey,
    recipient_public_key: bytes | VerifyKey,
    revision: int,
    expires_at: int,
    now: int | None = None,
) -> bytes:
    """Sign and encrypt one bundle to the recipient's existing device key.

    Call only after the trusted issuer has rechecked the durable human
    delegation.  ``scope`` must be populated from that current delegation; its
    credential categories are enforced before any secret is serialized.
    """
    try:
        sorted_network_ids = _validate_scope(scope)
        if not isinstance(issuer_key, SigningKey):
            raise ProvisioningError("invalid_input")
        recipient_bytes = (
            bytes(recipient_public_key)
            if isinstance(recipient_public_key, VerifyKey)
            else recipient_public_key
        )
        if not isinstance(recipient_bytes, bytes) or len(recipient_bytes) != 32:
            raise ProvisioningError("invalid_input")
        recipient_key = VerifyKey(recipient_bytes)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ProvisioningError("invalid_input")
        current_time = int(time.time()) if now is None else int(now)
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= current_time
            or expires_at - current_time > _MAX_BUNDLE_TTL_SECONDS
        ):
            raise ProvisioningError("expired")
        content = _validate_payload(payload, scope)
        content_digest = hashlib.sha256(_canonical_json(content)).hexdigest()
        unsigned = {
            "type": _BUNDLE_TYPE,
            "version": _BUNDLE_VERSION,
            "issuer": public_key_id(bytes(issuer_key.verify_key)),
            "recipient": public_key_id(recipient_bytes),
            "enrollment_id": scope.enrollment_id,
            "network_ids": list(sorted_network_ids),
            "audience": scope.audience,
            "revision": revision,
            "issued_at": current_time,
            "expires_at": expires_at,
            "content_digest": content_digest,
            "content": content,
        }
        signature = issuer_key.sign(
            _BUNDLE_DOMAIN + _canonical_json(unsigned)
        ).signature.hex()
        signed = {**unsigned, "signature": signature}
        plaintext = bytearray(_canonical_json(signed))
        try:
            if len(plaintext) > _MAX_PLAINTEXT_BYTES:
                raise ProvisioningError("too_large")
            ciphertext = SealedBox(recipient_key.to_curve25519_public_key()).encrypt(
                bytes(plaintext)
            )
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        if len(ciphertext) > _MAX_BUNDLE_BYTES:
            raise ProvisioningError("too_large")
        return ciphertext
    except ProvisioningError:
        raise
    except (CryptoError, TypeError, ValueError, OverflowError):
        raise ProvisioningError("invalid_input") from None


def provisioning_payload_digest(
    payload: ProvisioningPayload, *, scope: ProvisioningScope
) -> str:
    """Return the canonical content digest that an install receipt must echo."""
    content = _validate_payload(payload, scope)
    return hashlib.sha256(_canonical_json(content)).hexdigest()


def _content_from_dict(content: Any, scope: ProvisioningScope) -> ProvisioningPayload:
    if not isinstance(content, dict) or set(content) != {
        "profile",
        "networks",
        "settings",
        "credentials",
    }:
        raise ProvisioningError("invalid_bundle")
    profile_data = content["profile"]
    profile: ProfilePreferences | None = None
    if profile_data is not None:
        if not isinstance(profile_data, dict) or set(profile_data) != {
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
        }:
            raise ProvisioningError("invalid_bundle")
        profile = ProfilePreferences(**profile_data)
    network_data = content["networks"]
    if not isinstance(network_data, list) or len(network_data) > _MAX_NETWORKS:
        raise ProvisioningError("invalid_bundle")
    networks: list[NetworkPreferences] = []
    for network in network_data:
        if not isinstance(network, dict) or set(network) != {
            "network_id",
            "discovery_domain",
        }:
            raise ProvisioningError("invalid_bundle")
        networks.append(NetworkPreferences(**network))
    settings_data = content["settings"]
    if not isinstance(settings_data, dict) or set(settings_data) != {
        "default_agent",
        "active_skills",
    }:
        raise ProvisioningError("invalid_bundle")
    if not isinstance(settings_data["active_skills"], list):
        raise ProvisioningError("invalid_bundle")
    settings = SafeAgentSettings(
        default_agent=settings_data["default_agent"],
        active_skills=tuple(settings_data["active_skills"]),
    )
    credential_data = content["credentials"]
    if not isinstance(credential_data, list) or len(credential_data) > _MAX_CREDENTIALS:
        raise ProvisioningError("invalid_bundle")
    credentials: list[ProvisioningCredential] = []
    for credential in credential_data:
        if not isinstance(credential, dict) or set(credential) != {
            "category",
            "profile_name",
            "secret",
        }:
            raise ProvisioningError("invalid_bundle")
        category = credential["category"]
        secret = credential["secret"]
        if not isinstance(secret, str):
            raise ProvisioningError("invalid_bundle")
        credentials.append(
            ProvisioningCredential(
                category=category,
                profile_name=credential["profile_name"],
                secret=secret,
            )
        )
    result = ProvisioningPayload(
        profile=profile,
        networks=tuple(networks),
        settings=settings,
        credentials=tuple(credentials),
    )
    _validate_payload(result, scope)
    return result


def _open_bundle(
    ciphertext: bytes,
    *,
    recipient_key: SigningKey,
    expectation: ProvisioningExpectation,
    now: int,
) -> tuple[ProvisioningPayload, int, str]:
    if (
        not isinstance(ciphertext, bytes)
        or not 48 <= len(ciphertext) <= _MAX_BUNDLE_BYTES
    ):
        raise ProvisioningError("invalid_bundle")
    if not isinstance(recipient_key, SigningKey) or not isinstance(
        expectation, ProvisioningExpectation
    ):
        raise ProvisioningError("invalid_input")
    expected_networks = _validate_scope(expectation.scope)
    if (
        not isinstance(expectation.issuer_public_key, bytes)
        or len(expectation.issuer_public_key) != 32
    ):
        raise ProvisioningError("invalid_input")
    try:
        plaintext = bytearray(
            SealedBox(recipient_key.to_curve25519_private_key()).decrypt(ciphertext)
        )
    except (CryptoError, TypeError, ValueError):
        raise ProvisioningError("wrong_recipient") from None
    try:
        if len(plaintext) > _MAX_PLAINTEXT_BYTES:
            raise ProvisioningError("too_large")
        try:
            bundle = _strict_json(bytes(plaintext))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise ProvisioningError("invalid_bundle") from None
    finally:
        for index in range(len(plaintext)):
            plaintext[index] = 0

    required = {
        "type",
        "version",
        "issuer",
        "recipient",
        "enrollment_id",
        "network_ids",
        "audience",
        "revision",
        "issued_at",
        "expires_at",
        "content_digest",
        "content",
        "signature",
    }
    if not isinstance(bundle, dict) or set(bundle) != required:
        raise ProvisioningError("invalid_bundle")
    signature = bundle["signature"]
    if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
        raise ProvisioningError("invalid_signature")
    if bundle["issuer"] != public_key_id(expectation.issuer_public_key):
        raise ProvisioningError("wrong_issuer")
    signed = {key: value for key, value in bundle.items() if key != "signature"}
    try:
        VerifyKey(expectation.issuer_public_key).verify(
            _BUNDLE_DOMAIN + _canonical_json(signed), bytes.fromhex(signature)
        )
    except (CryptoError, ValueError, TypeError):
        raise ProvisioningError("invalid_signature") from None

    if (
        bundle["type"] != _BUNDLE_TYPE
        or isinstance(bundle["version"], bool)
        or not isinstance(bundle["version"], int)
        or bundle["version"] != _BUNDLE_VERSION
    ):
        raise ProvisioningError("invalid_bundle")
    recipient_id = public_key_id(bytes(recipient_key.verify_key))
    if bundle["recipient"] != recipient_id:
        raise ProvisioningError("wrong_recipient")
    scope = expectation.scope
    if bundle["enrollment_id"] != scope.enrollment_id:
        raise ProvisioningError("wrong_enrollment")
    if (
        bundle["network_ids"] != list(expected_networks)
        or bundle["audience"] != scope.audience
    ):
        raise ProvisioningError("wrong_scope")

    revision = bundle["revision"]
    issued_at = bundle["issued_at"]
    expires_at = bundle["expires_at"]
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= now
        or issued_at > now + _MAX_CLOCK_SKEW_SECONDS
        or expires_at <= issued_at
        or expires_at - issued_at > _MAX_BUNDLE_TTL_SECONDS
    ):
        raise ProvisioningError("expired")
    digest = bundle["content_digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ProvisioningError("invalid_bundle")
    try:
        actual_digest = hashlib.sha256(_canonical_json(bundle["content"])).hexdigest()
    except (TypeError, ValueError, OverflowError):
        raise ProvisioningError("invalid_bundle") from None
    if digest != actual_digest:
        raise ProvisioningError("invalid_bundle")
    try:
        payload = _content_from_dict(bundle["content"], scope)
    except ProvisioningError:
        raise
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise ProvisioningError("invalid_bundle") from None
    return payload, revision, digest


def _rollback_safely(transaction: ProvisioningTransaction) -> None:
    try:
        transaction.rollback()
    except Exception:
        # Adapter exception messages can contain provider data or file contents.
        pass


def install_provisioning_bundle(
    ciphertext: bytes,
    *,
    recipient_key: SigningKey,
    expectation: ProvisioningExpectation,
    store: ProvisioningStore,
    now: int | None = None,
) -> InstallReceipt:
    """Verify and atomically install an authorized device bundle.

    Returns a local storage receipt only.  The enrollment coordinator must wait
    for its matching remote acknowledgment before reporting joined/complete.
    """
    current_time = int(time.time()) if now is None else int(now)
    payload, revision, digest = _open_bundle(
        ciphertext,
        recipient_key=recipient_key,
        expectation=expectation,
        now=current_time,
    )
    scope = expectation.scope
    transaction: ProvisioningTransaction | None = None
    try:
        transaction = store.begin()
        installed = transaction.installed_revision(scope.enrollment_id)
        if installed is not None:
            if installed.revision == revision and installed.digest == digest:
                _rollback_safely(transaction)
                return InstallReceipt(
                    "already_installed", scope.enrollment_id, revision, digest
                )
            if installed.revision >= revision:
                raise ProvisioningError("replayed")
        transaction.ensure_targets_available(payload)
        transaction.stage_payload(payload)
        transaction.stage_revision(scope.enrollment_id, revision, digest)
        transaction.commit()
        return InstallReceipt("installed", scope.enrollment_id, revision, digest)
    except ProvisioningError:
        if transaction is not None:
            _rollback_safely(transaction)
        raise
    except Exception:
        if transaction is not None:
            _rollback_safely(transaction)
        raise ProvisioningError("storage_failure") from None


__all__ = [
    "InstallReceipt",
    "InstalledRevision",
    "NetworkPreferences",
    "ProfilePreferences",
    "ProvisioningCredential",
    "ProvisioningError",
    "ProvisioningExpectation",
    "ProvisioningPayload",
    "provisioning_payload_digest",
    "ProvisioningScope",
    "ProvisioningStore",
    "SafeAgentSettings",
    "install_provisioning_bundle",
    "seal_provisioning_bundle",
]
