# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

**Kollab Interface** - Terminal-based LLM chat application where **everything has hooks**. Every action triggers customizable hooks that plugins can attach to for complete customization.

## Architecture

Monorepo with extracted packages:
- **Core Application** (`kollabor/application.py`): Main orchestrator
- **Event System** (`packages/kollabor-events/`): Event bus + hook system (`kollabor_events`)
- **LLM AI** (`packages/kollabor-ai/`): API, conversation, model routing (`kollabor_ai`)
- **Terminal UI** (`packages/kollabor-tui/`): Rendering, input, widgets (`kollabor_tui`)
- **Agent System** (`packages/kollabor-agent/`): Shell execution, agent runtime (`kollabor_agent`)
- **Configuration** (`packages/kollabor-config/`): Config management (`kollabor_config`)
- **Plugin System** (`packages/kollabor-plugins/`, `plugins/`): Discovery and loading (`kollabor_plugins`)
- **LLM Orchestration** (`kollabor/llm/`): Coordinator, permissions, streaming
- **Commands** (`kollabor/commands/`): Slash command registry and handlers

## Key Components

### LLM AI Package (`kollabor_ai`)
- `api_communication_service.py` - API communication with rate limiting
- `conversation_logger.py` - Conversation persistence (KollaborConversationLogger)
- `conversation_manager.py` - Conversation state and history
- `model_router.py` - Model selection and routing
- `profile_manager.py` - LLM profile management
- `response_processor.py` - Response processing
- `response_parser.py` - Response parsing (includes Question Gate detection)
- `prompt_renderer.py` - Dynamic system prompt rendering
- `providers/` - Provider implementations (OpenAI, Anthropic, etc.)
- `oauth/` - OAuth token management

### LLM Orchestration (`kollabor/llm/`)
- `llm_coordinator.py` - Main LLM orchestration (replaces old llm_service.py)
- `hook_system.py` - LLM-specific hook management
- `message_handler.py` - Message processing
- `streaming_handler.py` - Streaming response handling
- `session_manager.py` - Session lifecycle
- `status_service.py` - LLM status reporting
- `permissions/` - Tool permission system
  - `hook.py` - Event bus integration at SECURITY priority (approval modes, risk assessment, response handling)
  - `README.md` - Permission system docs

### Terminal UI Package (`kollabor_tui`)
- `terminal_renderer.py` - Main terminal rendering with status areas
- `input_handler.py` - Raw mode input handling with key parsing
- `render_layout.py` - Terminal layout management
- `visual_effects.py` - Color palettes, visual effects, terminal color detection
- `status_renderer.py` - Multi-area status display
- `message_coordinator.py` - **CRITICAL** - Message flow AND render state coordination
- `message_renderer.py` - Message display rendering
- `buffer_manager.py` - Terminal buffer management
- `key_parser.py` - Keyboard input parsing
- `terminal_state.py` - Terminal state management
- `thinking_display.py` - Thinking animation display
- `tool_display.py` - Tool execution display
- `message_display_service.py` - Response formatting
- `design_system/` - Theme, colors, box rendering
- `widgets/` - UI widgets (Checkbox, Dropdown, Slider, TextInput, Label, Progress, SpinBox)
- `fullscreen/` - Fullscreen modal system
- `status/` - Status area views
- `input/` - Input subsystem (modal controller, etc.)

### Event System Package (`kollabor_events`)
- `bus.py` - Central event bus coordinator
- `registry.py` - Hook registration and lookup
- `executor.py` - Hook execution with error handling
- `processor.py` - Sequential event processing
- `models.py` - Event types, command definitions, hook models

### Commands (`kollabor/commands/`)
- `registry.py` - Command registration and lookup
- `executor.py` - Command execution
- `parser.py` - Command parsing
- `profile_command.py` - Profile management command
- `mcp_command.py` - MCP server management
- `ui_commands.py` - UI-related commands
- `system_commands/` - System command handlers

#### CRITICAL: Render State Management Rules

**NEVER directly manipulate these `terminal_renderer` properties:**
- `input_line_written`, `last_line_count`, `_last_render_content`, `writing_messages`

**ALWAYS use `MessageDisplayCoordinator` methods:**

```python
# Display messages
renderer.message_coordinator.display_message_sequence([...])

# Modal/fullscreen transitions
renderer.message_coordinator.enter_alternate_buffer()  # Before opening modal
renderer.message_coordinator.exit_alternate_buffer()  # After modal closes
renderer.message_coordinator.exit_alternate_buffer(restore_state=True)  # Restore state
```

**Why:** Direct manipulation causes duplicate input boxes, stale renders, and incorrect clearing. The coordinator uses flag-based coordination - `enter_alternate_buffer()` sets `writing_messages=True` to block the render loop.

**Modal exit patterns:**
```python
# Standard exit - restores state and renders input
await self._exit_modal_mode()

# Minimal exit - for commands displaying their own content
await self._exit_modal_mode_minimal()
```

**Safe direct calls:**
- `terminal_renderer.clear_active_area()` - uses state correctly
- `terminal_renderer.invalidate_render_cache()` - just clears cache

### Modal/Overlay Pattern for Status Widgets

**CRITICAL: Any fullscreen modal MUST use the coordinator pattern.**

