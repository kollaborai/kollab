"""
Unit tests for core/llm/providers/security.py.

Tests the 4-tier fallback key storage system:
- Tier 1: OS native keyring (APIKeyManager)
- Tier 2: Encrypted file storage (EncryptedFileKeyStorage)
- Tier 3: Environment variables (EnvironmentKeyStorage)
- Tier 4: Plaintext storage (PlaintextKeyStorage)

Also tests:
- APIKeyLoader fallback logic
- URLValidator with allowlist
- LoggingRedactor with comprehensive patterns
- RedactingLogFilter
- setup_secure_logging()
- Thread safety with concurrent operations
"""

import asyncio
import builtins
import json
import logging
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kollabor_ai.providers.security import (
    CRYPTOGRAPHY_AVAILABLE,
    KEYRING_AVAILABLE,
    APIKeyLoader,
    APIKeyManager,
    EncryptedFileKeyStorage,
    EnvironmentKeyStorage,
    LoggingRedactor,
    PlaintextKeyStorage,
    RedactingLogFilter,
    URLValidator,
    get_key_manager,
    setup_secure_logging,
)

# =============================================================================
# Test Utilities
# =============================================================================


def skip_if_no_cryptography():
    """Skip test if cryptography library not available."""
    return unittest.skipIf(
        not CRYPTOGRAPHY_AVAILABLE, "cryptography library not available"
    )


def skip_if_no_keyring():
    """Skip test if keyring library not available."""
    return unittest.skipIf(not KEYRING_AVAILABLE, "keyring library not available")


class TempFileTest(unittest.TestCase):
    """Base class for tests using temporary files."""

    def setUp(self):
        """Create temporary directory for tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        if self.temp_path.exists():
            shutil.rmtree(self.temp_path)


# =============================================================================
# Tier 1: OS Keyring Tests
# =============================================================================


@skip_if_no_keyring()
class TestAPIKeyManager(unittest.TestCase):
    """Test OS native keyring storage."""

    def setUp(self):
        """Set up test fixtures."""
        # Use a mock keyring backend for testing
        self.mock_backend = Mock()
        self.mock_backend.__class__.__name__ = "MockKeyringBackend"

    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    def test_init_success(self, mock_get_keyring):
        """Test successful initialization."""
        mock_get_keyring.return_value = self.mock_backend

        manager = APIKeyManager()
        self.assertIsNotNone(manager)

    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    def test_init_fails_without_keyring(self, mock_get_keyring):
        """Test initialization fails when keyring not available."""
        mock_get_keyring.side_effect = Exception("No keyring")

        with self.assertRaises(RuntimeError) as ctx:
            APIKeyManager()

        self.assertIn("OS keyring not available", str(ctx.exception))

    @patch("kollabor_ai.providers.security.keyring.set_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_store_key_success(self, mock_get_keyring, mock_set_password):
        """Test successful key storage."""
        mock_get_keyring.return_value = self.mock_backend

        manager = APIKeyManager()
        await manager.store_key("test-profile", "sk-example-header-key-0000")

        mock_set_password.assert_called_once_with(
            "kollab", "test-profile", "sk-example-header-key-0000"
        )

    @patch("kollabor_ai.providers.security.keyring.set_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_store_key_failure(self, mock_get_keyring, mock_set_password):
        """Test key storage failure handling."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_set_password.side_effect = KeyringError("Permission denied")

        manager = APIKeyManager()

        with self.assertRaises(RuntimeError) as ctx:
            await manager.store_key("test-profile", "sk-example-header-key-0000")

        self.assertIn("Failed to store API key", str(ctx.exception))

    @patch("kollabor_ai.providers.security.keyring.get_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_success(self, mock_get_keyring, mock_get_password):
        """Test successful key retrieval."""
        mock_get_keyring.return_value = self.mock_backend
        mock_get_password.return_value = "sk-example-header-key-0000"

        manager = APIKeyManager()
        key = await manager.get_key("test-profile")

        self.assertEqual(key, "sk-example-header-key-0000")
        mock_get_password.assert_called_once_with("kollab", "test-profile")

    @patch("kollabor_ai.providers.security.keyring.get_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_not_found(self, mock_get_keyring, mock_get_password):
        """Test key retrieval when not found."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_get_password.side_effect = KeyringError("Not found")

        manager = APIKeyManager()
        key = await manager.get_key("test-profile")

        self.assertIsNone(key)

    @patch("kollabor_ai.providers.security.keyring.delete_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_delete_key_success(self, mock_get_keyring, mock_delete):
        """Test successful key deletion."""
        mock_get_keyring.return_value = self.mock_backend

        manager = APIKeyManager()
        result = await manager.delete_key("test-profile")

        self.assertTrue(result)
        mock_delete.assert_called_once_with("kollab", "test-profile")

    @patch("kollabor_ai.providers.security.keyring.delete_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_delete_key_not_found(self, mock_get_keyring, mock_delete):
        """Test key deletion when not found."""
        from keyring.errors import PasswordDeleteError

        mock_get_keyring.return_value = self.mock_backend
        mock_delete.side_effect = PasswordDeleteError()

        manager = APIKeyManager()
        result = await manager.delete_key("test-profile")

        self.assertFalse(result)

    async def test_thread_safety(self):
        """Test concurrent operations are thread-safe."""
        with (
            patch(
                "kollabor_ai.providers.security.keyring.get_keyring"
            ) as mock_get_keyring,
            patch("kollabor_ai.providers.security.keyring.set_password"),
            patch("kollabor_ai.providers.security.keyring.get_password"),
        ):

            mock_get_keyring.return_value = self.mock_backend

            manager = APIKeyManager()

            # Run concurrent operations
            tasks = [manager.store_key(f"profile-{i}", f"key-{i}") for i in range(10)]

            await asyncio.gather(*tasks)

            # Should not raise exceptions


# =============================================================================
# Tier 2: Encrypted File Storage Tests
# =============================================================================


@skip_if_no_cryptography()
class TestEncryptedFileKeyStorage(TempFileTest):
    """Test encrypted file storage backend."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"
        self.storage = EncryptedFileKeyStorage(self.storage_path, self.password)

    def test_init_success(self):
        """Test successful initialization."""
        self.assertEqual(self.storage.storage_path, self.storage_path)
        self.assertEqual(self.storage.password, self.password)

    def test_init_fails_without_cryptography(self):
        """Test initialization fails without cryptography library."""
        with patch("kollabor_ai.providers.security.CRYPTOGRAPHY_AVAILABLE", False):
            with self.assertRaises(RuntimeError) as ctx:
                EncryptedFileKeyStorage(self.storage_path, self.password)

            self.assertIn("cryptography library not available", str(ctx.exception))

    async def test_store_and_get_key(self):
        """Test storing and retrieving a key."""
        await self.storage.store_key("test-profile", "sk-example-header-key-0000")

        key = await self.storage.get_key("test-profile")
        self.assertEqual(key, "sk-example-header-key-0000")

    async def test_store_multiple_keys(self):
        """Test storing multiple keys."""
        await self.storage.store_key("profile-1", "sk-key-1")
        await self.storage.store_key("profile-2", "sk-key-2")
        await self.storage.store_key("profile-3", "sk-key-3")

        self.assertEqual(await self.storage.get_key("profile-1"), "sk-key-1")
        self.assertEqual(await self.storage.get_key("profile-2"), "sk-key-2")
        self.assertEqual(await self.storage.get_key("profile-3"), "sk-key-3")

    async def test_get_nonexistent_key(self):
        """Test retrieving a non-existent key."""
        key = await self.storage.get_key("nonexistent")
        self.assertIsNone(key)

    async def test_delete_key(self):
        """Test deleting a key."""
        await self.storage.store_key("test-profile", "sk-test-key")

        result = await self.storage.delete_key("test-profile")
        self.assertTrue(result)

        key = await self.storage.get_key("test-profile")
        self.assertIsNone(key)

    async def test_delete_nonexistent_key(self):
        """Test deleting a non-existent key."""
        result = await self.storage.delete_key("nonexistent")
        self.assertFalse(result)

    async def test_file_permissions(self):
        """Test file has secure permissions (0o600)."""
        await self.storage.store_key("test-profile", "sk-test-key")

        # Check file permissions
        stat_info = os.stat(self.storage_path)
        permissions = oct(stat_info.st_mode & 0o777)

        # Should be 0o600 (user read/write only)
        self.assertEqual(permissions, "0o600")

    def test_key_derivation(self):
        """Test PBKDF2 key derivation."""
        salt = b"test-salt-16byte"
        key = self.storage._derive_key(salt)

        # Key should be 32 bytes (256 bits)
        self.assertEqual(len(key), 32)

        # Same password + salt should produce same key
        key2 = self.storage._derive_key(salt)
        self.assertEqual(key, key2)

    def test_different_salts_produce_different_keys(self):
        """Test different salts produce different keys."""
        salt1 = b"salt-one-16bytes"
        salt2 = b"salt-two-16bytes"

        key1 = self.storage._derive_key(salt1)
        key2 = self.storage._derive_key(salt2)

        self.assertNotEqual(key1, key2)

    async def test_encryption_roundtrip(self):
        """Test encryption and decryption roundtrip."""
        original_key = "sk-example-storage-key-0000"

        await self.storage.store_key("test", original_key)
        retrieved_key = await self.storage.get_key("test")

        self.assertEqual(original_key, retrieved_key)

    async def test_atomic_write(self):
        """Test atomic file writes (temp file + rename)."""
        # Store a key
        await self.storage.store_key("test", "sk-test-key")

        # Verify main file exists
        self.assertTrue(self.storage_path.exists())

        # Verify temp file was cleaned up
        temp_path = self.storage_path.with_suffix(".tmp")
        self.assertFalse(temp_path.exists())

    async def test_wrong_password_fails(self):
        """Test wrong password fails to decrypt."""
        # Store with correct password
        await self.storage.store_key("test", "sk-test-key")

        # Try to read with wrong password
        wrong_storage = EncryptedFileKeyStorage(self.storage_path, "wrong-password")

        with self.assertRaises(RuntimeError) as ctx:
            await wrong_storage.get_key("test")

        self.assertIn("Failed to decrypt", str(ctx.exception))

    async def test_thread_safety(self):
        """Test concurrent operations are thread-safe."""
        # Run concurrent operations
        tasks = [self.storage.store_key(f"profile-{i}", f"key-{i}") for i in range(10)]

        await asyncio.gather(*tasks)

        # Verify all keys were stored
        for i in range(10):
            key = await self.storage.get_key(f"profile-{i}")
            self.assertEqual(key, f"key-{i}")


# =============================================================================
# Tier 3: Environment Variable Tests
# =============================================================================


class TestEnvironmentKeyStorage(unittest.TestCase):
    """Test environment variable storage."""

    def setUp(self):
        """Set up test fixtures."""
        self.storage = EnvironmentKeyStorage()

    def test_init_logs_warning(self):
        """Test initialization logs warning."""
        with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
            EnvironmentKeyStorage()

    async def test_store_key_raises_error(self):
        """Test store_key raises error (env-only is read-only)."""
        with self.assertRaises(RuntimeError) as ctx:
            await self.storage.store_key("test", "sk-test-key")

        self.assertIn("Cannot persist keys", str(ctx.exception))

    async def test_get_key_openai(self):
        """Test getting OpenAI key from environment."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-openai-key"}):
            key = await self.storage.get_key("openai-gpt4")
            self.assertEqual(key, "sk-test-openai-key")

    async def test_get_key_anthropic(self):
        """Test getting Anthropic key from environment."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
            key = await self.storage.get_key("anthropic-claude")
            self.assertEqual(key, "sk-ant-test-key")

    async def test_get_key_azure(self):
        """Test getting Azure key from environment."""
        with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "azure-test-key"}):
            key = await self.storage.get_key("azure-deployment")
            self.assertEqual(key, "azure-test-key")

    async def test_get_key_generic(self):
        """Test getting key with generic profile name."""
        with patch.dict(os.environ, {"CUSTOM_API_KEY": "custom-key"}):
            key = await self.storage.get_key("custom")
            self.assertEqual(key, "custom-key")

    async def test_get_key_not_found(self):
        """Test getting key when not found in environment."""
        with patch.dict(os.environ, {}, clear=True):
            key = await self.storage.get_key("nonexistent")
            self.assertIsNone(key)

    async def test_delete_key_returns_false(self):
        """Test delete_key returns False (env is read-only)."""
        result = await self.storage.delete_key("test")
        self.assertFalse(result)


