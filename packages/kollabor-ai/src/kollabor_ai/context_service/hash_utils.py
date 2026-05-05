"""Hash utilities for ContextService.

Uses blake2b from stdlib (no new dependencies). Fast enough for
this use case (hashing ~50KB file content per read).
"""

import hashlib


def compute_hash(content: bytes) -> str:
    """Compute a content hash.

    Uses blake2b with 8-byte digest, returned as 16 hex chars.
    Non-cryptographic (good enough for dedup, not for security).

    Args:
        content: Raw bytes to hash.

    Returns:
        16-character hex string.
    """
    return hashlib.blake2b(content, digest_size=8).hexdigest()
