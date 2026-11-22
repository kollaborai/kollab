// Run: node --test tests/history-merge.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import {
  dropTransientTurnDuplicates,
  historyHasAssistantRequestId,
  historyMessageIndex,
  isCompactionMarker,
  mergeCompactionHistorySnapshot,
  mergeMissingHistoryMessages,
  reconcileBoundaryToolResults,
  hasUnsettledRows,
  inFlightSendIdsFor,
  rebaseOntoHistory,
  shownMessagesChanged,
  withoutInFlightSends,
  withoutOwnTurnRows,
  withSavedCopies,
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

test("a page-boundary tool result is corrected in place with its actual call", () => {
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
  assert.deepEqual(result?.map((message) => message.id), [fallback.id, later.id]);
});

test("a boundary correction keeps the shown id and matches its canonical history row later", () => {
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
  const corrected = reconcileBoundaryToolResults([fallback, later], [call, later], 0, 12);
  assert.deepEqual(corrected?.map((message) => message.id), [fallback.id, later.id]);
  assert.equal(corrected?.[0].metadata?.custom?.historySourceIndex, 9);
  assert.equal(corrected?.[0].metadata?.custom?.historyToolFallback, undefined);
  assert.equal(shownMessagesChanged(corrected ?? [], [call, later]), false);
  const next = mergeMissingHistoryMessages(corrected ?? [], [call, later]);
  assert.deepEqual(next.messages.map((message) => message.id), [fallback.id, later.id]);
  assert.deepEqual(next.added, []);
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
    ["history-1", "history-10", "history-11", "history-20", pending.id, live.id]);
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
  assert.deepEqual(result?.map((message) => message.id), ["history-10", "history-11"]);
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
  assert.deepEqual(result?.map((message) => message.id), ["history-10", "history-12"]);
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
  assert.equal(rebased.messages[0].id, old[0].id);
  assert.equal(historyMessageIndex(rebased.messages[0]), null);
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

test("compaction keeps carried row ids and updates their source indexes", () => {
  const shown = [
    {
      id: "user-demo-session-120",
      role: "user",
      content: "current prompt",
      metadata: { custom: { historySourceIndex: 120 } },
    },
    {
      id: "assistant-demo-session-121",
      role: "assistant",
      content: "current answer",
      metadata: { custom: { historySourceIndex: 121, requestId: "request-current" } },
    },
  ];
  const marker = {
    id: "history-0",
    role: "assistant",
    content: "Context compacted",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } },
  };
  const loaded = [
    marker,
    { id: "history-1", role: "user", content: "current prompt" },
    {
      id: "history-2",
      role: "assistant",
      content: "current answer",
      metadata: { custom: { requestId: "request-current" } },
    },
  ];

  const rebased = mergeCompactionHistorySnapshot(shown, loaded);

  assert.ok(rebased);
  assert.deepEqual(rebased.history.map((message) => message.id), [
    marker.id,
    shown[0].id,
    shown[1].id,
  ]);
  assert.equal(historyMessageIndex(rebased.history[1]), 1);
  assert.equal(historyMessageIndex(rebased.history[2]), 2);
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
  assert.deepEqual(rebased.messages.map((message) => message.id), ["history-0", "history-1", "history-2", "history-7"]);
  assert.equal(rebased.messages[3]?.metadata?.custom?.historySourceIndex, 3);
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

test("compaction keeps visible display IDs unique and releases archived source positions", () => {
  const shown = [
    { id: "user-demo-7", role: "user", content: "archived", metadata: { custom: { historySourceIndex: 3 } } },
    { id: "history-2", role: "assistant", content: "carried" },
  ];
  const marker = { id: "history-4", role: "assistant", content: "marker",
    metadata: { custom: { contextCompaction: true, compactionRound: 1 } } };
  const loaded = [
    { id: "history-1", role: "assistant", content: "carried" },
    { id: "history-2", role: "user", content: "new row" }, marker,
  ];
  const result = mergeCompactionHistorySnapshot(shown, loaded)!;
  assert.equal(result.messages[0].id, shown[0].id);
  assert.equal(historyMessageIndex(result.messages[0]), null);
  assert.equal(result.messages[1].id, shown[1].id);
  assert.equal(new Set(result.messages.map(m => m.id)).size, result.messages.length);
  assert.equal(shownMessagesChanged(result.messages, loaded), false);
  const older = { id: "history-3", role: "user", content: "different at three" };
  const merged = mergeMissingHistoryMessages(result.messages, [...loaded, older]);
  assert.deepEqual(merged.added.map(m => m.content), [older.content]);
});

// #1356: this page's own send is on screen as a transient row (pending, then
// the engine's) while a history sync brings the same send as a saved row. The
// page names its in-flight sends by id; other user-<session>-<n> rows are
// settled rows of earlier turns.
const sent = (id: string, text: string) => ({ id, role: "user", content: [{ type: "text", text }] });
const saved = (index: number, text: string) => ({ id: `history-${index}`, role: "user", content: text });
const ids = (...values: string[]) => new Set(values);

test("a history sync leaves out the saved copy of an in-flight send", () => {
  const shown = [sent("user-s1-38", "message B"), { id: "assistant-s1-39", role: "assistant", content: "" }];
  const busy = { id: "history-50", role: "user", content: "busy turn" };
  const work = { id: "history-51", role: "assistant", content: "working" };
  const tail = [busy, work, saved(53, "message B")];
  assert.deepEqual(withoutInFlightSends(shown, tail, ids("user-s1-38")), [busy, work]);
});

test("a history sync keeps user rows no in-flight send covers", () => {
  const tail = [saved(4, "another message"), saved(5, "message B")];
  assert.deepEqual(withoutInFlightSends([], tail, ids()), tail);
  assert.deepEqual(withoutInFlightSends([sent("user-s1-3", "message B")], tail, ids("user-s1-3")), [tail[0]]);
  // A row already in history is not an in-flight send, even with the same words.
  assert.deepEqual(withoutInFlightSends([sent("history-2", "message B")], tail, ids("history-2")), tail);
});

test("a settled row of an earlier turn is not an in-flight send", () => {
  // The engine keeps user-<session>-<n> on a settled row; only the page's own
  // send ids count.
  const tail = [saved(9, "yes")];
  assert.deepEqual(withoutInFlightSends([sent("user-s1-3", "yes")], tail, ids()), tail);
  const shown = [sent("user-s1-3", "yes"), sent("user-s1-8", "yes")];
  const two = [saved(9, "yes"), saved(10, "yes")];
  assert.deepEqual(withoutInFlightSends(shown, two, ids("user-s1-8")), two);
});

test("a pending copy is an in-flight send whatever its id", () => {
  // assistant-ui renumbers a pending copy as rows sync in; the request sent it
  // as user-s1-10.
  const pending = (id: string, text: string) => ({ ...sent(id, text), metadata: { isOptimistic: true } });
  const tail = [saved(12, "hello")];
  assert.deepEqual(withoutInFlightSends([pending("user-s1-12", "hello")], tail, ids("user-s1-10")), []);
  // A copy still queued behind the one in flight was not sent: it covers nothing.
  const queued = [pending("user-s1-12", "first"), pending("user-s1-13", "yes")];
  const other = [saved(14, "yes")];
  assert.deepEqual(withoutInFlightSends(queued, other, ids("user-s1-10")), other);
});

test("saved rows that outnumber the in-flight sends with their words are all kept", () => {
  // Another sender's "yes" and this page's "yes" land in one sync: the words
  // cannot tell which is which, so hiding either could hide the other sender.
  const tail = [saved(7, "yes"), saved(8, "yes")];
  assert.deepEqual(withoutInFlightSends([sent("user-s1-6", "yes")], tail, ids("user-s1-6")), tail);
});

test("each in-flight send covers one saved row with its words", () => {
  const tail = [saved(7, "yes"), saved(8, "yes"), saved(9, "no")];
  const shown = [sent("user-s1-5", "yes"), sent("user-s1-6", "yes")];
  assert.deepEqual(withoutInFlightSends(shown, tail, ids("user-s1-5", "user-s1-6")), [tail[2]]);
});

test("a picture placeholder does not prove a saved row is the in-flight send", () => {
  const tail = [saved(9, "[image1] look")];
  assert.deepEqual(withoutInFlightSends([sent("user-s1-8", "[image1] look")], tail, ids("user-s1-8")), tail);
});

test("a request's messages are in flight as rows after the state it sent", () => {
  const request = {
    commands: [{ type: "add-message" }, { type: "add-message" }],
    state: { messages: [{}, {}, {}] },
  };
  assert.deepEqual([...inFlightSendIdsFor(request, "s1", ids())], ["user-s1-3", "user-s1-4"]);
});

test("a request without a message keeps the sends already in flight", () => {
  // Approving a tool continues the turn that carries the send; its saved copy
  // can still arrive during that request.
  const previous = ids("user-s1-3");
  assert.equal(inFlightSendIdsFor({ commands: [{ type: "add-tool-result" }], state: { messages: [] } }, "s1", previous), previous);
  assert.equal(inFlightSendIdsFor({ commands: [] }, "s1", previous), previous);
});

// #1359: rows that name their send pair by id, not by words.
const named = <T extends { metadata?: unknown }>(row: T, custom: Record<string, unknown>) =>
  ({ ...row, metadata: { custom } });
const pending = (id: string, text: string, requestId: string) =>
  ({ ...sent(id, text), metadata: { isOptimistic: true, custom: { requestId } } });

test("another sender's identical words, saved first, stay while this page's send is in flight", () => {
  const theirs = named(saved(9, "hello"), { requestIds: ["their-send"] });
  assert.deepEqual(withoutInFlightSends([pending("user-s1-8", "hello", "my-send")], [theirs], ids("user-s1-8")), [theirs]);
  const mine = named(saved(10, "hello"), { requestIds: ["my-send"] });
  assert.deepEqual(withoutInFlightSends([pending("user-s1-8", "hello", "my-send")], [theirs, mine], ids("user-s1-8")), [theirs]);
});

test("the engine's row of a send finds its saved copy by id", () => {
  const engineRow = named(sent("user-s1-8", "hello"), { requestId: "my-send" });
  const mine = named(saved(10, "hello"), { requestIds: ["my-send"] });
  const theirs = named(saved(11, "hello"), { requestIds: ["their-send"] });
  assert.deepEqual(withoutInFlightSends([engineRow], [mine, theirs], ids("user-s1-8")), [theirs]);
});

test("a send with a picture finds its saved copy by id", () => {
  const mine = named(saved(9, "[image1] look"), { requestIds: ["my-send"] });
  assert.deepEqual(withoutInFlightSends([pending("user-s1-8", "[image1] look", "my-send")], [mine], ids("user-s1-8")), []);
});

test("a saved row holding this page's send and another sender's shows each send once", () => {
  // The daemon saves sends that arrive together as one row; history shows a
  // row per send (history-<n>, history-<n>-send-<i>), each naming its send.
  // This page's piece is left out while its send is in flight, the other
  // sender's piece shows next to it (#1359).
  const theirs = named({ ...saved(9, "hello"), id: "history-9-send-0" }, { requestIds: ["their-send"] });
  const mine = named({ ...saved(9, "hello"), id: "history-9-send-1" }, { requestIds: ["my-send"] });
  const shown = [pending("user-s1-8", "hello", "my-send")];
  assert.deepEqual(withoutInFlightSends(shown, [theirs, mine], ids("user-s1-8")), [theirs]);
  // At settle the engine maps the send's row to saved row 9; it takes its own
  // piece, so the other sender's piece is still added beside it.
  const sent = named(drawn("user-s1-8", "user", "hello", 9), { requestId: "my-send", historySourceIndex: 9 });
  // Before settle the row names saved row 9 only: that is no piece's id, so
  // the other sender's piece is not taken as shown.
  assert.deepEqual(mergeMissingHistoryMessages([sent], [theirs]).added, [theirs]);
  const [settled] = withSavedCopies([sent], [theirs, mine]);
  assert.equal(settled.id, "user-s1-8");
  assert.equal(settled.metadata?.custom?.historySourceId, "history-9-send-1");
  assert.deepEqual(mergeMissingHistoryMessages([settled], [theirs, mine]).added, [theirs]);
  assert.deepEqual(mergeMissingHistoryMessages([settled], [theirs, mine]).messages.map((row) => row.id),
    ["history-9-send-0", "user-s1-8"]);
});

test("a saved row that names no send still pairs by its words", () => {
  const unnamed = saved(9, "hello");
  assert.deepEqual(withoutInFlightSends([pending("user-s1-8", "hello", "my-send")], [unnamed], ids("user-s1-8")), []);
});

test("a replayed turn's transient rows drop when their saved copies name the same send", () => {
  const stored = [
    named({ id: "history-0", role: "user", content: "[image1] look" }, { requestIds: ["r1"] }),
    { id: "history-1", role: "assistant", content: "seen" },
  ];
  const transient = [
    named({ id: "user-s1-2", role: "user", content: "[image1] look" }, { requestId: "r1" }),
    { id: "assistant-s1-3", role: "assistant", content: "seen" },
  ];
  assert.deepEqual(dropTransientTurnDuplicates([...stored, ...transient], stored), stored);
  const other = [named(transient[0], { requestId: "r2" }), transient[1]];
  assert.equal(dropTransientTurnDuplicates([...stored, ...other], stored), null);
});

// #1358: the rows the page's own run drew carry the saved row the engine
// matched them to (custom.historySourceIndex).
const drawn = (id: string, role: string, content: unknown, index: number) =>
  ({ id, role, content, metadata: { custom: { historySourceIndex: index } } });

test("the run's own user rows take their saved copies under the ids they were drawn with", () => {
  const shown = [
    { id: "history-0", role: "user", content: [{ type: "text", text: "busy" }] },
    drawn("user-s1-1", "user", "hello", 1),
    drawn("assistant-s1-2", "assistant", [{ type: "tool-call", toolCallId: "t1" }, { type: "text", text: "Working" }], 3),
  ];
  const loaded = [
    shown[0],
    { id: "history-1", role: "user", content: [{ type: "text", text: "hello" }] },
    { id: "history-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "t1" }] },
    { id: "history-3", role: "assistant", content: [{ type: "text", text: "Working" }], metadata: { custom: { requestId: "r1" } } },
  ];
  const settled = withSavedCopies(shown, loaded);
  assert.deepEqual(settled.map((message) => message.id), ["history-0", "user-s1-1", "assistant-s1-2"]);
  // The reply keeps the tool call it drew: history saves the call as a row of
  // its own, and taking the words-only row would drop the card (#1358).
  assert.deepEqual(settled.map((message) => message.content), [shown[0].content, loaded[1].content, shown[2].content]);
  assert.deepEqual(settled.map(historyMessageIndex), [0, 1, 3]);
  assert.equal(settled[0], shown[0]);
  assert.equal(settled[2], shown[2]);
  // Already reading as saved: nothing to swap.
  assert.equal(withSavedCopies(settled, loaded), settled);
});

test("a row without a saved copy yet, or a history row, is left as shown", () => {
  const pending = drawn("user-s1-1", "user", "hello", 1);
  const older = { id: "history-0", role: "assistant", content: "old words" };
  const shown = [older, pending];
  assert.equal(withSavedCopies(shown, [{ id: "history-0", role: "assistant", content: "new words" }]), shown);
});

test("a run's row saved after the loaded history was read is not gone from it", () => {
  const busy = { id: "history-0", role: "user", content: "busy" };
  const sent = drawn("user-s1-1", "user", "hello", 1);
  assert.equal(shownMessagesChanged([busy, sent], [busy], 1), false);
  // Without the loaded row count nothing says when it was read.
  assert.equal(shownMessagesChanged([busy, sent], [busy]), true);
  // A history row past it is still gone (a talk-over took the reply away).
  const heard = { id: "history-1", role: "assistant", content: "Let me check" };
  assert.equal(shownMessagesChanged([busy, heard], [busy], 1), true);
});

// #1358: a turn that ran a tool draws its calls inside the run's reply row.
test("a saved calls row the run's reply already draws is not added again", () => {
  const call = { type: "tool-call", toolCallId: "c1", result: "ok" };
  const reply = drawn("assistant-s1-1", "assistant", [call, { type: "text", text: "Working" }], 2);
  const shown = [drawn("user-s1-0", "user", "hello", 0), reply];
  const loaded = [
    { id: "history-0", role: "user", content: [{ type: "text", text: "hello" }] },
    { id: "history-1", role: "assistant", content: [call] },
    { id: "history-2", role: "assistant", content: [{ type: "text", text: "Working" }] },
  ];
  assert.deepEqual(mergeMissingHistoryMessages(shown, loaded).added, []);
  // A calls row with a call the reply does not draw is added.
  const other = { id: "history-3", role: "assistant", content: [{ ...call, toolCallId: "c2" }] };
  assert.deepEqual(mergeMissingHistoryMessages(shown, [...loaded, other]).added, [other]);
  // The reply keeps what it drew, so its saved copy does not change the thread.
  assert.equal(withSavedCopies(shown, loaded)[1], reply);
  assert.equal(shownMessagesChanged(withSavedCopies(shown, loaded), loaded, 3), false);
});

test("a later turn's calls row is added even when an earlier reply drew the same call id", () => {
  // Some providers number calls per turn (call_0 each time): only calls rows
  // saved before the reply that drew them are taken as drawn (#1358).
  const call = { type: "tool-call", toolCallId: "call_0", result: "ok" };
  // The earlier turn: its send saved at 0, its reply at 2.
  const sent = drawn("user-s1-0", "user", "go", 0);
  const earlier = drawn("assistant-s1-1", "assistant", [call, { type: "text", text: "first" }], 2);
  const later = { id: "history-4", role: "assistant", content: [{ ...call, result: "again" }] };
  assert.deepEqual(mergeMissingHistoryMessages([sent, earlier], [later]).added, [later]);
  const before = { id: "history-1", role: "assistant", content: [call] };
  assert.deepEqual(mergeMissingHistoryMessages([sent, earlier], [before]).added, []);
});

test("rows saved while this page's turn runs go above that turn", () => {
  // assistant-ui takes the last user row with a reply after it for the turn it
  // keeps at the top: a row landing between this page's send and its reply
  // would scroll the reader to it (#1364). The page's own turn stays last.
  const a = named({ ...saved(9, "A"), id: "history-9-send-0" }, { requestIds: ["my-send"] });
  const b = named({ ...saved(9, "B"), id: "history-9-send-1" }, { requestIds: ["their-send"] });
  const sent = pending("user-s1-8", "A", "my-send");
  assert.deepEqual(mergeMissingHistoryMessages([sent], [b]).messages.map((row) => row.id), ["history-9-send-1", "user-s1-8"]);
  // Once the run's reply has its saved position, rows saved before it go above
  // the turn and rows saved after it below.
  const turn = [drawn("user-s1-8", "user", "A", 9), drawn("assistant-s1-9", "assistant", "done", 12)];
  const during = { id: "history-11", role: "user", content: "ping" };
  const after = { id: "history-13", role: "user", content: "later" };
  assert.deepEqual(mergeMissingHistoryMessages(turn, [b, during, after]).messages.map((row) => row.id),
    ["history-9-send-1", "history-11", "user-s1-8", "assistant-s1-9", "history-13"]);
  // An earlier turn the page ran is history like any other.
  const earlier = [drawn("user-s1-2", "user", "old", 2), drawn("assistant-s1-3", "assistant", "ok", 3),
    { id: "history-4", role: "user", content: "next" }];
  assert.deepEqual(mergeMissingHistoryMessages(earlier, [{ id: "history-5", role: "assistant", content: "x" }])
    .messages.map((row) => row.id), ["user-s1-2", "assistant-s1-3", "history-4", "history-5"]);
  // Pieces of one saved row sort by their number.
  assert.deepEqual(mergeMissingHistoryMessages([b], [a]).messages.map((row) => row.id), ["history-9-send-0", "history-9-send-1"]);
});

test("an earlier turn the page ran keeps its saved place behind its last one", () => {
  // The engine keeps user-/assistant-<session>-<n> on rows of turns that
  // ended: only the page's last turn stays last (#1364).
  const shown = [
    drawn("user-s1-2", "user", "first", 2), drawn("assistant-s1-3", "assistant", "one", 3),
    drawn("user-s1-4", "user", "second", 5), drawn("assistant-s1-5", "assistant", "two", 7),
  ];
  const between = { id: "history-4", role: "user", content: "theirs" };
  assert.deepEqual(mergeMissingHistoryMessages(shown, [between]).messages.map((row) => row.id),
    ["user-s1-2", "assistant-s1-3", "history-4", "user-s1-4", "assistant-s1-5"]);
});

test("an older calls row sharing a call id with the running reply is still added", () => {
  // Providers that reuse call ids (call_0 each turn): a calls row saved
  // before the rows shown ahead of the running turn is not that turn's (#1358).
  const call = { type: "tool-call", toolCallId: "call_0", result: "ok" };
  const shown = [
    { id: "history-10", role: "user", content: "earlier" },
    { id: "history-11", role: "assistant", content: "answer" },
    drawn("user-s1-2", "user", "now", 12),
    { id: "assistant-s1-3", role: "assistant", content: [call, { type: "text", text: "Working" }] },
  ];
  const older = { id: "history-3", role: "assistant", content: [{ ...call, result: "old" }] };
  const own = { id: "history-13", role: "assistant", content: [call] };
  assert.deepEqual(mergeMissingHistoryMessages(shown, [older, own]).added.map((row) => row.id), ["history-3"]);
});

test("with nothing saved above the running turn, a calls row is not hidden on a call id alone", () => {
  // No history row shown above the reply and no saved position for the send:
  // nothing bounds the reply's turn, so a reused call id hides nothing (#1358).
  const call = { type: "tool-call", toolCallId: "call_0", result: "ok" };
  const reply = { id: "assistant-s1-1", role: "assistant", content: [call, { type: "text", text: "Working" }] };
  const older = { id: "history-3", role: "assistant", content: [{ ...call, result: "old" }] };
  assert.deepEqual(mergeMissingHistoryMessages([{ id: "user-s1-0", role: "user", content: "now" }, reply], [older])
    .added.map((row) => row.id), ["history-3"]);
  // Once the send has its saved position, the turn's own calls row is drawn.
  const placed = [drawn("user-s1-0", "user", "now", 5), drawn("assistant-s1-1", "assistant", reply.content, 7)];
  const own = { id: "history-6", role: "assistant", content: [call] };
  assert.deepEqual(mergeMissingHistoryMessages(placed, [older, own]).added.map((row) => row.id), ["history-3"]);
});

test("a saved send the page already draws, in flight or not, is not added again", () => {
  // The page's own row of a send names its request id; its saved copy names
  // the same id. A run that ended before the engine matched them must not
  // leave the send drawn twice (#1359).
  const sent = { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: "r1" } } };
  const copy = named(saved(0, "hello"), { requestIds: ["r1"] });
  const other = named(saved(1, "hello"), { requestIds: ["r2"] });
  assert.deepEqual(mergeMissingHistoryMessages([sent], [copy, other]).added, [other]);
});

