"""Authenticated HTTPS and end-to-end ciphertext for device enrollment.

The relay sees signed mailbox metadata and a scrypt verifier. Enrollment
envelopes use a separate code-derived key that is never sent to the relay.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import ssl
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import dns.asyncresolver
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.signing import SigningKey, VerifyKey

from .dns.discovery import DiscoveryError, _PublicResolver, normalize_target
from .dns.private_directory import (
    PrivateDirectory,
    prove_pairing,
    public_key_id,
    verify_pairing_proof,
)
from .enrollment_codes import (
    EnrollmentCode,
    EnrollmentEnvelopeKey,
    derive_enrollment_code_verifier,
    derive_enrollment_envelope_key,
    enrollment_verifier_hash,
    generate_enrollment_code,
    parse_enrollment_code,
)
from .enrollment_delegations import (
    EnrollmentDelegationStore,
    EnrollmentRequestRecord,
    sign_installation_receipt,
    verify_installation_receipt,
)
from .enrollment_recovery import (
    EnrollmentRecoveryJournal,
    derive_enrollment_destination_recovery_key,
    derive_enrollment_recovery_key,
)

_SIGNATURE_DOMAIN = b"kollab-relay-enrollment-http/1\0"
_MESSAGE_SIGNATURE_DOMAIN = b"kollab-relay-enrollment-message/1\0"
_MAX_ENVELOPE_BYTES = 24 * 1024
# Keep each enrollment decision below the envelope cap after the bundle is
# base64-embedded alongside membership credentials and the room invitation.
_MAX_PROVIDER_CREDENTIAL_BYTES = 8 * 1024
# The relay accepts a 24 KiB ciphertext envelope, which becomes 32 KiB of
# base64url plus signed JSON metadata. Keep the client cap bounded below the
# relay's 64 KiB request-frame limit while allowing every valid envelope.
_MAX_HTTP_BODY_BYTES = 40 * 1024
_MAX_RESPONSE_BYTES = _MAX_HTTP_BODY_BYTES
_ALLOWED_ERRORS = {
    "invalid_request",
    "invalid_contact",
    "unauthorized",
    "unavailable",
    "bound",
    "claimed",
    "not_ready",
    "conflict",
    "request_too_large",
    "rate_limited",
    "replayed",
    "capacity",
    "backend_unavailable",
}
_PATH = re.compile(
    r"/relay/v1/enrollment/(?:offers|offers/[0-9a-f]{32}/(?:request|poll|challenge|proof|decision|reply/poll|ack|ack/poll))\Z"
)


class EnrollmentProtocolError(ValueError):
    """A safe fixed enrollment-protocol failure code."""

    def __init__(self, code: str, *, retry_after_seconds: float | None = None):
        self.code = code if code in _ALLOWED_ERRORS | {"transport", "invalid_response"} else "transport"
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.code)


def _parse_retry_after(value: str | None) -> float | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}", value):
        return None
    seconds = int(value)
    return float(min(seconds, 120)) if seconds > 0 else None


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def enrollment_signature_message(origin: str, method: str, path: str, body: dict[str, Any]) -> bytes:
    """Build the relay's canonical signature preimage."""
    if not isinstance(body, dict) or "signature" in body:
        raise EnrollmentProtocolError("invalid_request")
    try:
        return (
            _SIGNATURE_DOMAIN
            + method.upper().encode("ascii")
            + b"\n"
            + origin.encode("ascii")
            + b"\n"
            + path.encode("ascii")
            + b"\n"
            + _canonical_json(body)
        )
    except (UnicodeError, TypeError, ValueError) as exc:
        raise EnrollmentProtocolError("invalid_request") from exc


def signed_enrollment_frame(
    signing_key: SigningKey,
    origin: str,
    path: str,
    offer_id: str,
    fields: dict[str, Any],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Create a fresh signed POST frame without retaining the signature bytes."""
    if not _PATH.fullmatch(path) or not re.fullmatch(r"[0-9a-f]{32}", offer_id):
        raise EnrollmentProtocolError("invalid_request")
    if not isinstance(fields, dict) or {"v", "offer_id", "issued_at", "nonce", "signature"} & fields.keys():
        raise EnrollmentProtocolError("invalid_request")
    frame = {
        "v": 1,
        "offer_id": offer_id,
        "issued_at": int(time.time()) if now is None else int(now),
        "nonce": secrets.token_hex(16),
        **fields,
    }
    signature = signing_key.sign(enrollment_signature_message(origin, "POST", path, frame)).signature.hex()
    return {**frame, "signature": signature}


def sign_enrollment_payload(signing_key: SigningKey, payload: dict[str, Any]) -> dict[str, Any]:
    """Sign the inner owner-to-device control message."""
    if not isinstance(payload, dict) or "owner_signature" in payload:
        raise EnrollmentProtocolError("invalid_request")
    signature = signing_key.sign(_MESSAGE_SIGNATURE_DOMAIN + _canonical_json(payload)).signature.hex()
    return {**payload, "owner_signature": signature}


def verify_enrollment_payload(verify_key: bytes, payload: dict[str, Any]) -> dict[str, Any]:
    """Verify an inner control message against the signed discovery key."""
    signature = payload.get("owner_signature") if isinstance(payload, dict) else None
    if (
        not isinstance(verify_key, bytes)
        or len(verify_key) != 32
        or not isinstance(signature, str)
        or not re.fullmatch(r"[0-9a-f]{128}", signature)
    ):
        raise EnrollmentProtocolError("invalid_response")
    signed = {name: value for name, value in payload.items() if name != "owner_signature"}
    try:
        VerifyKey(verify_key).verify(
            _MESSAGE_SIGNATURE_DOMAIN + _canonical_json(signed),
            bytes.fromhex(signature),
        )
    except (CryptoError, ValueError, TypeError) as exc:
        raise EnrollmentProtocolError("invalid_response") from exc
    return signed


def encrypt_enrollment_envelope(key: EnrollmentEnvelopeKey, payload: dict[str, Any]) -> str:
    """Encrypt a bounded JSON envelope; ciphertext is opaque to the relay."""
    if not isinstance(key, EnrollmentEnvelopeKey) or not isinstance(payload, dict):
        raise EnrollmentProtocolError("invalid_request")
    try:
        ciphertext = SecretBox(key.for_envelope_encryption()).encrypt(_canonical_json(payload))
    except (CryptoError, TypeError, ValueError) as exc:
        raise EnrollmentProtocolError("invalid_request") from exc
    if len(ciphertext) > _MAX_ENVELOPE_BYTES:
        raise EnrollmentProtocolError("request_too_large")
    return base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")


def decrypt_enrollment_envelope(key: EnrollmentEnvelopeKey, envelope: str) -> dict[str, Any]:
    """Authenticate and decode a ciphertext envelope with strict JSON limits."""
    if not isinstance(key, EnrollmentEnvelopeKey) or not isinstance(envelope, str):
        raise EnrollmentProtocolError("invalid_response")
    if len(envelope) > ((_MAX_ENVELOPE_BYTES + 2) // 3) * 4:
        raise EnrollmentProtocolError("invalid_response")
    try:
        raw = base64.urlsafe_b64decode(envelope + "=" * (-len(envelope) % 4))
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != envelope:
            raise ValueError("noncanonical envelope")
        if len(raw) > _MAX_ENVELOPE_BYTES:
            raise ValueError("envelope too large")
        plaintext = SecretBox(key.for_envelope_encryption()).decrypt(raw)

        def pairs(items):
            result = {}
            for name, value in items:
                if name in result:
                    raise ValueError("duplicate JSON key")
                result[name] = value
            return result

        def constant(_):
            raise ValueError("non-finite JSON number")

        payload = json.loads(plaintext.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(payload, dict) or len(plaintext) > _MAX_ENVELOPE_BYTES:
            raise ValueError("invalid envelope payload")
        return payload
    except (CryptoError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise EnrollmentProtocolError("invalid_response") from exc


class EnrollmentHTTPClient:
    """Same-origin HTTPS-only client using discovery's validated DNS resolver."""

    def __init__(
        self,
        origin: str,
        *,
        ca: str = "",
        private_cidrs: tuple[str, ...] = (),
    ) -> None:
        try:
            target = normalize_target(origin, document=False)
        except (DiscoveryError, TypeError, ValueError) as exc:
            raise EnrollmentProtocolError("invalid_request") from exc
        if target.origin != origin or target.url != origin:
            raise EnrollmentProtocolError("invalid_request")
        self.origin = origin
        self.ca = ca or ""
        self.private_cidrs = tuple(private_cidrs)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> EnrollmentHTTPClient:
        context = ssl.create_default_context(cafile=self.ca or None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connector = aiohttp.TCPConnector(
            resolver=_PublicResolver(dns.asyncresolver.Resolver(), self.private_cidrs),
            use_dns_cache=False,
            force_close=True,
            ssl=context,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=10, connect=5, sock_connect=5),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def post(
        self,
        path: str,
        frame: dict[str, Any],
        *,
        expected_statuses: set[int] | frozenset[int] = frozenset({200, 201, 202}),
    ) -> dict[str, Any]:
        if self._session is None:
            raise EnrollmentProtocolError("transport")
        if not _PATH.fullmatch(path):
            raise EnrollmentProtocolError("invalid_request")
        body = _canonical_json(frame)
        if len(body) > _MAX_HTTP_BODY_BYTES:
            raise EnrollmentProtocolError("request_too_large")
        url = self.origin + path
        try:
            async with self._session.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
            ) as response:
                if str(response.url) != url or response.status in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }:
                    raise EnrollmentProtocolError("transport")
                if response.status not in expected_statuses:
                    code = await self._error_code(response)
                    retry_after = (
                        _parse_retry_after(response.headers.get("Retry-After")) if code == "rate_limited" else None
                    )
                    raise EnrollmentProtocolError(code, retry_after_seconds=retry_after)
                if (
                    response.content_type != "application/json"
                    or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                ):
                    raise EnrollmentProtocolError("invalid_response")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise EnrollmentProtocolError("invalid_response")
                return self._strict_json(bytes(raw))
        except EnrollmentProtocolError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
            raise EnrollmentProtocolError("transport") from exc

    async def post_signed(
        self,
        signing_key: SigningKey,
        path: str,
        offer_id: str,
        fields: dict[str, Any],
        *,
        expected_statuses: set[int] | frozenset[int] = frozenset({200, 201, 202}),
    ) -> dict[str, Any]:
        frame = signed_enrollment_frame(signing_key, self.origin, path, offer_id, fields)
        return await self.post(path, frame, expected_statuses=expected_statuses)

    async def post_signed_retry(
        self,
        signing_key: SigningKey,
        path: str,
        offer_id: str,
        fields: dict[str, Any],
        *,
        expected_statuses: set[int] | frozenset[int] = frozenset({200, 201, 202}),
    ) -> dict[str, Any]:
        """Retry an idempotent phase with the same payload and fresh signatures."""
        delay = 0.5
        for attempt in range(3):
            try:
                return await self.post_signed(
                    signing_key,
                    path,
                    offer_id,
                    fields,
                    expected_statuses=expected_statuses,
                )
            except EnrollmentProtocolError as exc:
                if exc.code not in {"rate_limited", "transport"} or attempt == 2:
                    raise
                if exc.code == "rate_limited":
                    await asyncio.sleep(exc.retry_after_seconds or 15.0)
                else:
                    await asyncio.sleep(delay)
                delay = min(delay * 2, 4.0)
        raise EnrollmentProtocolError("transport")

    async def _error_code(self, response: aiohttp.ClientResponse) -> str:
        if response.content_type != "application/json":
            return "transport"
        raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            return "transport"
        try:
            body = self._strict_json(raw)
        except EnrollmentProtocolError:
            return "transport"
        code = body.get("error")
        return code if isinstance(code, str) and code in _ALLOWED_ERRORS else "transport"

    @staticmethod
    def _strict_json(raw: bytes) -> dict[str, Any]:
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise EnrollmentProtocolError("invalid_response")

        def pairs(items):
            result = {}
            for name, value in items:
                if name in result:
                    raise ValueError("duplicate JSON key")
                result[name] = value
            return result

        def constant(_):
            raise ValueError("non-finite JSON number")

        try:
            parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
            if not isinstance(parsed, dict):
                raise ValueError("JSON object required")
            return parsed
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise EnrollmentProtocolError("invalid_response") from exc


def validate_phase_envelope(
    payload: dict[str, Any],
    *,
    offer_id: str,
    round_id: str,
    phase: str,
    destination_key: str,
) -> dict[str, Any]:
    """Require every encrypted phase to bind its mailbox routing metadata."""
    if (
        not isinstance(payload, dict)
        or type(payload.get("v")) is not int
        or payload.get("v") != 1
        or payload.get("offer_id") != offer_id
        or payload.get("round_id") != round_id
        or payload.get("phase") != phase
        or payload.get("destination_key") != destination_key
    ):
        raise EnrollmentProtocolError("invalid_response")
    return payload


def verify_signed_enrollment_frame(public_key: str, origin: str, method: str, path: str, frame: dict[str, Any]) -> bool:
    """Verify one relayed request frame using its original signing context."""
    if (
        not isinstance(public_key, str)
        or not re.fullmatch(r"[0-9a-f]{64}", public_key)
        or not isinstance(frame, dict)
        or not isinstance(frame.get("signature"), str)
        or not re.fullmatch(r"[0-9a-f]{128}", frame["signature"])
    ):
        return False
    unsigned = {key: value for key, value in frame.items() if key != "signature"}
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            enrollment_signature_message(origin, method, path, unsigned),
            bytes.fromhex(frame["signature"]),
        )
        return True
    except (CryptoError, ValueError, TypeError, UnicodeError):
        return False


def _provisioning_scope_from_payload(value: Any, *, enrollment_id: str, workspace_id: str):
    from .provisioning import ProvisioningError, ProvisioningScope

    profile_name = value.get("profile_name") if isinstance(value, dict) else None
    credential_categories = value.get("allowed_credential_categories") if isinstance(value, dict) else None
    profile_scope_valid = (
        profile_name is None
        and credential_categories == []
        or isinstance(profile_name, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", profile_name)
        and isinstance(credential_categories, list)
        and len(credential_categories) == 1
        and credential_categories[0]
        in {
            "provider:openai:api_key",
            "provider:anthropic:api_key",
            "provider:azure_openai:api_key",
            "provider:custom:api_key",
            "provider:openrouter:api_key",
            "provider:openai_responses:api_key",
            "provider:gemini:api_key",
            "provider:openai:oauth_tokens",
        }
    )
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "enrollment_id",
            "network_ids",
            "audience",
            "profile_name",
            "allowed_credential_categories",
            "allowed_agent_names",
            "allowed_skill_names",
        }
        or value["enrollment_id"] != enrollment_id
        or value["audience"] != workspace_id
        or not profile_scope_valid
        or value["allowed_agent_names"] != []
        or value["allowed_skill_names"] != []
        or not isinstance(value["network_ids"], list)
        or not 1 <= len(value["network_ids"]) <= 64
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", item)
            for item in value["network_ids"]
        )
        or value["network_ids"] != sorted(set(value["network_ids"]))
        or not re.fullmatch(r"[0-9a-f]{32}", workspace_id)
    ):
        raise EnrollmentProtocolError("invalid_response")
    try:
        return ProvisioningScope(
            enrollment_id=enrollment_id,
            network_ids=tuple(value["network_ids"]),
            audience=workspace_id,
            profile_name=profile_name,
            allowed_credential_categories=frozenset(credential_categories),
            allowed_agent_names=frozenset(),
            allowed_skill_names=frozenset(),
        )
    except (ProvisioningError, TypeError, ValueError) as exc:
        raise EnrollmentProtocolError("invalid_response") from exc