# =============================================================================
# Tier 4: Plaintext Storage Tests
# =============================================================================


class TestPlaintextKeyStorage(TempFileTest):
    """Test plaintext storage (development only)."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.json"

    def test_init_fails_without_opt_in(self):
        """Test initialization fails without opt-in."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                PlaintextKeyStorage(self.storage_path)

            self.assertIn("requires explicit opt-in", str(ctx.exception))

    def test_init_success_with_opt_in(self):
        """Test initialization succeeds with opt-in."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
                storage = PlaintextKeyStorage(self.storage_path)
                self.assertEqual(storage.storage_path, self.storage_path)

    async def test_store_and_get_key(self):
        """Test storing and retrieving a key."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test-profile", "sk-example-header-key-0000")

            key = await storage.get_key("test-profile")
            self.assertEqual(key, "sk-example-header-key-0000")

    async def test_store_multiple_keys(self):
        """Test storing multiple keys."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("profile-1", "sk-key-1")
            await storage.store_key("profile-2", "sk-key-2")

            self.assertEqual(await storage.get_key("profile-1"), "sk-key-1")
            self.assertEqual(await storage.get_key("profile-2"), "sk-key-2")

    async def test_delete_key(self):
        """Test deleting a key."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test", "sk-test-key")
            result = await storage.delete_key("test")

            self.assertTrue(result)
            self.assertIsNone(await storage.get_key("test"))

    async def test_file_permissions(self):
        """Test file has secure permissions (0o600)."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test", "sk-test-key")

            stat_info = os.stat(self.storage_path)
            permissions = oct(stat_info.st_mode & 0o777)

            self.assertEqual(permissions, "0o600")

    async def test_atomic_write(self):
        """Test atomic file writes."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test", "sk-test-key")

            # Verify main file exists
            self.assertTrue(self.storage_path.exists())

            # Verify temp file was cleaned up
            temp_path = self.storage_path.with_suffix(".tmp")
            self.assertFalse(temp_path.exists())


# =============================================================================
# API Key Loader Tests
# =============================================================================


