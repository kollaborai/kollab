---
title: WebUI Trajectory Tab
created: 2026-08-21
modified: 2026-08-21
status: draft
author: maintainers
---

# WebUI Trajectory Tab

a turn-aware event ledger view for the kollab web ui, modeled on the
trajectory tab in deepseek harness (dsh). the chat thread stays the
primary surface; the trajectory tab is a read-only inspection view
answering "what exactly happened in this session" -- every turn,
request, tool call, and result in one dense, searchable table.


## problem

the web ui renders one conversation thread. assistant-ui flattens
history into user/assistant chat bubbles (`runtime.tsx:
historyToMessages` filters to those two roles), so:

- tool calls and results are invisible (role `tool` messages and
  `metadata.tool_calls` are dropped before rendering)
- per-request ordering is implicit; there is no way to see request
  boundaries, tool fan-out, or batched tool-result messages
- debugging a long agent session from the browser means guessing what
  the daemon did

dsh solved this exact problem with a dedicated trajectory view: a
dense ledger with turn separators, request numbering, a details
inspector, and a timing overview. this spec ports the concept onto
kollab's existing engine surfaces without inventing new plumbing.


## reference: how dsh does it

reviewed `packages/client/ui-trajectory/` in the dsh checkout
(`/Users/malmazan/dev/deepseek-harness`).

### structure

- one self-contained client plugin. `src/client/index.ts` registers a
  tab into the `conversation.view` slot ring (`id: 'trajectory'`,
  `order: 10`, localized label). the session body renders one view at
  a time; plugin unload removes the tab.
- no separate data path. the plugin registers
  `ConversationNodeDefinition`s (assistant, tool, message,
  request-header, compaction) that project the same seq-ordered
  conversation snapshot the chat view consumes.
- view composition (`TrajectoryView.tsx`):
  - `TrajectoryToolbar` -- actual-vs-sequence duration toggle,
    collapse turns/calls, search box
  - `TrajectoryTimeline` -- chrome-network-style overview; assistant
    spans split into ttft + decoding; drag-to-focus, wheel zoom
  - `TrajectoryTable` -- dense ledger. thick rules mark turn
    boundaries, request numbers group assistant/tool rows, selection
    opens a local inspector. virtualized rows + load-older paging.
  - inspector panel -- Summary / Payload / Result / Schema / Timing
    tabs for the selected record

### record model (`trajectory-record.ts`)

each ledger row is a `TrajectoryCellProps`:

```
kind: 'system' | 'user' | 'context' | 'compacted' | 'message'
    | 'tool' | 'subtool'
index: number                     // 1-based, shown as #N
recordId / callId / sourceSeq     // stable identity across prepends
text                              // single-line summary
previewMarkdown                   // markdown source for the summary
opensTurn: boolean                // user record opens a new turn
inputDetail / outputDetail / thinkingDetail
timeSeconds: number | null        // null renders as em-dash
startedAt: number | null          // epoch ms
input / cacheRead / cacheWrite / output / think   // token counts
result / isError                  // tool result pairing
```

design rules worth copying: never fabricate timing (`null` renders
`—`), keep the ledger to index/event/content and push detail into the
inspector, and give every row an identity that survives pagination.

verified against `apps/web/tests/snapshots/navigation-panes/
trajectory.expected.md` and covered there by e2e, virtualization, and
perf tests.


## what exists today in kollab

### engine surfaces

`GET /sessions/{id}/history` (routes/sessions.py:399) returns
`{session_id, history: [...]}` where each item is a `MessageDto`
(kollabor/state/snapshots.py:35):

```
role: str            // system | user | assistant | tool
content: str
timestamp: str       // ISO 8601
metadata: dict
thinking: str | null
```

`?limit=N` returns the last N messages (already enough for
load-older paging). the daemon owns the conversation;
`session.refresh_history()` (kollabor_engine/session.py:207) pulls a
fresh copy per request, so the endpoint is always current.

### how history gets populated (packages/kollabor-agent queue_processor.py)

| path | writes | where |
|---|---|---|
| user turn | `role="user"` plain message | ~573 |
| assistant, native tools | `role="assistant"` with `metadata.tool_calls = [{id, type: "function", function: {name, arguments}}]` | ~1224-1246 |
| native tool results | `role="tool"` with `metadata.tool_call_id` + `tool_output_*` keys | ~1263-1285 |
| assistant, xml/plugin tools | `role="assistant"` plain (tags stripped from content) | ~1323 |
| xml/plugin tool results | one batched `role="user"` message, `metadata.tool_output_batch: true`, content `"Tool result: ..."` lines | ~1300-1315 |

usage stats (input_tokens, output_tokens, thinking_duration) are
computed per response (~1207-1217) but passed only to
`conversation_logger.log_assistant_message` -- they land in the
logger JSONL, **not** in history metadata.

### live updates

`GET /sessions/{id}/events` (SSE) emits typed events (sse.py):
`token`, `thinking`, `tool_start`, `tool_result`,
`permission_request/granted/denied`, `question_gate`, `turn_complete`,
`error`. `api.streamEvents()` already parses these frames.

### frontend

