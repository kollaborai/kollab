# AGENTS.md

This file gives coding agents a fast, practical map of the Kollab repo.
Use it as an execution guide before making changes.

## project snapshot

- name: `kollab`
- version: `1.0.1`
- python: `>=3.12`
- type: terminal AI chat + plugin system + agent runtime
- architecture: monorepo with extracted workspace packages under `packages/*`
- main entrypoint: `kollab` -> `kollabor_cli_main:cli_main`
- dev entrypoint: `python main.py`

Core repo positioning from `README.md`:
- "A terminal AI chat where everything has hooks"
- supports plugins, slash commands, MCP, pipe mode, provider profiles, and agents

## before you change anything

1. Inspect current repo state first.
2. Prefer minimal, pattern-matching changes.
3. Follow existing package boundaries.
4. Use existing hooks, widgets, commands, and managers before inventing new ones.
5. Run targeted validation after edits.
6. Do not overwrite unrelated user changes.
7. Be careful with secrets in `.env` and untracked credential files.

## repo layout

Top-level structure:

```text
kollabor/                  Core orchestration layer
packages/                  Workspace packages
plugins/                   Plugin implementations
bundles/agents/            Bundled agent definitions (system prompts + metadata)
bundles/skills/            Bundled Agent Skills (SKILL.md directories)
tests/                     Test suite
main.py                    Dev entrypoint
kollabor_cli_main.py       Installed CLI entrypoint
README.md                  Product + usage overview
CLAUDE.md                  Deep architecture + implementation guidance
```

Important packages from `README.md`, `pyproject.toml`, and docs:

- `packages/kollabor-ai`
  - provider integrations, profiles, OAuth, prompt rendering, conversation state
- `packages/kollabor-agent`
  - tool execution, file operations, shell execution, MCP, agent loading
- `packages/kollabor-tui`
  - terminal rendering, input, widgets, fullscreen UI, status layout
- `packages/kollabor-events`
  - event bus, hook registry/executor/processor, event models
- `packages/kollabor-config`
  - configuration loading and utilities
- `packages/kollabor-plugins`
  - plugin discovery, registry, factory, SDK, base class
- `packages/kollabor-engine`
  - engine/backend package
- `packages/kollabor-webui`
  - web UI package

Thin orchestration layer:
- `kollabor/` wires packages together
- `kollabor/application.py` is the main app orchestrator
- `kollabor/commands/` contains slash command plumbing
- `kollabor/llm/` contains LLM orchestration and permission integration

## agent system

Agent definitions are documented and implemented in:
- `docs/features/agents.md`
- `packages/kollabor-agent/src/kollabor_agent/agent_manager.py`
- bundled defaults under `bundles/agents/`

Resolution order:
1. local project agents: `.kollab/agents/`
2. global user agents: `~/.kollab/agents/`
3. bundled defaults: `bundles/agents/`

Agent directory shape:

```text
.kollab/agents/<agent-name>/
├── system_prompt.md
├── agent.json            optional metadata (declare skill names via "skills")
└── sections/             optional fragments included via <trender>

# Agent Skills library (same contract everywhere):
bundles/skills/<skill-name>/SKILL.md
~/.kollab/skills/<skill-name>/SKILL.md
.kollab/skills/<skill-name>/SKILL.md
```

