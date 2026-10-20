// Run: node --test tests/live-activity.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import {
  liveActivity,
  nextLiveActivity,
  NO_LIVE_ACTIVITY,
  presenceActivity,
  toolActivity,
} from "../src/components/gems/activity.ts";

type Event = { type?: string; [key: string]: unknown };
const fold = (...events: Event[]) => events.reduce(nextLiveActivity, NO_LIVE_ACTIVITY);

test("a turn this page did not start animates its running tool", () => {
  assert.equal(liveActivity(fold({ type: "thinking" })), "thinking");
  assert.equal(liveActivity(fold({ type: "tool_start", tool_id: "a", tool_name: "file_read: x.py" })), "reading");
  assert.equal(liveActivity(fold({ type: "tool_start", tool_id: "a", tool_name: "terminal: ls" })), "typing");
  assert.equal(liveActivity(fold({ type: "tool_start", tool_id: "a", tool_name: "hub_msg: lapis" })), "messaging");
});

test("the newest running tool wins and a finished one hands back to thinking", () => {
  const two = fold(
    { type: "tool_start", tool_id: "a", tool_name: "file_grep: x" },
    { type: "tool_start", tool_id: "b", tool_name: "terminal: ls" },
  );
  assert.equal(liveActivity(two), "typing");
  assert.equal(liveActivity(nextLiveActivity(two, { type: "tool_result", tool_id: "b" })), "searching");
  assert.equal(
    liveActivity(fold({ type: "tool_start", tool_id: "a", tool_name: "terminal: ls" }, { type: "tool_result", tool_id: "a" })),
    "thinking",
  );
});

test("tokens speak and the turn's end clears the gem", () => {
  assert.equal(liveActivity(fold({ type: "token" })), "speaking");
  assert.equal(liveActivity(fold({ type: "tool_start", tool_id: "a", tool_name: "terminal: ls" }, { type: "turn_complete" })), null);
  assert.equal(liveActivity(fold({ type: "token" }, { type: "error" })), null);
  assert.equal(liveActivity(fold({ type: "hub_message" })), null);
});

test("a harness call reads its ACP kind, not its title", () => {
  assert.equal(toolActivity("Read README.md", "read"), "reading");
  assert.equal(toolActivity("grep -rn foo", "search"), "searching");
  assert.equal(toolActivity("Edit app.py", "edit"), "typing");
  assert.equal(toolActivity("harness_tool", "fetch"), "searching");
  assert.equal(
    liveActivity(fold({ type: "tool_start", tool_id: "t1", tool_name: "Read README.md", tool_type: "read", acp_passthrough: true })),
    "reading",
  );
});

test("a row whose chat is not open acts out the tool its presence carries", () => {
  assert.equal(presenceActivity({ name: "terminal", type: "terminal" }), "typing");
  assert.equal(presenceActivity({ name: "file_read", type: "file_read" }), "reading");
  assert.equal(presenceActivity({ name: "web-search", type: "web_search" }), "searching");
  assert.equal(presenceActivity({ name: "hub_msg", type: "hub_msg" }), "messaging");
  assert.equal(presenceActivity({ name: "task_create", type: "task_create" }), "tasking");
  // A harness call's presence names its ACP kind.
  assert.equal(presenceActivity({ name: "read", type: "read" }), "reading");
  assert.equal(presenceActivity({ name: "execute", type: "execute" }), "typing");
  // Between tools the gem falls back to presence state.
  assert.equal(presenceActivity(null), null);
  assert.equal(presenceActivity(undefined), null);
  assert.equal(presenceActivity({ name: "", type: "" }), null);
});
