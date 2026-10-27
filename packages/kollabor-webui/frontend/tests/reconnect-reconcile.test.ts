// Run: node --test tests/reconnect-reconcile.test.ts (Node 24 strips the types).
// Reconnect/missed-event reconcile for the open chat, refs #304: e1294d403
// (PR #317) made every stream open reconcile from history, and the thread
// folds rows by message id. These tests pin that contract: a reload that
// only adds rows appends the missed ones once, an identical reconcile is a
// no-op, a reload whose missed events edited shown content swaps the thread
// whole, and the live event policy keeps external messages visible no matter
// when the stream came back.
import assert from "node:assert/strict";
import test from "node:test";
import { shownMessagesChanged } from "../src/history-merge.ts";
import { refreshesOnEvent } from "../src/live-follow.ts";

const user = { id: "history-0", role: "user", content: "go" };
const first = { id: "history-1", role: "assistant", content: "working" };
const late = { id: "history-2", role: "assistant", content: "done" };

test("a reconnect that missed new messages appends them once, never duplicates", () => {
  // The stream was down while history-2 landed; the reopen reconcile loads
  // the same ids plus the missed row. shownMessagesChanged is false, so the
  // thread takes the append merge: history-2 is added by id exactly once.
  assert.equal(shownMessagesChanged([user, first], [user, first, late]), false);
  // The stream drops and reopens again with nothing new: still a no-op.
  assert.equal(shownMessagesChanged([user, first, late], [user, first, late]), false);
});

test("a reconcile replaying rows the thread already shows changes nothing", () => {
  // The daemon answers a reopen with the full window including shown rows:
  // identical ids with identical content never flag, so a re-import by id
  // cannot branch the thread into a duplicate transcript.
  assert.equal(
    shownMessagesChanged(
      [user, first, late],
      [user, first, late, { id: "history-3", role: "assistant", content: "again" }],
    ),
    false,
  );
});

test("a reconnect whose missed events edited shown content reloads whole", () => {
  // A tool result folded into history-1 while the stream was down: the shown
  // card reads differently, so the reload replaces the thread instead of
  // leaving a stale card or duplicating the edited message.
  const step = (result?: string) => ({
    id: "history-1",
    role: "assistant",
    content: [{ type: "tool-call", toolCallId: "t1", ...(result === undefined ? {} : { result }) }],
  });
  assert.equal(shownMessagesChanged([user, step()], [user, step("done")]), true);
});

test("the event policy keeps external messages visible across reconnects", () => {
  // On every stream open the page reconciles regardless of what was missed;
  // hub_message and history_updated also refresh live, mid-turn included, so
  // an external message shows whether it arrived before or after the drop.
  assert.equal(refreshesOnEvent("hub_message"), true);
  assert.equal(refreshesOnEvent("history_updated"), true);
  assert.deepEqual(
    ["token", "thinking", "tool_start", "tool_result", "usage_update"].filter(refreshesOnEvent),
    [],
  );
});
