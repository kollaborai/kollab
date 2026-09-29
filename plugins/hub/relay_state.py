"""Private, workspace-scoped state for the outbound relay client."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.signing import SigningKey, VerifyKey

from .device_names import validate_device_name, validate_trust
from .dns.discovery import normalize_target

KEY = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_APPROVALS = 256
INVITE_PREFIX = "kollab-invite-v1:"


class RelayError(ValueError):
    """A safe, operator-visible relay failure."""


def strict_json(raw: str | bytes, *, limit: int = 65536) -> dict:
    if len(raw) > limit:
        raise RelayError("JSON size limit exceeded")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RelayError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise RelayError("non-finite JSON number")

    def validate(value, depth=0):
        if depth > 12:
            raise RelayError("JSON nesting limit exceeded")
        if isinstance(value, float):
            raise RelayError("floating-point JSON values are unsupported")
        if type(value) is int and not 0 <= value <= 2**53 - 1:
            raise RelayError("JSON integer outside supported range")
        if isinstance(value, (dict, list)):
            for item in value.values() if isinstance(value, dict) else value:
                validate(item, depth + 1)

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(result, dict):
            raise RelayError("JSON object required")
        validate(result)
        json.dumps(result, ensure_ascii=False).encode("utf-8")
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, RelayError):
            raise
        raise RelayError("invalid JSON") from exc


def canonical_origin(value: str) -> str:
    try:
        normalized = normalize_target(value, document=False)
        if normalized.origin != value or normalized.url != value:
            raise ValueError("origin is not canonical")
        return normalized.origin
    except (ValueError, TypeError) as exc:
        raise RelayError("canonical HTTPS relay origin required") from exc


def validate_key(value: str) -> str:
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise RelayError("expected a 64-character lowercase Ed25519 public key")
    return value


def validate_public_key(value: str) -> str:
    validate_key(value)
    try:
        VerifyKey(bytes.fromhex(value)).to_curve25519_public_key()
    except CryptoError as exc:
        raise RelayError("peer key cannot be used for authenticated encryption") from exc
    return value


def parse_invite(token: str) -> dict:
    """Inspect an invitation without writing state or connecting anywhere."""
    if not isinstance(token, str) or len(token) > 4096 or not token.startswith(INVITE_PREFIX):
        raise RelayError("invalid relay invitation")
    encoded = token[len(INVITE_PREFIX) :]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise RelayError("invalid invitation encoding")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != encoded:
            raise RelayError("noncanonical invitation encoding")
        payload = strict_json(raw, limit=2048)
    except (ValueError, UnicodeError) as exc:
        raise RelayError("invalid relay invitation") from exc
    if set(payload) != {"v", "origin", "room", "inviter"} or type(payload["v"]) is not int or payload["v"] != 1:
        raise RelayError("unsupported invitation")
    return {
        "origin": canonical_origin(payload["origin"]),
        "room": validate_key(payload["room"]),
        "key": validate_public_key(payload["inviter"]),
    }


@dataclass
class RelayState:
    origin: str = ""
    enabled: bool = False
    room: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    workspace_id: str = field(default_factory=lambda: secrets.token_hex(16))
    approvals: list[str] = field(default_factory=list)
    inviter: str = ""
    device_name: str = ""
    trust: str = "open"


class RelayStateStore:
    """No state, seed or invitation is written inside the workspace."""

    def __init__(self, workspace: Path, state_dir: Path | None):
        digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
        self.path = state_dir or Path.home() / ".kollab" / "network" / digest
        if self.path.is_symlink():
            raise RelayError("relay state directory must not be a symbolic link")
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path, 0o700)
        key_path = self.path / "device.key"
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            seed = self._read_private(key_path, 128).strip()
        else:
            seed = secrets.token_hex(32)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(seed + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        if not KEY.fullmatch(seed):
            raise RelayError("invalid relay device key file")
        self.key = SigningKey(bytes.fromhex(seed))
        self.state_path = self.path / "state.json"
        if self.state_path.exists() or self.state_path.is_symlink():
            payload = strict_json(self._read_private(self.state_path, 65536))
            # A subset check, not equality: an older state file predating
            # device_name/trust is missing those keys, and the dataclass
            # defaults fill them in. Any key outside the dataclass is still
            # rejected.
            if set(payload) - set(RelayState.__dataclass_fields__):
                raise RelayError("unsupported relay state fields")
            self.state = RelayState(**payload)
            self._validate()
        else:
            self.state = RelayState()
            self.save()

    @staticmethod
    def _read_private(path: Path, limit: int) -> str:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RelayError("relay state files must be owned by this user with mode 0600")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise RelayError("relay state file too large")
            return raw

    def _validate(self):
        value = self.state
        if type(value.enabled) is not bool:
            raise RelayError("invalid relay enabled state")
        if not isinstance(value.origin, str) or not isinstance(value.inviter, str):
            raise RelayError("invalid relay origin or inviter")
        validate_key(value.room)
        if not isinstance(value.workspace_id, str) or not ID.fullmatch(value.workspace_id):
            raise RelayError("invalid workspace identity")
        if value.origin:
            canonical_origin(value.origin)
        elif value.enabled:
            raise RelayError("enabled relay requires an origin")
        if (
            not isinstance(value.approvals, list)
            or len(value.approvals) > MAX_APPROVALS
            or any(not isinstance(key, str) for key in value.approvals)
            or len(set(value.approvals)) != len(value.approvals)
        ):
            raise RelayError("invalid relay approvals")
        for key in value.approvals:
            validate_public_key(key)
        if value.inviter:
            validate_public_key(value.inviter)
        try:
            if value.device_name:
                validate_device_name(value.device_name)
            validate_trust(value.trust)
        except ValueError as exc:
            # device_names raises plain ValueError; every failure out of this
            # store must be the operator-visible RelayError, like every other
            # field checked above.
            raise RelayError(str(exc)) from exc

    def save(self):
        self._validate()
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(asdict(self.state), stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def invite(self) -> str:
        if not self.state.origin:
            raise RelayError("connect to a verified relay before creating an invitation")
        payload = {
            "v": 1,
            "origin": self.state.origin,
            "room": self.state.room,
            "inviter": self.key.verify_key.encode().hex(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def join(self, token: str) -> str:
        payload = parse_invite(token)
        origin, room, inviter = payload["origin"], payload["room"], payload["key"]
        if inviter == self.key.verify_key.encode().hex():
            raise RelayError("cannot join your own invitation")
        # Joining authorizes only this inviter's network ping/presence. The
        # origin is merely staged here; the caller must verify discovery.
        self.state.origin = origin
        self.state.enabled = False
        self.state.room = room
        self.state.inviter = inviter
        self.state.approvals = [inviter]
        self.save()
        return origin
