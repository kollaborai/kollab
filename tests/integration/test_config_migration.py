"""
Integration tests for configuration migration.

Tests automatic profile migration with:
- Provider detection (FIXED ORDER from spec)
- 4-tier keyring fallback migration
- Atomic migration with temp files
- Rollback on failure
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kollabor_config.loader import CONFIG_VERSION, VERSION_KEY
from kollabor_config.migration import (
    ProfileMigrationError,
    ProfileMigrator,
    detect_provider_from_profile,
    migrate_config,
)


class TestProviderDetection(unittest.TestCase):
    """Test provider detection using FIXED ORDER from spec."""

    def test_detect_explicit_provider_field(self):
        """Test explicit provider field (highest priority)."""
        profile = {"provider": "anthropic", "api_key": "sk-anything"}
        self.assertEqual(detect_provider_from_profile(profile), "anthropic")

    def test_detect_azure_by_azure_base_url(self):
        """Test Azure detection via azure_base_url field."""
        profile = {
            "azure_base_url": "https://test.openai.azure.com/",
            "api_key": "sk-some-key",
        }
        self.assertEqual(detect_provider_from_profile(profile), "azure_openai")

    def test_detect_azure_by_api_type(self):
        """Test Azure detection via api_type=azure."""
        profile = {"api_type": "azure", "api_key": "sk-some-key"}
        self.assertEqual(detect_provider_from_profile(profile), "azure_openai")

    def test_detect_azure_in_api_base(self):
        """Test Azure detection via azure.com in api_base."""
        profile = {"api_base": "https://custom.azure.com/", "api_key": "sk-some-key"}
        self.assertEqual(detect_provider_from_profile(profile), "azure_openai")

    def test_detect_azure_openai_com_in_api_base(self):
        """Test Azure detection via azure.openai.com in api_base."""
        profile = {
            "api_base": "https://test.azure.openai.com/",
            "api_key": "sk-some-key",
        }
        self.assertEqual(detect_provider_from_profile(profile), "azure_openai")

    def test_detect_anthropic_by_sk_ant_prefix(self):
        """Test Anthropic detection via sk-ant- prefix (more specific than sk-)."""
        profile = {"api_key": "sk-ant-api123-test-key"}
        self.assertEqual(detect_provider_from_profile(profile), "anthropic")

    def test_detect_openai_by_sk_prefix(self):
        """Test OpenAI detection via sk- prefix (excludes Azure)."""
        profile = {"api_key": "sk-proj-test-key"}
        self.assertEqual(detect_provider_from_profile(profile), "openai")

    def test_detect_anthropic_from_api_base(self):
        """Test Anthropic detection via anthropic.com in api_base."""
        profile = {"api_base": "https://api.anthropic.com", "api_key": "custom-key"}
        self.assertEqual(detect_provider_from_profile(profile), "anthropic")

    def test_detect_openai_from_api_base(self):
        """Test OpenAI detection via openai.com in api_base (no Azure)."""
        profile = {"api_base": "https://api.openai.com/v1", "api_key": "custom-key"}
        self.assertEqual(detect_provider_from_profile(profile), "openai")

    def test_detect_default_auto(self):
        """Test default to 'auto' when no clues present."""
        profile = {"model": "gpt-4", "temperature": 0.7}
        self.assertEqual(detect_provider_from_profile(profile), "auto")

    def test_detect_priority_azure_over_anthropic(self):
        """Test Azure has priority over Anthropic in detection."""
        profile = {
            "azure_base_url": "https://test.openai.azure.com/",
            "api_key": "sk-ant-test-key",  # Anthropic-style key
        }
        # Azure should win because of azure_base_url field
        self.assertEqual(detect_provider_from_profile(profile), "azure_openai")

    def test_detect_priority_anthropic_over_openai(self):
        """Test Anthropic has priority over OpenAI in key format."""
        profile = {"api_key": "sk-ant-test-key"}
        self.assertEqual(detect_provider_from_profile(profile), "anthropic")

    def test_detect_openai_sk_prefix_excludes_azure(self):
        """Test OpenAI sk- prefix excludes Azure when azure.com not in api_base."""
        profile = {"api_key": "sk-proj-test", "api_base": "https://custom-endpoint.com"}
        self.assertEqual(detect_provider_from_profile(profile), "openai")


class TestProfileMigrator(unittest.TestCase):
    """Test profile migration logic."""

    def setUp(self):
        """Set up test environment with temp config file."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "config.json"
        self.migrator = ProfileMigrator(self.config_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migrate_v1_to_v2_adds_provider_field(self):
        """Test migration adds provider field to profiles."""
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "openai-gpt4": {"api_key": "sk-proj-test-key", "model": "gpt-4"}
                    }
                }
            },
        }

        migrated = self.migrator._migrate_config(config)

        # Check version updated
        self.assertEqual(migrated[VERSION_KEY], CONFIG_VERSION)

        # Check provider field added
        profile = migrated["kollabor"]["llm"]["profiles"]["openai-gpt4"]
        self.assertEqual(profile["provider"], "openai")

    def test_migrate_anthropic_profile(self):
        """Test migration of Anthropic profile."""
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "anthropic-claude": {
                            "api_key": "sk-ant-test-key",
                            "model": "claude-3-opus-20240229",
                        }
                    }
                }
            },
        }

        migrated = self.migrator._migrate_config(config)
        profile = migrated["kollabor"]["llm"]["profiles"]["anthropic-claude"]

        self.assertEqual(profile["provider"], "anthropic")

    def test_migrate_azure_profile(self):
        """Test migration of Azure OpenAI profile."""
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "azure-gpt4": {
                            "azure_base_url": "https://test.openai.azure.com/",
                            "api_key": "sk-test-key",
                            "model": "gpt-4",
                        }
                    }
                }
            },
        }

        migrated = self.migrator._migrate_config(config)
        profile = migrated["kollabor"]["llm"]["profiles"]["azure-gpt4"]

        self.assertEqual(profile["provider"], "azure_openai")

    def test_migrate_multiple_profiles(self):
        """Test migration of multiple profiles with different providers."""
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "openai-gpt4": {"api_key": "sk-proj-test", "model": "gpt-4"},
                        "anthropic-claude": {
                            "api_key": "sk-ant-test",
                            "model": "claude-3-opus-20240229",
                        },
                        "azure-gpt4": {
                            "azure_base_url": "https://test.openai.azure.com/",
                            "api_key": "sk-test",
                            "model": "gpt-4",
                        },
                    }
                }
            },
        }

        migrated = self.migrator._migrate_config(config)
        profiles = migrated["kollabor"]["llm"]["profiles"]

        self.assertEqual(profiles["openai-gpt4"]["provider"], "openai")
        self.assertEqual(profiles["anthropic-claude"]["provider"], "anthropic")
        self.assertEqual(profiles["azure-gpt4"]["provider"], "azure_openai")

    def test_migrate_preserves_existing_fields(self):
        """Test migration preserves existing profile fields."""
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "test-profile": {
                            "api_key": "sk-test",
                            "model": "gpt-4",
                            "temperature": 0.7,
                            "max_tokens": 1000,
                        }
                    }
                }
            },
        }

        migrated = self.migrator._migrate_config(config)
        profile = migrated["kollabor"]["llm"]["profiles"]["test-profile"]

        self.assertEqual(profile["model"], "gpt-4")
        self.assertEqual(profile["temperature"], 0.7)
        self.assertEqual(profile["max_tokens"], 1000)


