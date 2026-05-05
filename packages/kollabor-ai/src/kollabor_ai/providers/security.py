"""
Secure API key storage with 4-tier fallback system.

Provides enterprise-grade key storage with graceful degradation:
- Tier 1: OS native keyring (Keychain/DPAPI/Secret Service)
- Tier 2: Encrypted file storage (AES-256-GCM)
- Tier 3: Environment variables (read-only)
- Tier 4: Plaintext storage (opt-in only, development only)

Also includes:
- Deep recursive logging redaction
- URL validation with allowlist
- Thread-safe operations

Critical for enterprise environments where keyring may be unavailable
(Docker containers, CI/CD, headless servers, air-gapped systems).
"""

import asyncio
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict, Optional, Set
from urllib.parse import urlparse

# Cryptography imports for Tier 2 encrypted file storage
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives.hashes import SHA256  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore[import-not-found]

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False

# Keyring imports for Tier 1 OS keyring
try:
    import keyring
    from keyring.errors import KeyringError

    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    KeyringError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# =============================================================================
# TIER 1: OS Native Keyring (Secure - Recommended)
# =============================================================================


class APIKeyManager:
    """
    API key manager using OS native keyring.

    Supported backends:
    - macOS: Keychain
    - Windows: Credential Manager (DPAPI)
    - Linux: Secret Service (Gnome Keyring, KWallet)

    Thread-safe with asyncio.Lock for concurrent operations.
    """

    SERVICE_NAME = "kollab"

    def __init__(self):
        """
        Initialize OS keyring manager.

        Raises:
            RuntimeError: If keyring library is not available
        """
        if not KEYRING_AVAILABLE:
            raise RuntimeError(
                "keyring library not available. " "Install with: pip install keyring"
            )

        # Verify keyring backend is available
        try:
            backend = keyring.get_keyring()
            logger.debug(f"Using keyring backend: {backend.__class__.__name__}")
        except Exception as e:
            raise RuntimeError(
                f"OS keyring not available: {e}\n"
                "Install keyring backend: pip install keyring"
            )

        # Thread-safe lock
        self._lock = asyncio.Lock()

    async def store_key(self, profile_name: str, api_key: str) -> None:
        """
        Store API key in OS keyring (thread-safe).

        Args:
            profile_name: Profile identifier (e.g., "openai-gpt4")
            api_key: API key to store

        Raises:
            RuntimeError: If storage fails
        """
        async with self._lock:
            try:
                keyring.set_password(self.SERVICE_NAME, profile_name, api_key)
                logger.info(f"Stored API key in OS keyring for profile: {profile_name}")
            except KeyringError as e:
                raise RuntimeError(
                    f"Failed to store API key in keyring: {e}\n"
                    "Check OS keyring permissions."
                ) from e

    async def get_key(self, profile_name: str) -> Optional[str]:
        """
        Retrieve API key from OS keyring (thread-safe).

        Args:
            profile_name: Profile identifier

        Returns:
            API key or None if not found
        """
        async with self._lock:
            try:
                return keyring.get_password(self.SERVICE_NAME, profile_name)
            except KeyringError as e:
                logger.error(f"Failed to retrieve key for {profile_name}: {e}")
                return None

    async def delete_key(self, profile_name: str) -> bool:
        """
        Delete API key from OS keyring (thread-safe).

        Args:
            profile_name: Profile identifier

        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            try:
                keyring.delete_password(self.SERVICE_NAME, profile_name)
                logger.info(
                    f"Deleted API key from OS keyring for profile: {profile_name}"
                )
                return True
            except keyring.errors.PasswordDeleteError:
                return False
            except KeyringError as e:
                logger.error(f"Failed to delete key for {profile_name}: {e}")
                return False


# =============================================================================
# TIER 2: Encrypted File Storage (Secure - Fallback)
# =============================================================================


class EncryptedFileKeyStorage:
    """
    Encrypted file storage backend using AES-256-GCM.

    Use cases:
    - Docker containers (minimal base images)
    - CI/CD environments (headless)
    - Kubernetes pods
    - Air-gapped systems

    Security:
    - AES-256-GCM encryption
    - PBKDF2 key derivation (100,000 iterations)
    - Secure file permissions (0o600)
    - Atomic writes (temp file + rename)

    Requires cryptography library: pip install cryptography
    """

    def __init__(self, storage_path: Path, password: str):
        """
        Initialize encrypted storage.

        Args:
            storage_path: Path to encrypted keys file
            password: Encryption password (from env var or user prompt)

        Raises:
            RuntimeError: If cryptography library is not available
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise RuntimeError(
                "cryptography library not available. "
                "Install with: pip install cryptography"
            )

        self.storage_path = storage_path
        self.password = password
        self._lock = asyncio.Lock()

    def _derive_key(self, salt: bytes) -> bytes:
        """
        Derive encryption key from password using PBKDF2.

        Args:
            salt: Random salt for key derivation

        Returns:
            256-bit encryption key
        """
        kdf = PBKDF2HMAC(
            algorithm=SHA256(),
            length=32,  # 256 bits for AES-256
            salt=salt,
            iterations=100_000,  # OWASP recommendation
        )
        return bytes(kdf.derive(self.password.encode("utf-8")))

    def _load_keystore(self) -> Dict[str, str]:
        """
        Load and decrypt keystore from disk.

        Returns:
            Dictionary of profile_name -> api_key

        Raises:
            RuntimeError: If decryption fails
        """
        if not self.storage_path.exists():
            return {}

        try:
            with open(self.storage_path, "rb") as f:
                data = f.read()

            # Format: salt (16 bytes) + nonce (12 bytes) + ciphertext (+ tag)
            salt = data[:16]
            nonce = data[16:28]
            ciphertext = data[28:]

            # Derive key and decrypt
            key = self._derive_key(salt)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)

            return dict(json.loads(plaintext.decode("utf-8")))

        except Exception as e:
            logger.error(f"Failed to decrypt keystore: {e}")
            raise RuntimeError(
                "Failed to decrypt keys. "
                "Check KOLLAB_KEY_ENCRYPTION_PASSWORD or delete keystore file."
            ) from e

    def _save_keystore(self, keystore: Dict[str, str]) -> None:
        """
        Encrypt and save keystore to disk (atomic write).

        Args:
            keystore: Dictionary of profile_name -> api_key
        """
        # Generate random salt and nonce
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)

        # Derive key and encrypt
        key = self._derive_key(salt)
        aesgcm = AESGCM(key)
        plaintext = json.dumps(keystore).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Atomic write: temp file + rename
        temp_path = self.storage_path.with_suffix(".tmp")
        try:
            with open(temp_path, "wb") as f:
                # Write: salt + nonce + ciphertext (includes tag)
                f.write(salt + nonce + ciphertext)

            # Set secure permissions BEFORE rename
            temp_path.chmod(0o600)

            # Atomic rename (prevents corruption)
            temp_path.replace(self.storage_path)

        finally:
            # Clean up temp file if something went wrong
            if temp_path.exists():
                temp_path.unlink()

    async def store_key(self, profile_name: str, api_key: str) -> None:
        """
        Store encrypted API key (thread-safe).

        Args:
            profile_name: Profile identifier
            api_key: API key to store
        """
        async with self._lock:
            keystore = self._load_keystore()
            keystore[profile_name] = api_key
            self._save_keystore(keystore)
            logger.info(f"Stored encrypted key for profile: {profile_name}")

    async def get_key(self, profile_name: str) -> Optional[str]:
        """
        Retrieve decrypted API key (thread-safe).

        Args:
            profile_name: Profile identifier

        Returns:
            API key or None if not found
        """
        async with self._lock:
            keystore = self._load_keystore()
            return keystore.get(profile_name)

    async def delete_key(self, profile_name: str) -> bool:
        """
        Delete API key (thread-safe).

        Args:
            profile_name: Profile identifier

        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            keystore = self._load_keystore()
            if profile_name in keystore:
                del keystore[profile_name]
                self._save_keystore(keystore)
                logger.info(f"Deleted encrypted key for profile: {profile_name}")
                return True
            return False


# =============================================================================
# TIER 3: Environment Variables Only (Development/CI)
# =============================================================================


class EnvironmentKeyStorage:
    """
    Environment variable-only storage (read-only, no persistence).

    Use cases:
    - CI/CD pipelines
    - Development environments
    - Temporary testing

    WARNING: Keys are not persisted. This is insecure for production.
    """

    def __init__(self):
        """Initialize environment-only storage."""
        logger.warning(
            "Using environment variable storage for API keys. "
            "Keys are not persisted. This is for CI/CD and development only."
        )

    async def store_key(self, profile_name: str, api_key: str) -> None:
        """
        Cannot store in env-only mode.

        Args:
            profile_name: Profile identifier
            api_key: API key (ignored)

        Raises:
            RuntimeError: Always raises (env-only is read-only)
        """
        raise RuntimeError(
            "Cannot persist keys in environment-only mode. "
            "Set keys via OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "or AZURE_OPENAI_API_KEY environment variables."
        )

    async def get_key(self, profile_name: str) -> Optional[str]:
        """
        Get key from environment variables.

        Args:
            profile_name: Profile identifier

        Returns:
            API key from environment or None
        """
        # Map profile name to environment variable
        provider = profile_name.split("-")[0] if "-" in profile_name else profile_name

        env_vars = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "azure": "AZURE_OPENAI_API_KEY",
        }

        # Try provider-specific env var
        env_var = env_vars.get(provider.lower())
        if env_var:
            key = os.environ.get(env_var)
            if key:
                logger.debug(f"Retrieved {env_var} from environment")
                return key

        # Try generic profile name
        generic_env = f"{profile_name.upper()}_API_KEY"
        return os.environ.get(generic_env)

    async def delete_key(self, profile_name: str) -> bool:
        """
        Cannot delete from environment.

        Args:
            profile_name: Profile identifier

        Returns:
            False (environment is read-only)
        """
        return False


# =============================================================================
# TIER 4: Plaintext Storage (Development Only - Discouraged)
# =============================================================================


class PlaintextKeyStorage:
    """
    Plaintext storage (INSECURE - development only).

    WARNING: API keys are stored WITHOUT encryption.
    This is INSECURE and should ONLY be used for local development.
    NEVER use in production or commit to version control.

    Requires explicit opt-in via KOLLAB_ALLOW_PLAINTEXT_KEYS=true
    """

    def __init__(self, storage_path: Path):
        """
        Initialize plaintext storage.

        Args:
            storage_path: Path to plaintext keys file

        Raises:
            RuntimeError: If opt-in not confirmed
        """
        if not os.environ.get("KOLLAB_ALLOW_PLAINTEXT_KEYS", "").lower() == "true":
            raise RuntimeError(
                "Plaintext key storage requires explicit opt-in.\n"
                "Set environment variable: export KOLLAB_ALLOW_PLAINTEXT_KEYS=true\n"
                "WARNING: This is INSECURE and should ONLY be used for development."
            )

        self.storage_path = storage_path
        self._lock = asyncio.Lock()

        # Show scary warning
        logger.warning(
            "\n" + "=" * 70 + "\n"
            "WARNING: PLAINTEXT KEY STORAGE ENABLED\n"
            "API keys are stored WITHOUT encryption.\n"
            "This is INSECURE and should ONLY be used for development.\n"
            "NEVER use in production or commit to version control.\n" + "=" * 70
        )

    def _load_keystore(self) -> Dict[str, str]:
        """Load keystore from plaintext file."""
        if not self.storage_path.exists():
            return {}

        try:
            with open(self.storage_path, "r") as f:
                return dict(json.load(f))
        except Exception as e:
            logger.error(f"Failed to load plaintext keystore: {e}")
            return {}

    def _save_keystore(self, keystore: Dict[str, str]) -> None:
        """Save keystore to plaintext file (atomic write)."""
        # Atomic write: temp file + rename
        temp_path = self.storage_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w") as f:
                json.dump(keystore, f, indent=2)

            # Set restrictive permissions
            temp_path.chmod(0o600)

            # Atomic rename
            temp_path.replace(self.storage_path)

        finally:
            if temp_path.exists():
                temp_path.unlink()

    async def store_key(self, profile_name: str, api_key: str) -> None:
        """
        Store plaintext API key (INSECURE).

        Args:
            profile_name: Profile identifier
            api_key: API key to store
        """
        async with self._lock:
            keystore = self._load_keystore()
            keystore[profile_name] = api_key
            self._save_keystore(keystore)
            logger.warning(f"Stored PLAINTEXT key for {profile_name} (INSECURE)")

    async def get_key(self, profile_name: str) -> Optional[str]:
        """
        Retrieve plaintext API key.

        Args:
            profile_name: Profile identifier

        Returns:
            API key or None if not found
        """
        async with self._lock:
            keystore = self._load_keystore()
            return keystore.get(profile_name)

    async def delete_key(self, profile_name: str) -> bool:
        """
        Delete plaintext API key.

        Args:
            profile_name: Profile identifier

        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            keystore = self._load_keystore()
            if profile_name in keystore:
                del keystore[profile_name]
                self._save_keystore(keystore)
                logger.info(f"Deleted plaintext key for {profile_name}")
                return True
            return False


