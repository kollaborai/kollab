"""Integration tests for profile command with OpenAI wizard.

Tests profile creation wizard functionality:
- OpenAI profile creation wizard with all inputs
- Profile validation using Phase 7a config extension
- Masked API key input (first 3 chars + ... + last 4 chars)
- Provider auto-detection from API key format
- Azure OpenAI profile creation
- Profile testing with mocked API calls
- Error handling (invalid key, API errors)

Phase 7c: 120+ lines of tests
"""

import unittest
from unittest.mock import Mock

from kollabor.commands.profile_command import (
    ProfileCreationWizard,
    ProfileWizardConfig,
    ProfileWizardState,
    create_profile_wizard,
    get_provider_display_name,
)
from kollabor_config.loader import mask_api_key


class TestProfileWizardState(unittest.TestCase):
    """Test ProfileWizardState dataclass."""

    def test_initial_state(self) -> None:
        """Test wizard state initialization."""
        state = ProfileWizardState()
        self.assertEqual(state.step, 0)
        self.assertEqual(state.profile_name, "")
        self.assertEqual(state.provider, "auto")
        self.assertEqual(state.api_key, "")
        self.assertEqual(state.model, "gpt-4")
        self.assertEqual(state.temperature, 0.7)
        self.assertEqual(state.max_tokens, 4096)
        self.assertEqual(state.base_url, "")
        self.assertEqual(state.organization, "")
        # Advanced settings defaults
        self.assertEqual(state.description, "")
        self.assertEqual(state.timeout, 0)
        self.assertTrue(state.supports_tools)
        self.assertTrue(state.streaming)
        self.assertFalse(state.configure_advanced)
        self.assertEqual(state.errors, [])

    def test_state_with_values(self) -> None:
        """Test wizard state with custom values."""
        state = ProfileWizardState(
            step=2,
            profile_name="test-profile",
            provider="openai",
            api_key="sk-test123",
            model="gpt-4-turbo",
            temperature=1.0,
            max_tokens=8192,
        )
        self.assertEqual(state.step, 2)
        self.assertEqual(state.profile_name, "test-profile")
        self.assertEqual(state.provider, "openai")
        self.assertEqual(state.api_key, "sk-test123")


class TestProfileWizardConfig(unittest.TestCase):
    """Test ProfileWizardConfig configuration."""

    def test_wizard_steps(self) -> None:
        """Test wizard steps are defined correctly."""
        # BASIC_STEPS are the core steps
        self.assertIn("profile_name", ProfileWizardConfig.BASIC_STEPS)
        self.assertIn("provider", ProfileWizardConfig.BASIC_STEPS)
        self.assertIn("api_key", ProfileWizardConfig.BASIC_STEPS)
        self.assertIn("model", ProfileWizardConfig.BASIC_STEPS)
        self.assertIn("temperature", ProfileWizardConfig.BASIC_STEPS)
        self.assertEqual(len(ProfileWizardConfig.BASIC_STEPS), 8)

    def test_step_definitions(self) -> None:
        """Test step definitions exist for all steps."""
        # Check that all basic and advanced steps have definitions
        all_steps = set(
            ProfileWizardConfig.BASIC_STEPS
            + ProfileWizardConfig.ADVANCED_STEPS
            + ["advanced_settings_prompt"]
        )
        for step in all_steps:
            self.assertIn(step, ProfileWizardConfig.STEP_DEFINITIONS)
            step_def = ProfileWizardConfig.STEP_DEFINITIONS[step]
            self.assertIn("prompt", step_def)
            self.assertIn("help", step_def)


