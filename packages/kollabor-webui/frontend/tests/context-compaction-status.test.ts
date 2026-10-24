import assert from "node:assert/strict";
import test from "node:test";
import {
  contextCompactionStatusTitle,
  isUrgentContextCompactionStatus,
  parseContextCompactionStatus,
} from "../src/context-compaction-status.ts";

test("compaction status events provide actionable state and title", () => {
  const status = parseContextCompactionStatus({
    type: "context_compaction_status",
    status: "ready_to_retry",
    message: "The failed request was not retried.",
    action: "Retry the failed turn now.",
  });

  assert.deepEqual(status, {
    status: "ready_to_retry",
    message: "The failed request was not retried.",
    action: "Retry the failed turn now.",
  });
  assert.equal(contextCompactionStatusTitle(status!.status), "Context reduction ready");
  assert.equal(isUrgentContextCompactionStatus(status!.status), false);
});

test("paused and failed states are announced urgently", () => {
  for (const state of ["paused", "failed"] as const) {
    assert.equal(isUrgentContextCompactionStatus(state), true);
  }
  assert.equal(
    contextCompactionStatusTitle("paused"),
    "Automatic context compaction paused",
  );
  assert.equal(
    contextCompactionStatusTitle("failed"),
    "Context reduction needs attention",
  );
});

test("idle clears the banner and unrelated events are ignored", () => {
  assert.equal(
    parseContextCompactionStatus({
      type: "context_compaction_status",
      status: "idle",
    }),
    null,
  );
  assert.equal(parseContextCompactionStatus({ type: "usage_update" }), undefined);
});

test("unknown states fail closed to an actionable error banner", () => {
  const status = parseContextCompactionStatus({
    type: "context_compaction_status",
    status: "private_internal_state",
  });
  assert.equal(status?.status, "failed");
  assert.equal(status?.message, "Conversation context is being compacted.");
  assert.equal(isUrgentContextCompactionStatus(status!.status), true);
});