// #1359: the words of a turn the page ran show once, whatever its run did.
const words = (text: string) => [{ type: "text", text }];
const reply = (index: number, content: unknown) => ({ id: `history-${index}`, role: "assistant", content });
const mine = (id: string, text: string, requestId: string) =>
  ({ id, role: "user", content: text, metadata: { custom: { requestId } } });

test("a saved text row the settled reply already shows is not added again", () => {
  // The agent answered, then went on (another message was steered in): the
  // run's reply drew both answers, and history saves the first as a row of its
  // own inside the turn.
  const call = { type: "tool-call", toolCallId: "c1", result: "ok" };
  const shown = [drawn("user-s1-0", "user", "go", 0),
    drawn("assistant-s1-1", "assistant", [{ type: "text", text: "First answer." }, call, { type: "text", text: "Second answer." }], 4)];
  const first = reply(2, words("First answer."));
  const calls = reply(3, [call]);
  assert.deepEqual(mergeMissingHistoryMessages(shown, [first, calls]).added, []);
  // Words the reply does not show are another row's.
  const other = reply(2, words("Something else."));
  assert.deepEqual(mergeMissingHistoryMessages(shown, [other]).added, [other]);
});

test("while the page's run streams, the saved rows of its turn stay out", () => {
  const sent = mine("user-s1-4", "hello", "r1");
  const writing = { id: "assistant-s1-5", role: "assistant", content: words("Wor") };
  const copy = named(saved(10, "hello"), { requestIds: ["r1"] });
  const answer = reply(11, words("Working"));
  const theirs = named(saved(12, "ping"), { requestIds: ["r9"] });
  const older = reply(9, words("earlier turn"));
  // The reply was saved a moment before its last words reached the page.
  assert.deepEqual(withoutOwnTurnRows([sent, writing], [older, copy, answer, theirs], [older, copy, answer, theirs]),
    [older, theirs]);
  // Before the send is saved nothing places the turn: every row is another's.
  assert.deepEqual(withoutOwnTurnRows([sent, writing], [older], [older]), [older]);
  // A page with no row of its own takes them all.
  assert.deepEqual(withoutOwnTurnRows([saved(1, "x")], [copy, answer], [copy, answer]), [copy, answer]);
});