class TestKeyringMigration(unittest.TestCase):
    """Test 4-tier keyring fallback migration."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "config.json"
        self.migrator = ProfileMigrator(self.config_path)

    def tearDown(self):
        """Clean up temp files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(os.environ, {"KOLLAB_ALLOW_PLAINTEXT_KEYS": "true"})
    def test_migrate_to_tier4_plaintext_storage(self):
        """Test migration to Tier 4 plaintext storage (opt-in)."""
        profile = {"api_key": "sk-test-key-12345", "model": "gpt-4"}

        # Migrate to plaintext (Tier 4)
        result = self.migrator._try_plaintext_storage(
            "test-profile", profile["api_key"]
        )

        # Should succeed with opt-in
        self.assertTrue(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_plaintext_storage_requires_opt_in(self):
        """Test plaintext storage requires opt-in."""
        profile = {"api_key": "sk-test-key", "model": "gpt-4"}

        # Remove opt-in
        os.environ.pop("KOLLAB_ALLOW_PLAINTEXT_KEYS", None)

        result = self.migrator._try_plaintext_storage(
            "test-profile", profile["api_key"]
        )

        # Should fail without opt-in
        self.assertFalse(result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key-12345"})
    def test_migrate_to_tier3_env_variable(self):
        """Test migration to Tier 3 environment variable (check only)."""
        profile = {"api_key": "sk-env-key-12345", "model": "gpt-4"}

        result = self.migrator._check_env_variable(
            "test-profile", "openai", profile["api_key"]
        )

        # Should succeed if env var matches
        self.assertTrue(result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-different-key"})
    def test_env_variable_fails_on_mismatch(self):
        """Test environment variable check fails on mismatch."""
        profile = {"api_key": "sk-config-key", "model": "gpt-4"}

        result = self.migrator._check_env_variable(
            "test-profile", "openai", profile["api_key"]
        )

        # Should fail on mismatch
        self.assertFalse(result)


class TestAtomicMigration(unittest.TestCase):
    """Test atomic migration with temp files and rollback."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "config.json"
        self.backup_path = self.config_path.with_suffix(".backup")
        self.temp_path = self.config_path.with_suffix(".tmp")

    def tearDown(self):
        """Clean up temp files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_atomic_write_creates_temp_then_renames(self):
        """Test atomic write uses temp file then atomic rename."""
        migrator = ProfileMigrator(self.config_path)
        config = {"test": "data"}

        # Perform atomic write
        migrator._atomic_write(config)

        # Temp file should not exist after atomic rename
        self.assertFalse(self.temp_path.exists())

        # Config file should exist with data
        self.assertTrue(self.config_path.exists())
        with open(self.config_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["test"], "data")

    def test_atomic_write_preserves_on_failure(self):
        """Test atomic write preserves original on failure."""
        migrator = ProfileMigrator(self.config_path)

        # Create initial config
        initial_config = {"version": 1, "test": "initial"}
        with open(self.config_path, "w") as f:
            json.dump(initial_config, f)

        # Simulate failed write by mocking file operations
        with patch("builtins.open", side_effect=IOError("Write failed")):
            try:
                migrator._atomic_write({"version": 2, "test": "new"})
            except IOError:
                pass

        # Original config should be preserved
        with open(self.config_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(loaded["test"], "initial")

    def test_backup_created_before_migration(self):
        """Test backup is created before migration."""
        # Create initial config
        config = {
            VERSION_KEY: 1,
            "core": {"llm": {"profiles": {"test": {"api_key": "sk-test"}}}},
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        migrator = ProfileMigrator(self.config_path)

        # Create backup
        migrator._create_backup()

        # Backup should exist
        self.assertTrue(self.backup_path.exists())

        # Backup should match original
        with open(self.backup_path, "r") as f:
            backup_data = json.load(f)
        self.assertEqual(backup_data[VERSION_KEY], 1)

    def test_rollback_from_backup(self):
        """Test rollback from backup restores original config."""
        # Create initial and backup configs
        original_config = {VERSION_KEY: 1, "test": "original"}
        backup_config = {VERSION_KEY: 1, "test": "backup"}

        with open(self.config_path, "w") as f:
            json.dump(original_config, f)
        with open(self.backup_path, "w") as f:
            json.dump(backup_config, f)

        migrator = ProfileMigrator(self.config_path)

        # Rollback from backup
        migrator._rollback_from_backup()

        # Config should match backup
        with open(self.config_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["test"], "backup")

    def test_migration_rollback_on_validation_error(self):
        """Test migration rolls back on validation error."""
        # Create v1 config
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "test-profile": {"api_key": "sk-test", "model": "gpt-4"}
                    }
                }
            },
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        migrator = ProfileMigrator(self.config_path)

        # Mock validation to fail
        with patch.object(
            migrator,
            "_validate_migrated_config",
            side_effect=ValueError("Validation failed"),
        ):
            with self.assertRaises(ProfileMigrationError):
                migrator.migrate_all_profiles(config)

        # Config should be restored from backup
        with open(self.config_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded[VERSION_KEY], 1)  # Should still be v1

    def test_full_migration_success(self):
        """Test complete migration success with all steps."""
        # Create v1 config
        config = {
            VERSION_KEY: 1,
            "core": {
                "llm": {
                    "profiles": {
                        "openai-gpt4": {"api_key": "sk-test", "model": "gpt-4"}
                    }
                }
            },
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        migrator = ProfileMigrator(self.config_path)

        # Mock keyring migration to avoid actual keyring operations
        with patch.object(migrator, "_migrate_api_key_to_keyring"):
            migrated = migrator.migrate_all_profiles(config)

        # Check migration succeeded
        self.assertEqual(migrated[VERSION_KEY], CONFIG_VERSION)
        profile = migrated["kollabor"]["llm"]["profiles"]["openai-gpt4"]
        self.assertEqual(profile["provider"], "openai")


class TestMigrateConfigPublicAPI(unittest.TestCase):
    """Test public migrate_config() API."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "config.json"

    def tearDown(self):
        """Clean up temp files."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migrate_config_nonexistent_file(self):
        """Test migrate_config with nonexistent file."""
        result = migrate_config(self.config_path)
        self.assertEqual(result, {})

    def test_migrate_config_already_at_current_version(self):
        """Test migrate_config returns config unchanged if already at current version."""
        config = {
            VERSION_KEY: CONFIG_VERSION,
            "kollabor": {
                "llm": {"profiles": {"test": {"provider": "openai", "model": "gpt-4"}}}
            },
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        result = migrate_config(self.config_path)

        # Config should be returned unchanged (version should still match)
        self.assertEqual(result[VERSION_KEY], CONFIG_VERSION)
        profile = result["kollabor"]["llm"]["profiles"]["test"]
        self.assertEqual(profile["provider"], "openai")
        self.assertEqual(profile["model"], "gpt-4")


if __name__ == "__main__":
    unittest.main()