# =============================================================================
# API Key Loader with 4-Tier Fallback
# =============================================================================


class APIKeyLoader:
    """
    Loads API keys with 4-tier fallback system.

    Priority order:
    1. Environment variables (highest priority, overrides all)
    2. OS keyring (secure, recommended)
    3. Encrypted file storage (secure fallback)
    4. Plaintext storage (development only, requires opt-in)

    Automatic migration from plaintext → keyring or encrypted file.
    """

    def __init__(
        self,
        key_manager: Optional[APIKeyManager] = None,
        encrypted_storage: Optional[EncryptedFileKeyStorage] = None,
        plaintext_storage: Optional[PlaintextKeyStorage] = None,
    ):
        """
        Initialize key loader with available backends.

        Args:
            key_manager: Tier 1 OS keyring manager (optional)
            encrypted_storage: Tier 2 encrypted file storage (optional)
            plaintext_storage: Tier 4 plaintext storage (optional)
        """
        self.key_manager = key_manager
        self.encrypted_storage = encrypted_storage
        self.plaintext_storage = plaintext_storage

    async def load_api_key(
        self, profile: Dict[str, Any], provider: str
    ) -> Optional[str]:
        """
        Load API key with 4-tier fallback chain.

        Priority:
        1. Environment variable (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)
        2. OS keyring (if available)
        3. Encrypted file storage (if available)
        4. Plaintext storage (if available, requires opt-in)
        5. Profile config (legacy, auto-migrate)

        Args:
            profile: Profile configuration dict
            provider: Provider type (openai, anthropic, azure_openai)

        Returns:
            API key or None if not found in any tier
        """
        profile_name = profile.get("name", "unknown")

        # Tier 3: Environment variables (highest priority)
        env_var = self._get_env_var_name(provider)
        env_key = os.environ.get(env_var)
        if env_key:
            logger.debug(f"Using API key from {env_var} environment variable")
            return env_key

        # Tier 1: OS keyring
        if self.key_manager:
            keyring_key = await self.key_manager.get_key(profile_name)
            if keyring_key:
                logger.debug(f"Using API key from OS keyring for {profile_name}")
                return keyring_key

        # Tier 2: Encrypted file storage
        if self.encrypted_storage:
            encrypted_key = await self.encrypted_storage.get_key(profile_name)
            if encrypted_key:
                logger.debug(f"Using API key from encrypted file for {profile_name}")
                return encrypted_key

        # Tier 4: Plaintext storage (development only)
        if self.plaintext_storage:
            plaintext_key = await self.plaintext_storage.get_key(profile_name)
            if plaintext_key:
                logger.warning(
                    f"Using API key from PLAINTEXT storage for {profile_name}. "
                    "Consider migrating to encrypted storage."
                )
                return plaintext_key

        # Legacy: Profile config (auto-migrate to keyring)
        config_key = profile.get("api_key")
        if config_key:
            logger.warning(
                f"API key for {profile_name} found in config file (plaintext). "
                "Migrating to OS keyring..."
            )
            await self._migrate_key(profile_name, config_key)
            return str(config_key)

        # No key found
        logger.error(f"No API key found for profile: {profile_name}")
        return None

    async def _migrate_key(self, profile_name: str, api_key: str) -> None:
        """
        Migrate plaintext key from config to secure storage.

        Args:
            profile_name: Profile identifier
            api_key: API key to migrate
        """
        # Try to migrate to keyring first
        if self.key_manager:
            try:
                await self.key_manager.store_key(profile_name, api_key)
                logger.info(f"Migrated {profile_name} API key to OS keyring")
                return
            except Exception as e:
                logger.warning(f"Failed to migrate to keyring: {e}")

        # Fallback to encrypted file storage
        if self.encrypted_storage:
            try:
                await self.encrypted_storage.store_key(profile_name, api_key)
                logger.info(
                    f"Migrated {profile_name} API key to encrypted file storage"
                )
                return
            except Exception as e:
                logger.warning(f"Failed to migrate to encrypted storage: {e}")

        logger.error(
            f"Failed to migrate {profile_name} API key to secure storage. "
            "Key remains in config file (plaintext)."
        )

    def _get_env_var_name(self, provider: str) -> str:
        """
        Get environment variable name for provider.

        Args:
            provider: Provider type

        Returns:
            Environment variable name
        """
        env_vars = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "azure_openai": "AZURE_OPENAI_API_KEY",
        }
        return env_vars.get(provider, f"{provider.upper()}_API_KEY")


