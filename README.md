# Kollab

[![Python Version](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/kollab)](https://pypi.org/project/kollab/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/kollaborai/kollab/blob/main/LICENSE)

AI agents across your computers. Each one has a name and keeps its memory, they message each other, and you pick the model. Apache 2.0 licensed.

<p align="center">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/hero-dark.webp" alt="A Kollab chat where the koordinator agent has Claude Code fix a failing test and Lapis review the diff." width="100%">
</p>

```bash
uv tool install kollab
kollab                    # start an agent in this folder
kollab service install    # keep one running on a server
```

[Docs](https://kollabor.ai/docs) · [Agent network](https://kollabor.ai/docs/network) · [Providers](https://kollabor.ai/docs/loadouts)

**New:** Claude Code and Codex join the network as named agents, beside the ones Kollab runs itself. They sit in the same roster, other agents can message them, and their permission requests come to Kollab's approval prompt. Kollab itself can also run as an agent inside Zed and other ACP clients. [Details below.](https://github.com/kollaborai/kollab#bring-claude-code-and-codex)

## See It Run in a Terminal

<p align="center">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/kollab-join.webp" alt="Kollab running: koordinator spins up three agents that build and test checkout rate limiting together; a second pane SSHes into a server, joins the network with a one-time code, receives the laptop's settings, pulls the pushed code and runs the tests" width="100%">
</p>

koordinator starts three agents that build and test a change together. A second terminal on a server joins the network with a one-time code, receives the laptop's settings, pulls the pushed code and runs the tests.

## What Kollab Does

- Run named agents on all your computers. They find each other, message `agent@device`, and keep their memory between sessions. Each machine dials out on port 443, so nothing needs an open port.
- Bring Claude Code and Codex onto that network as named agents, and use Kollab from Zed and other ACP clients with `kollab acp`.
- Add MCP servers, CLI tools, and skills from `/mcp` in the terminal or the Apps button in the browser UI.
- Open every agent in a browser UI (`kollab --web-ui`): a roster of this computer and your network, chat with tool and diff cards, and a layout that fits a phone.
- Pick the model for each agent: Anthropic, OpenAI, Google Gemini, Azure OpenAI, OpenRouter, Ollama, LM Studio, or any OpenAI-compatible endpoint, with named loadouts for model, effort, and sampling. Switch with `/llm` without leaving the session.
- Chat in a fast terminal UI, or run one-shot prompts and shell pipelines with `kollab -p`.
- Gate shell, file, and MCP tool use through risk-aware approvals.
- Load tools on demand instead of injecting every schema into every request, and keep oversized tool results as inspectable artifacts.
- Launch specialized agents from bundled, global, or project-local definitions.
- Extend the runtime with Python plugins, slash commands, widgets, XML tool tags, and JSON config hooks. Every stage of a turn is hookable.
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
| OpenAI sign-in | `kollab --login openai` signs in with a browser instead of an API key |
| Model registry and loadouts | Catalog metadata, context windows, pricing, provider-aware sampling, reasoning effort, and named `/llm` presets |
| Pipe mode | `git diff \| kollab "review this" -p --timeout 5min` for scripts, CI, shell workflows, and automation |
| Tool permissions | Approval modes, risk assessment, session/project approvals, blocked tools, trusted tools, and safe wildcard matching |
| Tools and MCP | Native/XML tool parity, `workspace-set`, `web-search`/`web-fetch`, on-demand `tool-search`/`tool-load`, project/global MCP configs, and approval-aware external calls |
| Context protection | Aggregate tool-output budgets with lossless `.output` artifacts and bounded previews when context gets large |
| Agent system | Bundled agents (`bundles/agents/`), optional local/global agents, **[Agent Skills](https://agentskills.io/specification)** modules (`bundles/skills/` + `.kollab/skills/` + `~/.kollab/skills/`), and dynamic prompts |
| Agent hub | Peer discovery, identity/bundle mapping, hub messages, broadcasts, task ledger, checkpoint/snooze/QA flows, output capture, vault memory, cron messages, Telegram bridge, and org launch files |
| Harness agents (ACP) | Claude Code and Codex as named agents on the network, and `kollab acp` to use Kollab from Zed and other ACP clients |
| Agent DNS | Experimental identity, trust, capability lookup, Ed25519 keys, AID-style TXT export, ARDP-style registration payloads, and DNS roster commands |
| Plugin system | Event hooks, custom commands, startup info, config widgets, XML tags, context injection, and clean shutdown |
| Apps | MCP servers, CLI tools, and skills from `/mcp` or the Apps screen, with search across the official MCP Registry |
| Engine, web UI, and attach mode | Local FastAPI engine, assistant-ui browser client, bearer-token wiring, RPC state services, multi-context daemons, and attach workflows |

## Agent Hub

The hub is Kollab's agent collaboration layer. Agents launched in the same
project can discover each other automatically, communicate over local sockets,
and coordinate through a shared command surface.

Keep these three names separate:

- **identity** — the stable hub name or mailbox, such as `lapis`. It is what
  `/hub status`, `/hub msg`, and `/hub capture` address.
- **bundle** — the behavior package, such as `coder` or `research`: system
  prompt, prompt sections, allowed tools, and declared skills.
- **provider profile/loadout** — the model connection and tuning. A profile owns
  credentials; a loadout combines a profile with a model and parameters such as
  effort or output budget.

This launch runs the `coder` bundle under the `lapis` identity with the
`openai-oauth` provider profile:

```bash
kollab --agent coder --as lapis --llm openai-oauth
```

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
  want stable names instead of auto-assigned identities.
- Direct messages, broadcasts, live output capture, and interactive attach mode.
- Durable task ledgers with `active -> done -> QA review -> closed` workflows, including the `task_snooze` reminder control.
- Vault-backed memory with raw streams, rolling working memory, and crystallized long-term notes.
- Recurring hub messages through `/hub cron`.
- Optional Telegram forwarding for remote check-ins.
- Organization launch files for starting multi-agent teams.

An `offline inbox` is durable mail waiting for a known identity; it is not a
live process. `capture` only works for an online peer or active orchestrator
session, so `agent 'lapis' not found` means there is no capturable live runtime
under that name. A direct message can still be queued for the identity and
delivered when it reconnects.

`research` is normally a bundle name, not a hub identity. Capture the identity
shown by `/hub status` instead.

## Agent DNS

Agent DNS is experimental, but it is one of the more forward-looking pieces of
Kollab. It adds a discovery, identity, and trust layer on top of the hub so
agents can be addressed by more than a process name.

The local DNS registry stores agent records with an ARDP-style identity such as
`agent:lapis@kollabor.ai`, socket or endpoint bindings, capabilities, public keys,
approval state, trust score, and runtime state. Public domain discovery uses a
separate signed descriptor cache with persistent origin/key pins. Discovering a
domain does not import its keys into the messaging registry or authorize tools.

```text
/hub dns resolve
/hub dns resolve lapis
/hub dns find review
/hub dns trust lapis
/hub dns leaderboard
/hub dns endorse lapis review
/hub dns keys lapis
/hub dns endpoint
/hub dns connect example.com
```

Today this is best understood as an emerging trust and routing layer for local
agent meshes: resolve who is online, find agents by capability, inspect key
material, and track reputation through task outcomes and endorsements.

An optional off-box endpoint (`plugins.hub.endpoint_enabled`) binds a TCP/TLS
listener that shares the same Ed25519 handshake, so a remote agent on another
machine can authenticate and deliver messages over the network. It is disabled
by default, always requires the handshake, and refuses to bind a plaintext port
without an explicit opt-in. This historical direct stream transport is separate
from the WSS relay and the standard A2A workspace receiver. See the
[direct endpoint reference](https://github.com/kollaborai/kollab/blob/main/docs/specs/hub-remote-endpoint.md).

### Connect agents across machines

`/connect` makes the agents on your other machines part of the same hub. A hub
message to `agent@device` reaches that machine and wakes that agent; the rest of
the network observes it, like the local hub. Start with the
[connect guide](https://github.com/kollaborai/kollab/blob/main/docs/guides/connect.md):

```text
# on the machine that is already connected
/connect                        # the screen: an 8-character code, one device, five minutes
# on the new machine
/connect                        # type the code into the private form
# back on the first machine, the request shows up on the screen: press a, or
/connect accept home-server
# then, from any agent or a shell
kollab --hub msg ops@home-server "check the tunnel"
```

`kollab service install` in the joined folder keeps that machine's agent online
across crashes and reboots (systemd on Linux, launchd on macOS); `kollab` there
attaches to it.

Run your own directory on your own domain with one command, `kollab relay serve
--domain agents.example.com`: it prints the DNS record and the proxy routes it still
needs, and `--install` keeps it running as a systemd service (`kollab relay run
--config` is the form for several workers). The
[agent network contract](https://github.com/kollaborai/kollab/blob/main/docs/specs/agent-network-simple-flow.md) is the design;
the [public beacon contract](https://github.com/kollaborai/kollab/blob/main/docs/specs/agent-public-beacon.md) covers the relay's
routes, limits and key handling.

### How the network works

Both machines dial out to the directory on port 443, so nothing needs an open
port. The directory relays frames by device key and can't open them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/topology-dark.svg">
  <img alt="Both machines' kollab daemons dial out to kollabor.ai on port 443; the relay forwards frames between them without opening them." src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/topology-light.svg">
</picture>

Every message rides five layers deep. The directory reads the outer two; only
your two daemons can open the rest.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/layers-dark.svg">
  <img alt="One connection, five layers: the wss link and relay frame are visible to the directory; the NaCl Box, the mutual TLS 1.3 session and the hub message inside are readable only by your two daemons." src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/layers-light.svg">
</picture>

<details>
<summary>The join, step by step: a code, a sealed mailbox, one key press</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/join-dark.svg">
  <img alt="The join in 27 steps between your Mac, kollabor.ai and a new VPS: the Mac makes a code, the VPS uses it to claim a mailbox offer, both prove keys through sealed envelopes, you accept on the Mac, the VPS receives its credential and profile, then both hold relay links and talk over TLS 1.3 inside Box frames." src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/diagrams/agent-network/join-light.svg">
</picture>

</details>

The [agent network architecture](https://github.com/kollaborai/kollab/blob/main/docs/architecture/agent-network.md) has the
rest: what travels and what stays, keeping a device online, how `/connect` uses
your domain's DNS record, and running your own directory.

## Bring Claude Code and Codex

Claude Code and Codex join your network as named agents over the Agent Client Protocol (ACP). They sit in the roster beside Kollab's own agents. Other agents message them like any peer and get the final answer back. In turn they can message others through Kollab's hub tools (`hub_status`, `hub_agents`, `hub_msg`, `hub_ask`, `hub_spawn`).

The harness keeps its own model, login, tools and context. Kollab gives it a name on the network, a window, a saved transcript, routing, and the approval prompt.

<p align="center">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/shot-acp-dark.webp" alt="Claude Code as an agent in Kollab's browser UI: its searches, file reads, the edit to shop/checkout.py as a diff, and the test run show as cards." width="100%">
</p>

```bash
kollab --as claude --llm claude-code --detached   # Claude Code joins as "claude"
kollab --as codex --llm codex-cli --detached      # Codex joins as "codex"
kollab --attach claude                            # open a window on it
```

Install the harness and its ACP adapter on that computer and sign in there first; `/llm` shows the exact install command. Both have been run through model, tool, permission, cancel, steering and resume checks, and Codex has answered a request across two devices. Gemini CLI has a built-in `gemini-cli` profile but is not qualified yet.

### Use Kollab from Zed

`kollab acp` makes Kollab an ACP agent for Zed and other ACP clients. On its own it starts a headless Kollab daemon in the client's folder and stops it when the client closes. `--attach lapis` connects to the agent `lapis` already running there instead, and leaves it running when the client closes. To attach from Zed, add this to its settings (drop `"--attach", "lapis"` to start a daemon instead):

```json
{
  "agent_servers": {
    "kollab": {
      "type": "custom",
      "command": "kollab",
      "args": ["acp", "--attach", "lapis"]
    }
  }
}
```

Zed Preview is the client it has been checked against. Prompts are text only, and a client shows its own turns; turns the mesh starts show in Kollab's own windows. See [ACP agents](https://github.com/kollaborai/kollab/blob/main/docs/features/acp.md) for the details.

Claude Code, Codex and Zed are trademarks of their owners. Kollab is not affiliated with Anthropic, OpenAI or Zed Industries.

## Browser UI and Local Engine

The optional browser surface uses the same provider, tool, permission, MCP, and
hub runtime as the terminal app. From a source checkout or installed workspace:

```bash
kollab --web-ui
# browser: http://127.0.0.1:8080
# engine:  http://127.0.0.1:7433
```

The flag starts the local engine when needed, launches `kollabor-webui`, reuses a
healthy engine on port `7433` when it is this version from this install (any other
engine keeps running and the next port is used), and cleans up child processes on exit. A second
`kollab --web-ui` reuses the running UI; `kollab --web-ui --restart` stops it (and
the engine it started) and starts both again, e.g. after an update. For separate
development loops, see the [engine README](https://github.com/kollaborai/kollab/blob/main/packages/kollabor-engine/README.md)
and [web UI README](https://github.com/kollaborai/kollab/blob/main/packages/kollabor-webui/README.md).

The sidebar lists every agent on this computer and on the other computers of your network. Claude Code and Codex agents open like any other, with their tool calls and file changes as tool and diff cards. New Session has a folder picker, and the layout fits a phone.

<p align="center">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/shot-roster-dark.webp" alt="The Kollab sidebar: four agents on one computer (Koordinator, Claude Code, Codex, Lapis) with harness labels." width="53%">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/shot-mobile-dark.webp" alt="Kollab on a phone: the koordinator chat showing the diff review and the final test result." width="39%">
</p>

The web UI's Settings dialog (Session, Configuration, Loadouts, Model, Setup, Network) does what the terminal's `/config`, `/llm`, `/model`, `/setup` and `/connect` screens do; see [web settings panels](https://github.com/kollaborai/kollab/blob/main/docs/features/web-settings-panels.md).

## Apps

One place to give your agents more tools. Open **Apps** in the browser UI's sidebar, or run `/mcp` in the terminal (the manager lists the catalog's MCP servers; `a` adds one). Whatever you add is available to every agent on that computer, and the session you added it from uses it right away.

<p align="center">
  <img src="https://raw.githubusercontent.com/kollaborai/kollab/main/docs/media/shot-apps-dark.webp" alt="The Apps screen in Kollab's browser UI: MCP servers, CLI tools and skills, each with an Add button" width="100%">
</p>

| You add | What Add does |
| --- | --- |
| MCP server | Saves it and connects it to the current session |
| CLI tool | Installs it with a package manager you already have (brew, npm, uv or pipx) |
| Skill | Downloads it from its GitHub repo into `~/.kollab/skills/` |

```text
/mcp search <words>   every match, by name, description or tag
/mcp add context7     add an MCP server from the directory
/mcp add cli:gh       install a CLI tool
/mcp remove <id>      remove an MCP server or a skill added from Apps
```

Search also reaches the official MCP Registry (about 40,000 servers). Kollab has not reviewed those, so Add shows the exact command it will run first, and nothing is saved or started until you confirm. See [Apps](https://github.com/kollaborai/kollab/blob/main/docs/features/apps.md) for the rest.

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
paths are still being hardened. See [CHANGELOG.md](https://github.com/kollaborai/kollab/blob/main/CHANGELOG.md) and
[docs/release-process.md](https://github.com/kollaborai/kollab/blob/main/docs/release-process.md) for release notes and release
gates.

## Install

Recommended installer:

```bash
curl -sS https://raw.githubusercontent.com/kollaborai/kollab/main/install.sh | bash
kollab
```

It installs the single-file `kollab` binary for your machine (macOS 11+, or Linux
with glibc 2.28+, on x86_64 or arm64) into `~/.local/bin`: no Python needed.
`/upgrade` swaps it for the latest release's binary; with automatic updates on
(`/config`), each launch installs a newer release first and restarts into it. Every
[release](https://github.com/kollaborai/kollab/releases) carries the binaries;
elsewhere the installer falls back to the Python package below.

Python package managers:

```bash
uv tool install kollab
# or
pipx install kollab
```

`uv tool` and `pipx` are the recommended installers: each puts kollab in its own
isolated virtualenv, which sidesteps a class of startup failure where obsolete
stdlib-backport packages (`typing`, `asyncio`, `dataclasses`, `pathlib`, ...)
left in a shared interpreter shadow the standard library and crash kollab on
launch.

`pip install kollab` also works, but installs into the active interpreter and so
inherits whatever is already there:

```bash
pip install kollab
```

On an externally managed system Python (including Arch), use an isolated
environment. If `pipx` and `uv` are unavailable, Python itself is sufficient:

```bash
python3 -m venv ~/.local/share/kollab/venv
~/.local/share/kollab/venv/bin/python -m pip install --upgrade kollab
~/.local/share/kollab/venv/bin/kollab --login openai
```

This uses the published package without changing system Python. To run `kollab`
by name in the current shell, activate the environment with
`source ~/.local/share/kollab/venv/bin/activate`. For OpenAI account login, use
`--login openai` at launch or `/login openai` inside the app.

Homebrew packaging is prepared separately after a release wheel is published and
the formula SHA is available. Maintainer notes live in
[homebrew-tap/README.md](https://github.com/kollaborai/kollab/blob/main/homebrew-tap/README.md).

## Quick Start

New here? Launch Kollab and run **`/setup`** — a guided wizard walks you through
picking a provider, entering your API key, choosing a model, optionally testing
the connection, and saving it as your active profile. No env vars or JSON
required. (ChatGPT sign-in delegates to `/login`; Azure/advanced is configured
in `config.json` — see [docs/providers.md](https://github.com/kollaborai/kollab/blob/main/docs/providers.md).)

Prefer environment variables? Kollab also auto-detects common provider vars:

| Environment Variable | Provider | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | Anthropic | Claude models and Anthropic-compatible gateways |
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

Anthropic-compatible gateways can use the Claude Code-style env names:

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="<your-token>"
export ANTHROPIC_DEFAULT_OPUS_MODEL="glm-4.7"
kollab
```

Use `/llm` inside Kollab to switch models and presets. For more
configuration options, see [docs/configuration.md](https://github.com/kollaborai/kollab/blob/main/docs/configuration.md),
[docs/providers.md](https://github.com/kollaborai/kollab/blob/main/docs/providers.md), and
[docs/reference/env-vars.md](https://github.com/kollaborai/kollab/blob/main/docs/reference/env-vars.md).

You can also define profiles directly from environment variables:

```bash
# Pattern: KOLLAB_{PROFILE}_{FIELD}
export KOLLAB_WORK_MODEL=claude-sonnet-5
export KOLLAB_WORK_PROVIDER=anthropic
export KOLLAB_WORK_API_KEY="<your-anthropic-api-key>"
export KOLLAB_WORK_BASE_URL="https://api.anthropic.com"
# Optional tuning fields (TEMPERATURE/TOP_P are ignored on models that
# reject sampling params -- see bundles/data/models.json supports_sampling)
export KOLLAB_WORK_MAX_TOKENS=4096
export KOLLAB_WORK_TEMPERATURE=0.3
export KOLLAB_WORK_TIMEOUT=30
export KOLLAB_WORK_TOP_P=0.95
export KOLLAB_WORK_EFFORT=high
export KOLLAB_WORK_STREAMING=true
export KOLLAB_WORK_SUPPORTS_TOOLS=true
export KOLLAB_WORK_DESCRIPTION="Claude profile for work tasks"
# EXTRA_HEADERS must be valid JSON
export KOLLAB_WORK_EXTRA_HEADERS='{"x-trace-id":"work-session"}'
kollab --llm work
# Persist this env-defined profile to config
kollab --llm work --save
# Persist and set as startup default profile
kollab --llm work --default
# Persist to project-local config instead of global
kollab --llm work --save --local
# Set project-local default profile
kollab --llm work --default --local
```

Common profile fields:
`MODEL`, `PROVIDER`, `API_KEY`, `BASE_URL`, `MAX_TOKENS`, `TEMPERATURE`,
`TIMEOUT` (seconds), `TOP_P`, `EFFORT`, `STREAMING`, `SUPPORTS_TOOLS`, `DESCRIPTION`,
`EXTRA_HEADERS` (JSON string).

Resolution order is:
`KOLLAB_{PROFILE}_{FIELD}` -> `KOLLAB_{FIELD}` -> config -> defaults.
`KOLLAB_{PROFILE}_MODEL` is required to create a profile from env vars.

## Common Workflows

### Sign In With OpenAI

```bash
kollab --login openai
```

Kollab opens a browser for authorization, stores the token in local user runtime
state, and uses the Responses API with your OpenAI account.

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
workspace under `.kollab/agents/`. `--agent` selects the bundle; `--as` selects
the hub identity. See [docs/features/agents.md](https://github.com/kollaborai/kollab/blob/main/docs/features/agents.md).

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

Hub capabilities include identities, project-scoped memory, task ledger
workflows, recurring hub messages, optional Telegram forwarding, organization
launch files, and experimental DNS-style identity and trust commands. Start with
[docs/guides/hub-quick-start.md](https://github.com/kollaborai/kollab/blob/main/docs/guides/hub-quick-start.md).

### Manage Tools And MCP

```text
/permissions
/mcp                 open the MCP manager
/mcp setup           open the MCP manager
/mcp show            show MCP server status
/mcp status          show MCP server status (compatibility alias)
/mcp list            show MCP server status (alias)
/mcp servers         show MCP server status (alias)
/mcp tools [server]  list available MCP tools
/mcp test <server>   test one server connection
/mcp enable <server> enable a server in config
/mcp disable <server> disable a server in config
/mcp reload          reload config and reconnect enabled servers; report failures
aliases: /mcps, /servers
```

Inside `/mcp`, press `g` to toggle the global MCP subsystem. Use Space to
toggle the selected server, `a`/`d` to add or delete a configured server,
`t` to test, `r` to reload, `/` to filter, and Esc to exit.

See [docs/reference/commands.md](https://github.com/kollaborai/kollab/blob/main/docs/reference/commands.md) for the complete
slash-command reference and [docs/features/mcp.md](https://github.com/kollaborai/kollab/blob/main/docs/features/mcp.md) for
configuration details.

MCP tools run through the same approval system as native tools. See
[docs/features/permissions.md](https://github.com/kollaborai/kollab/blob/main/docs/features/permissions.md) and
[docs/features/mcp.md](https://github.com/kollaborai/kollab/blob/main/docs/features/mcp.md).

The built-in `tool-search` and `tool-load` tools let an agent discover a tool by
keyword and load its full schema only when needed. Large tool results remain
available as managed `.output` artifacts while the model receives a bounded
preview; see [tool-output artifacts](https://github.com/kollaborai/kollab/blob/main/docs/specs/tool-output-artifacts.md).

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
`packages/kollabor-plugins`. See [docs/plugins/overview.md](https://github.com/kollaborai/kollab/blob/main/docs/plugins/overview.md),
[docs/plugins/development.md](https://github.com/kollaborai/kollab/blob/main/docs/plugins/development.md), and
[docs/plugins/hooks-reference.md](https://github.com/kollaborai/kollab/blob/main/docs/plugins/hooks-reference.md).

## Slash Commands

| Command | Description |
| --- | --- |
| `/llm` | Switch model loadouts — provider + model + param presets |
| `/model` | Quick model selection and reasoning effort (`/model effort max`) |
| `/agent` | Switch agent definitions when available |
| `/skill` | Load or unload agent skills when available |
| `/setup` | Guided provider/profile setup |
| `/save` | Save conversation output |
| `/hub` | Manage the agent hub |
| `/hub dns` | Resolve agents, inspect trust, find capabilities, and show Agent DNS keys |
| `/connect` | Join machines into one agent network, name devices, set trust, and reach `agent@device` |
| `/terminal` | Manage terminal sessions |
| `/permissions` | Configure tool approval modes |
| `/login` | Run provider login flows |
| `/mcp` | Open the MCP manager; `browse`, `search`, `add`, `remove` for MCP servers, CLI tools, and skills |
| `/resume` | Resume a previous conversation |
| `/config` | Open the settings editor |
| `/updates` | Browse recent release notes in the terminal |
| `/help` | Show available commands |

Type `/` in the app to see the full command menu. Plugins can add more commands.

Common CLI-only or plugin surfaces:

```bash
kollab --help
kollab --doctor                 # first-run readiness check
kollab --updates                # recent changes
kollab --sub list               # agent orchestrator sessions
kollab --attach lapis           # interactively attach to a live identity
kollab service install          # keep this folder's agent running at boot/login and after a crash
kollab --hub status             # hub inspection without a TUI
kollab --web-ui                 # local engine + browser UI
kollab --web-ui --restart       # stop the running browser UI, start it again
```

## Repository Layout

Kollab is a Python monorepo. The root package provides the `kollab` command and
the `kollabor/` orchestration layer; reusable runtime pieces live in workspace
packages.

| Package | Role |
| --- | --- |
| [kollabor](https://github.com/kollaborai/kollab/tree/main/kollabor) | CLI startup, application orchestration, commands, LLM coordination |
| [packages/kollabor-ai](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-ai) | Providers, profiles, OAuth, prompt rendering, conversation state |
| [packages/kollabor-agent](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-agent) | Tool execution, MCP, permissions, file/shell operations, agents |
| [packages/kollabor-tui](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-tui) | Terminal rendering, input, widgets, fullscreen views, status UI |
| [packages/kollabor-events](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-events) | Event bus, hook registry, executor, processor, event models |
| [packages/kollabor-config](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-config) | Configuration loading and utilities |
| [packages/kollabor-plugins](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-plugins) | Plugin framework and SDK |
| [packages/kollabor-rpc](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-rpc) | RPC protocol and client helpers |
| [packages/kollabor-engine](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-engine) | Local engine/backend service |
| [packages/kollabor-webui](https://github.com/kollaborai/kollab/tree/main/packages/kollabor-webui) | Web UI package |
| [plugins](https://github.com/kollaborai/kollab/tree/main/plugins) | Concrete built-in plugin implementations |
| [bundles](https://github.com/kollaborai/kollab/tree/main/bundles) | Bundled agents, skills, themes, layouts, and widgets |

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

For contribution guidance, see [CONTRIBUTING.md](https://github.com/kollaborai/kollab/blob/main/CONTRIBUTING.md). For agent and
architecture guidance, see [AGENTS.md](https://github.com/kollaborai/kollab/blob/main/AGENTS.md), [CLAUDE.md](https://github.com/kollaborai/kollab/blob/main/CLAUDE.md), and
[docs/architecture/README.md](https://github.com/kollaborai/kollab/blob/main/docs/architecture/README.md).

## Documentation

- [Getting Started](https://github.com/kollaborai/kollab/blob/main/docs/getting-started.md)
- [Configuration](https://github.com/kollaborai/kollab/blob/main/docs/configuration.md)
- [Provider Profiles](https://github.com/kollaborai/kollab/blob/main/docs/providers.md)
- [Models and Loadouts](https://github.com/kollaborai/kollab/blob/main/docs/features/loadouts.md)
- [Reasoning Effort](https://github.com/kollaborai/kollab/blob/main/docs/features/reasoning-effort.md)
- [Setup Wizard](https://github.com/kollaborai/kollab/blob/main/docs/features/setup-wizard.md)
- [Agent System](https://github.com/kollaborai/kollab/blob/main/docs/features/agents.md)
- [ACP Agents](https://github.com/kollaborai/kollab/blob/main/docs/features/acp.md) — Claude Code and Codex as agents, Kollab inside Zed and other ACP clients; Gemini CLI qualification open
- [Apps](https://github.com/kollaborai/kollab/blob/main/docs/features/apps.md)
- [Voice Mode](https://github.com/kollaborai/kollab/blob/main/docs/features/voice-mode.md)
- [Hub Quick Start](https://github.com/kollaborai/kollab/blob/main/docs/guides/hub-quick-start.md)
- [Tasks and Checkpoints](https://github.com/kollaborai/kollab/blob/main/docs/features/tasks.md)
- [Tool Calling](https://github.com/kollaborai/kollab/blob/main/docs/features/tools.md)
- [Attach Mode](https://github.com/kollaborai/kollab/blob/main/docs/features/attach-mode.md)
- [Agent Network Architecture](https://github.com/kollaborai/kollab/blob/main/docs/architecture/agent-network.md)
- [Command Reference](https://github.com/kollaborai/kollab/blob/main/docs/reference/commands.md)
- [Engine](https://github.com/kollaborai/kollab/blob/main/packages/kollabor-engine/README.md) and [Web UI](https://github.com/kollaborai/kollab/blob/main/packages/kollabor-webui/README.md)
- [Tool-output Artifacts](https://github.com/kollaborai/kollab/blob/main/docs/specs/tool-output-artifacts.md)
- [Documentation Index](https://github.com/kollaborai/kollab/blob/main/docs/README.md)
- [Telegram Bridge Setup](https://github.com/kollaborai/kollab/blob/main/docs/guides/telegram-bridge-setup.md)
- [MCP](https://github.com/kollaborai/kollab/blob/main/docs/features/mcp.md)
- [Permissions](https://github.com/kollaborai/kollab/blob/main/docs/features/permissions.md)
- [Plugin Development](https://github.com/kollaborai/kollab/blob/main/docs/plugins/development.md)
- [Troubleshooting](https://github.com/kollaborai/kollab/blob/main/docs/troubleshooting.md)
- [Release Process](https://github.com/kollaborai/kollab/blob/main/docs/release-process.md)

## Support and Security

- Questions and bug reports: [GitHub Issues](https://github.com/kollaborai/kollab/issues)
- Support expectations: [SUPPORT.md](https://github.com/kollaborai/kollab/blob/main/SUPPORT.md)
- Vulnerability reporting: [SECURITY.md](https://github.com/kollaborai/kollab/blob/main/SECURITY.md)

Do not post API keys, OAuth tokens, private conversation logs, raw transcripts,
or local runtime data in public issues.

## License

Kollab is licensed under the [Apache License 2.0](https://github.com/kollaborai/kollab/blob/main/LICENSE).
