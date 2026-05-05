"""
Unit tests for configuration extension with provider support.

Tests provider validation, OpenAI-specific fields, configuration version tracking,
backward compatibility, and auto-detection from API key format.

Target: 70%+ coverage for new code.
"""

import tempfile
from pathlib import Path

import pytest

from kollabor_config.loader import (
    CONFIG_VERSION,
    VERSION_KEY,
    ConfigLoader,
    ConfigurationValidationError,
    detect_provider_from_api_key,
    mask_api_key,
    validate_openai_config,
    validate_provider_type,
)
from kollabor_config.manager import (
    ConfigManager,
    ConfigurationVersionError,
)


class TestDetectProviderFromApiKey:
    """Test provider detection from API key format."""

    def test_detect_openai_key(self):
        """Test detection of OpenAI API key."""
        assert detect_provider_from_api_key("sk-test-key-123") == "openai"

    def test_detect_openai_proj_key(self):
        """Test detection of OpenAI project key."""
        assert detect_provider_from_api_key("sk-proj-abc-xyz") == "openai"

    def test_detect_anthropic_key(self):
        """Test detection of Anthropic API key."""
        assert detect_provider_from_api_key("sk-ant-test-key-123") == "anthropic"

    def test_detect_empty_key(self):
        """Test detection with empty API key."""
        assert detect_provider_from_api_key("") == "auto"

    def test_detect_none_key(self):
        """Test detection with None API key."""
        assert detect_provider_from_api_key(None) == "auto"

    def test_detect_unknown_format(self):
        """Test detection with unknown API key format."""
        assert detect_provider_from_api_key("unknown-format") == "auto"

    def test_detect_case_insensitive(self):
        """Test detection is case insensitive."""
        assert detect_provider_from_api_key("SK-OPENAI") == "openai"
        assert detect_provider_from_api_key("SK-ANT-ANTHROPIC") == "anthropic"


class TestValidateProviderType:
    """Test provider type validation."""

    def test_valid_provider_openai(self):
        """Test valid OpenAI provider."""
        assert validate_provider_type("openai") is True

    def test_valid_provider_anthropic(self):
        """Test valid Anthropic provider."""
        assert validate_provider_type("anthropic") is True

    def test_valid_provider_azure_openai(self):
        """Test valid Azure OpenAI provider."""
        assert validate_provider_type("azure_openai") is True

    def test_valid_provider_auto(self):
        """Test valid auto provider."""
        assert validate_provider_type("auto") is True

    def test_invalid_provider(self):
        """Test invalid provider raises error."""
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_provider_type("invalid_provider")
        assert "Invalid provider" in str(exc_info.value)
        assert "invalid_provider" in str(exc_info.value)

    def test_invalid_provider_empty(self):
        """Test empty provider raises error."""
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_provider_type("")
        assert "Invalid provider" in str(exc_info.value)


