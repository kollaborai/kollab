# ACP bridge implementation checkpoint

2026-10-13: the initial implementation was committed as `9a123150`. The
subsequent source audit identified 13 concrete repair items before a replacement
goal was created. Those source repairs are now implemented in separate scoped
commits, recorded below. No tests, builds, lint,
adapter/client runs, browser runs or deployment were performed during this pass,
by explicit instruction. Earlier results below describe an earlier checkpoint
and do not qualify these changes.

## Current qualification pass (2026-10-14)

Executable qualification was subsequently authorized. Full section 9 remains
open; the evidence below distinguishes source checks, controlled SDK runs, real
models, rendered UI and the off-box network boundary.

Verified fixes and boundaries:

- Startup: `ea95af7f`, `d003ee71`, `94511e2f`, `359912ce` and `f1136882`
  resolve pinned managed adapters, preflight before spawning, wait for actual
  daemon readiness and retain actionable errors in the launcher. Claude's quota
  text survives opaque request errors. Missing executables leave no candidate
  daemon or ready engine session. Named profile strings use authoritative daemon
  profile state. The engine/startup suite passed 232 tests; a focused input/state/
  assistant/startup run passed 96. Counts overlap with later focused runs.
- Hub: `8dc42998`, `09cce6ab` and `29f1c38c` repair ordinary request admission,
  distinct request FIFO, completion-owner validation, question-back imports and
  public wait-state tags. Local collaboration, MCP stdio/socket, native reply and
  permission regressions pass. Real Codex adapter 2.1.1 handled an ordinary local
  Hub request, called `hub_status` and `hub_agents`, streamed `ACP LIVE CODEX OK`
  and returned one final message plus one hidden end frame on its original
  request/thread. The corrected application probe exited zero with no owned root
  PIDs. Trace: `/tmp/kacp-live-xVqw6o/codex-application.jsonl`; summary:
  `/tmp/kacp-live-xVqw6o/codex-live-summary.log`.
- Outbound: real Codex cancel, negotiated steering, resume and missing-login
  failure gates pass. Actual permission approve, deny and the complete 300-second
  timeout pass; only approval creates the owned sentinel. Evidence is in
  `codex-protocol.jsonl`, `codex-permissions.jsonl` and their summaries under
  `/tmp/kacp-live-xVqw6o`. `453ca014` rejects non-offered option IDs and empty
  eligible option tables. `f77d3734` journals observed partial output by owned
  request/turn and saves it once on cancellation/error without inventing vendor
  tool status. The latest six-file outbound run passed 87 tests in 35.35 seconds.
- Cleanup: `52fb41d1` recovers same-user, marked descendants orphaned before
  observation, including double-fork/`setsid` children born during prompt and
  close. Wrong-nonce outsiders survive. The real Codex run removed its four
  observed process identities. Its login shell discarded the inherited marker:
  cleanup is not OS containment, and unseen children that strip the marker or
  change user identity remain outside this proof.
- Inbound: `f2e293e1`, `362aea82`, `9715049c` and `14364d69` cover SDK variants,
  request-specific cancellation, attach ownership, saved replay, protocol-only
  stdout and complete handle cleanup. The full 17-case real CLI/daemon/stdio
  matrix passed in 189.52 seconds, with 24 protocol traces and no stray stdout
  lines. After the stopped-history fix, `61504527` added a real active cancel →
  close → fresh load check: partial text, pending/in-progress cards, typed diff,
  falsey output and cancelled parent metadata survive; initial, replacement and
  restored adapter PIDs exit. This new case and the directly affected normal
  restore case passed in 34.89 seconds. Artifacts:
  `/tmp/kacp-inbound-final.33P2UP` and
  `/tmp/kacp-inbound-sdk-stop-fixed.nhBMfF`. The earlier full run predates the
  stopped-history source fix; it is not a claim that all 18 cases were rerun.
