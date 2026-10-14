# ACP agents

Kollab can run an ACP harness as an agent and expose a Kollab daemon to an ACP
client. Real Claude Code and Codex model/tool/permission/cancel/steering/resume
runs, CLI/SDK lifecycle checks, Web UI saved-card flows and an isolated
cross-device real Codex request/reply and revocation run pass. Gemini's
missing-auth startup and clean runner-close checks
also pass. Claude's full 300-second permission timeout and missing-login
`-32000` checks pass; its normal empty-store session opens before the first
controlled prompt rejects authentication. Full qualification remains open for
Gemini's supported-auth positive rows and fresh real-provider Web UI and
interactive terminal walkthroughs. Actual Zed Preview
client checks pass with a controlled local SDK backend: normal/approval/absolute
diff, partial UI Stop, attached denial/retention and reconnect, then owned
physical shutdown after `4d4ceff1`. The earlier orphaned-daemon failure remains
recorded separately. Native tool wire IDs are session-unique in `79e13257`.
A new private native Koordinator → Codex Zircon round trip now passes ordinary
waited-message delivery, correlated final/end and native model feedback. The
same configured Z.AI route returned two actual HTTP 200 responses. Independent
offline validation qualifies that frozen runtime evidence; the controller's
observer assertion remains recorded as failed. The earlier user B/B2 and
restored histories were untouched. Fresh actual Claude browser approval/denial
and Codex browser/attached-terminal permissions, typed reply and active Esc also
pass. Terminal answer ownership and diagnostic overwrites were fixed in
`917a434d` and `ad53626e`. Long permission choices now wrap in `d048763e`;
`2b81ebf6` and `f128b4d0` keep Stop available for owned pending permissions and
send the exact parent request to the daemon. Controlled browser Stop/reload,
partial retention, a normal follow-up and 390px/820px layouts pass. The complete
UI matrix still needs actual-provider Stop/reload and the remaining Claude
terminal checks.
See the implementation checkpoint linked below for exact evidence and remaining
gates.

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
Attached handles belong to one connection: a fresh connection opens a new
attached session instead of resuming the old handle. Attached sessions advertise
close only; load and resume remain available for owned sessions.

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
