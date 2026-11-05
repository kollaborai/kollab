// Run: node --test tests/history-merge.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import {
  dropTransientTurnDuplicates,
  historyHasAssistantRequestId,
  isCompactionMarker,
  mergeCompactionHistorySnapshot,
  mergeMissingHistoryMessages,
  reconcileBoundaryToolResults,
  shownMessagesChanged,
} from "../src/history-merge.ts";
import { historyContentToThreadContent } from "../src/history-content.ts";

const user = { id: "history-0", role: "user", content: "go" };
const step = (result?: string) => ({
  id: "history-1",
  role: "assistant",
  content: [{ type: "tool-call", toolCallId: "t1", ...(result === undefined ? {} : { result }) }],
});

test("a tool result that lands after a mid-turn import changes a shown message", () => {
  // shown: imported mid-turn, call has no result; loaded: result row history-2 folded into history-1
  assert.equal(shownMessagesChanged([user, step()], [user, step("done")]), true);
});

test("a reply that a new voice turn now follows changes a shown message", () => {
  // the greeting row arrives after the reply: the reloaded reply ends its group
  const reply = { id: "history-1", role: "assistant", content: "pong" };
  const greeting = { id: "history-2", role: "assistant", content: "Hi, I'm here." };
  const ended = { ...reply, convertConfig: { joinStrategy: "none" } };
  assert.equal(shownMessagesChanged([user, reply], [user, ended, greeting]), true);
});

test("a reply a talk-over took away changes the thread, an older page does not", () => {
  const heard = { id: "history-1", role: "assistant", content: "Let me check" };
  const answer = { id: "history-2", role: "assistant", content: "It is sunny." };
  assert.equal(shownMessagesChanged([user, heard, answer], [user, heard]), true);
  // the reload is a later window: history-0 is an older page, still there
  assert.equal(shownMessagesChanged([user, heard, answer], [heard, answer]), false);
});

test("an unchanged thread, or one with only new messages, is not changed", () => {
  const next = { id: "history-3", role: "assistant", content: "hi" };
  assert.equal(shownMessagesChanged([user, step("done")], [user, step("done"), next]), false);
});

test("a recovered middle gap is inserted in order without replacing shown rows", () => {
  const row = (index: number) => ({ id: `history-${index}`, content: `message-${index}` });
  const shown = [row(1), row(2), row(6), row(7)];
  const merged = mergeMissingHistoryMessages(shown, [row(3), row(4), row(5), row(6)]);
  assert.deepEqual(merged.messages.map((message) => message.id),
    ["history-1", "history-2", "history-3", "history-4", "history-5", "history-6", "history-7"]);
  assert.equal(merged.messages[0], shown[0]);
  assert.equal(merged.messages[5], shown[2]);
  assert.deepEqual(merged.added.map((message) => message.id), ["history-3", "history-4", "history-5"]);
});

test("same-index note rows retain the engine's order during a partial merge", () => {
  const note = { id: "history-4-hub-0", content: "note" };
  const user = { id: "history-4", content: "input" };
  const later = { id: "history-5", content: "reply" };
  const merged = mergeMissingHistoryMessages([user, later], [note, user, later]);
  assert.deepEqual(merged.messages.map((message) => message.id), [note.id, user.id, later.id]);
});

test("new history stays before an existing pending permission", () => {
  const pending = { id: "permission-p-0", content: "approve?" };
  const merged = mergeMissingHistoryMessages([user, pending], [user, { id: "history-1", content: "reply" }]);
  assert.deepEqual(merged.messages.map((message) => message.id), ["history-0", "history-1", pending.id]);
});

test("a page-boundary tool result is replaced by its actual call", () => {
  const fallback = {
    id: "history-10", role: "assistant",
    metadata: { custom: { historyToolFallback: "tool-1" } },
    content: [{ type: "tool-call", toolCallId: "tool-1", toolName: "fallback" }],
  };
  const call = {
    id: "history-9", role: "assistant",
    content: [{ type: "tool-call", toolCallId: "tool-1", toolName: "actual", result: "done" }],
  };
  const later = { id: "history-11", role: "user", content: "next" };
  const result = reconcileBoundaryToolResults([fallback, later], [call, later], 0, 12);
  assert.deepEqual(result?.map((message) => message.id), [call.id, later.id]);
});

