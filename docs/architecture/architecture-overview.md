---
title: "Architecture Overview"
doc_type: architecture-reference
created: 2026-02-24
modified: 2026-04-10
status: active
---
# Architecture Overview

Kollab is a monorepo containing 8 extracted packages plus the core
orchestration layer. Everything is event-driven with hooks at every level.

## Monorepo Structure

```
kollab/
├── kollabor/                    # Core orchestration
│   ├── application.py           # Main TerminalLLMChat entry point
│   ├── cli.py                   # CLI argument parsing
│   ├── commands/                # Slash command system
│   ├── llm/                     # LLM orchestration layer
│   ├── fullscreen/              # Fullscreen command integration
│   ├── logging/                 # Logging configuration
│   └── config_hooks.py          # JSON hook loading
│
├── packages/
│   ├── kollabor-ai/             # LLM services
│   ├── kollabor-agent/          # Agent runtime
│   ├── kollabor-tui/            # Terminal UI
│   ├── kollabor-events/         # Event bus + hooks
│   ├── kollabor-config/         # Configuration management
│   ├── kollabor-plugins/        # Plugin framework
│   ├── kollabor-engine/         # HTTP server (future)
│   └── kollabor-webui/          # Web UI (future)
│
├── plugins/                     # Plugin implementations
└── bundles/agents/              # Agent definitions
```

## Package Summary

| Package | Purpose | Key Exports |
|---------|---------|-------------|
| kollabor-ai | LLM API, conversation, profiles | ProfileManager, ConversationManager, APICommunicationService |
| kollabor-agent | Tool execution, MCP, shell | AgentManager, ToolExecutor, MCPIntegration |
| kollabor-tui | Terminal rendering, input, widgets | TerminalRenderer, InputHandler, EventDrivenRenderLoop |
| kollabor-events | Event bus, hook registry | EventBus, EventType, Hook |
| kollabor-config | Config loading, migration | ConfigService, ConfigManager |
| kollabor-plugins | Plugin discovery, loading | PluginRegistry, PluginFactory, KollaborPluginSDK |

## Data Flow

```
user input
    ↓
InputHandler (kollabor_tui)
    ↓
SlashCommandParser → CommandExecutor (kollabor/commands/)
    ↓
LLMService.process_stream() (kollabor/llm/)
    ↓
APICommunicationService (kollabor_ai)
    ↓
Provider (kollabor_ai/providers/)
    ↓
StreamingHandler → MessageDisplayService (kollabor_tui)
    ↓
MessageDisplayCoordinator → TerminalRenderer (kollabor_tui)
```

## Event Flow

Every action emits events through the EventBus:

```
event_bus.emit(EventType.USER_INPUT, context)
    ↓
HookRegistry.get_hooks(event_type)
    ↓
HookExecutor.execute() [priority order: SYSTEM(1000) → DISPLAY(10)]
    ↓
plugin.hook(context) → modified_context
    ↓
return modified_context to caller
```

Hook priorities:
- SYSTEM (1000): Core framework hooks
- SECURITY (900): Permission checks
- PREPROCESSING (500): Input transformation
- LLM (100): LLM request/response
- POSTPROCESSING (50): Response processing
- DISPLAY (10): UI rendering

## Import Paths

Core application (kollabor/):
```python
from kollabor.application import TerminalLLMChat
from kollabor.llm import LLMService
from kollabor.commands.registry import SlashCommandRegistry
```

LLM services (kollabor-ai):
```python
from kollabor_ai import ProfileManager, ConversationManager
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.registry import ProviderRegistry
```

Agent system (kollabor-agent):
```python
from kollabor_agent import AgentManager, ToolExecutor
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.permissions.manager import PermissionManager  # Direct import
```

Terminal UI (kollabor-tui):
```python
from kollabor_tui import TerminalRenderer, InputHandler
from kollabor_tui.message_coordinator import MessageDisplayCoordinator
from kollabor_tui.terminal_state import get_terminal_size
from kollabor_tui.design_system import T, S, C, solid, TagBox
```

Event system (kollabor-events):
```python
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_events.models import CommandResult, ConversationMessage
```

Configuration (kollabor-config):
```python
from kollabor_config import ConfigService, ConfigLoader, initialize_config
```

Plugin framework (kollabor-plugins):
```python
from kollabor_plugins import PluginRegistry, KollaborPluginSDK
from kollabor_plugins.base import BasePlugin
```

## Key Architectural Patterns

1. Event-Driven: All communication via EventBus hooks
2. Dependency Injection: Services registered on EventBus, accessed via get_service()
3. Facade Pattern: InputHandler, TerminalRenderer coordinate subsystems
4. Coordinator Pattern: MessageDisplayCoordinator prevents race conditions
5. Provider Pattern: Pluggable LLM providers (OpenAI, Anthropic, Gemini, etc.)
6. Plugin System: Dynamic discovery and loading from plugins/ directories

## Runtime Directory Structure

```
~/.kollab/
├── config.json              # User configuration
├── agents/                  # Global agent definitions
├── system_prompt/           # Global system prompts
└── projects/
    └── <encoded-path>/      # Project-specific data
        ├── conversations/   # JSONL logs
        ├── logs/            # Application logs
        └── .kollab/   # Local overrides
            ├── agents/      # Project-specific agents
            └── hooks.json   # Config hooks
```

