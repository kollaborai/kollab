# ACP bridge implementation checkpoint

2026-10-13 implementation pass: source changes are ready for review in the shared
checkout. No tests, builds, lint, adapter runs or deployment are being performed
during this pass, by explicit instruction. Earlier results below describe an
earlier checkpoint and do not qualify these changes.

Current source inventory:

- `kollabor/llm/acp_bridge.py`: protocol negotiation, startup deadlines,
  serialized turns, structured permissions, steering, resume/load and process
  ownership including children that leave the process group.
- `kollabor/llm/acp_orchestrator.py`: daemon/profile ownership, transcript and
  tool-card records, correlated completion, batched observations, plans,
  context usage, permission waiting state and private session bindings.
- `kollabor/llm/acp_state.py`: device-local outbound bindings and stable
  inbound ACP-to-Kollab conversation mappings.
- `kollabor/acp_server.py` and `kollabor/acp_stdio.py`: ACP v1 stdio entrypoint,
  owned versus attached sessions, replay/load/resume, scoped prompt/cancel,
  permission callbacks and protocol-only stdout.
- `kollabor/llm/client_turns.py` and `kollabor_tui.turn_context`: native client
  FIFO admission, request ownership and correlation through daemon events.
- `kollabor/mcp_hub.py` and `plugins/hub/acp_tools.py`: authenticated
  `hub_status`, `hub_agents`, `hub_msg`, `hub_ask` and `hub_spawn` tools, owned
  threads, question-back continuation, bounded waits and circular-ask handling.
- Hub socket/routing/relay source: child-token validation, native bound asks,
  canonical terminal outcomes, typed ask/waiting markers, safe harness labels
  in directories and return delivery while an ACP prompt waits.
- Engine and Web UI source: harness identity, waiting badges, plan/context
  state, opaque permission choices, restored tool results, scoped grants and
  harness-owned model/effort controls. The CLI/status roster names harnesses.
- `FileOperationsExecutor` and `ToolExecutor`: bounded native file snapshots
  on edit/create/append/insert/delete/move/copy results, carried as ACP diffs.
- Native queue and state widgets: ACP usage events derived from native provider
  context limits and token counters, plus persistent harness context/cost state.

Review remains required for every changed surface. Real qualification remains
the acceptance bar in section 9 of the canonical spec; this source inventory
does not claim that any harness, ACP client, network run or UI run has passed.

## Review handoff

directive: implement the remaining ACP v1 source for the canonical spec, with
testing, builds, lint and live qualification deferred for later reviewers.
Read `bundles/skills/kollab-development/SKILL.md` before editing Kollab; network
review also requires its `references/relay-change-guide.md`.

execution: outbound lifecycle and bindings, Hub collaboration tools, network
returns, inbound stdio sessions, request correlation, file diff projection and
terminal/Web UI surfaces have implementation files in the shared checkout.
The source pass is recorded in Git and remains unqualified. Preserve unrelated edits and concurrent commits;
do not stash, reset, clean, switch branches or create a worktree here.

pivots: reuse SDK 0.12.1, the native daemon, native permission ownership and the
Hub/relay paths. ACP turns bypass native provider execution and HUD injection.
Inbound ACP submits into the daemon rather than introducing another model loop.

landmines: outbound turn generations and Hub child credentials are distinct;
process/session restart rotates the child binding. Native client turns inherit
correlation through `kollabor_tui.turn_context`; a request ID must remain on its
permission and terminal events. Attached EOF closes the connection only; owned
EOF stops its daemon. Relay fields `ask`, `waiting`, harness labels and terminal
outcomes require the current peer implementation; they confer no trust.
Local hidden terminal frames carry the hosting daemon's sender identity;
authenticated remote endpoints use their verified identity instead.

oversights corrected during this pass: ACP replies/broadcasts now enter the
observation batch before native HUD injection; steering does not acquire a
second final-answer owner; native stop strings such as `stop` and `length` map
to ACP outcomes; file snapshots are propagated into semantic tool metadata.

yield: the source pass is ready for independent review and qualification. The
following ownership boundaries need review; none has been executed as proof
during this pass:

