import assert from "node:assert/strict";
import test from "node:test";
import { GoalCancellationTracker, isGoalCommand } from "../src/utils/goal-cancel.ts";

const goal = (text: string) => ({ type: "add-message", message: { parts: [{ type: "text", text }] } });
const ordinary = goal("write a poem");

test("only authored goal text is a goal command", () => {
  assert.equal(isGoalCommand(goal("/goal show")), true);
  assert.equal(isGoalCommand(goal("/GOALS\nshow")), true);
  assert.equal(isGoalCommand(goal("/goalkeeper")), false);
  assert.equal(isGoalCommand(ordinary), false);
  assert.equal(isGoalCommand({ type: "add-tool-result" }), false);
});

test("a pending goal cancels only its securely generated handle", () => {
  const tracker = new GoalCancellationTracker();
  const first = goal("/goal show"), second = goal("/goal pending");
  const id = tracker.prepare([first]);
  assert.match(id ?? "", /^goal-[0-9a-f]{32}$/);
  assert.deepEqual(tracker.cancel([first]), { goal: true, id });
  assert.deepEqual(tracker.cancel([]), { goal: true, id });
  tracker.finish();
  const next = tracker.prepare([second]);
  assert.notEqual(next, id);
  assert.deepEqual(tracker.cancel([second]), { goal: true, id: next });
  tracker.finish();
  const retry = tracker.prepare([second]);
  assert.notEqual(retry, next);
  assert.deepEqual(tracker.cancel([second]), { goal: true, id: retry });
});

test("an early goal stop never falls back to another model turn", () => {
  const tracker = new GoalCancellationTracker();
  assert.deepEqual(tracker.cancel([goal("/goal show")]), { goal: true });
  assert.deepEqual(tracker.cancel([ordinary, goal("/goal show")]), { goal: false });
  tracker.prepare([ordinary]);
  assert.deepEqual(tracker.cancel([ordinary]), { goal: false });
});
