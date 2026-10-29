# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

Before maintaining Kollab, read `bundles/skills/kollab-development/SKILL.md`.
It defines the unreleased development policy, current-implementation workflow,
agent handoff requirements, and conditional relay operations guide.

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
- `model_registry.py` - Reads `bundles/data/models.json`: context windows, `supports_sampling`, per-provider model lists. Lookup is **longest-prefix match** on the model string, so every point release whose specs differ from its family needs its own entry (`grok-4.5` vs `grok-4`). `supports_sampling: false` means the model 400s on temperature/top_p/top_k and every provider omits them. `thinking_default: "adaptive"` marks Claude models that think by default but hide the text; the anthropic provider asks them for `display: "summarized"` (`providers/tuning.py:thinking_params`). Freshness gated by `scripts/validate_models.py`.
- `model_catalog.py` - Live "what models does this provider offer?" for the `/model` picker (codex backend, OpenRouter, Anthropic, any OpenAI-compatible `{base_url}/models`). Never raises; empty list on failure.
- `pricing_registry.py` - Cost rates. Seeded from `models.json` (authoritative) on top of `default_pricing.json`, then `~/.kollab/pricing.json`. Falls back across providers for the same model id, since rates belong to the model not the transport.
- `profile_manager.py` - LLM profile management (`EFFORT_LEVELS`, per-field env resolution)
- `response_processor.py` - Response processing
- `response_parser.py` - Response parsing
- `prompt_renderer.py` - Dynamic system prompt rendering
- `prompt_composition.py` - Prompt libraries (`~/.kollab/prompt-libraries/`, `defaults` edits reach every role's `_base` sections) and role picks (`~/.kollab/prompt-roles/<role>.json`: per-variant `exclude` of `_base` keys and `add` of `library/key` sections; a variant with `providers` wins for that provider). `render_system_prompt` appends added sections only when given a bundle, so the per-turn rail is untouched; session context can never be excluded. Doc `docs/features/prompt-libraries.md`
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

### Panels (`kollabor/panels/`)

Daemon-owned screens the web UI renders as Settings tabs (spec `docs/specs/webui-unified-config.md`, user doc `docs/features/web-settings-panels.md`). The terminal's fullscreen views and the web call the same code, so a save from either shows in both.

- **Contract** (`base.py`): a `Panel` has `name`, `kind` (`form` | `picker` | `wizard`), `async describe(ctx, params)` (the screen as data) and `async act(ctx, action, payload)`. Return `ok_result(...)` / `error_result(msg, {path: text})`, or raise `PanelError(msg, errors=None, status=None)` for a request you cannot serve (400 default; 403 read-only, 404 unknown row/action, 503 service unavailable). Non-empty `errors` makes the route answer 400.
- **Registry**: each `kollabor.panels.<module>` exports `PANELS: dict[str, Panel]`; a name's first dash segment is its module (`connect-join` lives in `connect.py`). Lookup is lazy (`get_panel`, `require_panel`, `list_panels`) and a missing module or name is "unknown panel" (404), never an import crash. `COMMAND_PANELS` maps slash commands to panels; `PANEL_LOCATIONS` holds the "Settings → Tab" label a web command answers with.
- **Secrets**: build fields with `make_field(...)` (a secret keeps `is_set`, never its value; labels and managed paths are not editable). The one secret rule is `kollabor_config.secrets.is_secret_path`. Never log a payload: it can hold secrets and join codes.
- **Add a panel**: `kollabor/panels/<name>.py` with a `Panel` class and `PANELS = {Cls.name: Cls()}`; add the slash command to `COMMAND_PANELS` and a label to `PANEL_LOCATIONS`; keep action bodies to the payload contract in the spec's "deviations" section (row actions declare `payload_key`); add the tab in `packages/kollabor-webui/frontend/src/components/panels/PanelHost.tsx`.
- **Network panels** (`connect`, `connect-*`): one daemon per folder runs the network and the others describe it read-only (`read_only: true`). The engine (`routes/panels.py:_network_owner`) sends a read-only chat's network panel calls and actions to the live session in the same folder that runs it.
- **Web commands**: a web-originated slash command that opens a fullscreen view (`AltViewStackManager.push`, `FullScreenManager.launch_plugin`) records the attempt in the `unavailable_attempts` ContextVar and raises `AltViewUnavailable`; `LocalStateService._run_web_slash_command` answers with one line and the turn ends. Pipe mode and `--detached` refuse the same way.

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
- `models.py` - HubMessage, WorkSlot, AgentState, `desired_bundle_for_identity`
- `rosters.py` - Persona rosters and teams from JSON (`bundles/rosters`, `bundles/teams` -> `~/.kollab/{rosters,teams}` -> `.kollab/{rosters,teams}`): loader, validation, `find_persona`, `roster_personas`, `team_launch_order`; spec `docs/specs/rosters-and-teams.md`
- `presence.py` - Heartbeat files, agent discovery, socket liveness checks
- `coordinator.py` - flock election, work queue, designation assignment
- `xml_tags.py` - Hub XML tag matching: attributes in any order, either quote style (`tag_pattern`, `tag_attrs`)
- `messenger.py` - Unix socket server/client + off-box TCP/TLS endpoint, message delivery
- `dns/` - Agent DNS: discovery, Ed25519 identity, trust, capabilities (AID/ARDP/ANS-aligned)
- `dns/endpoint.py` - Off-box A2A endpoint: TLS listener + federation bootstrap (well-known fetch/import)
- `messaging_bridge.py` - Telegram bridge: long-poll inbox + send. It runs on `plugins.hub.bridge_target_agent` (empty = coordinator), like the email watcher (`_runs_inbox`), so a chat message wakes that agent and its replies go back through its own bridge; a bridge turned on in another agent (`/hub bridge enable`) sends what it receives on with `_route_message`. A reply to a bridge message goes out whole (`_answers_bridge_message`), anything else is mirrored cut to 500 chars (`_bridge_forward`). `tests/tmux/lib/fake_telegram.py` is a local Bot API for specs
- `email_bridge.py` - Email wake: IMAP IDLE (stdlib) on one inbox, read-only with `BODY.PEEK`; mail that arrives after start becomes a direct hub message that wakes `plugins.hub.email_target_agent` (empty = coordinator). That agent runs the watcher itself (`_email_inbox_loop`, `_runs_email_watch`); it shares `_run_bridge_inbox` and `BridgePollLock` with the Telegram bridge. Guide `docs/guides/email-wake-setup.md`
- `network_attach.py` - Open an agent on another device of the network as a full chat: its own socket over the sealed device channel. The device running it decides by its trust, as for messages (`RelayAgentBridge.attach_target`: `open` every member device, `agents` only the agents that device may message, `manual` none); the engine opens `agent@device` session ids through it (`kollabor_engine/remote_attach.py`)
- `vault.py` - Three-tier persistent memory (stream, working, crystallized)
- `crystal_store.py` - Structured crystal entries with IDs, keywords, dedup, nudge retrieval
- `text_utils.py` - Keyword extraction, stemming, relevance scoring for crystal nudge
- `scratchpad.py` - Ephemeral agent notes (survives compaction)
- `nudge_engine.py` - Behavioral nudges (scratchpad reminders, task checkpoints)
- `feed.py` - Dashboard content generator
- `org_launcher.py` - Launch a team (`/hub org`, `--hub org`, `--org`) as detached processes; each gets its seat's instructions in `KOLLAB_LAUNCH_BRIEF`

**Key concepts:**
- Agents auto-discover via presence files in `~/.kollab/hub/presence/`
- Presence carries the running tool (`current_tool`: bare name and type, never its arguments), kept from the display tap's `tool_start`/`tool_result`/`turn_complete` (`HubPlugin._follow_tool`, the tap's `on_event`) and dropped at idle. The engine's `GET /agents` `live` rows pass it on, so every web UI sidebar gem acts out its agent's tool with no chat open; the open chat's gem reads its session's event stream instead. Agents on other computers carry state and tool in their device's relay directory rows (`LocalAgentDirectory.publishable_agents` -> `remote_agents` -> `network_status` -> the engine's network rows). Each device reads its peers every 15 s, and every 3 s a peer whose agent is at work (`RelayAgentBridge._busy_peers`), so a remote gem picks up work up to 15 s late and then follows its tools within ~3 s
- First agent becomes coordinator via `flock()` on `hub.lock`
- Designations come from the folder's roster (`plugins.hub.roster`, default `gems`: lapis, peridot, ruby with color castes); a persona is a name, look, agent bundle, role and prompt, and `kollab --as [roster/]name` takes one from any roster. Teams are reporting chains of personas; the engine serves `GET /rosters`, `GET /teams`, `POST /teams/{name}/launch`
- Open channel: all agents see all messages (like Slack)
- Off-box mesh: `plugins.hub.endpoint_enabled` binds a TCP/TLS listener sharing the
  same `_handle_connection` handler; remote agents complete the same Ed25519 handshake.
  Default OFF (unix socket only). The endpoint always forces auth and refuses to bind
  plaintext without `endpoint_allow_insecure`. Federation: `/hub dns connect <authority>`
  fetches + imports a remote mesh's `/.well-known/agent-keys.json`. See
  `docs/specs/hub-remote-endpoint.md`.
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
- **Rebirth** - On restart, `get_rebirth_context(task_ledger=...)` lists the agent's active TaskLedger cards (id, status, priority, assigner, title; "none" when it has none, "unavailable" if the ledger can't be read) plus the last 15 stream entries, working memory and crystallized memory. Both the startup injection (`plugins/hub/plugin.py`) and the `hub_vault` trender pass the ledger

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
kollab --hub capture <name|agent@device> [N]  # last N interactions; own network only, never a contact (off: plugins.hub.disallow_cross_device_capture)
kollab --hub where <name>        # live socket path, any project on this machine (read-only)
kollab --hub help                # full command list
kollab --attach lapis@devbox     # attach over ssh: `where` on devbox + ssh -L (kollabor/attach_remote.py)
```

**Starting/stopping agents:**
```bash
kollab --agent koordinator --detached   # start detached
kollab --hub stop koordinator                    # stop (socket + SIGTERM fallback)
kollab --hub status                              # verify
kollab service install                           # this folder's agent under systemd/launchd (kollabor/service.py)
```

`kollab service` runs `kollab --detached` with `KOLLAB_SERVICE=1`: cli_main skips the fork so the manager owns the process, pops the flag (agents it spawns still detach) and sets `KOLLAB_SERVICE_PID`. Its hub status then reports `service: true`, `find_workspace_daemon` attaches to it although launchd does not make it a session leader, and the attaching window never owns it (no `KOLLAB_DAEMON_PID`).

**The web UI opens every agent on this computer.** `HubBridge.discover_sessions` lists each live agent with an attach socket (session id = its presence `agent_id`, since gem names repeat across folders). The first session route that names one adopts it (`server.open_named_session` → `EngineSession.adopt` → `DaemonPool.adopt`): a handle with no launcher, so closing it only detaches and the engine never signals a process it did not start. A `--detached` daemon sends a permission prompt to every attached window and, once one answers, publishes `permission_granted`/`permission_denied` (`attach_bridge.resolution_event`) so the others close theirs. A `--no-daemon` session keeps its prompts in its own terminal (`_try_attach_permission_prompt`). Agents on other computers are listed through every agent on this computer that is on a network, one answer per network, since folders can be on different networks: every live daemon, this engine's chats included, is asked at once over its hub socket, read-only (`network_status`), this engine's folder first (`hub_bridge.network_daemons`, used by `routes/sessions.py:_network_snapshot`). Each network's rows land in its own sidebar block (`network_aggregate.py`) with its own month-long roster, and one opens through the first chat or daemon whose network lists it (`remote_attach.py`), so they show and open with no chat open here.

**Hub message flow (4 flows, see docs/specs/hub-message-flow.md):**
1. Human types -> `_broadcast_user_input` -> broadcast to all peers
2. LLM emits `<hub_msg>` -> `_parse_hub_messages` -> route + strip tags
3. Agent receives via socket/mailbox -> display + inject + trigger LLM
4. Event pipeline: PRE -> MAIN -> POST phase data propagation

**Hub message attributes:**
- `<hub_msg to="lapis">msg</hub_msg>` -- send, continue working (default)
- `<hub_msg to="lapis" wait="true">msg</hub_msg>` -- send, then STOP: the sender's turn ends once the send succeeds (the result's `metadata["end_turn"]`, read by `_tool_results_requiring_followup` in `queue_processor.py`, for XML tags and native calls alike). Other tools in the reply still run; a rejected send, a "not online" warning or any failed tool keeps the turn going so the model sees the error
- Auto-wait: a short message (25 words or fewer) with "standing by", "waiting for", "going quiet", "staying quiet" auto-sets wait; a longer working message never does
- `kollab --hub msg <agent>` / `@agent` / `/hub wake` are operator messages: they always wake the target (only a redelivered message id is dropped); a wake that lands while the human is typing or in the ESC cooldown waits instead of being dropped
- The @ menu (TUI and web) leads with `@broadcast` (this project's mesh), `@local-broadcast` (plus every live agent in this computer's other project folders, socket to socket: `_broadcast_other_folders`, `presence.live_agents_on_machine`) and `@global-broadcast` (plus every network agent under open trust), then live agents, then other computers' agents as `agent@device` (`BROADCAST_TARGETS`, `list_agent_targets` in `plugins/hub/plugin.py`). The web @ list keeps direct targets to the chat's folder
- Attributes come in any order and either quote style; tags with two or more attributes are built with `tag_pattern` / read with `tag_attrs` (`plugins/hub/xml_tags.py`), never a fixed-order regex

**LLM response processing (unified pipeline):**
- Single code path in queue_processor.py handles both native and XML tools
- response_parser runs on ALL responses regardless of native tool presence
- Plugin tags (hub_msg, scratchpad, etc) are registered via `register_plugin_tag()` and executed via `register_plugin_handler()` -- real tools, not regex hacks on LLM_RESPONSE_POST
- 43 hub pipeline tags registered (hub_msg, hub_broadcast, hub_stop, hub_status, scratchpad*, state_update, task_*, change feed, agent ops, vault_write, hub_ask_ctx)
- A native call the model writes into its reply as text (`<functions.NAME>{json}</functions.NAME>`, `<tool_call>{"name": ...}</tool_call>`) parses as an MCP call by name; `resolve_text_tool_call` (`kollabor_agent/tool_call_contract.py`) sends it to the built-in or plugin tool it names before scope and permission checks, as `normalize_native_tool_call` does for real native calls. Its result returns in the XML batch, never as a provider tool result (the provider never issued that call id). Beside real native calls such a block only echoes them and never runs (`is_text_written_call`); a `<tool_call>` with no name is a `malformed_tool`, never a tool named "unknown"
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
- Agent loop (standing by forever): each delivery woke the agent again, fix with wait="true"
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
python scripts/build_binary.py   # single-file binary for this machine -> dist/bin/
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

2. **Available actions:** `start_app`, `slash_command`, `type`, `send_keys`, `capture`, `assert_contains`, `assert_not_contains`, `wait_for`, `shell`, `escape`, `enter`, `arrow`, `control`, `sleep`, `section`
   - `send_keys` types **literal text** — use `escape` / `enter` / `arrow` for special keys
   - `slash_command` sends `/`, waits for the palette, types the command — prefer it over `type` + `enter`
   - prefer `wait_for` over `sleep` before an assertion, and pass `--no-daemon` in `config.command` so the spec can't attach to a developer's live daemon

3. **Run:** `tests/tmux/lib/test_runner.sh tests/tmux/specs/your-test.json`

**Never let a test touch the OS keyring.** On macOS every read/store of a
missing Keychain entry pops a system dialog, so a spec that boots the app with
an `api_key` in its config prompts on every run. The harness exports
`KOLLAB_NO_KEYRING=1` for the app and every `shell` step, and
`keyring_enabled()` (`kollabor_ai.providers.security`) is off automatically
under pytest. Any new harness must do the same.

**Never let a test touch the real `~/.kollab`.** The harness runs the app command
and every `shell` step with `HOME` set to a throwaway directory (seeded with
`application.setup_completed` and the connect-guide marker), so a spec cannot
attach to a live daemon, join the developer's hub, wake running agents or write
to their conversations and vaults. A spec without its own `HOME` also gets its
own `KOLLAB_PROJECT_ROOT`, because hub sockets live in
`/tmp/kollabor-hub/<project hash>/`, outside `HOME`. When the spec ends the
harness stops every process whose environment carries that `HOME` and removes
the directory. A spec that sets `HOME` in its own command keeps it. Opt-outs in
`config`: `"real_home": true` (loud warning; only for a spec that needs the
developer's real providers) and `"first_run": true` (unseeded `HOME`, for the
first-run wizard). Any new harness must isolate `HOME` the same way.

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

**Dot notation:** `config.get("kollabor.llm.enable_streaming", False)`

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

**Agent resolution:** Local -> Global -> Bundled (`AgentManager.find_agent_dir`, the one resolver: the engine's `POST /sessions` uses it too, with the chat's folder as the local tier (`AgentManager(project_path=...)`); app start also seeds the bundled agents into the global dir)

**Config save targets (`/config` modal, `Ctrl+S`):**
- Local (`L`): Saves to `.kollab/config.json` in cwd (project-specific overrides)
- Global (`G`): Saves to `~/.kollab/config.json` (user-wide settings)
- Only overrides (diff from defaults) are persisted, not the full config tree
- **Reload:** `ConfigService` polls the config files' mtimes once a second on the event loop (no watchdog) and reloads, notifying callbacks, when another process saved. Its own writes refresh the stored mtimes, so a save is not reloaded twice.

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

**Priority order:** CLI `--system-prompt` arg -> `KOLLAB_SYSTEM_PROMPT` env var -> `KOLLAB_SYSTEM_PROMPT_FILE` env var -> Local `.kollab/agents/default/system_prompt.md` -> Global `~/.kollab/agents/default/system_prompt.md` -> Bundled `bundles/agents/default/system_prompt.md` -> Built-in fallback

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

Supported widget types: `checkbox`, `slider`, `dropdown`, `text_input`, `spinbox`. Each widget requires `type`, `label`, `config_path`, and `help`. Additional keys depend on the widget type (e.g., `min_value`/`max_value`/`step` for slider, `options` for dropdown, plus an optional `option_labels` mapping each option to the text people read when the options are ids; the option is still what is saved).

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
- One shared checkout on `main` for every agent (see SOFTWARE_DEVELOPMENT_LIFECYCLE.md):
  never create a git worktree (unless Marco asks for one), and never stash, reset,
  clean, restore or switch branches in it
- Other agents edit the same tree at once: leave their uncommitted edits alone and
  commit only your own files, `git commit -- <paths>` (never `git add -A` or `git add .`)
- Commit: Conventional Commits format `type(scope): summary`, referencing the issue:
  `fixes #123`, `closes #123`, `resolves #123`, or `#123`