```python
async def _show_my_modal(self) -> bool:
    self.coordinator.enter_alternate_buffer()
    try:
        sys.stdout.write('\033[?1049h')  # Enter alternate buffer
        sys.stdout.write('\033[?25l')    # Hide cursor
        sys.stdout.flush()
        # ... render and handle input ...
    finally:
        sys.stdout.write('\033[?25h')    # Show cursor
        sys.stdout.write('\033[?1049l')  # Exit alternate buffer
        sys.stdout.flush()
        self.coordinator.exit_alternate_buffer(restore_state=True)
    return result
```

**Why:** `coordinator.enter_alternate_buffer()` pauses the render loop, `exit_alternate_buffer()` resets render state. Without this, artifacts remain after modal closes. The ANSI alternate buffer preserves main screen content.

### Config Modal (`/config`)

Fullscreen modal for editing all application and plugin settings.

**Keyboard shortcuts:**
- `/` - Activate search filter (filters sections and widgets by label, help text, or config path)
- `Enter` - Lock filter and navigate filtered results
- `Esc` - Clear active filter (or close modal if no filter active)
- `Ctrl+S` - Save config (prompts Local vs Global target)
- Arrow keys / Tab - Navigate between widgets

### STDOUT IS SACRED

**CRITICAL: Never use print() or sys.stdout.write() in plugin code.**

The terminal UI is a delicate render system. Any rogue stdout writes
corrupt the display (wrong cursor position, duplicate input boxes,
garbled status bar). There are exactly TWO legal ways to output:

**For messages visible to the user:**
```python
renderer.message_coordinator.display_message_sequence([
    ("system", "your message", {"display_type": "info"})
])
```

**For hub agent messages:**
```python
renderer.message_coordinator.display_message_sequence([
    ("agent", content, {"agent_color": color, "tag_char": " > "})
])
```

**For logging (goes to file, not screen):**
```python
logger.info("debug info here")
```

**NEVER do this in plugins:**
```python
print("anything")           # ILLEGAL - corrupts UI
sys.stdout.write("anything") # ILLEGAL - corrupts UI
sys.stderr.write("debug")   # Use logger.debug() instead
```

The only code allowed to call print() is `message_coordinator._output_rendered()`
which IS the render system's output path. Everything else must route through it.

### Hub System (`plugins/hub/`)

Peer-to-peer agent mesh with persistent identity.

**Core files:**
- `plugin.py` - Lifecycle, social layer, /hub commands, message display, 33 pipeline tags
- `models.py` - GemDesignation, HubMessage, WorkSlot, designation pool
- `presence.py` - Heartbeat files, agent discovery, socket liveness checks
- `coordinator.py` - flock election, work queue, designation assignment
- `messenger.py` - Unix socket server/client, message delivery
- `vault.py` - Three-tier persistent memory (stream, working, crystallized)
- `crystal_store.py` - Structured crystal entries with IDs, keywords, dedup, nudge retrieval
- `text_utils.py` - Keyword extraction, stemming, relevance scoring for crystal nudge
- `scratchpad.py` - Ephemeral agent notes (survives compaction)
- `nudge_engine.py` - Behavioral nudges (scratchpad reminders, task checkpoints)
- `feed.py` - Dashboard content generator
- `org_launcher.py` - Launch teams from JSON org charts

**Key concepts:**
- Agents auto-discover via presence files in `~/.kollab/hub/presence/`
- First agent becomes coordinator via `flock()` on `hub.lock`
- Designations are gem-inspired names (lapis, peridot, ruby) with color castes
- Open channel: all agents see all messages (like Slack)
- Vaults persist across sessions at `~/.kollab/hub/vaults/<designation>/`
- `--agent` flag sets both agent bundle AND hub designation (phase 2+)
- Status widget: `◈ designation* +peers` on status bar row 3

**Vault system (persistent agent memory):**
- Three-tier storage per agent: `~/.kollab/hub/vaults/<designation>/`
  - `stream.jsonl` - Raw append-only log (ground truth)
  - `working_memory.md` - Rolling context for system prompt injection
  - `crystallized.md` - Structured long-term knowledge (dreaming output)
  - `scratchpad.md` - Ephemeral notes (survives compaction)
- **CrystalStore** (`plugins/hub/crystal_store.py`) - Structured entries with IDs, dates, keywords, summaries
- **Nudge system** - Auto-injects relevant crystal entries as system messages when user input matches keywords
- **Dreaming** - Idle agents review their stream and distill insights into crystallized.md
- **Rebirth** - On restart, `get_rebirth_context()` loads last 15 stream entries + working memory + crystallized + scratchpad

**Vault XML tags (agent-accessible):**
- `<vault_write keywords="a,b">insight text</vault_write>` - Save new crystal entry
- `<hub_vault name="identity"/>` - Get vault summary
- `<hub_vaults/>` - List all vaults
- Crystal read/search/list/edit/delete tags: see `docs/specs/crystal-memory-xml-tags.md`

**Scratchpad XML tags:**
- `<scratchpad>content</scratchpad>` - Overwrite
- `<scratchpad_append>content</scratchpad_append>` - Append
- `<scratchpad_get/>` - Read back
- `<scratchpad_clear/>` - Wipe

