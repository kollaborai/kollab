// Run: node --test tests/setup-guide-1327.test.ts (Node 24 strips the types).
// An unknown persisted step must not erase the user's dismissal decision.

import assert from "node:assert/strict";
import test from "node:test";

import { loadGuideState, shouldAutoOpenGuide } from "../src/setup/guide-model.ts";

test("an unknown dismissed step resumes at welcome without reopening", () => {
  const saved = JSON.stringify({ step: "bogus", dismissed: true });

  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: true });
  assert.equal(shouldAutoOpenGuide(saved), false);
});

test("an unknown undismissed step recovers as a fresh welcome", () => {
  const saved = JSON.stringify({ step: "bogus", dismissed: false });

  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: false });
  assert.equal(shouldAutoOpenGuide(saved), true);
});
