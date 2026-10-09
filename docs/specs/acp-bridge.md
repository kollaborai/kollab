# Kollab speaks ACP

Status: **draft**, 2026-10-09. Not built yet. In Marco's words: "if all harness
support acp and a kollab agent needs to interact with a claude code agent or a
codex agent, kollab agents need to be able to speak acp … we don't need to
replace the kollab protocol … if we can bridge ACP with what kollab has, other
non kollab agents can join the mesh."

Kollab keeps its own protocols: the hub socket, attach, the network and the
engine API. It adds the [Agent Client Protocol](https://agentclientprotocol.com)
(ACP) in both directions, as the bridge to every other harness:

- **Outbound.** Claude Code, Codex, Gemini CLI or any ACP agent runs as a kollab
  agent: a name in the roster that gets and sends hub messages, opens in every
  window, and is reachable as `agent@device` on the network.
- **Inbound.** `kollab acp` makes kollab an ACP agent, so Zed, JetBrains, Buzz or
  any ACP client can drive a kollab agent.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/acp-bridge/overview-dark.svg">
  <img alt="Kollab speaks ACP both ways: Zed, JetBrains, Buzz or any ACP client drives a kollab agent through kollab acp, and kollab runs Claude Code, Codex and Gemini CLI as hub members over ACP; the network makes every one of them reachable from your other devices." src="../diagrams/acp-bridge/overview-light.svg">
</picture>

The diagrams come from [`scripts/build_acp_diagrams.py`](../../scripts/build_acp_diagrams.py).
Change a label there when this spec changes, then run it.

## 1. What you can do

| You can | How |
|---|---|
| Run Claude Code, Codex or Gemini CLI as a mesh member | `kollab --as claude --llm claude-code --detached` |
| Have any agent ask it something | "lapis, ask claude to review my diff": `hub_msg to="claude"`. Its final answer returns on lapis's thread |
| Let it start conversations | its kollab hub tools: `hub_msg`, `hub_ask` (waits for the answer), `hub_status`, `hub_agents` |
| Chat with it yourself | terminal attach, web UI, phone: it is a kollab agent |
| Reach it from another computer | `hub_msg to="claude@home-server"`. That device's trust decides |
| Approve its tools in kollab | its permission requests are kollab prompts, under your approval mode |
| Stop it, clear it, resume it | Esc or Stop, `/clear`, `/resume` |
| Run kollab agents inside Zed, JetBrains or Buzz | `kollab acp`, or `kollab acp --attach lapis` |

## 2. What you can't do

| You can't | Why | Instead |
|---|---|---|
| Join a Claude Code window already open in a terminal | ACP starts its own agent process | start it under kollab. `/resume` picks up a stored session when the agent supports it |
| Pick its model, effort or billing from kollab | they belong to the harness and its login | set them in the harness |
| Gate every tool it runs | it asks only where its own rules say to, and kollab's config hooks never see its tools | keep its allow list tight and use its own hooks |
| Give it kollab's system prompt, skills or memory | ACP has no system prompt field | a short preface goes in its first prompt; its `CLAUDE.md` or `AGENTS.md` still apply |
| Let it read the whole open channel | it gets only your input and messages addressed to it | message it, or broadcast |
| Let it use kollab's tools | it runs its own tools | it gets kollab's hub tools only |
| Interrupt a running turn with a message | ACP v1 runs one prompt at a time per session | the message waits for its next turn; Esc stops the turn |
| Run ACP across the internet | ACP's only finished transport is stdio; its remote transport is a draft | the kollab network carries `claude@device`, and ACP stays on each machine |
| Share its login between devices | the sealed config carries kollab's settings, not other harnesses' logins | log in once on each device that runs it |

Why these split where they do: kollab owns the agent, the harness owns the
brain, and ACP is the only wire between them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/acp-bridge/ownership-dark.svg">
  <img alt="What kollab controls and what the harness keeps. Kollab owns the agent: its name, hub messages, network trust, windows, permission prompts and transcript. The harness owns the brain: model, login, context, tools, its own permission rules, hooks and cost. ACP carries session/new, session/prompt, session/update, session/request_permission, session/cancel and the stop reason between them, and the harness calls back through kollab mcp hub." src="../diagrams/acp-bridge/ownership-light.svg">
</picture>

## 3. Setup

Install each harness's ACP command once, on each device that runs it, and log
in with the harness itself. Kollab never runs `npx`, so an agent starts on the
version you installed, offline and under a service manager.

| Profile | Install (versions checked 2026-10-09) | Log in |
|---|---|---|
| `claude-code` | `npm install -g @agentclientprotocol/claude-agent-acp` (0.89.0) | `claude`, then `/login` |
| `codex-cli` | `npm install -g @agentclientprotocol/codex-acp` (2.1.1) | `codex login` |
| `gemini-cli` | `npm install -g @google/gemini-cli` (0.63.0), run as `gemini --acp` | `gemini`, then sign in |

The three ship as profiles in `kollabor.llm.profiles`. `/llm` lists them under
"ACP agents", and one whose command is missing shows its install line instead
of a model list. Your own:

```json
"my-agent": {"provider": "acp", "command": ["my-agent", "--acp"], "pass_env": ["ANTHROPIC_API_KEY"]}
```

- `command` is resolved to an absolute path when the profile is saved, and the
  child's `PATH` starts with that command's directory, so a node-based adapter
  finds its `node` under `kollab service` too.
- The child gets your environment minus provider keys (`ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`):
  an exported `ANTHROPIC_API_KEY` silently switches Claude Code from your
  subscription to API billing. `pass_env` names the ones to keep. Kollab never
  passes a key it stores.

Start one: `kollab --as claude --llm claude-code --detached`. `--as` names it
on the hub. No `--agent`: a kollab bundle's system prompt means nothing to
another harness.

## 4. Outbound: an ACP agent as a kollab agent's brain

The kollab agent keeps everything a kollab agent has: identity, presence, the
hub socket, attach from the terminal, web UI and phone, history, `/resume`, the
network. Only its turns change: an **ACP turn runner** beside the LLM
coordinator runs them on the harness instead of a model API. Hub routing does
not change, because `claude` is an ordinary agent name.

The runner is not a provider under the tool loop. The harness runs its own
tools, so its text is display and history only: kollab never parses it for
tags, and `<terminal>` quoted in a reply stays text. Kollab's tools reach the
harness only through `kollab mcp hub` (section 6).

### One request, start to finish

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/acp-bridge/sequence-dark.svg">
  <img alt="One request in 11 steps: you ask lapis to have claude review your diff; lapis sends a hub message; kollab turns it into an ACP prompt for Claude Code; Claude Code streams tool cards and asks permission to run the tests; you approve in kollab's prompt; Claude Code ends its turn, kollab sends its final text to lapis on lapis's thread, and lapis tells you what claude found." src="../diagrams/acp-bridge/sequence-light.svg">
</picture>

### The session

On its first turn the runner starts the command in the agent's folder and
sends `initialize`: protocol 1, client info `kollab`, `session.notices`, and no
`fs` or `terminal` capability, so the harness uses its own tools. Then
`session/new` with the folder as `cwd` and `kollab mcp hub` in `mcpServers`.
The harness keeps the conversation; kollab never resends it.

| Kollab | ACP |
|---|---|
| a turn | `session/prompt` with only what is new (below) |
| Esc or Stop | `session/cancel`; pending permission prompts answer `cancelled` |
| `/clear` | a new session |
| `/resume` | `session/resume` when the agent advertises it (no replay); else `session/load`, whose replay of the old conversation is swallowed so nothing shows twice; else a new session: the history shows, the agent starts fresh |
| the process dies | the turn fails with its exit code and last stderr line; the next turn starts it again and resumes the session when it can |
| the daemon stops | the process is closed, then terminated |
| login missing | `session/new` fails with `-32000` (authentication required); the turn shows the agent's own login instructions |

### What the harness sees

The first prompt of each session opens with a preface: its name on the mesh
(`claude`, and `claude@laptop` on the network), that its final answer goes back
to whoever messaged it, and that its kollab tools message or ask the other
agents. After that, a prompt carries only:

- what you typed, as is;
- a hub message to it or to everyone, as `[lapis] text`.

Messages between other agents (the open channel), the HUD after the preface,
vault rebirth, working memory, crystal and scratchpad nudges and kollab's system
prompt never reach it. Compaction and dreaming are off: the harness manages its
own context.

### The answer goes back

When a hub message starts the turn and the harness sent nothing on that thread,
its final text is the reply: the runtime sends it to the asker on the asker's
thread. A network request already works this way (`_end_network_turn`); for an
ACP agent it applies to local requests too. A harness does not know kollab's
conventions, so the runtime returns its answer.

### Updates

Every update is display and history, never a tool kollab runs:

| `session/update` | Kollab |
|---|---|
| `agent_message_chunk` | the streamed reply |
| `agent_thought_chunk` | thinking |
| `tool_call`, `tool_call_update` | tool cards: title, kind, status, diff or output |
| `plan` | the plan, as a list |
| `usage_update` | context use and cost in the status bar, when the agent sends them |
| `notice` | a system line |
| the rest | logged |

Stop reasons: `end_turn` ends the turn; `cancelled` is a stopped turn;
`max_tokens`, `max_turn_requests` and `refusal` end it with a line naming the
reason.

## 5. Permissions

`session/request_permission` is kollab's own permission prompt, in every window
attached to the agent, under its approval mode. No new allow list. Another
agent never answers it, the same rule as for network messages.

| Kollab | ACP answer |
|---|---|
| `a` approve once | the agent's `allow_once` option |
| `s` session, `p` project, `A` always edits, `t` trust tool | `allow_once`, and kollab remembers the grant as it does for its own tools, keyed on the call's kind and title |
| `d` deny | `reject_once` |
| Esc, or the turn is cancelled | `cancelled` |
| nobody answers in 5 minutes | `reject_once`, as kollab's own prompts time out |
| `trust_all` | `allow_once`, no prompt |

Kollab never picks `allow_always`: the harness would save that choice in its
own settings, where `/permissions clear` cannot undo it.

The harness asks only for what its own rules say needs asking. Its allow list
(Claude Code's `permissions.allow`, for one) runs without asking kollab.

## 6. Hub tools for the harness: `kollab mcp hub`

ACP agents must support stdio MCP servers and should connect to the ones the
client lists. Kollab lists `kollab mcp hub`, a stdio MCP server that talks to
the hosting daemon over its hub socket. The daemon runs each call through its
own `hub_msg` path, as the agent: dedup, threads and network trust apply as
they do to any agent.

| Tool | Does |
|---|---|
| `hub_msg(to, message)` | sends; returns once the hub took it |
| `hub_ask(to, question, timeout=300)` | sends, then waits for the first reply on that thread and returns it. On timeout it says the answer will arrive as a new message |
| `hub_status()` | who is online, here and on the network |
| `hub_agents()` | names it can message |

`hub_msg` has no `wait`: an MCP call cannot end the harness's turn. `hub_ask` is
the way to ask and use the answer in the same turn. `to` takes `name` or
`name@device`.

## 7. Across machines

`claude@home-server` is an `agent@device` like any other. The network delivers
it and the far device's trust decides (`docs/specs/agent-network-simple-flow.md`
section 4). ACP stays on each machine: kollab's network carries the message,
and home-server's kollab hands it to Claude Code over stdio.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/acp-bridge/network-dark.svg">
  <img alt="Across machines: lapis on the laptop sends a hub message to claude@home-server; the network carries it sealed, home-server's trust decides, and home-server's kollab hands it to Claude Code over ACP on stdio. ACP never leaves home-server." src="../diagrams/acp-bridge/network-light.svg">
</picture>

A permission prompt goes to every window that has claude open: home-server's
own, and under `open` trust any member device's web UI.

## 8. Inbound: `kollab acp`

`kollab acp` is an ACP agent over stdio, for any ACP client. In Zed:

```json
"agent_servers": {"kollab": {"type": "custom", "command": "kollab", "args": ["acp", "--attach", "lapis"]}}
```

- `kollab acp` starts a kollab session in the client's `cwd`;
  `kollab acp --attach lapis` drives that running agent, as `kollab --attach`
  does, so the client and the mesh share one lapis.
- `initialize` advertises `loadSession` (kollab replays its history),
  `sessionCapabilities.resume`, and text prompts.
- `session/new`: the client's `mcpServers` join that new session. An attached
  agent keeps its own servers, since other windows share it.
- `session/prompt` runs a kollab turn and streams `agent_message_chunk`,
  `agent_thought_chunk`, `tool_call` and `tool_call_update` from kollab's tool
  cards (file edits as `diff` content), and `usage_update`.
- Kollab's permission prompts become `session/request_permission` to the client.
- `session/set_mode`: kollab's approval modes are the ACP modes.
- `session/cancel` stops the turn.
- Kollab edits files on disk with its own tools and never calls the client's
  `fs` or `terminal` methods.

Limit: a turn the mesh starts (another agent wakes lapis) runs outside any
`session/prompt`, and ACP v1 has no place for it. The client gets a `notice`
naming the sender when it advertises `session.notices`; the full turn shows in
kollab's own windows. ACP v2's draft separates prompt acceptance from turns,
which would fix this.

## 9. Build order and proof

1. **Outbound core** (sections 3 to 5): the turn runner, the profiles, the
   preface and prompt allowlist, the reply on the thread, resume, permissions.
   Unit tests drive a fake ACP agent built on the SDK's agent side: streaming,
   tool cards, approve, deny, cancel, timeout, a crashed process, a load
   replay. A tmux spec runs a kollab agent on the fake and answers its prompt
   with `a`. Live: lapis asks claude through `claude-agent-acp` on this
   computer. About 2 to 3 days.
2. **`kollab mcp hub`** (section 6). Live: claude uses `hub_ask` to ask lapis
   and uses the answer in the same turn. About 1 to 2 days.
3. **Across machines** (section 7). Live: `claude@home-server` from the laptop,
   its prompt approved in the laptop's web UI under `open`. About 1 day.
4. **`kollab acp`** (section 8). Live in Zed, then Buzz. About 2 days.
5. **Web UI**: which harness an agent runs on, beside its name. Half a day.

A clean transcript is the bar on every live run. Python SDK:
`agent-client-protocol` 0.12.1 on PyPI (import `acp`, Apache-2.0, Python 3.10
to 3.14), which has both sides: `connect_to_agent` and `Client` for the runner,
the agent base classes for `kollab acp`.

## 10. Open

- Model choice: when an agent advertises its model as an ACP config option,
  `/model` could set it through `session/set_config_option`.
- More profiles (goose, opencode, Cursor CLI) once their commands are checked.
- Images and files in prompts, both ways.
- ACP v2, in draft since 2026-07-20: revisit when it is stable.
