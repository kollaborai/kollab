import assert from "node:assert/strict";
import { test } from "node:test";

import { olderLoadFailed, olderLoadRunning } from "../src/history-status.ts";

test("a failed load of older messages stays on screen while a scroll tries again", () => {
  // Each scroll to the top tries again. Swapping the failure for the loading
  // line and back on every try makes the red strip blink.
  const loading = olderLoadRunning(null, "s1");
  assert.deepEqual(loading, { sessionId: "s1", kind: "loading-older", message: "Loading older messages…" });
  const failed = olderLoadFailed(loading, "s1");
  assert.equal(failed?.kind, "error");
  assert.match(failed?.message ?? "", /^Couldn't load older messages\./);
  // The next try, and its failure: the same strip, not drawn again.
  assert.equal(olderLoadRunning(failed, "s1"), failed);
  assert.equal(olderLoadFailed(failed, "s1"), failed);
});

test("a load of older messages replaces any other history status", () => {
  const other = { sessionId: "s1", kind: "error" as const, message: "Couldn't load the conversation." };
  assert.equal(olderLoadRunning(other, "s1")?.kind, "loading-older");
  // Another chat's failure is not this chat's.
  const theirs = olderLoadFailed(null, "s2");
  assert.equal(olderLoadRunning(theirs, "s1")?.kind, "loading-older");
  assert.equal(olderLoadRunning(theirs, "s1")?.sessionId, "s1");
});
