"""kollabor-events: Event bus and hook system for Kollabor."""

from .bus import EventBus
from .data_models import (
    ConversationMessage,
    ConversationMetadata,
    SessionMetadata,
)
from .dict_utils import (
    deep_merge,
    safe_get,
    safe_set,
)
from .error_utils import (
    ErrorAccumulator,
    log_and_continue,
    safe_execute,
)
from .executor import HookExecutor
from .hook_adapter import HookAdapter
from .models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    Event,
    EventType,
    Hook,
    HookPriority,
    HookStatus,
    ParameterDefinition,
    SlashCommand,
    SubcommandInfo,
    UIConfig,
)
from .processor import EventProcessor
from .ready_message import ReadyMessageCollector, ReadyMessageItem
from .registry import HookRegistry

__all__ = [
    "EventBus",
    "Event",
    "EventType",
    "Hook",
    "HookStatus",
    "HookPriority",
    "CommandMode",
    "CommandCategory",
    "UIConfig",
    "ParameterDefinition",
    "SubcommandInfo",
    "CommandDefinition",
    "SlashCommand",
    "CommandResult",
    "HookRegistry",
    "HookExecutor",
    "EventProcessor",
    "HookAdapter",
    "ReadyMessageItem",
    "ReadyMessageCollector",
    # Data models
    "ConversationMessage",
    "SessionMetadata",
    "ConversationMetadata",
    # Dict utils
    "deep_merge",
    "safe_get",
    "safe_set",
    # Error utils
    "log_and_continue",
    "safe_execute",
    "ErrorAccumulator",
]