test("a send left by a run that ended early keeps its saved copy out while a later send runs", () => {
  // The first run's stream dropped; its rows stayed. A second send is now in flight.
  const first = mine("user-s1-4", "one", "r1");
  const partial = { id: "assistant-s1-5", role: "assistant", content: [{ type: "tool-call", toolCallId: "c1" }] };
  const second = { ...mine("user-s1-6", "two", "r2"), metadata: { isOptimistic: true, custom: { requestId: "r2" } } };
  const history = [named(saved(10, "one"), { requestIds: ["r1"] }), reply(11, [{ type: "tool-call", toolCallId: "c1", result: "ok" }]),
    named(saved(12, "two"), { requestIds: ["r2"] }), reply(13, words("done"))];
  assert.deepEqual(withoutOwnTurnRows([first, partial, second], history, history), []);
});

test("a run that ended before its turn is rebuilt on the saved history under the ids it drew", () => {
  const call = { type: "tool-call", toolCallId: "c1", result: "ok" };
  const before = { id: "history-0", role: "user", content: words("earlier") };
  const shown = [before, mine("user-s1-1", "one", "r1"),
    { id: "assistant-s1-2", role: "assistant", content: [{ type: "tool-call", toolCallId: "c1" }] },
    mine("user-s1-3", "two", "r2"), { id: "assistant-s1-4", role: "assistant", content: words("done") }];
  const loaded = [before, named({ id: "history-1", role: "user", content: words("one") }, { requestIds: ["r1"] }),
    reply(2, [call]), named({ id: "history-3", role: "user", content: words("two") }, { requestIds: ["r2"] }),
    reply(4, words("done"))];
  const rows = rebaseOntoHistory(shown, loaded, 5);
  // Each send keeps the id it was drawn with. A reply's saved words keep the
  // reply's id; a row of calls alone is its saved row.
  assert.deepEqual(rows.map((row) => row.id), ["history-0", "user-s1-1", "history-2", "user-s1-3", "assistant-s1-4"]);
  assert.deepEqual(rows.map(historyMessageIndex), [0, 1, 2, 3, 4]);
  assert.deepEqual(rows.map((row) => row.content), loaded.map((row) => row.content));
  assert.equal(rows[0], before);
  assert.equal(hasUnsettledRows(rows), false);
  // Already rebuilt: the same rows again.
  assert.deepEqual(rebaseOntoHistory(rows, loaded, 5), rows);
});

