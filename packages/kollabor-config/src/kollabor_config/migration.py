"""
Automatic profile migration with provider detection and 4-tier keyring fallback.

Implements automatic migration of legacy profiles to current schema:
- Provider detection using FIXED ORDER from spec
- API key migration to 4-tier keyring fallback system
- Atomic migration with temp files
- Rollback on failure

Provider Detection (FIXED ORDER from spec lines 869-921):
1. Azure OpenAI (check for azure_base_url or api_type=azure)
2. Anthropic (check for sk-ant- prefix)
3. OpenAI (check for sk- prefix, exclude Azure)
4. Default: auto

4-Tier Keyring Fallback:
- Tier 1: OS keyring (if available)
- Tier 2: Encrypted file (AES-256-GCM, password from env or prompt)
- Tier 3: Environment variables (KOLLAB_API_KEY)
- Tier 4: Plaintext (opt-in with KOLLAB_ALLOW_PLAINTEXT_KEYS)
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .config_utils import get_existing_global_config_path, get_global_config_path
from .loader import CONFIG_VERSION, VERSION_KEY

logger = logging.getLogger(__name__)


# =============================================================================
# Provider Detection (FIXED ORDER from spec)
# =============================================================================


def detect_provider_from_profile(profile: Dict[str, Any]) -> str:
    """
    Detect provider type from profile data using FIXED ORDER.

    Priority (from spec lines 869-921):
    1. Explicit provider field (highest priority)
    2. Azure OpenAI (check for azure_base_url or api_type=azure)
    3. Anthropic (check for sk-ant- prefix)
    4. OpenAI (check for sk- prefix, exclude Azure)
    5. Default: auto

    Args:
        profile: Profile configuration dict

    Returns:
        Provider type string: 'azure_openai', 'anthropic', 'openai', or 'auto'
    """
    # 1. Explicit provider field (highest priority)
    if "provider" in profile:
        provider = profile["provider"]
        if provider and isinstance(provider, str):
            return str(provider)

    api_key = profile.get("api_key", "")
    api_base = profile.get("api_base", "") or profile.get("base_url", "")
    azure_base_url = profile.get("azure_base_url", "")
    api_type = profile.get("api_type", "")

    # 2. Azure OpenAI detection (must check FIRST before OpenAI)
    # Check for azure_base_url field
    if azure_base_url and isinstance(azure_base_url, str) and azure_base_url.strip():
        return "azure_openai"

    # Check for api_type=azure
    if api_type and isinstance(api_type, str) and api_type.lower() == "azure":
        return "azure_openai"

    # Check for azure.com in api_base
    if api_base and isinstance(api_base, str) and "azure.com" in api_base.lower():
        return "azure_openai"

    # Check for azure.openai.com or openai.azure.com in api_base
    if api_base and isinstance(api_base, str):
        if (
            "azure.openai.com" in api_base.lower()
            or "openai.azure.com" in api_base.lower()
        ):
            return "azure_openai"

    # 3. Anthropic detection (sk-ant- prefix is MORE SPECIFIC than sk-)
    if api_key and isinstance(api_key, str):
        api_key_lower = api_key.lower()
        if api_key_lower.startswith("sk-ant-"):
            return "anthropic"

    # 4. OpenAI detection (generic sk- prefix)
    if api_key and isinstance(api_key, str):
        if api_key.startswith("sk-"):
            return "openai"

    # 5. API base URL detection (after key format check)
    if api_base and isinstance(api_base, str):
        api_base_lower = api_base.lower()
        if "anthropic.com" in api_base_lower:
            return "anthropic"
        if "openai.com" in api_base_lower and "azure" not in api_base_lower:
            return "openai"

    # 6. Default for legacy configs
    return "auto"


# =============================================================================
# Profile Migration with 4-Tier Keyring Fallback
# =============================================================================


class ProfileMigrationError(Exception):
    """Raised when profile migration fails."""

    pass


class ProfileMigrator:
    """
    Automatic profile migration with 4-tier keyring fallback.

    Handles:
    - Provider detection using FIXED ORDER from spec
    - API key migration to secure keyring storage
    - Atomic migration with temp files
    - Rollback on failure

    4-Tier Keyring Fallback:
    1. OS keyring (if available)
    2. Encrypted file (AES-256-GCM, password from env)
    3. Environment variables (KOLLAB_API_KEY)
    4. Plaintext (opt-in with KOLLAB_ALLOW_PLAINTEXT_KEYS)
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize profile migrator.

        Args:
            config_path: Path to config file (optional, for testing)
        """
        self.config_path = config_path or get_existing_global_config_path()
        self.backup_path = self.config_path.with_suffix(".backup")
        self.temp_path = self.config_path.with_suffix(".tmp")

        # Initialize keyring storage backends (lazy load)
        self._key_manager = None
        self._encrypted_storage = None
        self._keyring_tier: Optional[int] = None

    def migrate_all_profiles(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate all profiles in config to current schema.

        Args:
            config: Configuration dictionary

        Returns:
            Migrated configuration dictionary

        Raises:
            ProfileMigrationError: If migration fails
        """
        version = config.get(VERSION_KEY, 1)

        if version == CONFIG_VERSION:
            logger.debug("Config already at current version, skipping migration")
            return config

        logger.info(f"Migrating config from version {version} to {CONFIG_VERSION}")

        # Create backup
        self._create_backup()

        try:
            # Perform migration
            migrated_config = self._migrate_config(config)

            # Validate migrated config
            self._validate_migrated_config(migrated_config)

            # Atomic write
            self._atomic_write(migrated_config)

            logger.info(
                f"Successfully migrated config from version {version} to {CONFIG_VERSION}"
            )
            return migrated_config

        except Exception as e:
            logger.error(f"Migration failed: {e}, rolling back from backup")
            self._rollback_from_backup()
            raise ProfileMigrationError(f"Migration failed: {e}") from e

    def _create_backup(self) -> None:
        """Create backup of original config file."""
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)
            logger.debug(f"Created backup: {self.backup_path}")

    def _rollback_from_backup(self) -> None:
        """Rollback config from backup."""
        if self.backup_path.exists():
            shutil.copy2(self.backup_path, self.config_path)
            logger.info(f"Rolled back config from backup: {self.backup_path}")

    def _atomic_write(self, config: Dict[str, Any]) -> None:
        """
        Atomically write config to file (temp file + rename).

        Args:
            config: Configuration dictionary to write
        """
        # Write to temp file
        with open(self.temp_path, "w") as f:
            json.dump(config, f, indent=2)

        # Atomic rename
        self.temp_path.replace(self.config_path)
        logger.debug(f"Atomically wrote config to: {self.config_path}")

    def _migrate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform actual migration logic.

        Args:
            config: Configuration dictionary

        Returns:
            Migrated configuration dictionary
        """
        version = config.get(VERSION_KEY, 1)

        # Version 1 -> Version 2: Migrate profiles with provider detection
        if version == 1:
            config = self._migrate_v1_to_v2(config)

        # Set current version
        config[VERSION_KEY] = CONFIG_VERSION

        return config

    def _migrate_v1_to_v2(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate version 1 config to version 2.

        Version 1 had no provider field and api_key in profiles.
        Version 2 adds provider field and migrates api_key to keyring.

        Args:
            config: Version 1 configuration

        Returns:
            Version 2 configuration
        """
        # Support both old "core" key and new "kollabor" key
        top_key = "kollabor" if "kollabor" in config else "core"
        core_llm = config.get(top_key, {}).get("llm", {})
        profiles = core_llm.get("profiles", {})

        if not profiles:
            logger.debug("No profiles to migrate")
            return config

        logger.info(f"Migrating {len(profiles)} profiles")

        for profile_name, profile in profiles.items():
            try:
                migrated_profile = self._migrate_profile(profile_name, profile)
                profiles[profile_name] = migrated_profile
                logger.info(f"Migrated profile: {profile_name}")
            except Exception as e:
                logger.warning(f"Failed to migrate profile {profile_name}: {e}")
                # Keep original profile on error
                profiles[profile_name] = profile

        # Rename "core" -> "kollabor" if needed
        if "core" in config:
            config["kollabor"] = config.pop("core")

        # Update config with migrated profiles
        if "kollabor" not in config:
            config["kollabor"] = {}
        if "llm" not in config["kollabor"]:
            config["kollabor"]["llm"] = {}

        config["kollabor"]["llm"]["profiles"] = profiles

        return config

    def _migrate_profile(
        self, profile_name: str, profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Migrate single profile: detect provider and migrate api_key.

        Args:
            profile_name: Profile name
            profile: Profile configuration

        Returns:
            Migrated profile configuration
        """
        # Create a copy to avoid modifying original
        migrated = dict(profile)

        # 1. Detect provider using FIXED ORDER from spec
        provider = detect_provider_from_profile(profile)
        migrated["provider"] = provider
        logger.debug(f"Detected provider '{provider}' for profile: {profile_name}")

        # 2. Migrate api_key to keyring storage
        api_key = profile.get("api_key")
        if api_key:
            try:
                self._migrate_api_key_to_keyring(profile_name, api_key, provider)
                # Remove api_key from config after successful migration
                migrated.pop("api_key", None)
                logger.info(f"Migrated API key for profile '{profile_name}' to keyring")
            except Exception as e:
                logger.warning(
                    f"Failed to migrate API key for profile '{profile_name}' to keyring: {e}. "
                    "API key remains in config file."
                )
                # Keep api_key in config on error

        return migrated

    def _migrate_api_key_to_keyring(
        self, profile_name: str, api_key: str, provider: str
    ) -> None:
        """
        Migrate API key to 4-tier keyring fallback system.

        Tries each tier in order:
        1. OS keyring (if available)
        2. Encrypted file (if available)
        3. Environment variables (check if already set)
        4. Plaintext (only if KOLLAB_ALLOW_PLAINTEXT_KEYS=true)

        Args:
            profile_name: Profile name
            api_key: API key to migrate
            provider: Provider type

        Raises:
            RuntimeError: If migration fails on all tiers
        """
        # Tier 1: Try OS keyring
        if self._try_os_keyring(profile_name, api_key):
            self._keyring_tier = 1
            return

        # Tier 2: Try encrypted file storage
        if self._try_encrypted_storage(profile_name, api_key):
            self._keyring_tier = 2
            return

        # Tier 3: Check environment variable
        if self._check_env_variable(profile_name, provider, api_key):
            self._keyring_tier = 3
            return

        # Tier 4: Plaintext storage (opt-in only)
        if self._try_plaintext_storage(profile_name, api_key):
            self._keyring_tier = 4
            return

        # All tiers failed
        raise RuntimeError(
            f"Failed to migrate API key for profile '{profile_name}' to any storage tier. "
            "Install keyring (pip install keyring) or set KOLLAB_ALLOW_PLAINTEXT_KEYS=true "
            "for development (not recommended)."
        )

    def _try_os_keyring(self, profile_name: str, api_key: str) -> bool:
        """
        Try storing API key in OS keyring (Tier 1).

        Args:
            profile_name: Profile name
            api_key: API key to store

        Returns:
            True if successful, False otherwise
        """
        try:
            from kollabor_ai.providers.security import APIKeyManager

            key_manager = APIKeyManager()
            # Use asyncio to run async method
            import asyncio

            asyncio.run(key_manager.store_key(profile_name, api_key))
            logger.info(
                f"Stored API key in OS keyring (Tier 1) for profile: {profile_name}"
            )
            return True
        except ImportError:
            logger.debug("keyring library not available, skipping Tier 1")
            return False
        except Exception as e:
            logger.debug(f"Failed to store in OS keyring: {e}")
            return False

    def _try_encrypted_storage(self, profile_name: str, api_key: str) -> bool:
        """
        Try storing API key in encrypted file (Tier 2).

        Args:
            profile_name: Profile name
            api_key: API key to store

        Returns:
            True if successful, False otherwise
        """
        # Only use tier 2 if user explicitly set an encryption password
        password = os.environ.get("KOLLAB_KEY_ENCRYPTION_PASSWORD")
        if not password:
            logger.debug(
                "KOLLAB_KEY_ENCRYPTION_PASSWORD not set, skipping Tier 2"
            )
            return False

        try:
            from kollabor_ai.providers.security import EncryptedFileKeyStorage

            storage_path = get_global_config_path().parent / "keys.enc"
            encrypted_storage = EncryptedFileKeyStorage(storage_path, password)

            # Use asyncio to run async method
            import asyncio

            asyncio.run(encrypted_storage.store_key(profile_name, api_key))
            logger.info(
                f"Stored API key in encrypted file (Tier 2) for profile: {profile_name}"
            )
            return True
        except ImportError:
            logger.debug("cryptography library not available, skipping Tier 2")
            return False
        except Exception as e:
            logger.debug(f"Failed to store in encrypted file: {e}")
            return False

    def _check_env_variable(
        self, profile_name: str, provider: str, api_key: str
    ) -> bool:
        """
        Check if API key is already in environment variable (Tier 3).

        Args:
            profile_name: Profile name
            provider: Provider type
            api_key: API key to check

        Returns:
            True if env var matches, False otherwise
        """
        # Map provider to environment variable
        env_vars = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "azure_openai": "AZURE_OPENAI_API_KEY",
        }

        env_var = env_vars.get(provider, f"{provider.upper()}_API_KEY")
        env_key = os.environ.get(env_var)

        if env_key and env_key == api_key:
            logger.info(f"API key already in environment variable (Tier 3): {env_var}")
            return True

        return False

    def _try_plaintext_storage(self, profile_name: str, api_key: str) -> bool:
        """
        Try storing API key in plaintext file (Tier 4 - OPT-IN ONLY).

        Args:
            profile_name: Profile name
            api_key: API key to store

        Returns:
            True if successful, False otherwise
        """
        # Check opt-in
        if not os.environ.get("KOLLAB_ALLOW_PLAINTEXT_KEYS", "").lower() == "true":
            logger.debug(
                "Plaintext storage not enabled (set KOLLAB_ALLOW_PLAINTEXT_KEYS=true)"
            )
            return False

        try:
            from kollabor_ai.providers.security import PlaintextKeyStorage

            storage_path = get_global_config_path().parent / "keys.json"
            plaintext_storage = PlaintextKeyStorage(storage_path)

            # Use asyncio to run async method
            import asyncio

            asyncio.run(plaintext_storage.store_key(profile_name, api_key))
            logger.warning(
                f"Stored API key in PLAINTEXT file (Tier 4) for profile: {profile_name}. "
                "This is INSECURE and should only be used for development."
            )
            return True
        except Exception as e:
            logger.debug(f"Failed to store in plaintext file: {e}")
            return False

    def _validate_migrated_config(self, config: Dict[str, Any]) -> None:
        """
        Validate migrated configuration.

        Args:
            config: Migrated configuration dictionary

        Raises:
            ProfileMigrationError: If validation fails
        """
        # Check version
        version = config.get(VERSION_KEY, 1)
        if version != CONFIG_VERSION:
            raise ProfileMigrationError(
                f"Migration failed: version mismatch (expected {CONFIG_VERSION}, got {version})"
            )

        # Validate profiles structure
        core_llm = config.get("kollabor", config.get("core", {})).get("llm", {})
        profiles = core_llm.get("profiles", {})

        for profile_name, profile in profiles.items():
            # Validate provider field exists
            if "provider" not in profile:
                raise ProfileMigrationError(
                    f"Migration failed: profile '{profile_name}' missing provider field"
                )

            # Validate provider value
            provider = profile["provider"]
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
                raise ProfileMigrationError(
                    f"Migration failed: profile '{profile_name}' has invalid provider '{provider}'"
                )

        logger.debug("Migrated config validation passed")


# =============================================================================
# Public API
# =============================================================================


def migrate_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Migrate configuration file to current version.

    Args:
        config_path: Path to config file (optional)

    Returns:
        Migrated configuration dictionary

    Raises:
        ProfileMigrationError: If migration fails
    """
    import json

    if config_path is None:
        config_path = get_existing_global_config_path()

    # Load existing config
    if not config_path.exists():
        logger.debug(f"Config file not found: {config_path}")
        return {}

    with open(config_path, "r") as f:
        config = json.load(f)

    # Migrate
    migrator = ProfileMigrator(config_path)
    return migrator.migrate_all_profiles(config)