- `App.tsx` `RuntimeShell`: header (session id, status,
  `SessionToolbar`) + `<Thread />`. **no view-switcher concept
  exists.**
- `components/ui/` has shadcn primitives but **no `tabs.tsx`**.
- `api.ts` `EngineApi.getHistory()` exists; `runtime.tsx`
  `historyToMessages` filters to user/assistant only.

### gap summary

| trajectory needs | in `/history` today |
|---|---|
| turn boundaries | yes -- user messages |
| request grouping | derivable -- assistant messages |
| native tool calls + results | yes -- metadata.tool_calls / role=tool |
| xml/plugin tool results | partially -- batched user messages need parsing |
| per-record duration | deltas only -- message timestamps |
| tokens / ttft / decode | **no** -- usage never reaches history |


## goals

1. a Trajectory tab in the web ui next to the chat thread, fed by the
   existing history endpoint -- **zero engine changes in phase 1**
2. every history record visible: turns, requests, native + xml tool
   calls, results, thinking
3. local inspector for full input/output/thinking of any record
4. refresh on `turn_complete` so the ledger tracks the live session
5. staged path to token/timing columns once the engine mirrors usage
   into history metadata

## non-goals

- no editing, replay, or message deletion from the trajectory view
- no new frontend plugin/slot system -- the tab is local state in
  `RuntimeShell`; kollab's webui has exactly one view consumer
- no timeline overview or row virtualization in phase 1 (deferred;
  see phase 3)
- no changes to the terminal ui; this is webui-only


## design

### phase 1 -- read-only ledger from existing history

#### 1. view switcher

`RuntimeShell` (App.tsx) gains a two-state view switcher in the
header next to the session id:

```tsx
type SessionView = "chat" | "trajectory";
// header: [SidebarTrigger | session id | Chat | Trajectory | status]
{view === "chat" ? <Thread /> : <TrajectoryView sessionId={...} />}
```

add shadcn tabs (`npx shadcn@latest add tabs` -- `components.json`
already present) or two `Button`s with `aria-pressed`; either is a
smaller change than a router. switching views must not unmount
`EngineRuntimeProvider` -- keep the switch inside `RuntimeShell` so
the assistant transport stays connected while the trajectory tab is
open.

#### 2. component layout

```
frontend/src/components/trajectory/
  TrajectoryView.tsx        // fetch + state + composition
  trajectory-records.ts     // HistoryMessage[] -> TrajectoryRecord[] projector (pure)
  TrajectoryTable.tsx       // dense ledger, turn/request grouping, selection
  TrajectoryInspector.tsx   // details panel for selected record
  trajectory-format.ts      // duration/token/text formatting helpers
```

`TrajectoryView` holds: records, loading/error state, selected record
id, collapse-turns flag, search query. it fetches on mount and
refetches on `turn_complete` (see §live updates).

#### 3. record projection (pure function, the heart of the feature)

```ts
type TrajectoryRecordKind =
  | "system" | "user" | "assistant" | "tool" | "tool-batch";

interface TrajectoryRecord {
  id: string;              // stable: `${kind}:${index}` minimum
  kind: TrajectoryRecordKind;
  index: number;           // 1-based over the projected list
  turn: number;            // 0 for pre-conversation system rows
  request: number | null;  // assistant request number
  title: string;           // e.g. tool name, "USER", "ASSISTANT"
  summary: string;         // single line, css-ellipsized
  input?: string;          // tool arguments / raw user content
  output?: string;         // tool result / assistant content
  thinking?: string;
  timestamp: string | null;
  durationSeconds: number | null;   // null -> render "—"
  isError?: boolean;
}
```

projection rules over `HistoryMessage[]`:

1. **system** `role === "system"` -- own row, `turn 0`, summary =
   first line.
2. **user turn** `role === "user" && !metadata.tool_output_batch` --
   increments `turn`, `request` stays; opens a turn separator.
3. **assistant** `role === "assistant"` -- increments `request`; row
   per message plus one child row per `metadata.tool_calls[]` entry
   (tool call: name + arguments from `function.arguments`, which is a
   JSON string -- parse defensively). `thinking` field maps to the
   inspector's thinking tab.
4. **native tool result** `role === "tool"` -- join to its call via
   `metadata.tool_call_id`; render inline on the call's row
   (`name{args} -> result`) exactly like dsh; unmatched ids (history
   trimmed by `?limit`) get their own row.
5. **xml tool batch** `role === "user" && metadata.tool_output_batch`
   -- parse the content's `"Tool result: ..."` lines; one row per
   line, grouped under the preceding assistant request. this is the
   fiddliest rule; if a line fails to parse, render it verbatim
   rather than dropping it.
6. **turn/request numbering** survives filtering: compute both in one
   pass before search/collapse filters apply.

durations: `t(record) - t(previous record)` in seconds, `null` when
either timestamp is missing/unparseable. never fabricate.

#### 4. ledger rendering

- table with three columns: **#** (index), **event** (kind + turn/
  request badge), **content** (summary, `truncate`).
- thick top border on turn-opening rows; `Request #N` prefix on
  assistant rows; tool rows indented under their request.