- Persistence: `d475fef2`, `168dfe07`, `008fecfa` and `25cdde92` repair native
  argument decoding, exact parent/tool-result matching, resumed base durability,
  typed ACP history producers and sparse card merges. Existing ACP logs lacking
  display metadata can recover it from a matching snapshot by exact UUID or
  unique shared parent UUID; original text/order/tail stay authoritative and old
  files are not rewritten. The replay regression run passed 150 tests plus five
  subtests; later typed history checks passed 20 tests. A copied version of the
  actual old Zircon data recovered five ACP assistant rows and eight cards with
  source bytes unchanged: `/tmp/kacp-olddata-recovery-4f0ez6ss/summary.json`.
- Web UI: `decf3a83` serves the current source bundle; `d54ad450` clears the
  previous cancel header on admitted input; `95eaf915` omits unobserved durations
  instead of displaying 0 ms. `d39c99c3` uses the authoritative harness label for
  intentionally empty harness-managed model fields; actual Claude, Codex and
  native header titles were checked. Typecheck and build pass. Real controlled
  SDK/browser checks pass for startup errors, completed text/diff cards, pending
  permission reload and exact opaque approvals, Stop → reload partial history,
  and a subsequent normal turn. Cancelled cards retain the last vendor status.
  Actual 390px and 820px renders have no horizontal document overflow. Private
  screenshots and DOM evidence are under `/tmp/kacp-web-4xxf6eaj`, including
  `stopped-and-completed-390.jpg` and `stopped-and-completed-820.jpg`. This is
  controlled SDK UI evidence, not a Claude or Gemini model run.
- User runtime: the source engine/Web UI was restarted and the six engine-owned
  sessions were recreated with their previous profiles/identities and saved
  conversation bases. Unrelated external agents were preserved. Backups and the
  verified restore map are under `/tmp/kacp-live-restart-_swfzhr6`. Fresh Codex
  probe `ACP-UI-261014-A` called `hub_agents` and replied in Zircon's real window;
  it was an observation on a still-owned native reply chain, not a new peer
  request. The separate B probe failed at its native sender's rate limit before
  any Hub delivery. Direct CLI operator probe C passed: request/thread
  `0f403e9982d445538d8cd1a11f806ee3`, turn `7`, source `hub_attachment`, one
  parent-linked assistant with `end_turn`, 77 saved updates and a completed real
  roster tool card. Snapshot, HTTP history and JSONL display metadata agree.
  Metadata-only proof is in
  `/tmp/kacp-live-restart-_swfzhr6/live-qualification-summary.json`; the saved
  parent UUID matches the manager's user UUID. Logger-generated message/request
  IDs use a separate namespace and were not used as runtime request authority.
  Neither operator C nor observation A substitutes for the blocked native B gate.
- Network: `99f4e9de` rechecks durable request authority after transport awaits
  and during remote ACP/CLI waits. Seven regressions reproduced hangs before
  the fix and pass afterward; 313 adjacent tests and two manual-grant/fast-final
  safeguards pass. An actual isolated two-host mutual-TLS run, 10.0.0.6 ↔
  10.0.0.7, passed correlated trust denial with zero model admission, in-flight
  revocation in 0.0044 seconds, accepted ask trust change in 0.2458 seconds and
  accepted CLI peer revocation in 0.2479 seconds. Owned probes exited and pending/
  dispatch maps were empty. Existing remote agents, installed source and real
  membership state were unchanged. Evidence:
  `/tmp/kacp-cross-device-wb6byW/qualification_summary.json`, `events.jsonl` and
  `source_sha256.json`. Its remote application handler is controlled; this does
  not qualify a remote ACP provider/harness.

Required open gates:

1. Claude Code adapter 0.89.0: the fresh user Web session reports the weekly limit
   (reset reported as 5am America/Phoenix). Real streamed tool, permission,
   cancel and steering gates remain open. An isolated copied-auth probe also
   reported expired OAuth; neither result counts as a successful model turn.
2. Gemini CLI 0.63.0: initialize passes, but session creation rejects the test
   account's Code Assist individual client and reports an Antigravity migration.
   The model/tool/permission/cancel matrix remains open; this is not treated as
   a generic missing-login success or a reason to change account/billing settings.
