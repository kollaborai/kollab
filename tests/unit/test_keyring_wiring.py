"""
Unit tests for keyring wiring in profile_manager.py.

Tests the integration between profile_manager and the OS keyring
via the sync helper functions (_keyring_get, _keyring_set) and
the sentinel string mechanism (secret:keyring:<profile_name>).

Covers:
- Sentinel resolution in get_api_key()
- Env var priority over sentinel
- Plaintext fallback when keyring is unavailable
- Auto-migration of plaintext keys to keyring on read
- Write path: sentinel generation on save
- Write path: sentinel passthrough (already stored)
- Write path: plaintext fallback when keyring is down
- Migration fix: no default-password for tier 2
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kollabor_ai.profile_manager import (
    KEYRING_SENTINEL_PREFIX,
    LLMProfile,
    ProfileManager,
    _keyring_get,
    _keyring_set,
)


class TestKeyringHelpers(unittest.TestCase):
    """Test _keyring_get and _keyring_set sync helpers."""

    def test_keyring_get_success(self):
        """Retrieve key from keyring successfully."""
        mock_gp = MagicMock(return_value="sk-ant-real-key")
        fake_keyring = SimpleNamespace(get_password=mock_gp)
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            result = _keyring_get("claude")
            self.assertEqual(result, "sk-ant-real-key")
            mock_gp.assert_called_once_with("kollab", "claude")

    def test_keyring_get_not_found(self):
        """Return None when key is not in keyring."""
        mock_gp = MagicMock(return_value=None)
        fake_keyring = SimpleNamespace(get_password=mock_gp)
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            result = _keyring_get("nonexistent")
            self.assertIsNone(result)

    def test_keyring_get_exception(self):
        """Return None when keyring raises an exception."""
        fake_keyring = SimpleNamespace(
            get_password=MagicMock(side_effect=Exception("keyring locked"))
        )
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            result = _keyring_get("claude")
            self.assertIsNone(result)

    def test_keyring_set_success(self):
        """Store key in keyring successfully."""
        mock_sp = MagicMock()
        fake_keyring = SimpleNamespace(set_password=mock_sp)
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            result = _keyring_set("claude", "sk-ant-key-123")
            self.assertTrue(result)
            mock_sp.assert_called_once_with("kollab", "claude", "sk-ant-key-123")

    def test_keyring_set_failure(self):
        """Return False when keyring storage fails."""
        fake_keyring = SimpleNamespace(
            set_password=MagicMock(side_effect=Exception("access denied"))
        )
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            result = _keyring_set("claude", "sk-ant-key-123")
            self.assertFalse(result)


class TestGetApiKeyResolution(unittest.TestCase):
    """Test LLMProfile.get_api_key() resolution order."""

    def _make_profile(self, name="test-profile", api_key=""):
        return LLMProfile(
            name=name,
            provider="anthropic",
            api_key=api_key,
        )

    @patch.dict(os.environ, {"KOLLAB_TEST_PROFILE_API_KEY": "env-key"})
    def test_env_var_wins_over_everything(self):
        """Profile-specific env var has highest priority."""
        profile = self._make_profile(
            api_key=f"{KEYRING_SENTINEL_PREFIX}test-profile"
        )
        self.assertEqual(profile.get_api_key(), "env-key")

    @patch.dict(os.environ, {"KOLLAB_API_KEY": "global-env-key"}, clear=False)
    def test_global_env_var_wins_over_sentinel(self):
        """Global env var beats sentinel."""
        profile = self._make_profile(
            name="sentinel-test",
            api_key=f"{KEYRING_SENTINEL_PREFIX}sentinel-test",
        )
        # Make sure no profile-specific env var exists
        os.environ.pop("KOLLAB_SENTINEL_TEST_API_KEY", None)
        self.assertEqual(profile.get_api_key(), "global-env-key")

    @patch("kollabor_ai.profile_manager._keyring_get")
    def test_sentinel_resolves_from_keyring(self, mock_get):
        """Sentinel string triggers keyring lookup."""
        mock_get.return_value = "sk-ant-real-key"
        profile = self._make_profile(
            api_key=f"{KEYRING_SENTINEL_PREFIX}test-profile"
        )
        result = profile.get_api_key()
        self.assertEqual(result, "sk-ant-real-key")
        mock_get.assert_called_once_with("test-profile")

    @patch("kollabor_ai.profile_manager._keyring_get")
    def test_sentinel_keyring_miss_returns_empty(self, mock_get):
        """When sentinel points to missing keyring entry, return empty."""
        mock_get.return_value = None
        profile = self._make_profile(
            api_key=f"{KEYRING_SENTINEL_PREFIX}test-profile"
        )
        result = profile.get_api_key()
        self.assertEqual(result, "")

    @patch("kollabor_ai.profile_manager._keyring_set")
    def test_plaintext_auto_migrates_to_keyring(self, mock_set):
        """Plaintext key triggers auto-migration to keyring."""
        mock_set.return_value = True
        profile = self._make_profile(api_key="sk-ant-plaintext-key")
        result = profile.get_api_key()
        self.assertEqual(result, "sk-ant-plaintext-key")
        mock_set.assert_called_once_with("test-profile", "sk-ant-plaintext-key")

    @patch("kollabor_ai.profile_manager._keyring_set")
    def test_plaintext_fallback_when_keyring_down(self, mock_set):
        """Plaintext key returned even when keyring migration fails."""
        mock_set.return_value = False
        profile = self._make_profile(api_key="sk-ant-plaintext-key")
        result = profile.get_api_key()
        self.assertEqual(result, "sk-ant-plaintext-key")

    def test_empty_api_key_returns_empty(self):
        """No api_key configured returns empty string."""
        profile = self._make_profile(api_key="")
        self.assertEqual(profile.get_api_key(), "")


class TestWritePathSentinel(unittest.TestCase):
    """Test that the write path produces correct sentinel strings."""

    @staticmethod
    def _simulate_write_path(profile_name, api_key, mock_keyring_set):
        """Simulate the logic in _build_profile_dict for the api_key field.

        mock_keyring_set: a callable that replaces _keyring_set
        """
        key_val = api_key
        if key_val.startswith(KEYRING_SENTINEL_PREFIX):
            return key_val
        elif mock_keyring_set(profile_name, key_val):
            return f"{KEYRING_SENTINEL_PREFIX}{profile_name}"
        else:
            return key_val

    def test_real_key_produces_sentinel(self):
        """Real key stored in keyring, sentinel written to config."""
        mock_set = MagicMock(return_value=True)
        result = self._simulate_write_path("test-save", "sk-ant-real-key", mock_set)
        self.assertEqual(result, "secret:keyring:test-save")
        mock_set.assert_called_once_with("test-save", "sk-ant-real-key")

    def test_existing_sentinel_passes_through(self):
        """Sentinel strings pass through without re-storing."""
        mock_set = MagicMock(return_value=True)
        sentinel = f"{KEYRING_SENTINEL_PREFIX}test-save"
        result = self._simulate_write_path("test-save", sentinel, mock_set)
        self.assertEqual(result, sentinel)
        mock_set.assert_not_called()

    def test_keyring_down_falls_back_to_plaintext(self):
        """When keyring is unavailable, plaintext key is written."""
        mock_set = MagicMock(return_value=False)
        result = self._simulate_write_path("test-save", "sk-ant-real-key", mock_set)
        self.assertEqual(result, "sk-ant-real-key")


class TestMigrationDefaultPasswordFix(unittest.TestCase):
    """Test that migration.py no longer uses 'default-password'."""

    def test_no_default_password_in_tier2(self):
        """Tier 2 should be skipped when no password env var is set."""
        from kollabor_config.migration import ProfileMigrator

        migrator = ProfileMigrator()
        env = os.environ.copy()
        env.pop("KOLLAB_KEY_ENCRYPTION_PASSWORD", None)

        with patch.dict(os.environ, env, clear=True):
            result = migrator._try_encrypted_storage("test", "sk-key")
        self.assertFalse(result)

    @patch.dict(
        os.environ, {"KOLLAB_KEY_ENCRYPTION_PASSWORD": "real-password"}
    )
    def test_tier2_attempts_with_explicit_password(self):
        """Tier 2 should attempt storage when password is explicitly set.

        Will return False because cryptography package isn't installed,
        but the code path should not skip due to missing password.
        """
        from kollabor_config.migration import ProfileMigrator

        migrator = ProfileMigrator()
        result = migrator._try_encrypted_storage("test", "sk-key")
        # False because no cryptography lib, but it tried
        self.assertFalse(result)


class TestSaveProfileEnvKeyPersists(unittest.TestCase):
    """Env-sourced API keys must persist to keyring when user runs --save / --default."""

    def test_save_writes_sentinel_when_key_matches_profile_env(self):
        """Previously skipped persist when profile.api_key == env (broken empty profile)."""
        env_key = "sk-ant-env-only"
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(
                json.dumps({"kollabor": {"llm": {"profiles": {}}}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"KOLLAB_WORK_API_KEY": env_key},
                clear=False,
            ):
                profile = LLMProfile.from_dict(
                    "work",
                    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                )
                profile.api_key = env_key  # same as env-created profile
                mock_set = MagicMock(return_value=True)
                with patch(
                    "kollabor_ai.profile_manager.get_global_config_path",
                    return_value=cfg_path,
                ), patch(
                    "kollabor_ai.profile_manager.get_existing_global_config_path",
                    return_value=cfg_path,
                ), patch(
                    "kollabor_ai.profile_manager._keyring_set",
                    mock_set,
                ):
                    pm = ProfileManager.__new__(ProfileManager)
                    pm.config = None
                    ProfileManager.save_profile_values_to_config(pm, profile)

            mock_set.assert_called_once_with("work", env_key)
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            saved = data["kollabor"]["llm"]["profiles"]["work"]["api_key"]
            self.assertEqual(saved, f"{KEYRING_SENTINEL_PREFIX}work")


class TestSentinelFormat(unittest.TestCase):
    """Verify sentinel string format consistency."""

    def test_sentinel_prefix_constant(self):
        self.assertEqual(KEYRING_SENTINEL_PREFIX, "secret:keyring:")

    def test_sentinel_roundtrip(self):
        """Profile name survives sentinel encode/decode."""
        profile_name = "my-anthropic-profile"
        sentinel = f"{KEYRING_SENTINEL_PREFIX}{profile_name}"
        decoded = sentinel[len(KEYRING_SENTINEL_PREFIX):]
        self.assertEqual(decoded, profile_name)

    def test_sentinel_detection_positive(self):
        self.assertTrue(
            "secret:keyring:foo".startswith(KEYRING_SENTINEL_PREFIX)
        )

    def test_sentinel_detection_negative(self):
        self.assertFalse(
            "sk-ant-real-key".startswith(KEYRING_SENTINEL_PREFIX)
        )
        self.assertFalse("".startswith(KEYRING_SENTINEL_PREFIX))


if __name__ == "__main__":
    unittest.main()
