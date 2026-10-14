# ACP bridge implementation checkpoint

2026-10-13 implementation pass: source changes were committed as `9a123150`.
The subsequent source audit found implementation blockers; the source pass is
incomplete. No tests, builds, lint, adapter runs or deployment were performed
during this pass, by explicit instruction. Earlier results below describe an
earlier checkpoint and do not qualify these changes.

## Audited repair goal after 9a123150

The source audit is complete for this repair scope. It compared the canonical
spec's current sections 3-8 with the outbound runner/orchestrator, native client
broker, inbound server, Hub/MCP/local and relay returns, permission bridge,
conversation state, engine projections and terminal/Web UI consumers. It also
inspected the installed SDK 0.12.1 schema, connection and router implementation.
These are source findings, not reproduced runtime results. Section 9's real
qualification and section 10's future features are separate from this goal.

The goal is to implement the following 13 concrete repairs, with small scoped
commits and source evidence for each. All items are open at goal creation.

1. **R1: Valid inbound native tool notifications.**
   `kollabor/acp_server.py:_tool_start/_tool_result` return `ToolCall` and
   `ToolCallUpdate`, without the notification discriminator. SDK
   `AgentSideConnection.session_update` requires `ToolCallStart` and
   `ToolCallProgress`. Emit those notification variants with `sessionUpdate`
   for both live native tools and saved native replay. Retain `ToolCallUpdate`
   for permission request cards, where it is the correct SDK model.
2. **R2: Reachable advertised inbound resume/close routes.**
   `KollabACPAgent.initialize` advertises resume/close, but `main` uses
   `acp.run_agent` with its default unstable routes disabled. SDK 0.12.1 marks
   both routes unstable and refuses them with method-not-found. Enable the
   required routes so capability declarations and dispatch agree; preserve
   attached-session restrictions and unknown-method errors.
3. **R3: Prompt cancellation releases its own queue slot and settles once.**
   `ClientTurnBroker.cancel` marks queued items but waits behind the active
   client before publishing their terminal event. `ACPRunner.cancel_queued`
   cancels the future but leaves the item occupying the ten-slot FIFO. Remove
   and settle queued requests promptly in both paths. Fence active native
   cancellation so the broker's immediate terminal and the later native
   completion cannot count as two terminals. Preserve FIFO and other owners.
4. **R4: Resume bindings follow the continued conversation ID.**
   `LocalStateService.resume_conversation` restores ACP before allocating the
   continued conversation's new native ID; `ACPOrchestrator.start` captures
   the earlier ID. Load from the original binding, then persist subsequent ACP
   bindings under the continued conversation ID. Keep profile/cwd/command
   matching and fresh-context fallback intact.
5. **R5: Persistence cannot strand admissions or skip owned cleanup.**
   `ACPOrchestrator.admit` queues before recording the user, and registers
   completion only afterward. `_complete` persists outside its error handler;
   `close` marks closed and saves before runner teardown. Inbound
   `new_session/close_session` also save before owned cleanup is guaranteed.
   Ensure every admitted request retains a completion owner and a correlated
   terminal failure even if history/binding writes fail. Teardown must run
   despite those errors, preserving the underlying error and attached daemons.
6. **R6: Exactly-once local and relay return consumption.**
   `HubPlugin._on_message_received` consumes ask replies before message-ID
   deduplication. Relay `_rpc_event` consumes verified returns before checking
   `presented`. Redelivery can append the same interim reply repeatedly.
   Deduplicate before mutable wait/receipt handling in both paths, after
   authorization. Preserve distinct reply/end events, ordered `earlier`, and
   normal treatment of legitimate late replies.