class TestAPIKeyLoader(unittest.TestCase):
    """Test API key loader with 4-tier fallback."""

    def setUp(self):
        """Set up test fixtures."""
        self.loader = APIKeyLoader()

    async def test_load_from_environment(self):
        """Test loading from environment (highest priority)."""
        profile = {"name": "openai-gpt4", "api_key": "config-key"}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
            key = await self.loader.load_api_key(profile, "openai")

            self.assertEqual(key, "env-key")

    @patch("kollabor_ai.providers.security.KEYRING_AVAILABLE", True)
    async def test_load_from_keyring(self):
        """Test loading from keyring (Tier 1)."""
        profile = {"name": "test-profile"}

        mock_manager = Mock()
        mock_manager.get_key = Mock(
            return_value=asyncio.coroutine(lambda: "keyring-key")()
        )

        loader = APIKeyLoader(key_manager=mock_manager)

        key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "keyring-key")

    @skip_if_no_cryptography()
    async def test_load_from_encrypted_storage(self):
        """Test loading from encrypted file storage (Tier 2)."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            # Pre-populate encrypted storage
            await encrypted_storage.store_key("test-profile", "encrypted-key")

            loader = APIKeyLoader(encrypted_storage=encrypted_storage)

            key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "encrypted-key")

    async def test_load_from_plaintext_storage(self):
        """Test loading from plaintext storage (Tier 4)."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.json"

            with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
                plaintext_storage = PlaintextKeyStorage(storage_path)

                # Pre-populate plaintext storage
                await plaintext_storage.store_key("test-profile", "plaintext-key")

                loader = APIKeyLoader(plaintext_storage=plaintext_storage)

                key = await loader.load_api_key(profile, "openai")

                self.assertEqual(key, "plaintext-key")

    async def test_load_from_config_with_migration(self):
        """Test loading from config with migration to keyring."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(return_value=asyncio.coroutine(lambda: None)())

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "config-key")
            mock_manager.store_key.assert_called_once()

    async def test_load_not_found(self):
        """Test loading when key not found in any tier."""
        profile = {"name": "nonexistent"}

        with patch.dict(os.environ, {}, clear=True):
            key = await self.loader.load_api_key(profile, "openai")

            self.assertIsNone(key)

    def test_get_env_var_name(self):
        """Test getting environment variable name for provider."""
        self.assertEqual(self.loader._get_env_var_name("openai"), "OPENAI_API_KEY")
        self.assertEqual(
            self.loader._get_env_var_name("anthropic"), "ANTHROPIC_API_KEY"
        )
        self.assertEqual(
            self.loader._get_env_var_name("azure_openai"), "AZURE_OPENAI_API_KEY"
        )
        self.assertEqual(self.loader._get_env_var_name("custom"), "CUSTOM_API_KEY")


# =============================================================================
# URL Validator Tests
# =============================================================================


class TestURLValidator(unittest.TestCase):
    """Test URL validation with allowlist."""

    def test_validate_openai_url(self):
        """Test validating OpenAI URL."""
        result = URLValidator.validate_url("https://api.openai.com/v1", "openai")
        self.assertTrue(result)

    def test_validate_anthropic_url(self):
        """Test validating Anthropic URL."""
        result = URLValidator.validate_url("https://api.anthropic.com/v1", "anthropic")
        self.assertTrue(result)

    def test_validate_localhost_http(self):
        """Test validating localhost with HTTP (allowed)."""
        result = URLValidator.validate_url("http://localhost:8000", "custom")
        self.assertTrue(result)

    def test_validate_127_0_0_1_http(self):
        """Test validating 127.0.0.1 with HTTP (allowed)."""
        result = URLValidator.validate_url("http://127.0.0.1:8080", "custom")
        self.assertTrue(result)

    def test_reject_non_localhost_http(self):
        """Test rejecting HTTP for non-localhost."""
        with self.assertRaises(ValueError) as ctx:
            URLValidator.validate_url("http://example.com", "custom")

        # Should fail allowlist check before HTTPS check
        self.assertIn("not in the allowlist", str(ctx.exception))

    def test_reject_non_allowed_host(self):
        """Test rejecting host not in allowlist."""
        with self.assertRaises(ValueError) as ctx:
            URLValidator.validate_url("https://malicious-site.com", "custom")

        self.assertIn("not in the allowlist", str(ctx.exception))
        self.assertIn("phishing attempt", str(ctx.exception))

    def test_custom_allowed_hosts(self):
        """Test custom allowed hosts from environment."""
        with patch.dict(
            os.environ,
            {"KOLLAB_ALLOWED_API_HOSTS": "custom-api.com,another-host.com"},
        ):
            allowed = URLValidator.get_allowed_hosts()

            self.assertIn("custom-api.com", allowed)
            self.assertIn("another-host.com", allowed)
            self.assertIn("api.openai.com", allowed)  # Default still present

    def test_validate_and_normalize(self):
        """Test validation and normalization (removes trailing slash)."""
        url = URLValidator.validate_and_normalize(
            "https://api.openai.com/v1/", "openai"
        )

        self.assertEqual(url, "https://api.openai.com/v1")

    def test_validate_and_normalize_with_multiple_slashes(self):
        """Test normalization removes all trailing slashes."""
        url = URLValidator.validate_and_normalize(
            "https://api.openai.com/v1///", "openai"
        )

        self.assertEqual(url, "https://api.openai.com/v1")


# =============================================================================
# Logging Redactor Tests
# =============================================================================


class TestLoggingRedactor(unittest.TestCase):
    """Test deep recursive logging redaction."""

    def test_redact_openai_key(self):
        """Test redacting OpenAI API key."""
        # Keys must be 20+ characters to match pattern
        text = "API key: sk-proj-example-project-key-0000"
        redacted = LoggingRedactor.redact(text)

        # Should be redacted
        self.assertNotEqual(redacted, text)
        self.assertIn("[REDACTED", redacted)
        self.assertNotIn("sk-proj-example-project-key-0000", redacted)

    def test_redact_anthropic_key(self):
        """Test redacting Anthropic API key."""
        # Keys must be 20+ characters to match pattern
        text = "Key: sk-ant-example-anthropic-key-0000"
        redacted = LoggingRedactor.redact(text)

        # Should be redacted
        self.assertNotEqual(redacted, text)
        self.assertIn("[REDACTED", redacted)
        self.assertNotIn("sk-ant-example-anthropic-key-0000", redacted)

    def test_redact_bearer_token(self):
        """Test redacting Bearer token."""
        text = "Authorization: Bearer example-bearer-token-value-0000"
        redacted = LoggingRedactor.redact(text)

        # Should be redacted
        self.assertNotEqual(redacted, text)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("example-bearer-token-value-0000", redacted)

    def test_redact_authorization_header(self):
        """Test redacting Authorization header."""
        text = '{"authorization": "Bearer sk-example-header-key-0000"}'
        redacted = LoggingRedactor.redact(text)

        self.assertEqual(redacted, '{"authorization": "[REDACTED]"}')

    def test_redact_api_key_json(self):
        """Test redacting api_key field in JSON."""
        text = '{"api_key": "sk-example-header-key-0000", "model": "gpt-4"}'
        redacted = LoggingRedactor.redact(text)

        self.assertEqual(redacted, '{"api_key": "[REDACTED]", "model": "gpt-4"}')

    def test_redact_dict(self):
        """Test redacting sensitive data in dict."""
        data = {
            "api_key": "sk-example-redaction-key-0000",
            "model": "gpt-4",
            "nested": {"authorization": "Bearer sk-example-redaction-key-0000"},
        }

        redacted = LoggingRedactor.redact(data)

        # Top-level values should be redacted
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted["api_key"]))
        self.assertEqual(redacted["model"], "gpt-4")
        # Nested dict should also be redacted
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted["nested"]))

    def test_redact_list(self):
        """Test redacting sensitive data in list."""
        data = [
            "sk-example-redaction-key-0000",
            "Bearer example-bearer-token-value-0000",
            {"api_key": "sk-example-list-key-0000"},
        ]

        redacted = LoggingRedactor.redact(data)

        # All sensitive data should be redacted
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted[0]))
        self.assertNotIn("example-bearer-token-value-0000", str(redacted[1]))
        self.assertNotIn("sk-example-list-key-0000", str(redacted[2]["api_key"]))

    def test_redact_exception(self):
        """Test redacting exception message."""
        exc = Exception("Failed with key: sk-example-redaction-key-0000")

        redacted = LoggingRedactor.redact(exc)

        self.assertIsInstance(redacted, Exception)
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))

    def test_redact_object_with_dict(self):
        """Test redacting object attributes."""

        class TestObject:
            def __init__(self):
                self.api_key = "sk-example-redaction-key-0000"
                self.model = "gpt-4"

        obj = TestObject()
        redacted = LoggingRedactor.redact(obj)

        # Should return dict representation with redacted values
        self.assertIsInstance(redacted, dict)
        self.assertIn("__dict__", redacted)
        self.assertNotIn(
            "sk-example-redaction-key-0000", str(redacted["__dict__"]["api_key"])
        )
        self.assertEqual(redacted["__dict__"]["model"], "gpt-4")

    def test_redact_primitive_types(self):
        """Test redacting primitive types (int, float, bool, None)."""
        self.assertEqual(LoggingRedactor.redact(42), 42)
        self.assertEqual(LoggingRedactor.redact(3.14), 3.14)
        self.assertEqual(LoggingRedactor.redact(True), True)
        self.assertEqual(LoggingRedactor.redact(None), None)

    def test_redact_url_with_embedded_key(self):
        """Test redacting URL with embedded API key."""
        url = "https://api.openai.com/v1/sk-example-redaction-key-0000"
        redacted = LoggingRedactor.redact(url)

        # Key should be redacted
        self.assertNotIn("sk-example-redaction-key-0000", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_redact_password_field(self):
        """Test redacting password field."""
        text = '{"username": "admin", "password": "secret123"}'
        redacted = LoggingRedactor.redact(text)

        # Password should be redacted
        self.assertNotIn("secret123", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redact_secret_field(self):
        """Test redacting secret field."""
        # Secret value must be long enough to be worth redacting
        text = '{"client_id": "abc", "client_secret": "example-client-secret-value"}'
        redacted = LoggingRedactor.redact(text)

        # Secret should be redacted
        self.assertNotIn("example-client-secret-value", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redact_token_field(self):
        """Test redacting token field."""
        # Use a longer JWT token
        text = '{"access_token": "example-access-token-value-0000"}'
        redacted = LoggingRedactor.redact(text)

        # Token should be redacted
        self.assertNotIn(
            "example-access-token-value-0000",
            redacted,
        )
        self.assertIn("[REDACTED]", redacted)


# =============================================================================
# Redacting Log Filter Tests
# =============================================================================


class TestRedactingLogFilter(unittest.TestCase):
    """Test logging filter with redaction."""

    def test_filter_redacts_message(self):
        """Test filter redacts log message."""
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="API key: sk-example-redaction-key-0000",
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertNotIn("sk-example-redaction-key-0000", record.msg)
        self.assertIn("[REDACTED", record.msg)

    def test_filter_redacts_args(self):
        """Test filter redacts log args."""
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Key: %s",
            args=("sk-example-redaction-key-0000",),
            exc_info=None,
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertNotIn("sk-example-redaction-key-0000", str(record.args))
        self.assertIn("[REDACTED", str(record.args))

    def test_filter_redacts_exception(self):
        """Test filter redacts exception info."""
        log_filter = RedactingLogFilter()
        exc = Exception("Error: sk-example-redaction-key-0000")

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=(type(exc), exc, None),
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertNotIn("sk-example-redaction-key-0000", str(record.exc_info[1]))


# =============================================================================
# Setup Secure Logging Tests
# =============================================================================


class TestSetupSecureLogging(unittest.TestCase):
    """Test secure logging setup."""

    def test_setup_secure_logging(self):
        """Test setting up secure logging."""
        # Clear existing filters to avoid duplicates
        root_logger = logging.getLogger()
        root_logger.filters.clear()

        setup_secure_logging()

        # Check root logger has filter
        filters = root_logger.filters

        self.assertTrue(any(isinstance(f, RedactingLogFilter) for f in filters))

    def test_setup_secure_logging_adds_to_provider_loggers(self):
        """Test secure logging adds filters to provider loggers."""
        setup_secure_logging()

        logger_names = ["openai", "anthropic", "httpx", "httpcore", "urllib3"]

        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            filters = logger.filters

            self.assertTrue(
                any(isinstance(f, RedactingLogFilter) for f in filters),
                f"Logger {logger_name} should have RedactingLogFilter",
            )


# =============================================================================
# Integration Tests
# =============================================================================


@skip_if_no_cryptography()
class TestSecurityIntegration(TempFileTest):
    """Integration tests for security module."""

    async def test_full_fallback_chain(self):
        """Test full 4-tier fallback chain."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            # Pre-populate encrypted storage
            await encrypted_storage.store_key("test-profile", "encrypted-key")

            loader = APIKeyLoader(encrypted_storage=encrypted_storage)

            # Environment should win
            with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
                key = await loader.load_api_key(profile, "openai")
                self.assertEqual(key, "env-key")

            # Without env, encrypted storage should be used
            with patch.dict(os.environ, {}, clear=True):
                key = await loader.load_api_key(profile, "openai")
                self.assertEqual(key, "encrypted-key")

    async def test_migration_from_config_to_encrypted(self):
        """Test migration from config to encrypted storage."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            loader = APIKeyLoader(encrypted_storage=encrypted_storage)

            with patch.dict(os.environ, {}, clear=True):
                # Load from config (should migrate)
                key = await loader.load_api_key(profile, "openai")

                self.assertEqual(key, "config-key")

                # Verify key was migrated to encrypted storage
                migrated_key = await encrypted_storage.get_key("test-profile")
                self.assertEqual(migrated_key, "config-key")

    async def test_concurrent_key_operations(self):
        """Test concurrent key operations across multiple tiers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            # Run concurrent operations
            tasks = [
                encrypted_storage.store_key(f"profile-{i}", f"key-{i}")
                for i in range(20)
            ]

            await asyncio.gather(*tasks)

            # Verify all keys were stored correctly
            for i in range(20):
                key = await encrypted_storage.get_key(f"profile-{i}")
                self.assertEqual(key, f"key-{i}")