test("reconciling an older window preserves newer rows and pending/live state", () => {
  const row = (index: number) => ({ id: `history-${index}`, content: `message-${index}` });
  const fallback = { id: "history-10", metadata: { custom: { historyToolFallback: "t1" } },
    content: [{ type: "tool-call", toolCallId: "t1", result: "done" }] };
  const pending = { id: "permission-p-0", content: "approve?" };
  const live = { id: "live-turn:one", content: "streaming" };
  const shown = [row(1), fallback, row(20), pending, live];
  const loaded = [{ id: "history-9", content: [{ type: "tool-call", toolCallId: "t1" }] }, row(11)];
  const result = reconcileBoundaryToolResults(shown, loaded, 9, 12);
  assert.deepEqual(result?.map((message) => message.id),
    ["history-1", "history-9", "history-11", "history-20", pending.id, live.id]);
  assert.equal(result?.at(-2), pending);
  assert.equal(result?.at(-1), live);
});

test("a stale older window cannot overwrite the displayed tool result or other rows", () => {
  const fallback = { id: "history-10", metadata: { custom: { historyToolFallback: "t1" } },
    content: [{ type: "tool-call", toolCallId: "t1", result: "current result", isError: true }] };
  const current = { id: "history-11", content: "current reply" };
  const loaded = [{ id: "history-9", content: [{ type: "tool-call", toolCallId: "t1", result: "cached result" }] },
    { id: "history-11", content: "cached reply" }];
  const result = reconcileBoundaryToolResults([fallback, current], loaded, 9, 12);
  assert.deepEqual(result?.map((message) => message.id), ["history-9", "history-11"]);
  assert.equal((result?.[0].content as Array<{ result?: string }>)[0].result, "current result");
  assert.equal((result?.[0].content as Array<{ isError?: boolean }>)[0].isError, true);
  assert.equal(result?.[1], current);
});

test("unmatched tool results remain visible if no earlier call is found", () => {
  const fallback = { id: "history-10", metadata: { custom: { historyToolFallback: "t1" } },
    content: [{ type: "tool-call", toolCallId: "t1", result: "only result" }] };
  const unrelated = { id: "history-9", content: [{ type: "tool-call", toolCallId: "t2" }] };
  assert.equal(reconcileBoundaryToolResults([fallback], [unrelated], 9, 11), null);
});

test("a repeated tool ID attaches its displayed result to the nearest earlier call", () => {
  const fallback = { id: "history-12", metadata: { custom: { historyToolFallback: "same-id" } },
    content: [{ type: "tool-call", toolCallId: "same-id", result: "second result" }] };
  const loaded = [
    { id: "history-9", content: [{ type: "tool-call", toolCallId: "same-id", result: "first result" }] },
    { id: "history-11", content: [{ type: "tool-call", toolCallId: "same-id", result: "second result" }] },
  ];
  const result = reconcileBoundaryToolResults([fallback], loaded, 9, 13);
  assert.equal((result?.[0].content as Array<{ result?: string }>)[0].result, "first result");
  assert.equal((result?.[1].content as Array<{ result?: string }>)[0].result, "second result");
});

test("separate fallback results with a reused ID retain their own calls", () => {
  const fallback = (index: number, result: string) => ({
    id: `history-${index}`, metadata: { custom: { historyToolFallback: "same-id" } },
    content: [{ type: "tool-call", toolCallId: "same-id", result }],
  });
  const loaded = [
    { id: "history-9", content: [{ type: "tool-call", toolCallId: "same-id", result: "cached first" }] },
    { id: "history-11", content: [{ type: "tool-call", toolCallId: "same-id", result: "cached second" }] },
  ];
  const result = reconcileBoundaryToolResults([fallback(10, "first"), fallback(12, "second")], loaded, 9, 13);
  assert.deepEqual(result?.map((message) => message.id), ["history-9", "history-11"]);
  assert.equal((result?.[0].content as Array<{ result?: string }>)[0].result, "first");
  assert.equal((result?.[1].content as Array<{ result?: string }>)[0].result, "second");
});

test("rows outside an older window are not interpreted as removals", () => {
  const row = (index: number) => ({ id: `history-${index}`, content: `message-${index}` });
  assert.equal(reconcileBoundaryToolResults([row(10), row(20)], [row(9), row(10)], 9, 11), null);
});


