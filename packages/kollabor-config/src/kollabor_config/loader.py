"""Configuration loading and plugin integration logic."""

import logging
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_ai.prompt_renderer import render_system_prompt
from kollabor_events.dict_utils import deep_merge
from kollabor_events.error_utils import log_and_continue, safe_execute

from .config_utils import (
    get_existing_global_config_path,
    get_existing_local_config_path,
    get_global_config_path,
    get_global_config_path_candidates,
    get_local_config_path,
    get_local_config_path_candidates,
    get_project_data_dir,
    get_project_data_dir_candidates,
    get_system_prompt_content,
    get_system_prompt_path,
)
from .manager import ConfigManager
from .plugin_config_manager import PluginConfigManager

# Configuration version tracking
CONFIG_VERSION = 2  # Current version
VERSION_KEY = "_config_version"


class ConfigurationValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


def detect_provider_from_api_key(api_key: str) -> str:
    """
    Detect provider type from API key format.

    Args:
        api_key: API key to analyze

    Returns:
        Provider type: 'openai', 'anthropic', or 'auto' if unknown
    """
    if not api_key:
        return "auto"

    api_key_lower = api_key.lower()

    if api_key_lower.startswith("sk-ant-"):
        return "anthropic"
    elif api_key_lower.startswith("sk-"):
        return "openai"
    else:
        return "auto"


def validate_provider_type(provider: str) -> bool:
    """
    Validate provider type is supported.

    Args:
        provider: Provider type string

    Returns:
        True if valid, raises ConfigurationValidationError otherwise
    """
    valid_providers = [
        "openai",
        "anthropic",
        "azure_openai",
        "gemini",
        "openai_responses",
        "openrouter",
        "custom",
        "auto",
    ]
    if provider not in valid_providers:
        raise ConfigurationValidationError(
            f"Invalid provider '{provider}'. Must be one of: {', '.join(valid_providers)}"
        )
    return True


def validate_openai_config(config: Dict[str, Any]) -> None:
    """
    Validate OpenAI-specific configuration fields.

    Args:
        config: Configuration dictionary to validate

    Raises:
        ConfigurationValidationError: If validation fails
    """
    # Validate api_key format if present
    api_key = config.get("api_key")
    if api_key and not isinstance(api_key, str):
        raise ConfigurationValidationError("api_key must be a string")

    # Validate model is present and is a string
    if "model" not in config:
        raise ConfigurationValidationError("model field is required for OpenAI")

    model = config.get("model")
    if not model or not isinstance(model, str):
        raise ConfigurationValidationError("model must be a non-empty string")

    # Validate temperature range if present
    if "temperature" in config:
        temperature = config["temperature"]
        if not isinstance(temperature, (int, float)):
            raise ConfigurationValidationError("temperature must be a number")
        if not 0.0 <= temperature <= 2.0:
            raise ConfigurationValidationError(
                f"temperature must be between 0.0 and 2.0, got {temperature}"
            )

    # Validate max_tokens if present
    if "max_tokens" in config:
        max_tokens = config["max_tokens"]
        if not isinstance(max_tokens, int):
            raise ConfigurationValidationError("max_tokens must be an integer")
        if max_tokens < 1:
            raise ConfigurationValidationError(
                f"max_tokens must be >= 1, got {max_tokens}"
            )

    # Validate base_url format if present
    if "base_url" in config and config["base_url"]:
        base_url = config["base_url"]
        if not isinstance(base_url, str):
            raise ConfigurationValidationError("base_url must be a string")

        # Validate URL format
        url_pattern = re.compile(
            r"^(https?://)?"  # http:// or https:// (optional)
            r"([a-zA-Z0-9-]+\.)+"  # domain
            r"[a-zA-Z]{2,}"  # TLD
            r"(:\d+)?"  # optional port
            r"(/.*)?$"  # optional path
        )
        if (
            not url_pattern.match(base_url)
            and "localhost" not in base_url
            and "127.0.0.1" not in base_url
        ):
            raise ConfigurationValidationError(
                f"base_url must be a valid URL, got: {base_url}"
            )

    # Validate organization if present
    if "organization" in config and config["organization"]:
        organization = config["organization"]
        if not isinstance(organization, str):
            raise ConfigurationValidationError("organization must be a string")


