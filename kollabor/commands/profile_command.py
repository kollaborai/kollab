"""Profile command extension with OpenAI wizard and validation.

This module extends the profile command functionality with:
- OpenAI profile creation wizard with step-by-step prompts
- Masked API key input (shows first 3 chars + ... + last 4 chars)
- Profile validation using Phase 7a config extension
- Profile testing with optional API call
- Provider auto-detection from API key format
- Support for Azure OpenAI profiles

Phase 7c: Profile Command Extension (200-250 lines)
Phase 8A: Validators extracted to kollabor_ai.profile_validator
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from kollabor_ai.profile_validator import (
    build_profile_config as build_profile_config_fn,
)
from kollabor_ai.profile_validator import (
    detect_provider_from_api_key,
)
from kollabor_ai.profile_validator import (
    get_provider_display_name as get_provider_display_name_fn,
)
from kollabor_ai.profile_validator import (
    test_profile as test_profile_fn,
)
from kollabor_ai.profile_validator import (
    validate_api_key as validate_api_key_fn,
)
from kollabor_ai.profile_validator import (
    validate_base_url as validate_base_url_fn,
)
from kollabor_ai.profile_validator import (
    validate_max_tokens as validate_max_tokens_fn,
)
from kollabor_ai.profile_validator import (
    validate_profile_config as validate_profile_config_fn,
)
from kollabor_ai.profile_validator import (
    validate_profile_name as validate_profile_name_fn,
)
from kollabor_ai.profile_validator import (
    validate_provider as validate_provider_fn,
)
from kollabor_ai.profile_validator import (
    validate_temperature as validate_temperature_fn,
)
from kollabor_ai.profile_validator import (
    validate_timeout as validate_timeout_fn,
)
from kollabor_ai.profile_validator import (
    validate_yes_no as validate_yes_no_fn,
)
from kollabor_config.loader import mask_api_key
from kollabor_events.models import CommandResult

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Profile Wizard
# =============================================================================


@dataclass
class ProfileWizardState:
    """State for profile creation wizard."""

    step: int = 0
    profile_name: str = ""
    provider: str = "auto"
    api_key: str = ""
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 4096
    base_url: str = ""
    organization: str = ""
    # Advanced settings
    description: str = ""
    timeout: int = 0  # 0 means no timeout (infinity)
    supports_tools: bool = True
    streaming: bool = True
    errors: Optional[List[str]] = None
    configure_advanced: bool = False  # Whether user wants advanced settings

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# =============================================================================
# Profile Wizard Configuration
# =============================================================================


class ProfileWizardConfig:
    """Configuration for profile creation wizard."""

    # Basic wizard steps (in order)
    BASIC_STEPS = [
        "profile_name",
        "provider",
        "api_key",
        "model",
        "temperature",
        "max_tokens",
        "base_url",
        "organization",
    ]

    # Advanced steps (shown after advanced_settings_prompt if user chooses yes)
    ADVANCED_STEPS = [
        "description",
        "timeout",
        "supports_tools",
        "streaming",
    ]

    @property
    def WIZARD_STEPS(self) -> List[str]:
        """Get current wizard steps (may change based on user choices).

        This is a property to allow dynamic step lists based on state.
        The actual implementation in ProfileCreationWizard handles this dynamically.
        """
        # This is just a reference - the actual wizard uses state to determine steps
        return self.BASIC_STEPS + ["advanced_settings_prompt"] + self.ADVANCED_STEPS

    # Step definitions
    STEP_DEFINITIONS = {
        "profile_name": {
            "prompt": "Enter profile name",
            "help": "Profile name (e.g., openai-prod, claude-dev)",
            "required": True,
            "validator": "_validate_profile_name",
        },
        "provider": {
            "prompt": "Select provider",
            "help": "Provider type (auto/OpenAI/Anthropic/Azure/Gemini/OpenAI Responses)",
            "required": False,
            "default": "auto",
            "options": [
                "auto",
                "openai",
                "anthropic",
                "azure_openai",
                "gemini",
                "openai_responses",
                "openrouter",
                "custom",
            ],
            "validator": "_validate_provider",
        },
        "api_key": {
            "prompt": "Enter API key",
            "help": "API key (will be masked as sk-...-xyz)",
            "required": True,
            "password": True,
            "validator": "_validate_api_key",
        },
        "model": {
            "prompt": "Enter model",
            "help": "Model identifier (e.g., gpt-4, claude-3-sonnet)",
            "required": False,
            "default": "gpt-4",
            "validator": None,
        },
        "temperature": {
            "prompt": "Enter temperature",
            "help": "Sampling temperature (0.0-2.0, default: 0.7)",
            "required": False,
            "default": "0.7",
            "validator": "_validate_temperature",
        },
        "max_tokens": {
            "prompt": "Enter max tokens",
            "help": "Maximum tokens (default: 4096)",
            "required": False,
            "default": "4096",
            "validator": "_validate_max_tokens",
        },
        "base_url": {
            "prompt": "Enter base URL",
            "help": "Custom base URL (optional)",
            "required": False,
            "default": "",
            "validator": "_validate_base_url",
        },
        "organization": {
            "prompt": "Enter organization",
            "help": "Organization ID (OpenAI only, optional)",
            "required": False,
            "default": "",
            "validator": None,
        },
        "advanced_settings_prompt": {
            "prompt": "Configure advanced settings?",
            "help": "Add timeout, description, tool calling, streaming (y/n)",
            "required": False,
            "default": "n",
            "options": ["y", "n", "yes", "no"],
            "validator": "_validate_yes_no",
        },
        "description": {
            "prompt": "Enter description",
            "help": "Profile description (optional)",
            "required": False,
            "default": "",
            "validator": None,
        },
        "timeout": {
            "prompt": "Enter timeout",
            "help": "Request timeout in milliseconds (0 = no timeout, default: 0)",
            "required": False,
            "default": "0",
            "validator": "_validate_timeout",
        },
        "supports_tools": {
            "prompt": "Enable tool calling?",
            "help": "Support function/tool calling (y/n, default: y)",
            "required": False,
            "default": "y",
            "options": ["y", "n", "yes", "no"],
            "validator": "_validate_yes_no",
        },
        "streaming": {
            "prompt": "Enable streaming?",
            "help": "Stream responses (y/n, default: y)",
            "required": False,
            "default": "y",
            "options": ["y", "n", "yes", "no"],
            "validator": "_validate_yes_no",
        },
    }


# =============================================================================
# Profile Creation Wizard
# =============================================================================


class ProfileCreationWizard:
    """Wizard for creating OpenAI/LLM profiles with validation and testing."""

    def __init__(self, profile_manager, config_manager=None):
        """Initialize profile creation wizard.

        Args:
            profile_manager: Profile manager for creating profiles
            config_manager: Optional config manager for validation
        """
        self.profile_manager = profile_manager
        self.config_manager = config_manager
        self.state = ProfileWizardState()
        self.config = ProfileWizardConfig()
        self.logger = logger

    def reset(self) -> None:
        """Reset wizard state."""
        self.state = ProfileWizardState()

    def get_current_steps(self) -> List[str]:
        """Get current step list based on advanced settings choice.

        The flow is:
        1. Run BASIC_STEPS
        2. Show advanced_settings_prompt (yes/no for advanced settings)
        3. If yes, run ADVANCED_STEPS
        4. Complete

        Returns:
            List of step names for current wizard flow
        """
        # Always include basic steps first
        steps = list(self.config.BASIC_STEPS)

        # Add advanced settings prompt after basic steps
        # We'll determine if advanced steps are included when processing the prompt
        steps.append("advanced_settings_prompt")

        # If user has chosen to configure advanced settings, include advanced steps
        if self.state.configure_advanced:
            steps.extend(self.config.ADVANCED_STEPS)

        return steps

    def get_current_step(self) -> str:
        """Get current wizard step name."""
        current_steps = self.get_current_steps()
        if self.state.step < len(current_steps):
            return str(current_steps[self.state.step])
        return "complete"

    def get_step_definition(self, step_name: str) -> Optional[Dict[str, Any]]:
        """Get step definition by name."""
        result = self.config.STEP_DEFINITIONS.get(step_name)
        if isinstance(result, dict):
            return result
        return None

    def advance_step(self) -> str:
        """Advance to next wizard step.

        Returns:
            Next step name
        """
        self.state.step += 1
        return self.get_current_step()

    def is_complete(self) -> bool:
        """Check if wizard is complete."""
        current_steps = self.get_current_steps()
        return bool(self.state.step >= len(current_steps))

    # ========================================================================
    # Input Processing
    # ========================================================================

    def process_input(self, input_value: str) -> CommandResult:
        """Process input for current wizard step.

        Args:
            input_value: User input value

        Returns:
            Command result with next step or completion
        """
        current_step = self.get_current_step()

        if current_step == "complete":
            return self._complete_profile()

        step_def = self.get_step_definition(current_step)
        if not step_def:
            return CommandResult(
                success=False,
                message=f"Invalid step: {current_step}",
                display_type="error",
            )

        # Trim input
        input_value = input_value.strip()

        # Handle empty input for optional fields
        if not input_value and not step_def.get("required", False):
            default_value = step_def.get("default", "")
            self._set_step_value(current_step, default_value)
            return self._advance_to_next_step()

        # Validate input
        validator_name = step_def.get("validator")
        if validator_name:
            validation_result = self._validate_input(validator_name, input_value)
            if not validation_result.success:
                return validation_result

        # Handle advanced_settings_prompt specially
        if current_step == "advanced_settings_prompt":
            self._set_step_value(current_step, input_value)
            # Check if user wants advanced settings
            wants_advanced = input_value.lower() in ("y", "yes")
            if wants_advanced:
                self.state.configure_advanced = True
            # Continue to next step
            return self._advance_to_next_step()

        # Set value and advance
        self._set_step_value(current_step, input_value)
        return self._advance_to_next_step()

    def _set_step_value(self, step: str, value: str) -> None:
        """Set value for wizard step.

        Args:
            step: Step name
            value: Value to set
        """
        if step == "profile_name":
            self.state.profile_name = value
        elif step == "provider":
            self.state.provider = value
        elif step == "api_key":
            self.state.api_key = value
        elif step == "model":
            self.state.model = value
        elif step == "temperature":
            self.state.temperature = float(value)
        elif step == "max_tokens":
            self.state.max_tokens = int(value)
        elif step == "base_url":
            self.state.base_url = value
        elif step == "organization":
            self.state.organization = value
        elif step == "description":
            self.state.description = value
        elif step == "timeout":
            self.state.timeout = int(value)
        elif step == "supports_tools":
            self.state.supports_tools = value.lower() in ("y", "yes")
        elif step == "streaming":
            self.state.streaming = value.lower() in ("y", "yes")
        elif step == "advanced_settings_prompt":
            # This is handled in process_input, not here
            pass

    def _advance_to_next_step(self) -> CommandResult:
        """Advance to next step and return prompt.

        Returns:
            Command result with next step prompt
        """
        next_step = self.advance_step()

        if next_step == "complete":
            # Show summary and ask for confirmation
            return self._show_summary()

        step_def = self.get_step_definition(next_step)
        if step_def is None:
            return CommandResult(
                success=False,
                message="\n[err] Unknown wizard step configuration",
                display_type="error",
            )
        prompt = step_def["prompt"]
        help_text = step_def.get("help", "")
        options = step_def.get("options")

        message = f"\n{prompt}"
        if help_text:
            message += f"\n  ({help_text})"
        if options:
            message += f"\n  Options: {', '.join(options)}"
        message += "\n"

        return CommandResult(
            success=True,
            message=message,
            display_type="info",
            data={"step": next_step, "wizard_state": self._get_state_summary()},
        )

    def _get_state_summary(self) -> Dict[str, Any]:
        """Get summary of current wizard state.

        Returns:
            Dictionary with wizard state summary
        """
        summary = {
            "profile_name": self.state.profile_name,
            "provider": self.state.provider,
            "api_key": (
                mask_api_key(self.state.api_key) if self.state.api_key else "<not set>"
            ),
            "model": self.state.model,
            "temperature": self.state.temperature,
            "max_tokens": self.state.max_tokens,
            "base_url": self.state.base_url or "<default>",
            "organization": self.state.organization or "<not set>",
        }

        # Add advanced settings if configured
        if self.state.configure_advanced:
            if self.state.description:
                summary["description"] = self.state.description
            summary["timeout"] = (
                f"{self.state.timeout}ms" if self.state.timeout > 0 else "no timeout"
            )
            summary["supports_tools"] = "yes" if self.state.supports_tools else "no"
            summary["streaming"] = "yes" if self.state.streaming else "no"

        return summary

    def _show_summary(self) -> CommandResult:
        """Show profile summary and ask for confirmation.

        Returns:
            Command result with summary
        """
        summary = self._get_state_summary()

        message = "\nProfile Summary:\n"
        for key, value in summary.items():
            message += f"  {key}: {value}\n"
        message += "\nPress Enter to create profile, or 'cancel' to abort"

        return CommandResult(
            success=True,
            message=message,
            display_type="info",
            data={"action": "confirm_create", "wizard_state": summary},
        )

    # ========================================================================
    # Profile Creation
    # ========================================================================

    def _complete_profile(self) -> CommandResult:
        """Complete profile creation with validation and testing.

        Returns:
            Command result with creation status
        """
        # Build profile configuration
        profile_config = self._build_profile_config()

        # Validate profile configuration
        validation_result = self._validate_profile_config(profile_config)
        if not validation_result.success:
            return validation_result

        # Auto-detect provider if set to auto
        if self.state.provider == "auto" and self.state.api_key:
            detected_provider = detect_provider_from_api_key(self.state.api_key)
            profile_config["provider"] = detected_provider
            self.logger.info(f"Auto-detected provider: {detected_provider}")

        # Create profile
        try:
            profile = self.profile_manager.create_profile(
                name=self.state.profile_name,
                base_url=profile_config.get("base_url", ""),
                model=self.state.model,
                api_key=self.state.api_key,
                temperature=self.state.temperature,
                description=profile_config.get(
                    "description", "Created via profile wizard"
                ),
                timeout=self.state.timeout,
                streaming=self.state.streaming,
                supports_tools=self.state.supports_tools,
                provider=profile_config.get("provider", "custom"),
                save_to_config=True,
            )

            if profile:
                # Test profile (optional)
                test_result = self._test_profile(profile)

                message = f"\n[ok] Created profile: {self.state.profile_name}"
                message += f"\n  Provider: {profile_config.get('provider', 'auto')}"
                message += f"\n  Model: {self.state.model}"
                message += f"\n  API Key: {mask_api_key(self.state.api_key)}"

                # Show advanced settings if configured
                if self.state.configure_advanced:
                    if self.state.description:
                        message += f"\n  Description: {self.state.description}"
                    timeout_display = (
                        f"{self.state.timeout}ms"
                        if self.state.timeout > 0
                        else "no timeout"
                    )
                    message += f"\n  Timeout: {timeout_display}"
                    message += f"\n  Tool calling: {'enabled' if self.state.supports_tools else 'disabled'}"
                    message += f"\n  Streaming: {'enabled' if self.state.streaming else 'disabled'}"

                if test_result.success:
                    message += f"\n{test_result.message}"
                else:
                    message += "\n[warn] Profile test skipped or failed"

                return CommandResult(
                    success=True,
                    message=message,
                    display_type="success",
                    data={
                        "profile_name": self.state.profile_name,
                        "test_result": test_result.data,
                    },
                )
            else:
                return CommandResult(
                    success=False,
                    message=f"\n[err] Failed to create profile '{self.state.profile_name}'. May already exist.",
                    display_type="error",
                )

        except Exception as e:
            self.logger.error(f"Error creating profile: {e}")
            return CommandResult(
                success=False,
                message=f"\n[err] Error creating profile: {str(e)}",
                display_type="error",
            )

    def _build_profile_config(self) -> Dict[str, Any]:
        """Build profile configuration from wizard state.

        Returns:
            Profile configuration dictionary
        """
        return cast(Dict[str, Any], build_profile_config_fn(self.state))

    # ========================================================================
    # Validation
    # ========================================================================

    def _validate_input(self, validator_name: str, value: str) -> CommandResult:
        """Validate input using validator method.

        Args:
            validator_name: Name of validator method
            value: Value to validate

        Returns:
            Command result with validation status
        """
        validator = getattr(self, validator_name, None)
        if not validator:
            return CommandResult(success=True, message="", display_type="info")

        try:
            result = validator(value)
            if not result.success:
                return cast(CommandResult, result)
            return CommandResult(success=True, message="", display_type="info")
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"\n[err] Validation error: {str(e)}",
                display_type="error",
            )

    def _validate_profile_name(self, value: str) -> CommandResult:
        """Validate profile name.

        Args:
            value: Profile name to validate

        Returns:
            Command result with validation status
        """
        return validate_profile_name_fn(value, profile_manager=self.profile_manager)

    def _validate_provider(self, value: str) -> CommandResult:
        """Validate provider type.

        Args:
            value: Provider to validate

        Returns:
            Command result with validation status
        """
        return validate_provider_fn(value)

    def _validate_api_key(self, value: str) -> CommandResult:
        """Validate API key format.

        Args:
            value: API key to validate

        Returns:
            Command result with validation status
        """
        return validate_api_key_fn(
            value, current_provider=self.state.provider, logger=self.logger
        )

    def _validate_temperature(self, value: str) -> CommandResult:
        """Validate temperature value.

        Args:
            value: Temperature to validate

        Returns:
            Command result with validation status
        """
        return validate_temperature_fn(value)

    def _validate_max_tokens(self, value: str) -> CommandResult:
        """Validate max tokens value.

        Args:
            value: Max tokens to validate

        Returns:
            Command result with validation status
        """
        return validate_max_tokens_fn(value)

    def _validate_base_url(self, value: str) -> CommandResult:
        """Validate base URL.

        Args:
            value: Base URL to validate

        Returns:
            Command result with validation status
        """
        return validate_base_url_fn(value)

    def _validate_yes_no(self, value: str) -> CommandResult:
        """Validate yes/no input.

        Args:
            value: Yes/no value to validate

        Returns:
            Command result with validation status
        """
        return validate_yes_no_fn(value)

    def _validate_timeout(self, value: str) -> CommandResult:
        """Validate timeout value.

        Args:
            value: Timeout to validate (milliseconds, 0 = no timeout)

        Returns:
            Command result with validation status
        """
        return validate_timeout_fn(value)

    def _validate_profile_config(self, config: Dict[str, Any]) -> CommandResult:
        """Validate complete profile configuration.

        Args:
            config: Profile configuration to validate

        Returns:
            Command result with validation status
        """
        return validate_profile_config_fn(config)

    # ========================================================================
    # Profile Testing
    # ========================================================================

    def _test_profile(self, profile) -> CommandResult:
        """Test profile with optional API call.

        Args:
            profile: Profile to test

        Returns:
            Command result with test status
        """
        return test_profile_fn(profile)


# =============================================================================
# Helper Functions
# =============================================================================


def get_provider_display_name(provider: str) -> str:
    """Get display name for provider.

    Args:
        provider: Provider type

    Returns:
        Display name
    """
    return cast(str, get_provider_display_name_fn(provider))