test("compaction markers are recognized only from their history metadata", () => {
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted (round 1): 20 -> 4 messages.",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  assert.equal(isCompactionMarker(marker), true);
  assert.equal(isCompactionMarker({ id: "history-0", content: marker.content }), false);
});


test("compaction only replaces a live assistant turn for its exact request ID", () => {
  const older = { id: "history-2", role: "assistant", content: "older reply",
    metadata: { custom: { requestId: "request-old" } } };
  assert.equal(historyHasAssistantRequestId([older], "request-active"), false);
  assert.equal(historyHasAssistantRequestId([older], "request-old"), true);
  assert.equal(historyHasAssistantRequestId([{ ...older, role: "user" }], "request-old"), false);
});

test("compaction archives shown history and imports the compacted snapshot once", () => {
  const old = [
    { id: "history-0", role: "user", content: "original prompt" },
    { id: "history-1", role: "assistant", content: "original answer" },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted (round 1): 20 -> 4 messages.",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const recent = { id: "history-1", role: "user", content: "retained prompt" };
  const appended = { id: "history-2", role: "assistant", content: "answer during compaction" };

  const rebased = mergeCompactionHistorySnapshot(old, [marker, recent, appended]);

  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.content), [
    "original prompt",
    "original answer",
    marker.content,
    "retained prompt",
    "answer during compaction",
  ]);
  assert.match(rebased.messages[0].id ?? "", /^compaction-archive-/);
  assert.equal(shownMessagesChanged(rebased.messages, [marker, recent, appended]), false);
  assert.equal(mergeCompactionHistorySnapshot(rebased.messages, [marker, recent, appended]), null);
});

test("post-compaction rows append after archived transcript rather than before it", () => {
  const old = [{ id: "history-0", role: "user", content: "original prompt" }];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const rebased = mergeCompactionHistorySnapshot(old, [marker, { id: "history-1", content: "recent" }]);
  assert.ok(rebased);

  const appended = mergeMissingHistoryMessages(rebased.messages, [
    marker,
    { id: "history-1", content: "recent" },
    { id: "history-2", content: "new turn" },
  ]);
  assert.deepEqual(appended.messages.map((message) => message.content), [
    "original prompt",
    "Context compacted",
    "recent",
    "new turn",
  ]);
});

test("compaction shows a retained row once, from the compacted history", () => {
  // the engine kept the last two rows verbatim; they must not also be archived
  const shown = [
    { id: "history-0", role: "user", content: "What is 1+1?" },
    { id: "history-1", role: "assistant", content: "2" },
    { id: "history-2", role: "user", content: "What is 2+2?" },
    { id: "history-3", role: "assistant", content: "4" },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted (round 1): 4 -> 3 messages.",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const loaded = [
    marker,
    { id: "history-1", role: "user", content: "What is 2+2?" },
    { id: "history-2", role: "assistant", content: "4" },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.content), [
    "What is 1+1?",
    "2",
    marker.content,
    "What is 2+2?",
    "4",
  ]);
});

test("a row appended during compaction follows the retained rows, which still show once", () => {
  const shown = [
    { id: "history-0", role: "user", content: "What is 2+2?" },
    { id: "history-1", role: "assistant", content: "4" },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const loaded = [
    marker,
    { id: "history-1", role: "user", content: "What is 2+2?" },
    { id: "history-2", role: "assistant", content: "4" },
    { id: "history-3", role: "user", content: "and 3+3?" },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.content), [
    marker.content,
    "What is 2+2?",
    "4",
    "and 3+3?",
  ]);
});

test("a compaction that stores a turn drops the window's transient copies, as a sync does", () => {
  // the window still holds the turn as transient rows; the snapshot now stores it
  const shown = [
    { id: "history-0", role: "user", content: "first prompt" },
    { id: "history-1", role: "assistant", content: "first answer" },
    { id: "user-ws1-5", role: "user", content: "Just submitted" },
    { id: "assistant-ws1-6", role: "assistant", content: "Fresh reply" },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted (round 1): 4 -> 3 messages.",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const loaded = [
    marker,
    { id: "history-1", role: "user", content: "Just submitted" },
    { id: "history-2", role: "assistant", content: "Fresh reply" },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.content), [
    "first prompt",
    "first answer",
    marker.content,
    "Just submitted",
    "Fresh reply",
  ]);
});