**CRITICAL: How `kollab --hub msg` actually works:**
- `kollab --hub msg <name> <text>` BROADCASTS to ALL online agents, not just the named target
- The named agent gets `is_intended=True` and TRIGGER_LLM_CONTINUE fires (wakes it up)
- Other agents see the message in their conversation_history but do NOT get triggered
- They will process it when something else triggers their next turn (dreaming, another msg, etc)
- This means: messages to koordinator are VISIBLE to lapis/sapphire but don't ACTIVATE them
- To activate a specific agent, send `kollab --hub msg <that-agent> <text>` directly
- To assign work to multiple agents, send SEPARATE `--hub msg` to EACH agent

**Message types for hub display:**
```python
# Direct message (bright, > tag)
display_message_sequence([("agent", content, {"agent_color": (r,g,b)})])

# Observed message (dim, ~ tag)
display_message_sequence([("agent", content, {
    "agent_color": (r,g,b), "tag_char": " ~ ", "observing": True
})])
```

**Hub CLI commands (no TUI needed):**
```bash
kollab --hub status              # who's online
kollab --hub stop <name|all>     # stop agent(s) -- falls back to SIGTERM
kollab --hub msg <name> <text>   # send message
kollab --hub capture <name> [N]  # last N interactions (not lines)
kollab --hub help                # full command list
```

**Starting/stopping agents:**
```bash
kollab --agent koordinator --detached   # start detached
kollab --hub stop koordinator                    # stop (socket + SIGTERM fallback)
kollab --hub status                              # verify
```

**Hub message flow (4 flows, see docs/specs/hub-message-flow.md):**
1. Human types -> `_broadcast_user_input` -> broadcast to all peers
2. LLM emits `<hub_msg>` -> `_parse_hub_messages` -> route + strip tags
3. Agent receives via socket/mailbox -> display + inject + trigger LLM
4. Event pipeline: PRE -> MAIN -> POST phase data propagation

**Hub message attributes:**
- `<hub_msg to="lapis">msg</hub_msg>` -- send, continue working (default)
- `<hub_msg to="lapis" wait="true">msg</hub_msg>` -- send, then STOP
- Auto-wait: messages with "standing by", "going quiet", etc auto-set wait

**LLM response processing (unified pipeline):**
- Single code path in queue_processor.py handles both native and XML tools
- response_parser runs on ALL responses regardless of native tool presence
- Plugin tags (hub_msg, scratchpad, etc) are registered via `register_plugin_tag()` and executed via `register_plugin_handler()` -- real tools, not regex hacks on LLM_RESPONSE_POST
- 43 hub pipeline tags registered (hub_msg, hub_broadcast, hub_stop, hub_status, scratchpad*, state_update, task_*, change feed, agent ops, vault_write, hub_ask_ctx)
- See `docs/specs/unified-tool-pipeline.md` for full design

**Troubleshooting hub bugs -- DO NOT GUESS, read the data:**
```bash
# 1. Check the log file for errors
grep "Failed executing\|ERROR.*hub" ~/.kollab/projects/*/logs/kollab.log | tail -20

# 2. Check raw API responses (tool calls, stop reasons)
ls ~/.kollab/projects/*/conversations/raw/ | sort -r | head -5
# Then parse with python: json.loads(line)['response']['tool_calls']

# 3. Check hub tag strip debug logging
grep "HUB_STRIP\|Hub tag strip" ~/.kollab/projects/*/logs/kollab.log | tail -10

# 4. Check agent presence
ls ~/.kollab/hub/presence/
cat ~/.kollab/hub/presence/*.json

# 5. Check if process is actually alive
kill -0 <pid>  # exit 0 = alive
```

**Common hub bugs and their root causes:**
- Raw `<hub_msg>` tags in UI: hook crashing (check "Failed executing hook" in log)
- Agent loop (standing by forever): force_continue on delivery, fix with wait="true"
- Agent not dying on stop: socket shutdown failed, needs SIGTERM fallback
- Doubled messages: dedup window too short (now 120s)
- Human typing in agent A shows as "-> agent B": broadcast display needs source_agent metadata

**Live debugging workflow (the way that actually works):**

When a user reports a hub bug, do not guess. Run this loop:

1. **Reproduce via CLI** (no TUI attach needed for most bugs):
   ```bash
   kollab --hub status                    # who's online (also: kollab --hub help)
   kollab --hub msg <agent> '<text>'      # send a message + activate agent
   kollab --hub capture <agent> [N]       # last N interactions over the socket
   kollab --hub stop <agent|all>          # clean up between attempts
   ```

   To trigger spawn from outside, message the coordinator:
   ```bash
   kollab --hub msg koordinator 'spawn lapis with: <hub_spawn name="lapis">echo test</hub_spawn>'
   ```

2. **Read the log, do NOT guess.** All agents share one project log:
   ```bash
   tail -3000 ~/.kollab/projects/Users_example_dev_kollab/logs/kollab.log > /tmp/recent.log
   grep "TRIGGER_LLM_CONTINUE\|Hub continue\|Sending initial\|Failed executing\|coalescing" /tmp/recent.log | head -40
   ```
   Sort by timestamp. Count cause→effect pairs. The amplifier is usually
   visible (e.g. 1 peer message → 12 "Hub continue: turn N" lines = stacked
   retries; 1 spawn → no "Sending initial message" = task delivery broken).

3. **Capture is reverse-chronological + interleaved with vault stream
   playback.** Recent socket messages appear at the top, but `[session_start]`
   / `[session_end]` lines may be from prior sessions in the agent's vault.
   Cross-check with the log timestamps when ordering matters.