- Main branch protected - no direct pushes. Push `HEAD` to a PR branch named
  `issue-123-description`, with no local branch; check `git log origin/main..HEAD`
  first, since every commit it lists ships
- Agents ALWAYS open PRs as drafts: `gh pr create --draft`. Only Marco or the
  koordinator marks a PR ready or approves it; auto-merge then merges it once checks pass.
- PRs merge themselves (`.github/workflows/auto-merge.yml`): a PR from a branch of this
  repo, opened by a person with write access, merges with a merge commit once the three
  required checks pass. Forks, bots and drafts never do: open a draft to hold one, and
  `gh pr merge <n> --disable-auto` stops one already armed. Such a merge starts no
  push-to-main CI run.
- Merging needs the PR branch up to date with `main`: `gh pr update-branch <n>` (a
  merge, never a rebase). Once it merges, `git pull --ff-only origin main`; if that
  refuses, `main` has diverged: stop and tell Marco, never reset, rebase or force-push

```bash
gh issue create --title "Fix version display"
git commit -m "fix(cli): version display, fixes #8" -- kollabor/cli.py
git log --oneline origin/main..HEAD
git push origin HEAD:refs/heads/issue-8-version-display-fix
gh pr create --draft --head issue-8-version-display-fix --title "fix(cli): version display" --body "Fixes #8"
git pull --ff-only origin main   # once the PR has merged
```

