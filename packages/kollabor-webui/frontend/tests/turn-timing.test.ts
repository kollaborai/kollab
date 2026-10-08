// Run: node --test tests/turn-timing.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { historyTurnTiming } from "../src/turn-timing.ts";

test("a history turn is timed from the user's message to its final reply", () => {
  const timing = historyTurnTiming("2026-10-07T08:50:12.975632", "2026-10-07T08:50:26.955814", 1);
  assert.equal(timing?.totalStreamTime, 13980);
  assert.equal(timing?.toolCallCount, 1);
});

test("missing or backwards timestamps give no timing", () => {
  assert.equal(historyTurnTiming(null, "2026-10-07T08:50:26", 0), undefined);
  assert.equal(historyTurnTiming("2026-10-07T08:50:26", "2026-10-07T08:50:12", 0), undefined);
});