4. **Distinguish "agent is lying" from "fix isn't working."** Agents
   hallucinate. They will claim to have run a tool when they didn't. The
   discriminator is `[TOOL-EXECUTION] tool execution completed: [SUCCESS]`
   in the log -- that's the tool runner, not the LLM. If the log shows
   `Sending initial message: <task>` but no subsequent `Tool execution
   completed`, the task was delivered but the model never called a tool.
   That's a prompt/model issue, not a delivery bug.

5. **Vault rebirth is not a bug.** Agents load
   `~/.kollab/hub/vaults/<identity>/` (stream + working_memory +
   crystallized + scratchpad) on startup. Spawning lapis after lapis has
   3 prior sessions of work means lapis will reference that work in its
   first reply. For clean smoke tests, use a gem with a thinner vault
   (`sapphire`, `peridot`) or `rm -rf ~/.kollab/hub/vaults/<name>`
   first (only with explicit user permission).

6. **Plumbing-only fixes don't need a live test.** Lint, dead code
   removal, and surgical refactors only need `python -m pytest tests/unit/`
   green. Behavioral changes to hub message flow, spawn, or LLM
   continuation MUST be verified live via the loop above.

**Plugin tool SDK (shipped):**
- `response_parser.register_plugin_tag(name, pattern, display_label, extractor)` -- register XML tags for parsing
- `tool_executor.register_plugin_handler(name, handler_method)` -- register execution handlers
- Handler must return `ToolExecutionResult(tool_id=..., tool_type=..., success=..., output=...)` -- NOT tool_name/result kwargs
- See `docs/specs/unified-tool-pipeline.md` for full design

**AltView fullscreen views (use AltView stack, NOT LiveModal):**
- `/hub feed` - Live dashboard (plugins/altview/hub_feed_altview.py)
- `/hub console` - Sidebar + feed panel (plugins/altview/hub_console_altview.py)
- `/terminal view` - Tmux session viewer (plugins/terminal_altview.py)

### Plugin Architecture
- Plugin discovery from `plugins/` directory
- Dynamic instantiation with dependency injection
- Hook registration for event interception
- Configuration merging from plugin configs

### UI Design System (`kollabor_tui.design_system`)

**CRITICAL: Always use existing UI components. Never invent new patterns.**

**Core Imports:**
```python
from kollabor_tui.design_system import T, S, C, Box, TagBox, solid, solid_fg, gradient
```

**Theme Access (`T()`):**
- `T().primary`, `T().dark`, `T().text`, `T().text_dim`
- `T().success`, `T().error`, `T().warning`

**Style Constants (`S`):**
- `S.BOLD`, `S.RESET_BOLD`, `S.DIM`, `S.RESET_DIM`, `S.UNDERLINE`, `S.RESET_UNDERLINE`

**Box Rendering (solid block style):**
```python
solid_fg("▄" * width, T().dark[0])              # Top edge
solid(f"   {text:<{w}}", T().dark[0], T().text, w)  # Content
solid_fg("▀" * width, T().dark[0])              # Bottom edge
```

**TagBox (tag + content pattern):**
```python
TagBox.render(
    lines=["content"],
    tag_bg=T().primary[0], tag_fg=T().text_dark, tag_width=3,
    content_colors=T().dark[0], content_fg=T().text,
    content_width=width - 7,
    tag_chars=[" > "],
)
```

**Widgets (`kollabor_tui.widgets`):**
`CheckboxWidget`, `DropdownWidget`, `SliderWidget`, `TextInputWidget`, `LabelWidget`, `ProgressWidget`, `SpinBoxWidget`

**DO NOT:**
- Create box-drawing with characters (use solid block style instead)
- Hardcode colors (use `T()`)
- Access `terminal_state.width` directly (use `get_size()`)
- Invent new widget styles

### Universal Terminal State Management

**CRITICAL: All code must access terminal dimensions through the global state.**

```python
from kollabor_tui.terminal_state import get_terminal_size, get_terminal_width, get_terminal_height

width, height = get_terminal_size()
width = get_terminal_width()
height = get_terminal_height()

# Direct instance
from kollabor_tui.terminal_state import get_global_terminal_state
ts = get_global_terminal_state()
width, height = ts.get_size()
```

**WRONG:**
```python
import shutil
size = shutil.get_terminal_size()  # NO!
import os
size = os.get_terminal_size()  # NO!
```

**For Plugins:**
```python
async def render_frame(self, delta_time: float) -> bool:
    width, height = self.renderer.get_terminal_size()
```

## Development Commands

```bash
# Installation
pip install -e .              # Development mode
pip install -e ".[dev]"       # With dev dependencies
pip install kollab             # From PyPI

# Running
kollab                        # Installed command
python main.py                # Development mode
echo "query" | kollab -p      # Pipe mode
kollab "query" --timeout 5min # With timeout

# Testing
tests/tmux/lib/test_runner.sh tests/tmux/specs/mcp_memory_usage.json  # JSON test
tests/tmux/run_all_tests.sh    # All tmux tests
python tests/run_tests.py      # Unit tests
python scripts/validate_bundled_agent_skills.py  # Bundled Agent Skills layout (agentskills.io)
python -m unittest tests.unit.llm.test_llm_plugin  # Specific test

# Code quality
python -m black kollabor/ plugins/ tests/ main.py   # Format (88 chars)
python -m ruff check kollabor/ packages/ plugins/  # Lint (120 chars)
python -m mypy kollabor/ plugins/                  # Type check
python scripts/clean.py                        # Clean cache

# Building
python scripts/clean.py
python -m build
python -m twine upload --repository testpypi dist/*
```