def _profile_manager(plugin):
    event_bus = getattr(plugin, "event_bus", None)
    get_service = getattr(event_bus, "get_service", None)
    if get_service is None:
        return None
    try:
        return get_service("profile_manager")
    except Exception:
        return None


def _profile_reference(name: str) -> str:
    digest = hashlib.sha256(b"kollab-relay-enrollment-profile-v1\0" + name.encode("utf-8")).hexdigest()
    return "profile:" + digest


def _profile_preferences(profile, *, name: str | None = None):
    from .provisioning import ProfilePreferences, ProvisioningError, _validate_profile

    profile_name = name or getattr(profile, "name", None)
    if not isinstance(profile_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", profile_name):
        raise EnrollmentProtocolError("unavailable")
    provider = profile.get_provider()
    model = profile.get_model()
    auth_type = getattr(profile, "auth_type", "") or "api_key"
    if auth_type not in {"api_key", "oauth"}:
        raise EnrollmentProtocolError("unavailable")
    max_tokens = profile.get_max_tokens()
    context_window = getattr(profile, "context_window", None)
    preferences = ProfilePreferences(
        name=profile_name,
        provider=provider,
        model=model,
        auth_type=auth_type,
        base_url=profile.get_endpoint() or None,
        temperature=profile.get_temperature(),
        max_tokens=16384 if max_tokens is None else max_tokens,
        context_window=200000 if context_window is None else context_window,
        top_p=profile.get_top_p(),
        effort=profile.get_effort() or None,
        organization=getattr(profile, "organization", None),
        api_version=getattr(profile, "api_version", None),
        azure_endpoint=getattr(profile, "azure_endpoint", None),
        deployment_id=getattr(profile, "deployment_id", None),
        http_referer=getattr(profile, "http_referer", None),
        x_title=getattr(profile, "x_title", None),
        project_id=getattr(profile, "project_id", None),
        location=getattr(profile, "location", None),
        store_responses=getattr(profile, "store_responses", None),
    )
    try:
        _validate_profile(preferences)
    except ProvisioningError as exc:
        raise EnrollmentProtocolError("unavailable") from exc
    return preferences


def _provisioning_plan_to_recovery(plan: _ProvisioningPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "source_profile_name": plan.source_profile_name,
        "destination_profile_name": plan.destination_profile_name,
        "profile_preferences": asdict(plan.profile_preferences),
        "credential_category": plan.credential_category,
        "display": plan.display,
    }


def _provisioning_plan_from_recovery(value: Any) -> _ProvisioningPlan | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "source_profile_name",
        "destination_profile_name",
        "profile_preferences",
        "credential_category",
        "display",
    }:
        raise EnrollmentProtocolError("invalid_response")
    preferences = value["profile_preferences"]
    if not isinstance(preferences, dict):
        raise EnrollmentProtocolError("invalid_response")
    try:
        from .provisioning import ProfilePreferences, ProvisioningError, _validate_profile

        profile_preferences = ProfilePreferences(**preferences)
        _validate_profile(profile_preferences)
    except (TypeError, ValueError, ProvisioningError) as exc:
        raise EnrollmentProtocolError("invalid_response") from exc
    for key in ("source_profile_name", "destination_profile_name", "credential_category", "display"):
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > 256:
            raise EnrollmentProtocolError("invalid_response")
    return _ProvisioningPlan(
        source_profile_name=value["source_profile_name"],
        destination_profile_name=value["destination_profile_name"],
        profile_preferences=profile_preferences,
        credential_category=value["credential_category"],
        display=value["display"],
    )


def _encode_recovery_value(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_recovery_value(value: Any, *, maximum: int) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
    ):
        raise EnrollmentProtocolError("invalid_response")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise EnrollmentProtocolError("invalid_response") from exc
    if _encode_recovery_value(decoded) != value:
        raise EnrollmentProtocolError("invalid_response")
    return decoded


def _profile_credential_category(profile) -> str | None:
    provider = profile.get_provider()
    auth_type = getattr(profile, "auth_type", "") or "api_key"
    if auth_type == "oauth" and provider == "openai_responses":
        return "provider:openai:oauth_tokens"
    if auth_type != "api_key" or provider not in {
        "openai",
        "anthropic",
        "azure_openai",
        "custom",
        "openrouter",
        "openai_responses",
        "gemini",
    }:
        return None
    return f"provider:{provider}:api_key"


def _valid_credential_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.encode("utf-8")) <= _MAX_PROVIDER_CREDENTIAL_BYTES
        and value == value.strip()
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    )


async def _profile_credential(profile, *, category: str, destination_profile_name: str):
    from .provisioning import (
        OpenAIOAuthCredential,
        ProvisioningCredential,
    )

    if category.endswith(":api_key"):
        secret = profile.get_api_key()
        if not _valid_credential_text(secret):
            raise EnrollmentProtocolError("unavailable")
        return ProvisioningCredential(category, destination_profile_name, secret)
    if category == "provider:openai:oauth_tokens":
        from kollabor_ai.oauth.token_storage import OAuthTokenStorage

        tokens = await OAuthTokenStorage().load_tokens(
            "openai",
            auto_refresh=False,
            profile_name=(profile.name if getattr(profile, "is_provisioned", False) else None),
        )
        if (
            tokens is None
            or not _valid_credential_text(tokens.access_token)
            or not _valid_credential_text(tokens.refresh_token)
            or len(tokens.access_token.encode("utf-8")) + len(tokens.refresh_token.encode("utf-8"))
            > _MAX_PROVIDER_CREDENTIAL_BYTES
            or isinstance(tokens.expires_at, bool)
            or not isinstance(tokens.expires_at, (int, float))
            or not math.isfinite(tokens.expires_at)
            or tokens.expires_at <= 0
            or (tokens.account_id is not None and not _valid_credential_text(tokens.account_id))
            or (tokens.account_id is not None and len(tokens.account_id) > 256)
        ):
            raise EnrollmentProtocolError("unavailable")
        secret = OpenAIOAuthCredential(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            account_id=tokens.account_id,
        )
        return ProvisioningCredential(category, destination_profile_name, secret)
    raise EnrollmentProtocolError("unavailable")


@dataclass(frozen=True, slots=True)
class _ProvisioningPlan:
    source_profile_name: str
    destination_profile_name: str
    profile_preferences: Any = field(repr=False)
    credential_category: str
    display: str


_DESTINATION_RECOVERY_STATUSES = {
    "request_ready": 0,
    "challenge_saved": 1,
    "proof_ready": 2,
    "proof_stored": 3,
    "decision_saved": 4,
    "install_committed": 5,
    "ack_ready": 6,
    "ack_stored": 7,
    "credential_imported": 8,
    "invite_joined": 9,
    "attached": 10,
}
_DESTINATION_RECOVERY_ACTIVE: set[tuple[str, str]] = set()
_DESTINATION_RECOVERY_MAX_AGE = 24 * 60 * 60
_DESTINATION_RETRY_BASE_SECONDS = 5
_DESTINATION_RETRY_MAX_SECONDS = 300
_DESTINATION_RETRY_MAX_ATTEMPTS = 10


def _destination_recovery_journal(client) -> EnrollmentRecoveryJournal:
    return EnrollmentRecoveryJournal(
        client.state_dir / "enrollment-destination-recovery.json",
        derive_enrollment_destination_recovery_key(client._store.key.encode()),
    )


def _destination_recovery_slot(client, offer_id: str) -> tuple[str, str]:
    return str(client.state_dir.resolve()), offer_id


def _claim_destination_recovery(client, offer_id: str) -> bool:
    slot = _destination_recovery_slot(client, offer_id)
    if slot in _DESTINATION_RECOVERY_ACTIVE:
        return False
    _DESTINATION_RECOVERY_ACTIVE.add(slot)
    return True


def _release_destination_recovery(client, offer_id: str) -> None:
    _DESTINATION_RECOVERY_ACTIVE.discard(_destination_recovery_slot(client, offer_id))