7. **R7: Question-back ownership and bounded continuation lifetime.**
   `ACPHubTools.questions` stores the old wait but no immutable question ID;
   answers use the latest peer ID instead. A subsequent peer message can change
   the reply target. Relay `_rpc_event` can consume a question without
   `observe`, leaving a new question thread unowned. `stop` retains question
   state, and question entries are not bounded. Store the exact question's
   ID/thread/peer and original wait; register verified remote receipts before
   returning their handles. Bound and expire continuation state, clear it on
   session retirement, and send question-answer end frames as hidden terminal
   actions. Both `hub_msg` and `hub_ask` must answer the correct question;
   `hub_ask` must then wait for the original request's final answer.
8. **R8: Final-answer fallback survives consecutive tools.**
   `ACPRunner._select_answer` searches only between the last two tool starts
   when no text follows the last tool. A stream `text, tool A, tool B` produces
   an empty answer although text exists before B. Select the last nonempty
   assistant text segment before the final tool when no later text exists;
   retain message-ID selection and exclude thoughts, tool output and interim
   Hub messages.
9. **R9: Owned process cleanup survives parent exit and late child birth.**
   `ACPRunner._terminate_process` snapshots descendants only while the adapter
   is alive, before SDK/session close. Already orphaned `setsid` children and
   children created during close escape that snapshot. Retain descendant
   ownership while the adapter lives, refresh it through teardown, and use
   process identity checks so reused PIDs are never signalled. Apply the spec's
   bounded five-second graceful/termination stages and reap owned survivors on
   clear, close, cancel timeout, startup failure and crash/restart.
10. **R10: Permission cancellation and resolution retain turn ownership.**
    `_acp_permission_decider` can return `None` on callback error/cancellation;
    `ACPPermissionBridge` can do so on outer timeout. `_request_permission`
    returns a cancelled outcome without universally arranging session cancel.
    Also `resolution_event` drops explicit request/turn fields from details,
    while SDK callbacks run outside a native turn context. Stop the matching
    generation when a valid selection cannot be made and preserve explicit
    ownership on resolution events so other ACP clients/windows close their
    prompts. Keep opaque options, exact grants and first-answer-wins behavior.
11. **R11: Merge live and restored harness tool cards in the Web UI.**
    `ACPOrchestrator` produces merged `tool_call_update/acp_update`, but engine
    `assistant_transport` handles only tool start and terminal result. It drops
    intermediate title/kind/status/content updates; terminal structured content
    is rendered as generic JSON. Carry the merged card by tool ID through the
    live transport and history projector, displaying text/diff output and
    current status without executing harness tools or duplicating cards.
12. **R12: Adopted/CLI-switched harness identity stays current.**
    `EngineSession._read_agent_facts` reads a profile once, `_track_events` uses
    `acp_session` only to clear state, and `list_sessions` refreshes names but
    not registered sessions' provider/harness. Switching profiles from an
    attached CLI can leave the Web UI's harness label/model controls on the
    prior provider. Update authoritative profile/harness state on changes,
    including transitions back to native, without persisting client modes.
13. **R13: Roster merging preserves circular-ask detection.**
    `HubPlugin._acp_roster` appends `_roster` summaries after full presence rows
    and keeps the last row per identity. `PresenceManager.get_roster_summary`
    omits tags, erasing peers' `acp_ask_waiting`, harness and approval-waiting
    metadata. Merge summaries without discarding current presence tags so
    asking an agent waiting on another ask returns `busy`, while the original
    requester remains eligible for a question back.

Execution ownership: outbound bridge/process repairs; Hub/relay/continuation
repairs; inbound protocol/broker and engine/Web UI projection repairs; root
conversation/persistence/permission integration and documentation. Only the
root stages and commits, one coherent repair at a time, with explicit paths.
All agents use the shared main checkout and preserve concurrent work.

Completion of this goal means the 13 source repairs are implemented, committed
and individually recorded below with source evidence. It does not qualify ACP
for production. By Marco's instruction, do not add/run tests, builds, lint,
adapter/client runs or live qualification. Those checks remain deferred;
never reuse the earlier checkpoint's results as proof of this repair pass.

## Source inventory

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
