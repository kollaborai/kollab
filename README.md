# Kollab

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/kollab)](https://pypi.org/project/kollab/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<p align="center">
  <strong>A terminal AI workspace for people who want their agents, tools, providers, plugins, and workflows to share one inspectable runtime.</strong>
</p>

<p align="center">
  <a href="docs/getting-started.md">Getting Started</a> ·
  <a href="docs/providers.md">Providers</a> ·
  <a href="docs/features/agents.md">Agents</a> ·
  <a href="docs/plugins/overview.md">Plugins</a> ·
  <a href="docs/guides/hub-quick-start.md">Hub</a>
</p>

<p align="center">
  <img src="docs/assets/kollab-demo.gif" alt="Kollab terminal demo showing koordinator coordinating with lapis and sapphire through the local agent hub" width="760">
</p>

Kollab is a terminal-native AI workspace for developers who want more than a
single chat box. It brings interactive chat, slash commands, provider profiles,
tool permissions, MCP servers, plugins, pipe mode, and collaborating agents into
one CLI.

The point is not to hide the machinery. Kollab gives you a fast daily AI
terminal, but it also exposes the runtime underneath it: every meaningful stage
is hookable. User input, LLM requests, streamed responses, message display, tool
calls, and post-tool results all move through an event pipeline that plugins can
observe, transform, or block.

That lets Kollab do things that are still unusual in a terminal AI app: multiple
agents can discover each other without a central server, exchange hub messages,
carry task ledgers across context compaction, keep vault-backed memory, receive
scheduled prompts, and advertise identity, trust, and capabilities through an
experimental Agent DNS layer.

Kollab is still beta, and some of the ambitious pieces are being hardened. But
the shape is already there: a local, inspectable AI workspace that can grow from
one chat session into a small team of coordinated agents.

## What Kollab Does

- Chat with frontier, local, or OpenAI-compatible models from a fast terminal UI.
- Switch providers and model profiles without leaving the session.
- Use a ChatGPT subscription through OpenAI OAuth, or bring API keys for other providers.
- Run one-shot prompts and shell pipelines with `kollab -p`.
- Connect MCP servers for filesystems, GitHub, databases, browsers, search, and custom tools.
- Gate shell, file, and MCP tool use through risk-aware approvals.
- Launch specialized agents from bundled, global, or project-local definitions.
- Coordinate multiple terminal agents through the hub: status, messages, task ledgers, vault memory, attach mode, and recurring prompts.
- Resolve agents by identity, capability, and trust with the experimental Agent DNS layer.
- Extend the runtime with Python plugins, slash commands, widgets, XML tool tags, and JSON config hooks.
- Save, resume, inspect, and compact conversations as work evolves.

## Why People Notice It

Kollab feels familiar at first: open a terminal, ask for help, use tools, keep
working. The difference shows up once the work gets bigger than one prompt.

You can start a coding agent in one terminal, a reviewer in another, and a
coordinator in a third. They join a project-scoped mesh automatically through
presence files and Unix sockets. You can message them from the TUI or from the
shell, attach to their output, assign durable tasks, schedule recurring check-ins,
and inspect the memory they carry forward.

The result is a local agent workspace instead of a black-box automation. You can
see who is online, what they were asked to do, what they produced, which tools
were approved, and which hooks or plugins changed the flow.

## Feature Tour

| Area | What you get |
| --- | --- |
| Terminal chat | Streaming responses, command menu, status layout, themes, widgets, conversation history, and resume |
| Providers and profiles | Anthropic, OpenAI, Google Gemini, Azure OpenAI, OpenRouter, Ollama, LM Studio, and custom OpenAI-compatible endpoints |
| OpenAI OAuth | `kollab --login openai` for ChatGPT subscription-backed usage without an API key |
| Pipe mode | `git diff \| kollab "review this" -p --timeout 5min` for scripts, CI, shell workflows, and automation |
| Tool permissions | Approval modes, risk assessment, session/project approvals, blocked tools, trusted tools, and safe wildcard matching |
| MCP integration | Project and global MCP configs, `/mcp` management, external tools, and approval-aware MCP calls |
| Agent system | Bundled agents (`bundles/agents/`), optional local/global agents, **[Agent Skills](https://agentskills.io/specification)** modules (`bundles/skills/` + `.kollab/skills/` + `~/.kollab/skills/`), and dynamic prompts |
| Agent hub | Peer discovery, hub messages, broadcasts, task ledger, output capture, vault memory, cron messages, Telegram bridge, and org launch files |
| Agent DNS | Experimental identity, trust, capability lookup, Ed25519 keys, AID-style TXT export, ARDP-style registration payloads, and DNS roster commands |
| Plugin system | Event hooks, custom commands, startup info, config widgets, XML tags, context injection, and clean shutdown |
| Engine and attach mode | Local daemon/backend pieces, RPC state services, and attach workflows for long-running sessions |

## Agent Hub

The hub is Kollab's agent collaboration layer. Agents launched in the same
project can discover each other automatically, communicate over local sockets,
and coordinate through a shared command surface.

```bash
kollab --agent coder --as lapis
kollab --agent technical-writer --as sapphire
kollab --hub status
kollab --hub msg lapis "review the latest diff"
kollab --hub capture lapis 100
```

Hub features include:

- Zero-config peer discovery for local agents in the same project.
- Optional fixed identities with `--as` (for example, `--as lapis`) when you
  want stable names instead of auto-assigned designations.
- Direct messages, broadcasts, live output capture, and read-only attach mode.
- Durable task ledgers with `active -> done -> QA review -> closed` workflows.
- Vault-backed memory with raw streams, rolling working memory, and crystallized long-term notes.
- Recurring hub messages through `/hub cron`.
- Optional Telegram forwarding for remote check-ins.
- Organization launch files for starting multi-agent teams.

## Agent DNS

Agent DNS is experimental, but it is one of the more forward-looking pieces of
Kollab. It adds a discovery, identity, and trust layer on top of the hub so
agents can be addressed by more than a process name.

The DNS registry stores agent records with an ARDP-style identity such as
`agent:lapis@kollabor.ai`, local socket or remote endpoint bindings,
capability entries, public keys, approval state, trust score, and current
runtime state. It can export AID-style TXT records and publish well-known
agent key metadata for interoperability experiments.

```text
/hub dns resolve
/hub dns resolve lapis
/hub dns find review
/hub dns trust lapis
/hub dns leaderboard
/hub dns endorse lapis review
/hub dns keys lapis
```

Today this is best understood as an emerging trust and routing layer for local
agent meshes: resolve who is online, find agents by capability, inspect key
material, and track reputation through task outcomes and endorsements.

## Why It Exists

Most AI CLIs are either chat windows or opaque automations. Kollab aims for the
middle ground: a terminal workspace you can actually steer, inspect, and rewire.
It is useful as a day-to-day coding chat, but the interesting part is the
runtime beneath it: hooks, plugins, agents, permissions, MCP, and durable
conversation state all share the same pipeline.

```text
user input -> pre_user_input -> pre_api_request -> [LLM API]
           -> post_api_response -> pre_message_display -> output
                                    |
                                    -> pre_tool_use -> tool execution -> post_tool_use
```

That pipeline is how the hub, permissions, context compaction, MCP, terminal
sessions, save/resume workflows, and plugin-provided tools layer onto the same
core app.

## Status

Kollab is beta software. The CLI, provider integrations, plugin APIs, and agent
workflows are usable today, but some advanced runtime, engine, and integration
paths are still being hardened. See [CHANGELOG.md](CHANGELOG.md) and
[docs/release-process.md](docs/release-process.md) for release notes and release
gates.

## Install

Recommended installer:

```bash
curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash
kollab
```

Python package managers:

```bash
uv tool install kollab
pipx install kollab
pip install kollab
```

Homebrew packaging is prepared separately after a release wheel is published and
the formula SHA is available. Maintainer notes live in
[homebrew-tap/README.md](homebrew-tap/README.md).

## Quick Start

Kollab auto-detects common provider environment variables:

| Environment Variable | Provider | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic | Claude models |
| `OPENAI_API_KEY` | OpenAI | GPT models |
| `GEMINI_API_KEY` | Google | Gemini models |
| `OPENROUTER_API_KEY` | OpenRouter | Gateway to many providers |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI | Requires Azure endpoint and model settings |

```bash
export OPENAI_API_KEY="<your-openai-api-key>"
kollab
```

By default, Kollab starts with the `koordinator` agent and joins the local hub
mesh, so a plain `kollab` session is ready for multi-agent coordination.

Provider-specific model env vars are also supported for auto-detected profiles:

```bash
export OPENROUTER_API_KEY="<your-openrouter-api-key>"
export OPENROUTER_MODEL="deepseek/deepseek-v3.2"
kollab
```

Use `/profile` inside Kollab to list, switch, and create profiles. For more
configuration options, see [docs/configuration.md](docs/configuration.md),
[docs/providers.md](docs/providers.md), and
[docs/reference/env-vars.md](docs/reference/env-vars.md).

You can also define profiles directly from environment variables:

```bash
# Pattern: KOLLAB_{PROFILE}_{FIELD}
export KOLLAB_WORK_MODEL=claude-sonnet-4-6
export KOLLAB_WORK_PROVIDER=anthropic
export KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
export KOLLAB_WORK_BASE_URL="https://api.anthropic.com"
# Optional tuning fields
export KOLLAB_WORK_MAX_TOKENS=4096
export KOLLAB_WORK_TEMPERATURE=0.3
export KOLLAB_WORK_TIMEOUT=30000
export KOLLAB_WORK_TOP_P=0.95
export KOLLAB_WORK_STREAMING=true
export KOLLAB_WORK_SUPPORTS_TOOLS=true
export KOLLAB_WORK_DESCRIPTION="Claude profile for work tasks"
# EXTRA_HEADERS must be valid JSON
export KOLLAB_WORK_EXTRA_HEADERS='{"x-trace-id":"work-session"}'
kollab --profile work
# Persist this env-defined profile to config
kollab --profile work --save
# Persist and set as startup default profile
kollab --profile work --default
# Persist to project-local config instead of global
kollab --profile work --save --local
# Set project-local default profile
kollab --profile work --default --local
```

Common profile fields:
`MODEL`, `PROVIDER`, `API_KEY`, `BASE_URL`, `MAX_TOKENS`, `TEMPERATURE`,
`TIMEOUT`, `TOP_P`, `STREAMING`, `SUPPORTS_TOOLS`, `DESCRIPTION`,
`EXTRA_HEADERS` (JSON string).

Resolution order is:
`KOLLAB_{PROFILE}_{FIELD}` -> `KOLLAB_{FIELD}` -> config -> defaults.
`KOLLAB_{PROFILE}_MODEL` is required to create a profile from env vars.

## Common Workflows

### Use ChatGPT OAuth

```bash
kollab --login openai
```

Kollab opens a browser for authorization, stores the token in local user runtime
state, and uses the Responses API with the subscription quota.

### Run Pipe Mode

```bash
kollab "What is the capital of France?"
echo "Explain this code" | kollab -p
cat document.txt | kollab "summarize this" -p
git diff | kollab "write a concise commit message" -p --timeout 30s
```

### Launch Agents

```bash
kollab --agent coder --as lapis
kollab --agent technical-writer --as sapphire --skill readme-writing
```

Agents can be bundled with the project, installed globally, or defined inside a
workspace under `.kollab/agents/`. See [docs/features/agents.md](docs/features/agents.md).

### Coordinate Agents With The Hub

```bash
kollab --agent coder --as lapis
kollab --hub status
kollab --hub msg lapis "review the latest diff"
kollab --hub capture lapis 100
kollab --hub org engineering "ship the billing flow"
kollab --org engineering
kollab --hub stop lapis
```

Hub capabilities include designations, project-scoped memory, task ledger
workflows, recurring hub messages, optional Telegram forwarding, organization
launch files, and experimental DNS-style identity and trust commands. Start with
[docs/guides/hub-quick-start.md](docs/guides/hub-quick-start.md).

### Manage Tools And MCP

```text
/permissions
/mcp show
/mcp add
```

MCP tools run through the same approval system as native tools. See
[docs/features/permissions.md](docs/features/permissions.md) and
[docs/features/mcp.md](docs/features/mcp.md).

### Extend Kollab

Plugins can register hooks, slash commands, startup info, config widgets, and
tool handlers:

```python
from kollabor_plugins import BasePlugin

class MyPlugin(BasePlugin):
    async def register_hooks(self):
        self.event_bus.register_hook("pre_api_request", self.inject_context)
```

Plugin entry points live under `plugins/`, and the plugin SDK lives in
`packages/kollabor-plugins`. See [docs/plugins/overview.md](docs/plugins/overview.md),
[docs/plugins/development.md](docs/plugins/development.md), and
[docs/plugins/hooks-reference.md](docs/plugins/hooks-reference.md).

## Slash Commands

| Command | Description |
| --- | --- |
| `/profile` | List, switch, and create LLM profiles |
| `/agent` | Switch agent definitions when available |
| `/skill` | Load or unload agent skills when available |
| `/save` | Save conversation output |
| `/hub` | Manage the agent hub |
| `/hub dns` | Resolve agents, inspect trust, find capabilities, and show Agent DNS keys |
| `/terminal` | Manage terminal sessions |
| `/permissions` | Configure tool approval modes |
| `/login` | Run provider login flows |
| `/mcp` | Manage MCP servers |
| `/resume` | Resume a previous conversation |
| `/config` | Open the settings editor |
| `/help` | Show available commands |

Type `/` in the app to see the full command menu. Plugins can add more commands.

## Repository Layout

Kollab is a Python monorepo. The root package provides the `kollab` command and
the `kollabor/` orchestration layer; reusable runtime pieces live in workspace
packages.

| Package | Role |
| --- | --- |
| [kollabor](kollabor) | CLI startup, application orchestration, commands, LLM coordination |
| [packages/kollabor-ai](packages/kollabor-ai) | Providers, profiles, OAuth, prompt rendering, conversation state |
| [packages/kollabor-agent](packages/kollabor-agent) | Tool execution, MCP, permissions, file/shell operations, agents |
| [packages/kollabor-tui](packages/kollabor-tui) | Terminal rendering, input, widgets, fullscreen views, status UI |
| [packages/kollabor-events](packages/kollabor-events) | Event bus, hook registry, executor, processor, event models |
| [packages/kollabor-config](packages/kollabor-config) | Configuration loading and utilities |
| [packages/kollabor-plugins](packages/kollabor-plugins) | Plugin framework and SDK |
| [packages/kollabor-rpc](packages/kollabor-rpc) | RPC protocol and client helpers |
| [packages/kollabor-engine](packages/kollabor-engine) | Local engine/backend service |
| [packages/kollabor-webui](packages/kollabor-webui) | Web UI package |
| [plugins](plugins) | Concrete built-in plugin implementations |
| [bundles](bundles) | Bundled agents, skills, themes, layouts, and widgets |

## Development

```bash
git clone https://github.com/kollaborai/kollab.git
cd kollab
uv sync --all-packages --extra dev
uv run python main.py
```

Focused validation used by the current CI baseline:

```bash
uv run python -m pytest \
  tests/unit/test_openai_streaming_finish_reason.py \
  tests/unit/test_turn_runner_tool_loop.py \
  tests/unit/test_hub_project_scope.py \
  tests/unit/test_provider_models.py \
  tests/unit/test_provider_security.py \
  tests/unit/test_gemini_provider.py
uv run python -m ruff check --select E9,F63,F7,F82 kollabor packages plugins tests
uv run python -m py_compile kollabor/cli.py kollabor_cli_main.py plugins/hub/plugin.py
```

For contribution guidance, see [CONTRIBUTING.md](CONTRIBUTING.md). For agent and
architecture guidance, see [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), and
[docs/architecture/README.md](docs/architecture/README.md).

## Documentation

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [Provider Profiles](docs/providers.md)
- [Agent System](docs/features/agents.md)
- [Hub Quick Start](docs/guides/hub-quick-start.md)
- [Telegram Bridge Setup](docs/guides/telegram-bridge-setup.md)
- [MCP](docs/features/mcp.md)
- [Permissions](docs/features/permissions.md)
- [Plugin Development](docs/plugins/development.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Release Process](docs/release-process.md)

## Support and Security

- Questions and bug reports: [GitHub Issues](https://github.com/kollaborai/kollab/issues)
- Support expectations: [SUPPORT.md](SUPPORT.md)
- Vulnerability reporting: [SECURITY.md](SECURITY.md)

Do not post API keys, OAuth tokens, private conversation logs, raw transcripts,
or local runtime data in public issues.

## License

Kollab is licensed under the [MIT License](LICENSE).