test("a reply history does not hold yet stays behind its send, without the rows it already shows", () => {
  // The stream dropped mid-reply: the page shows a call and half an answer,
  // history has the call's row only.
  const call = { type: "tool-call", toolCallId: "c1", result: "ok" };
  const half = { id: "assistant-s1-2", role: "assistant", content: [call, { type: "text", text: "Half an ans" }] };
  const shown = [mine("user-s1-1", "one", "r1"), half];
  const sent = named({ id: "history-1", role: "user", content: words("one") }, { requestIds: ["r1"] });
  const rows = rebaseOntoHistory(shown, [sent, reply(2, [call])], 3);
  assert.deepEqual(rows.map((row) => row.id), ["user-s1-1", "assistant-s1-2"]);
  assert.equal(rows[1], half);
  assert.equal(hasUnsettledRows(rows), true);
  // A row the reply does not show is added behind it.
  const more = reply(3, [{ type: "tool-call", toolCallId: "c2", result: "ok" }]);
  assert.deepEqual(rebaseOntoHistory(shown, [sent, reply(2, [call]), more], 4).map((row) => row.id),
    ["user-s1-1", "assistant-s1-2", "history-3"]);
  // Once history holds the whole answer, it replaces the reply.
  const whole = reply(3, words("Half an answer."));
  assert.deepEqual(rebaseOntoHistory(shown, [sent, reply(2, [call]), whole], 4).map((row) => row.id),
    ["user-s1-1", "history-2", "history-3"]);
  // A turn saved as words alone keeps the reply's id, and reads as saved from then on.
  const plain = [mine("user-s1-1", "one", "r1"), { id: "assistant-s1-2", role: "assistant", content: words("An answer") }];
  const answered = rebaseOntoHistory(plain, [sent, reply(2, words("An answer."))], 3);
  assert.deepEqual(answered.map((row) => row.id), ["user-s1-1", "assistant-s1-2"]);
  assert.deepEqual(answered[1].content, words("An answer."));
  assert.equal(historyMessageIndex(answered[1]), 2);
  assert.equal(shownMessagesChanged(answered, [sent, reply(2, words("An edited answer."))], 3), true);
});