test("a compaction keeps a transient reply whose stored copy the snapshot lacks", () => {
  // the earlier turn is displayed and carried; the new turn is not stored yet
  const shown = [
    { id: "history-0", role: "user", content: "2+2?" },
    { id: "history-1", role: "assistant", content: "4" },
    { id: "user-ws1-5", role: "user", content: "2+2?" },
    { id: "assistant-ws1-6", role: "assistant", content: "4" },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted (round 1): 4 -> 3 messages.",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const loaded = [
    marker,
    { id: "history-1", role: "user", content: "2+2?" },
    { id: "history-2", role: "assistant", content: "4" },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.content), [
    marker.content,
    "2+2?",
    "4",
    "2+2?",
    "4",
  ]);
});

// Issue #666: the turn a window just ran renders as transient rows
// (user-<session>-<n>, assistant-<session>-<n>) while its stored rows are
// still unwritten. When the sync point missed the turn -- the daemon's
// refresh failed or read the mirror before the rows landed -- the next
// history reload replays the turn as a tail, and the window drew it twice
// until a manual reload.
const turnShown = [
  { id: "history-0", role: "user", content: "q1" },
  { id: "history-1", role: "assistant", content: "a1" },
  { id: "user-ws1-5", role: "user", content: "q2" },
  { id: "assistant-ws1-6", role: "assistant", content: "a2" },
];
const turnLoaded = [
  { id: "history-0", role: "user", content: "q1" },
  { id: "history-1", role: "assistant", content: "a1" },
  { id: "history-2", role: "user", content: "q2" },
  { id: "history-3", role: "assistant", content: "a2" },
];

test("a replayed turn replaces its transient rows instead of doubling them", () => {
  // from.history=2 (stale by two): the tail replays the whole turn.
  const merged = mergeMissingHistoryMessages(turnShown, turnLoaded);
  const tail = merged.added;
  assert.deepEqual(tail.map((m) => m.id), ["history-2", "history-3"]);
  assert.equal(merged.messages.length, 6); // today: both copies present
  const deduped = dropTransientTurnDuplicates(merged.messages, tail);
  assert.deepEqual(deduped.map((m) => m.id),
    ["history-0", "history-1", "history-2", "history-3"]);
});

test("a late assistant row replaces its transient block once the user row landed", () => {
  // from.history=3: the user row synced, only the reply replays.
  const merged = mergeMissingHistoryMessages(turnShown, turnLoaded);
  const tail = merged.added.filter((m) => m.id === "history-3");
  const deduped = dropTransientTurnDuplicates(merged.messages, tail);
  assert.deepEqual(deduped.map((m) => m.id),
    ["history-0", "history-1", "history-2", "history-3"]);
});

test("a tail from another turn leaves the transient rows alone", () => {
  const merged = mergeMissingHistoryMessages(turnShown, turnLoaded);
  const tail = [{ id: "history-4", role: "user", content: "different prompt" }];
  assert.equal(dropTransientTurnDuplicates(merged.messages, tail), null);
});

test("a page without trailing transient rows has nothing to drop", () => {
  const shown = turnLoaded.slice(0, 3);
  const merged = mergeMissingHistoryMessages(shown, turnLoaded);
  assert.equal(dropTransientTurnDuplicates(merged.messages, merged.added), null);
});

test("an empty tail has nothing to dedupe against", () => {
  assert.equal(dropTransientTurnDuplicates(turnShown, []), null);
});

test("a repeated prompt keeps its new reply when only its user row was stored", () => {
  // an earlier turn already answered "2+2?" with "4"; this turn's reply was never stored
  const shown = [
    { id: "history-0", role: "user", content: "2+2?" },
    { id: "history-1", role: "assistant", content: "4" },
    { id: "user-ws1-5", role: "user", content: "2+2?" },
    { id: "assistant-ws1-6", role: "assistant", content: "4" },
  ];
  const loaded = [
    { id: "history-0", role: "user", content: "2+2?" },
    { id: "history-1", role: "assistant", content: "4" },
    { id: "history-2", role: "user", content: "2+2?" },
  ];
  const merged = mergeMissingHistoryMessages(shown, loaded);
  assert.deepEqual(merged.added.map((m) => m.id), ["history-2"]);
  assert.equal(dropTransientTurnDuplicates(merged.messages, merged.added), null);
});