def _destination_retry_delay(attempt: int) -> int:
    """Return capped exponential backoff for a persisted recovery attempt."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("retry attempt must be a positive integer")
    exponent = min(attempt - 1, 6)
    return min(_DESTINATION_RETRY_BASE_SECONDS * (2**exponent), _DESTINATION_RETRY_MAX_SECONDS)


def _schedule_destination_retry(
    journal: EnrollmentRecoveryJournal,
    record: dict[str, Any],
    error_code: str,
    *,
    now: int | None = None,
) -> None:
    allowed = _ALLOWED_ERRORS | {"transport", "invalid_response"}
    safe_error = (
        error_code
        if isinstance(error_code, str) and error_code in allowed
        else "transport"
    )
    attempts = min(record["retry_attempts"] + 1, _DESTINATION_RETRY_MAX_ATTEMPTS)
    retry_after = (int(time.time()) if now is None else int(now)) + _destination_retry_delay(attempts)
    record.update(
        retry_attempts=attempts,
        retry_after=retry_after,
        last_error_code=safe_error,
    )
    journal.put(record["offer_id"], record)


def _schedule_issuer_recovery_retry(
    journal: EnrollmentRecoveryJournal,
    record: dict[str, Any],
    error_code: str,
    *,
    now: int | None = None,
) -> None:
    allowed = _ALLOWED_ERRORS | {"transport", "invalid_response"}
    safe_error = (
        error_code
        if isinstance(error_code, str) and error_code in allowed
        else "transport"
    )
    previous = record.get("retry_attempts", 0)
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        previous = 0
    attempts = min(previous + 1, _DESTINATION_RETRY_MAX_ATTEMPTS)
    retry_after = (int(time.time()) if now is None else int(now)) + _destination_retry_delay(attempts)
    record.update(
        retry_attempts=attempts,
        retry_after=retry_after,
        last_error_code=safe_error,
    )
    journal.put(record["offer_id"], record)


def _store_issuer_recovery_progress(
    journal: EnrollmentRecoveryJournal,
    record: dict[str, Any],
) -> None:
    record.update(retry_attempts=0, retry_after=0, last_error_code=None)
    journal.put(record["offer_id"], record)


def _validate_destination_recovery_record(record: Any, offer_id: str) -> dict[str, Any]:
    fields = {
        "version",
        "offer_id",
        "status",
        "created_at",
        "domain",
        "origin",
        "owner_key",
        "issuer_relay_key",
        "destination_key",
        "workspace_id",
        "round_id",
        "code_verifier",
        "envelope_key",
        "request_envelope",
        "challenge_envelope",
        "proof_envelope",
        "decision_envelope",
        "expires_at",
        "install_status",
        "revision",
        "digest",
        "ack_envelope",
        "ack_receipt",
        "retry_attempts",
        "retry_after",
        "last_error_code",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise EnrollmentProtocolError("invalid_response")
    if (
        type(record["version"]) is not int
        or record["version"] != 1
        or record["offer_id"] != offer_id
        or record["status"] not in _DESTINATION_RECOVERY_STATUSES
        or isinstance(record["created_at"], bool)
        or not isinstance(record["created_at"], int)
        or not 0 < record["created_at"] <= int(time.time()) + 30
        or not isinstance(record["domain"], str)
        or not record["domain"]
        or len(record["domain"]) > 253
        or not isinstance(record["origin"], str)
        or len(record["origin"]) > 512
        or not isinstance(record["destination_key"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", record["destination_key"])
        or not isinstance(record["workspace_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", record["workspace_id"])
        or not isinstance(record["round_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", record["round_id"])
        or not isinstance(record["code_verifier"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", record["code_verifier"])
        or not isinstance(record["envelope_key"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", record["envelope_key"])
        or not isinstance(record["request_envelope"], str)
        or len(record["request_envelope"]) > 32768
    ):
        raise EnrollmentProtocolError("invalid_response")
    issuer_key = record["issuer_relay_key"]
    if issuer_key is not None and (
        not isinstance(issuer_key, str) or not re.fullmatch(r"[0-9a-f]{64}", issuer_key)
    ):
        raise EnrollmentProtocolError("invalid_response")
    owner_key = record["owner_key"]
    if owner_key is not None and (
        not isinstance(owner_key, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_key)
    ):
        raise EnrollmentProtocolError("invalid_response")
    for name, maximum in (
        ("challenge_envelope", 32768),
        ("proof_envelope", 32768),
        ("decision_envelope", 65536),
        ("ack_envelope", 32768),
    ):
        value = record[name]
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise EnrollmentProtocolError("invalid_response")
    for name in ("expires_at", "revision"):
        value = record[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise EnrollmentProtocolError("invalid_response")
    if record["digest"] is not None and (
        not isinstance(record["digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", record["digest"])
    ):
        raise EnrollmentProtocolError("invalid_response")
    if record["install_status"] not in {None, "installed", "already_installed"}:
        raise EnrollmentProtocolError("invalid_response")
    if record["ack_receipt"] not in {None, record["round_id"]}:
        raise EnrollmentProtocolError("invalid_response")
    if (
        isinstance(record["retry_attempts"], bool)
        or not isinstance(record["retry_attempts"], int)
        or not 0 <= record["retry_attempts"] <= _DESTINATION_RETRY_MAX_ATTEMPTS
        or isinstance(record["retry_after"], bool)
        or not isinstance(record["retry_after"], int)
        or record["retry_after"] < 0
        or record["last_error_code"] is not None
        and record["last_error_code"] not in _ALLOWED_ERRORS | {"transport", "invalid_response"}
    ):
        raise EnrollmentProtocolError("invalid_response")
    rank = _DESTINATION_RECOVERY_STATUSES[record["status"]]
    if rank >= 1 and (
        record["challenge_envelope"] is None
        or record["expires_at"] is None
        or issuer_key is None
        or owner_key is None
    ):
        raise EnrollmentProtocolError("invalid_response")
    if rank >= 2 and record["proof_envelope"] is None:
        raise EnrollmentProtocolError("invalid_response")
    if rank >= 4 and record["decision_envelope"] is None:
        raise EnrollmentProtocolError("invalid_response")
    if rank >= 5 and (
        record["install_status"] is None
        or record["revision"] is None
        or record["digest"] is None
    ):
        raise EnrollmentProtocolError("invalid_response")
    if rank >= 6 and record["ack_envelope"] is None:
        raise EnrollmentProtocolError("invalid_response")
    if rank >= 7 and record["ack_receipt"] != record["round_id"]:
        raise EnrollmentProtocolError("invalid_response")
    return record


def _store_destination_recovery(
    journal: EnrollmentRecoveryJournal,
    record: dict[str, Any],
    status: str,
    **updates: Any,
) -> None:
    if _DESTINATION_RECOVERY_STATUSES[status] < _DESTINATION_RECOVERY_STATUSES[record["status"]]:
        raise EnrollmentProtocolError("conflict")
    record.update(updates)
    record.update(retry_attempts=0, retry_after=0, last_error_code=None)
    record["status"] = status
    journal.put(record["offer_id"], record)


async def _discover_destination(commands, domain: str):
    """Locate the relay. The issuer key comes from the code-authenticated
    challenge, not the discovery document (agent-device-pairing.md step 4)."""
    try:
        discovery, ca, private_cidrs, _is_card = await commands._discover(domain)
        relay_url = commands._relay_url(discovery)
        control = discovery.manifest["endpoints"].get("control")
    except Exception as exc:
        raise EnrollmentProtocolError("unavailable") from exc
    if not relay_url or control != discovery.origin + "/relay/v1":
        raise EnrollmentProtocolError("unavailable")
    return discovery, ca, private_cidrs


def _decode_destination_challenge(
    record: dict[str, Any],
    envelope_key: EnrollmentEnvelopeKey,
    owner_key: bytes,
    *,
    require_fresh: bool = True,
) -> dict[str, Any]:
    challenge = validate_phase_envelope(
        decrypt_enrollment_envelope(envelope_key, record["challenge_envelope"]),
        offer_id=record["offer_id"],
        round_id=record["round_id"],
        phase="challenge",
        destination_key=record["destination_key"],
    )
    _require_shape(
        challenge,
        {
            "v",
            "offer_id",
            "round_id",
            "phase",
            "destination_key",
            "owner_key",
            "issuer_relay_key",
            "origin",
            "expires_at",
            "pairing_challenge",
            "provisioning_scope",
            "owner_signature",
        },
    )
    expires_at = challenge["expires_at"]
    if (
        challenge["origin"] != record["origin"]
        or challenge["owner_key"] != record["owner_key"]
        or challenge["issuer_relay_key"] != record["issuer_relay_key"]
        or not isinstance(challenge["pairing_challenge"], str)
        or len(challenge["pairing_challenge"]) > 8192
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or (require_fresh and expires_at <= int(time.time()))
        or expires_at > int(time.time()) + 900
    ):
        raise EnrollmentProtocolError("invalid_response")
    verify_enrollment_payload(owner_key, challenge)
    return challenge


def _decode_destination_decision(
    record: dict[str, Any],
    envelope_key: EnrollmentEnvelopeKey,
    owner_key: bytes,
    *,
    require_unexpired_challenge: bool,
) -> tuple[dict[str, Any], dict[str, str] | None, Any]:
    decision = validate_phase_envelope(
        decrypt_enrollment_envelope(envelope_key, record["decision_envelope"]),
        offer_id=record["offer_id"],
        round_id=record["round_id"],
        phase="decision",
        destination_key=record["destination_key"],
    )
    if decision.get("status") == "rejected":
        _require_shape(
            decision,
            {
                "v",
                "offer_id",
                "round_id",
                "phase",
                "destination_key",
                "owner_key",
                "issuer_relay_key",
                "origin",
                "status",
                "owner_signature",
            },
        )
        verify_enrollment_payload(owner_key, decision)
        if (
            decision["origin"] != record["origin"]
            or decision["owner_key"] != record["owner_key"]
            or decision["issuer_relay_key"] != record["issuer_relay_key"]
        ):
            raise EnrollmentProtocolError("invalid_response")
        return decision, None, None
    _require_shape(
        decision,
        {
            "v",
            "offer_id",
            "round_id",
            "phase",
            "destination_key",
            "owner_key",
            "issuer_relay_key",
            "origin",
            "status",
            "credential",
            "invite",
            "provisioning_bundle",
            "owner_signature",
        },
    )
    verify_enrollment_payload(owner_key, decision)
    if (
        decision["status"] != "approved"
        or decision["origin"] != record["origin"]
        or decision["owner_key"] != record["owner_key"]
        or decision["issuer_relay_key"] != record["issuer_relay_key"]
        or not isinstance(decision["credential"], str)
        or len(decision["credential"]) > 16384
        or not isinstance(decision["invite"], str)
        or len(decision["invite"]) > 4096
        or not isinstance(decision["provisioning_bundle"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,27307}", decision["provisioning_bundle"])
    ):
        raise EnrollmentProtocolError("invalid_response")
    if require_unexpired_challenge and record["expires_at"] <= int(time.time()):
        raise EnrollmentProtocolError("unavailable")
    from .relay_state import parse_invite

    try:
        invite = parse_invite(decision["invite"])
    except Exception as exc:
        raise EnrollmentProtocolError("invalid_response") from exc
    if invite["origin"] != record["origin"] or invite["key"] != record["issuer_relay_key"]:
        raise EnrollmentProtocolError("invalid_response")
    try:
        scope = _provisioning_scope_from_payload(
        _decode_destination_challenge(
            record,
            envelope_key,
            owner_key,
            require_fresh=require_unexpired_challenge,
        )["provisioning_scope"],
            enrollment_id=record["round_id"],
            workspace_id=record["workspace_id"],
        )
    except EnrollmentProtocolError:
        raise
    except Exception as exc:
        raise EnrollmentProtocolError("invalid_response") from exc
    return decision, invite, scope


def _destination_state_empty(client) -> bool:
    state = client.state
    return not state.origin and not state.enabled and not state.inviter and not state.approvals


def _destination_state_matches_invite(client, invite: dict[str, str]) -> bool:
    state = client.state
    return (
        state.origin == invite["origin"]
        and state.room == invite["room"]
        and state.inviter == invite["key"]
        and invite["key"] in state.approvals
    )


async def _finish_destination_enrollment(
    commands,
    discovery,
    ca: str,
    private_cidrs: tuple[str, ...],
    owner_key: bytes,
    record: dict[str, Any],
    decision: dict[str, Any],
    invite: dict[str, str],
    scope,
    envelope_key: EnrollmentEnvelopeKey,
    journal: EnrollmentRecoveryJournal,
    transport,
) -> dict[str, str]:
    client = commands.client
    signing_key = client._store.key
    directory = PrivateDirectory(
        client.state_dir / "private-directory.json",
        owner_public_key=owner_key,
        workspace_id=record["workspace_id"],
    )
    credential = directory.validate_device_credential(decision["credential"])
    if (
        credential.owner_id != public_key_id(owner_key)
        or credential.device_id != public_key_id(bytes(signing_key.verify_key))
        or credential.expires_at <= int(time.time())
    ):
        raise EnrollmentProtocolError("invalid_response")

    if not _destination_state_empty(client) and not _destination_state_matches_invite(client, invite):
        raise EnrollmentProtocolError("conflict")

    rank = _DESTINATION_RECOVERY_STATUSES[record["status"]]
    if rank < _DESTINATION_RECOVERY_STATUSES["install_committed"]:
        try:
            bundle = base64.urlsafe_b64decode(
                decision["provisioning_bundle"]
                + "=" * (-len(decision["provisioning_bundle"]) % 4)
            )
            if (
                len(bundle) > 20 * 1024
                or base64.urlsafe_b64encode(bundle).rstrip(b"=").decode("ascii")
                != decision["provisioning_bundle"]
            ):
                raise ValueError("invalid provisioning bundle encoding")
        except (ValueError, TypeError) as exc:
            raise EnrollmentProtocolError("invalid_response") from exc
        from .provisioning import ProvisioningExpectation, install_provisioning_bundle
        from .provisioning_store import FilesystemProvisioningStore

        receipt = install_provisioning_bundle(
            bundle,
            recipient_key=signing_key,
            expectation=ProvisioningExpectation(issuer_public_key=owner_key, scope=scope),
            store=getattr(commands, "_provisioning_store", None) or FilesystemProvisioningStore(),
        )
        _store_destination_recovery(
            journal,
            record,
            "install_committed",
            install_status=receipt.status,
            revision=receipt.revision,
            digest=receipt.digest,
        )
    else:
        if record["revision"] is None or record["digest"] is None:
            raise EnrollmentProtocolError("invalid_response")

    rank = _DESTINATION_RECOVERY_STATUSES[record["status"]]
    if rank < _DESTINATION_RECOVERY_STATUSES["ack_ready"]:
        ack_payload = {
            "v": 1,
            "offer_id": record["offer_id"],
            "round_id": record["round_id"],
            "phase": "installation_ack",
            "destination_key": record["destination_key"],
            "workspace_id": record["workspace_id"],
            "status": record["install_status"],
            "revision": record["revision"],
            "digest": record["digest"],
        }
        signed_ack = sign_installation_receipt(signing_key, ack_payload)
        ack_envelope = encrypt_enrollment_envelope(envelope_key, signed_ack)
        _store_destination_recovery(
            journal, record, "ack_ready", ack_envelope=ack_envelope
        )
    else:
        signed_ack = validate_phase_envelope(
            decrypt_enrollment_envelope(envelope_key, record["ack_envelope"]),
            offer_id=record["offer_id"],
            round_id=record["round_id"],
            phase="installation_ack",
            destination_key=record["destination_key"],
        )
        _require_shape(
            signed_ack,
            {
                "v",
                "offer_id",
                "round_id",
                "phase",
                "destination_key",
                "workspace_id",
                "status",
                "revision",
                "digest",
                "device_signature",
            },
        )
        if (
            signed_ack["workspace_id"] != record["workspace_id"]
            or signed_ack["status"] != record["install_status"]
            or signed_ack["revision"] != record["revision"]
            or signed_ack["digest"] != record["digest"]
            or not verify_installation_receipt(record["destination_key"], signed_ack)
        ):
            raise EnrollmentProtocolError("invalid_response")

    if _DESTINATION_RECOVERY_STATUSES[record["status"]] < _DESTINATION_RECOVERY_STATUSES["ack_stored"]:
        stored_ack = await transport.post_signed_retry(
            signing_key,
            f"/relay/v1/enrollment/offers/{record['offer_id']}/ack",
            record["offer_id"],
            {
                "destination_key": record["destination_key"],
                "round_id": record["round_id"],
                "envelope": record["ack_envelope"],
            },
        )
        _require_shape(stored_ack, {"status", "receipt"})
        if stored_ack["status"] != "stored" or stored_ack["receipt"] != record["round_id"]:
            raise EnrollmentProtocolError("invalid_response")
        _store_destination_recovery(
            journal,
            record,
            "ack_stored",
            ack_receipt=stored_ack["receipt"],
        )

    if _DESTINATION_RECOVERY_STATUSES[record["status"]] < _DESTINATION_RECOVERY_STATUSES["credential_imported"]:
        directory.import_device_credential(decision["credential"])
        _store_destination_recovery(journal, record, "credential_imported")
    if _DESTINATION_RECOVERY_STATUSES[record["status"]] < _DESTINATION_RECOVERY_STATUSES["invite_joined"]:
        if _destination_state_empty(client):
            client.join_invite(decision["invite"])
        elif not _destination_state_matches_invite(client, invite):
            raise EnrollmentProtocolError("conflict")
        if not _destination_state_matches_invite(client, invite):
            raise EnrollmentProtocolError("invalid_response")
        _store_destination_recovery(journal, record, "invite_joined")

    status = client.status()
    attached = status.get("state") == "online" and status.get("origin") == invite["origin"]
    if not attached:
        async def attach_if_idle() -> None:
            current = client.status()
            if current.get("state") == "online" and current.get("origin") == invite["origin"]:
                return
            task = getattr(client, "_task", None)
            if client.state.enabled or (task is not None and not task.done()):
                # RelayCommands.resume owns startup reconnects. Do not race it
                # with a second RelayClient.connect/close cycle.
                raise EnrollmentProtocolError("unavailable")
            await commands._attach(discovery, ca, private_cidrs)

        lock = getattr(commands, "_lock", None)
        if lock is None:
            await attach_if_idle()
        else:
            async with lock:
                await attach_if_idle()
        status = client.status()
        attached = status.get("state") == "online" and status.get("origin") == invite["origin"]
    if not attached:
        raise EnrollmentProtocolError("unavailable")
    _store_destination_recovery(journal, record, "attached")
    journal.delete(record["offer_id"])
    return {"status": "approved"}


async def _drive_destination_enrollment(
    commands,
    record: dict[str, Any],
    journal: EnrollmentRecoveryJournal,
    envelope_key: EnrollmentEnvelopeKey,
) -> dict[str, str]:
    client = commands.client
    offer_id = record["offer_id"]
    record = _validate_destination_recovery_record(record, offer_id)
    if (
        record["destination_key"] != client.public_key
        or record["workspace_id"] != client.state.workspace_id
    ):
        raise EnrollmentProtocolError("conflict")
    discovery, ca, private_cidrs = await _discover_destination(commands, record["domain"])
    if discovery.origin != record["origin"]:
        raise EnrollmentProtocolError("conflict")
    # Pinned from the first code-authenticated challenge; None before it.
    owner_key = bytes.fromhex(record["owner_key"]) if record["owner_key"] else None

    rank = _DESTINATION_RECOVERY_STATUSES[record["status"]]
    decision = invite = scope = None
    if record["decision_envelope"] is not None:
        decision, invite, scope = _decode_destination_decision(
            record,
            envelope_key,
            owner_key,
            require_unexpired_challenge=rank < _DESTINATION_RECOVERY_STATUSES["ack_ready"],
        )
        if decision["status"] == "rejected":
            journal.delete(offer_id)
            return {"status": "rejected"}
        if not _destination_state_empty(client) and not _destination_state_matches_invite(client, invite):
            raise EnrollmentProtocolError("conflict")
    elif not _destination_state_empty(client):
        raise EnrollmentProtocolError("conflict")

    signing_key = client._store.key
    async with EnrollmentHTTPClient(discovery.origin, ca=ca, private_cidrs=private_cidrs) as transport:
        if rank < _DESTINATION_RECOVERY_STATUSES["decision_saved"]:
            if int(time.time()) > record["created_at"] + 600:
                journal.delete(offer_id)
                raise EnrollmentProtocolError("unavailable")
            request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
            submitted = await transport.post_signed_retry(
                signing_key,
                request_path,
                offer_id,
                {
                    "destination_key": record["destination_key"],
                    "round_id": record["round_id"],
                    "code_verifier": record["code_verifier"],
                    "envelope": record["request_envelope"],
                },
            )
            _require_shape(submitted, {"status", "receipt"})
            if submitted["status"] != "pending" or submitted["receipt"] != record["round_id"]:
                raise EnrollmentProtocolError("invalid_response")

            async def submit_saved_proof(challenge: dict[str, Any]) -> None:
                proof_payload = validate_phase_envelope(
                    decrypt_enrollment_envelope(envelope_key, record["proof_envelope"]),
                    offer_id=offer_id,
                    round_id=record["round_id"],
                    phase="proof",
                    destination_key=record["destination_key"],
                )
                _require_shape(
                    proof_payload,
                    {"v", "offer_id", "round_id", "phase", "destination_key", "proof_token"},
                )
                verify_pairing_proof(
                    challenge["pairing_challenge"],
                    proof_payload["proof_token"],
                    owner_public_key=owner_key,
                )
                stored = await transport.post_signed_retry(
                    signing_key,
                    f"/relay/v1/enrollment/offers/{offer_id}/proof",
                    offer_id,
                    {
                        "destination_key": record["destination_key"],
                        "round_id": record["round_id"],
                        "envelope": record["proof_envelope"],
                    },
                )
                _require_shape(stored, {"status", "receipt"})
                if stored["status"] != "stored" or stored["receipt"] != record["round_id"]:
                    raise EnrollmentProtocolError("invalid_response")
                _store_destination_recovery(journal, record, "proof_stored")

            if record["challenge_envelope"] is not None and record["proof_envelope"] is None:
                challenge = _decode_destination_challenge(record, envelope_key, owner_key)
                scope = _provisioning_scope_from_payload(
                    challenge["provisioning_scope"],
                    enrollment_id=record["round_id"],
                    workspace_id=record["workspace_id"],
                )
                pairing_proof = prove_pairing(
                    challenge["pairing_challenge"],
                    signing_key,
                    owner_public_key=owner_key,
                )
                proof_envelope = encrypt_enrollment_envelope(
                    envelope_key,
                    {
                        "v": 1,
                        "offer_id": offer_id,
                        "round_id": record["round_id"],
                        "phase": "proof",
                        "destination_key": record["destination_key"],
                        "proof_token": pairing_proof.token,
                    },
                )
                _store_destination_recovery(
                    journal,
                    record,
                    "proof_ready",
                    proof_envelope=proof_envelope,
                )
            if record["proof_envelope"] is not None and record["status"] == "proof_ready":
                challenge = _decode_destination_challenge(record, envelope_key, owner_key)
                scope = _provisioning_scope_from_payload(
                    challenge["provisioning_scope"],
                    enrollment_id=record["round_id"],
                    workspace_id=record["workspace_id"],
                )
                await submit_saved_proof(challenge)

            reply_path = f"/relay/v1/enrollment/offers/{offer_id}/reply/poll"
            deadline = min(record["created_at"] + 600, int(time.time()) + 600)
            poll_delay = 5.0
            while int(time.time()) < deadline:
                await asyncio.sleep(poll_delay)
                if record["expires_at"] is not None and int(time.time()) >= record["expires_at"]:
                    journal.delete(offer_id)
                    raise EnrollmentProtocolError("unavailable")
                try:
                    polled = await transport.post_signed(
                        signing_key,
                        reply_path,
                        offer_id,
                        {"destination_key": record["destination_key"]},
                    )
                except EnrollmentProtocolError as exc:
                    if exc.code not in {"rate_limited", "transport"}:
                        raise
                    poll_delay = (
                        min(exc.retry_after_seconds, 120.0)
                        if exc.retry_after_seconds is not None
                        else min(poll_delay + 5.0, 20.0)
                    )
                    continue
                if polled.get("status") == "pending":
                    if set(polled) != {"status"}:
                        raise EnrollmentProtocolError("invalid_response")
                    poll_delay = min(poll_delay + 5.0, 20.0)
                    continue
                _require_shape(polled, {"status", "phase", "round_id", "envelope"})
                if polled["status"] != "ready" or polled["round_id"] != record["round_id"]:
                    raise EnrollmentProtocolError("invalid_response")
                if polled["phase"] == "challenge":
                    if record["status"] == "proof_stored":
                        poll_delay = min(poll_delay + 5.0, 20.0)
                        continue
                    if record["challenge_envelope"] is not None and record["challenge_envelope"] != polled["envelope"]:
                        raise EnrollmentProtocolError("conflict")
                    challenge_envelope = record["challenge_envelope"] or polled["envelope"]
                    if record["challenge_envelope"] is None:
                        raw_challenge = validate_phase_envelope(
                            decrypt_enrollment_envelope(envelope_key, challenge_envelope),
                            offer_id=offer_id,
                            round_id=record["round_id"],
                            phase="challenge",
                            destination_key=record["destination_key"],
                        )
                        _require_shape(
                            raw_challenge,
                            {
                                "v", "offer_id", "round_id", "phase", "destination_key",
                                "owner_key", "issuer_relay_key", "origin", "expires_at",
                                "pairing_challenge", "provisioning_scope", "owner_signature",
                            },
                        )
                        claimed_owner = raw_challenge.get("owner_key")
                        if (
                            not isinstance(claimed_owner, str)
                            or not re.fullmatch(r"[0-9a-f]{64}", claimed_owner)
                            or (record["owner_key"] is not None and claimed_owner != record["owner_key"])
                            or raw_challenge.get("origin") != record["origin"]
                            or not isinstance(raw_challenge.get("issuer_relay_key"), str)
                            or not re.fullmatch(r"[0-9a-f]{64}", raw_challenge["issuer_relay_key"])
                        ):
                            raise EnrollmentProtocolError("invalid_response")
                        # Only the agent that created this code can encrypt its
                        # challenge, so the code authenticates the issuer key.
                        owner_key = bytes.fromhex(claimed_owner)
                        # The signed challenge is persisted only after its owner
                        # signature and expiry have been checked below.
                        verify_enrollment_payload(owner_key, raw_challenge)
                        record["owner_key"] = claimed_owner
                        record["issuer_relay_key"] = raw_challenge["issuer_relay_key"]
                        expiry = raw_challenge["expires_at"]
                        if (
                            isinstance(expiry, bool)
                            or not isinstance(expiry, int)
                            or expiry <= int(time.time())
                            or expiry > int(time.time()) + 900
                        ):
                            raise EnrollmentProtocolError("invalid_response")
                        _store_destination_recovery(
                            journal,
                            record,
                            "challenge_saved",
                            challenge_envelope=challenge_envelope,
                            expires_at=expiry,
                        )
                    challenge = _decode_destination_challenge(record, envelope_key, owner_key)
                    scope = _provisioning_scope_from_payload(
                        challenge["provisioning_scope"],
                        enrollment_id=record["round_id"],
                        workspace_id=record["workspace_id"],
                    )
                    if record["proof_envelope"] is None:
                        pairing_proof = prove_pairing(
                            challenge["pairing_challenge"],
                            signing_key,
                            owner_public_key=owner_key,
                        )
                        proof_envelope = encrypt_enrollment_envelope(
                            envelope_key,
                            {
                                "v": 1,
                                "offer_id": offer_id,
                                "round_id": record["round_id"],
                                "phase": "proof",
                                "destination_key": record["destination_key"],
                                "proof_token": pairing_proof.token,
                            },
                        )
                        _store_destination_recovery(
                            journal,
                            record,
                            "proof_ready",
                            proof_envelope=proof_envelope,
                        )
                    await submit_saved_proof(challenge)
                    poll_delay = 5.0
                    continue
                if polled["phase"] != "decision" or record["status"] != "proof_stored":
                    raise EnrollmentProtocolError("invalid_response")
                decision_envelope = polled["envelope"]
                if record["decision_envelope"] is not None and record["decision_envelope"] != decision_envelope:
                    raise EnrollmentProtocolError("conflict")
                _store_destination_recovery(
                    journal,
                    record,
                    "decision_saved",
                    decision_envelope=decision_envelope,
                )
                break
            if record["decision_envelope"] is None:
                raise EnrollmentProtocolError("unavailable")
            decision, invite, scope = _decode_destination_decision(
                record,
                envelope_key,
                owner_key,
                require_unexpired_challenge=True,
            )
            if decision["status"] == "rejected":
                journal.delete(offer_id)
                return {"status": "rejected"}
        else:
            if decision is None:
                raise EnrollmentProtocolError("invalid_response")

        return await _finish_destination_enrollment(
            commands,
            discovery,
            ca,
            private_cidrs,
            owner_key,
            record,
            decision,
            invite,
            scope,
            envelope_key,
            journal,
            transport,
        )


async def enroll_device(commands, domain: str, private_code: str) -> dict[str, str]:
    """Complete or resume one destination-side enrollment."""
    code = verifier = envelope_key = None
    offer_id = None
    claimed = False
    drive_started = False
    journal = None
    record = None
    client = commands.client
    try:
        code = parse_enrollment_code(private_code)
        offer_id = code.offer_id
        if not _claim_destination_recovery(client, offer_id):
            raise EnrollmentProtocolError("unavailable")
        claimed = True
        verifier = derive_enrollment_code_verifier(code)
        envelope_key = derive_enrollment_envelope_key(code)
        journal = _destination_recovery_journal(client)
        record = journal.get(offer_id)
        if record is not None:
            record = _validate_destination_recovery_record(record, offer_id)
            if (
                record["domain"] != domain
                or record["destination_key"] != client.public_key
                or record["workspace_id"] != client.state.workspace_id
                or not hmac.compare_digest(record["code_verifier"], verifier.for_protocol())
                or _decode_recovery_value(record["envelope_key"], maximum=64)
                != envelope_key.for_envelope_encryption()
            ):
                raise EnrollmentProtocolError("conflict")
        else:
            if not _destination_state_empty(client):
                raise EnrollmentProtocolError("conflict")
            discovery, _ca, _private_cidrs = await _discover_destination(commands, domain)
            round_id = secrets.token_hex(16)
            request_envelope = encrypt_enrollment_envelope(
                envelope_key,
                {
                    "v": 1,
                    "offer_id": offer_id,
                    "round_id": round_id,
                    "phase": "request",
                    "destination_key": client.public_key,
                    "workspace_id": client.state.workspace_id,
                },
            )
            record = {
                "version": 1,
                "offer_id": offer_id,
                "status": "request_ready",
                "created_at": int(time.time()),
                "domain": domain,
                "origin": discovery.origin,
                "owner_key": None,
                "issuer_relay_key": None,
                "destination_key": client.public_key,
                "workspace_id": client.state.workspace_id,
                "round_id": round_id,
                "code_verifier": verifier.for_protocol(),
                "envelope_key": _encode_recovery_value(envelope_key.for_envelope_encryption()),
                "request_envelope": request_envelope,
                "challenge_envelope": None,
                "proof_envelope": None,
                "decision_envelope": None,
                "expires_at": None,
                "install_status": None,
                "revision": None,
                "digest": None,
                "ack_envelope": None,
                "ack_receipt": None,
                "retry_attempts": 0,
                "retry_after": 0,
                "last_error_code": None,
            }
            _validate_destination_recovery_record(record, offer_id)
            # The stable round and exact request are durable before the first
            # network write, so the relay can return this same decision later.
            journal.put(offer_id, record)
        drive_started = True
        return await _drive_destination_enrollment(commands, record, journal, envelope_key)
    except asyncio.CancelledError:
        raise
    except EnrollmentProtocolError as exc:
        if drive_started and journal is not None and isinstance(record, dict):
            try:
                _schedule_destination_retry(journal, record, exc.code)
            except Exception:
                pass
        return {"error": exc.code}
    except Exception:
        if drive_started and journal is not None and isinstance(record, dict):
            try:
                _schedule_destination_retry(journal, record, "transport")
            except Exception:
                pass
        # Keep raw input, credentials, paths, and transport exceptions out of UI.
        return {"error": "transport"}
    finally:
        if claimed and offer_id is not None:
            _release_destination_recovery(client, offer_id)
        for secret in (envelope_key, verifier, code):
            if secret is not None:
                secret.wipe()


def _require_shape(value: dict[str, Any], fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise EnrollmentProtocolError("invalid_response")


def _device_key_fingerprint(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", public_key_hex):
        raise EnrollmentProtocolError("invalid_response")
    return hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + bytes.fromhex(public_key_hex)
    ).hexdigest()


@dataclass(slots=True)
class _ActiveEnrollmentOffer:
    offer_id: str
    expires_at: int
    human_action_id: str
    session_id: str
    issuer_key: str
    room_capability: str
    origin: str
    issuer_principal_id: str
    network_ids: tuple[str, ...]
    profile: str | None
    envelope_key: EnrollmentEnvelopeKey
    owner_signing_key: SigningKey
    discovery: Any
    ca: str
    private_cidrs: tuple[str, ...]
    domain: str = ""
    relay_session_id: str | None = None
    recovery: dict[str, Any] | None = field(default=None, repr=False)
    recovery_mode: bool = False
    provisioning_plan: _ProvisioningPlan | None = field(default=None, repr=False)
    credential_categories: tuple[str, ...] = ("conversation:send",)

    @property
    def active_session_id(self) -> str:
        return self.relay_session_id or self.session_id


@dataclass(frozen=True, slots=True)
class EnrollmentApprovalRequest:
    """Secret-free request metadata for a trusted issuer decision."""

    enrollment_id: str
    device_key_fingerprint: str
    issuer: str
    network_ids: tuple[str, ...]
    configuration_profile: str | None
    credential_categories: tuple[str, ...]
    workspace_id: str | None
    expires_at: int
    remaining_new_devices: int
    decision_available: bool
    profile_summary: str | None = None


@dataclass(slots=True)
class _LiveEnrollmentRequest:
    offer: _ActiveEnrollmentOffer = field(repr=False)
    destination_key: str = field(repr=False)
    round_id: str
    workspace_id: str
    challenge_token: str = field(repr=False)
    proof_token: str = field(repr=False)
    decision_event: asyncio.Event = field(repr=False)
    decision: str | None = None


class EnrollmentIssuer:
    """Issuer-side worker for one-device, human-authorized offers."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._tasks: dict[str, asyncio.Task] = {}
        self._offers: dict[str, _ActiveEnrollmentOffer] = {}
        self._live_requests: dict[str, _LiveEnrollmentRequest] = {}
        self._approved_offer_ids: set[str] = set()
        self._recovery_task: asyncio.Task | None = None
        self._destination_tasks: dict[str, asyncio.Task] = {}

    def _recovery_journal(self, client) -> EnrollmentRecoveryJournal:
        return EnrollmentRecoveryJournal(
            self.bridge.owner.state_dir / "enrollment-recovery.json",
            derive_enrollment_recovery_key(client._store.key.encode()),
        )

    def destination_recovery_status(self) -> dict[str, Any] | None:
        """Return counts and fixed error codes, never enrollment secrets or IDs."""
        if self.bridge.commands is None:
            return None
        try:
            client = self.bridge.commands.client
            destination_journal = _destination_recovery_journal(client)
            destination_records = []
            for raw in destination_journal.records():
                offer_id = raw.get("offer_id") if isinstance(raw, dict) else None
                if not isinstance(offer_id, str):
                    continue
                try:
                    destination_records.append(
                        _validate_destination_recovery_record(raw, offer_id)
                    )
                except EnrollmentProtocolError:
                    continue
            issuer_records = []
            for raw in self._recovery_journal(client).records():
                if not isinstance(raw, dict):
                    continue
                offer_id = raw.get("offer_id")
                status = raw.get("status")
                if (
                    isinstance(offer_id, str)
                    and re.fullmatch(r"[0-9a-f]{32}", offer_id)
                    and isinstance(status, str)
                    and status in {"approval_intent", "approved"}
                ):
                    issuer_records.append(raw)
        except Exception:
            return None
        destination_pending = [
            record
            for record in destination_records
            if record["status"] != "attached"
        ]
        pending = destination_pending + issuer_records
        now = int(time.time())
        retry_delays = [
            max(0, record.get("retry_after", 0) - now)
            for record in pending
            if isinstance(record.get("retry_after", 0), int)
            and not isinstance(record.get("retry_after", 0), bool)
            and record.get("retry_after", 0) > now
        ]
        allowed = _ALLOWED_ERRORS | {"transport", "invalid_response"}
        errors = [
            record["last_error_code"]
            for record in pending
            if isinstance(record.get("last_error_code"), str)
            and record.get("last_error_code") in allowed
        ]
        active = sum(
            1
            for offer_id, task in self._destination_tasks.items()
            if not task.done()
            and any(
                record["offer_id"] == offer_id for record in destination_pending
            )
        )
        active += sum(
            1
            for offer_id, task in self._tasks.items()
            if not task.done()
            and any(record["offer_id"] == offer_id for record in issuer_records)
        )
        return {
            "pending": len(pending),
            "active": active,
            "destination_pending": len(destination_pending),
            "issuer_pending": len(issuer_records),
            "last_error_code": errors[-1] if errors else None,
            "retry_in_seconds": min(retry_delays) if retry_delays else None,
        }

    def _recovery_record(
        self, live: _LiveEnrollmentRequest, client
    ) -> dict[str, Any]:
        offer = live.offer
        discovery_authority = getattr(offer.discovery, "requested_authority", None)
        recovery_domain = (
            offer.domain
            or discovery_authority
            or urlsplit(offer.origin).hostname
            or ""
        )
        return {
            "version": 1,
            "offer_id": offer.offer_id,
            "status": "approval_intent",
            "expires_at": offer.expires_at,
            "human_action_id": offer.human_action_id,
            "session_id": offer.session_id,
            "issuer_key": offer.issuer_key,
            "room_capability": offer.room_capability,
            "origin": offer.origin,
            "domain": recovery_domain,
            "issuer_principal_id": offer.issuer_principal_id,
            "network_ids": list(offer.network_ids),
            "profile": offer.profile,
            "credential_categories": list(offer.credential_categories),
            "owner_public_key": bytes(offer.owner_signing_key.verify_key).hex(),
            "issuer_workspace_id": client.state.workspace_id,
            "envelope_key": _encode_recovery_value(
                offer.envelope_key.for_envelope_encryption()
            ),
            "request": {
                "destination_key": live.destination_key,
                "round_id": live.round_id,
                "workspace_id": live.workspace_id,
                "challenge_token": live.challenge_token,
                "proof_token": live.proof_token,
            },
            "provisioning_plan": _provisioning_plan_to_recovery(
                offer.provisioning_plan
            ),
            "provisioning_content": None,
            "issued_credential": None,
            "expected_install_digest": None,
            "decision_envelope": None,
            "retry_attempts": 0,
            "retry_after": 0,
            "last_error_code": None,
        }

    def start_recovery(self) -> None:
        """Resume only durable, exact-scope approvals after the relay reconnects."""
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task = asyncio.create_task(
                self._recovery_loop(), name="kollab-enrollment-recovery"
            )

    async def _recovery_loop(self) -> None:
        while not self.bridge._closed:
            try:
                await self._restore_pending_destination_enrollments()
                await self._restore_approved_offers()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Recovery state contains credentials; expose neither its contents
                # nor raw filesystem/protocol failures through the Hub.
                pass
            await asyncio.sleep(5)

    async def _restore_pending_destination_enrollments(self) -> int:
        bridge = self.bridge
        if bridge.commands is None or bridge._closed:
            return 0
        client = bridge.commands.client
        journal = _destination_recovery_journal(client)
        started = 0
        now = int(time.time())
        for raw_record in journal.records():
            offer_id = raw_record.get("offer_id") if isinstance(raw_record, dict) else None
            if not isinstance(offer_id, str):
                continue
            try:
                record = _validate_destination_recovery_record(raw_record, offer_id)
            except EnrollmentProtocolError:
                continue
            age = now - record["created_at"]
            rank = _DESTINATION_RECOVERY_STATUSES[record["status"]]
            if (
                age > _DESTINATION_RECOVERY_MAX_AGE
                or record["status"] == "attached"
                or (
                    record["expires_at"] is not None
                    and now >= record["expires_at"]
                    and rank < _DESTINATION_RECOVERY_STATUSES["ack_ready"]
                )
            ):
                journal.delete(offer_id)
                continue
            existing = self._destination_tasks.get(offer_id)
            if existing is not None and not existing.done():
                continue
            if existing is not None:
                self._destination_tasks.pop(offer_id, None)
            if record["retry_after"] > now:
                continue
            if not _claim_destination_recovery(client, offer_id):
                continue
            task = asyncio.create_task(
                self._resume_destination_record(client, journal, record),
                name="kollab-destination-enrollment-recovery",
            )
            self._destination_tasks[offer_id] = task
            started += 1
        return started

    async def _resume_destination_record(
        self,
        client,
        journal: EnrollmentRecoveryJournal,
        record: dict[str, Any],
    ) -> None:
        key = None
        try:
            key_bytes = _decode_recovery_value(record["envelope_key"], maximum=64)
            key = EnrollmentEnvelopeKey(record["offer_id"], key_bytes)
            await _drive_destination_enrollment(
                self.bridge.commands, record, journal, key
            )
        except asyncio.CancelledError:
            raise
        except EnrollmentProtocolError as exc:
            try:
                _schedule_destination_retry(journal, record, exc.code)
            except Exception:
                pass
        except Exception:
            try:
                _schedule_destination_retry(journal, record, "transport")
            except Exception:
                pass
        finally:
            if key is not None:
                key.wipe()
            _release_destination_recovery(client, record["offer_id"])
            task = self._destination_tasks.get(record["offer_id"])
            if task is asyncio.current_task():
                self._destination_tasks.pop(record["offer_id"], None)

    async def _restore_approved_offers(self) -> int:
        bridge = self.bridge
        if bridge.commands is None or bridge._closed:
            return 0
        client = bridge.commands.client
        active_session = client._session_id
        if (
            not active_session
            or client.status().get("state") != "online"
            or not client.state.origin
            or client.state.origin != client.status().get("origin")
        ):
            return 0
        delegation_store = EnrollmentDelegationStore(
            bridge.owner.state_dir / "enrollment-delegations.json"
        )
        try:
            self._reconcile_completed_installs(delegation_store, client)
        except Exception:
            # Continue into per-row recovery. Receipt-bearing rows will be kept
            # as ambiguous and receive a bounded fixed-code retry below.
            pass
        journal = self._recovery_journal(client)
        started = 0
        now = int(time.time())
        for recovery in journal.records():
            offer_id = recovery.get("offer_id") if isinstance(recovery, dict) else None
            if not isinstance(offer_id, str) or not re.fullmatch(r"[0-9a-f]{32}", offer_id):
                continue
            existing = self._tasks.get(offer_id)
            if existing is not None and not existing.done():
                continue
            retry_after = recovery.get("retry_after", 0)
            if (
                isinstance(retry_after, int)
                and not isinstance(retry_after, bool)
                and retry_after > now
            ):
                if retry_after <= now + _DESTINATION_RETRY_MAX_SECONDS:
                    continue
                try:
                    _schedule_issuer_recovery_retry(
                        journal, recovery, "invalid_response", now=now
                    )
                except Exception:
                    pass
                continue
            try:
                offer = await self._restore_recovery_offer(recovery, client)
            except asyncio.CancelledError:
                raise
            except EnrollmentProtocolError as exc:
                try:
                    _schedule_issuer_recovery_retry(
                        journal, recovery, exc.code, now=now
                    )
                except Exception:
                    pass
                continue
            except Exception:
                try:
                    _schedule_issuer_recovery_retry(
                        journal, recovery, "transport", now=now
                    )
                except Exception:
                    pass
                continue
            if offer is None:
                continue
            self._approved_offer_ids.add(offer.offer_id)
            self._offers[offer.offer_id] = offer
            self._tasks[offer.offer_id] = asyncio.create_task(
                self._serve_offer(offer), name="kollab-relay-enrollment-recovery"
            )
            started += 1
        return started

    async def _restore_recovery_offer(
        self, recovery: dict[str, Any], client
    ) -> _ActiveEnrollmentOffer | None:
        required = {
            "version",
            "offer_id",
            "status",
            "expires_at",
            "human_action_id",
            "session_id",
            "issuer_key",
            "room_capability",
            "origin",
            "domain",
            "issuer_principal_id",
            "network_ids",
            "profile",
            "credential_categories",
            "owner_public_key",
            "issuer_workspace_id",
            "envelope_key",
            "request",
            "provisioning_plan",
            "provisioning_content",
            "issued_credential",
            "expected_install_digest",
            "decision_envelope",
        }
        retry_fields = {"retry_attempts", "retry_after", "last_error_code"}
        if not isinstance(recovery, dict) or frozenset(recovery) not in {
            frozenset(required),
            frozenset(required | retry_fields),
        }:
            raise EnrollmentProtocolError("invalid_response")
        # Existing encrypted rows predate retry metadata. Normalize those rows
        # in memory and persist the new fields on the next state transition.
        recovery = dict(recovery)
        for key, value in (
            ("retry_attempts", 0),
            ("retry_after", 0),
            ("last_error_code", None),
        ):
            recovery.setdefault(key, value)
        now = int(time.time())
        offer_id = recovery["offer_id"]
        allowed_errors = _ALLOWED_ERRORS | {"transport", "invalid_response"}
        if (
            isinstance(recovery["version"], bool)
            or recovery["version"] != 1
            or not isinstance(recovery["status"], str)
            or recovery["status"] not in {"approval_intent", "approved"}
            or not isinstance(offer_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", offer_id)
            or isinstance(recovery["expires_at"], bool)
            or not isinstance(recovery["expires_at"], int)
            or isinstance(recovery["retry_attempts"], bool)
            or not isinstance(recovery["retry_attempts"], int)
            or not 0 <= recovery["retry_attempts"] <= _DESTINATION_RETRY_MAX_ATTEMPTS
            or isinstance(recovery["retry_after"], bool)
            or not isinstance(recovery["retry_after"], int)
            or recovery["retry_after"] < 0
            or (
                recovery["last_error_code"] is not None
                and (
                    not isinstance(recovery["last_error_code"], str)
                    or recovery["last_error_code"] not in allowed_errors
                )
            )
        ):
            raise EnrollmentProtocolError("invalid_response")

        bridge = self.bridge
        identity = bridge.identity
        client = bridge.commands.client
        request = recovery["request"]
        if not isinstance(request, dict) or set(request) != {
            "destination_key",
            "round_id",
            "workspace_id",
            "challenge_token",
            "proof_token",
        }:
            raise EnrollmentProtocolError("invalid_response")
        destination_key = request["destination_key"]
        if (
            not isinstance(destination_key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", destination_key)
            or not isinstance(request["round_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", request["round_id"])
            or not isinstance(request["workspace_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", request["workspace_id"])
            or not isinstance(request["challenge_token"], str)
            or not request["challenge_token"]
            or len(request["challenge_token"]) > 8192
            or not isinstance(request["proof_token"], str)
            or not request["proof_token"]
            or len(request["proof_token"]) > 8192
            or not isinstance(recovery["human_action_id"], str)
            or not recovery["human_action_id"]
            or len(recovery["human_action_id"]) > 256
            or not isinstance(recovery["session_id"], str)
            or not recovery["session_id"]
            or len(recovery["session_id"]) > 256
            or not isinstance(recovery["issuer_key"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", recovery["issuer_key"])
            or not isinstance(recovery["room_capability"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", recovery["room_capability"])
            or not isinstance(recovery["origin"], str)
            or not isinstance(recovery["domain"], str)
            or not isinstance(recovery["issuer_principal_id"], str)
            or not isinstance(recovery["issuer_workspace_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", recovery["issuer_workspace_id"])
            or not isinstance(recovery["owner_public_key"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", recovery["owner_public_key"])
            or not isinstance(recovery["network_ids"], list)
            or any(not isinstance(value, str) for value in recovery["network_ids"])
            or not isinstance(recovery["credential_categories"], list)
            or any(
                not isinstance(value, str)
                for value in recovery["credential_categories"]
            )
            or (
                recovery["profile"] is not None
                and not isinstance(recovery["profile"], str)
            )
            or (
                recovery["issued_credential"] is not None
                and (
                    not isinstance(recovery["issued_credential"], str)
                    or not recovery["issued_credential"]
                    or len(recovery["issued_credential"]) > 8192
                )
            )
            or (
                recovery["expected_install_digest"] is not None
                and (
                    not isinstance(recovery["expected_install_digest"], str)
                    or not re.fullmatch(
                        r"[0-9a-f]{64}", recovery["expected_install_digest"]
                    )
                )
            )
            or (
                recovery["decision_envelope"] is not None
                and (
                    not isinstance(recovery["decision_envelope"], str)
                    or len(recovery["decision_envelope"]) > _MAX_ENVELOPE_BYTES * 2
                )
            )
        ):
            raise EnrollmentProtocolError("invalid_response")

        if (
            recovery["issuer_key"] != client.public_key
            or recovery["origin"] != client.state.origin
            or recovery["room_capability"] != client.state.room
            or recovery["issuer_workspace_id"] != client.state.workspace_id
        ):
            raise EnrollmentProtocolError("conflict")

        manager = getattr(getattr(bridge, "plugin", None), "_dns_identity", None)
        designation = getattr(identity, "identity", None)
        if (
            manager is None
            or not isinstance(designation, str)
            or getattr(identity, "is_coordinator", False) is not True
        ):
            raise EnrollmentProtocolError("conflict")
        try:
            owner_private_hex, owner_public_hex = manager.get_or_create_keypair(
                designation
            )
            room_fingerprint = hashlib.sha256(
                b"kollab-relay-enrollment-recovery-room-v1\0"
                + bytes.fromhex(client.state.room)
            ).hexdigest()
        except Exception as exc:
            raise EnrollmentProtocolError("unavailable") from exc
        if owner_public_hex != recovery["owner_public_key"]:
            raise EnrollmentProtocolError("conflict")

        store = EnrollmentDelegationStore(
            bridge.owner.state_dir / "enrollment-delegations.json"
        )
        try:
            state = store.get_recovery_state(
                request["round_id"],
                human_action_id=recovery["human_action_id"],
            )
        except Exception as exc:
            raise EnrollmentProtocolError("unavailable") from exc
        if state is None:
            raise EnrollmentProtocolError("conflict")
        record = state.request
        delegation = state.delegation
        if (
            record.enrollment_id != request["round_id"]
            or record.human_action_id != recovery["human_action_id"]
            or record.authorized_agent_id != str(identity.agent_id)
            or record.authorized_session_id != recovery["session_id"]
            or delegation.human_action_id != recovery["human_action_id"]
            or delegation.authorized_agent_id != str(identity.agent_id)
            or delegation.authorized_session_id != recovery["session_id"]
            or record.issuer != recovery["issuer_principal_id"]
            or record.device_key_fingerprint
            != _device_key_fingerprint(destination_key)
            or record.workspace_id != request["workspace_id"]
            or record.expires_at != recovery["expires_at"]
            or record.network_ids != tuple(recovery["network_ids"])
            or record.configuration_profile != recovery["profile"]
            or record.credential_categories
            != tuple(recovery["credential_categories"])
        ):
            raise EnrollmentProtocolError("conflict")

        has_receipt = any(
            value is not None
            for value in (
                record.installation_status,
                record.installation_revision,
                record.installation_digest,
                record.installation_signature,
            )
        )
        journal = self._recovery_journal(client)
        if record.peer_approved:
            # A completed peer is never revoked by enrollment cleanup.
            journal.delete(offer_id)
            return None

        # A stored installation receipt is evidence that the destination may
        # already be using the credential. Reconciliation runs before this
        # method; if it could not prove peer approval, preserve the row and
        # retry with a fixed conflict status instead of revoking it.
        if has_receipt:
            raise EnrollmentProtocolError("conflict")

        intent_present = any(
            value is not None
            for value in (
                record.offer_id,
                record.destination_public_key,
                record.issuer_relay_key,
                record.issuer_origin,
                record.room_fingerprint,
                record.owner_public_key,
                record.delivery_signature,
            )
        )
        if intent_present and (
            record.offer_id != offer_id
            or record.destination_public_key != destination_key
            or record.issuer_relay_key != client.public_key
            or record.issuer_origin != client.state.origin
            or record.room_fingerprint != room_fingerprint
            or record.issuer_workspace_id != client.state.workspace_id
            or record.owner_public_key != owner_public_hex
            or record.delivery_signature is None
            or (
                recovery["expected_install_digest"] is not None
                and recovery["expected_install_digest"]
                != record.expected_install_digest
            )
        ):
            raise EnrollmentProtocolError("conflict")

        terminal = (
            recovery["expires_at"] <= now
            or record.expires_at <= now
            or delegation.revoked
            or delegation.expires_at <= now
        )
        issued_credential = recovery["issued_credential"]
        if terminal:
            if issued_credential is None:
                # Before approval or delivery intent, no credential could have
                # been issued. If approval was durable but the exact token was
                # lost, retain the row for operator recovery instead.
                if (
                    record.status != "approved"
                    and not intent_present
                    and record.installation_status is None
                ):
                    journal.delete(offer_id)
                    return None
                raise EnrollmentProtocolError("conflict")
            try:
                directory = PrivateDirectory(
                    client.state_dir / "private-directory.json",
                    owner_public_key=bytes.fromhex(owner_public_hex),
                    workspace_id=client.state.workspace_id,
                )
                directory.revoke_issued_credential(
                    issued_credential,
                    SigningKey(bytes.fromhex(owner_private_hex)),
                    expected_device_public_key=bytes.fromhex(destination_key),
                    now=now,
                )
            except Exception as exc:
                raise EnrollmentProtocolError("conflict") from exc
            journal.delete(offer_id)
            return None

        if record.status != "approved":
            # A recovery intent is never a substitute for durable human consent.
            raise EnrollmentProtocolError("conflict")

        discovery, ca, private_cidrs, is_card = await bridge.commands._discover(recovery["domain"])
        if (
            is_card
            or discovery.origin != recovery["origin"]
            or recovery["issuer_principal_id"] != public_key_id(bytes.fromhex(recovery["owner_public_key"]))
            or bridge.commands._relay_url(discovery) is None
        ):
            raise EnrollmentProtocolError("conflict")
        key_bytes = _decode_recovery_value(recovery["envelope_key"], maximum=64)
        if len(key_bytes) != 32:
            raise EnrollmentProtocolError("invalid_response")
        plan = _provisioning_plan_from_recovery(recovery["provisioning_plan"])
        offer = _ActiveEnrollmentOffer(
            offer_id=offer_id,
            expires_at=recovery["expires_at"],
            human_action_id=recovery["human_action_id"],
            session_id=recovery["session_id"],
            issuer_key=recovery["issuer_key"],
            room_capability=recovery["room_capability"],
            origin=recovery["origin"],
            issuer_principal_id=recovery["issuer_principal_id"],
            network_ids=tuple(recovery["network_ids"]),
            profile=recovery["profile"],
            envelope_key=EnrollmentEnvelopeKey(offer_id, key_bytes),
            owner_signing_key=SigningKey(bytes.fromhex(owner_private_hex)),
            discovery=discovery,
            ca=ca,
            private_cidrs=private_cidrs,
            domain=recovery["domain"],
            relay_session_id=client._session_id,
            recovery=recovery,
            recovery_mode=True,
            provisioning_plan=plan,
            credential_categories=tuple(recovery["credential_categories"]),
        )
        return offer

    async def _make_provisioning_plan(self, offer_id: str) -> _ProvisioningPlan | None:
        manager = _profile_manager(getattr(self.bridge, "plugin", None))
        if manager is None:
            return None
        try:
            profile = manager.get_active_profile()
            preferences = _profile_preferences(profile)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}", preferences.model):
                return None
            category = _profile_credential_category(profile)
            if category is None:
                return None
            destination_profile_name = "kollab-" + offer_id[:16]
            # Offer creation records only allowlisted preferences and scope.
            # Read the credential itself only after a human accepts the request.
            display = f"{preferences.name} ({preferences.provider}/{preferences.model}) to {destination_profile_name}"
            return _ProvisioningPlan(
                source_profile_name=preferences.name,
                destination_profile_name=destination_profile_name,
                profile_preferences=preferences,
                credential_category=category,
                display=display,
            )
        except Exception:
            # Unsupported or unavailable local profiles produce network-only
            # enrollment; raw settings and credential errors stay local.
            return None

    async def _accepted_profile_payload(self, offer: _ActiveEnrollmentOffer):
        plan = offer.provisioning_plan
        if plan is None:
            return None, ()
        manager = _profile_manager(getattr(self.bridge, "plugin", None))
        if manager is None:
            raise EnrollmentProtocolError("unavailable")
        profile = manager.get_profile(plan.source_profile_name)
        if profile is None or _profile_preferences(profile) != plan.profile_preferences:
            raise EnrollmentProtocolError("conflict")
        credential = await _profile_credential(
            profile,
            category=plan.credential_category,
            destination_profile_name=plan.destination_profile_name,
        )
        return (
            replace(plan.profile_preferences, name=plan.destination_profile_name),
            (credential,),
        )

    def pending_requests(self) -> tuple[EnrollmentApprovalRequest, ...]:
        """Return authorized pending requests without codes, proofs, or tokens."""
        bridge = self.bridge
        if bridge.commands is None:
            raise EnrollmentProtocolError("unavailable")
        client = bridge.commands.client
        identity = bridge.identity
        session_id = client._session_id
        if (
            not session_id
            or client.status().get("state") != "online"
            or not client.state.origin
            or client.state.origin != client.status().get("origin")
        ):
            raise EnrollmentProtocolError("unavailable")
        store = EnrollmentDelegationStore(bridge.owner.state_dir / "enrollment-delegations.json")
        try:
            self._reconcile_completed_installs(store, client)
            records = store.pending_requests(agent_id=str(identity.agent_id), session_id=session_id)
        except Exception as exc:
            raise EnrollmentProtocolError("unavailable") from exc
        pending = []
        for record in records:
            live = self._matching_live_request(record, client, str(identity.agent_id), session_id)
            pending.append(
                EnrollmentApprovalRequest(
                    enrollment_id=record.enrollment_id,
                    device_key_fingerprint=record.device_key_fingerprint,
                    issuer=record.issuer,
                    network_ids=record.network_ids,
                    configuration_profile=record.configuration_profile,
                    credential_categories=record.credential_categories,
                    workspace_id=record.workspace_id,
                    expires_at=record.expires_at,
                    remaining_new_devices=record.remaining_new_devices,
                    decision_available=live is not None,
                    profile_summary=(
                        live.offer.provisioning_plan.display
                        if live is not None and live.offer.provisioning_plan is not None
                        else None
                    ),
                )
            )
        return tuple(pending)

    def _reconcile_completed_installs(
        self,
        store: EnrollmentDelegationStore,
        client,
    ) -> int:
        """Finish only journaled installs with exact owner and device signatures."""
        if (
            client.status().get("state") != "online"
            or not client.state.origin
            or client.state.origin != client.status().get("origin")
        ):
            return 0
        bridge = self.bridge
        manager = getattr(getattr(bridge, "plugin", None), "_dns_identity", None)
        designation = getattr(bridge.identity, "identity", None)
        if manager is None or not isinstance(designation, str):
            return 0
        try:
            _private_key, current_owner_key = manager.get_or_create_keypair(designation)
            issuer_workspace_id = client.state.workspace_id
            room_fingerprint = hashlib.sha256(
                b"kollab-relay-enrollment-recovery-room-v1\0" + bytes.fromhex(client.state.room)
            ).hexdigest()
        except Exception:
            return 0
        records = store.install_receipts_for_reconciliation(
            owner_public_key=current_owner_key,
            issuer_relay_key=client.public_key,
            issuer_origin=client.state.origin,
            room_fingerprint=room_fingerprint,
            issuer_workspace_id=issuer_workspace_id,
        )

        reconciled = 0
        for record in records:
            destination_key = record.destination_public_key
            if (
                destination_key is None
                or record.expected_install_digest is None
                or record.installation_digest != record.expected_install_digest
                or record.installation_signature is None
                or record.offer_id is None
                or record.owner_public_key != current_owner_key
                or record.issuer_relay_key != client.public_key
                or record.issuer_origin != client.state.origin
                or record.room_fingerprint != room_fingerprint
                or record.issuer_workspace_id != issuer_workspace_id
            ):
                continue
            receipt = {
                "v": 1,
                "offer_id": record.offer_id,
                "round_id": record.enrollment_id,
                "phase": "installation_ack",
                "destination_key": destination_key,
                "workspace_id": record.workspace_id,
                "status": record.installation_status,
                "revision": record.installation_revision,
                "digest": record.installation_digest,
                "device_signature": record.installation_signature,
            }
            if not verify_installation_receipt(destination_key, receipt):
                continue
            try:
                directory = PrivateDirectory(
                    client.state_dir / "private-directory.json",
                    owner_public_key=bytes.fromhex(current_owner_key),
                    workspace_id=client.state.workspace_id,
                )
                if not any(member.public_key.hex() == destination_key for member in directory.members()):
                    continue
                client.approve(destination_key)
                store.mark_peer_approved(
                    record.enrollment_id,
                    destination_public_key=destination_key,
                    owner_public_key=current_owner_key,
                    issuer_relay_key=client.public_key,
                    issuer_origin=client.state.origin,
                    room_fingerprint=room_fingerprint,
                    issuer_workspace_id=issuer_workspace_id,
                )
                reconciled += 1
            except Exception as exc:
                raise EnrollmentProtocolError("unavailable") from exc
        return reconciled

    def _matching_live_request(
        self,
        record: EnrollmentRequestRecord,
        client,
        agent_id: str,
        session_id: str,
    ) -> _LiveEnrollmentRequest | None:
        live = self._live_requests.get(record.enrollment_id)
        if live is None:
            return None
        offer = live.offer
        if (
            live.round_id != record.enrollment_id
            or offer.human_action_id != record.human_action_id
            or offer.session_id != session_id
            or offer.issuer_key != client.public_key
            or offer.room_capability != client.state.room
            or offer.origin != client.state.origin
            or offer.issuer_principal_id != record.issuer
            or offer.network_ids != record.network_ids
            or offer.profile != record.configuration_profile
            or record.workspace_id != live.workspace_id
            or record.authorized_agent_id != agent_id
            or record.authorized_session_id != session_id
            or record.credential_categories != offer.credential_categories
            or record.device_key_fingerprint != _device_key_fingerprint(live.destination_key)
            or record.expires_at != offer.expires_at
            or record.expires_at <= int(time.time())
        ):
            return None
        return live

    async def decide(self, enrollment_id: str, *, decision: str) -> dict[str, str]:
        """Accept or reject one proof-bound request under its active delegation."""
        if decision not in {"accept", "reject"}:
            raise EnrollmentProtocolError("invalid_request")
        bridge = self.bridge
        if bridge.commands is None or getattr(bridge, "_closed", False):
            raise EnrollmentProtocolError("unavailable")
        client = bridge.commands.client
        identity = bridge.identity
        session_id = client._session_id
        if (
            not session_id
            or client.status().get("state") != "online"
            or not client.state.origin
            or client.state.origin != client.status().get("origin")
        ):
            raise EnrollmentProtocolError("unavailable")
        store = EnrollmentDelegationStore(bridge.owner.state_dir / "enrollment-delegations.json")
        try:
            self._reconcile_completed_installs(store, client)
            record = store.get_enrollment_request(
                enrollment_id,
                agent_id=str(identity.agent_id),
                session_id=session_id,
            )
        except Exception as exc:
            raise EnrollmentProtocolError("unauthorized") from exc
        expected_status = "approved" if decision == "accept" else "rejected"
        if record.status in {"approved", "rejected"}:
            if record.status != expected_status:
                raise EnrollmentProtocolError("conflict")
            status = record.status
            if record.status == "approved":
                self.start_recovery()
                if record.peer_approved:
                    status = "approved"
                elif record.installation_status is not None:
                    status = "installed"
                else:
                    status = "accepted"
            return {"status": status, "receipt_id": record.enrollment_id}
        if record.status != "pending":
            raise EnrollmentProtocolError("unavailable")
        live = self._matching_live_request(record, client, str(identity.agent_id), session_id)
        if live is None:
            # The durable proof and request survive, but the in-memory code key
            # and mailbox worker do not. Never infer approval after restart.
            raise EnrollmentProtocolError("unavailable")
        if decision == "accept":
            journal = self._recovery_journal(client)
            recovery_record = self._recovery_record(live, client)
            try:
                # Write intent first. Recovery still requires the separately
                # durable approved status in EnrollmentDelegationStore.
                journal.put(live.offer.offer_id, recovery_record)
            except Exception as exc:
                raise EnrollmentProtocolError("unavailable") from exc
            try:
                store.consume(
                    record.human_action_id,
                    enrollment_id=record.enrollment_id,
                    device_key_fingerprint=record.device_key_fingerprint,
                    agent_id=str(identity.agent_id),
                    session_id=session_id,
                    issuer=record.issuer,
                    network_ids=record.network_ids,
                    configuration_profile=record.configuration_profile,
                    credential_categories=record.credential_categories,
                    workspace_id=record.workspace_id,
                )
            except Exception as exc:
                try:
                    journal.delete(live.offer.offer_id)
                except Exception:
                    pass
                raise EnrollmentProtocolError("unauthorized") from exc
            self._approved_offer_ids.add(live.offer.offer_id)
            recovery_record["status"] = "approved"
            live.offer.recovery = recovery_record
            try:
                # If this replacement fails, the prior intent remains enough
                # to recover after checking the durable approval ledger.
                journal.put(live.offer.offer_id, recovery_record)
            except Exception:
                pass
        else:
            try:
                store.reject_pending(
                    record.human_action_id,
                    enrollment_id=record.enrollment_id,
                    device_key_fingerprint=record.device_key_fingerprint,
                    agent_id=str(identity.agent_id),
                    session_id=session_id,
                    issuer=record.issuer,
                    network_ids=record.network_ids,
                    configuration_profile=record.configuration_profile,
                    credential_categories=record.credential_categories,
                    workspace_id=record.workspace_id,
                )
            except Exception as exc:
                raise EnrollmentProtocolError("unauthorized") from exc
        live.decision = expected_status
        live.decision_event.set()
        return {
            "status": "accepted" if decision == "accept" else "rejected",
            "receipt_id": record.enrollment_id,
        }

    async def create_offer(self, domain: str) -> dict[str, str]:
        """Create an expiring one-device offer and start its bounded worker."""
        bridge = self.bridge
        if bridge._turn.get() is not None:
            raise EnrollmentProtocolError("unauthorized")
        if bridge.commands is None:
            raise EnrollmentProtocolError("unavailable")
        commands = bridge.commands
        client = commands.client
        status = client.status()
        session_id = client._session_id
        if (
            status.get("state") != "online"
            or not session_id
            or not client.state.origin
            or client.state.origin != status.get("origin")
        ):
            raise EnrollmentProtocolError("unavailable")

        discovery, ca, private_cidrs, is_card = await commands._discover(domain)
        if is_card or commands._relay_url(discovery) is None:
            raise EnrollmentProtocolError("unavailable")
        if discovery.origin != client.state.origin:
            raise EnrollmentProtocolError("conflict")
        try:
            discovery_origin = urlsplit(discovery.origin)
            if (
                discovery_origin.scheme != "https"
                or not discovery_origin.hostname
                or discovery_origin.port not in (None, 443)
            ):
                raise EnrollmentProtocolError("unavailable")
        except ValueError as exc:
            raise EnrollmentProtocolError("unavailable") from exc
        manager = getattr(bridge.plugin, "_dns_identity", None)
        identity = bridge.identity
        designation = getattr(identity, "identity", None)
        if (
            manager is None
            or getattr(identity, "is_coordinator", False) is not True
            or not isinstance(designation, str)
            or not designation
        ):
            raise EnrollmentProtocolError("unauthorized")
        # The issuing agent's key owns its private network; the discovery
        # domain only locates the relay.
        owner_private_hex, owner_public_hex = manager.get_or_create_keypair(designation)
        issuer_principal_id = public_key_id(bytes.fromhex(owner_public_hex))

        offer_id = secrets.token_hex(16)
        code: EnrollmentCode | None = None
        verifier = envelope_key = None
        human_action_id = str(uuid.uuid4())
        now = int(time.time())
        expires_at = now + 300
        room_id = (
            "room:" + hashlib.sha256(b"kollab-relay-network-id-v1\0" + bytes.fromhex(client.state.room)).hexdigest()
        )
        origin_id = (
            "origin:"
            + hashlib.sha256(b"kollab-relay-network-origin-v1\0" + discovery.origin.encode("ascii")).hexdigest()
        )
        network_ids = tuple(sorted({origin_id, issuer_principal_id, room_id}))
        profile_manager = _profile_manager(getattr(bridge, "plugin", None))
        profile = getattr(identity, "profile", None) if profile_manager is None else None
        if isinstance(profile, str) and profile:
            profile = (
                "profile:"
                + hashlib.sha256(b"kollab-relay-enrollment-profile-v1\0" + profile.encode("utf-8")).hexdigest()
            )
        else:
            profile = None
        provisioning_plan = await self._make_provisioning_plan(offer_id)
        credential_categories = ("conversation:send",)
        if provisioning_plan is not None:
            profile = _profile_reference(provisioning_plan.source_profile_name)
            credential_categories = (
                "conversation:send",
                provisioning_plan.credential_category,
            )
        store = EnrollmentDelegationStore(bridge.owner.state_dir / "enrollment-delegations.json")
        delegated = False
        try:
            store.create(
                human_action_id=human_action_id,
                authorized_agent_id=str(identity.agent_id),
                authorized_session_id=session_id,
                issuer=issuer_principal_id,
                network_ids=network_ids,
                configuration_profile=profile,
                credential_categories=credential_categories,
                max_new_devices=1,
                expires_at=expires_at,
            )
            delegated = True
            code = generate_enrollment_code(offer_id)
            verifier = derive_enrollment_code_verifier(code)
            envelope_key = derive_enrollment_envelope_key(code)
            signing_key = client._store.key
            path = "/relay/v1/enrollment/offers"
            async with EnrollmentHTTPClient(discovery.origin, ca=ca, private_cidrs=private_cidrs) as transport:
                response = await transport.post_signed_retry(
                    signing_key,
                    path,
                    offer_id,
                    {
                        "issuer_key": client.public_key,
                        "room_capability": client.state.room,
                        "session": session_id,
                        "expires_at": expires_at,
                        "code_verifier_hash": enrollment_verifier_hash(offer_id, verifier),
                    },
                )
            _require_shape(response, {"status", "offer_id", "expires_at"})
            if (
                response["status"] != "offered"
                or response["offer_id"] != offer_id
                or response["expires_at"] != expires_at
            ):
                raise EnrollmentProtocolError("invalid_response")

            offer = _ActiveEnrollmentOffer(
                offer_id=offer_id,
                expires_at=expires_at,
                human_action_id=human_action_id,
                session_id=session_id,
                issuer_key=client.public_key,
                room_capability=client.state.room,
                origin=discovery.origin,
                issuer_principal_id=issuer_principal_id,
                network_ids=network_ids,
                profile=profile,
                envelope_key=envelope_key,
                owner_signing_key=SigningKey(bytes.fromhex(owner_private_hex)),
                discovery=discovery,
                ca=ca,
                private_cidrs=private_cidrs,
                domain=domain,
                relay_session_id=session_id,
                provisioning_plan=provisioning_plan,
                credential_categories=credential_categories,
            )
            self._offers[offer_id] = offer
            self._tasks[offer_id] = asyncio.create_task(self._serve_offer(offer), name="kollab-relay-enrollment")
            displayed_code = code.for_private_display()
            code.wipe()
            code = None
            verifier.wipe()
            verifier = None
            envelope_key = None  # The active offer owns the wipeable key.
            return {
                "status": "offered",
                "offer_id": offer_id,
                "expires_at": str(expires_at),
                "code": displayed_code,
            }
        except asyncio.CancelledError:
            if delegated:
                try:
                    store.revoke(human_action_id)
                except Exception:
                    pass
            raise
        except EnrollmentProtocolError:
            if delegated:
                store.revoke(human_action_id)
            raise
        except Exception as exc:
            if delegated:
                try:
                    store.revoke(human_action_id)
                except Exception:
                    pass
            raise EnrollmentProtocolError("transport") from exc
        finally:
            for secret in (envelope_key, verifier, code):
                if secret is not None:
                    secret.wipe()

    async def close(self) -> None:
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)
            self._recovery_task = None
        destination_tasks = tuple(self._destination_tasks.values())
        for task in destination_tasks:
            task.cancel()
        if destination_tasks:
            await asyncio.gather(*destination_tasks, return_exceptions=True)
        self._destination_tasks.clear()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for offer in self._offers.values():
            offer.envelope_key.wipe()
            if offer.offer_id in self._approved_offer_ids:
                continue
            try:
                EnrollmentDelegationStore(self.bridge.owner.state_dir / "enrollment-delegations.json").revoke(
                    offer.human_action_id
                )
            except Exception:
                pass
        self._offers.clear()
        self._live_requests.clear()

    async def _serve_offer(self, offer: _ActiveEnrollmentOffer) -> None:
        bridge = self.bridge
        client = bridge.commands.client
        store = EnrollmentDelegationStore(bridge.owner.state_dir / "enrollment-delegations.json")
        directory = PrivateDirectory(
            client.state_dir / "private-directory.json",
            owner_public_key=bytes(offer.owner_signing_key.verify_key),
            workspace_id=client.state.workspace_id,
        )
        relay_signing_key = client._store.key
        claim_ids = {"request": secrets.token_hex(16), "proof": secrets.token_hex(16)}
        challenge_token = ""
        destination_key = ""
        round_id = ""
        proof_token = ""
        issued_credential = None
        device_authorized = False
        receipt_persisted = False
        live_request: _LiveEnrollmentRequest | None = None
        try:
            async with EnrollmentHTTPClient(offer.origin, ca=offer.ca, private_cidrs=offer.private_cidrs) as transport:
                poll_path = f"/relay/v1/enrollment/offers/{offer.offer_id}/poll"
                while int(time.time()) < offer.expires_at:
                    if (
                        bridge._closed
                        or client._session_id != offer.active_session_id
                        or client.state.origin != offer.origin
                        or client.public_key != offer.issuer_key
                        or client.state.room != offer.room_capability
                        or client.status().get("state") != "online"
                    ):
                        return
                    if offer.recovery_mode:
                        recovery_request = offer.recovery["request"]
                        destination_key = recovery_request["destination_key"]
                        round_id = recovery_request["round_id"]
                        challenge_token = recovery_request["challenge_token"]
                        proof_token = recovery_request["proof_token"]
                        request = {"workspace_id": recovery_request["workspace_id"]}
                        proof_envelope = encrypt_enrollment_envelope(
                            offer.envelope_key,
                            {
                                "v": 1,
                                "offer_id": offer.offer_id,
                                "round_id": round_id,
                                "phase": "proof",
                                "destination_key": destination_key,
                                "proof_token": proof_token,
                            },
                        )
                        polled = {
                            "status": "claimed",
                            "phase": "proof",
                            "round_id": round_id,
                            "destination_key": destination_key,
                            "envelope": proof_envelope,
                        }
                    else:
                        phase_for_claim = "proof" if challenge_token else "request"
                        try:
                            polled = await transport.post_signed(
                                relay_signing_key,
                                poll_path,
                                offer.offer_id,
                                {
                                    "issuer_key": offer.issuer_key,
                                    "room_capability": offer.room_capability,
                                    "session": offer.active_session_id,
                                    "claim_id": claim_ids[phase_for_claim],
                                },
                            )
                        except EnrollmentProtocolError as exc:
                            if exc.code == "claimed":
                                return
                            if exc.code not in {"rate_limited", "transport"}:
                                return
                            await asyncio.sleep(exc.retry_after_seconds or 20)
                            continue
                    if polled.get("status") == "empty":
                        if set(polled) != {"status"}:
                            return
                        await asyncio.sleep(15)
                        continue
                    if polled.get("status") != "claimed" or set(polled) != {
                        "status",
                        "phase",
                        "round_id",
                        "destination_key",
                        "envelope",
                    }:
                        return
                    phase = polled["phase"]
                    if phase not in {"request", "proof"}:
                        return
                    if phase == "request":
                        request = validate_phase_envelope(
                            decrypt_enrollment_envelope(offer.envelope_key, polled["envelope"]),
                            offer_id=offer.offer_id,
                            round_id=polled["round_id"],
                            phase="request",
                            destination_key=polled["destination_key"],
                        )
                        _require_shape(
                            request,
                            {
                                "v",
                                "offer_id",
                                "round_id",
                                "phase",
                                "destination_key",
                                "workspace_id",
                            },
                        )
                        if not re.fullmatch(r"[0-9a-f]{32}", request["workspace_id"]):
                            return
                        destination_key = request["destination_key"]
                        round_id = request["round_id"]
                        if not re.fullmatch(r"[0-9a-f]{64}", destination_key):
                            return
                        try:
                            store.check_eligible(
                                offer.human_action_id,
                                agent_id=str(bridge.identity.agent_id),
                                session_id=offer.session_id,
                                issuer=offer.issuer_principal_id,
                                network_ids=offer.network_ids,
                                configuration_profile=offer.profile,
                                credential_categories=offer.credential_categories,
                            )
                        except Exception:
                            await self._publish_rejection(
                                transport,
                                offer,
                                relay_signing_key,
                                destination_key,
                                round_id,
                            )
                            return
                        challenge = directory.begin_pairing(
                            offer.owner_signing_key,
                            expected_device_public_key=bytes.fromhex(destination_key),
                            expires_in_seconds=max(30, min(300, offer.expires_at - int(time.time()))),
                        )
                        challenge_token = challenge.token
                        provisioning_scope_payload = {
                            "enrollment_id": round_id,
                            "network_ids": list(offer.network_ids),
                            "audience": request["workspace_id"],
                            "profile_name": (
                                offer.provisioning_plan.destination_profile_name
                                if offer.provisioning_plan is not None
                                else None
                            ),
                            "allowed_credential_categories": (
                                [offer.provisioning_plan.credential_category]
                                if offer.provisioning_plan is not None
                                else []
                            ),
                            "allowed_agent_names": [],
                            "allowed_skill_names": [],
                        }
                        payload = sign_enrollment_payload(
                            offer.owner_signing_key,
                            {
                                "v": 1,
                                "offer_id": offer.offer_id,
                                "round_id": round_id,
                                "phase": "challenge",
                                "destination_key": destination_key,
                                "owner_key": bytes(offer.owner_signing_key.verify_key).hex(),
                                "issuer_relay_key": offer.issuer_key,
                                "origin": offer.origin,
                                "expires_at": min(challenge.expires_at, offer.expires_at),
                                "pairing_challenge": challenge_token,
                                "provisioning_scope": provisioning_scope_payload,
                            },
                        )
                        envelope = encrypt_enrollment_envelope(offer.envelope_key, payload)
                        path = f"/relay/v1/enrollment/offers/{offer.offer_id}/challenge"
                        stored = await transport.post_signed_retry(
                            relay_signing_key,
                            path,
                            offer.offer_id,
                            {
                                "issuer_key": offer.issuer_key,
                                "room_capability": offer.room_capability,
                                "session": offer.active_session_id,
                                "round_id": round_id,
                                "destination_key": destination_key,
                                "envelope": envelope,
                            },
                        )
                        _require_shape(stored, {"status", "receipt"})
                        if stored["status"] != "stored" or stored["receipt"] != round_id:
                            return
                        await asyncio.sleep(15)
                        continue

                    if (
                        not challenge_token
                        or polled["round_id"] != round_id
                        or polled["destination_key"] != destination_key
                    ):
                        return
                    proof = validate_phase_envelope(
                        decrypt_enrollment_envelope(offer.envelope_key, polled["envelope"]),
                        offer_id=offer.offer_id,
                        round_id=round_id,
                        phase="proof",
                        destination_key=destination_key,
                    )
                    _require_shape(
                        proof,
                        {
                            "v",
                            "offer_id",
                            "round_id",
                            "phase",
                            "destination_key",
                            "proof_token",
                        },
                    )
                    if not isinstance(proof["proof_token"], str) or len(proof["proof_token"]) > 8192:
                        return
                    proof_token = proof["proof_token"]
                    if offer.recovery_mode:
                        verified_proof = verify_pairing_proof(
                            challenge_token,
                            proof_token,
                            owner_public_key=bytes.fromhex(
                                bytes(offer.owner_signing_key.verify_key).hex()
                            ),
                        )
                    else:
                        verified_proof = directory.record_pairing_proof(
                            challenge_token, proof_token
                        )
                    if verified_proof.public_key.hex() != destination_key:
                        return
                    if offer.recovery_mode:
                        pending = store.get_enrollment_request(
                            round_id,
                            agent_id=str(bridge.identity.agent_id),
                            session_id=offer.session_id,
                        )
                        if (
                            pending.status != "approved"
                            or pending.human_action_id != offer.human_action_id
                            or pending.device_key_fingerprint
                            != _device_key_fingerprint(destination_key)
                            or pending.workspace_id != request["workspace_id"]
                        ):
                            return
                    else:
                        try:
                            pending = store.record_pending(
                                offer.human_action_id,
                                enrollment_id=round_id,
                                device_key_fingerprint=_device_key_fingerprint(destination_key),
                                agent_id=str(bridge.identity.agent_id),
                                session_id=offer.session_id,
                                issuer=offer.issuer_principal_id,
                                network_ids=offer.network_ids,
                                configuration_profile=offer.profile,
                                credential_categories=offer.credential_categories,
                                workspace_id=request["workspace_id"],
                                expires_at=offer.expires_at,
                            )
                        except Exception:
                            await self._publish_rejection(
                                transport,
                                offer,
                                relay_signing_key,
                                destination_key,
                                round_id,
                            )
                            return
                        if pending.status != "pending":
                            return
                    live_request = _LiveEnrollmentRequest(
                        offer=offer,
                        destination_key=destination_key,
                        round_id=round_id,
                        workspace_id=request["workspace_id"],
                        challenge_token=challenge_token,
                        proof_token=proof_token,
                        decision_event=asyncio.Event(),
                    )
                    self._live_requests[round_id] = live_request
                    if offer.recovery_mode:
                        live_request.decision = "approved"
                    else:
                        try:
                            await asyncio.wait_for(
                                live_request.decision_event.wait(),
                                timeout=max(0.0, offer.expires_at - time.time()),
                            )
                        except asyncio.TimeoutError:
                            return
                        finally:
                            self._live_requests.pop(round_id, None)
                    if live_request.decision == "rejected":
                        await self._publish_rejection(
                            transport,
                            offer,
                            relay_signing_key,
                            destination_key,
                            round_id,
                        )
                        return
                    if live_request.decision != "approved":
                        return
                    record = store.get_enrollment_request(
                        round_id,
                        agent_id=str(bridge.identity.agent_id),
                        session_id=offer.session_id,
                    )
                    if record.status != "approved":
                        return
                    if (
                        bridge._closed
                        or client._session_id != offer.active_session_id
                        or client.public_key != offer.issuer_key
                        or client.state.room != offer.room_capability
                        or client.state.origin != offer.origin
                        or client.status().get("state") != "online"
                    ):
                        return
                    issued_credential = directory.approve_pairing(
                        challenge_token,
                        proof_token,
                        offer.owner_signing_key,
                        approved_by_human=True,
                        scopes=("conversation:send",),
                    )
                    if offer.recovery is not None:
                        offer.recovery["issued_credential"] = issued_credential.token
                        _store_issuer_recovery_progress(
                            self._recovery_journal(client), offer.recovery
                        )
                    if record.workspace_id != request["workspace_id"]:
                        return
                    from .provisioning import (
                        NetworkPreferences,
                        ProvisioningPayload,
                        ProvisioningScope,
                        _content_from_dict,
                        _validate_payload,
                        provisioning_payload_digest,
                        seal_provisioning_bundle,
                    )

                    origin = urlsplit(offer.origin)
                    discovery_domain = origin.hostname
                    if origin.scheme != "https" or origin.port not in (None, 443) or not discovery_domain:
                        return
                    provisioning_scope = ProvisioningScope(
                        enrollment_id=round_id,
                        network_ids=record.network_ids,
                        audience=record.workspace_id,
                        profile_name=(
                            offer.provisioning_plan.destination_profile_name
                            if offer.provisioning_plan is not None
                            else None
                        ),
                        allowed_credential_categories=frozenset(
                            {offer.provisioning_plan.credential_category}
                            if offer.provisioning_plan is not None
                            else set()
                        ),
                        allowed_agent_names=frozenset(),
                        allowed_skill_names=frozenset(),
                    )
                    recovery = offer.recovery
                    if recovery is not None and isinstance(
                        recovery.get("provisioning_content"), dict
                    ):
                        provisioning_payload = _content_from_dict(
                            recovery["provisioning_content"], provisioning_scope
                        )
                    else:
                        profile_preferences, credentials = await self._accepted_profile_payload(offer)
                        provisioning_payload = ProvisioningPayload(
                            profile=profile_preferences,
                            networks=tuple(
                                NetworkPreferences(network_id, discovery_domain)
                                for network_id in record.network_ids
                            ),
                            credentials=credentials,
                        )
                        if recovery is not None:
                            recovery["provisioning_content"] = _validate_payload(
                                provisioning_payload, provisioning_scope
                            )
                            _store_issuer_recovery_progress(
                                self._recovery_journal(client), recovery
                            )
                    expected_install_digest = provisioning_payload_digest(
                        provisioning_payload, scope=provisioning_scope
                    )
                    if (
                        recovery is not None
                        and recovery.get("expected_install_digest") is not None
                        and recovery["expected_install_digest"] != expected_install_digest
                    ):
                        return
                    owner_public_key = bytes(offer.owner_signing_key.verify_key).hex()
                    room_fingerprint = hashlib.sha256(
                        b"kollab-relay-enrollment-recovery-room-v1\0" + bytes.fromhex(offer.room_capability)
                    ).hexdigest()
                    intent_payload = sign_enrollment_payload(
                        offer.owner_signing_key,
                        {
                            "v": 1,
                            "offer_id": offer.offer_id,
                            "round_id": round_id,
                            "phase": "installation_intent",
                            "destination_key": destination_key,
                            "workspace_id": record.workspace_id,
                            "expected_digest": expected_install_digest,
                            "issuer_relay_key": offer.issuer_key,
                            "issuer_origin": offer.origin,
                            "room_fingerprint": room_fingerprint,
                            "issuer_workspace_id": client.state.workspace_id,
                            "owner_public_key": owner_public_key,
                            "scope_fingerprint": record.scope_fingerprint,
                        },
                    )
                    store.record_delivery_intent(
                        round_id,
                        agent_id=str(bridge.identity.agent_id),
                        session_id=offer.session_id,
                        offer_id=offer.offer_id,
                        destination_public_key=destination_key,
                        expected_digest=expected_install_digest,
                        issuer_relay_key=offer.issuer_key,
                        issuer_origin=offer.origin,
                        room_fingerprint=room_fingerprint,
                        issuer_workspace_id=client.state.workspace_id,
                        owner_public_key=owner_public_key,
                        owner_signature=intent_payload["owner_signature"],
                    )
                    if recovery is not None and recovery.get("decision_envelope"):
                        envelope = recovery["decision_envelope"]
                        saved_payload = verify_enrollment_payload(
                            bytes.fromhex(owner_public_key),
                            validate_phase_envelope(
                                decrypt_enrollment_envelope(offer.envelope_key, envelope),
                                offer_id=offer.offer_id,
                                round_id=round_id,
                                phase="decision",
                                destination_key=destination_key,
                            ),
                        )
                        if (
                            saved_payload.get("status") != "approved"
                            or saved_payload.get("credential") != issued_credential.token
                            or saved_payload.get("owner_key") != owner_public_key
                            or saved_payload.get("issuer_relay_key") != offer.issuer_key
                            or saved_payload.get("origin") != offer.origin
                        ):
                            return
                    else:
                        bundle = seal_provisioning_bundle(
                            provisioning_payload,
                            scope=provisioning_scope,
                            issuer_key=offer.owner_signing_key,
                            recipient_public_key=bytes.fromhex(destination_key),
                            revision=1,
                            expires_at=offer.expires_at,
                        )
                        payload = sign_enrollment_payload(
                            offer.owner_signing_key,
                            {
                                "v": 1,
                                "offer_id": offer.offer_id,
                                "round_id": round_id,
                                "phase": "decision",
                                "destination_key": destination_key,
                                "owner_key": bytes(offer.owner_signing_key.verify_key).hex(),
                                "issuer_relay_key": offer.issuer_key,
                                "origin": offer.origin,
                                "status": "approved",
                                "credential": issued_credential.token,
                                "invite": client.invite(),
                                "provisioning_bundle": base64.urlsafe_b64encode(bundle).rstrip(b"=").decode("ascii"),
                            },
                        )
                        envelope = encrypt_enrollment_envelope(offer.envelope_key, payload)
                        if recovery is not None:
                            recovery["expected_install_digest"] = expected_install_digest
                            recovery["decision_envelope"] = envelope
                            recovery["status"] = "approved"
                            _store_issuer_recovery_progress(
                                self._recovery_journal(client), recovery
                            )
                    decision_path = f"/relay/v1/enrollment/offers/{offer.offer_id}/decision"
                    stored = await transport.post_signed_retry(
                        relay_signing_key,
                        decision_path,
                        offer.offer_id,
                        {
                            "issuer_key": offer.issuer_key,
                            "room_capability": offer.room_capability,
                            "session": offer.active_session_id,
                            "round_id": round_id,
                            "destination_key": destination_key,
                            "envelope": envelope,
                        },
                    )
                    _require_shape(stored, {"status", "receipt"})
                    if stored["status"] == "stored" and stored["receipt"] == round_id:
                        ack_poll_path = f"/relay/v1/enrollment/offers/{offer.offer_id}/ack/poll"
                        while int(time.time()) < offer.expires_at:
                            try:
                                ack = await transport.post_signed(
                                    relay_signing_key,
                                    ack_poll_path,
                                    offer.offer_id,
                                    {
                                        "issuer_key": offer.issuer_key,
                                        "room_capability": offer.room_capability,
                                        "session": offer.active_session_id,
                                    },
                                )
                            except EnrollmentProtocolError as exc:
                                if exc.code not in {"rate_limited", "transport"}:
                                    return
                                await asyncio.sleep(exc.retry_after_seconds or 5)
                                continue
                            if ack.get("status") == "pending":
                                if set(ack) != {"status"}:
                                    return
                                await asyncio.sleep(5)
                                continue
                            _require_shape(ack, {"status", "frame"})
                            signed_ack = ack["frame"]
                            _require_shape(
                                signed_ack,
                                {
                                    "v",
                                    "offer_id",
                                    "issued_at",
                                    "nonce",
                                    "destination_key",
                                    "round_id",
                                    "envelope",
                                    "signature",
                                },
                            )
                            ack_path = f"/relay/v1/enrollment/offers/{offer.offer_id}/ack"
                            if (
                                ack["status"] != "ready"
                                or signed_ack["v"] != 1
                                or signed_ack["offer_id"] != offer.offer_id
                                or signed_ack["destination_key"] != destination_key
                                or signed_ack["round_id"] != round_id
                                or not verify_signed_enrollment_frame(
                                    destination_key,
                                    offer.origin,
                                    "POST",
                                    ack_path,
                                    signed_ack,
                                )
                            ):
                                return
                            ack_payload = validate_phase_envelope(
                                decrypt_enrollment_envelope(offer.envelope_key, signed_ack["envelope"]),
                                offer_id=offer.offer_id,
                                round_id=round_id,
                                phase="installation_ack",
                                destination_key=destination_key,
                            )
                            _require_shape(
                                ack_payload,
                                {
                                    "v",
                                    "offer_id",
                                    "round_id",
                                    "phase",
                                    "destination_key",
                                    "workspace_id",
                                    "status",
                                    "revision",
                                    "digest",
                                    "device_signature",
                                },
                            )
                            if (
                                ack_payload["workspace_id"] != record.workspace_id
                                or ack_payload["status"] not in {"installed", "already_installed"}
                                or isinstance(ack_payload["revision"], bool)
                                or not isinstance(ack_payload["revision"], int)
                                or ack_payload["revision"] != 1
                                or not isinstance(ack_payload["digest"], str)
                                or not re.fullmatch(r"[0-9a-f]{64}", ack_payload["digest"])
                                or ack_payload["digest"] != expected_install_digest
                                or not verify_installation_receipt(destination_key, ack_payload)
                            ):
                                return
                            store.record_install_ack(
                                round_id,
                                agent_id=str(bridge.identity.agent_id),
                                session_id=offer.session_id,
                                device_key_fingerprint=record.device_key_fingerprint,
                                workspace_id=record.workspace_id,
                                install_status=ack_payload["status"],
                                revision=ack_payload["revision"],
                                digest=ack_payload["digest"],
                                offer_id=offer.offer_id,
                                device_signature=ack_payload["device_signature"],
                            )
                            receipt_persisted = True
                            client.approve(destination_key)
                            store.mark_peer_approved(
                                round_id,
                                destination_public_key=destination_key,
                                owner_public_key=owner_public_key,
                                issuer_relay_key=offer.issuer_key,
                                issuer_origin=offer.origin,
                                room_fingerprint=room_fingerprint,
                                issuer_workspace_id=client.state.workspace_id,
                            )
                            device_authorized = True
                            try:
                                self._recovery_journal(client).delete(offer.offer_id)
                            except Exception:
                                pass
                            return
                        return
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not expose codes, proof tokens, paths, or raw protocol errors.
            return
        finally:
            if live_request is not None:
                self._live_requests.pop(live_request.round_id, None)
            offer.envelope_key.wipe()
            accepted = offer.offer_id in self._approved_offer_ids or (
                live_request is not None and live_request.decision == "approved"
            )
            if not accepted and not device_authorized and not receipt_persisted:
                if issued_credential is not None:
                    try:
                        directory.revoke(
                            "credential",
                            issued_credential.credential_id,
                            offer.owner_signing_key,
                        )
                    except Exception:
                        pass
                try:
                    store.revoke(offer.human_action_id)
                except Exception:
                    pass
            self._offers.pop(offer.offer_id, None)
            self._tasks.pop(offer.offer_id, None)

    async def _publish_rejection(
        self,
        transport: EnrollmentHTTPClient,
        offer: _ActiveEnrollmentOffer,
        relay_signing_key: SigningKey,
        destination_key: str,
        round_id: str,
    ) -> None:
        payload = sign_enrollment_payload(
            offer.owner_signing_key,
            {
                "v": 1,
                "offer_id": offer.offer_id,
                "round_id": round_id,
                "phase": "decision",
                "destination_key": destination_key,
                "owner_key": bytes(offer.owner_signing_key.verify_key).hex(),
                "issuer_relay_key": offer.issuer_key,
                "origin": offer.origin,
                "status": "rejected",
            },
        )
        envelope = encrypt_enrollment_envelope(offer.envelope_key, payload)
        path = f"/relay/v1/enrollment/offers/{offer.offer_id}/decision"
        stored = await transport.post_signed_retry(
            relay_signing_key,
            path,
            offer.offer_id,
            {
                "issuer_key": offer.issuer_key,
                "room_capability": offer.room_capability,
                "session": offer.active_session_id,
                "round_id": round_id,
                "destination_key": destination_key,
                "envelope": envelope,
            },
        )
        _require_shape(stored, {"status", "receipt"})
        if stored["status"] != "stored" or stored["receipt"] != round_id:
            raise EnrollmentProtocolError("invalid_response")