1. Outbound runner/orchestrator/application: SDK calls, restart/clear/resume,
   FIFO ownership, steering, transcript ordering and final versus interim text.
2. Hub/MCP/socket/native bound asks: token/generation fencing, thread handles,
   question-back continuation, cancel/timeout, handoff and final/end settlement.
3. Relay/directory: authenticated source and target, room/trust/grant checks,
   request correlation and mid-turn delivery of answers and waiting notices.
4. Inbound server/stdio/client broker/state RPC: load/replay/resume, owned versus
   adopted daemons, client MCP isolation, mode persistence and scoped cancel.
5. Engine/TUI/Web UI/permissions: opaque options, reusable operation grants,
   prompt settlement across windows, harness labels, context/cost, plan state,
   restored cards and small-screen rendering.

Full diff cards are captured for UTF-8 text files up to 256 KiB. Larger or binary
files retain the tool result instead. Model selection/config options, multimedia
prompts, notices and ACP v2 remain the separate future scope in section 10.

Section 9 remains the acceptance gate: real Claude Code, Codex and Gemini
adapters; native/harness questions and circular asks; remote trust and replies;
an actual ACP client; cancellation/EOF; and real terminal/Web UI approvals.
Earlier fake/unit results below must not be reused as proof of this source pass.

## Earlier checkpoint

This document records the current implementation checkpoint; the canonical
phase contract remains in `docs/specs/acp-bridge.md`.

implemented in commits 22da746d, 854d3dbb, 557fdebe, fca1178f,
30ddd1b0, 02a7b848, 0f112962, a51d658d, ebbf1e2f, and e6951da0:

- portable `ACPProfile` command/cwd/workspace/env/pass_env resolution with
  provider-key stripping and device-local executable lookup
- built-in portable `claude-code`, `codex-cli`, and `gemini-cli` profiles with
  pinned install guidance for missing adapters
- device-local `~/.kollab/acp/resolved.json` cache recording command identity,
  symlink-preserving executable directory, and optional Node resolution;
  vanished or changed command paths are re-resolved, and adapter/Node
  directories prefix the child PATH
- pinned `agent-client-protocol==0.12.1` dependency and typed SDK connection
  for initialize, session/new, prompt, session updates, permission callback,
  cancel, process-group cleanup, and stderr draining
- FIFO prompt queue bounded at ten entries, process/session generation fencing,
  per-turn IDs, cancel rebasing for queued turns, typed final answer, and a
  correlated hidden turn-end frame
- fail-closed permission bridge using nested `AllowedOutcome`/`DeniedOutcome`,
  offered opaque `optionId` validation, timeout/cancel settlement, and
  allow_once/reject_once handling
- standard MCP SDK initialize/initialized/tools/list/tools/call server exposing
  `hub_status`; every call rechecks a parent AgentSocketServer binding over a
  temporary Unix socket and receives the current roster payload
- CLI flags `--acp-command`, `--acp-profile`, `--acp-prompt`, and hidden
  `--mcp-hub`; SDK fake, queue/cancel, permission, parent binding, revocation,
  and MCP ClientSession tests

validation checkpoint:

- 24 ACP bridge/profile tests pass, with focused ACP/MCP/CLI/input suites green
- `ruff`, `py_compile`, and `git diff --check` pass on changed files
- `kollab --version`, `kollab --hub status`, and live MCP ClientSession
  initialize/list/call plus parent roster-revocation smoke pass
- instrumented startup proof confirms ACP profiles skip native API/provider init
  (0/0 calls), while a native profile retains both calls (1/1)

remaining phase-1 gates at that earlier checkpoint (historical):

- canonical `--as`/`--llm` daemon/profile integration is not wired; current CLI
  flags are a one-shot harness path only
- real streaming/history/tool-card mapping, immutable origin/Hub final/end
  frame, lifecycle/steering/resume behavior, permission windows/grants/waiting
  line, and a real adapter run still need implementation/qualification
- installed `claude` and `codex` binaries report versions but have not been
  qualified as ACP adapters; no login/billing or fake proof substitutes this
- inbound ACP, hub message/ask, network routing, Web UI integration, reusable
  grants beyond the phase-1 option bridge, and broad profile support remain
  later phases in the canonical plan