class TestProfileCreationWizard(unittest.TestCase):
    """Test ProfileCreationWizard class."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.profile_manager.create_profile = Mock(return_value=Mock())
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_initialization(self) -> None:
        """Test wizard initialization."""
        self.assertEqual(self.wizard.state.step, 0)
        self.assertIsNotNone(self.wizard.config)

    def test_reset(self) -> None:
        """Test wizard reset."""
        self.wizard.state.step = 5
        self.wizard.state.profile_name = "test"
        self.wizard.reset()
        self.assertEqual(self.wizard.state.step, 0)
        self.assertEqual(self.wizard.state.profile_name, "")

    def test_get_current_step(self) -> None:
        """Test getting current step."""
        self.assertEqual(self.wizard.get_current_step(), "profile_name")
        self.wizard.state.step = 1
        self.assertEqual(self.wizard.get_current_step(), "provider")
        # Step 8 is advanced_settings_prompt (after 8 basic steps)
        self.wizard.state.step = 8
        self.assertEqual(self.wizard.get_current_step(), "advanced_settings_prompt")
        # Step 9 is complete (when not using advanced settings)
        self.wizard.state.step = 9
        self.assertEqual(self.wizard.get_current_step(), "complete")

    def test_advance_step(self) -> None:
        """Test advancing to next step."""
        self.assertEqual(self.wizard.state.step, 0)
        next_step = self.wizard.advance_step()
        self.assertEqual(self.wizard.state.step, 1)
        self.assertEqual(next_step, "provider")

    def test_is_complete(self) -> None:
        """Test completion check."""
        self.assertFalse(self.wizard.is_complete())
        # After step 8 (advanced_settings_prompt), should not be complete if advanced settings are enabled
        self.wizard.state.step = 8
        self.assertFalse(self.wizard.is_complete())
        # After step 9 (or after advanced_settings_prompt with no advanced settings), should be complete
        self.wizard.state.step = 9
        self.assertTrue(self.wizard.is_complete())


class TestProfileWizardValidation(unittest.TestCase):
    """Test profile wizard validation."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_validate_profile_name_valid(self) -> None:
        """Test valid profile name."""
        result = self.wizard._validate_profile_name("test-profile")
        self.assertTrue(result.success)

    def test_validate_profile_name_empty(self) -> None:
        """Test empty profile name."""
        result = self.wizard._validate_profile_name("")
        self.assertFalse(result.success)
        self.assertIn("required", result.message)

    def test_validate_profile_name_too_short(self) -> None:
        """Test profile name too short."""
        result = self.wizard._validate_profile_name("ab")
        self.assertFalse(result.success)
        self.assertIn("at least 3 characters", result.message)

    def test_validate_profile_name_invalid_chars(self) -> None:
        """Test profile name with invalid characters."""
        result = self.wizard._validate_profile_name("test profile!")
        self.assertFalse(result.success)
        self.assertIn("letters, numbers, hyphens, and underscores", result.message)

    def test_validate_profile_name_already_exists(self) -> None:
        """Test profile name that already exists."""
        self.profile_manager.get_profile_names = Mock(return_value=["existing-profile"])
        result = self.wizard._validate_profile_name("existing-profile")
        self.assertFalse(result.success)
        self.assertIn("already exists", result.message)

    def test_validate_provider_valid(self) -> None:
        """Test valid provider types."""
        for provider in ["auto", "openai", "anthropic", "azure_openai"]:
            result = self.wizard._validate_provider(provider)
            self.assertTrue(result.success, f"Provider {provider} should be valid")

    def test_validate_provider_invalid(self) -> None:
        """Test invalid provider type."""
        result = self.wizard._validate_provider("invalid")
        self.assertFalse(result.success)
        self.assertIn("Invalid provider", result.message)

    def test_validate_api_key_valid_openai(self) -> None:
        """Test valid OpenAI API key."""
        result = self.wizard._validate_api_key("sk-example-openai-key")
        self.assertTrue(result.success)

    def test_validate_api_key_valid_anthropic(self) -> None:
        """Test valid Anthropic API key."""
        result = self.wizard._validate_api_key("sk-ant-1234567890abcdef")
        self.assertTrue(result.success)

    def test_validate_api_key_empty(self) -> None:
        """Test empty API key."""
        result = self.wizard._validate_api_key("")
        self.assertFalse(result.success)
        self.assertIn("required", result.message)

    def test_validate_api_key_too_short(self) -> None:
        """Test API key too short."""
        result = self.wizard._validate_api_key("short")
        self.assertFalse(result.success)
        self.assertIn("too short", result.message)

    def test_validate_temperature_valid(self) -> None:
        """Test valid temperature values."""
        for temp in ["0.0", "0.7", "1.0", "2.0"]:
            result = self.wizard._validate_temperature(temp)
            self.assertTrue(result.success, f"Temperature {temp} should be valid")

    def test_validate_temperature_invalid_range(self) -> None:
        """Test temperature out of range."""
        result = self.wizard._validate_temperature("2.5")
        self.assertFalse(result.success)
        self.assertIn("between 0.0 and 2.0", result.message)

    def test_validate_temperature_invalid_format(self) -> None:
        """Test temperature with invalid format."""
        result = self.wizard._validate_temperature("invalid")
        self.assertFalse(result.success)
        self.assertIn("must be a number", result.message)

    def test_validate_max_tokens_valid(self) -> None:
        """Test valid max tokens values."""
        for tokens in ["1", "100", "4096", "8192"]:
            result = self.wizard._validate_max_tokens(tokens)
            self.assertTrue(result.success, f"Max tokens {tokens} should be valid")

    def test_validate_max_tokens_invalid(self) -> None:
        """Test max tokens less than 1."""
        result = self.wizard._validate_max_tokens("0")
        self.assertFalse(result.success)
        self.assertIn("must be >= 1", result.message)

    def test_validate_max_tokens_invalid_format(self) -> None:
        """Test max tokens with invalid format."""
        result = self.wizard._validate_max_tokens("invalid")
        self.assertFalse(result.success)
        self.assertIn("must be an integer", result.message)

    def test_validate_base_url_valid(self) -> None:
        """Test valid base URL."""
        result = self.wizard._validate_base_url("https://api.openai.com/v1")
        self.assertTrue(result.success)

    def test_validate_base_url_valid_localhost(self) -> None:
        """Test valid localhost base URL."""
        result = self.wizard._validate_base_url("http://localhost:1234")
        self.assertTrue(result.success)

    def test_validate_base_url_invalid(self) -> None:
        """Test invalid base URL."""
        result = self.wizard._validate_base_url("not-a-url")
        self.assertFalse(result.success)
        self.assertIn("Invalid URL format", result.message)

    def test_validate_base_url_empty(self) -> None:
        """Test empty base URL (optional field)."""
        result = self.wizard._validate_base_url("")
        self.assertTrue(result.success)

    def test_validate_yes_no_valid(self) -> None:
        """Test valid yes/no inputs."""
        for value in ["y", "n", "yes", "no"]:
            result = self.wizard._validate_yes_no(value)
            self.assertTrue(result.success, f"Value {value} should be valid")

    def test_validate_yes_no_invalid(self) -> None:
        """Test invalid yes/no input."""
        result = self.wizard._validate_yes_no("maybe")
        self.assertFalse(result.success)
        self.assertIn("Please enter 'y' or 'n'", result.message)

    def test_validate_timeout_valid(self) -> None:
        """Test valid timeout values."""
        for timeout in ["0", "1000", "30000", "60000"]:
            result = self.wizard._validate_timeout(timeout)
            self.assertTrue(result.success, f"Timeout {timeout} should be valid")

    def test_validate_timeout_invalid(self) -> None:
        """Test invalid timeout value."""
        result = self.wizard._validate_timeout("-1")
        self.assertFalse(result.success)
        self.assertIn("must be >= 0", result.message)

    def test_validate_timeout_invalid_format(self) -> None:
        """Test timeout with invalid format."""
        result = self.wizard._validate_timeout("invalid")
        self.assertFalse(result.success)
        self.assertIn("must be an integer", result.message)