**Log locations:**
- `~/.kollab/projects/<encoded-path>/logs/kollab.log` (daily rotation)
- `~/.kollab/config.json` (global config)
- `~/.kollab/projects/<encoded-path>/conversations/` (JSONL)

## Agent Verification Requirements (MANDATORY)

**All agents implementing features MUST create JSON-based tmux verification tests.**

1. **Create JSON test spec** in `tests/tmux/specs/`:
```json
{
  "name": "feature-name",
  "description": "What this test verifies",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "slash_command", "command": "mcp", "subcommand": "show" },
    { "action": "sleep", "seconds": 1 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "MCP|Status" }
  ]
}
```

2. **Available actions:** `start_app`, `slash_command`, `type`, `send_keys`, `capture`, `assert_contains`, `assert_not_contains`, `sleep`, `section`

3. **Run:** `tests/tmux/lib/test_runner.sh tests/tmux/specs/your-test.json`

4. **Report:**
```
implementation complete:
  feature: [name]
  test: tests/tmux/specs/feature-name.json
  result: PASS (X/Y assertions verified)
```

**DO NOT report implementation as complete without a passing test.**

See `tests/tmux/README.md` for complete documentation.

## Entry Points

1. **`kollab` command** (after `pip install`): Entry point `kollab = "kollabor_cli_main:cli_main"` in `pyproject.toml`
2. **`python main.py`** (development): Direct execution from local `kollabor/`

Both initialize `TerminalLLMChat` in `kollabor/application.py`.

## Configuration System

**Dot notation:** `config.get("kollabor.llm.max_history", 90)`

**Global directory (`~/.kollab/`):**
- `config.json` - User configuration
- `agents/` - Global agent definitions
- `projects/` - Project-specific data

**Project data (`~/.kollab/projects/<encoded-path>/`):**
- `conversations/` - JSONL logs
- `conversations/raw/` - Raw API logs
- `conversations/memory/` - Intelligence cache
- `conversations/snapshots/` - Snapshots
- `logs/` - Application logs

**Path encoding:** `/home/user/myproject` -> `home_user_myproject`

**Local directory (`.kollab/` - OPTIONAL):**
- `agents/` - Project-specific agents (override global)

**Agent resolution:** Local -> Global

**Config save targets (`/config` modal, `Ctrl+S`):**
- Local (`L`): Saves to `.kollab/config.json` in cwd (project-specific overrides)
- Global (`G`): Saves to `~/.kollab/config.json` (user-wide settings)
- Only overrides (diff from defaults) are persisted, not the full config tree

### Dynamic System Prompts with `<trender>` Tags

System prompts support dynamic content rendering at runtime (`kollabor_ai.prompt_renderer`):

```markdown
<!-- File includes (most common) -->
<trender type="include" path="sections/00-header.md" />

<!-- Agents + skills listing -->
<trender type="agents_list" />

<!-- Shell aliases detected from user's shell -->
<trender type="shell_aliases" />

<!-- Connected MCP servers and tools -->
<trender type="mcp_tools" />

<!-- Active LLM profile (provider, model, endpoint, log file) -->
<trender type="active_llm" />

<!-- Hub tags (require event_bus with hub_plugin service) -->
<trender type="hub_identity" />
<trender type="hub_roster" />
<trender type="hub_vault" />
<trender type="hub_work_queue" />
<trender type="hub_peers" />

<!-- Raw shell command execution -->
<trender>git log --oneline -5</trender>
```

**Implemented types** (see `packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py`):
`include`, `agents_list`, `shell_aliases`, `mcp_tools`, `active_llm`,
`hub_identity`, `hub_roster`, `hub_vault`, `hub_work_queue`, `hub_peers`,
plus raw `<trender>cmd</trender>` shell execution.

Unknown `type=` attributes are left in the prompt as-is (no error, but no render).

**Priority order:** CLI `--system-prompt` arg -> `KOLLAB_SYSTEM_PROMPT` env var -> `KOLLAB_SYSTEM_PROMPT_FILE` env var -> Local `.kollab/agents/default/system_prompt.md` -> Global `~/.kollab/agents/default/system_prompt.md` -> Built-in fallback

## Core Architecture Patterns

### Event-Driven Design
Event bus (`kollabor_events.bus`) coordinates:
- **HookRegistry** (`kollabor_events.registry`) - Hook registration and lookup
- **HookExecutor** (`kollabor_events.executor`) - Hook execution with error handling
- **EventProcessor** (`kollabor_events.processor`) - Sequential event processing
- **EventBus** (`kollabor_events.bus`) - Central coordinator

### Plugin Lifecycle
1. **Discovery** - `PluginDiscovery` scans `plugins/`
2. **Registry** - `PluginRegistry` maintains metadata
3. **Factory** - `PluginFactory` instantiates with dependency injection
4. **Initialization** - Plugins call `initialize()` and `register_hooks()`
5. **Execution** - Events trigger registered hooks with priority ordering
6. **Cleanup** - Plugins call `shutdown()`