- click selects; arrow keys move selection; `Escape` clears.
- collapse-turns toggle hides tool rows, keeping one summary row per
  request.
- search filters summaries/titles (case-insensitive substring,
  phase 1).

#### 5. inspector

right-side panel (or bottom sheet on narrow viewports) with tabs
**Input / Output / Thinking**:

- tool call: input = parsed arguments (pretty JSON), output = joined
  result, error styling when `isError`
- assistant: output = full content, thinking = `message.thinking`
- user: input = full content

plain `<pre>` with `whitespace-pre-wrap` + `ScrollArea`; no markdown
rendering in phase 1 (the summaries are single-line text by design).

#### 6. live updates

`TrajectoryView` opens `api.streamEvents(sessionId, signal)` and
refetches history on `turn_complete` (and once on `error`). it must
**not** refetch per token/tool event -- the ledger is a settled-view
surface. abort the stream on unmount and session switch, mirroring
`recoveryRef` handling in App.tsx.

### phase 2 -- usage + timing from the engine

problem: `/history` carries no token counts. smallest sufficient
change: mirror the usage stats queue_processor already computes into
the assistant history message metadata at write time:

```python
# queue_processor.py, both assistant write paths
assistant_metadata["usage"] = {
    "input_tokens": ..., "output_tokens": ...,
    "thinking_duration": ...,
}
```

then the projector gains `input`/`output` token columns on assistant
rows and `durationSeconds` prefers `thinking_duration` over timestamp
deltas. history consumers that ignore unknown metadata keys are
unaffected (MessageDto metadata is a free-form dict).

fallback option (if per-tool durations or ttft are wanted later): a
dedicated `GET /sessions/{id}/trajectory` endpoint that reads the
conversation logger JSONL, which already records usage per assistant
message and `tool_result` subtypes with `tool_use_id`. deferred --
do not build it until phase 2 metadata proves insufficient.

### phase 3 -- polish (each item independently shippable)

- load-older paging via `?limit=` + "load earlier" control at the
  ledger top (dsh pattern: semantic row keys survive prepends)
- row virtualization if sessions exceed ~500 records
- timeline overview (dsh `TrajectoryTimeline`) once real durations
  exist
- deep link from chat tool rows to the trajectory record (dsh
  `Inspect` pill pattern)


## testing

### unit (new)

- `tests/unit/test_webui_trajectory_projection.py` -- pure projector
  tests if the projection is mirrored python-side for contract tests;
  otherwise cover it via frontend typecheck + the component tests
  below
- `tests/unit/test_engine_history_usage_metadata.py` (phase 2) --
  assert assistant history messages carry `metadata.usage` on both
  native and xml paths

### frontend

```
cd packages/kollabor-webui/frontend
npm run typecheck
npm run build
```

add `TrajectoryView` rendering tests once a frontend test runner is
chosen (none exists today; vitest + testing-library is the natural
pick -- separate decision, tracked in phase 1 review).

### manual / e2e checklist

1. `kollab --web-ui`, create a session, run a turn with native tools
   (bash + file read) -- ledger shows turn 1, request 1, two tool
   rows with results
2. run a turn that triggers xml/plugin tools -- batched results
   appear as grouped rows, no raw `Tool result:` text in the chat
   thread regression
3. send a message while on the trajectory tab -- ledger refreshes on
   `turn_complete` without unmounting the transport
4. `?limit=` behavior: reload with a trimmed history -- unmatched
   tool results render as standalone rows, numbering stays stable
5. narrow viewport -- inspector becomes a sheet, ledger stays
   scrollable

### regression

```
python -m pytest tests/unit/test_webui_auth_wiring.py -q
python -m py_compile packages/kollabor-webui/src/kollabor_webui/*.py
```


## risks and open questions

- **xml batch parsing** is the fragile projection; mitigated by
  verbatim fallback rendering. open: should `format_result_for_
  conversation` emit structured metadata instead of prose? (engine
  change; deferred to phase 2 review)
- **history size**: `refresh_history` pulls the full conversation
  every fetch. acceptable now (same cost as the existing chat
  restore); revisit with load-older paging
- **role="tool" content format** differs per provider
  (`format_tool_result`); projector must treat it as opaque text
- **thinking visibility**: `MessageDto.thinking` is already surfaced;
  confirm nothing in the engine strips it for webui consumers
- **shadcn tabs vs buttons**: tabs adds a dependency-managed
  component; buttons keep the diff minimal. default to buttons in
  phase 1 unless tabs land anyway

## acceptance criteria

phase 1:

- [ ] header switcher toggles chat/trajectory without dropping the
      assistant transport connection
- [ ] every history message is represented: system, user turns,
      assistant requests, native tool calls + results, xml tool
      batches
- [ ] selecting a record shows full input/output/thinking
- [ ] ledger refreshes on `turn_complete`
- [ ] `npm run typecheck` and `npm run build` pass;
      `test_webui_auth_wiring.py` stays green

phase 2:

- [ ] assistant rows show token counts sourced from
      `metadata.usage`
- [ ] both native and xml assistant paths write `metadata.usage`
- [ ] projector falls back to timestamp deltas when usage is absent
      (older sessions)