class TestProfileWizardProcessing(unittest.TestCase):
    """Test profile wizard input processing."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_process_profile_name_valid(self) -> None:
        """Test processing valid profile name."""
        result = self.wizard.process_input("test-profile")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.profile_name, "test-profile")
        self.assertEqual(self.wizard.state.step, 1)

    def test_process_provider_valid(self) -> None:
        """Test processing valid provider."""
        self.wizard.state.step = 1
        result = self.wizard.process_input("openai")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.provider, "openai")

    def test_process_api_key_valid(self) -> None:
        """Test processing valid API key."""
        self.wizard.state.step = 2
        result = self.wizard.process_input("sk-example-openai-key")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.api_key, "sk-example-openai-key")

    def test_process_temperature_valid(self) -> None:
        """Test processing valid temperature."""
        self.wizard.state.step = 4
        result = self.wizard.process_input("1.0")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.temperature, 1.0)

    def test_process_optional_field_empty(self) -> None:
        """Test processing empty optional field."""
        self.wizard.state.step = 7  # organization step (optional)
        result = self.wizard.process_input("")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.organization, "")

    def test_get_state_summary(self) -> None:
        """Test getting wizard state summary."""
        self.wizard.state.profile_name = "test-profile"
        self.wizard.state.api_key = "sk-example-openai-key"
        summary = self.wizard._get_state_summary()
        self.assertEqual(summary["profile_name"], "test-profile")
        self.assertIn("...", summary["api_key"])  # Should be masked


class TestProfileCreation(unittest.TestCase):
    """Test profile creation with wizard."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.profile_manager.create_profile = Mock()
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_build_profile_config(self) -> None:
        """Test building profile configuration."""
        self.wizard.state.provider = "openai"
        self.wizard.state.model = "gpt-4"
        self.wizard.state.temperature = 0.7
        self.wizard.state.api_key = "sk-test"

        config = self.wizard._build_profile_config()
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["model"], "gpt-4")
        self.assertEqual(config["temperature"], 0.7)
        self.assertEqual(config["api_key"], "sk-test")

    def test_complete_profile_success(self) -> None:
        """Test successful profile completion."""
        self.wizard.state.profile_name = "test-profile"
        self.wizard.state.provider = "openai"
        self.wizard.state.model = "gpt-4"
        self.wizard.state.api_key = "sk-example-openai-key"
        self.wizard.state.temperature = 0.7
        self.wizard.state.max_tokens = 4096

        mock_profile = Mock()
        mock_profile.model = "gpt-4"
        mock_profile.api_token = "sk-example-openai-key"
        mock_profile.get_endpoint = Mock(return_value="https://api.openai.com/v1")
        self.profile_manager.create_profile = Mock(return_value=mock_profile)

        result = self.wizard._complete_profile()
        self.assertTrue(result.success)
        self.assertIn("Created profile", result.message)
        self.profile_manager.create_profile.assert_called_once()

    def test_complete_profile_validation_error(self) -> None:
        """Test profile completion with validation error."""
        self.wizard.state.profile_name = "test-profile"
        self.wizard.state.provider = "invalid"
        self.wizard.state.model = "gpt-4"

        result = self.wizard._complete_profile()
        self.assertFalse(result.success)
        self.assertIn("validation failed", result.message.lower())

    def test_complete_profile_already_exists(self) -> None:
        """Test profile completion when profile already exists."""
        self.wizard.state.profile_name = "existing"
        self.wizard.state.provider = "openai"
        self.wizard.state.model = "gpt-4"
        self.wizard.state.api_key = "sk-test"
        self.profile_manager.create_profile = Mock(return_value=None)

        result = self.wizard._complete_profile()
        self.assertFalse(result.success)
        self.assertIn("Failed", result.message)