test("a partially replayed turn keeps transient rows without a replayed counterpart", () => {
  // the anchor row replayed into kept history, the reply has not:
  // only the anchor matches, so dropping the block would erase live content.
  const shown = [
    { id: "history-0", role: "user", content: "q1" },
    { id: "history-1", role: "assistant", content: "a1" },
    { id: "history-2", role: "user", content: "q2" },
    { id: "user-ws1-5", role: "user", content: "q2" },
    { id: "assistant-ws1-6", role: "assistant", content: "a2" },
  ];
  const tail = [{ id: "history-2", role: "user", content: "q2" }];
  assert.equal(dropTransientTurnDuplicates(shown, tail), null);
});

// Issue #637 review (5ca3a3dc): a paged tool result's reference is signed with
// its row position, and compaction renumbers the retained rows.
const pagedOutput = (reference) => ({
  kind: "history-output-preview",
  sessionId: "s1",
  reference,
  preview: "line 1\n...",
});
const compactionMarker = {
  id: "history-0",
  role: "assistant",
  content: "Context compacted (round 1): 4 -> 3 messages.",
  metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
};
const callCount = (messages, callId) =>
  messages.filter((message) =>
    JSON.stringify(message.content ?? null).includes(`"toolCallId":"${callId}"`)).length;

test("a paged tool result shows once when compaction renumbers its reference", () => {
  const shown = [
    { id: "history-0", role: "user", content: "list the logs" },
    { id: "history-10", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1", result: pagedOutput("ref-position-10") }] },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: "list the logs" },
    { id: "history-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1", result: pagedOutput("ref-position-1") }] },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.equal(callCount(rebased.messages, "t1"), 1);
});

test("a live tool turn is kept when a stored turn with the same words used another call", () => {
  // no retained row overlaps, so the replayed tail is every loaded row; the same words are not the same turn
  const shown = [
    { id: "history-0", role: "user", content: "older question" },
    { id: "history-1", role: "assistant", content: "older answer" },
    { id: "user-ws1-7", role: "user", content: "lookup same" },
    { id: "assistant-ws1-8", role: "assistant", content: [{ type: "tool-call", toolCallId: "t2", result: "new output" }, { type: "text", text: "Done" }] },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: "lookup same" },
    { id: "history-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1", result: "old output" }, { type: "text", text: "Done" }] },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.ok(rebased.messages.some((message) => message.id === "assistant-ws1-8"));
});

test("a live tool turn is kept when a stored turn reuses its call id with another result", () => {
  const shown = [
    { id: "history-0", role: "user", content: "older question" },
    { id: "history-1", role: "assistant", content: "older answer" },
    { id: "user-ws1-7", role: "user", content: "lookup same" },
    { id: "assistant-ws1-8", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1", result: "new output" }, { type: "text", text: "Done" }] },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: "lookup same" },
    { id: "history-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1", result: "old output" }, { type: "text", text: "Done" }] },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.ok(rebased.messages.some((message) => message.id === "assistant-ws1-8"));
});

test("a live tool turn is replaced by its stored copy when both carry the same request", () => {
  // the stored copy's output is a paged reference, so the results differ in shape, not turn
  const request = { custom: { requestId: "req-2" } };
  const shown = [
    { id: "history-0", role: "user", content: "older question" },
    { id: "history-1", role: "assistant", content: "older answer" },
    { id: "user-ws1-7", role: "user", content: "lookup same", metadata: request },
    { id: "assistant-ws1-8", role: "assistant", content: [{ type: "tool-call", toolCallId: "t2", result: "live output" }, { type: "text", text: "Done" }], metadata: request },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: "lookup same", metadata: request },
    { id: "history-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "t2", result: pagedOutput("ref-position-2") }, { type: "text", text: "Done" }], metadata: request },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.equal(rebased.messages.some((message) => message.id === "assistant-ws1-8"), false);
  assert.equal(callCount(rebased.messages, "t2"), 1);
});

test("each loaded row takes at most one shown row, matched from the newest", () => {
  // the window shows one "go" prompt; the snapshot keeps two identical prompts
  const shown = [{ id: "history-7", role: "user", content: "go" }];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: "go" },
    { id: "history-2", role: "assistant", content: "ok" },
    { id: "history-3", role: "user", content: "go" },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.deepEqual(rebased.messages.map((message) => message.id), ["history-0", "history-1", "history-2", "history-3"]);
  assert.equal(rebased.messages.filter((message) => message.content === "go").length, 2);
});

