"""kollabor-config: Configuration management for Kollab.

Provides config loading, hot reload, migration, plugin schemas,
and path management as reusable infrastructure.
"""

from .config_utils import (
    encode_project_path,
    ensure_config_directory,
    get_config_directory,
    get_config_directory_candidates,
    get_conversations_dir,
    get_existing_global_config_path,
    get_existing_local_config_path,
    get_global_config_path,
    get_global_config_path_candidates,
    get_local_config_directory,
    get_local_config_directory_candidates,
    get_local_config_path,
    get_local_config_path_candidates,
    get_logs_dir,
    get_project_data_dir,
    get_project_data_dir_candidates,
    get_system_prompt_content,
    get_system_prompt_path,
    initialize_config,
    initialize_system_prompt,
    resolve_global_path,
    resolve_local_path,
)
from .llm_task_config import BackgroundTasksConfig, LLMTaskConfig, QueueConfig
from .loader import ConfigLoader
from .manager import ConfigManager
from .plugin_config_manager import PluginConfigManager, get_plugin_config_manager
from .plugin_schema import (
    ConfigField,
    ConfigSchemaBuilder,
    PluginConfigSchema,
    WidgetType,
)
from .service import ConfigService

__all__ = [
    "ConfigManager",
    "ConfigLoader",
    "ConfigService",
    "LLMTaskConfig",
    "BackgroundTasksConfig",
    "QueueConfig",
    "PluginConfigSchema",
    "ConfigField",
    "WidgetType",
    "ConfigSchemaBuilder",
    "PluginConfigManager",
    "get_plugin_config_manager",
    "encode_project_path",
    "get_project_data_dir",
    "get_conversations_dir",
    "get_logs_dir",
    "get_system_prompt_content",
    "get_system_prompt_path",
    "initialize_system_prompt",
    "initialize_config",
    "get_config_directory",
    "get_config_directory_candidates",
    "get_local_config_directory",
    "get_local_config_directory_candidates",
    "get_global_config_path",
    "get_global_config_path_candidates",
    "get_existing_global_config_path",
    "get_local_config_path",
    "get_local_config_path_candidates",
    "get_existing_local_config_path",
    "get_project_data_dir_candidates",
    "resolve_global_path",
    "resolve_local_path",
    "ensure_config_directory",
]