class TestProfileTesting(unittest.TestCase):
    """Test profile testing functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_test_profile_with_model(self) -> None:
        """Test profile with model."""
        mock_profile = Mock()
        mock_profile.model = "gpt-4"
        mock_profile.api_token = "sk-test"
        mock_profile.get_endpoint = Mock(return_value="https://api.openai.com/v1")

        result = self.wizard._test_profile(mock_profile)
        self.assertTrue(result.success)
        self.assertIn("validation passed", result.message)

    def test_test_profile_missing_model(self) -> None:
        """Test profile without model."""
        mock_profile = Mock()
        mock_profile.model = None

        result = self.wizard._test_profile(mock_profile)
        self.assertFalse(result.success)
        self.assertIn("missing model", result.message)

    def test_test_profile_no_api_key(self) -> None:
        """Test profile without API key."""
        mock_profile = Mock()
        mock_profile.model = "gpt-4"
        mock_profile.api_token = None
        mock_profile.get_endpoint = Mock(return_value=None)

        result = self.wizard._test_profile(mock_profile)
        self.assertFalse(result.success)
        self.assertIn("no API key", result.message)


class TestMaskedApiKey(unittest.TestCase):
    """Test masked API key functionality."""

    def test_mask_api_key_openai(self) -> None:
        """Test masking OpenAI API key."""
        masked = mask_api_key("sk-example-openai-keyghijklmnop")
        self.assertIn("sk-", masked)
        self.assertIn("...", masked)
        self.assertIn("nop", masked)  # Last 3 chars
        self.assertNotIn("1234567890", masked)  # Middle part should be hidden

    def test_mask_api_key_short(self) -> None:
        """Test masking short API key."""
        masked = mask_api_key("short")
        self.assertEqual(masked, "***")

    def test_mask_api_key_empty(self) -> None:
        """Test masking empty API key."""
        masked = mask_api_key("")
        self.assertEqual(masked, "<not set>")

    def test_mask_api_key_none(self) -> None:
        """Test masking None API key."""
        masked = mask_api_key(None)
        self.assertEqual(masked, "<not set>")


class TestProviderDetection(unittest.TestCase):
    """Test provider auto-detection."""

    def test_detect_openai_key(self) -> None:
        """Test detecting OpenAI key format."""
        from kollabor_config.loader import detect_provider_from_api_key

        provider = detect_provider_from_api_key("sk-example-openai-key")
        self.assertEqual(provider, "openai")

    def test_detect_anthropic_key(self) -> None:
        """Test detecting Anthropic key format."""
        from kollabor_config.loader import detect_provider_from_api_key

        provider = detect_provider_from_api_key("sk-ant-1234567890abcdef")
        self.assertEqual(provider, "anthropic")

    def test_detect_unknown_key(self) -> None:
        """Test detecting unknown key format."""
        from kollabor_config.loader import detect_provider_from_api_key

        provider = detect_provider_from_api_key("unknown-format")
        self.assertEqual(provider, "auto")


class TestHelperFunctions(unittest.TestCase):
    """Test helper functions."""

    def test_get_provider_display_name(self) -> None:
        """Test getting provider display name."""
        self.assertEqual(get_provider_display_name("openai"), "OpenAI")
        self.assertEqual(get_provider_display_name("anthropic"), "Anthropic")
        self.assertEqual(get_provider_display_name("azure_openai"), "Azure OpenAI")
        self.assertEqual(get_provider_display_name("auto"), "Auto-detect")
        self.assertEqual(get_provider_display_name("unknown"), "unknown")

    def test_create_profile_wizard(self) -> None:
        """Test wizard factory function."""
        profile_manager = Mock()
        wizard = create_profile_wizard(profile_manager)
        self.assertIsInstance(wizard, ProfileCreationWizard)
        self.assertEqual(wizard.profile_manager, profile_manager)


class TestOpenAIWizardFlow(unittest.TestCase):
    """Test complete OpenAI wizard flow."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_complete_wizard_flow(self) -> None:
        """Test complete wizard flow from start to finish."""
        # Step 1: Profile name
        result = self.wizard.process_input("openai-prod")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.profile_name, "openai-prod")

        # Step 2: Provider (use default)
        self.wizard.state.step = 1
        result = self.wizard.process_input("openai")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.provider, "openai")

        # Step 3: API key
        self.wizard.state.step = 2
        result = self.wizard.process_input("sk-example-openai-key")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.api_key, "sk-example-openai-key")

        # Step 4: Model (use default)
        self.wizard.state.step = 3
        result = self.wizard.process_input("gpt-4")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.model, "gpt-4")

        # Step 5: Temperature
        self.wizard.state.step = 4
        result = self.wizard.process_input("0.7")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.temperature, 0.7)

        # Step 6: Max tokens
        self.wizard.state.step = 5
        result = self.wizard.process_input("4096")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.max_tokens, 4096)

        # Step 7: Base URL (optional, skip)
        self.wizard.state.step = 6
        result = self.wizard.process_input("")
        self.assertTrue(result.success)

        # Step 8: Organization (optional, skip)
        self.wizard.state.step = 7
        result = self.wizard.process_input("")
        self.assertTrue(result.success)

        # Step 9: Advanced settings prompt (say no)
        self.wizard.state.step = 8
        result = self.wizard.process_input("n")
        self.assertTrue(result.success)
        self.assertFalse(self.wizard.state.configure_advanced)

        # Should be complete (since we said no to advanced settings)
        self.assertTrue(self.wizard.is_complete())

    def test_complete_wizard_flow_with_advanced_settings(self) -> None:
        """Test complete wizard flow with advanced settings."""
        # Complete basic steps first
        self.wizard.state.profile_name = "advanced-profile"
        self.wizard.state.provider = "openai"
        self.wizard.state.api_key = "sk-example-openai-key"
        self.wizard.state.model = "gpt-4"
        self.wizard.state.temperature = 0.7
        self.wizard.state.max_tokens = 4096
        self.wizard.state.base_url = ""
        self.wizard.state.organization = ""

        # Step 9: Advanced settings prompt (say yes)
        self.wizard.state.step = 8
        result = self.wizard.process_input("y")
        self.assertTrue(result.success)
        self.assertTrue(self.wizard.state.configure_advanced)

        # Step 10: Description (optional)
        self.wizard.state.step = 9
        result = self.wizard.process_input("My advanced profile")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.description, "My advanced profile")

        # Step 11: Timeout
        self.wizard.state.step = 10
        result = self.wizard.process_input("60000")
        self.assertTrue(result.success)
        self.assertEqual(self.wizard.state.timeout, 60000)

        # Step 12: Supports tools
        self.wizard.state.step = 11
        result = self.wizard.process_input("y")
        self.assertTrue(result.success)
        self.assertTrue(self.wizard.state.supports_tools)

        # Step 13: Streaming
        self.wizard.state.step = 12
        result = self.wizard.process_input("n")
        self.assertTrue(result.success)
        self.assertFalse(self.wizard.state.streaming)

        # Should be complete after all advanced steps
        self.assertTrue(self.wizard.is_complete())