# =============================================================================
# URL Validation with Allowlist
# =============================================================================


class URLValidator:
    """
    Validates API endpoint URLs with allowlist.

    Prevents phishing attacks by checking URLs against an allowlist.
    Requires HTTPS for all non-localhost endpoints.

    Custom hosts can be added via KOLLAB_ALLOWED_API_HOSTS env var.
    """

    # Default allowlist
    DEFAULT_ALLOWED_HOSTS = {
        "api.openai.com",
        "api.anthropic.com",
        "localhost",
        "127.0.0.1",
    }

    @classmethod
    def get_allowed_hosts(cls) -> Set[str]:
        """
        Get allowed hosts from config + environment.

        Returns:
            Set of allowed hostnames
        """
        allowed = set(cls.DEFAULT_ALLOWED_HOSTS)

        # Allow custom hosts via environment variable
        custom_hosts = os.environ.get("KOLLAB_ALLOWED_API_HOSTS", "")
        if custom_hosts:
            allowed.update(host.strip() for host in custom_hosts.split(","))

        return allowed

    @classmethod
    def validate_url(cls, url: str, provider: str) -> bool:
        """
        Validate URL is safe to use.

        Args:
            url: URL to validate
            provider: Provider name (for error messages)

        Returns:
            True if valid

        Raises:
            ValueError: If URL is invalid or not in allowlist
        """
        parsed = urlparse(url)

        # Check allowlist first (before HTTPS check)
        allowed_hosts = cls.get_allowed_hosts()

        # Extract hostname from netloc (remove port if present)
        hostname = parsed.netloc.split(":")[0]

        # Localhost and 127.0.0.1 are always allowed (any port)
        is_localhost = hostname in {"localhost", "127.0.0.1"}

        # Must be in allowlist (or localhost)
        if not is_localhost and hostname not in allowed_hosts:
            raise ValueError(
                f"API endpoint '{parsed.netloc}' is not in the allowlist.\n"
                f"This may be a phishing attempt to steal your API key.\n"
                f"Allowed hosts: {', '.join(sorted(allowed_hosts))}\n\n"
                f"To add custom hosts, set environment variable:\n"
                f'export KOLLAB_ALLOWED_API_HOSTS="{parsed.netloc},other-host.com"'
            )

        # Must use HTTPS (except localhost)
        if parsed.scheme != "https" and not is_localhost:
            raise ValueError(
                f"API endpoint must use HTTPS. Got: {parsed.scheme}://{parsed.netloc}\n"
                f"This prevents API key theft via man-in-the-middle attacks."
            )

        return True

    @classmethod
    def validate_and_normalize(cls, url: str, provider: str) -> str:
        """
        Validate and normalize URL.

        Args:
            url: URL to validate
            provider: Provider name

        Returns:
            Normalized URL (trailing slash removed)
        """
        cls.validate_url(url, provider)

        # Normalize: remove trailing slashes
        return url.rstrip("/")


