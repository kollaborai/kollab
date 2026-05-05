# kollab one-pager

terminal-based LLM chat where **everything has hooks**. monorepo, python 3.12+,
async/await throughout. the tagline isn't marketing — every user action, every
LLM request, every tool call, every render frame fires an event that plugins can
intercept, transform, or block.

## the core idea

a single `EventBus` sits at the center. every other subsystem is a producer or
consumer of events on that bus. plugins register hooks at priority levels
(SYSTEM → SECURITY → PREPROCESSING → LLM → POSTPROCESSING → DISPLAY) and can
modify the context object that flows through. this is how features like the tool
permission system, the hub mesh, hook monitoring, and agent orchestration all
layer on the same base app without touching core code.

## startup flow

`kollab` → `kollabor_cli_main:cli_main` → `kollabor/cli.py` parses args (plugins
get to register CLI flags during discovery) → `kollabor/application.py`
instantiates `TerminalLLMChat`. the render loop starts **immediately** so the
input bar appears in <100ms, while `_deferred_startup()` loads the LLM service,
initializes plugins, checks for updates in the background. user input is gated
by `_startup_ready` (asyncio.Event) until core services are ready.

## the packages

```
kollabor/                 ~24k  orchestration: application, cli, commands, llm/
packages/kollabor-ai/     ~7k   providers, profiles, OAuth, streaming, pricing
packages/kollabor-agent/  ~7k   tools, MCP, permissions, queue processor
packages/kollabor-tui/    ~42k  rendering, input, design system, widgets, altview
packages/kollabor-events/ ~2k   event bus, hooks, registry, executor, processor
packages/kollabor-config/ ~2k   config loader, dot-notation, local/global merge
packages/kollabor-plugins/ ~3k  plugin SDK, discovery, registry, factory
packages/kollabor-engine/ ~5k   daemon server + RPC (attach mode)
packages/kollabor-rpc/    ~2k   RPC protocol for daemon transparency
packages/kollabor-webui/  ~3k   web UI (separate product, same backend)
```

## request lifecycle (user types → reply on screen)

```
keypress
  → input_handler (raw mode, 100Hz poll)
  → USER_INPUT_PRE  (plugins preprocess)
  → USER_INPUT      (add to conversation_history)
  → LLM_REQUEST_PRE (system prompt render, context injection)
  → api_communication_service  (rate-limited HTTP, retries)
  → LLM_RESPONSE    (streaming tokens → StreamingHandler)
  → response_parser (detects native tool calls + XML plugin tags)
  → TOOL_CALL_PRE   (permission hook at SECURITY priority)
  → tool_executor   (native tools OR plugin handlers — same pipeline)
  → TOOL_CALL_POST  (result injected back into conversation)
  → LLM_RESPONSE_POST → message_coordinator displays
```

## the three critical subsystems

**MessageCoordinator** (`packages/kollabor-tui/.../message_coordinator.py`) owns
render state. the `writing_messages` flag pauses the render loop during message
display and modal entry. *never* touch render state directly — always go
through the coordinator. modals use `enter_alternate_buffer()` /
`exit_alternate_buffer(restore_state=True)` to pause/resume cleanly.

**LLMCoordinator** (`kollabor/llm/llm_coordinator.py`) replaced the old
llm_service.py. it orchestrates the QueueProcessor (handles question gate
suspension), StreamingHandler, MessageHandler, and the permission hook system.
the question gate protocol suspends pending tools when the agent emits
`<question>` tags so it can ask before acting.

**Unified tool pipeline**: native tool calls (from the provider API) and XML
plugin tags (like `<hub_msg>`, `<scratchpad>`, `<vault_write>`) share the same
code path. plugins register via `response_parser.register_plugin_tag()` +
`tool_executor.register_plugin_handler()`. handlers return a `ToolExecutionResult`.
this replaced 33+ regex hacks on LLM_RESPONSE_POST with real first-class tools.

## configuration

dot-notation: `config.get("kollabor.llm.max_history", 90)`.

- global: `~/.kollab/config.json` (user-wide)
- local: `.kollab/config.json` (project override, merged on top)
- only diffs from defaults are persisted
- `/config` modal: Ctrl+S prompts local (L) vs global (G)
- path encoding: `/home/user/proj` → `home_user_proj` for per-project data

runtime data lives under `~/.kollab/projects/<encoded-path>/`:
conversations (JSONL), raw API logs, memory cache, application logs.

## plugins

discovered from `plugins/` at startup. each inherits `BasePlugin`, registers
hooks in `register_hooks()`, optionally exposes config widgets via
`get_config_widgets()` for the `/config` modal. 18+ built-in plugins:

- **hub** — peer-to-peer agent mesh with presence, sockets, persistent vaults
  (three-tier: stream.jsonl, working_memory.md, crystallized.md)
- **agent_orchestrator** — `/sub` multi-agent task delegation
- **mcp_plugin** — Model Context Protocol server integration
- **terminal_plugin** — tmux session management (`/terminal`)
- **altview** — fullscreen views (hub feed, console, tmux viewer)
- **modern_input, fullscreen, resume, save, context_compaction** — UX polish
- **hook_monitoring, example_context** — dev/observability

config hooks (Claude Code-compatible JSON in `.kollab/hooks.json`) let you
register shell commands as hooks without writing python.

## modes

- **interactive**: full TUI, render loop, status widgets in areas A/B/C
- **pipe**: `kollab "query"` or `echo q | kollab -p` — suppresses UI, plugins
  still run (via `app.pipe_mode` flag)
- **daemon + attach** (phase 4.5): `kollab --detached` forks a daemon, then
  `kollab --attach <name>` boots a TUI proxy over RPC. `StateService` is the
  abstraction — `LocalStateService` in-process, `RemoteStateService` over RPC.
  `ContextRegistry` enables multi-context daemons with snapshot-and-swap

## agents, skills, system prompts

- agents live in `bundles/agents/` (global) or `.kollab/agents/` (local,
  wins). all inherit from `bundles/agents/_base/` via shared sections
- `<trender>` tags in system prompts render at runtime: project tree, file
  contents, timestamps, hub identity, hub roster, vault contents
- priority: `KOLLAB_SYSTEM_PROMPT` env → `_FILE` env → local default →
  global default → built-in fallback

## key stdout rule

plugins **never** call `print()` or `sys.stdout.write()`. the only legal output
path is `renderer.message_coordinator.display_message_sequence([...])`. rogue
writes corrupt the render state (duplicate input boxes, garbled status bar).
logging goes through `logging.getLogger(__name__)` to files, not stdout.

## testing

primary approach: JSON-based tmux tests in `tests/tmux/specs/`. a test spec
describes actions (start_app, slash_command, type, assert_contains) and the
runner drives a real tmux session with a real app. unit tests in `tests/unit/`,
run with `python -m pytest tests/unit/ -x -q`.

## where to look next

- `docs/architecture/` — overview, terminal rendering, event system
- `docs/features/` — every user-facing feature has a spec
- `docs/specs/` — hub message flow, unified tool pipeline, crystal memory
- `CLAUDE.md` — the rules (render state, stdout, hub bugs, commit style)