class TestValidateOpenAIConfig:
    """Test OpenAI-specific configuration validation."""

    def test_valid_minimal_config(self):
        """Test valid minimal OpenAI config."""
        config = {"model": "gpt-4"}
        validate_openai_config(config)  # Should not raise

    def test_valid_full_config(self):
        """Test valid full OpenAI config."""
        config = {
            "model": "gpt-4",
            "temperature": 0.7,
            "max_tokens": 4096,
            "base_url": "https://api.openai.com/v1",
            "organization": "org-123",
        }
        validate_openai_config(config)  # Should not raise

    def test_missing_model_field(self):
        """Test missing model field raises error."""
        config = {}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "model field is required" in str(exc_info.value)

    def test_empty_model_field(self):
        """Test empty model field raises error."""
        config = {"model": ""}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "model must be a non-empty string" in str(exc_info.value)

    def test_invalid_temperature_type(self):
        """Test invalid temperature type raises error."""
        config = {"model": "gpt-4", "temperature": "invalid"}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "temperature must be a number" in str(exc_info.value)

    def test_temperature_out_of_range_high(self):
        """Test temperature above maximum raises error."""
        config = {"model": "gpt-4", "temperature": 2.5}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "temperature must be between 0.0 and 2.0" in str(exc_info.value)

    def test_temperature_out_of_range_low(self):
        """Test temperature below minimum raises error."""
        config = {"model": "gpt-4", "temperature": -0.1}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "temperature must be between 0.0 and 2.0" in str(exc_info.value)

    def test_invalid_max_tokens_type(self):
        """Test invalid max_tokens type raises error."""
        config = {"model": "gpt-4", "max_tokens": "invalid"}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "max_tokens must be an integer" in str(exc_info.value)

    def test_max_tokens_too_small(self):
        """Test max_tokens less than 1 raises error."""
        config = {"model": "gpt-4", "max_tokens": 0}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "max_tokens must be >= 1" in str(exc_info.value)

    def test_invalid_base_url_type(self):
        """Test invalid base_url type raises error."""
        config = {"model": "gpt-4", "base_url": 123}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "base_url must be a string" in str(exc_info.value)

    def test_invalid_base_url_format(self):
        """Test invalid base_url format raises error."""
        config = {"model": "gpt-4", "base_url": "not-a-url"}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "base_url must be a valid URL" in str(exc_info.value)

    def test_valid_base_url_localhost(self):
        """Test localhost base_url is valid."""
        config = {"model": "gpt-4", "base_url": "http://localhost:8000"}
        validate_openai_config(config)  # Should not raise

    def test_valid_base_url_127_0_0_1(self):
        """Test 127.0.0.1 base_url is valid."""
        config = {"model": "gpt-4", "base_url": "http://127.0.0.1:8080"}
        validate_openai_config(config)  # Should not raise

    def test_invalid_organization_type(self):
        """Test invalid organization type raises error."""
        config = {"model": "gpt-4", "organization": 123}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "organization must be a string" in str(exc_info.value)

    def test_api_key_wrong_type(self):
        """Test api_key with wrong type raises error."""
        config = {"model": "gpt-4", "api_key": 123}
        with pytest.raises(ConfigurationValidationError) as exc_info:
            validate_openai_config(config)
        assert "api_key must be a string" in str(exc_info.value)


class TestMaskApiKey:
    """Test API key masking for logging."""

    def test_mask_none_key(self):
        """Test masking None key."""
        assert mask_api_key(None) == "<not set>"

    def test_mask_empty_key(self):
        """Test masking empty key."""
        assert mask_api_key("") == "<not set>"

    def test_mask_short_key(self):
        """Test masking short key."""
        assert mask_api_key("short") == "***"

    def test_mask_normal_key(self):
        """Test masking normal API key."""
        assert mask_api_key("sk-test-key-123") == "sk-t...-123"
        assert mask_api_key("sk-ant-api-key-456") == "sk-a...-456"

    def test_mask_long_key(self):
        """Test masking long API key."""
        long_key = "sk-" + "a" * 50
        masked = mask_api_key(long_key)
        assert masked.startswith("sk-a...")
        assert len(masked) < len(long_key)


