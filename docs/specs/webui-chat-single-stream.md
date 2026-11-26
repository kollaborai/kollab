# Web UI chat: one transcript, one stream

Issue: #1451 (with #1371 and #1452)  
Status: Draft  
Owner: Kollab daemon, engine and web UI

## Problem

The web UI chat redraws in ways the reader sees: a sent message in two bubbles, a reply that blinks out, a `2 / 2` branch picker, a blank pane with the scroll position reset, and a working state that outlives the turn.

Recorded with `npm run record:chat` on `main` at ed585ba3e against a codex-cli (ACP) agent, every animation frame sampled:

| flow | result |
|---|---|
| plain, tools, busy, other, watch, read, three, reload | clean |
| cut | sent message in two bubbles; three rows fade in at once; working state still on 4 minutes later |
| cuterr | sent message in two bubbles; three rows fade in at once |
| watchsend | sent message in two bubbles |
| cutwatch | a raw "Stream ended abruptly" error box stays after the turn ends (#1452) |

Causes found:

- The page has three sources for one conversation: its own `/assistant` stream for the run it started, `/history` pages, and the session events stream for everything else (other clients, the agent's terminal, hub notes, compaction). `packages/kollabor-webui/frontend/src/runtime.tsx` (about 2,000 lines) reconciles them by guessing how rows line up.
- When it cannot prove they line up, six call sites remount the whole thread (`onStale`, then a new `runtimeEpoch` key, #1451).
- assistant-ui's transport runtime (`useAssistantTransportRuntime`, marked `@alpha` in its source) feeds the store a flat `messages` list. On that path the store never deletes a row, so a row that comes back under a new id stays as a branch.
- An input steered into a running ACP turn is saved under a new id when no request id reaches the steering call: `kollabor/llm/acp_orchestrator.py:1375` (`record_request_id = request_id or f"steering-{uuid.uuid4().hex}"`). In `cut` and `watchsend` the saved row carries `steering-<hex>` instead of the page's id, so the page cannot pair it with the bubble it shows, and the request never gets a terminal event (#1371).
- In `cuterr` the input kept the page's id end to end and still showed twice half a second after the send: a page-side duplicate, not traced further.
- The native loop saves inputs that arrive together as one row, so one row can stand for several sends.

## Goal and non-goals

Goal: the daemon keeps one transcript per conversation in which every row has a permanent id, every client follows it through one ordered stream, and the web UI mirrors that stream. The contract is the same for the native loop and for ACP harnesses (codex-cli, claude-code).

The sync layer is written from scratch rather than patched: new files carry the stream and the store, the reconciliation in `runtime.tsx` is deleted rather than ported, and the switch from old to new is one change at the end. The chat's display components (bubbles, markdown, tool rows) stay.

Non-goals:

- How inputs reach the model. Several inputs may still reach the model in one step; the transcript keeps one row per input.
- New chat design. Bubbles, labels and layout stay as they are, apart from the open question below and one removal: the follow-up suggestion row and the new-chat suggestion chips go, because no agent kind produces suggestions (the new-chat composer sits about 7px lower). Edit and Reload stay hidden, as they are today: neither works on any agent kind, and edit and regenerate need their own spec.
- Cross-device merging. The daemon is the only writer of its transcript.
- The daemon-owned waiting queue itself, which is `docs/specs/webui-daemon-queue.md` (#1366). This spec relies on it for admission and adds ids, lifecycle events and the stream.

## What people see

- A sent message appears at once and stays one bubble. Queued, joined to the running turn, or started as its own turn, it is the same bubble updated in place.
- The working state ends when the turn the message started or joined ends, in every client.
- Another client's message, a prompt answered in the agent's terminal, a hub note or a compaction updates the open chat in place. The pane does not go blank and the reader's scroll position holds. Only switching chats remounts the thread.
- A dropped connection that recovers shows no error. One that cannot recover shows a notice in plain words with a way to retry (#1452).
- No branch picker appears unless the person edited a message to branch.

## Contract

Owning files: admission and the transcript in `kollabor/llm/llm_coordinator.py` (`_admit_acp` and the native path) and `kollabor/llm/acp_orchestrator.py` (the steering record); the stream in `packages/kollabor-engine/src/kollabor_engine/routes/`; the mirror in `packages/kollabor-webui/frontend/src/`. The shared shapes live in two new files written first (lane 0): `packages/kollabor-events/src/kollabor_events/chat_stream.py` (the daemon and the engine both depend on `kollabor-events`) and `packages/kollabor-webui/frontend/src/chat-stream.ts`, with JSON fixtures both sides test against in `packages/kollabor-webui/frontend/tests/fixtures/chat-stream/`.

**Ids.** A client picks the id of each input it sends; the web UI already does, as the `add-message` id. The daemon makes one when a sender has none (the TUI, hub messages, goals). That id is the row's id for the life of the row: queued, joined, saved, shown. No path creates a second id for an input. A resend with a known id is answered with the input's current state instead of making a second input.

**Rows.** `{id, seq, kind, role, turn_id, status, parts, created_at}`. `kind` is one of `input`, `reply`, `tool`, `note` (hub notes, today glued into user text as `<agent_hud>`), `prompt`, or `marker` (compaction, errors shown in the thread). A hub message carries a `hub-note` part built from the daemon's structured hub fields (`from`, `to`, `text`, `observed`, optional `kind`, `thread_id`, `reply_to`, `is_direct`, `presence`), never parsed from text: a hub message that starts no turn is a `note` row with that part and no input lifecycle; one that starts a turn is an `input` row whose id is the hub message id, with the full input lifecycle and the part inside its parts.

**Lifecycle.** Each input moves `queued`, then `started` or `joined`, then `ended`, with `ended.outcome` one of `completed`, `failed` or `cancelled`. `joined` names the turn it joined, and the input ends when that turn ends. A state never goes back, and an input that belongs to a turn ends only after that turn's `turn.state` `ended`, whatever its outcome. Every input reaches `ended` exactly once.

**Goal origin.** An admitted native goal attempt owns one stream turn across its continuation calls. Its optional immutable `origin` is `{kind: "goal", goal_id, attempt_id}`, with both full persisted identifiers. The driver binds the attempt to the turn after admission and before publishing `running`; yielded attempts have no turn. Every later state, replay and snapshot retains that origin, including when human input joins, and the turn ends on success, error or cancellation. No synthetic user input is created. A browser reload snapshot proves continuity only while the daemon remains alive. ACP goal execution remains unsupported by the capability guard.

**Persisted goal read.** Authenticated `GET /sessions/{id}/goal` reads the session daemon, including when the engine is remote. It returns `{conversation_uid, goal}` for the daemon's current conversation. `goal` is null when that conversation has no unfinished goal; otherwise it is `{goal_id, status, attempt}` with the full persisted goal ID. `attempt` is null when there is no unfinished attempt, otherwise `{attempt_id, state, turn_id}` contains the full persisted attempt ID and its bound stream turn ID or null before admission. The latest unfinished attempt is selected by continuation sequence. This point-in-time read contains no objective, paths, or other goal content. An unavailable daemon, goal store, or conversation identity is an error, not an absent goal; an unknown session is 404. The normal engine bearer authorization applies. Clients compare conversation and full IDs with the stream and must handle state changing between reads; a terminal turn during reload does not prove a running-turn reload.

**Stream.** `GET /sessions/{id}/stream?after=<seq>&conversation_uid=<uid>&generation=<generation>` (SSE). Every event is `{seq, conversation_uid, generation, type, data}`, with `seq` strictly increasing within one conversation generation. A generation is a fresh opaque token for a daemon-owned conversation log; it changes when the daemon process recreates that log, even when the persisted `conversation_uid` stays the same:

| type | data |
|---|---|
| `snapshot` | the latest window of rows, `next_seq`, and `older`: the cursor for older rows, or `null` when there are none |
| `row.upsert` | a whole row |
| `row.append` | `{id, part, text}`: text streaming into a row |
| `row.delete` | `{id}` |
| `replace` | rows that replace the window (compaction, a new conversation) |
| `input.state` | `{id, state, turn_id, outcome}` |
| `turn.state` | `{turn_id, state, input_ids, origin?}` |

A client connects without a cursor and gets a `snapshot`. A replay cursor is the full tuple `{after, conversation_uid, generation}` from the last event it applied; for a `snapshot`, `after` is `data.next_seq - 1`. With a complete matching cursor, it gets every later event. It gets a new `snapshot` instead when the cursor is missing either identity field, the conversation UID or daemon generation changed, or `after` is older than the daemon keeps (default: the current turn and the last 2,000 events). In particular, an older caller that sends `after` alone receives a snapshot. The engine relays the daemon's generation unchanged, so an engine reconnect can replay from the same daemon log; after a daemon restart, the changed generation forces a snapshot before sequence numbers can be reused. Older rows load through `GET /sessions/{id}/rows?before=<cursor>&limit=<n>`, which answers `{rows, older}` in the same row shape, oldest first; `older` is the next older cursor or `null`, and a `409` means the cursor belongs to another conversation, so the client resyncs. The legacy `/history` route keeps its old envelope until cutover.

**Sending.** `POST /sessions/{id}/inputs {id, text, attachments}` answers `202 {id, state}`. The reply arrives on the stream, for the sender as for every other client. `POST /sessions/{id}/inputs/{input_id}/cancel` cancels a queued input. `/assistant`, the `synced` point and the `history_updated` refetch are removed at cutover.

`POST /sessions/{id}/inputs/{input_id}/send-now` sends only the selected queued input, even when it shares a queue batch; siblings remain queued in their original order. It answers `202` when accepted. A retry after that input has started, joined, or ended with a turn answers `200` with its recorded state and turn id; an unknown input answers `404`, and an input that cannot be sent and has not moved answers `409`.

**Runtimes.** The native loop and the ACP adapter admit inputs through one function and emit the same events. The ACP adapter maps session updates to rows: `agent_message_chunk` to `row.append`, and `tool_call` and `tool_call_update` to `row.upsert`. A harness that cannot steer leaves the input `queued` until its turn starts.

**Web UI.** A store applies the stream and gives assistant-ui `useExternalStoreRuntime({ messageRepository })`, which mirrors the rows exactly, deletes included. `isRunning` follows `turn.state`. `useAssistantTransportRuntime`, the `importExternalState` reconciliation, `historyToMessages`, `live-turn.ts` and the six `onStale` remounts are removed.

AG-UI (MIT) defines the same ideas (`MESSAGES_SNAPSHOT`, `STATE_SNAPSHOT` and run lifecycle events). The names above map one to one, so an AG-UI adapter can be added later without changing the daemon.

## Scenarios

- If the page sends while idle, then the input is `started`, its bubble stays one bubble from send to saved, and its turn's end ends the working state.
- If the page sends while its own turn runs and the runtime can steer, then the input is `joined` to that turn under the page's id and ends with it. If the runtime cannot steer, then the input is `queued` and starts the next turn.
- If another client sends while the page's turn runs, then the page receives that input as `row.upsert` and `input.state` and draws it once. The same holds the other way round, when the page sends during another client's turn.
- If the page's connection drops mid-turn and the page sends again, then it reconnects with its last `seq`, gets every event it missed, and draws no row twice.
- If the page only watches, then it sees the same rows and states as the sender.
- If the reader has scrolled up, then rows added below do not move the row they are reading.
- If the page reloads mid-turn, then the `snapshot` carries the running turn and the queued inputs, and the stream continues from `next_seq`.
- If the conversation compacts, then a `replace` arrives, and rows kept under the same id stay mounted.
- If a permission prompt opens or is answered in any client or in the agent's terminal, then every client gets the prompt row's new status and none reloads the thread.
- If a hub note arrives mid-turn, then it is a `note` row of its own and no user row changes.
- If two inputs reach the model in one step, then they are still two rows.
- If the person stops a turn, then the turn and every input that joined it end `cancelled`, and queued inputs stay queued (`webui-daemon-queue.md`).

## Failure modes

1. A `seq` gap (an event lost on the way): the client drops its state and reconnects without `after`, receiving a `snapshot`.
2. The daemon restarts: the new log has a different `generation`, so an old cursor gets a `snapshot` even if `conversation_uid` and `seq` match; the page keeps its scroll by row id.
3. The engine restarts: clients reconnect with `{after, conversation_uid, generation}`. The daemon, not the engine, owns the events and generation, so replay continues while that daemon log remains alive.
4. A resend of a known id (a POST retried after a timeout): answered with the input's current state, with no second row.
5. A harness crashes or a turn never ends: the daemon ends the turn `failed`, and every input that joined it `failed`, so no client stays in the working state.
6. A snapshot is too large: it carries only the latest window; older rows come from `GET /sessions/{id}/rows?before=`.
7. Two clients pick the same id for different text: the second POST is refused with 409. Ids carry a per-client prefix, so only a bug can cause this.

## Decisions

Each decision is proposed by this draft and settles when the spec is approved.

- **Replace the transport-runtime choice in `docs/specs/assistant-ui-migration.md`.** That handoff picked `useAssistantTransportRuntime` and ruled out `ExternalStoreRuntime` as hand-written state sync. Why: the transport runtime is single-client and add-only, the multi-client sync was written by hand anyway in `runtime.tsx`, and it still shows defects in four of twelve recorded flows. With one stream, the external store only mirrors.
- **The daemon owns ids, and a sender's id is final.** Why: two of the three recorded double bubbles trace to an input saved under a new id, and a store keyed by input id has no second path for the third.
- **One row per input,** even when inputs reach the model together. Why: a merged row cannot be paired with its bubbles.
- **Own event names, shaped like AG-UI.** Why: `@assistant-ui/react-ag-ui` is pre-1.0 and its multi-thread support is experimental. The one-to-one mapping keeps that option open.
- **Remove the old routes at cutover.** Why: Kollab keeps no compatibility for nonexistent users (`AGENTS.md`).

## Lanes

Times are one agent's working time. Lanes 1 to 5 run at the same time once lane 0 is merged, because each owns separate files and codes against lane 0's shapes and fixtures.

| lane | work | files | needs | time |
|---|---|---|---|---|
| 0 | Contract: event and route shapes, JSON fixtures, the store's hook interface | new `kollabor_events/chat_stream.py`, new `frontend/src/chat-stream.ts`, fixtures | none | 30 min |
| 1 | Ids and lifecycle: the sender's id through queue, steer and save for native and ACP; one row per input; `input.state` and `turn.state`; hub notes as rows. Fixes #1371 on its own | `kollabor/llm/acp_orchestrator.py`, `kollabor/llm/llm_coordinator.py`, the native admission path | 0 | 60-90 min |
| 2 | Stream: the daemon's event log with `seq` and its window; engine `stream`, `inputs` and cancel routes | daemon socket actions, new `kollabor_engine/routes/stream.py` | 0 | 90-120 min |
| 3 | Web store: apply events, the `messageRepository` adapter, send and cancel through `inputs`, `isRunning` | new `frontend/src/chat-store.ts`, new `frontend/src/chat-runtime.tsx` | 0 | 60-90 min |
| 4 | Web features on the store: permissions, attachments, the queue controls, voice and Stop; delete the reconciliation, `live-turn.ts` and the remounts; remove their tests | `PermissionTool.tsx`, `App.tsx`, composer and voice files, `runtime.tsx`, tests | 0 (lane 3's hooks are pinned there) | 90-120 min |
| 5 | Recorder: flows for a prompt answered elsewhere, compaction, a goal reload and a hub note mid-turn; a runtime matrix option | `frontend/scripts/chat-recorder.mjs`, `docs/quality/webui-browser-coverage.md` | none | 45 min |
| 6 | Integration and live proof: lanes 1 to 4 on one side stack; all 16 flows on a native, a codex-cli and a claude-code agent | none | 1-5 | 90 min |
| 7 | Review and CI: an independent review of each PR's exact head, CI, and fix rounds | none | each PR | 60 min a round |
| 8 | Release so Kollab.app runs it: release PR, tag on the owner's word, publish, app update | none | 7 | 60 min |

Wall clock with five agents in parallel: lane 0 (30 min), then lanes 1 to 5 (about 2 hours, set by lanes 2 and 4), then lane 6 (90 min), with lane 7 overlapping as each PR lands, then lane 8 (60 min). That makes 5 to 7 hours when each review passes the first time; each failed review round adds about an hour. Splitting lanes 2 and 4 into three agents each brings the parallel stretch to about an hour, at roughly twice the tokens; lanes 0, 6, 7 and 8 stay serial.

## Test plan

- Red-first daemon unit tests: a steered ACP input and a queued native input keep the sender's id and end exactly once, and two inputs in one step save two rows.
- Engine tests: `seq` strictly increases, `after` replays exactly the missed events, and a stale `after` gets a `snapshot`.
- Web UI vitest for the store (`upsert`, `append`, `delete`, `replace`, and a gap that leads to a `snapshot`), added to the `vite.config.ts` include list.
- tmux spec `tests/tmux/specs/chat-single-stream.json`: a TUI send while a turn runs shows one row, and the turn ends.
- Live proof: every `npm run record:chat` flow, plus the new ones, with zero defects on a native agent, a codex-cli agent and a claude-code agent.
- Sibling surfaces to sweep: remote chats (#963), voice mode (`VoiceRoomProvider` sits outside today's remount key), the phone layout at 390 px and 820 px, and Kollab.app's bundled web UI.

## Docs and changelog fragment

- Update `docs/quality/webui-browser-coverage.md` (new flows), `docs/reference/agent-guide/components.md` (stream and input routes), and the pointer at the top of `docs/specs/assistant-ui-migration.md`.
- Fragments land with the fixing PRs: `changes/1371.fixed.md` with lane 1, and `changes/1451.fixed.md` and `changes/1452.fixed.md` with lane 4. Users read them in the release notes and in the web UI's What's New.

## Open questions

- Whether an input that joined a running turn gets a visible mark under its bubble. That is new interface text and needs a mockup and the owner's approval before it is built; until then it looks like any sent message.