### LLM Coordinator Architecture
`LLMCoordinator` (`kollabor/llm/llm_coordinator.py`) orchestrates:
- **APICommunicationService** (`kollabor_ai`) - HTTP client with rate limiting
- **KollaborConversationLogger** (`kollabor_ai`) - Persistent conversation history
- **MessageDisplayService** (`kollabor_tui`) - Response formatting and streaming
- **StreamingHandler** (`kollabor/llm/`) - Stream processing
- **MessageHandler** (`kollabor/llm/`) - Message flow
- **LLMHookSystem** (`kollabor/llm/`) - Request/response interception

### Question Gate Protocol

Suspends tool execution when agent asks clarifying questions using `<question>` tags (prevents runaway loops):

1. Agent includes `<question>...</question>` tag
2. System detects tag, suspends pending tools (stored in `pending_tools`)
3. User responds
4. Suspended tools execute, results injected
5. Agent continues

**Configuration:** `kollabor.llm.question_gate_enabled` (default: `true`)

**Key files:** `kollabor_ai.response_parser` (detection), `kollabor/llm/llm_coordinator.py` (queue management)

See `docs/features/question-gate-protocol.md`.

### Plugin System Details
**Discovery locations (in order):**
1. `<package_root>/plugins/` (pip install)
2. `./plugins/` (development mode)

**Each plugin can:**
- Register hooks at any priority (CRITICAL, HIGH, NORMAL, LOW)
- Access shared services via dependency injection
- Contribute status line items to areas A, B, or C
- Merge custom configuration into global config
- Register slash commands via CommandRegistry

## Hook System

Plugins can:
- Intercept user input (`pre_user_input`)
- Transform LLM requests (`pre_api_request`)
- Process responses (`post_api_response`)
- Add custom status indicators (`get_status_line()`)
- Create new terminal UI elements

### Config Hooks (JSON, no Python needed)

Claude Code-compatible JSON hooks. Create `.kollab/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_edit",
        "hooks": [{ "type": "command", "command": "python guard.py" }]
      }
    ]
  }
}
```

Commands receive event data on stdin (JSON), respond via exit code + stdout JSON.
Exit 0 = proceed, exit 2 = block. Supports Claude Code event names (`PreToolUse`,
`UserPromptSubmit`, etc.) plus Kollabor-native names (`LlmRequestPre`, `McpToolCallPre`, etc.).

**Key file:** `kollabor/config_hooks.py` (ConfigHookLoader)

See `docs/features/config-hooks.md` for full reference.

## Plugin Development

Plugins should:
1. Inherit from base classes in `kollabor_plugins`
2. Register hooks in `register_hooks()` using `EventType` enum
3. Provide status info via `get_status_line()`
4. Implement `initialize()` and `shutdown()`
5. Use `async def` for all hook handlers
6. Optionally implement `get_config_widgets()` to expose settings in the `/config` modal

### Plugin Config Widgets

Plugins can expose configurable settings in the `/config` modal by implementing a static `get_config_widgets()` method. Widgets are auto-discovered from the `plugins/` directory (no manual registration needed).

```python
@staticmethod
def get_config_widgets() -> Dict[str, Any]:
    return {
        "title": "My Plugin",
        "widgets": [
            {"type": "checkbox", "label": "Enabled", "config_path": "plugins.my_plugin.enabled", "help": "Enable plugin"},
            {"type": "slider", "label": "Timeout", "config_path": "plugins.my_plugin.timeout", "min_value": 1, "max_value": 60, "step": 1, "help": "Timeout in seconds"},
        ],
    }
```

Supported widget types: `checkbox`, `slider`, `dropdown`, `text_input`, `spinbox`. Each widget requires `type`, `label`, `config_path`, and `help`. Additional keys depend on the widget type (e.g., `min_value`/`max_value`/`step` for slider, `options` for dropdown).

**Important:** every `config_path` in a widget must have a matching entry in `get_default_config()`. If the default is missing, the widget will display an empty or incorrect value regardless of what the user sets. This is the single most common bug in config widgets — always verify the `config_path` key appears in both `get_config_widgets()` and `get_default_config()`.

## Project Structure

