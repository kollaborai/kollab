# Kollab

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/kollab)](https://pypi.org/project/kollab/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Kollab is a terminal AI workspace for developers who want chat, tools,
providers, plugins, and collaborating agents in one CLI.

It combines a terminal chat interface, hookable event pipeline, multi-provider
LLM profiles, MCP support, slash commands, plugin loading, and an agent hub that
lets multiple agents coordinate work from the command line.

```bash
curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash
kollab
```

## Status

Kollab is beta software. The CLI, provider integrations, plugin APIs, and
agent workflows are usable, but some advanced runtime and integration paths are
still being hardened. See [CHANGELOG.md](CHANGELOG.md) and
[docs/release-process.md](docs/release-process.md) for release notes and release
gates.

## Highlights

- **Terminal AI chat** with streaming output, slash commands, pipe mode, and
  conversation persistence.
- **Agent hub** for launching agents, sending hub messages, checking status,
  capturing output, and coordinating multi-agent work.
- **Multi-provider profiles** for Anthropic, OpenAI, Google Gemini, Azure
  OpenAI, OpenRouter, Ollama, LM Studio, and OpenAI-compatible endpoints.
- **OpenAI OAuth** for using a ChatGPT subscription with `kollab --login openai`.
- **Hook and plugin system** for intercepting the pipeline, adding commands,
  registering XML tool tags, and extending runtime behavior.
- **MCP support** for connecting external Model Context Protocol servers.
- **Telegram bridge** for optional remote interaction with running agents.
- **Workspace packages** for AI providers, agent runtime, TUI, events,
  configuration, plugins, engine, RPC, and web UI components.

## Install

Recommended installer:

```bash
curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash
```

Python package managers:

```bash
uv tool install kollab
pipx install kollab
pip install kollab
```

Homebrew packaging is prepared separately after a release wheel is published and
the formula SHA is available.

## Quick Start

Kollab auto-detects common provider environment variables:

| Environment Variable | Provider | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | Claude models |
| `OPENAI_API_KEY` | OpenAI | GPT models |
| `GEMINI_API_KEY` | Google | Gemini models |
| `OPENROUTER_API_KEY` | OpenRouter | Gateway to many providers |

```bash
export OPENAI_API_KEY="<your-openai-api-key>"
kollab
```

Use `/profile` inside Kollab to list, switch, and create profiles. For more
configuration options, see [docs/configuration.md](docs/configuration.md),
[docs/providers.md](docs/providers.md), and
[docs/reference/env-vars.md](docs/reference/env-vars.md).

## OpenAI OAuth

Use a ChatGPT subscription without an API key:

```bash
kollab --login openai
```

Kollab opens a browser for authorization, stores the token in local user
runtime state, and uses the Responses API with the subscription quota.

## Pipe Mode

```bash
kollab "What is the capital of France?"
echo "Explain this code" | kollab -p
cat document.txt | kollab -p
kollab --timeout 5min "Analyze this repository"
```

## Agent Hub

Agents can discover each other, exchange hub messages, and coordinate work.

```bash
kollab --agent coder
kollab --hub status
kollab --hub msg lapis "review the latest diff"
kollab --hub capture lapis 100
kollab --hub stop lapis
```

Hub capabilities include designations, project-scoped memory, task ledger
workflows, recurring hub messages, optional Telegram forwarding, and organization
launch files. Start with [docs/guides/hub-quick-start.md](docs/guides/hub-quick-start.md)
and [docs/features/agents.md](docs/features/agents.md).

## Slash Commands

| Command | Description |
|---|---|
| `/profile` | List, switch, and create LLM profiles |
| `/save` | Save conversation output |
| `/hub` | Manage the agent hub |
| `/terminal` | Manage terminal sessions |
| `/permissions` | Configure tool approval modes |
| `/login` | Run provider login flows |
| `/mcp` | Manage MCP servers |
| `/resume` | Resume a previous conversation |
| `/config` | Open the settings editor |
| `/help` | Show available commands |

Type `/` in the app to see the full command menu.

## Hooks and Plugins

Every major stage of the pipeline is hookable:

```text
user input -> pre_user_input -> pre_api_request -> [LLM API]
           -> post_api_response -> pre_message_display -> output
                                    |
                                    -> pre_tool_use -> [tool execution] -> post_tool_use
```

Plugin entry points live under `plugins/`, and the plugin SDK lives in
`packages/kollabor-plugins`. See [docs/plugins/overview.md](docs/plugins/overview.md),
[docs/plugins/development.md](docs/plugins/development.md), and
[docs/plugins/hooks-reference.md](docs/plugins/hooks-reference.md).

## Repository Layout

Kollab is a Python monorepo. The root package provides the `kollab` command and
the `kollabor/` orchestration layer; reusable runtime pieces live in workspace
packages.

| Package | Role |
|---|---|
| [packages/kollabor-ai](packages/kollabor-ai) | Providers, profiles, OAuth, prompt rendering |
| [packages/kollabor-agent](packages/kollabor-agent) | Tool execution, MCP, permissions, agents |
| [packages/kollabor-tui](packages/kollabor-tui) | Terminal rendering, input, widgets, status UI |
| [packages/kollabor-events](packages/kollabor-events) | Event bus and hook registry |
| [packages/kollabor-config](packages/kollabor-config) | Configuration loading and utilities |
| [packages/kollabor-plugins](packages/kollabor-plugins) | Plugin framework and SDK |
| [packages/kollabor-rpc](packages/kollabor-rpc) | RPC protocol and client helpers |
| [packages/kollabor-engine](packages/kollabor-engine) | Local engine/backend service |
| [packages/kollabor-webui](packages/kollabor-webui) | Web UI package |

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
