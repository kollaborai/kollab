// Run: node --test tests/history-merge.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { mergeMissingHistoryMessages, shownMessagesChanged } from "../src/history-merge.ts";

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
