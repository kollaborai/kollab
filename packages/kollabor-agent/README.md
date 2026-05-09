
# kollabor-agent

`kollabor-agent` is the tool and agent execution runtime for Kollabor.

It owns tool dispatch, MCP server connections, file operations, shell execution,
agent/skill loading, background tasks, process management, and permission-aware
execution. The CLI and engine both build on this package when a model wants to
act on the local environment.

## Current Role

- Execute built-in tools such as terminal commands, file operations, and MCP
  tool calls.
- Load agent definitions and skills from project, user, and bundled locations.
- Manage MCP stdio servers and expose their tools to model providers.
- Provide permission risk assessment and approval flow primitives.
- Run background processes/tasks and provide process lifecycle helpers.
- Bridge native/XML tool definitions into the unified tool registry.

## Package Structure (~25K lines)

```
kollabor_agent/
├── agent_manager.py              (1744 lines) Agent/skill discovery and loading
├── runtime.py                    (651 lines)  AgentRuntime identity and lifecycle
├── queue_processor.py            (1286 lines) LLM turn execution and tool queue
├── tool_executor.py              (1121 lines) Unified tool dispatch engine
├── tool_definition.py            (173 lines)  ToolDefinition/ToolParameter dataclasses
├── tool_registry.py              (144 lines)  Singleton tool registry
├── tool_definitions/             (10 files)   Individual tool metadata modules
│   ├── file_ops.py               (542 lines)  read/edit/create/delete/move/copy/append
│   ├── hub.py                    (1039 lines) hub_msg, hub_spawn, hub_capture, etc.
│   ├── terminal.py               (188 lines)  terminal/shell execution
│   ├── scratchpad.py             (109 lines)  scratchpad get/append/clear
│   ├── task.py                   (148 lines)  task_checkpoint/complete/approve/reject
│   ├── context.py                (125 lines)  context_query, curate, evict
│   ├── git.py                    (52 lines)   git operations
│   └── wait.py                   (66 lines)   wait_for_user
├── tool_generators/              (3 files)    Schema/doc generators
│   ├── markdown.py               (154 lines)  ToolDefinition -> markdown docs
│   ├── xml_regex.py              (71 lines)   ToolDefinition -> XML regex pattern
│   └── native_json.py            (70 lines)   ToolDefinition -> JSON schema
├── file_operations_executor.py   (1471 lines) Safe file ops with backups/validation
├── shell_executor.py             (365 lines)  Async shell command execution
├── shell_command_service.py      (409 lines)  !-prefix shell command service
├── shell_utils.py                (230 lines)  Shell alias detection for prompt context
├── mcp_integration.py            (1221 lines) MCP JSON-RPC protocol over stdio
├── mcp_manager.py                (381 lines)  MCP config file management (no UI deps)
├── mentiko_adapter.py            (203 lines)  Mentiko platform spawn adapter
├── native_tools_handler.py       (214 lines)  Native API function-calling routing
├── process_manager.py            (782 lines)  Agent subprocess lifecycle management
├── background_task_manager.py    (507 lines)  Background task tracking with circuit breaker
├── permissions/                  (3 files)    Permission system
│   ├── manager.py                (734 lines)  Central permission manager
│   ├── risk_assessor.py          (109 lines)  Tool risk level assessment
│   └── response_handler.py       (106 lines)  User confirmation response handling
```

## Architecture

### Tool System

The tool system has four layers:

1. **ToolDefinition** (`tool_definition.py`) — Single source of truth for one
   tool's metadata: name, description, parameters, XML form, risk level,
   examples, safety features, key rules. Everything else is generated from this.

2. **ToolRegistry** (`tool_registry.py`) — Singleton holding all ToolDefinitions.
   Modules in `tool_definitions/` auto-register on import via `register_all()`.
   Supports lookup by name, native_name (underscore form), or XML tag.

3. **ToolGenerators** (`tool_generators/`) — Generate three artifacts from
   each ToolDefinition:
   - `markdown.py` — Markdown docs injected into agent system prompts
   - `xml_regex.py` — Regex pattern for parsing XML tool tags in LLM responses
   - `native_json.py` — JSON schema for native API function calling