```
.
├── kollabor/                       # Core orchestration
│   ├── application.py             # Main TerminalLLMChat class
│   ├── cli.py                     # CLI entry point
│   ├── commands/                  # Slash command system
│   │   ├── registry.py           # Command registration
│   │   ├── executor.py           # Command execution
│   │   ├── profile_command.py    # Profile management
│   │   ├── mcp_command.py        # MCP server management
│   │   └── system_commands/      # System command handlers
│   ├── llm/                       # LLM orchestration layer
│   │   ├── llm_coordinator.py    # Main coordinator
│   │   ├── hook_system.py        # LLM hooks
│   │   ├── streaming_handler.py  # Stream processing
│   │   ├── message_handler.py    # Message flow
│   │   ├── session_manager.py    # Session lifecycle
│   │   └── permissions/          # Tool permission system
│   ├── config_hooks.py           # JSON hook loading
│   ├── fullscreen/               # Fullscreen command integration
│   └── logging/                  # Logging configuration
├── packages/                      # Extracted packages
│   ├── kollabor-ai/              # LLM services (kollabor_ai)
│   ├── kollabor-tui/             # Terminal UI (kollabor_tui)
│   ├── kollabor-events/          # Event system (kollabor_events)
│   ├── kollabor-config/          # Configuration (kollabor_config)
│   ├── kollabor-agent/           # Agent runtime (kollabor_agent)
│   ├── kollabor-plugins/         # Plugin framework (kollabor_plugins)
│   ├── kollabor-engine/          # Engine server (kollabor_engine)
│   ├── kollabor-rpc/             # RPC transport (kollabor_rpc)
│   └── kollabor-webui/           # Web UI (kollabor_webui)
├── plugins/                       # Plugin implementations
│   ├── modern_input_plugin.py
│   ├── hook_monitoring_plugin.py
│   ├── mcp_plugin.py, mcp_status_plugin.py
│   ├── agent_orchestrator_plugin.py
│   ├── context_service_plugin.py, context_compaction_plugin.py
│   ├── resume_conversation_plugin.py
│   ├── hub/, altview/, modern_input/, deep_thought/, fullscreen/
│   └── [other plugins]
├── bundles/                       # Agent bundles
│   ├── agents/                   # Agent definitions (prompts + agent.json skill names)
│   │   └── _base/                # Shared base template (all agents inherit)
│   └── skills/                   # Agent Skills: <name>/SKILL.md ([agentskills.io](https://agentskills.io/specification))
├── tests/                         # Test suite
│   ├── tmux/                     # JSON-based UI tests
│   │   ├── specs/                # Test specifications
│   │   ├── lib/                  # Test framework
│   │   └── README.md
│   ├── unit/, integration/, visual/
│   └── test_*.py
├── docs/                          # Documentation
│   ├── features/                 # Feature specifications
│   ├── guides/                   # Developer guides
│   ├── reference/                # API and architecture reference
│   ├── providers/                # Provider setup guides
│   ├── mcp/                      # MCP documentation
│   └── plugins/                  # Plugin specifications
├── main.py                       # Entry point
└── .github/scripts/              # Repository automation
```

**Runtime data (`~/.kollab/`):**
```
~/.kollab/
├── config.json                   # User configuration
├── agents/                       # Global agent definitions
└── projects/                     # Project-specific data
    └── <encoded-path>/
        ├── conversations/        # Conversation history (JSONL)
        └── logs/                 # Application logs
```

## Development Guidelines

### Git Workflow
**Pre-commit:** Commits to `kollabor/`, `plugins/`, `tests/` MUST reference GitHub issue
- Branch: `issue-123-description` or `feature/issue-123-description`
- Commit: `fixes #123`, `closes #123`, `resolves #123`, or `#123`
- Main branch protected - no direct pushes

```bash
gh issue create --title "Fix version display"
git switch -c issue-8-version-display-fix
git commit -m "Fix version display fixes #8"
git push -u kollaborai issue-8-version-display-fix
gh pr create --title "Fix version display" --body "Fixes #8"
```

### Version Management
Version managed centrally in `pyproject.toml`:
- **Development mode** (`python main.py`): Reads from `pyproject.toml` at runtime
- **Production mode** (pip install): Uses `importlib.metadata.version("kollab")`

Files reading version: `kollabor/application.py`, `kollabor/cli.py`, `kollabor_config.loader`

To update: Only modify `pyproject.toml`.

### Code Standards
- PEP 8 with Black formatting (88-char) and ruff linting (120-char)
- Lint: `ruff check` must pass with 0 violations before commit
- Double quotes for strings, single for chars
- Type hints on public functions
- `async def` for all hooks (even if no await)
- `logging.getLogger(__name__)` for logging

### Async/Await Patterns
- Main event loop: `asyncio.run()` in `cli_main()`
- Concurrent tasks: `asyncio.gather()` for render loop + input handler
- Background tasks: Use `app.create_background_task()` for tracking
- Cleanup: All tasks cancelled in `app.cleanup()` via `finally`
- Plugin hooks: Must be async

### Testing Strategy
- **JSON tmux tests** (`tests/tmux/specs/`) - PRIMARY approach for UI/features
- Unit tests in `tests/unit/`
- Integration tests in `tests/integration/`
- Visual tests in `tests/visual/`
- Component tests (`test_*.py`)

### Hook Development
Consider:
- Hook priority (`HookPriority` enum: SYSTEM=1000, SECURITY=900, PREPROCESSING=500, LLM=100, POSTPROCESSING=50, DISPLAY=10)
- Error handling - hooks shouldn't crash (caught by HookExecutor)
- Performance - hooks are in hot path
- State management - avoid shared mutable state
- Return modified context from hooks
- All handlers must be async

## Key Features

### Interactive Mode
- Real-time status updates across three areas (A, B, C)
- Thinking animations during LLM processing
- Multi-line input with visual input box
- Conversation history with scrollback
- Plugin-driven extensibility

### Pipe Mode
- `kollab "query"` - Single query and exit
- `echo "query" | kollab -p` - Read from stdin
- `kollab --timeout 5min "task"` - Configurable timeout
- Suppresses interactive UI elements
- Full plugin support (`app.pipe_mode` flag)

### Tool Permission System
- 4 approval modes: CONFIRM_ALL (default), DEFAULT, AUTO_APPROVE_EDITS, TRUST_ALL
- Risk-based assessment with pattern matching
- Inline permission prompts (no modal interruption)
- Session-scoped approvals
- `/permissions` command for runtime management
- Event bus integration at SECURITY priority (900)

**Permission keys:** `a` approve once, `s` session, `p` project, `d` deny, `ESC` cancel
**Context-specific:** `A` always edits, `t` trust tool, `c` cancel