def mask_api_key(api_key: Optional[str]) -> str:
    """
    Mask API key for logging purposes.

    Args:
        api_key: API key to mask

    Returns:
        Masked API key (e.g., "sk-...xyz123")
    """
    if not api_key:
        return "<not set>"

    if len(api_key) <= 8:
        return "***"

    return f"{api_key[:4]}...{api_key[-4:]}"


def _get_version_from_pyproject() -> str:
    """Read version from pyproject.toml for development mode."""
    try:
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            for line in content.splitlines():
                if line.startswith("version ="):
                    # Extract version from: version = "0.4.10"
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None  # type: ignore[return-value]


def _is_running_from_source() -> bool:
    """Check if we're running from source (development mode) vs installed package."""
    try:
        # If pyproject.toml exists in parent directory, we're running from source
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        return pyproject_path.exists()
    except Exception:
        return False


# Get version: prefer pyproject.toml when running from source, otherwise use installed version
if _is_running_from_source():
    # Development mode: use pyproject.toml
    _package_version = _get_version_from_pyproject() or "0.0.0"
else:
    # Production mode: use installed package version
    try:
        _package_version = get_version("kollabor")
    except PackageNotFoundError:
        _package_version = "0.0.0"

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Handles complex configuration loading with plugin integration.

    This class manages the coordination between file-based configuration
    and plugin-provided configurations, implementing the complex merging
    logic that was previously in ConfigManager.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        plugin_registry=None,
        fast_mode: bool = False,
    ):
        """Initialize the config loader.

        Args:
            config_manager: Basic config manager for file operations.
            plugin_registry: Optional plugin registry for plugin configs.
            fast_mode: If True, skip expensive operations like system prompt loading.
        """
        self.config_manager = config_manager
        self.plugin_registry = plugin_registry
        self.fast_mode = fast_mode
        self.plugin_config_manager = None

        # Initialize plugin config manager if registry is available
        if plugin_registry and hasattr(plugin_registry, "discovery"):
            self.plugin_config_manager = PluginConfigManager(plugin_registry.discovery)
            logger.debug("PluginConfigManager initialized")

        logger.debug("ConfigLoader initialized")

    def get_config_version(self, config: Dict[str, Any]) -> int:
        """
        Get the configuration version from a config dictionary.

        Args:
            config: Configuration dictionary

        Returns:
            Configuration version (defaults to 1 if not present)
        """
        return int(config.get(VERSION_KEY, 1))

    def set_config_version(self, config: Dict[str, Any], version: int) -> None:
        """
        Set the configuration version in a config dictionary.

        Args:
            config: Configuration dictionary to update
            version: Version number to set
        """
        config[VERSION_KEY] = version

    def validate_provider_config(self, provider: str, config: Dict[str, Any]) -> None:
        """
        Validate provider-specific configuration.

        Args:
            provider: Provider type (openai, anthropic, azure_openai)
            config: Configuration dictionary for the provider

        Raises:
            ConfigurationValidationError: If validation fails
        """
        # Validate provider type first
        validate_provider_type(provider)

        # Provider-specific validation
        if provider == "openai":
            validate_openai_config(config)
        elif provider == "anthropic":
            # Anthropic-specific validation can be added here
            # For now, just validate basic fields
            if "model" not in config:
                raise ConfigurationValidationError(
                    "model field is required for Anthropic"
                )
        elif provider == "azure_openai":
            # Azure-specific validation
            if "azure_endpoint" not in config:
                raise ConfigurationValidationError(
                    "azure_endpoint field is required for Azure OpenAI"
                )
        # 'auto' provider doesn't need specific validation

    def migrate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate configuration from older versions to current version.

        Args:
            config: Configuration dictionary to migrate

        Returns:
            Migrated configuration dictionary
        """
        version = self.get_config_version(config)

        if version == CONFIG_VERSION:
            # Already at current version
            return config

        logger.info(
            f"Migrating configuration from version {version} to {CONFIG_VERSION}"
        )

        # Version 1 -> Version 2: Add provider field and structure
        if version == 1:
            config = self._migrate_v1_to_v2(config)
            self.set_config_version(config, 2)

        return config

    def _migrate_v1_to_v2(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate version 1 configuration to version 2.

        Version 1 had no provider field and flat structure.
        Version 2 adds provider field with provider-specific validation.

        Args:
            config: Version 1 configuration

        Returns:
            Version 2 configuration
        """
        # Check if there's an api_key to auto-detect provider
        # Support both old "core" key and new "kollabor" key
        top_key = "kollabor" if "kollabor" in config else "core"
        core_llm = config.get(top_key, {}).get("llm", {})

        # If there's no provider field, try to detect it
        if "provider" not in core_llm:
            # Look for api_key in old location
            api_key = core_llm.get("api_token") or core_llm.get("api_key")

            if api_key:
                detected_provider = detect_provider_from_api_key(api_key)
                core_llm["provider"] = detected_provider
                logger.info(f"Auto-detected provider: {detected_provider}")
            else:
                core_llm["provider"] = "auto"
                logger.info("No API key found, setting provider to 'auto'")

        # Rename "core" -> "kollabor" if needed
        if "core" in config:
            config["kollabor"] = config.pop("core")

        # Ensure the config has the updated structure
        if "kollabor" not in config:
            config["kollabor"] = {}
        if "llm" not in config["kollabor"]:
            config["kollabor"]["llm"] = {}

        config["kollabor"]["llm"] = core_llm

        return config

    def _load_system_prompt(self, skip_render: bool = False) -> str:
        """Load system prompt from env vars or file and render dynamic content.

        Processes <trender>command</trender> tags by executing commands
        and replacing tags with their output.

        Priority:
        1. KOLLAB_SYSTEM_PROMPT environment variable (direct string)
        2. KOLLAB_SYSTEM_PROMPT_FILE environment variable (custom file path)
        3. Local/global system_prompt/default.md files
        4. Fallback default

        Args:
            skip_render: If True, skip rendering trender tags (for fast startup).

        Returns:
            System prompt content with rendered commands or fallback message.
        """
        # Fast path for help mode - skip expensive prompt loading
        if skip_render:
            return ""

        try:
            # Use the new unified function that checks env vars and files
            content = get_system_prompt_content()

            # Get the system prompt file path to use as base_path for includes
            prompt_path = get_system_prompt_path()
            base_path = prompt_path.parent if prompt_path.exists() else None

            # Render dynamic <trender> tags with base_path set to prompt's directory
            # This allows includes like "sections/file.md" to be resolved correctly
            rendered_content = render_system_prompt(
                content, timeout=5, base_path=base_path
            )

            return str(rendered_content)
        except Exception as e:
            logger.error(f"Failed to load system prompt: {e}")
            return "You are Kollab, an intelligent coding assistant."

    def get_base_config(self) -> Dict[str, Any]:
        """Get the base application configuration with defaults.

        Returns:
            Base configuration dictionary with application defaults.
        """
        # Load system prompt from file (skip in fast mode for -h)
        system_prompt = self._load_system_prompt(skip_render=self.fast_mode)

        return {
            "terminal": {
                "render_fps": 20,
                "spinner_frames": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"],
                "status_lines": 4,
                "thinking_message_limit": 25,
                "thinking_effect": "shimmer",
                "shimmer_speed": 3,
                "shimmer_wave_width": 4,
                "render_error_delay": 0.1,
                "render_cache_enabled": True,
                "tool_spinner_enabled": True,
                "tool_spinner_style": "braille",
                "tool_spinner_frames": None,
                "tool_spinner_speed_ms": 100,
                "interactive_shell": True,  # Source .zshrc/.bashrc for shell aliases
            },
            "input": {
                "ctrl_c_exit": True,
                "backspace_enabled": True,
                "input_buffer_limit": 100000,
                "polling_delay": 0.01,
                "error_delay": 0.1,
                "history_limit": 100,
                "error_threshold": 10,
                "error_window_minutes": 5,
                "max_errors": 100,
                "paste_detection_enabled": True,
                "paste_threshold_ms": 50,
                "paste_min_chars": 3,
                "paste_max_chars": 10000,
                "bracketed_paste_enabled": True,
            },
            "logging": {
                "level": "INFO",
                "file": None,  # Determined dynamically by get_logs_dir()
                "format_type": "compact",
                "format": "%(asctime)s - %(levelname)-4s - %(message)-100s - %(filename)s:%(lineno)04d",
            },
            "hooks": {
                "default_timeout": 30,
                "default_retries": 3,
                "default_error_action": "continue",
            },
            "application": {
                "name": "Kollab",
                "version": _package_version,
                "description": "AI Edition",
            },
            "kollabor": {
                "llm": {
                    # Note: api_url, api_token, model, temperature, timeout are now in profiles
                    # See kollabor.llm.profiles.* for LLM connection settings
                    "auto_detect_provider": True,
                    "max_history": 999,
                    "save_conversations": True,
                    "conversation_format": "jsonl",
                    "show_status": True,
                    "http_connector_limit": 10,
                    "message_history_limit": 20,
                    "thinking_phase_delay": 0.5,
                    "log_message_truncate": 50,
                    "enable_streaming": True,
                    "processing_delay": 0.1,
                    "thinking_delay": 0.3,
                    "api_poll_delay": 0.01,
                    "terminal_timeout": 120,
                    "mcp_timeout": 60,
                    "system_prompt": {
                        "base_prompt": system_prompt,
                        "include_project_structure": False,
                        "attachment_files": [],
                        "custom_prompt_files": [],
                    },
                    "oauth": {
                        "auto_refresh": True,
                        "token_expiry_buffer_seconds": 300,
                    },
                    "task_management": {
                        "background_tasks": {
                            "max_concurrent": 10000,
                            "default_timeout": 0,
                            "cleanup_interval": 60,
                            "enable_monitoring": True,
                            "log_task_events": True,
                            "log_task_errors": True,
                            "enable_metrics": True,
                            "task_retry_attempts": 0,
                            "task_retry_delay": 1.0,
                            "enable_task_circuit_breaker": False,
                            "circuit_breaker_threshold": 5,
                            "circuit_breaker_timeout": 60.0,
                        },
                        "queue": {
                            "max_size": 1000,
                            "overflow_strategy": "drop_oldest",
                            "block_timeout": 1.0,
                            "enable_queue_metrics": True,
                            "log_queue_events": True,
                        },
                    },
                }
            },
            "performance": {
                "failure_rate_warning": 0.05,
                "failure_rate_critical": 0.15,
                "degradation_threshold": 0.15,
            },
            "plugins": {
                "system_commands": {"enabled": True},
                "hook_monitoring": {
                    "enabled": False,
                    "debug_logging": True,
                    "show_status": True,
                    "hook_timeout": 5,
                    "log_all_events": True,
                    "log_event_data": False,
                    "log_performance": True,
                    "log_failures_only": False,
                    "performance_threshold_ms": 100,
                    "max_error_log_size": 50,
                    "enable_plugin_discovery": True,
                    "discovery_interval": 30,
                    "auto_analyze_capabilities": True,
                    "enable_service_registration": True,
                    "register_performance_service": True,
                    "register_health_service": True,
                    "register_metrics_service": True,
                    "enable_cross_plugin_communication": True,
                    "message_history_limit": 20,
                    "auto_respond_to_health_checks": True,
                    "health_check_interval": 30,
                    "memory_threshold_mb": 50,
                    "performance_degradation_threshold": 0.15,
                    "collect_plugin_metrics": True,
                    "metrics_retention_hours": 24,
                    "detailed_performance_tracking": True,
                    "enable_health_dashboard": True,
                    "dashboard_update_interval": 10,
                    "show_plugin_interactions": True,
                    "show_service_usage": True,
                },
                "modern_input": {
                    "enabled": True,
                    "width_mode": "auto",
                    "min_width": 40,
                    "max_width": None,
                    "max_visible_lines": 3,
                    "show_placeholder": True,
                    "placeholder": "Type your message...",
                    "cursor_blink": True,
                    "cursor_blink_rate": 0.5,
                    "use_theme_colors": True,
                    "custom_text_color": None,
                    "custom_placeholder_color": None,
                    "show_status": False,
                },
                "fullscreen": {"enabled": False},
                "context_compaction": {
                    "enabled": False,
                    "trigger_threshold": 16,
                    "keep_recent": 4,
                    "re_trigger_threshold": 12,
                    "count_mode": "interactions",
                    "summarization_profile": None,
                    "max_summary_tokens": 2000,
                    "log_compaction_events": True,
                },
            },
        }

    def get_plugin_configs(self) -> Dict[str, Any]:
        """Get merged configuration from all plugins.

        Returns:
            Merged plugin configurations or empty dict if no plugins.
        """
        if not self.plugin_registry:
            return {}

        # Discover plugin schemas first
        self.discover_plugin_schemas()

        def get_configs():
            return self.plugin_registry.get_merged_config()

        plugin_configs: Dict[str, Any] = safe_execute(
            get_configs,
            "getting plugin configurations",
            default={},
            logger_instance=logger,
        )

        return plugin_configs if isinstance(plugin_configs, dict) else {}

    def discover_plugin_schemas(self) -> None:
        """Discover and register plugin configuration schemas."""
        if not self.plugin_config_manager:
            return

        def discover():
            self.plugin_config_manager.discover_plugin_schemas()

        safe_execute(
            discover, "discovering plugin schemas", default=None, logger_instance=logger
        )

    def get_plugin_config_sections(self) -> List[Dict[str, Any]]:
        """Get UI sections for plugin configuration.

        Returns:
            List of section definitions for the configuration UI.
        """
        if not self.plugin_config_manager:
            return []

        def get_sections():
            return self.plugin_config_manager.get_plugin_config_sections()

        sections: List[Any] = safe_execute(
            get_sections,
            "getting plugin config sections",
            default=[],
            logger_instance=logger,
        )

        return sections if isinstance(sections, list) else []

    def get_plugin_widget_definitions(self) -> List[Dict[str, Any]]:
        """Get widget definitions for all plugin configurations.

        Returns:
            List of widget definition dictionaries.
        """
        if not self.plugin_config_manager:
            return []

        def get_widgets():
            return self.plugin_config_manager.get_widget_definitions()

        widgets: List[Any] = safe_execute(
            get_widgets,
            "getting plugin widget definitions",
            default=[],
            logger_instance=logger,
        )

        return widgets if isinstance(widgets, list) else []

    def load_complete_config(self) -> Dict[str, Any]:
        """Load complete configuration including plugins.

        This is the main entry point for getting a fully merged configuration
        that includes base defaults, plugin configs, and user overrides.

        Priority order for user config (new layered system):
        1. Global config (~/.kollab/config.json) - base layer
        2. Project config (~/.kollab/projects/<encoded>/config.json) - project defaults
        3. Local config (.kollab/config.json in current directory) - local override
        4. Base defaults (if none exist)

        Returns:
            Complete merged configuration dictionary.
        """
        # Start with base application configuration
        base_config = self.get_base_config()

        # Add plugin configurations
        plugin_configs = self.get_plugin_configs()
        if plugin_configs:
            base_config = deep_merge(base_config, plugin_configs)
            logger.debug("Merged configurations from plugins")

        # Load user configuration with fallback to global
        user_config = self._load_user_config_with_fallback()
        if user_config:
            # Migrate configuration if needed
            user_config = self.migrate_config(user_config)

            # User config takes precedence over defaults and plugins
            base_config = deep_merge(base_config, user_config)
            logger.debug("Merged user configuration")

        # Set current version on the final config
        self.set_config_version(base_config, CONFIG_VERSION)

        # Validate provider configuration if present
        core_llm = base_config.get("kollabor", {}).get("llm", {})
        provider = core_llm.get("provider", "auto")
        try:
            self.validate_provider_config(provider, core_llm)
            logger.debug(f"Validated provider configuration: {provider}")
        except ConfigurationValidationError as e:
            logger.warning(f"Provider configuration validation failed: {e}")

        return base_config

    def _load_user_config_with_fallback(self) -> Dict[str, Any]:
        """Load user configuration with layered resolution.

        Priority order (new layered system):
        1. Explicit config_manager.config_path (if provided and exists)
        2. Global config (~/.kollab/config.json) - base layer
        3. Project config (~/.kollab/projects/<encoded>/config.json) - project defaults
        4. Local config (.kollab/config.json) - local override

        Each layer is merged on top of the previous one using deep_merge.

        Returns:
            Merged user configuration dictionary, or empty dict if none found.
        """
        import json

        merged_config = {}

        # Check if an explicit config path was provided (e.g., for testing)
        explicit_path = None
        if self.config_manager and self.config_manager.config_path:
            explicit_path = self.config_manager.config_path
            # If explicit path exists and is not in standard locations, load only from it
            standard_paths = [
                *get_global_config_path_candidates(),
                *(path / "config.json" for path in get_project_data_dir_candidates()),
                *get_local_config_path_candidates(),
            ]
            if explicit_path.exists() and explicit_path not in standard_paths:
                try:
                    with open(explicit_path, "r") as f:
                        return json.load(f) or {}
                except Exception as e:
                    logger.warning(f"Failed to load explicit config: {e}")
                    return {}

        # Layer 1: Global config (base)
        global_config_path = get_existing_global_config_path()
        if global_config_path.exists():
            try:
                with open(global_config_path, "r") as f:
                    global_config = json.load(f)
                if global_config:
                    merged_config = global_config
                    logger.debug(f"Loaded global config from: {global_config_path}")
            except Exception as e:
                logger.warning(f"Failed to load global config: {e}")

        # Layer 2: Project config (defaults for this project)
        project_config_path = next(
            (
                path / "config.json"
                for path in get_project_data_dir_candidates()
                if (path / "config.json").exists()
            ),
            get_project_data_dir() / "config.json",
        )
        if project_config_path.exists():
            try:
                with open(project_config_path, "r") as f:
                    project_config = json.load(f)
                if project_config:
                    merged_config = deep_merge(merged_config, project_config)
                    logger.debug(f"Merged project config from: {project_config_path}")
            except Exception as e:
                logger.warning(f"Failed to load project config: {e}")

        # Layer 3: Local config (override)
        local_config_path = get_existing_local_config_path()
        if local_config_path.exists():
            try:
                with open(local_config_path, "r") as f:
                    local_config = json.load(f)
                if local_config:
                    merged_config = deep_merge(merged_config, local_config)
                    logger.debug(f"Merged local config from: {local_config_path}")
            except Exception as e:
                logger.warning(f"Failed to load local config: {e}")

        if not merged_config:
            logger.debug("No user configuration found (global, project, or local)")

        return merged_config

    @staticmethod
    def _diff_from_defaults(
        config: Dict[str, Any], defaults: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract only values that differ from defaults.

        Recursively compares config against defaults and returns a minimal
        dict containing only user-modified values. When this minimal dict
        is deep_merged on top of defaults, it reproduces the full config.
        """
        if not isinstance(config, dict):
            return config
        if not isinstance(defaults, dict):
            return config

        result = {}
        for key, value in config.items():
            if key not in defaults:
                # User-added key not in defaults - keep it
                result[key] = value
            elif isinstance(value, dict) and isinstance(defaults[key], dict):
                # Recurse into nested dicts
                nested_diff = ConfigLoader._diff_from_defaults(value, defaults[key])
                if nested_diff:
                    result[key] = nested_diff
            elif value != defaults[key]:
                # Value differs from default - keep it
                result[key] = value
        return result

    def save_merged_config(
        self, config: Dict[str, Any], save_target: Optional[str] = None
    ) -> bool:
        """Save only user-modified configuration values to file.

        Computes the diff between the current in-memory config and code
        defaults (base config + plugin configs), then writes only the
        values that differ. This keeps config.json minimal and prevents
        defaults from bloating the file on every save.

        Note: base_prompt is excluded from saving because it should always
        be dynamically loaded from the system_prompt/*.md files on startup.

        Args:
            config: Configuration dictionary to save.
            save_target: Optional target override ("local" or "global").
                         None uses existing auto-detect behavior.

        Returns:
            True if save successful, False otherwise.
        """
        import json

        # JSON round-trip instead of deepcopy: strips non-serializable objects
        # (asyncio.Future, etc.) that plugins may inject at runtime.
        try:
            config_to_save = json.loads(json.dumps(config, default=str))
        except (TypeError, ValueError) as e:
            logger.warning(f"JSON round-trip failed, falling back to deepcopy: {e}")
            import copy

            config_to_save = copy.deepcopy(config)

        # Remove base_prompt - it should always be loaded fresh from .md files
        try:
            if "kollabor" in config_to_save and "llm" in config_to_save["kollabor"]:
                if "system_prompt" in config_to_save["kollabor"]["llm"]:
                    config_to_save["kollabor"]["llm"]["system_prompt"].pop(
                        "base_prompt", None
                    )
        except (KeyError, TypeError):
            pass  # Config structure doesn't match expected format

        # Compute defaults to diff against (same merge as load_complete_config)
        try:
            defaults = self.get_base_config()
            # Strip base_prompt from defaults too for accurate diff
            try:
                defaults["kollabor"]["llm"]["system_prompt"].pop("base_prompt", None)
            except (KeyError, TypeError):
                pass

            plugin_configs = self.get_plugin_configs()
            if plugin_configs:
                defaults = deep_merge(defaults, plugin_configs)

            # Include permission config defaults (merged separately in app init)
            try:
                from kollabor_events.permissions_config import (
                    PERMISSION_CONFIG_DEFAULTS,
                )

                defaults = deep_merge(defaults, PERMISSION_CONFIG_DEFAULTS)
            except ImportError:
                pass

            config_to_save = self._diff_from_defaults(config_to_save, defaults)
        except Exception as e:
            # If defaults computation fails, fall back to saving full config
            logger.warning(f"Could not compute config defaults for diff: {e}")

        # Determine save path based on explicit target or auto-detect
        if save_target == "local":
            save_path = get_local_config_path()
        elif save_target == "global":
            save_path = get_global_config_path()
        else:
            # CRITICAL FIX: Always determine save path dynamically based on existing files
            # Never use config_manager.config_path for save operations because it was
            # initialized before we knew which config files actually exist.
            # This prevents saving changes to the wrong layer (e.g., saving to
            # global config when local .kollab/config.json exists).
            save_path = self._get_config_save_path()
        save_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(save_path, "w") as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved configuration to: {save_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save configuration to {save_path}: {e}")
            return False

    def _get_config_save_path(self) -> Path:
        """Determine the appropriate config save path.

        CRITICAL: Must match the logic in _load_user_config_with_fallback()
        to ensure saves go to the same place as loads came from.

        The layered config system loads in this order:
        1. Global config (~/.kollab/config.json) - base layer
        2. Project config (~/.kollab/projects/<encoded>/config.json) - project defaults
        3. Local config (.kollab/config.json) - local override

        Save priority (reverse of load priority - save to highest layer):
        1. Local .kollab/config.json (if local config exists) - local override
        2. Project config (if exists) - project defaults
        3. Global config - fallback

        Returns:
            Path where config should be saved.
        """
        # Check local override first (highest priority)
        if any(path.exists() for path in get_local_config_path_candidates()):
            local_path = get_local_config_path()
            logger.debug(f"Saving to local config: {local_path}")
            return local_path

        # Check project config (middle priority)
        project_path = get_project_data_dir() / "config.json"
        if any((path / "config.json").exists() for path in get_project_data_dir_candidates()):
            logger.debug(f"Saving to project config: {project_path}")
            return project_path

        # Default to global config (lowest priority)
        global_path = get_global_config_path()
        logger.debug(f"Saving to global config: {global_path}")
        return global_path

    def update_with_plugins(self) -> bool:
        """Update the configuration file with newly discovered plugins.

        This method reloads the complete configuration including any new
        plugin configurations and saves it to the config file.

        Returns:
            True if update successful, False otherwise.
        """
        if not self.plugin_registry:
            logger.warning("No plugin registry available for config update")
            return False

        try:
            # Load complete config including plugins
            updated_config = self.load_complete_config()

            # Save the updated configuration
            success = self.save_merged_config(updated_config)

            if success:
                # Update the config manager's in-memory config
                self.config_manager.config = updated_config
                plugin_count = len(self.plugin_registry.list_plugins())
                logger.info(
                    f"Updated config with configurations from {plugin_count} plugins"
                )

            return success

        except Exception as e:
            log_and_continue(logger, "updating config with plugins", e)
            return False