# =============================================================================
# Deep Recursive Logging Redaction
# =============================================================================


class LoggingRedactor:
    """
    Deep recursive redaction of sensitive data from logs.

    Redacts:
    - API keys (sk-*, sk-proj-*, sk-ant-*)
    - Bearer tokens
    - Authorization headers
    - API key fields in JSON
    - Passwords and secrets

    Handles all data types: strings, dicts, lists, tuples, exceptions, objects.
    """

    # Comprehensive regex patterns
    PATTERNS = [
        # OpenAI API keys
        (re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "[REDACTED-OPENAI-KEY]"),
        (re.compile(r"sk-proj-[a-zA-Z0-9_-]{20,}"), "[REDACTED-OPENAI-PROJECT-KEY]"),
        # Anthropic API keys
        (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[REDACTED-ANTHROPIC-KEY]"),
        # Bearer tokens
        (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}"), "Bearer [REDACTED]"),
        # Authorization headers
        (
            re.compile(r"Authorization:\s*[^\s]+", re.IGNORECASE),
            "Authorization: [REDACTED]",
        ),
        (
            re.compile(r'"authorization":\s*"[^"]+"', re.IGNORECASE),
            '"authorization": "[REDACTED]"',
        ),
        # API key fields in JSON/dicts (any value)
        (
            re.compile(r'"api[_-]?key":\s*"[^"]+"', re.IGNORECASE),
            '"api_key": "[REDACTED]"',
        ),
        # Tokens and secrets (any value 8+ chars)
        (
            re.compile(r'"access[_-]?token":\s*"[^"]{8,}"', re.IGNORECASE),
            '"access_token": "[REDACTED]"',
        ),
        (
            re.compile(r'"refresh[_-]?token":\s*"[^"]{8,}"', re.IGNORECASE),
            '"refresh_token": "[REDACTED]"',
        ),
        (re.compile(r'"token":\s*"[^"]{8,}"', re.IGNORECASE), '"token": "[REDACTED]"'),
        (
            re.compile(r'"client[_-]?secret":\s*"[^"]{8,}"', re.IGNORECASE),
            '"client_secret": "[REDACTED]"',
        ),
        (
            re.compile(r'"secret":\s*"[^"]{8,}"', re.IGNORECASE),
            '"secret": "[REDACTED]"',
        ),
        (
            re.compile(r'"password":\s*"[^"]+"', re.IGNORECASE),
            '"password": "[REDACTED]"',
        ),
        # URLs with embedded keys
        (re.compile(r"(https?://[^/]+/)sk-[a-zA-Z0-9_-]{20,}"), r"\1[REDACTED-KEY]"),
        (
            re.compile(r"(https?://[^/]+/)sk-proj-[a-zA-Z0-9_-]{20,}"),
            r"\1[REDACTED-KEY]",
        ),
        (
            re.compile(r"(https?://[^/]+/)sk-ant-[a-zA-Z0-9_-]{20,}"),
            r"\1[REDACTED-KEY]",
        ),
    ]

    @classmethod
    def redact(cls, obj: Any) -> Any:
        """
        Recursively redact sensitive data from any object.

        Args:
            obj: Any object to redact

        Returns:
            Redacted version (same type as input)
        """
        if isinstance(obj, str):
            return cls._redact_string(obj)

        elif isinstance(obj, dict):
            return {key: cls.redact(value) for key, value in obj.items()}

        elif isinstance(obj, list):
            return [cls.redact(item) for item in obj]

        elif isinstance(obj, tuple):
            redacted_items = tuple(cls.redact(item) for item in obj)
            if hasattr(obj, "_fields"):
                try:
                    return type(obj)(*redacted_items)
                except TypeError:
                    return redacted_items
            if type(obj) is tuple:
                return redacted_items
            try:
                return type(obj)(redacted_items)
            except TypeError:
                return redacted_items

        elif isinstance(obj, Exception):
            return cls._redact_exception(obj)

        elif hasattr(obj, "__dict__"):
            return cls._redact_object(obj)

        else:
            # Primitive types (int, float, bool, None)
            return obj

    @classmethod
    def _redact_string(cls, text: str) -> str:
        """
        Apply all regex patterns to string.

        Args:
            text: String to redact

        Returns:
            Redacted string
        """
        for pattern, replacement in cls.PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @classmethod
    def _redact_exception(cls, exc: Exception) -> Exception:
        """
        Redact exception message and args.

        Args:
            exc: Exception to redact

        Returns:
            New exception with redacted message
        """
        # Create new exception with redacted message
        exc_type = type(exc)
        redacted_msg = cls._redact_string(str(exc))

        try:
            # Try to create new exception with redacted message
            redacted_exc = exc_type(redacted_msg)
            # Preserve original exception chain
            redacted_exc.__cause__ = exc.__cause__
            redacted_exc.__context__ = exc.__context__
            return redacted_exc
        except Exception:
            # If exception creation fails, return string representation
            return Exception(redacted_msg)

    @classmethod
    def _redact_object(cls, obj: Any) -> Dict[str, Any]:
        """
        Redact object __dict__ attributes.

        Args:
            obj: Object to redact

        Returns:
            Dictionary with redacted attributes
        """
        # Don't modify original object
        redacted_dict = {}
        for key, value in obj.__dict__.items():
            redacted_dict[key] = cls.redact(value)

        # Return dict representation (safer than modifying object)
        return {"__type__": type(obj).__name__, "__dict__": redacted_dict}


class RedactingLogFilter(logging.Filter):
    """
    Logging filter with deep recursive redaction.

    Automatically redacts sensitive data from all log records.
    Install on root logger to protect all log output.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact all sensitive data from log record.

        Args:
            record: Log record to filter

        Returns:
            True (always log, but redacted)
        """
        # Redact message
        if isinstance(record.msg, str):
            record.msg = LoggingRedactor.redact(record.msg)
        elif record.msg is not None:
            record.msg = LoggingRedactor.redact(record.msg)

        # Redact args (recursively)
        if record.args:
            record.args = tuple(LoggingRedactor.redact(arg) for arg in record.args)

        # Redact exception info
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info  # type: ignore[assignment]
            if exc_value:
                redacted_exc = LoggingRedactor.redact(exc_value)
                record.exc_info = (exc_type, redacted_exc, exc_tb)  # type: ignore[assignment]

        return True


def setup_secure_logging() -> None:
    """
    Set up secure logging with redaction on all loggers.

    Call during application initialization to protect all log output.

    Installs RedactingLogFilter on:
    - Root logger
    - Provider loggers (openai, anthropic)
    - HTTP loggers (httpx, httpcore, urllib3)
    """
    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addFilter(RedactingLogFilter())

    # Add to specific provider loggers
    for logger_name in ["openai", "anthropic", "httpx", "httpcore", "urllib3"]:
        logger = logging.getLogger(logger_name)
        logger.addFilter(RedactingLogFilter())

    logger.info("Secure logging with redaction enabled")


# =============================================================================
# Thread-Safe Singleton Instances
# =================================================================rijk

_key_manager: Optional[APIKeyManager] = None
_key_manager_lock: Optional[asyncio.Lock] = None


async def get_key_manager() -> APIKeyManager:
    """
    Get thread-safe singleton key manager.

    Returns:
        Shared APIKeyManager instance
    """
    global _key_manager, _key_manager_lock

    if _key_manager_lock is None:
        _key_manager_lock = asyncio.Lock()

    if _key_manager is not None:
        return _key_manager

    async with _key_manager_lock:
        if _key_manager is None:
            _key_manager = APIKeyManager()
        return _key_manager