test("a send history does not hold yet stays last, with its reply", () => {
  const before = { id: "history-0", role: "user", content: words("earlier") };
  const sent = mine("user-s1-1", "one", "r1");
  const answer = { id: "assistant-s1-2", role: "assistant", content: words("done") };
  const rows = rebaseOntoHistory([before, sent, answer], [before], 1);
  assert.deepEqual(rows, [before, sent, answer]);
  assert.equal(rows[1], sent);
});

test("a reply that drew nothing goes once its send is saved", () => {
  const sent = named({ id: "history-1", role: "user", content: words("one") }, { requestIds: ["r1"] });
  const rows = rebaseOntoHistory([mine("user-s1-1", "one", "r1"), { id: "assistant-s1-2", role: "assistant", content: "" }], [sent], 2);
  assert.deepEqual(rows.map((row) => row.id), ["user-s1-1"]);
});

test("a rebuilt thread takes changed rows in place and drops rows history no longer has", () => {
  const user = { id: "history-0", role: "user", content: words("go") };
  const pendingCall = reply(1, [{ type: "tool-call", toolCallId: "t1" }]);
  const heard = reply(2, words("Let me check"));
  const shown = [user, pendingCall, heard];
  const done = reply(1, [{ type: "tool-call", toolCallId: "t1", result: "ok" }]);
  const next = { id: "history-2", role: "user", content: words("stop") };
  // The call got its result; a talk-over took the reply away and a new row has its place.
  const rows = rebaseOntoHistory(shown, [user, done, next], 3);
  assert.deepEqual(rows, [user, done, next]);
  assert.equal(rows[0], user);
  // Older pages and rows saved after the history was read stay as they are.
  const older = { id: "history-0", role: "user", content: words("old page") };
  const late = drawn("user-s1-9", "user", "late", 7);
  assert.deepEqual(rebaseOntoHistory([older, reply(5, words("a")), late], [reply(5, words("b"))], 6).map((row) => row.id),
    ["history-0", "history-5", "user-s1-9"]);
});