Path encoding: `/home/user/myproject` → `home_user_myproject`

## Daemon Transparency and State Service

Phase 4.5 introduced a unified StateService abstraction so commands and widgets
access daemon state through one interface regardless of whether they run
in-process (local mode) or cross-process via RPC (attach mode).

### The Problem

Before phase 4.5, status widgets and command handlers read state directly from
in-process services (profile_manager, agent_manager, llm_service). In attach
mode those references pointed at the client's shadow managers, not the daemon's
live state. The status bar showed stale data and launch flags like
--profile openai-oauth silently failed.

### The Solution: StateService Protocol

One interface, two implementations. All reads and writes go through StateService:

- LocalStateService: in-process, wraps llm_service/profile_manager/agent_manager
  directly. Used in local mode and by daemon-side RPC handlers.
- RemoteStateService: RPC-backed, forwards calls to daemon and reconstructs
  snapshot DTOs from JSON responses. Used by attach clients.

Commands and widgets depend on the protocol, never on a specific implementation,
so code works identically in both modes.

### Service Registration

event_bus.register_service("state_service", state_service) at init. In attach
mode, the client skips LocalStateService registration entirely (phase 4.5
step 9) - RemoteStateService becomes the first and only state_service. No
interim period where state_service points at empty shadow state.

### Protocol Methods

Read methods (return snapshots):
- get_conversation, get_session_stats, get_active_profile, list_profiles
- get_permission_state, get_mcp_state, get_hub_state, get_processing_state
- get_system_info, get_active_agent, list_agents, list_skills
- get_system_prompt, list_contexts, get_active_context
- get_mcp_tools, get_hub_status_text, get_hub_whoami_text, get_hub_work_text

Write methods (return updated snapshots):
- save_conversation, set_active_profile, set_approval_mode
- set_agent, clear_agent, activate_skill, deactivate_skill
- set_system_prompt, create_context, attach_to_context, archive_context
- restart_session, enable_mcp_server, disable_mcp_server, test_mcp_server
- clear_session_approvals, clear_project_approvals, list_project_approvals
- resume_conversation

See kollabor/state/interface.py for the full protocol definition with
docstrings.

### Snapshot DTOs

All state crossing the RPC boundary is wrapped in immutable snapshot DTOs
(kollabor/state/snapshots.py). Wire-safe, JSON-serializable, with to_dict/from_dict
methods. Key snapshots:

- ConversationSnapshot, MessageDto
- SessionStats, ProfileSnapshot, ProfileListSnapshot
- PermissionSnapshot, McpSnapshot, McpServerInfo
- HubSnapshot, HubPeer, ProcessingSnapshot, SystemInfoSnapshot
- AgentSnapshot, AgentListSnapshot, SkillInfo, SkillListSnapshot
- SystemPromptSnapshot
- ConversationContext, ContextListSnapshot (multi-context)

### RPC Handler Registration

Daemon-side handlers (kollabor/state/handlers.py) wrap LocalStateService methods
and register on RpcServer under "state.<method>" namespace. 40+ handlers covering
the full StateService surface. Each handler is a thin async wrapper that calls
the daemon's LocalStateService and returns snapshot.to_dict().

### Multi-Context Architecture

Phase 4.5 step 6 added per-daemon context switching via ContextRegistry. A daemon
holds N named ConversationContexts; exactly one is "live" at any moment. Switching
contexts is snapshot-and-swap:

1. Snapshot the live LLMCoordinator state back into the old context
2. Load the new context's state into LLMCoordinator
3. Mark the new context as live

Critical invariant: list.clear() + list.extend() preserves the
conversation_history list object identity. QueueProcessor, SessionManager, and
hub plugin hold cached references to this list. Replacing the list object would
break 176+ cached references in the hot path. Snapshots preserve identity so
existing callers keep working unchanged.

Persistence: registries serialize to ~/.kollab/hub/contexts/{identity}.json.
Loaded on startup, saved after every write operation.

### Launch Flag Drain

CLI launch flags (--profile, --agent, --skill, --system-prompt, --context)
must cross the attach-client -> daemon boundary. They're stashed in
_attach_pending_flags during init and drained AFTER _read_remote_events task
is scheduled. Drain order matters: before the reader loop exists, RPC replies
queue in the socket buffer and every call times out after 10s (phase 4.5
step 5 landmine).

### Data Flow

```
local mode:
  command/widget -> state_service -> LocalStateService -> llm_service

attach mode:
  command/widget -> state_service -> RemoteStateService -> RPC
    -> daemon RpcServer -> state handler -> LocalStateService -> llm_service
```

### Known Gaps

Phase 4.5 shipped core commands migrated. Deferred to phase 4.6:
- /hub msg, broadcast, stop, spawn, org (cross-process messaging)
- /terminal view, attach (streaming transport needed)
- /sub completion notification (MessageInjector deprecation)
- /resume modal, search, branch, filter paths
- /login OAuth browser split

See docs/architecture/records/audits/AUDIT-2026-04-10-plugin-command-migration-phase-4-5-step-8.md for the full audit of which
commands and plugins are migrated and which defer to phase 4.6.