class TestConfigLoaderVersionTracking:
    """Test configuration version tracking in ConfigLoader."""

    def test_get_config_version_default(self):
        """Test getting version from config without version key."""
        config = {}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump(config, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            assert loader.get_config_version(config) == 1

    def test_get_config_version_explicit(self):
        """Test getting explicit config version."""
        config = {VERSION_KEY: 2}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump(config, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            assert loader.get_config_version(config) == 2

    def test_set_config_version(self):
        """Test setting config version."""
        config = {}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump(config, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            loader.set_config_version(config, 2)
            assert config[VERSION_KEY] == 2

    def test_validate_provider_config_openai(self):
        """Test validating OpenAI provider config."""
        config = {"model": "gpt-4", "temperature": 0.7}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            loader.validate_provider_config("openai", config)  # Should not raise

    def test_validate_provider_config_invalid_provider(self):
        """Test validating with invalid provider."""
        config = {"model": "gpt-4"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            with pytest.raises(ConfigurationValidationError):
                loader.validate_provider_config("invalid", config)

    def test_validate_provider_config_anthropic_requires_model(self):
        """Test Anthropic config requires model."""
        config = {}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            with pytest.raises(ConfigurationValidationError) as exc_info:
                loader.validate_provider_config("anthropic", config)
            assert "model field is required" in str(exc_info.value)

    def test_validate_provider_config_azure_requires_endpoint(self):
        """Test Azure config requires endpoint."""
        config = {"model": "gpt-4"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            with pytest.raises(ConfigurationValidationError) as exc_info:
                loader.validate_provider_config("azure_openai", config)
            assert "azure_endpoint field is required" in str(exc_info.value)

    def test_validate_provider_config_auto_no_validation(self):
        """Test auto provider doesn't require validation."""
        config = {}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            loader.validate_provider_config("auto", config)  # Should not raise


class TestConfigMigration:
    """Test configuration migration from v1 to v2."""

    def test_migrate_current_version_no_change(self):
        """Test config at current version is not modified."""
        config = {VERSION_KEY: CONFIG_VERSION, "kollabor": {"llm": {}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            result = loader.migrate_config(config)
            assert result[VERSION_KEY] == CONFIG_VERSION

    def test_migrate_v1_to_v2_openai_key(self):
        """Test migration from v1 to v2 detects OpenAI provider."""
        config = {"core": {"llm": {"api_token": "sk-test-key-123"}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            result = loader._migrate_v1_to_v2(config)
            assert result["kollabor"]["llm"]["provider"] == "openai"
            # _migrate_v1_to_v2 doesn't set version, migrate_config does
            # So we verify the provider detection worked correctly

    def test_migrate_v1_to_v2_anthropic_key(self):
        """Test migration from v1 to v2 detects Anthropic provider."""
        config = {"core": {"llm": {"api_key": "sk-ant-test-key-123"}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            result = loader._migrate_v1_to_v2(config)
            assert result["kollabor"]["llm"]["provider"] == "anthropic"

    def test_migrate_v1_to_v2_no_key_sets_auto(self):
        """Test migration without API key sets provider to auto."""
        config = {"core": {"llm": {}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            result = loader._migrate_v1_to_v2(config)
            assert result["kollabor"]["llm"]["provider"] == "auto"


class TestConfigManagerVersionTracking:
    """Test configuration version tracking in ConfigManager."""

    def test_get_config_version_default(self):
        """Test getting default version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.get_config_version() == 1

    def test_get_config_version_explicit(self):
        """Test getting explicit version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: 2}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.get_config_version() == 2

    def test_set_config_version(self):
        """Test setting config version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()
            manager.set_config_version(2)

            assert manager.get_config_version() == 2
            assert manager.config[VERSION_KEY] == 2

    def test_is_config_version_current(self):
        """Test checking if config is at current version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: CONFIG_VERSION}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.is_config_version_current() is True

    def test_is_config_version_not_current(self):
        """Test checking if config is not at current version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: 1}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.is_config_version_current() is False

    def test_get_min_supported_version(self):
        """Test getting minimum supported version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.get_min_supported_version() == 1

    def test_is_config_version_supported(self):
        """Test checking if version is supported."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: 1}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.is_config_version_supported(1) is True
            assert manager.is_config_version_supported(2) is True
            assert manager.is_config_version_supported(3) is False

    def test_validate_config_version_supported(self):
        """Test validating supported version doesn't raise."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: CONFIG_VERSION}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            manager.validate_config_version()  # Should not raise

    def test_validate_config_version_unsupported(self):
        """Test validating unsupported version raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({VERSION_KEY: 999}, f)
            f.flush()

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            with pytest.raises(ConfigurationVersionError) as exc_info:
                manager.validate_config_version()
            assert "Unsupported configuration version" in str(exc_info.value)


class TestBackwardCompatibility:
    """Test backward compatibility with existing configs."""

    def test_load_config_without_version_key(self):
        """Test loading config without version key defaults to v1."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({"core": {"llm": {}}}, f)
            f.flush()

            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            manager.load_config_file()

            assert manager.get_config_version() == 1

    def test_config_with_api_token_migrated(self):
        """Test config with api_token is migrated to use provider field."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            old_config = {"core": {"llm": {"api_token": "sk-test-key-123"}}}
            json.dump(old_config, f)
            f.flush()

            from kollabor_config.loader import ConfigLoader
            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            # Load and migrate
            manager.load_config_file()
            migrated = loader.migrate_config(manager.config)

            assert migrated["kollabor"]["llm"]["provider"] == "openai"
            assert migrated[VERSION_KEY] == 2

    def test_config_with_api_key_migrated(self):
        """Test config with api_key is migrated to use provider field."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            old_config = {"core": {"llm": {"api_key": "sk-ant-test-key-123"}}}
            json.dump(old_config, f)
            f.flush()

            from kollabor_config.loader import ConfigLoader
            from kollabor_config.manager import ConfigManager

            manager = ConfigManager(Path(f.name))
            loader = ConfigLoader(manager)

            # Load and migrate
            manager.load_config_file()
            migrated = loader.migrate_config(manager.config)

            assert migrated["kollabor"]["llm"]["provider"] == "anthropic"
            assert migrated[VERSION_KEY] == 2
