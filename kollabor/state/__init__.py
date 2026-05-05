"""Kollabor StateService abstraction - unified interface for state access.

In local mode, commands and widgets read state from LocalStateService which
wraps in-process services directly. In attach mode, they read from
RemoteStateService which calls RPC methods on the daemon's LocalStateService.

The interface is identical in both modes. Commands don't know or care which
implementation they're talking to.
"""

from __future__ import annotations

from .context import ContextListSnapshot, ConversationContext
from .handlers import register_state_handlers
from .interface import StateService
from .local import LocalStateService
from .refresher import WidgetStateRefresher
from .remote import RemoteStateService
from .snapshots import (
    AgentListSnapshot,
    AgentSnapshot,
    ConversationSnapshot,
    HubPeer,
    HubSnapshot,
    McpServerInfo,
    McpSnapshot,
    MessageDto,
    PermissionSnapshot,
    ProcessingSnapshot,
    ProfileListSnapshot,
    ProfileSnapshot,
    SessionStats,
    SkillInfo,
    SkillListSnapshot,
    Snapshot,
    SystemInfoSnapshot,
    SystemPromptSnapshot,
)

__all__ = [
    "StateService",
    "LocalStateService",
    "RemoteStateService",
    "register_state_handlers",
    "WidgetStateRefresher",

    "Snapshot",
    "ConversationSnapshot",
    "MessageDto",
    "SessionStats",
    "ProfileSnapshot",
    "ProfileListSnapshot",
    "PermissionSnapshot",
    "McpSnapshot",
    "McpServerInfo",
    "HubSnapshot",
    "HubPeer",
    "ProcessingSnapshot",
    "SystemInfoSnapshot",
    # Phase 4.5
    "AgentSnapshot",
    "AgentListSnapshot",
    "SkillInfo",
    "SkillListSnapshot",
    "SystemPromptSnapshot",
    # Phase 4.5 step 6 -- multi-context daemon
    "ConversationContext",
    "ContextListSnapshot",
]
