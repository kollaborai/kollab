"""Pinned TLS 1.3 sessions carried as bounded, sequenced relay packets.

The caller owns routing and must deliver each direction's packets in order.
Each packet carries a random 128-bit transport correlator and a monotonically
increasing sequence number. After mutual authentication, both endpoints expose
a separate transcript ID derived from the pinned identities and the ordered,
role-labeled TLS handshake bytes. The transport correlator is not an identity
or proof. TLS records are opaque bytes; no plaintext is stored by the packet
layer.

Python's ``ssl.SSLContext.load_cert_chain`` accepts paths rather than in-memory
keys. To load the existing Ed25519 identity without durable key storage, this
module writes a mode-0600 PEM into a mode-0700 temporary directory, loads it
synchronously into OpenSSL, and unlinks it immediately. This is transient
private-key materialization. Platforms where the private permissions cannot
be confirmed fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import ssl
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ALPN = "kollab-agent/1"
SESSION_ID_BYTES = 16
MAX_CERTIFICATE_BYTES = 16 * 1024
MAX_PACKET_BYTES = 128 * 1024
MAX_PENDING_BYTES = 256 * 1024
MAX_PLAINTEXT_BYTES = 64 * 1024
MAX_HANDSHAKE_BYTES = 512 * 1024
MAX_HANDSHAKE_PACKETS = 256
MAX_SESSION_PLAINTEXT_BYTES = 1 * 1024 * 1024 * 1024
# Relay RPC sequence values pass through JSON numbers, whose interoperable
# exact-integer range is bounded by IEEE-754's 53-bit mantissa.
MAX_SEQUENCE = (1 << 53) - 1

__all__ = [
    "ALPN",
    "MAX_HANDSHAKE_BYTES",
    "MAX_HANDSHAKE_PACKETS",
    "MAX_PACKET_BYTES",
    "MAX_PENDING_BYTES",
    "MAX_PLAINTEXT_BYTES",
    "MAX_SEQUENCE",
    "MAX_SESSION_PLAINTEXT_BYTES",
    "SESSION_ID_BYTES",
    "SecureSession",
    "SecureSessionError",
    "SessionUpdate",
    "TLSRecordPacket",
    "identity_certificate",
    "identity_public_key",
]

_IDENTITY_CERT_NOT_BEFORE = datetime(2020, 1, 1, tzinfo=timezone.utc)
_IDENTITY_CERT_NOT_AFTER = datetime(2100, 1, 1, tzinfo=timezone.utc)


class SecureSessionError(RuntimeError):
    """Safe, non-secret failure from the TLS session or packet boundary."""


@dataclass(frozen=True, slots=True)
class TLSRecordPacket:
    """One opaque TLS byte chunk on a single, ordered session direction."""

    session_id: bytes
    sequence: int
    data: bytes


@dataclass(frozen=True, slots=True)
class SessionUpdate:
    """Result of accepting a packet from the peer."""

    packet: TLSRecordPacket | None
    plaintext: bytes
    established: bool


def identity_public_key(identity_seed: bytes) -> bytes:
    """Return the Ed25519 public key for the existing 32-byte Kollab seed."""
    if not isinstance(identity_seed, bytes) or len(identity_seed) != 32:
        raise SecureSessionError("identity seed must be exactly 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(identity_seed)
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def identity_certificate(identity_seed: bytes) -> bytes:
    """Build a stable self-signed Ed25519 identity certificate as PEM bytes.

    The certificate contains no private material. Its public key is exactly the
    Ed25519 public key derived from ``identity_seed``. Callers may distribute
    and store this public certificate with the approved raw public-key pin.
    """
    if not isinstance(identity_seed, bytes) or len(identity_seed) != 32:
        raise SecureSessionError("identity seed must be exactly 32 bytes")

    private_key = Ed25519PrivateKey.from_private_bytes(identity_seed)
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kollab-agent")])
    serial = int.from_bytes(
        hashlib.sha256(b"kollab-secure-session-cert-v1\0" + public_bytes).digest()[:19],
        "big",
    )
    serial = max(serial, 1)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(serial)
        .not_valid_before(_IDENTITY_CERT_NOT_BEFORE)
        .not_valid_after(_IDENTITY_CERT_NOT_AFTER)
        # The self-signed identity certificate is the sole trust anchor for
        # one pinned peer. Exact certificate comparison after the handshake
        # prevents a child certificate from being accepted as that identity.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.CLIENT_AUTH,
                    ExtendedKeyUsageOID.SERVER_AUTH,
                ]
            ),
            critical=False,
        )
        .sign(private_key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _parse_pinned_certificate(
    certificate_pem: bytes, expected_public_key: bytes
) -> tuple[x509.Certificate, bytes]:
    if (
        not isinstance(certificate_pem, bytes)
        or not 1 <= len(certificate_pem) <= MAX_CERTIFICATE_BYTES
    ):
        raise SecureSessionError("peer certificate size is invalid")
    if not isinstance(expected_public_key, bytes) or len(expected_public_key) != 32:
        raise SecureSessionError("expected peer key must be exactly 32 bytes")
    if certificate_pem.count(b"-----BEGIN CERTIFICATE-----") != 1:
        raise SecureSessionError(
            "peer certificate must contain exactly one certificate"
        )

    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem)
        public_key = certificate.public_key()
        if not isinstance(public_key, Ed25519PublicKey):
            raise SecureSessionError("peer certificate key type is not Ed25519")
        actual_public_key = public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if not hmac.compare_digest(actual_public_key, expected_public_key):
            raise SecureSessionError("peer certificate does not match the approved key")
        if certificate.subject != certificate.issuer:
            raise SecureSessionError("peer identity certificate is not self-signed")
        if certificate.subject != x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "kollab-agent")]
        ):
            raise SecureSessionError("peer identity certificate subject is invalid")
        public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        if not constraints.ca or constraints.path_length != 0:
            raise SecureSessionError(
                "peer identity certificate constraints are invalid"
            )
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        if not usage.digital_signature or not usage.key_cert_sign:
            raise SecureSessionError("peer identity certificate usage is invalid")
        extended_usage = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        if not {
            ExtendedKeyUsageOID.CLIENT_AUTH,
            ExtendedKeyUsageOID.SERVER_AUTH,
        }.issubset(set(extended_usage)):
            raise SecureSessionError("peer identity certificate purpose is invalid")
    except SecureSessionError:
        raise
    except Exception:
        raise SecureSessionError("peer identity certificate is invalid") from None

    return certificate, certificate.public_bytes(serialization.Encoding.DER)


def _write_private_file(path: Path, contents: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
        ):
            raise SecureSessionError("private TLS key file permissions are unsafe")
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(contents)
            output.flush()
    finally:
        os.close(descriptor)


def _load_identity_chain(
    context: ssl.SSLContext, certificate_pem: bytes, identity_seed: bytes
) -> None:
    """Load a local certificate and key without leaving a durable key file."""
    if not hasattr(os, "getuid"):
        raise SecureSessionError(
            "secure TLS key loading is unsupported on this platform"
        )

    private_key = Ed25519PrivateKey.from_private_bytes(identity_seed)
    private_key_pem = bytearray(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key = None
    try:
        with tempfile.TemporaryDirectory(prefix="kollab-tls-") as temp_dir:
            directory = Path(temp_dir)
            os.chmod(directory, 0o700)
            directory_info = directory.stat()
            if (
                stat.S_IMODE(directory_info.st_mode) != 0o700
                or directory_info.st_uid != os.getuid()
                or not stat.S_ISDIR(directory_info.st_mode)
            ):
                raise SecureSessionError(
                    "private TLS key directory permissions are unsafe"
                )

            certificate_path = directory / "identity.pem"
            key_path = directory / "identity-key.pem"
            _write_private_file(certificate_path, certificate_pem)
            _write_private_file(key_path, bytes(private_key_pem))
            try:
                context.load_cert_chain(str(certificate_path), str(key_path))
            except (OSError, ssl.SSLError, ValueError):
                raise SecureSessionError(
                    "OpenSSL could not load the local TLS identity"
                ) from None
            finally:
                # Remove the secret before removing the directory. Never retain
                # this path or include it in an exception/log message.
                try:
                    key_path.unlink(missing_ok=True)
                except OSError:
                    raise SecureSessionError(
                        "temporary TLS key could not be removed"
                    ) from None
    except SecureSessionError:
        raise
    except (OSError, ValueError):
        raise SecureSessionError("secure temporary TLS key loading failed") from None
    finally:
        for index in range(len(private_key_pem)):
            private_key_pem[index] = 0


class SecureSession:
    """One mutually authenticated TLS 1.3 session over sequenced relay bytes.

    ``identity_seed`` is the existing raw 32-byte Kollab Ed25519 seed.
    ``expected_peer_certificate_pem`` must be the public self-signed identity
    certificate associated with ``expected_peer_public_key``; it is normally
    exchanged and pinned with the peer identity during pairing.

    The caller must serialize each returned ``TLSRecordPacket`` in its
    direction's order and pass each inbound packet exactly once. A session ID
    is new for each client-initiated handshake. The server adopts the ID from
    the first packet; TLS authenticates both identities before the session is
    established. Both directions begin at sequence zero. Packets are opaque
    TLS records, not application messages. Calls on a session must be
    serialized; ``ssl.SSLObject`` is not a concurrent transport API.
    """

    def __init__(
        self,
        identity_seed: bytes,
        expected_peer_public_key: bytes,
        expected_peer_certificate_pem: bytes,
        *,
        role: Literal["client", "server"],
    ) -> None:
        if not isinstance(identity_seed, bytes) or len(identity_seed) != 32:
            raise SecureSessionError("identity seed must be exactly 32 bytes")
        if role not in ("client", "server"):
            raise SecureSessionError("TLS role must be client or server")
        _parse_pinned_certificate(
            expected_peer_certificate_pem, expected_peer_public_key
        )

        self._role = role
        self.local_public_key = identity_public_key(identity_seed)
        self.expected_peer_public_key = expected_peer_public_key
        self._expected_peer_certificate_der = x509.load_pem_x509_certificate(
            expected_peer_certificate_pem
        ).public_bytes(serialization.Encoding.DER)
        self._authenticated_peer_public_key: bytes | None = None
        self._session_id = (
            secrets.token_bytes(SESSION_ID_BYTES) if role == "client" else None
        )
        self._next_outgoing_sequence = 0
        self._next_incoming_sequence = 0
        self._handshake_incoming_bytes = 0
        self._handshake_outgoing_bytes = 0
        self._handshake_incoming_packets = 0
        self._handshake_outgoing_packets = 0
        self._handshake_incoming = bytearray()
        self._handshake_outgoing = bytearray()
        self._transcript_id: bytes | None = None
        self._sent_plaintext_bytes = 0
        self._received_plaintext_bytes = 0
        self._application_started = False
        self._started = False
        self._state: Literal["new", "handshaking", "open", "closed"] = "new"

        context = ssl.SSLContext(
            ssl.PROTOCOL_TLS_CLIENT if role == "client" else ssl.PROTOCOL_TLS_SERVER
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.options |= ssl.OP_NO_COMPRESSION
        if hasattr(ssl, "OP_NO_TICKET"):
            context.options |= ssl.OP_NO_TICKET
        if role == "server":
            context.num_tickets = 0
        context.set_alpn_protocols([ALPN])
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = False
        try:
            context.load_verify_locations(
                cadata=expected_peer_certificate_pem.decode("ascii")
            )
        except (UnicodeDecodeError, ssl.SSLError, ValueError):
            raise SecureSessionError(
                "pinned peer certificate could not be loaded"
            ) from None

        local_certificate_pem = identity_certificate(identity_seed)
        _load_identity_chain(context, local_certificate_pem, identity_seed)

        self._context: ssl.SSLContext | None = context
        self._incoming: ssl.MemoryBIO | None = ssl.MemoryBIO()
        self._outgoing: ssl.MemoryBIO | None = ssl.MemoryBIO()
        self._tls: ssl.SSLObject | None = context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_side=role == "server",
            server_hostname=None,
            session=None,
        )
        self._state = "handshaking"

    @property
    def session_id(self) -> bytes | None:
        """The fresh session ID, or ``None`` until a server receives packet 0."""
        return self._session_id

    @property
    def authenticated_peer_public_key(self) -> bytes | None:
        """Return the peer key only after its certificate has passed TLS checks."""
        return self._authenticated_peer_public_key

    @property
    def transcript_id(self) -> bytes | None:
        """Return the authenticated handshake binding after TLS establishment.

        This digest is distinct from ``TLSRecordPacket.session_id``, which is
        only an untrusted packet-routing correlator. The digest excludes that
        correlator and is computed from both pinned Ed25519 keys plus the
        ordered client-to-server and server-to-client TLS handshake bytes.
        """
        return self._transcript_id

    @property
    def established(self) -> bool:
        return self._state == "open"

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def start(self) -> TLSRecordPacket | None:
        """Start the TLS handshake; only the client emits the first packet."""
        self._ensure_active()
        if self._started:
            self._abort()
            raise SecureSessionError("TLS handshake was already started")
        self._started = True
        self._drive_handshake()
        return self._packet_from_pending_output()

    def receive(self, packet: TLSRecordPacket) -> SessionUpdate:
        """Accept one ordered TLS packet and return any response/plaintext."""
        self._ensure_active()
        if not self._started:
            self._abort()
            raise SecureSessionError("TLS handshake has not been started")
        self._validate_packet(packet)
        if self._session_id is None:
            # The id is an untrusted routing tag until TLS authenticates peers.
            self._session_id = packet.session_id
        if not hmac.compare_digest(packet.session_id, self._session_id):
            self._abort()
            raise SecureSessionError("TLS packet belongs to another session")
        if self._next_incoming_sequence > MAX_SEQUENCE:
            self._abort()
            raise SecureSessionError("TLS packet sequence exhausted")
        if packet.sequence != self._next_incoming_sequence:
            self._abort()
            raise SecureSessionError("TLS packet sequence is duplicate or out of order")
        self._next_incoming_sequence += 1

        incoming = self._incoming
        if incoming is None or incoming.pending + len(packet.data) > MAX_PENDING_BYTES:
            self._abort()
            raise SecureSessionError("TLS pending input limit exceeded")
        if (
            not self._application_started
            and self._handshake_incoming_bytes + len(packet.data) > MAX_HANDSHAKE_BYTES
        ):
            self._abort()
            raise SecureSessionError("TLS handshake input limit exceeded")
        if self._transcript_id is None:
            self._record_handshake_packet(
                self._handshake_incoming,
                packet.data,
                incoming=True,
            )
        if not self._application_started:
            self._handshake_incoming_bytes += len(packet.data)

        try:
            written = incoming.write(packet.data)
            if written != len(packet.data):
                raise SecureSessionError("TLS input was not fully accepted")
            if not self.established:
                self._drive_handshake()
            plaintext = self._read_plaintext() if self.established else b""
            if (
                self._received_plaintext_bytes + len(plaintext)
                > MAX_SESSION_PLAINTEXT_BYTES
            ):
                raise SecureSessionError("TLS session data limit exceeded")
            if plaintext:
                self._received_plaintext_bytes += len(plaintext)
                self._application_started = True
            if incoming.pending > MAX_PENDING_BYTES:
                raise SecureSessionError("TLS pending input limit exceeded")
            response = self._packet_from_pending_output()
            self._maybe_finalize_transcript()
            return SessionUpdate(response, plaintext, self.established)
        except SecureSessionError:
            self._abort()
            raise
        except (ssl.SSLError, ValueError, OSError):
            self._abort()
            raise SecureSessionError("TLS packet or handshake was rejected") from None

    def send(self, plaintext: bytes) -> TLSRecordPacket:
        """Encrypt one bounded application chunk after the TLS handshake."""
        self._ensure_active()
        if not self.established or self._tls is None or self._transcript_id is None:
            self._abort()
            raise SecureSessionError("authenticated TLS transcript is not established")
        if (
            not isinstance(plaintext, bytes)
            or not 1 <= len(plaintext) <= MAX_PLAINTEXT_BYTES
        ):
            self._abort()
            raise SecureSessionError("plaintext size is invalid")
        if self._sent_plaintext_bytes + len(plaintext) > MAX_SESSION_PLAINTEXT_BYTES:
            self._abort()
            raise SecureSessionError("TLS session data limit exceeded")

        try:
            offset = 0
            while offset < len(plaintext):
                count = self._tls.write(plaintext[offset:])
                if count <= 0:
                    raise SecureSessionError("TLS did not accept application data")
                offset += count
                outgoing = self._outgoing
                if outgoing is None or outgoing.pending > MAX_PENDING_BYTES:
                    raise SecureSessionError("TLS pending output limit exceeded")
            packet = self._packet_from_pending_output()
            if packet is None:
                raise SecureSessionError("TLS produced no encrypted application packet")
            self._sent_plaintext_bytes += len(plaintext)
            self._application_started = True
            return packet
        except SecureSessionError:
            self._abort()
            raise
        except (ssl.SSLError, ValueError, OSError):
            self._abort()
            raise SecureSessionError(
                "TLS application data could not be encrypted"
            ) from None

    def end_of_stream(self) -> None:
        """Reject a transport EOF without an authenticated TLS close alert."""
        self._ensure_active()
        if self._incoming is not None:
            try:
                self._incoming.write_eof()
            except (ValueError, ssl.SSLError):
                pass
        self._abort()
        raise SecureSessionError(
            "TLS transport ended without an authenticated close alert"
        )

    def close(self) -> None:
        """Discard this session; reconnects must perform a fresh handshake."""
        self._abort()

    def _validate_packet(self, packet: TLSRecordPacket) -> None:
        if not isinstance(packet, TLSRecordPacket):
            self._abort()
            raise SecureSessionError("TLS packet type is invalid")
        if (
            not isinstance(packet.session_id, bytes)
            or len(packet.session_id) != SESSION_ID_BYTES
        ):
            self._abort()
            raise SecureSessionError("TLS packet session id is invalid")
        if type(packet.sequence) is not int or not 0 <= packet.sequence <= MAX_SEQUENCE:
            self._abort()
            raise SecureSessionError("TLS packet sequence is invalid")
        if (
            not isinstance(packet.data, bytes)
            or not 1 <= len(packet.data) <= MAX_PACKET_BYTES
        ):
            self._abort()
            raise SecureSessionError("TLS packet size is invalid")

    def _drive_handshake(self) -> None:
        tls = self._tls
        if tls is None or self._state == "closed":
            raise SecureSessionError("TLS session is closed")
        try:
            tls.do_handshake()
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            return
        except (ssl.SSLError, ValueError, OSError):
            self._abort()
            raise SecureSessionError(
                "TLS handshake or peer authentication failed"
            ) from None

        try:
            if tls.version() != "TLSv1.3":
                raise SecureSessionError("TLS 1.3 is required")
            if tls.selected_alpn_protocol() != ALPN:
                raise SecureSessionError("TLS application protocol negotiation failed")
            if tls.session_reused:
                raise SecureSessionError("TLS session resumption is disabled")
            peer_der = tls.getpeercert(binary_form=True)
            if not peer_der or not hmac.compare_digest(
                peer_der, self._expected_peer_certificate_der
            ):
                raise SecureSessionError("TLS peer certificate does not match its pin")
            peer_certificate = x509.load_der_x509_certificate(peer_der)
            peer_key = peer_certificate.public_key()
            if not isinstance(peer_key, Ed25519PublicKey):
                raise SecureSessionError("TLS peer identity is not Ed25519")
            peer_public_key = peer_key.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            if not hmac.compare_digest(peer_public_key, self.expected_peer_public_key):
                raise SecureSessionError("TLS peer key does not match its approval")
            if self._session_id is None:
                raise SecureSessionError("TLS session id was not established")
            self._authenticated_peer_public_key = peer_public_key
            self._state = "open"
        except SecureSessionError:
            self._abort()
            raise
        except Exception:
            self._abort()
            raise SecureSessionError("TLS peer identity validation failed") from None

    def _read_plaintext(self) -> bytes:
        tls = self._tls
        if tls is None:
            raise SecureSessionError("TLS session is closed")
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = tls.read(min(16 * 1024, MAX_PLAINTEXT_BYTES + 1 - total))
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                break
            except ssl.SSLZeroReturnError:
                self._abort()
                raise SecureSessionError("TLS peer closed the session") from None
            except (ssl.SSLError, ValueError, OSError):
                self._abort()
                raise SecureSessionError("TLS plaintext record was rejected") from None
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PLAINTEXT_BYTES:
                raise SecureSessionError("TLS plaintext limit exceeded")
            chunks.append(chunk)
        return b"".join(chunks)

    def _packet_from_pending_output(self) -> TLSRecordPacket | None:
        outgoing = self._outgoing
        session_id = self._session_id
        if outgoing is None:
            raise SecureSessionError("TLS session is closed")
        pending = outgoing.pending
        if pending == 0:
            return None
        if pending > MAX_PACKET_BYTES or pending > MAX_PENDING_BYTES:
            self._abort()
            raise SecureSessionError("TLS output packet limit exceeded")
        if (
            not self._application_started
            and self._handshake_outgoing_bytes + pending > MAX_HANDSHAKE_BYTES
        ):
            self._abort()
            raise SecureSessionError("TLS handshake output limit exceeded")
        if session_id is None:
            self._abort()
            raise SecureSessionError("TLS output has no session id")
        if self._next_outgoing_sequence > MAX_SEQUENCE:
            self._abort()
            raise SecureSessionError("TLS packet sequence exhausted")
        data = outgoing.read(pending)
        if len(data) != pending:
            self._abort()
            raise SecureSessionError("TLS output packet was incomplete")
        if not self._application_started:
            self._handshake_outgoing_bytes += len(data)
        if self._transcript_id is None:
            self._record_handshake_packet(
                self._handshake_outgoing,
                data,
                incoming=False,
            )
        packet = TLSRecordPacket(session_id, self._next_outgoing_sequence, data)
        self._next_outgoing_sequence += 1
        return packet

    def _record_handshake_packet(
        self, transcript: bytearray, data: bytes, *, incoming: bool
    ) -> None:
        count_name = (
            "_handshake_incoming_packets" if incoming else "_handshake_outgoing_packets"
        )
        count = getattr(self, count_name)
        if (
            count >= MAX_HANDSHAKE_PACKETS
            or len(transcript) + len(data) > MAX_HANDSHAKE_BYTES
        ):
            self._abort()
            raise SecureSessionError("TLS handshake transcript limit exceeded")
        transcript.extend(data)
        setattr(self, count_name, count + 1)

    def _maybe_finalize_transcript(self) -> None:
        if self._transcript_id is not None or not self.established:
            return
        if self._session_id is None:
            self._abort()
            raise SecureSessionError("TLS session id was not established")

        if self._role == "client":
            client_key, server_key = (
                self.local_public_key,
                self.expected_peer_public_key,
            )
            client_to_server = self._handshake_outgoing
            server_to_client = self._handshake_incoming
        else:
            client_key, server_key = (
                self.expected_peer_public_key,
                self.local_public_key,
            )
            client_to_server = self._handshake_incoming
            server_to_client = self._handshake_outgoing

        digest = hashlib.sha256()
        digest.update(b"kollab-tls-transcript-v1\0")
        digest.update(ALPN.encode("ascii") + b"\0")
        digest.update(b"client-ed25519\0" + client_key)
        digest.update(b"server-ed25519\0" + server_key)
        for label, wire_bytes in (
            (b"client-to-server", client_to_server),
            (b"server-to-client", server_to_client),
        ):
            digest.update(label + b"\0")
            digest.update(len(wire_bytes).to_bytes(8, "big"))
            digest.update(wire_bytes)
        self._transcript_id = digest.digest()
        self._handshake_incoming.clear()
        self._handshake_outgoing.clear()

    def _ensure_active(self) -> None:
        if self._state == "closed":
            raise SecureSessionError("TLS session is closed")

    def _abort(self) -> None:
        self._state = "closed"
        self._authenticated_peer_public_key = None
        self._transcript_id = None
        self._handshake_incoming.clear()
        self._handshake_outgoing.clear()
        self._tls = None
        self._incoming = None
        self._outgoing = None
        self._context = None