// Round-3 review (41b055f6): the composer sends a picture without a request id, so
// a stored turn with the same words and another picture was taken for the same turn.
test("a new picture is kept when a stored turn repeats the words with another picture", () => {
  const shown = [
    { id: "history-0", role: "user", content: "older question" },
    { id: "history-1", role: "assistant", content: "older answer" },
    { id: "user-ws1-11", role: "user", content: [{ type: "text", text: "describe this" }, { type: "image", image: "data:image/png;base64,NEW" }] },
    { id: "assistant-ws1-12", role: "assistant", content: "Looks like a cat." },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: [{ type: "text", text: "describe this" }, { type: "image", image: "data:image/png;base64,OLD" }] },
    { id: "history-2", role: "assistant", content: "Looks like a cat." },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.ok(rebased.messages.some((message) => message.id === "user-ws1-11"));
});

// Live-to-live only. The engine never stores an image string: a stored picture is
// {type:"image", source:{kind:"managed_upload", media_id, media_type}}, which the web converter
// (runtime.tsx historyContentToThreadContent) shows as the text "[imageN]". managed_upload coverage is out of scope here.
test("a transient picture turn is replaced by a stored row with the same image string", () => {
  const shown = [
    { id: "history-0", role: "user", content: "older question" },
    { id: "history-1", role: "assistant", content: "older answer" },
    { id: "user-ws1-11", role: "user", content: [{ type: "text", text: "describe this" }, { type: "image", image: "data:image/png;base64,SAME" }] },
    { id: "assistant-ws1-12", role: "assistant", content: "Looks like a cat." },
  ];
  const loaded = [
    compactionMarker,
    { id: "history-1", role: "user", content: [{ type: "text", text: "describe this" }, { type: "image", image: "data:image/png;base64,SAME" }] },
    { id: "history-2", role: "assistant", content: "Looks like a cat." },
  ];
  const rebased = mergeCompactionHistorySnapshot(shown, loaded);
  assert.ok(rebased);
  assert.equal(rebased.messages.some((message) => message.id === "user-ws1-11"), false);
  assert.equal(JSON.stringify(rebased.messages).split("data:image/png;base64,SAME").length - 1, 1);
});

// Round-4 P2 (#648 at 7ec6a17e). The engine stores a pasted picture as a managed_upload
// part with no public URL, and the thread shows it as the placeholder [imageN]. The live
// transient row carries the same placeholder for a different picture, so the words alone
// matched and the new picture was dropped. The stored row goes through the real converter.
test("a new picture turn with a placeholder is kept next to a stored turn with the same words", () => {
  const stored = {
    id: "history-1",
    role: "user",
    content: historyContentToThreadContent([
      { type: "text", text: "describe this" },
      { type: "image", source: { kind: "managed_upload", media_id: "media-OLD", media_type: "image/png" } },
    ]),
  };
  const shown = [
    stored,
    { id: "history-2", role: "assistant", content: "Looks like a cat." },
    { id: "user-ws1-11", role: "user", content: "describe this\n[image1]" },
    { id: "assistant-ws1-12", role: "assistant", content: "Looks like a cat." },
  ];
  const tail = [{ id: "history-3", role: "user", content: "describe this\n[image1]" }];
  assert.equal(dropTransientTurnDuplicates(shown, tail), null);
});

test("a placeholder picture turn is replaced by its stored copy when both rows carry the same request id", () => {
  const requested = { metadata: { custom: { requestId: "req-1" } } };
  const shown = [
    {
      id: "history-1",
      role: "user",
      content: historyContentToThreadContent([
        { type: "text", text: "describe this" },
        { type: "image", source: { kind: "managed_upload", media_id: "media-OLD", media_type: "image/png" } },
      ]),
      ...requested,
    },
    { id: "history-2", role: "assistant", content: "Looks like a cat.", ...requested },
    { id: "user-ws1-11", role: "user", content: "describe this\n[image1]", ...requested },
    { id: "assistant-ws1-12", role: "assistant", content: "Looks like a cat.", ...requested },
  ];
  const tail = [{ id: "history-3", role: "user", content: "describe this\n[image1]" }];
  const deduped = dropTransientTurnDuplicates(shown, tail);
  assert.ok(deduped);
  assert.equal(deduped.some((message) => message.id === "user-ws1-11"), false);
});