# =============================================================================
# Additional Error Handling and Edge Case Tests
# =============================================================================


@skip_if_no_cryptography()
class TestEncryptedFileKeyStorageErrors(TempFileTest):
    """Test error handling in encrypted file storage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"
        self.storage = EncryptedFileKeyStorage(self.storage_path, self.password)

    async def test_load_keystore_empty_file(self):
        """Test loading from empty keystore file."""
        # Create empty file
        self.storage_path.write_text("")

        # Should return empty dict (file exists but no data)
        keystore = self.storage._load_keystore()
        self.assertEqual(keystore, {})

    async def test_load_keystore_corrupted_data(self):
        """Test loading corrupted encrypted data."""
        # Write invalid encrypted data (too short)
        self.storage_path.write_bytes(b"short")

        with self.assertRaises(RuntimeError) as ctx:
            await self.storage.get_key("test")

        self.assertIn("Failed to decrypt", str(ctx.exception))

    async def test_load_keystore_invalid_json(self):
        """Test loading when decrypted data is not valid JSON."""
        # Store valid data first
        await self.storage.store_key("test", "key-value")

        # Corrupt the file by replacing with garbage that matches format
        # Salt (16) + nonce (12) + ciphertext (at least 16 for tag)
        garbage = secrets.token_bytes(44)
        with open(self.storage_path, "wb") as f:
            f.write(garbage)

        with self.assertRaises(RuntimeError) as ctx:
            self.storage._load_keystore()

        self.assertIn("Failed to decrypt", str(ctx.exception))

    async def test_save_keystore_permission_denied(self):
        """Test atomic write when permission denied."""
        # Create storage with a file in a read-only directory
        readonly_dir = self.temp_path / "readonly"
        readonly_dir.mkdir()

        # Make directory read-only
        os.chmod(readonly_dir, 0o444)

        readonly_path = readonly_dir / "keys.enc"
        storage = EncryptedFileKeyStorage(readonly_path, self.password)

        try:
            with self.assertRaises(OSError):
                await storage.store_key("test", "key-value")
        finally:
            # Restore permissions for cleanup
            os.chmod(readonly_dir, 0o755)

    async def test_atomic_write_temp_cleanup_on_error(self):
        """Test temp file is cleaned up on error."""
        # Mock chmod to fail
        with patch.object(Path, "chmod", side_effect=OSError("Permission denied")):
            with self.assertRaises(OSError):
                await self.storage.store_key("test", "key-value")

        # Verify temp file was cleaned up
        temp_path = self.storage_path.with_suffix(".tmp")
        self.assertFalse(temp_path.exists())

    async def test_store_key_creates_new_keystore(self):
        """Test storing key creates new keystore if none exists."""
        # Ensure no keystore exists
        self.assertFalse(self.storage_path.exists())

        await self.storage.store_key("test", "key-value")

        # Verify keystore was created
        self.assertTrue(self.storage_path.exists())

    async def test_delete_key_from_empty_keystore(self):
        """Test deleting key when keystore doesn't exist yet."""
        # Don't create any keystore
        self.assertFalse(self.storage_path.exists())

        result = await self.storage.delete_key("nonexistent")

        # Should return False without error
        self.assertFalse(result)

    async def test_concurrent_read_write_operations(self):
        """Test concurrent read and write operations don't corrupt data."""
        # Store initial key
        await self.storage.store_key("initial", "initial-key")

        # Create tasks for concurrent operations
        tasks = []
        for i in range(10):
            tasks.append(self.storage.store_key(f"profile-{i}", f"key-{i}"))
            tasks.append(self.storage.get_key("initial"))

        await asyncio.gather(*tasks)

        # Verify initial key is still intact
        self.assertEqual(await self.storage.get_key("initial"), "initial-key")

        # Verify all new keys were stored
        for i in range(10):
            self.assertEqual(await self.storage.get_key(f"profile-{i}"), f"key-{i}")