### Version Management
Version managed centrally in `pyproject.toml`:
- **Development mode** (`python main.py`): Reads from `pyproject.toml` at runtime
- **Production mode** (pip install): Uses `importlib.metadata.version("kollab")`

Files reading version: `kollabor/application.py`, `kollabor/cli.py`, `kollabor_config.loader`

To update the semantic version: only modify `pyproject.toml`.

Build provenance is separate from the release version. Python wheel/binary
builds embed the exact source branch and full HEAD SHA in `kollabor._build_info`;
Vite embeds the same identity in the WebUI and signed PhoneLink bundle. The CLI
prints `Build: <branch> @ <8-character SHA>` before its final `kollab X.Y.Z`
line. A detached checkout reports branch `unknown` while retaining its SHA
when HEAD is resolvable; unavailable fields are `unknown`. Installed artifacts
do not need runtime Git access.

### Cutting a Release

`main` is branch-protected: **no direct pushes**, every change lands via PR + 3
required CI checks (`ci/security-scan`, `ci/standards-check`, `ci/tests`).

**Correct order: prep PR BEFORE the tag** (source must already match the tag; do
not rely on CI to repair it):

1. Prep commit on local `main` (`git commit -- <paths>`; never a worktree, branch
   switch, scratch clone or uncommitted files) bumping *everything* to `X.Y.Z`:
   - root `pyproject.toml` + all 10 `packages/*/pyproject.toml` versions
   - inter-package `kollabor-*>=` constraints, then `uv lock`
   - move `[Unreleased]` → `## [X.Y.Z] - DATE` in **both** `CHANGELOG.md` and
     `kollabor/updates/CHANGELOG.md` (keep them **byte-identical**, `cmp` them;
     the in-app update UI reads the packaged copy)
