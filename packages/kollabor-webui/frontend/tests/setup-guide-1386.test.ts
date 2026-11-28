// Run: node --test tests/setup-guide-1386.test.ts (Node 24 strips the types).
// Unknown saved steps recover to welcome without undoing an explicit dismissal.

import assert from "node:assert/strict";
import test from "node:test";

import { loadGuideState, shouldAutoOpenGuide } from "../src/setup/guide-model.ts";

test("an unknown saved step does not reopen a dismissed welcome guide", () => {
  const saved = JSON.stringify({ step: "bogus", dismissed: true });

  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: true });
  assert.equal(shouldAutoOpenGuide(saved), false);
});

test("an unknown saved step without dismissal still opens the welcome guide", () => {
  const saved = JSON.stringify({ step: "bogus", dismissed: false });

  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: false });
  assert.equal(shouldAutoOpenGuide(saved), true);
});