class TestAzureOpenAIProfile(unittest.TestCase):
    """Test Azure OpenAI profile creation."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_azure_openai_provider_validation(self) -> None:
        """Test Azure OpenAI provider is valid."""
        result = self.wizard._validate_provider("azure_openai")
        self.assertTrue(result.success)

    def test_azure_base_url_validation(self) -> None:
        """Test Azure base URL validation."""
        result = self.wizard._validate_base_url("https://my-resource.openai.azure.com")
        self.assertTrue(result.success)


class TestErrorHandling(unittest.TestCase):
    """Test error handling in wizard."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.profile_manager = Mock()
        self.profile_manager.get_profile_names = Mock(return_value=[])
        self.wizard = ProfileCreationWizard(self.profile_manager)

    def test_invalid_api_key_format(self) -> None:
        """Test handling invalid API key format."""
        self.wizard.state.step = 2
        result = self.wizard.process_input("short")
        self.assertFalse(result.success)
        self.assertIn("too short", result.message)

    def test_temperature_out_of_range(self) -> None:
        """Test handling temperature out of range."""
        self.wizard.state.step = 4
        result = self.wizard.process_input("3.0")
        self.assertFalse(result.success)
        self.assertIn("between 0.0 and 2.0", result.message)

    def test_max_tokens_invalid(self) -> None:
        """Test handling invalid max tokens."""
        self.wizard.state.step = 5
        result = self.wizard.process_input("invalid")
        self.assertFalse(result.success)
        self.assertIn("must be an integer", result.message)


if __name__ == "__main__":
    unittest.main()