Skills follow the [Agent Skills](https://agentskills.io/specification) layout: one directory per skill, required `SKILL.md` with YAML frontmatter (`name` must equal the folder name; `description` required; optional `scripts/`, `references/`, `assets/`).

What `AgentManager` expects:
- required: `system_prompt.md`
- optional: `agent.json` with fields like:
  - `description`
  - `profile`
  - `skills` (list of skill names from the library, or `["*"]` for all)
  - `default_skills`
- `sections/` and other Markdown includes are fragments for the base prompt (`<trender type="include" ... />`), not the skill library
- prompts are rendered through `PromptRenderer`, so `<trender ...>` tags may be used

Bundled agents present in this repo:
- `coder`
- `creative-writer`
- `data-analyst`
- `default`
- `kollabor`
- `native`
- `research`
- `tech-dude`
- `technical-writer`

If asked to add or update an agent:
- prefer editing `bundles/agents/<name>/`
- keep `agent.json`, `system_prompt.md`, and referenced Agent Skills (`agent.json` skill names) consistent
- preserve existing tone and section-based prompt structure

## plugin system

Primary references:
- `packages/kollabor-plugins/README.md`
- `packages/kollabor-plugins/src/kollabor_plugins/base.py`
- examples in `plugins/`
- extra prompt guidance in `bundles/agents/kollabor/sections/17-plugin-development.md`

Discovery and lifecycle:
1. discover plugin classes
2. instantiate with dependencies
3. call `initialize()`
4. call `register_hooks()`
5. call `shutdown()` on exit

Discovery order from plugin package docs:
1. `./plugins/`
2. installed package plugin locations

Base plugin API from `BasePlugin`:
- `register_cli_args(parser)`
- `handle_early_args(args)`
- `async initialize(args=None, **kwargs)`
- `async register_hooks()`
- `async shutdown()`
- `get_default_config()`
- `get_startup_info(config)`
- `get_config_widgets()`

Existing plugin examples worth copying:
- `plugins/agent_orchestrator/plugin.py`
- `plugins/context_compaction_plugin.py`
- `plugins/example_context_plugin.py`
- `plugins/hook_monitoring_plugin.py`
- `plugins/terminal_plugin.py`
- `plugins/save_conversation_plugin.py`

Implementation guidance:
- inherit from `kollabor_plugins.BasePlugin` when possible
- register hooks on the event bus, do not hardwire behavior into unrelated layers
- use command registry for slash commands
- prefer short status line/status lines output
- keep plugin state isolated and clean up tasks/resources in `shutdown()`

## event and hook model

Kollab is event-driven.
Useful references:
- `README.md`
- `docs/architecture/architecture-overview.md`
- `CLAUDE.md`

Hook pipeline described in the README:

```text
user input -> pre_user_input -> pre_api_request -> [LLM API]
           -> post_api_response -> pre_message_display -> output
                                    |
                                    -> pre_tool_use -> [tool execution] -> post_tool_use
```

Hook priorities from architecture docs:
- SYSTEM: 1000
- SECURITY: 900
- PREPROCESSING: 500
- LLM: 100
- POSTPROCESSING: 50
- DISPLAY: 10

General rule:
- use the event bus and existing hook points first
- avoid bypassing the pipeline unless absolutely necessary

## terminal ui rules

This repo has strict TUI patterns. Read `CLAUDE.md` before changing UI code.

Critical rules from `CLAUDE.md`:
- do not directly manipulate `terminal_renderer` render-state internals
- use `MessageDisplayCoordinator` for message flow and alternate-buffer transitions
- fullscreen/modal features must use the coordinator enter/exit pattern
- use the design system in `kollabor_tui.design_system`
- use terminal size helpers from `kollabor_tui.terminal_state`
- do not use `shutil.get_terminal_size()` or `os.get_terminal_size()` directly

If touching fullscreen/status/input rendering, inspect existing code first in:
- `packages/kollabor-tui/src/kollabor_tui/`
- `packages/kollabor-tui/src/kollabor_tui/status/`
- `packages/kollabor-tui/src/kollabor_tui/fullscreen/`

## command system

Slash commands live under `kollabor/commands/`.
Important pieces called out in docs:
- `registry.py`
- `executor.py`
- `parser.py`
- system handlers under `kollabor/commands/system_commands/handlers/`

Examples of existing commands include:
- `/profile`
- `/save`
- `/terminal`
- `/permissions`
- `/login`
- `/mcp`
- `/resume`
- `/agent`
- `/skill`

When adding commands:
- register through the command registry
- follow existing `CommandDefinition` usage
- mirror current handler layout rather than creating ad hoc command wiring

## development commands

Common commands from repo docs:

```bash
pip install -e ".[dev]"
python main.py
python tests/run_tests.py
python -m black kollabor/ plugins/ tests/ main.py
python -m ruff check kollabor/ packages/ plugins/
python -m mypy kollabor/ plugins/
```

Useful targeted checks:

```bash
python -m pytest tests/
python -m pytest tests/test_specific.py
python -m pytest -k "keyword"
python -m py_compile path/to/file.py
```

## investigation workflow for agents

Recommended sequence for any non-trivial task:

1. inspect git state
   - `git status --short`
   - `git diff -- <relevant files>`
2. locate the implementation
   - search with `rg`
   - map related files with `fd`
3. read the exact files you will touch
4. identify an existing pattern nearby
5. make the smallest coherent edit
6. run targeted validation
7. summarize what changed and any remaining risk

For this repo specifically:
- check whether a change belongs in `kollabor/` or one of the extracted packages
- if logic already exists in a package, extend the package instead of duplicating in core
- if behavior is hookable, consider a plugin/hook implementation before adding branching logic

## file placement rules

Choose the right layer:

- put app wiring and startup orchestration in `kollabor/`
- put reusable agent runtime logic in `packages/kollabor-agent/`
- put provider/profile/prompt logic in `packages/kollabor-ai/`
- put rendering, widgets, fullscreen, and status UI in `packages/kollabor-tui/`
- put event bus or hook abstractions in `packages/kollabor-events/`
- put plugin framework code in `packages/kollabor-plugins/`
- put concrete plugin features in `plugins/`
- put agent prompt definitions in `bundles/agents/`
- put end-user documentation in `docs/` or top-level markdown files

## testing expectations

After changes, prefer the smallest useful validation set.

Examples:
- agent prompt/doc changes:
  - verify paths/files exist and content is internally consistent
- command changes:
  - run focused tests for command handlers if present
- plugin changes:
  - run related integration or plugin tests
- prompt/agent-manager changes:
  - run agent-related or prompt-renderer tests
- TUI changes:
  - run focused tests and inspect for coordinator/design-system compliance

If you modify cross-cutting behavior, run broader tests before declaring success.

## gotchas

- repo may contain in-progress user edits; avoid reverting unrelated work
- `.env` exists and may contain secrets
- some docs reference historical paths or older names; verify against live code
- `README.md` is high-level; `CLAUDE.md` and package source are better for implementation details
- plugin examples in prompt bundles may be illustrative; prefer live code in `plugins/` and `packages/`

## fast references

Read these first for most tasks:
- `README.md`
- `CLAUDE.md`
- `docs/architecture/architecture-overview.md`
- `docs/features/agents.md`
- `packages/kollabor-agent/src/kollabor_agent/agent_manager.py`
- `packages/kollabor-plugins/src/kollabor_plugins/base.py`
- `plugins/agent_orchestrator/plugin.py`

## if the task is specifically "create or update AGENTS.md"

Aim for a document that:
- explains the monorepo structure clearly
- tells agents where logic belongs
- captures repo-specific conventions, not generic advice
- points to authoritative files
- emphasizes investigation-first workflow
- warns about the TUI coordinator rules and hook-first architecture

## summary

If you are an agent operating in this repository:
- investigate first
- respect package boundaries
- follow hook/plugin patterns
- use the TUI coordinator/design system correctly
- validate with targeted tests
- do not disturb unrelated user changes