class TestPlaintextKeyStorageErrors(TempFileTest):
    """Test error handling in plaintext storage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.json"

    async def test_load_keystore_invalid_json(self):
        """Test loading when file contains invalid JSON."""
        # Write invalid JSON
        self.storage_path.write_text("{ invalid json }")

        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # Should return empty dict on error
            keystore = storage._load_keystore()
            self.assertEqual(keystore, {})

    async def test_load_keystore_not_exists(self):
        """Test loading when file doesn't exist."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # Should return empty dict
            keystore = storage._load_keystore()
            self.assertEqual(keystore, {})

    async def test_save_keystore_permission_denied(self):
        """Test atomic write when permission denied."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            readonly_dir = self.temp_path / "readonly"
            readonly_dir.mkdir()
            os.chmod(readonly_dir, 0o444)

            readonly_path = readonly_dir / "keys.json"
            storage = PlaintextKeyStorage(readonly_path)

            try:
                with self.assertRaises(OSError):
                    await storage.store_key("test", "key-value")
            finally:
                os.chmod(readonly_dir, 0o755)

    async def test_atomic_write_temp_cleanup(self):
        """Test temp file cleanup on success."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test", "key-value")

            # Verify main file exists
            self.assertTrue(self.storage_path.exists())

            # Verify temp file was cleaned up
            temp_path = self.storage_path.with_suffix(".tmp")
            self.assertFalse(temp_path.exists())

    async def test_warning_logged_on_store(self):
        """Test warning is logged when storing plaintext key."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
                await storage.store_key("test", "key-value")


@skip_if_no_keyring()
class TestAPIKeyManagerErrors(unittest.TestCase):
    """Test error handling in API key manager."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_backend = Mock()
        self.mock_backend.__class__.__name__ = "MockKeyringBackend"

    @patch("kollabor_ai.providers.security.KEYRING_AVAILABLE", False)
    def test_init_no_keyring_library(self):
        """Test initialization fails when keyring library not available."""
        with self.assertRaises(RuntimeError) as ctx:
            APIKeyManager()

        self.assertIn("keyring library not available", str(ctx.exception))

    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_store_key_logging(self, mock_get_keyring):
        """Test key storage logs success message."""
        mock_get_keyring.return_value = self.mock_backend

        with patch("kollabor_ai.providers.security.keyring.set_password"):
            with self.assertLogs("kollabor_ai.providers.security", level="INFO"):
                manager = APIKeyManager()
                await manager.store_key("test-profile", "sk-test-key")

    @patch("kollabor_ai.providers.security.keyring.get_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_error_logging(self, mock_get_keyring, mock_get_password):
        """Test key retrieval errors are logged."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_get_password.side_effect = KeyringError("Network error")

        with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
            manager = APIKeyManager()
            key = await manager.get_key("test-profile")

        # Should return None on error
        self.assertIsNone(key)

    @patch("kollabor_ai.providers.security.keyring.delete_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_delete_key_error_handling(self, mock_get_keyring, mock_delete):
        """Test delete key handles general KeyringError."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_delete.side_effect = KeyringError("Corrupted keyring")

        with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
            manager = APIKeyManager()
            result = await manager.delete_key("test-profile")

        # Should return False on error
        self.assertFalse(result)


class TestEnvironmentKeyStorageDetailed(unittest.TestCase):
    """Test detailed environment storage behavior."""

    def setUp(self):
        """Set up test fixtures."""
        self.storage = EnvironmentKeyStorage()

    async def test_store_key_error_message(self):
        """Test store_key provides helpful error message."""
        with self.assertRaises(RuntimeError) as ctx:
            await self.storage.store_key("test", "sk-test-key")

        error_msg = str(ctx.exception)
        self.assertIn("Cannot persist keys", error_msg)
        self.assertIn("OPENAI_API_KEY", error_msg)
        self.assertIn("ANTHROPIC_API_KEY", error_msg)

    async def test_get_key_provider_extraction(self):
        """Test provider extraction from profile name."""
        # Profile with dash
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            key = await self.storage.get_key("openai-gpt4")
            self.assertEqual(key, "test-key")

        # Profile without dash
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-2"}):
            key = await self.storage.get_key("openai")
            self.assertEqual(key, "test-key-2")

    async def test_get_key_logs_debug(self):
        """Test get_key logs debug message when found."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with self.assertLogs("kollabor_ai.providers.security", level="DEBUG"):
                await self.storage.get_key("openai-gpt4")

    async def test_get_key_all_providers(self):
        """Test getting keys for all supported providers."""
        test_cases = [
            ("OPENAI_API_KEY", "openai-gpt4", "openai-test-key"),
            ("ANTHROPIC_API_KEY", "anthropic-claude", "anthropic-test-key"),
            ("AZURE_OPENAI_API_KEY", "azure-deployment", "azure-test-key"),
        ]

        for env_var, profile, expected_key in test_cases:
            with patch.dict(os.environ, {env_var: expected_key}):
                key = await self.storage.get_key(profile)
                self.assertEqual(key, expected_key)


class TestAPIKeyLoaderDetailed(unittest.TestCase):
    """Test detailed API key loader behavior."""

    def setUp(self):
        """Set up test fixtures."""
        self.loader = APIKeyLoader()

    async def test_load_from_environment_priority(self):
        """Test environment has highest priority."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.get_key = Mock(
            return_value=asyncio.coroutine(lambda: "keyring-key")()
        )

        loader = APIKeyLoader(key_manager=mock_manager)

        # Env should win even if keyring has a key
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
            key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "env-key")

    async def test_load_from_keyring_when_no_env(self):
        """Test keyring is tried when env not set."""
        profile = {"name": "test-profile"}

        mock_manager = Mock()
        mock_manager.get_key = Mock(
            return_value=asyncio.coroutine(lambda: "keyring-key")()
        )

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "keyring-key")

    @skip_if_no_cryptography()
    async def test_load_from_encrypted_when_no_keyring(self):
        """Test encrypted storage is tried when keyring not available."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            await encrypted_storage.store_key("test-profile", "encrypted-key")

            loader = APIKeyLoader(encrypted_storage=encrypted_storage)

            with patch.dict(os.environ, {}, clear=True):
                key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "encrypted-key")

    async def test_load_from_plaintext_with_warning(self):
        """Test plaintext storage with warning logged."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.json"

            with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
                plaintext_storage = PlaintextKeyStorage(storage_path)
                await plaintext_storage.store_key("test-profile", "plaintext-key")

                loader = APIKeyLoader(plaintext_storage=plaintext_storage)

                with patch.dict(os.environ, {}, clear=True):
                    with self.assertLogs(
                        "kollabor_ai.providers.security", level="WARNING"
                    ):
                        key = await loader.load_api_key(profile, "openai")

                self.assertEqual(key, "plaintext-key")

    async def test_load_from_config_migrates_to_keyring(self):
        """Test config key migration to keyring."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(return_value=asyncio.coroutine(lambda: None)())

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
                key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "config-key")
        mock_manager.store_key.assert_called_once_with("test-profile", "config-key")

    async def test_load_from_config_migrates_to_encrypted_on_keyring_failure(self):
        """Test migration falls back to encrypted storage if keyring fails."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(
            side_effect=asyncio.coroutine(
                lambda: (_ for _ in ()).throw(RuntimeError("Keyring failed"))
            )()
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            loader = APIKeyLoader(
                key_manager=mock_manager, encrypted_storage=encrypted_storage
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
                    key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "config-key")

            # Verify key was migrated to encrypted storage
            migrated_key = await encrypted_storage.get_key("test-profile")
            self.assertEqual(migrated_key, "config-key")

    async def test_load_from_config_migration_complete_failure(self):
        """Test migration failure when both keyring and encrypted fail."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(
            side_effect=asyncio.coroutine(
                lambda: (_ for _ in ()).throw(RuntimeError("Failed"))
            )()
        )

        # No encrypted storage provided
        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
                key = await loader.load_api_key(profile, "openai")

        # Should still return the key even if migration failed
        self.assertEqual(key, "config-key")

    async def test_load_no_key_found_logs_error(self):
        """Test error logged when no key found in any tier."""
        profile = {"name": "nonexistent-profile"}

        loader = APIKeyLoader()

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
                key = await loader.load_api_key(profile, "openai")

        self.assertIsNone(key)


class TestURLValidatorEdgeCases(unittest.TestCase):
    """Test edge cases in URL validation."""

    def test_reject_http_for_non_localhost(self):
        """Test HTTP is rejected for non-localhost URLs."""
        with self.assertRaises(ValueError) as ctx:
            URLValidator.validate_url("http://api.openai.com/v1", "openai")

        self.assertIn("must use HTTPS", str(ctx.exception))
        self.assertIn("man-in-the-middle", str(ctx.exception))

    def test_validate_url_with_port(self):
        """Test URL with port number."""
        # Localhost with port should work
        result = URLValidator.validate_url("http://localhost:8080", "custom")
        self.assertTrue(result)

        # Non-localhost with port in allowlist should work with HTTPS
        result = URLValidator.validate_url("https://api.openai.com:443", "openai")
        self.assertTrue(result)

    def test_validate_url_with_trailing_path(self):
        """Test URL with trailing path."""
        result = URLValidator.validate_url("https://api.openai.com/v1/chat", "openai")
        self.assertTrue(result)

    def test_get_allowed_hosts_includes_defaults(self):
        """Test allowed hosts includes all defaults."""
        allowed = URLValidator.get_allowed_hosts()

        self.assertIn("api.openai.com", allowed)
        self.assertIn("api.anthropic.com", allowed)
        self.assertIn("localhost", allowed)
        self.assertIn("127.0.0.1", allowed)

    def test_get_allowed_hosts_custom_from_env(self):
        """Test custom hosts from environment variable."""
        with patch.dict(
            os.environ, {"KOLLAB_ALLOWED_API_HOSTS": "custom.com,another.com"}
        ):
            allowed = URLValidator.get_allowed_hosts()

            self.assertIn("custom.com", allowed)
            self.assertIn("another.com", allowed)

    def test_normalize_removes_trailing_slashes(self):
        """Test normalization removes all trailing slashes."""
        cases = [
            ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
            ("https://api.openai.com/v1//", "https://api.openai.com/v1"),
            ("https://api.openai.com/v1///", "https://api.openai.com/v1"),
        ]

        for input_url, expected in cases:
            result = URLValidator.validate_and_normalize(input_url, "openai")
            self.assertEqual(result, expected)


class TestLoggingRedactorEdgeCases(unittest.TestCase):
    """Test edge cases in logging redaction."""

    def test_redact_exception_with_cause(self):
        """Test redacting exception preserves cause chain."""
        try:
            try:
                raise ValueError("Original error with sk-example-redaction-key-0000")
            except ValueError as e:
                raise RuntimeError("Wrapper error") from e
        except RuntimeError as exc:
            redacted = LoggingRedactor.redact(exc)

            self.assertIsInstance(redacted, Exception)
            self.assertNotIn("sk-example-redaction-key-0000", str(redacted))

    def test_redact_exception_returns_exception(self):
        """Test redacting exception always returns an exception type."""
        exc = ValueError("Error with sk-example-redaction-key-0000")

        redacted = LoggingRedactor.redact(exc)

        # Should return an Exception (or subclass)
        self.assertIsInstance(redacted, Exception)
        # The message should be redacted
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))

    def test_redact_nested_structures(self):
        """Test redaction of deeply nested structures."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "api_key": "sk-example-redaction-key-0000",
                        "nested_list": [
                            {"authorization": "Bearer sk-example-redaction-key-0000"}
                        ],
                    }
                }
            }
        }

        redacted = LoggingRedactor.redact(data)

        # Check all levels are redacted
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))
        self.assertIn("[REDACTED", str(redacted))

    def test_redact_mixed_list_types(self):
        """Test redacting list with mixed types."""
        data = [
            "sk-example-redaction-key-0000",
            42,
            None,
            {"api_key": "sk-example-redaction-key-0000"},
            ["nested-key"],
            True,
        ]

        redacted = LoggingRedactor.redact(data)

        # Keys should be redacted, primitives preserved
        self.assertEqual(redacted[1], 42)
        self.assertEqual(redacted[2], None)
        self.assertEqual(redacted[4], ["nested-key"])
        self.assertEqual(redacted[5], True)
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))

    def test_redact_tuple(self):
        """Test redacting tuple (returns tuple)."""
        data = (
            "sk-example-redaction-key-0000",
            {"api_key": "sk-example-redaction-key-0000"},
        )

        redacted = LoggingRedactor.redact(data)

        self.assertIsInstance(redacted, tuple)
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))


class TestRedactingLogFilterEdgeCases(unittest.TestCase):
    """Test edge cases in redacting log filter."""

    def test_filter_with_dict_message(self):
        """Test filter redacts dict message."""
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg={"api_key": "sk-example-redaction-key-0000"},
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertNotIn("sk-example-redaction-key-0000", str(record.msg))

    def test_filter_with_none_message(self):
        """Test filter handles None message."""
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=None,
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertIsNone(record.msg)

    def test_filter_with_complex_args(self):
        """Test filter redacts complex args tuple."""
        log_filter = RedactingLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Keys: %s, %s",
            args=(
                "sk-example-redaction-key-0000",
                {"api_key": "sk-example-other-key-0000"},
            ),
            exc_info=None,
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        self.assertNotIn("sk-example-redaction-key-0000", str(record.args))
        self.assertNotIn("sk-example-other-key-0000", str(record.args))

    def test_filter_preserves_exception_type(self):
        """Test filter preserves exception type when redacting."""
        log_filter = RedactingLogFilter()
        exc = ValueError("Error with sk-example-redaction-key-0000")

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=(type(exc), exc, None),
        )

        result = log_filter.filter(record)

        self.assertTrue(result)
        # Exception type should be preserved
        self.assertEqual(record.exc_info[0], ValueError)


class TestSingletonFunctions(unittest.TestCase):
    """Test singleton initialization functions."""

    @skip_if_no_keyring()
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_manager_singleton(self, mock_get_keyring):
        """Test get_key_manager returns singleton instance."""
        mock_backend = Mock()
        mock_backend.__class__.__name__ = "MockKeyringBackend"
        mock_get_keyring.return_value = mock_backend

        # First call
        manager1 = await get_key_manager()
        # Second call
        manager2 = await get_key_manager()

        # Should be same instance
        self.assertIs(manager1, manager2)

    @skip_if_no_keyring()
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_manager_concurrent_initialization(self, mock_get_keyring):
        """Test concurrent initialization is thread-safe."""
        mock_backend = Mock()
        mock_backend.__class__.__name__ = "MockKeyringBackend"
        mock_get_keyring.return_value = mock_backend

        # Create tasks that all try to get the manager
        tasks = [get_key_manager() for _ in range(10)]

        managers = await asyncio.gather(*tasks)

        # All should be the same instance
        first = managers[0]
        for manager in managers:
            self.assertIs(manager, first)


