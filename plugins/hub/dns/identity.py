"""Ed25519 identity management for Agent DNS.

Lightweight cryptographic identity using PyNaCl (libsodium).
Each designation gets a keypair; the coordinator signs attestations.

Compatible with:
- AID pka/k field (Ed25519 public key in hex)
- ARDP proof-of-control (signed registration body)
- AID PKA handshake (challenge-response verification)

This is NOT full PKI. No CAs, no certificate chains, no revocation
lists. Just enough crypto to prevent impersonation: an agent that
claims to be "peridot" must prove it holds peridot's private key.
"""

import logging
import struct
import time
from typing import Optional, Tuple

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import SigningKey, VerifyKey
    _NACL_AVAILABLE = True
except ImportError:
    raise ImportError(
        "PyNaCl is required for Agent DNS. Install with: pip install pynacl"
    )

from .models import Attestation
from .storage import DNSStorage

logger = logging.getLogger(__name__)


class IdentityManager:
    """Manages Ed25519 keypairs, signing, and attestation verification."""

    def __init__(self, storage: DNSStorage):
        self._storage = storage
        self._signing_keys: dict[str, SigningKey] = {}
        self._verify_keys: dict[str, VerifyKey] = {}

    def get_or_create_keypair(self, designation: str) -> Tuple[str, str]:
        """Load existing keypair or generate new one for a designation.

        Returns (private_key_hex, public_key_hex).
        Keys persist across sessions — same designation always gets
        same keys (identity survives rebirth).
        """
        # Check cache
        if designation in self._signing_keys:
            sk = self._signing_keys[designation]
            return sk.encode().hex(), sk.verify_key.encode().hex()

        # Try loading from storage
        key_bytes = self._storage.load_private_key(designation)
        if key_bytes is not None:
            try:
                sk = SigningKey(key_bytes)
                self._signing_keys[designation] = sk
                self._verify_keys[designation] = sk.verify_key
                pub_hex = sk.verify_key.encode().hex()
                logger.debug(f"loaded existing keypair for {designation}")
                return sk.encode().hex(), pub_hex
            except Exception as e:
                logger.warning(f"corrupt key for {designation}, regenerating: {e}")

        # Generate new keypair
        sk = SigningKey.generate()
        self._signing_keys[designation] = sk
        self._verify_keys[designation] = sk.verify_key

        # Persist
        self._storage.save_private_key(designation, sk.encode())
        pub_hex = sk.verify_key.encode().hex()
        self._storage.save_public_key(designation, pub_hex)
        logger.info(f"generated new keypair for {designation}")

        return sk.encode().hex(), pub_hex

    def sign_message(self, designation: str, message: bytes) -> str:
        """Sign a message with designation's private key.

        Returns signature as hex string.
        """
        sk = self._signing_keys.get(designation)
        if sk is None:
            raise ValueError(f"no private key loaded for {designation}")
        signed = sk.sign(message)
        return signed.signature.hex()

    def verify_signature(
        self, public_key_hex: str, message: bytes, signature_hex: str
    ) -> bool:
        """Verify a signature against a public key.

        Returns True if valid, False otherwise.
        """
        try:
            vk = VerifyKey(bytes.fromhex(public_key_hex))
            vk.verify(message, bytes.fromhex(signature_hex))
            return True
        except (BadSignatureError, ValueError, Exception):
            return False

    def create_attestation(
        self, subject: str, issuer: str, subject_public_key_hex: str
    ) -> Attestation:
        """Create a signed attestation.

        The issuer signs a statement binding subject to their public key.
        Used by coordinator to attest designation assignments.
        """
        issued_at = time.time()

        # Build the message to sign: subject + public_key + timestamp
        message = self._attestation_message(
            subject, subject_public_key_hex, issued_at
        )

        # Sign with issuer's key
        signature = self.sign_message(issuer, message)

        return Attestation(
            subject=subject,
            issuer=issuer,
            public_key=subject_public_key_hex,
            signature=signature,
            issued_at=issued_at,
            attestation_type="registration",
        )

    def verify_attestation(self, attestation: Attestation) -> bool:
        """Verify an attestation's signature.

        Uses the issuer's public key. Falls back to the coordinator's
        published key if the issuer's key is not found locally.
        """
        if attestation.is_expired:
            return False

        # Get issuer's public key (try designation key first, then coordinator)
        issuer_pub = self._get_public_key_hex(attestation.issuer)
        if issuer_pub is None:
            issuer_pub = self._get_coordinator_public_key_hex()
        if issuer_pub is None:
            logger.warning(
                f"cannot verify attestation: no public key for issuer {attestation.issuer}"
            )
            return False

        # Rebuild the signed message
        message = self._attestation_message(
            attestation.subject,
            attestation.public_key,
            attestation.issued_at,
        )

        return self.verify_signature(issuer_pub, message, attestation.signature)

    def create_presence_signature(self, designation: str, timestamp: float) -> str:
        """Sign a heartbeat/presence announcement.

        Proves this agent controls the private key for its designation.
        Compatible with AID PKA handshake concept.
        """
        message = self._presence_message(designation, timestamp)
        return self.sign_message(designation, message)

    def verify_presence_signature(
        self,
        public_key_hex: str,
        designation: str,
        timestamp: float,
        signature_hex: str,
        max_age: float = 300.0,
    ) -> bool:
        """Verify a signed presence announcement.

        Checks signature validity and timestamp freshness (within max_age
        seconds, default 300s — matches AID's 300-second window).
        """
        # Check timestamp freshness
        age = abs(time.time() - timestamp)
        if age > max_age:
            return False

        message = self._presence_message(designation, timestamp)
        return self.verify_signature(public_key_hex, message, signature_hex)

    def publish_coordinator_key(self, designation: str) -> None:
        """Publish coordinator's public key so all agents can verify attestations."""
        pub_hex = self._get_public_key_hex(designation)
        if pub_hex:
            self._storage.save_coordinator_pub(pub_hex)
            logger.info(f"published coordinator public key for {designation}")

    # --- Internal helpers ---

    def _get_public_key_hex(self, designation: str) -> Optional[str]:
        """Get a designation's public key hex from cache or storage."""
        if designation in self._verify_keys:
            return self._verify_keys[designation].encode().hex()
        # Try storage (designation-specific key file)
        pub_hex = self._storage.load_public_key(designation)
        if pub_hex:
            try:
                vk = VerifyKey(bytes.fromhex(pub_hex))
                self._verify_keys[designation] = vk
                return pub_hex
            except Exception:
                pass
        return None

    def _get_coordinator_public_key_hex(self) -> Optional[str]:
        """Get the coordinator's public key for attestation verification."""
        coord_pub = self._storage.load_coordinator_pub()
        if coord_pub:
            try:
                VerifyKey(bytes.fromhex(coord_pub))
                return coord_pub
            except Exception:
                pass
        return None

    @staticmethod
    def _attestation_message(
        subject: str, public_key_hex: str, issued_at: float
    ) -> bytes:
        """Build the canonical message for attestation signing."""
        ts_bytes = struct.pack(">d", issued_at)
        return subject.encode() + b":" + public_key_hex.encode() + b":" + ts_bytes

    @staticmethod
    def _presence_message(designation: str, timestamp: float) -> bytes:
        """Build the canonical message for presence signing."""
        ts_bytes = struct.pack(">d", timestamp)
        return b"presence:" + designation.encode() + b":" + ts_bytes
