# Components, panels and project layout

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

## Key Components

### LLM AI Package (`kollabor_ai`)
- `api_communication_service.py` - API communication with rate limiting
- `conversation_logger.py` - Conversation persistence (KollaborConversationLogger)
- `conversation_manager.py` - Conversation state and history
- `model_registry.py` - Reads `bundles/data/models.json`: context windows, `supports_sampling`, per-provider model lists. Lookup is **longest-prefix match** on the model string, so every point release whose specs differ from its family needs its own entry (`grok-4.5` vs `grok-4`). `supports_sampling: false` means the model 400s on temperature/top_p/top_k and every provider omits them. `thinking_default: "adaptive"` marks Claude models that think by default but hide the text; the anthropic provider asks them for `display: "summarized"` (`providers/tuning.py:thinking_params`). Freshness gated by `scripts/validate_models.py`.
- `model_catalog.py` - Live "what models does this provider offer?" for the `/model` picker (codex backend, OpenRouter, Anthropic, any OpenAI-compatible `{base_url}/models`). Never raises; empty list on failure.
- `pricing_registry.py` - Cost rates. Seeded from `models.json` (authoritative) on top of `default_pricing.json`, then `~/.kollab/pricing.json`. Falls back across providers for the same model id, since rates belong to the model not the transport.
- `usage_ledger.py` - Per-agent, per-lane token and cost ledger (JSONL, owner-only, local). Unknown prices are "unpriced", never 0; missing provider usage is null, never 0. Fed by `plugins/usage_ledger_plugin.py` from `LLM_REQUEST_POST`. Doc `docs/features/usage-ledger.md`
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
- `voice_aside.py` - Words said while a turn runs get a Quick Model answer at once, on its own provider; the turn keeps going, and a spoken stop ends it (#388)
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

### Engine routes and folders

An engine route that takes a folder (`workspace`, `folder`, the picker's `path`) calls `folder_or_400` from `packages/kollabor-engine/src/kollabor_engine/workspace_folder.py` first and uses what it returns, never the request's own text: one rule (the folder exists, is a directory, and is inside home, the engine's folder, a folder an agent runs in or a recent web session folder), one place for CodeQL's `py/path-injection` sanitizer. Spec `docs/specs/engine-folder-rule.md`; new routes join `FOLDER_ROUTES` in `tests/test_workspace_folder_routes.py`.

The authenticated Code routes are an exception for folders on a session's daemon, which may be on another computer: the engine proxies the selection and never opens that path locally. The daemon resolves and validates the selected absolute directory on its own machine before serving files or Git (`docs/specs/floating-code-shell.md`).

### Web UI Learn

The Learn dialog uses one renderer (`frontend/src/components/learn/`) and the typed catalog in `frontend/src/learn/`, under `packages/kollabor-webui/`. Each topic's JSON defines workflows, exact entry points, steps, command examples, expected results, recovery, and product media. Keep topic IDs stable and change the shared renderer for repeated presentation changes. See [Learn maintenance](../../features/learn.md) for the content contract and recording process.

Desktop images and optional recordings live in `frontend/public/learn/`. The phone-app build inlines only referenced image previews through `frontend/scripts/learn-phone-media-plugin.ts`; it uses those previews and written steps without requesting unsigned media or changing the portal CSP. Validate the catalog with `frontend/tests/learn-catalog.test.ts` and the actual browser surface with `tests/tmux/specs/learn_web_workflows.json` (requires the frontend dependencies and a Playwright-compatible Chromium executable).

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