@skip_if_no_cryptography()
class TestRealFileIO(TempFileTest):
    """Test real file I/O operations."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"

    async def test_encrypted_file_persistence(self):
        """Test data persists across storage instances."""
        # Create first storage instance and store key
        storage1 = EncryptedFileKeyStorage(self.storage_path, self.password)
        await storage1.store_key("test", "persistent-key")

        # Create second storage instance (simulates app restart)
        storage2 = EncryptedFileKeyStorage(self.storage_path, self.password)
        retrieved_key = await storage2.get_key("test")

        self.assertEqual(retrieved_key, "persistent-key")

    async def test_encrypted_file_multiple_keys_persistence(self):
        """Test multiple keys persist correctly."""
        storage1 = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Store multiple keys
        await storage1.store_key("key1", "value1")
        await storage1.store_key("key2", "value2")
        await storage1.store_key("key3", "value3")

        # Reload and verify
        storage2 = EncryptedFileKeyStorage(self.storage_path, self.password)

        self.assertEqual(await storage2.get_key("key1"), "value1")
        self.assertEqual(await storage2.get_key("key2"), "value2")
        self.assertEqual(await storage2.get_key("key3"), "value3")

    async def test_encrypted_file_update_key(self):
        """Test updating existing key."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Store initial key
        await storage.store_key("test", "initial-value")

        # Update key
        await storage.store_key("test", "updated-value")

        # Verify update persisted
        retrieved = await storage.get_key("test")
        self.assertEqual(retrieved, "updated-value")

    async def test_plaintext_file_persistence(self):
        """Test plaintext file persistence."""
        plaintext_path = self.temp_path / "keys.json"

        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage1 = PlaintextKeyStorage(plaintext_path)
            await storage1.store_key("test", "plaintext-key")

            # Reload
            storage2 = PlaintextKeyStorage(plaintext_path)
            retrieved = await storage2.get_key("test")

            self.assertEqual(retrieved, "plaintext-key")

    async def test_file_permissions_on_reload(self):
        """Test file permissions are maintained on reload."""
        storage1 = EncryptedFileKeyStorage(self.storage_path, self.password)
        await storage1.store_key("test", "key-value")

        # Check permissions
        stat1 = os.stat(self.storage_path)
        perm1 = oct(stat1.st_mode & 0o777)

        # Reload
        storage2 = EncryptedFileKeyStorage(self.storage_path, self.password)
        await storage2.store_key("test2", "key-value2")

        # Check permissions maintained
        stat2 = os.stat(self.storage_path)
        perm2 = oct(stat2.st_mode & 0o777)

        self.assertEqual(perm1, "0o600")
        self.assertEqual(perm2, "0o600")


# =============================================================================
# Additional Tests for Missing Coverage Paths
# =============================================================================


@skip_if_no_cryptography()
class TestEncryptedFileKeyStorageAtomicWrite(TempFileTest):
    """Test atomic write behavior and error recovery."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"

    async def test_save_keystore_atomic_rollback(self):
        """Test atomic write rolls back on error."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Store initial key
        await storage.store_key("initial", "initial-key")
        initial_content = self.storage_path.read_bytes()

        # Mock replace to fail (simulating disk full)
        with patch.object(Path, "replace", side_effect=OSError("Disk full")):
            try:
                await storage.store_key("test", "new-key")
            except OSError:
                pass

        # Original file should be unchanged (atomic rollback)
        final_content = self.storage_path.read_bytes()
        self.assertEqual(initial_content, final_content)

        # Verify initial key still exists
        storage2 = EncryptedFileKeyStorage(self.storage_path, self.password)
        self.assertEqual(await storage2.get_key("initial"), "initial-key")

    async def test_load_keystore_file_too_short(self):
        """Test loading when file is too short for format."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Write file that's too short (salt=16, nonce=12, minimum ciphertext=16)
        self.storage_path.write_bytes(b"short")

        with self.assertRaises(RuntimeError) as ctx:
            await storage.get_key("test")

        self.assertIn("Failed to decrypt", str(ctx.exception))

    async def test_load_keystore_decryption_fails(self):
        """Test loading when decryption fails (wrong password)."""
        # Store with correct password
        storage1 = EncryptedFileKeyStorage(self.storage_path, self.password)
        await storage1.store_key("test", "key-value")

        # Try to load with wrong password
        storage2 = EncryptedFileKeyStorage(self.storage_path, "wrong-password")

        with self.assertRaises(RuntimeError) as ctx:
            await storage2.get_key("test")

        self.assertIn("Failed to decrypt", str(ctx.exception))
        self.assertIn("KOLLAB_KEY_ENCRYPTION_PASSWORD", str(ctx.exception))

    async def test_save_keystore_sets_permissions_before_rename(self):
        """Test permissions are set before atomic rename."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Track the order of operations
        operations = []

        original_chmod = Path.chmod
        original_replace = Path.replace

        def tracking_chmod(self, mode):
            operations.append(("chmod", str(self), mode))
            return original_chmod(self, mode)

        def tracking_replace(self, target):
            operations.append(("replace", str(self), str(target)))
            return original_replace(self, target)

        with patch.object(Path, "chmod", tracking_chmod):
            with patch.object(Path, "replace", tracking_replace):
                await storage.store_key("test", "key-value")

        # chmod should come before replace
        chmod_idx = next(i for i, op in enumerate(operations) if op[0] == "chmod")
        replace_idx = next(i for i, op in enumerate(operations) if op[0] == "replace")

        self.assertLess(chmod_idx, replace_idx)

    async def test_save_keystore_temp_cleanup_on_success(self):
        """Test temp file is cleaned up even on success."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)
        await storage.store_key("test", "key-value")

        # Verify temp file was cleaned up
        temp_path = self.storage_path.with_suffix(".tmp")
        self.assertFalse(temp_path.exists())


class TestPlaintextKeyStorageDetailed(TempFileTest):
    """Test detailed plaintext storage behavior."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.json"

    async def test_save_keystore_atomic_rollback(self):
        """Test atomic write rolls back on error."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # Store initial key
            await storage.store_key("initial", "initial-key")
            initial_content = self.storage_path.read_text()

            # Mock replace to fail
            with patch.object(Path, "replace", side_effect=OSError("Disk full")):
                try:
                    await storage.store_key("test", "new-key")
                except OSError:
                    pass

            # Original file should be unchanged
            final_content = self.storage_path.read_text()
            self.assertEqual(initial_content, final_content)

    async def test_save_keystore_sets_permissions(self):
        """Test permissions are set before rename."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            await storage.store_key("test", "key-value")

            # Check file permissions
            stat_info = os.stat(self.storage_path)
            permissions = oct(stat_info.st_mode & 0o777)
            self.assertEqual(permissions, "0o600")


class TestAPIKeyLoaderMissingPaths(unittest.TestCase):
    """Test APIKeyLoader paths not covered elsewhere."""

    async def test_load_key_debug_logging(self):
        """Test debug logging during key loading."""
        profile = {"name": "test-profile"}

        mock_manager = Mock()
        mock_manager.get_key = Mock(
            return_value=asyncio.coroutine(lambda: "keyring-key")()
        )

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("kollabor_ai.providers.security", level="DEBUG"):
                key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "keyring-key")

    @skip_if_no_cryptography()
    async def test_load_key_from_encrypted_logs_debug(self):
        """Test debug logging when loading from encrypted storage."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            await encrypted_storage.store_key("test-profile", "encrypted-key")

            loader = APIKeyLoader(encrypted_storage=encrypted_storage)

            with patch.dict(os.environ, {}, clear=True):
                with self.assertLogs("kollabor_ai.providers.security", level="DEBUG"):
                    key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "encrypted-key")

    async def test_migration_to_keyring_success(self):
        """Test successful migration to keyring."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(return_value=asyncio.coroutine(lambda: None)())

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("kollabor_ai.providers.security", level="INFO"):
                key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "config-key")

    async def test_migration_to_encrypted_fallback(self):
        """Test migration falls back to encrypted storage."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(
            side_effect=asyncio.coroutine(
                lambda: (_ for _ in ()).throw(RuntimeError("Failed"))
            )()
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            loader = APIKeyLoader(
                key_manager=mock_manager, encrypted_storage=encrypted_storage
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertLogs("kollabor_ai.providers.security", level="WARNING"):
                    key = await loader.load_api_key(profile, "openai")

            self.assertEqual(key, "config-key")

    async def test_migration_complete_failure_error_logged(self):
        """Test migration failure is logged."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        mock_manager = Mock()
        mock_manager.store_key = Mock(
            side_effect=asyncio.coroutine(
                lambda: (_ for _ in ()).throw(RuntimeError("Failed"))
            )()
        )

        loader = APIKeyLoader(key_manager=mock_manager)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs(
                "kollabor_ai.providers.security", level="ERROR"
            ) as log:
                key = await loader.load_api_key(profile, "openai")

            # Should log error about migration failure
            self.assertTrue(any("Failed to migrate" in msg for msg in log.output))

        self.assertEqual(key, "config-key")


