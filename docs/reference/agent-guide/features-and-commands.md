# Features, permissions and slash commands

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

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
- Sandbox gate (`kollabor_agent/sandbox_gate.py`, `sandbox_gate.*`, default off): `ToolExecutor` refuses file writes and terminal commands that touch a protected root unless `sandbox-promote` validated a clean scratch checkout and issued a one-use token (root + HEAD SHA + 15 min); owner override is env-only (`KOLLAB_SANDBOX_GATE_OVERRIDE`); audit log JSONL. Doc `docs/features/sandbox-gate.md`
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

The `/llm` list is the provider's **live** catalog once it is cached (`model_catalog.py`, TTL + versioned disk cache), plus the profile's own model; `models.json` seeds it only while the catalog was never fetched or the provider has no listing API (#558). The list reads the cache with no TTL (an expired entry is still the last list the provider served; the pickers refresh on open), and the Anthropic fetcher reads every `/v1/models` page, returning empty rather than a partial list. Each cache entry records the provider and a fingerprint of the endpoint it came from (never the endpoint, which can hold credentials) and is ignored for a profile now on another provider or endpoint. A provider contributing zero rows still renders a section explaining why — never silently omit it, that was the original bug.
- `/permissions` (aliases: `/perms`, `/security`) - Manage permissions (show|default|strict|trust|stats|clear)
- `/terminal` (aliases: `/tmux`, `/term`, `/t`) - Manage tmux sessions (new|view|list|kill)
- `/hub` (aliases: `/mesh`) - Agent hub (status|msg|broadcast|feed|console|org|vault|whoami)
- `/login` - OAuth login for providers (currently OpenAI)
- `/mcp` - Open the MCP manager; subcommands are `setup`, `show`, `status`, `list`, `servers`, `tools [server]`, `test <server>`, `enable <server>`, `disable <server>`, `reload`, and the Apps verbs `browse`, `search <words>`, `add <id>`, `remove <id>` (`kollabor/commands/mcp_apps.py`; there is no `/apps`). The manager screen lists the Apps catalog's MCP servers beside the example config, and `a` adds one through `AppsService`. Aliases: `/mcps`, `/servers`.
- `/resume` - Resume a previous conversation
- `/new` (alias: `/clear`) - Clear the conversation and start fresh
- `/restart` - Restart this detached agent in place on the installed Kollab version (same folder, identity, flags and conversation; waits for the running turn to end). Web UI: a chat whose agent runs an older version shows a Restart Agent button (`kollabor.updates.restart_agents`) or this command's name. A terminal window attached to the agent stays open and attaches again by itself (`TerminalLLMChat._attach_again`; the daemon announces `daemon_restarting` first and the engine drops it). See `docs/features/agents.md` "Restart After Upgrade"
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
