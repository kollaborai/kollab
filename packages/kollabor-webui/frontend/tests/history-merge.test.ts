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