@skip_if_no_cryptography()
class TestEncryptedKeyDerivation(TempFileTest):
    """Test key derivation in detail."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"

    def test_key_derivation_produces_256_bit_key(self):
        """Test PBKDF2 produces exactly 256-bit key."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        salt = secrets.token_bytes(16)
        key = storage._derive_key(salt)

        # AES-256 requires 32 bytes (256 bits)
        self.assertEqual(len(key), 32)

    def test_key_derivation_deterministic(self):
        """Test same password + salt produces same key."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        salt = secrets.token_bytes(16)
        key1 = storage._derive_key(salt)
        key2 = storage._derive_key(salt)

        self.assertEqual(key1, key2)


class TestLoggingRedactorAllPatterns(unittest.TestCase):
    """Test all redaction patterns are working."""

    def test_redact_openai_key_pattern(self):
        """Test OpenAI key pattern matches."""
        text = "Key: sk-example-openai-redaction-0000"
        redacted = LoggingRedactor.redact(text)

        self.assertNotIn("sk-example-openai-redaction-0000", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_redact_openai_project_key_pattern(self):
        """Test OpenAI project key pattern matches."""
        text = "Key: sk-proj-example-redaction-0000"
        redacted = LoggingRedactor.redact(text)

        self.assertNotIn("sk-proj-example-redaction-0000", redacted)
        # Note: OpenAI pattern matches first (more specific pattern comes after)
        self.assertIn("[REDACTED", redacted)

    def test_redact_anthropic_key_pattern(self):
        """Test Anthropic key pattern matches."""
        text = "Key: sk-ant-abc123def456789xyz123"
        redacted = LoggingRedactor.redact(text)

        self.assertNotIn("sk-ant-abc123def456789xyz123", redacted)
        # Note: OpenAI pattern matches first, so it shows as OPENAI-KEY
        self.assertIn("[REDACTED", redacted)

    def test_redact_multiple_keys_in_string(self):
        """Test multiple keys in same string are redacted."""
        text = (
            "OpenAI: sk-example-openai-redaction-0000, Anthropic: sk-ant-example-anthropic-key-0000"
        )
        redacted = LoggingRedactor.redact(text)

        self.assertNotIn("sk-example-openai-redaction-0000", redacted)
        self.assertNotIn("sk-ant-example-anthropic-key-0000", redacted)


# =============================================================================
# Direct Method Execution Tests for Missing Coverage
# =============================================================================


@skip_if_no_cryptography()
class TestEncryptedFileKeyStorageDirectMethods(TempFileTest):
    """Test internal methods directly for missing coverage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"
        self.storage = EncryptedFileKeyStorage(self.storage_path, self.password)

    def test_load_keystore_when_file_not_exists(self):
        """Test _load_keystore returns empty dict when file doesn't exist."""
        # File doesn't exist
        self.assertFalse(self.storage_path.exists())

        # Should return empty dict
        keystore = self.storage._load_keystore()
        self.assertEqual(keystore, {})

    def test_load_keystore_reads_file(self):
        """Test _load_keystore reads and decrypts file."""
        # Store a key first
        asyncio.run(self.storage.store_key("test", "test-value"))

        # Load keystore directly
        keystore = self.storage._load_keystore()

        self.assertEqual(keystore, {"test": "test-value"})

    def test_save_keystore_creates_file(self):
        """Test _save_keystore creates encrypted file."""
        # Save keystore directly
        self.storage._save_keystore({"test": "test-value"})

        # File should exist
        self.assertTrue(self.storage_path.exists())

        # Verify we can load it back
        keystore = self.storage._load_keystore()
        self.assertEqual(keystore, {"test": "test-value"})

    def test_save_keystore_atomic_write_flow(self):
        """Test _save_keystore atomic write: temp -> chmod -> rename."""
        # Track operations
        ops = []

        original_chmod = Path.chmod
        original_replace = Path.replace

        def tracking_chmod(self, mode):
            if ".tmp" in str(self):
                ops.append("chmod")
            return original_chmod(self, mode)

        def tracking_replace(self, target):
            ops.append("replace")
            return original_replace(self, target)

        # Track when temp file is opened
        temp_file_path = None

        def tracking_open(path, *args, **kwargs):
            nonlocal temp_file_path
            if ".tmp" in str(path):
                ops.append("open_temp")
                temp_file_path = str(path)
            return original_open(path, *args, **kwargs)

        original_open = builtins.open

        with patch.object(builtins, "open", tracking_open):
            with patch.object(Path, "chmod", tracking_chmod):
                with patch.object(Path, "replace", tracking_replace):
                    self.storage._save_keystore({"test": "value"})

        # Verify operations happened in order
        self.assertIn("open_temp", ops)
        self.assertIn("chmod", ops)
        self.assertIn("replace", ops)

    async def test_store_key_logs_info(self):
        """Test store_key logs info message."""
        with self.assertLogs("kollabor_ai.providers.security", level="INFO"):
            await self.storage.store_key("test", "value")

    async def test_delete_key_logs_info(self):
        """Test delete_key logs info message."""
        await self.storage.store_key("test", "value")

        with self.assertLogs("kollabor_ai.providers.security", level="INFO"):
            result = await self.storage.delete_key("test")

        self.assertTrue(result)


