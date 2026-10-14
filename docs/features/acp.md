# ACP agents

Kollab can run an ACP harness as an agent and expose a Kollab daemon to an ACP
client. Real Codex model/tool/permission runs, CLI/SDK lifecycle checks and Web
UI saved-card flows pass. Full qualification remains open: Claude is quota-
blocked, Gemini rejects the pinned client for the test account, and actual Zed
and remote ACP harness runs remain unverified. See the implementation checkpoint
linked below for exact evidence and remaining gates.

## Run a harness on the Hub

Install the harness, log in on this device, and install its ACP adapter using
the pinned guidance shown by `/llm`. Built-in profiles are `claude-code`,
`codex-cli`, and `gemini-cli`.

```sh
kollab --as claude --llm claude-code --detached
kollab --attach claude
```

The harness owns its model, login, tools and context. Kollab owns the Hub name,
windows, transcript, permission UI and routing. Provider API keys are stripped
from the child environment unless the portable profile explicitly lists them
in `pass_env`. Executables are resolved independently on each device.

Custom profile example:

```json
{"provider":"acp","command":["my-agent","--acp"],"pass_env":[]}
```

The harness receives `kollab mcp hub` as a stdio MCP server. Its tools are
`hub_status`, `hub_agents`, `hub_msg`, `hub_ask`, and `hub_spawn`. Incoming
requests carry an owned thread; their final answer is returned automatically.
Replies and broadcasts do not get automatic replies. Steering is used only
when negotiated; otherwise follow-ups wait for a new turn.

Approvals use the offered opaque option IDs. Reusable grants are scoped to
the structured operation, workspace and harness profile. A vendor's saved
approval remains the vendor's setting and cannot be undone by Kollab.

## Use Kollab from an ACP client

```sh
kollab acp
kollab acp --llm my-profile
kollab acp --attach lapis
```

Example Zed configuration:

```json
{"agent_servers":{"kollab":{"type":"custom","command":"kollab","args":["acp","--attach","lapis"]}}}
```

An owned session starts a headless Kollab daemon in the client's folder. EOF
stops that daemon. An attached session requires one matching local agent and
folder; EOF detaches and leaves that agent running. A client's cancel targets
its own request, including while queued, rather than whichever turn the mesh
happens to be running.

Owned sessions accept client stdio MCP servers and process-scoped approval
modes. Attached sessions ignore those MCP servers and refuse mode changes.
ACP v1 does not show turns started by the mesh outside the client's prompt;
Kollab's own windows continue to show them. Prompts currently accept text.

`session/load` replays saved text, thoughts and tool cards; `session/resume`
restores context without replay. Native file edits include diff cards for UTF-8
text files up to 256 KiB; larger and binary files show their tool result.

For the implementation contract and qualification gates, see
[`../specs/acp-bridge.md`](../specs/acp-bridge.md).
The current evidence and open checks are recorded in
[`../specs/acp-bridge-phase1.md`](../specs/acp-bridge-phase1.md).