4. **ToolExecutor** (`tool_executor.py`) — Unified dispatch engine. Routes
   tool calls to the correct handler:
   - Terminal commands -> `ShellExecutor`
   - File operations -> `FileOperationsExecutor`
   - MCP tool calls -> `MCPIntegration`
   - Plugin handlers -> registered via `register_plugin_handler()`
   - Returns `ToolExecutionResult` (success/error, output, execution time, metadata)

### Agent Loading

`AgentManager` (`agent_manager.py`) discovers and loads agents from three
locations in priority order:

1. Local project: `.kollab/agents/`
2. Global user: `~/.kollab/agents/`
3. Bundled defaults: `bundles/agents/`

Each agent is a directory containing:
- `system_prompt.md` (required)
- `agent.json` (optional metadata: description, profile, tools, skills list)
- `sections/` directory (optional prompt fragments included via `<trender>`)

Skills are **not** loose `.md` files next to `system_prompt.md`. They follow the
[Agent Skills](https://agentskills.io/specification) directory contract: each
skill is `<name>/SKILL.md` with YAML frontmatter, shipped or installed under
`bundles/skills/`, `~/.kollab/skills/`, or `.kollab/skills/`, and referenced by
name from `agent.json`'s `skills` field.

### Agent Runtime

`AgentRuntime` (`runtime.py`) is the canonical runtime representation of an
agent. It merges static definition (from disk) with live state (process, hub,
vault). Lifecycle states: BOOTING -> READY -> WORKING -> THINKING -> BLOCKED ->
DREAMING -> SUSPENDED -> DYING -> DEAD.

### Queue Processing

`QueueProcessor` (`queue_processor.py`) manages the LLM turn loop:
- Message queue with overflow strategies (drop_newest, drop_oldest, block)
- Batch message processing
- Conversation continuation (agentic multi-turn)
- Deduped LLM turn execution
- Tool result ingestion into context ledger for large outputs (>=8KB)

### File Operations

`FileOperationsExecutor` (`file_operations_executor.py`) provides 14 safe file
operations with:
- Automatic `.bak` backups before destructive operations
- Protected path checking (kollabor/, main.py, .git/, venv/)
- Path traversal prevention
- Binary file detection and rejection
- Optional Python syntax validation with automatic rollback on errors
- File size limits (10MB edit, 5MB create)
- Three path access modes: PROJECT_ONLY, KOLLAB_CONFIG, ANYWHERE

Operations: read, edit, create, create_overwrite, delete, move, copy,
copy_overwrite, append, insert_after, insert_before, mkdir, rmdir, grep.

### Shell Execution

Three shell-related modules:
- `ShellExecutor` — Low-level async subprocess execution with cancellation
  support, timeout handling, and per-instance state isolation.
- `ShellCommandService` — High-level `!`-prefix command service for user input.
  Validates against dangerous patterns, blocks interactive commands (vim, ssh),
  handles cd warnings, strips ANSI, emits pre/post/cancel/error events.
- `ShellUtils` — Detects user shell aliases (fd, rg, eza, etc.) and formats
  syntax hints for injection into AI system prompts.

### MCP Integration

Two modules handle Model Context Protocol:

- `MCPIntegration` (`mcp_integration.py`) — Live connection management.
  Implements MCP JSON-RPC 2.0 over stdio: server initialization handshake,
  `tools/list` for discovery, `tools/call` for execution. Manages
  `MCPServerConnection` instances (subprocess with piped stdio).

- `MCPManager` (`mcp_manager.py`) — Pure business logic for config file
  management. Load/save `mcp_settings.json`, enable/disable servers,
  configure API keys, list servers/tools with status. No UI dependencies.

### Permission System

Three modules under `permissions/`:

- `PermissionManager` — Central permission manager. Integrates with event bus
  to intercept tool execution. Manages approval modes (auto_approve, ask, etc.),
  session-scoped and project-scoped approvals, pending confirmations via
  asyncio.Event, and approval statistics.

- `RiskAssessor` — Evaluates tool risk levels based on configurable rules:
  blocked tools, trusted tools, high/medium risk command patterns (regex),
  and per-tool-type default risk levels.

- `ResponseHandler` — Processes user responses to permission dialogs:
  deny, approve-once, approve-session, approve-project, approve-always,
  approve-tool-always. Records approvals in the appropriate scope.

### Process Management

`ProcessManager` (`process_manager.py`) manages agent subprocess lifecycles:
- Strategy pattern for spawn backends (default: `SubprocessStrategy`)
- Circuit breaker for crash-loop prevention (3 failures in 120s opens circuit)
- Ring buffer for stdout capture (thread-safe, default 2000 lines)
- Resource tracking (RSS, uptime, restart count)
- Pluggable strategies: `SpawnStrategy` ABC with spawn/kill/is_alive/stdio
  methods. Future: DockerStrategy, SSHStrategy.

### Background Tasks

`BackgroundTaskManager` (`background_task_manager.py`) manages async background
tasks:
- Overflow strategies: drop_newest, drop_oldest, block (with configurable timeout)
- Circuit breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED state machine
- Configurable retry logic (attempts, delay)
- Task monitoring and periodic cleanup
- Per-task metrics (optional)

### Other Modules

- `MentikoAdapter` (`mentiko_adapter.py`) — Adapter for the Mentiko platform.
  Spawns kollabor agents via subprocess with `--detached` flag. Provides both
  Python API (`spawn_agent()`) and CLI entrypoint.

- `NativeToolsHandler` (`native_tools_handler.py`) — Bridges native API
  function calling (OpenAI/Anthropic style) with the tool executor. Handles
  malformed tool names from LLM confusion, routes file/terminal/plugin/MCP
  tools to correct handlers.

## Usage

```python
from kollabor_agent import MCPIntegration, ToolExecutor
from kollabor_events import EventBus

bus = EventBus()
mcp = MCPIntegration(event_bus=bus)
executor = ToolExecutor(mcp_integration=mcp, event_bus=bus)

result = await executor.execute_tool({
    "id": "tool_1",
    "type": "terminal",
    "command": "pwd",
})

print(result.success, result.output)
```

## Tool Definition Registration

Tool definitions live in `tool_definitions/` as Python modules. Each module
defines `ToolDefinition` instances and a `register_all()` function that
auto-registers on import. The registry loads all modules at startup via
`ToolRegistry.get_global()`.

To add a new tool:
1. Create a new module in `tool_definitions/`
2. Define a `ToolDefinition` with name, description, parameters, XML form,
   examples, and metadata
3. Add a `register_all()` function that calls `registry.register(tool_def)`
4. Import the module in `tool_registry.py` `_load_definitions()`

The three generators (markdown, XML regex, native JSON) automatically pick up
new tools. Agent system prompts include only tools listed in the agent's
`agent.json` `tools` array.

## Known Gaps

- Tool execution depends on caller-provided context for workspace/cwd behavior;
  service callers must wire project boundaries explicitly until the runtime has
  a stronger workspace object.
- MCP session connect behavior is mostly implemented through internal helpers;
  a smaller public connection API would reduce route-level coupling.
- Permission scope, bundle scope, and tool registry behavior are powerful but
  spread across several modules; more contract tests would make changes safer.
- Some diagnostics and legacy compatibility paths still live inside the runtime
  and should be made quieter or moved behind debug flags.
- Tool definitions are hardcoded in Python modules — no user-overridable path
  from `~/.kollab` exists yet.

## Roadmap

### Phase 1: Execution boundaries

- Add a first-class workspace/project execution context used by shell and file
  operations.
- Expose a public MCP connect/disconnect/list-tools API for service callers.
- Tighten cancellation behavior across shell, MCP, background, and plugin tools.

### Phase 2: Tool contract stabilization

- Keep the unified tool registry as the canonical source for schemas,
  permissions, bundle scope, and prompt rendering.
- Add regression tests for native JSON, XML, and markdown tool generation.
- Document exact tool result metadata expected by context-service and display
  layers.

### Phase 3: Agent runtime maturity

- Clarify which runtime pieces are reusable library APIs versus CLI orchestration
  internals.
- Harden process cleanup, circuit-breaker behavior, and background task
  reporting.
- Expand agent/skill loading tests across project, user, and bundled sources.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-agent/src/kollabor_agent/*.py
python -m pytest tests/unit/mcp tests/unit/test_auto_grant_mcp_tools.py -q
```

## Dependencies

- `kollabor-events`
- `kollabor-config`
- `kollabor-ai`
- `pyyaml`

## License

MIT