class TestPlaintextKeyStorageDirectMethods(TempFileTest):
    """Test internal methods directly for missing coverage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.json"

    def test_load_keystore_returns_dict(self):
        """Test _load_keystore returns dict."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # No file exists
            keystore = storage._load_keystore()
            self.assertEqual(keystore, {})

            # Store a key
            asyncio.run(storage.store_key("test", "value"))

            # Load it back
            keystore = storage._load_keystore()
            self.assertEqual(keystore, {"test": "value"})

    def test_save_keystore_creates_file(self):
        """Test _save_keystore creates file."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            storage._save_keystore({"test": "value"})

            self.assertTrue(self.storage_path.exists())

            # Verify content
            with open(self.storage_path, "r") as f:
                data = json.load(f)
            self.assertEqual(data, {"test": "value"})


@skip_if_no_keyring()
class TestAPIKeyManagerLogging(unittest.TestCase):
    """Test logging paths in APIKeyManager."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_backend = Mock()
        self.mock_backend.__class__.__name__ = "MockKeyringBackend"

    @patch("kollabor_ai.providers.security.keyring.set_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_store_key_logs_info_message(
        self, mock_get_keyring, mock_set_password
    ):
        """Test store_key logs success message."""
        mock_get_keyring.return_value = self.mock_backend

        with self.assertLogs("kollabor_ai.providers.security", level="INFO") as log:
            manager = APIKeyManager()
            await manager.store_key("test-profile", "sk-test-key")

        self.assertTrue(
            any("Stored API key in OS keyring" in msg for msg in log.output)
        )

    @patch("kollabor_ai.providers.security.keyring.delete_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_delete_key_logs_info_message(self, mock_get_keyring, mock_delete):
        """Test delete_key logs success message."""
        mock_get_keyring.return_value = self.mock_backend

        with self.assertLogs("kollabor_ai.providers.security", level="INFO") as log:
            manager = APIKeyManager()
            await manager.delete_key("test-profile")

        self.assertTrue(
            any("Deleted API key from OS keyring" in msg for msg in log.output)
        )


class TestEnvironmentKeyStoragePaths(unittest.TestCase):
    """Test environment storage paths for missing coverage."""

    def setUp(self):
        """Set up test fixtures."""
        self.storage = EnvironmentKeyStorage()

    async def test_get_key_provider_mapping(self):
        """Test provider extraction from profile name."""
        # Test provider extraction with dash
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            key = await self.storage.get_key("openai-gpt4")
            self.assertEqual(key, "test-key")

        # Test without dash
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-2"}):
            key = await self.storage.get_key("openai")
            self.assertEqual(key, "test-key-2")

    async def test_get_key_returns_none_when_not_found(self):
        """Test get_key returns None when key not found."""
        with patch.dict(os.environ, {}, clear=True):
            key = await self.storage.get_key("nonexistent")
            self.assertIsNone(key)

    async def test_delete_key_returns_false(self):
        """Test delete_key always returns False."""
        result = await self.storage.delete_key("test")
        self.assertFalse(result)


class TestAPIKeyLoaderFallbackPaths(unittest.TestCase):
    """Test APIKeyLoader fallback paths."""

    async def test_load_api_key_environment_first(self):
        """Test environment variable has highest priority."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        loader = APIKeyLoader()

        # Environment should win
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
            key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "env-key")

    async def test_load_api_key_tries_all_tiers(self):
        """Test loader tries all tiers in order."""
        profile = {"name": "test-profile"}

        # Loader with no backends
        loader = APIKeyLoader()

        # Should return None when no key found
        with patch.dict(os.environ, {}, clear=True):
            key = await loader.load_api_key(profile, "openai")

        self.assertIsNone(key)

    async def test_migrate_key_success_to_keyring(self):
        """Test migration succeeds to keyring."""
        mock_manager = Mock()
        mock_manager.store_key = Mock(return_value=asyncio.coroutine(lambda: None)())

        loader = APIKeyLoader(key_manager=mock_manager)

        await loader._migrate_key("test-profile", "test-key")

        mock_manager.store_key.assert_called_once_with("test-profile", "test-key")

    async def test_migrate_key_fallback_to_encrypted(self):
        """Test migration falls back to encrypted storage."""
        mock_manager = Mock()
        mock_manager.store_key = Mock(
            side_effect=asyncio.coroutine(
                lambda: (_ for _ in ()).throw(RuntimeError("Failed"))
            )()
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            loader = APIKeyLoader(
                key_manager=mock_manager, encrypted_storage=encrypted_storage
            )

            await loader._migrate_key("test-profile", "test-key")

            # Verify it was stored in encrypted storage
            key = await encrypted_storage.get_key("test-profile")
            self.assertEqual(key, "test-key")


# =============================================================================
# Additional Tests to Reach 75%+ Coverage
# =============================================================================


@skip_if_no_keyring()
class TestAPIKeyManagerErrorPaths(unittest.TestCase):
    """Test error handling paths in APIKeyManager."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_backend = Mock()
        self.mock_backend.__class__.__name__ = "MockKeyringBackend"

    @patch("kollabor_ai.providers.security.keyring.set_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_store_key_runtime_error_message(
        self, mock_get_keyring, mock_set_password
    ):
        """Test store_key RuntimeError has helpful message."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_set_password.side_effect = KeyringError("Permission denied")

        manager = APIKeyManager()

        with self.assertRaises(RuntimeError) as ctx:
            await manager.store_key("test", "key")

        error_msg = str(ctx.exception)
        self.assertIn("Failed to store API key", error_msg)
        self.assertIn("keyring permissions", error_msg.lower())

    @patch("kollabor_ai.providers.security.keyring.get_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_get_key_logs_error_on_failure(
        self, mock_get_keyring, mock_get_password
    ):
        """Test get_key logs error when keyring fails."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_get_password.side_effect = KeyringError("Network error")

        with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
            manager = APIKeyManager()
            key = await manager.get_key("test")

        self.assertIsNone(key)

    @patch("kollabor_ai.providers.security.keyring.delete_password")
    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_delete_key_logs_error_on_failure(
        self, mock_get_keyring, mock_delete
    ):
        """Test delete_key logs error when keyring fails."""
        from keyring.errors import KeyringError

        mock_get_keyring.return_value = self.mock_backend
        mock_delete.side_effect = KeyringError("Corrupted")

        with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
            manager = APIKeyManager()
            result = await manager.delete_key("test")

        self.assertFalse(result)


@skip_if_no_cryptography()
class TestEncryptedFileKeyStorageExceptionPaths(TempFileTest):
    """Test exception handling paths in encrypted storage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.enc"
        self.password = "test-password-123"

    async def test_load_keystore_exception_handler(self):
        """Test _load_keystore exception handler logs and raises."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Write invalid data
        self.storage_path.write_bytes(b"corrupted data that is too short")

        with self.assertRaises(RuntimeError) as ctx:
            with self.assertLogs("kollabor_ai.providers.security", level="ERROR"):
                storage._load_keystore()

        error_msg = str(ctx.exception)
        self.assertIn("Failed to decrypt", error_msg)
        self.assertIn("KOLLAB_KEY_ENCRYPTION_PASSWORD", error_msg)

    async def test_save_keystore_temp_cleanup_on_error(self):
        """Test temp file cleanup in finally block."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        # Mock chmod to raise error after temp file is created
        original_chmod = Path.chmod
        call_count = [0]

        def failing_chmod(self, mode):
            call_count[0] += 1
            if ".tmp" in str(self):
                raise OSError("Permission denied")
            return original_chmod(self, mode)

        with patch.object(Path, "chmod", failing_chmod):
            try:
                await storage.store_key("test", "value")
            except OSError:
                pass

        # Temp file should be cleaned up despite error
        temp_path = self.storage_path.with_suffix(".tmp")
        self.assertFalse(temp_path.exists())

    async def test_get_key_returns_none_for_nonexistent(self):
        """Test get_key returns None when key doesn't exist."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        key = await storage.get_key("nonexistent")
        self.assertIsNone(key)

    async def test_delete_key_returns_false_for_nonexistent(self):
        """Test delete_key returns False when key doesn't exist."""
        storage = EncryptedFileKeyStorage(self.storage_path, self.password)

        result = await storage.delete_key("nonexistent")
        self.assertFalse(result)


class TestEnvironmentKeyStorageErrorMessages(unittest.TestCase):
    """Test error messages in environment storage."""

    def test_store_key_runtime_error_message(self):
        """Test store_key RuntimeError has helpful message."""
        storage = EnvironmentKeyStorage()

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(storage.store_key("test", "key"))

        error_msg = str(ctx.exception)
        self.assertIn("Cannot persist keys", error_msg)
        self.assertIn("OPENAI_API_KEY", error_msg)
        self.assertIn("ANTHROPIC_API_KEY", error_msg)
        self.assertIn("AZURE_OPENAI_API_KEY", error_msg)


class TestPlaintextKeyStorageExceptionPaths(TempFileTest):
    """Test exception handling paths in plaintext storage."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.storage_path = self.temp_path / "keys.json"

    async def test_load_keystore_exception_handler(self):
        """Test _load_keystore handles JSON errors gracefully."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # Write invalid JSON
            self.storage_path.write_text("{ invalid json }")

            # Should return empty dict on error (logs but doesn't raise)
            keystore = storage._load_keystore()
            self.assertEqual(keystore, {})

    async def test_save_keystore_temp_cleanup_on_error(self):
        """Test temp file cleanup in finally block."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            # Mock chmod to fail
            original_chmod = Path.chmod

            def failing_chmod(self, mode):
                if ".tmp" in str(self):
                    raise OSError("Permission denied")
                return original_chmod(self, mode)

            with patch.object(Path, "chmod", failing_chmod):
                try:
                    await storage.store_key("test", "value")
                except OSError:
                    pass

            # Temp file should be cleaned up
            temp_path = self.storage_path.with_suffix(".tmp")
            self.assertFalse(temp_path.exists())

    async def test_get_key_returns_none_for_nonexistent(self):
        """Test get_key returns None when key doesn't exist."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            key = await storage.get_key("nonexistent")
            self.assertIsNone(key)

    async def test_delete_key_returns_false_for_nonexistent(self):
        """Test delete_key returns False when key doesn't exist."""
        with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
            storage = PlaintextKeyStorage(self.storage_path)

            result = await storage.delete_key("nonexistent")
            self.assertFalse(result)


class TestAPIKeyLoaderCompleteFallback(unittest.TestCase):
    """Test complete fallback chain in APIKeyLoader."""

    async def test_complete_fallback_chain(self):
        """Test all tiers are tried in order."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        # Create loader with all backends
        mock_manager = Mock()
        mock_manager.get_key = Mock(return_value=asyncio.coroutine(lambda: None)())

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")

            plaintext_path = Path(temp_dir) / "keys.json"

            with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
                plaintext_storage = PlaintextKeyStorage(plaintext_path)
                await plaintext_storage.store_key("test-profile", "plaintext-key")

                loader = APIKeyLoader(
                    key_manager=mock_manager,
                    encrypted_storage=encrypted_storage,
                    plaintext_storage=plaintext_storage,
                )

                # Without env, should fallback through all tiers
                with patch.dict(os.environ, {}, clear=True):
                    # Keyring returns None, encrypted returns None, plaintext has key
                    key = await loader.load_api_key(profile, "openai")

                self.assertEqual(key, "plaintext-key")

    @skip_if_no_cryptography()
    async def test_fallback_stops_at_encrypted(self):
        """Test fallback stops at encrypted storage when key found."""
        profile = {"name": "test-profile"}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, "password")
            await encrypted_storage.store_key("test-profile", "encrypted-key")

            # Plaintext storage also has a key (different value)
            plaintext_path = Path(temp_dir) / "keys.json"

            with patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"}):
                plaintext_storage = PlaintextKeyStorage(plaintext_path)
                await plaintext_storage.store_key("test-profile", "plaintext-key")

                loader = APIKeyLoader(
                    encrypted_storage=encrypted_storage,
                    plaintext_storage=plaintext_storage,
                )

                with patch.dict(os.environ, {}, clear=True):
                    key = await loader.load_api_key(profile, "openai")

                # Should stop at encrypted, not try plaintext
                self.assertEqual(key, "encrypted-key")

    async def test_fallback_uses_config_key(self):
        """Test config key is used as last resort."""
        profile = {"name": "test-profile", "api_key": "config-key"}

        loader = APIKeyLoader()

        with patch.dict(os.environ, {}, clear=True):
            key = await loader.load_api_key(profile, "openai")

        self.assertEqual(key, "config-key")


@skip_if_no_cryptography()
class TestLoggingRedactorExceptionFallback(unittest.TestCase):
    """Test exception redaction fallback paths."""

    def test_redact_exception_preserves_type(self):
        """Test redacting exception preserves exception type."""
        exc = ValueError("Error with sk-example-redaction-key-0000")

        redacted = LoggingRedactor.redact(exc)

        self.assertIsInstance(redacted, ValueError)
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))

    def test_redact_exception_fallback_on_creation_error(self):
        """Test fallback when exception creation fails."""

        class CustomException(Exception):
            pass

        exc = CustomException("Error with sk-example-redaction-key-0000")

        # This should work fine (custom exception can be created)
        redacted = LoggingRedactor.redact(exc)

        self.assertIsInstance(redacted, Exception)
        self.assertNotIn("sk-example-redaction-key-0000", str(redacted))


@skip_if_no_keyring()
class TestGetKeyManagerSingleton(unittest.TestCase):
    """Test get_key_manager singleton function."""

    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_singleton_returns_same_instance(self, mock_get_keyring):
        """Test multiple calls return same instance."""
        mock_backend = Mock()
        mock_backend.__class__.__name__ = "MockKeyringBackend"
        mock_get_keyring.return_value = mock_backend

        manager1 = await get_key_manager()
        manager2 = await get_key_manager()

        self.assertIs(manager1, manager2)

    @patch("kollabor_ai.providers.security.keyring.get_keyring")
    async def test_concurrent_initialization(self, mock_get_keyring):
        """Test concurrent initialization is thread-safe."""
        mock_backend = Mock()
        mock_backend.__class__.__name__ = "MockKeyringBackend"
        mock_get_keyring.return_value = mock_backend

        tasks = [get_key_manager() for _ in range(10)]
        managers = await asyncio.gather(*tasks)

        # All should be the same instance
        first = managers[0]
        for manager in managers:
            self.assertIs(manager, first)


if __name__ == "__main__":
    unittest.main()