2. `git push origin "HEAD:refs/heads/release/X.Y.Z"` and open the PR; it merges itself
   with a merge commit once the checks pass (never squash). Then
   `git pull --ff-only origin main`.
3. Annotated tag on the merged commit: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`,
   then `git push origin vX.Y.Z` (push the exact tag, never `--tags`).
   Releases are tagged whenever Marco decides, and agents may create a release
   tag ONLY when Marco explicitly instructs them to. Desktop Mac/Linux builds
   run only from the release tag; they are never PR checks or merge
   requirements.
4. Once publishing is green: `uv sync --all-extras` and
   `KOLLAB_VERSION=X.Y.Z bash install.sh` (the release binary in
   `~/.local/bin`). Done = `main` matches
   `origin/main`, `git status` lists none of the release's files, and
   `kollab --version` reports `X.Y.Z` inside and outside the repo.

The tag triggers `.github/workflows/publish.yml` → the four platform binaries
(`binaries.yml`, built and checked before anything publishes) → PyPI + Homebrew
tap → GitHub Release with the binaries attached. Its first step, **Verify release consistency**, fails the publish
if the tagged commit's versions/changelog don't already agree with the tag, so
a tag cut without a prep PR fails fast instead of publishing silently.

Canonical checklist: `docs/release-process.md`. Full agent playbook (not
invocable from Claude Code; read + follow by hand): `bundles/skills/release-manager/SKILL.md`.

### Release Binaries

Every release also ships `kollab` as one executable per platform
(`kollab-macos-aarch64`, `kollab-macos-x86_64`, `kollab-linux-x86_64`,
`kollab-linux-aarch64`, each with a `.sha256`): a pex scie holding a portable
CPython 3.14 and every wheel, so it runs with no Python installed.
`scripts/build_binary.py` builds and smoke-tests it for the machine it runs on,
with wheels picked for macOS 11 and glibc 2.28, never for the build host;
`.github/workflows/binaries.yml` runs it on each platform only for a `v*` tag
(publish.yml calls it) or a manual run; PRs and branch pushes build no packages.
`install.sh` installs the binary first, and in a binary
`/upgrade` swaps in the latest release's binary after checking its SHA-256 and
that it starts (`kollabor/updates/auto_update.py:_upgrade_binary`).

**Code that starts kollab again** (re-exec, service units, spawned daemons) uses
`kollabor.binary.kollab_argv()`, never `sys.executable -m kollabor_cli_main`:
inside a binary `sys.executable` is its unpacked venv, so an upgraded binary
would keep restarting the old code. `tests/tmux/specs/binary_chat.json` builds
the binary from the checkout and chats through it.

With `kollabor.updates.auto_update_enabled`, an interactive launch installs a
newer release before it starts anything, then execs itself as the new version
(`kollabor/cli.py:_update_before_launch`; `KOLLAB_UPDATED_TO` stops a second
check). In-app, only the launched window installs: a `--detached` daemon just
reports the release, so the two never install it at once.

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
- A Stop (ESC, the web UI's Stop, a voice talk-over) ends the turn and parks its open prompts (`PermissionManager.park_confirmations`): the tool comes back "Not run yet" and the card stays open. A card unanswered for 290 s parks the same way and the agent writes that it needs the OK. Allow on a parked card runs that call once and the agent goes on (HUD note + `TRIGGER_LLM_CONTINUE`); Deny drops it quietly; the user's typed yes runs it and closes the card. Terminals close a parked prompt without answering (`permission_parked`). ACP's Stop answers its prompts cancelled (`cancel_confirmations`)
- A voice turn never waits on a prompt: a call that needs the user's OK (the voice guard's `confirmation_required`: a command on the voice list that is not read-only, or read-only with its tool outside `/bin` and `/usr/bin`; or a Confirm-mode prompt) returns "Not run yet" and the agent asks in its reply. The user's own next message decides (`kollabor_agent/yes_approval.py`, fed by `LLMService.process_user_input`, only for a person answering live: its `may_answer` is closed by default and `MessageHandler` opens it only for `kollabor.user_input_source.YES_SOURCES`, which are the TUI, an attached window, state RPC and the `/voice` plugin; a hub peer's "agent" source, `kollab --hub msg` (to an ACP agent it goes in as `hub_message`), the bridges and a `/agent` or `/skill` generation prompt never answer): a short yes runs each call the agent's last finished reply asked for once, a short no drops them, and anything else drops them too. Asks open when the reply's chain ends (`QueueProcessor.is_processing` falling); a yes sent before then, or said over the agent (the worker's `talk_over`, `voice_talk_over` in the context; a talk-over cut calls `YesApprovals.reply_cut`), answers nothing, and a voice ask stops counting after 5 min. The greeting asks for nothing. Asks come only from the guard's or the manager's decision (`PermissionDecision.needs_yes`), never a tool's text. A yes runs the guard's plan again, approved (`evaluate(approved=True)`): argv with no shell, the tool found on PATH but never in the project or a directory others can write, and the hard blocks still refuse it (interpreters and wrappers like `bash -c`/`env`, git config options, git sent outside the project with `-C`/`--git-dir`/`--work-tree`, git subcommands outside `_VOICE_GIT_COMMANDS`, a `<helper>::` remote on a subcommand that takes one). A yes runs only the voice list (`_voice_may_run`: read-only commands, git, `mkdir`/`touch`/`cp`/`mv`/`rm` inside the project and outside `.git` with a few options, the project's own `make` targets and `npm` run/test/start/install/ci with no extra argument, read-only `gh` and `kollab --hub`); anything else comes back `not_by_voice` before it is asked for, and the agent offers to run it from a typed message (no `npm exec`, `ex`, `sqlite3`, `curl`...). A tool other than the terminal and the hub tools (`file_create`, `file_edit`, MCP...) comes back `command_blocked` with that same list (`_VOICE_CAN`), so a model that reaches for `file_create` makes the file with the terminal's `touch` and asks
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
- `/setup` (aliases: `/onboard`, `/wizard`) - Guided fullscreen wizard to configure a new LLM provider (pick provider → API key → endpoint → model → optional live test → save + activate). New-user entry point; ChatGPT OAuth delegates to `/login`, Azure/advanced is configured manually in `config.json` (see `docs/providers.md`).
- `/save` - Save conversation (transcript|markdown|jsonl|clipboard|both|local)
- `/model` (aliases: `/mod`, `/m`) - Fullscreen model picker for the active provider (list|search|set|effort). The list is seeded from `bundles/data/models.json` (retired entries filtered) and merged with the provider's live catalog when one exists. `/model effort [level]` shows or sets reasoning effort — see `docs/features/reasoning-effort.md`.
- `/llm` (aliases: `/loadout`, `/ld`) - Fullscreen loadout picker: provider + model + param presets. Every `models.json` model for a configured provider is an implicit loadout; `/llm <name>` activates one directly, `/llm new` opens a pre-filled create form, and `kollab --llm <loadout>` resolves loadouts at launch, with `--model` / `--effort` overriding individual fields on top — see `docs/features/loadouts.md`.

The `/llm` list merges `models.json` with the provider's **live** catalog (`model_catalog.py`, TTL + versioned disk cache). A provider contributing zero rows still renders a section explaining why — never silently omit it, that was the original bug.
- `/permissions` (aliases: `/perms`, `/security`) - Manage permissions (show|default|strict|trust|stats|clear)
- `/terminal` (aliases: `/tmux`, `/term`, `/t`) - Manage tmux sessions (new|view|list|kill)
- `/hub` (aliases: `/mesh`) - Agent hub (status|msg|broadcast|feed|console|org|vault|whoami)
- `/login` - OAuth login for providers (currently OpenAI)
- `/mcp` - Open the MCP manager; subcommands are `setup`, `show`, `status`, `list`, `servers`, `tools [server]`, `test <server>`, `enable <server>`, `disable <server>`, `reload`, and the Apps verbs `browse`, `search <words>`, `add <id>`, `remove <id>` (`kollabor/commands/mcp_apps.py`; there is no `/apps`). The manager screen lists the Apps catalog's MCP servers beside the example config, and `a` adds one through `AppsService`. Aliases: `/mcps`, `/servers`.
- `/resume` - Resume a previous conversation
- `/goal` (aliases: `/goals`) - Durable session goal (create|show|pause|resume|clear|history) with bounded continuation. Daemon-owned state in SQLite (`kollabor/state/goal_store.py`), policy in `kollabor/goals/service.py`, execution on the existing queue path (`kollabor/goals/driver.py`), one `goal_report` tool via the unified pipeline (`kollabor/goals/tooling.py`). Control subcommands work during a processing turn. See `docs/features/goal-command.md` and `docs/specs/goal-command-harnesses.md`.
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

**The codebase uses Python 3.14+ with async/await throughout.**

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