See `kollabor/llm/permissions/README.md`.

### Terminal Color Support

**Auto-detected modes:** TRUE_COLOR (24-bit), EXTENDED (256-color), BASIC (16-color), NONE

**Detection order:** `COLORTERM` env var -> `TERM_PROGRAM` -> `TERM` variable -> Apple Terminal (256-color)

**Manual override:**
```bash
KOLLAB_COLOR_MODE=256 kollab        # 256-color
KOLLAB_COLOR_MODE=truecolor kollab  # True color
KOLLAB_COLOR_MODE=none kollab       # No colors
export KOLLAB_COLOR_MODE=256        # Persist
```

**Programmatic:**
```python
from kollabor_tui.visual_effects import set_color_support, ColorSupport
set_color_support(ColorSupport.EXTENDED)
```

## Slash Commands

**Built-in:**
- `/help` - Show available commands
- `/save` - Save conversation (transcript|markdown|jsonl|clipboard|both|local)
- `/profile` (aliases: `/prof`, `/llm`) - Manage LLM profiles (list|set|create)
- `/permissions` (aliases: `/perms`, `/security`) - Manage permissions (show|default|strict|trust|stats|clear)
- `/terminal` (aliases: `/tmux`, `/term`, `/t`) - Manage tmux sessions (new|view|list|kill)
- `/hub` (aliases: `/mesh`) - Agent hub (status|msg|broadcast|feed|console|org|vault|whoami)
- `/login` - OAuth login for providers (currently OpenAI)
- `/mcp` - Manage MCP servers (show|add|remove)
- `/resume` - Resume a previous conversation
- `/config` - Fullscreen config editor modal. Press `/` to search/filter settings by label, help text, or config path. `Ctrl+S` prompts for Local (L) or Global (G) save target.
- `/matrix` - Matrix rain effect
- `/version` - Show version

**Command menu:** Triggered by `/`, filters with prefix matching, arrow keys navigate, Enter executes. Prioritizes name matches over aliases. When filtered to single command, subcommands appear and are selectable.

**Adding custom commands with subcommands:**
```python
from kollabor_events.models import CommandDefinition, CommandCategory, SubcommandInfo

command_def = CommandDefinition(
    name="mycommand",
    description="My custom command",
    category=CommandCategory.CUSTOM,
    aliases=["mc", "mycmd"],
    handler=my_handler_function,
    subcommands=[
        SubcommandInfo("action1", "", "Execute action 1"),
        SubcommandInfo("action2", "<arg>", "Execute action 2 with argument"),
    ]
)
command_registry.register_command(command_def)
```

## Current State

Monorepo with 8 extracted packages. Recent development:
- Unified tool pipeline -- single code path for native + XML tools, plugin tags are real tools via `register_plugin_tag()`/`register_plugin_handler()`
- Structured crystallized memories -- CrystalStore with keyword extraction, dedup, nudge system, dreaming
- Agent prompt consolidation -- shared base template (`bundles/agents/_base/`), removed 28k lines of duplicate skill files
- 43 hub pipeline tags (hub_msg, scratchpad, task management, change feed, agent ops, vault_write)
- Vault system with three-tier persistence (stream, working memory, crystallized)
- LLM service refactored into llm_coordinator.py + extracted modules
- Dynamic system prompt rendering with `<trender>` tags
- Environment variable configuration for LLM profiles
- OAuth login (OpenAI)
- MCP integration
- Tool permission system with risk assessment

**The codebase uses Python 3.12+ with async/await throughout.**

## Completion Checklist

Before closing out any feature or fix, run this audit.
Use sub agents for parallel checks when possible.

**Robustness:**
- [ ] Identify 5 failure modes and implement fixes
- [ ] Are errors visible to the user? (message_coordinator display, not print)
- [ ] Did this remove or break existing functionality?
- [ ] Are there pre-existing errors in nearby code? Fix them too
- [ ] Does the render loop survive this change? (no stale writing_messages)

**Testing:**
- [ ] Tmux test spec created in `tests/tmux/specs/`?
- [ ] Unit tests in `tests/unit/` for new logic?
- [ ] `ruff check` passes with 0 violations?
- [ ] `py_compile` passes on all modified files?
- [ ] Existing test suite still green? (`python -m pytest tests/unit/ -x -q`)

**Documentation + Discoverability:**
- [ ] `CLAUDE.md` updated for new components/patterns?
- [ ] `README.md` updated if user-facing feature?
- [ ] `docs/` page created or updated? (features/, guides/, specs/)
- [ ] Related docs cross-linked?

**Hub / Agent System (if applicable):**
- [ ] Pipeline tag count updated in log line?
- [ ] Agent prompt docs updated? (`bundles/agents/_base/sections/`)
- [ ] Vault/crystal tags documented in spec?
- [ ] Hub slash commands table updated in README?

**Plugin System (if applicable):**
- [ ] `register_plugin_tag` + `register_plugin_handler` pattern followed?
- [ ] `ToolExecutionResult` uses correct kwargs (`tool_id`, `tool_type`, `success`, `output`)?
- [ ] Handler follows vault_write pattern (lazy import, try/except, stream logging)?

**Changelog + Comms:**
- [ ] Git commit with issue reference?
- [ ] Does this impact kollabor.ai website docs? (`~/dev/kollabor.ai`)
- [ ] Does this impact kowork (web app)?