3. Actual Zed initialize/prompt/permission/cancel/attach remains unverified.
   Isolated config/proxy artifacts are prepared; Zed's singleton reused its
   existing process, and computer control cannot operate the owned restricted
   test window. No successful ACP client trace exists. User project settings
   and the existing Zed application were preserved.
4. Repeat the separate native Koordinator → Zircon request B after sender capacity
   returns, checking a new request's original reply and terminal IDs. The actual
   sender failed before Hub admission; the earlier A reply-chain result and C
   operator result are recorded separately.
5. Actual cross-device ACP harness collaboration needs a scoped remote runtime
   preparation. Inspected remote installs are 0.14.2 and 0.15.5; current ACP
   modules, SDK/MCP dependencies and pinned adapters are absent. The passing
   isolated network boundary is not remote model proof.

Counts above overlap and are not a combined distinct-test total. Private
artifacts and the owned Zed test folder are retained until qualification evidence
is reviewed and the open client gate is resolved. No push, release, installation
of a Kollab release binary or production deployment occurred. The current
qualification goal is not complete.

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
   `presented`; verified-return checks already reject ordinary presented
   replays, but sibling/leased consumption needs an idempotence guard when
   presentation writes fail. Local redelivery can append an interim reply
   repeatedly.
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

### Repair commit ledger

Source inspection and scoped diffs are the evidence for this ledger. The
13-item source-repair goal is complete; runtime behavior remains unverified.

- R1 source fixed: `ed283dd2`; native live/replay share discriminated SDK helpers.
- R2 source fixed: `92988ec2`; inbound runner enables the required SDK routes.
- R3 source fixed: `ae020bd9`, `cae657a8`, `73baf934`; both queues release
  cancelled slots, native terminals share a private completion fence, and a
  cancelled queued client cannot finish another Web stream or inflate turns.
- R4 source fixed: `1248596b`; ACP restore follows native conversation renaming.
- R5 source fixed: `550e2240`, `947801d3`, `9c2d3aae`, `3409732d`, `f30f9cee`;
  admission/completion and inbound startup/save/close retain cleanup owners,
  partial attachments clean up, failed pool stops retain retry ownership, and
  engine clients receive a correlated visible error before failed completion.
- R6 source fixed: `e0064a22`; local and relay consumption deduplicate by sender,
  ID and action, preserving synthesized reply/end pairs.
- R7 source fixed: `7cc20b53`; immutable question receipts, original-final
  buffering, bounded continuation lifetime and hidden answer end frames.
- R8 source fixed: `c1b38e2f`; fallback scans earlier nonempty text segments.
- R9 source fixed: `e69262da`, `4c457ee3`; retained PID/birth identities, descendant
  refresh during shutdown, bounded grace/termination and explicit survivor
  errors; failed teardown owners remain reachable for retry. Capture of a child
  born, detached and orphaned wholly between polling observations remains
  unverified; this is not proof of complete
  live process containment.
- R10 source fixed: `a85df02f`, `41f28c25`, `d6f02499`, `d27c07ac`;
  explicit resolution ownership, cancellation settlement in the Web transport,
  waiting state cleared at the owned turn boundary, and
  tracked generation-scoped cancellation when no offered selection is valid.
  Timeout still chooses the first offered one-shot rejection when available.
- R11 source fixed: `8f0e414b`, `d27c07ac`, `ed4d64a1`, `84f206c1`; full merged
  cards survive history, live cards update by ID, text/diff content is rendered,
  and terminal parents stop activity without inventing a tool outcome.
- R12 source fixed: `0cfa0293`; authoritative profile events refresh engine
  snapshots, and initial adoption reads cannot overwrite newer profile state.
- R13 source fixed: `4093cf10`; fresh presence overrides cached summaries,
  preserving harness/waiting tags and circular-ask detection.

Source completion does not close the qualification gates: native cancellation
and permission races, process teardown (including detached children), nested
local/remote asks, persistence failure behavior, SDK/client interoperability,
and rendered Web/TUI behavior at 390px/820px have not been exercised. No version
was released or deployed and no commits were pushed. The shared main checkout
was preserved throughout; only explicit repair paths were staged.

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
