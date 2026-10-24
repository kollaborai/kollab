// Run: node --test tests/live-follow.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { refreshesOnEvent } from "../src/live-follow.ts";

/** What App's event follower does: refresh on the events the policy names. */
const refreshed = (types: string[]) => types.filter(refreshesOnEvent);

test("an external message shows mid-turn, without waiting for turn_complete", () => {
  const mid = ["token", "tool_start", "hub_message", "tool_result", "history_updated", "token"];
  assert.deepEqual(refreshed(mid), ["hub_message", "history_updated"]);
});

test("a turn's end, an error and a permission prompt still reload", () => {
  assert.deepEqual(refreshed(["turn_complete", "error", "permission_request"]), ["turn_complete", "error", "permission_request"]);
});

test("token and tool traffic never reloads the history", () => {
  assert.deepEqual(refreshed(["token", "thinking", "tool_start", "tool_result", "usage_update"]), []);
});