test("a rebuilt thread keeps a settled turn as the run drew it", () => {
  const call = { type: "tool-call", toolCallId: "c1", result: "ok" };
  const answer = drawn("assistant-s1-1", "assistant", [call, { type: "text", text: "Working" }], 2);
  const shown = [drawn("user-s1-0", "user", "hello", 0), answer, { id: "history-3", role: "user", content: words("old words") }];
  const loaded = [{ id: "history-0", role: "user", content: words("hello") }, reply(1, [call]), reply(2, words("Working")),
    { id: "history-3", role: "user", content: words("new words") }];
  const rows = rebaseOntoHistory(shown, loaded, 4);
  assert.deepEqual(rows.map((row) => row.id), ["user-s1-0", "assistant-s1-1", "history-3"]);
  assert.equal(rows[1], answer);
  assert.deepEqual(rows[2].content, words("new words"));
});

test("an earlier turn the page ran ends where its next one starts", () => {
  // The earlier reply has no saved row (its run ended early) and the page's
  // next send is saved at 4: only the last reply may still be streaming.
  const call = { type: "tool-call", toolCallId: "call_0", result: "ok" };
  const shown = [
    drawn("user-s1-0", "user", "a", 0), { id: "assistant-s1-1", role: "assistant", content: [call] },
    drawn("user-s1-2", "user", "b", 4), { id: "assistant-s1-3", role: "assistant", content: [call] },
  ];
  const first = reply(1, [call]);
  // Saved in the earlier turn, and nothing the earlier reply shows: it is added.
  const unseen = reply(2, [{ type: "tool-call", toolCallId: "call_7", result: "ok" }]);
  const own = reply(5, [call]);
  const answer = reply(6, words("done"));
  assert.deepEqual(mergeMissingHistoryMessages(shown, [first, unseen, own, answer]).added, [unseen]);
});
