// Run: node --test tests/setup-guide-one-screen.test.ts (Node 24 strips the types).
// #1217: the welcome guide must not carry a second "Choose a provider" screen.
// Settings > Setup is the one provider screen; "Get Started" opens it when no
// real provider is set, and the engine's built-in profiles do not count as one.

import assert from "node:assert/strict";
import test from "node:test";

import {
  SETUP_STEPS,
  loadGuideState,
} from "../src/setup/guide-model.ts";

test("the guide has no provider card: Settings > Setup is the one provider screen", () => {
  assert.deepEqual(
    SETUP_STEPS.map((step) => step.id),
    ["welcome", "network", "agent", "done"],
  );
});

// Progress from the removed provider card returns to the welcome card, as the
// issue specifies; the guide then proceeds through its remaining cards.
test("progress saved at the removed provider card resumes at welcome", () => {
  const saved = JSON.stringify({ step: "provider", dismissed: false });
  assert.equal(loadGuideState(saved).step, "welcome");
});

test("a dismissed guide saved at the removed provider card stays dismissed", () => {
  const saved = JSON.stringify({ step: "provider", dismissed: true });
  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: true });
});
