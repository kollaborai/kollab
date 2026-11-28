// Regression coverage for issue #1317: invalid saved steps must not erase dismissal.
import assert from "node:assert/strict";
import test from "node:test";

import { loadGuideState, shouldAutoOpenGuide } from "../src/setup/guide-model.ts";

test("an unknown saved step preserves dismissal and stays closed at startup", () => {
  const saved = JSON.stringify({ step: "bogus", dismissed: true });

  assert.deepEqual(loadGuideState(saved), { step: "welcome", dismissed: true });
  assert.equal(shouldAutoOpenGuide(saved), false);
});
