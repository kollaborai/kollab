import { expect, test } from "vitest";

import type { ChatRow, InputInfo } from "../src/chat-stream";
import { threadActivityOf } from "../src/components/gems/activity";

// The gem's activity while this page's chat runs, read from the chat store's rows
// and inputs (#1451). A failed connection or a failed latest input reads as an error.

const at = "2026-11-25T10:00:00Z";

const input = (id: string): ChatRow => ({
  id,
  seq: 1,
  kind: "input",
  role: "user",
  turn_id: "t1",
  status: "complete",
  parts: [{ type: "text", text: "hello" }],
  created_at: at,
});

const reply = (id: string, parts: ChatRow["parts"]): ChatRow => ({
  id,
  seq: 2,
  kind: "reply",
  role: "assistant",
  turn_id: "t1",
  status: "running",
  parts,
  created_at: at,
});

const started = (id: string): InputInfo => ({ id, state: "started", turn_id: "t1" });
const ended = (id: string, outcome: "completed" | "failed" | "cancelled"): InputInfo => ({
  id,
  state: "ended",
  turn_id: "t1",
  outcome,
});

test("nothing runs and the stream is fine: no activity", () => {
  const rows = [input("web-1"), reply("r1", [{ type: "text", text: "done" }])];
  expect(threadActivityOf(rows, { "web-1": ended("web-1", "completed") }, false, false)).toBeNull();
});

test("a failed connection reads as an error when nothing runs", () => {
  expect(threadActivityOf([], {}, false, true)).toBe("error");
});

test("the latest input ended failed reads as an error", () => {
  expect(threadActivityOf([input("web-1")], { "web-1": ended("web-1", "failed") }, false, false)).toBe("error");
});

test("an earlier failed input does not count once a later input completed", () => {
  const rows = [input("web-1"), input("web-2")];
  const inputs = { "web-1": ended("web-1", "failed"), "web-2": ended("web-2", "completed") };
  expect(threadActivityOf(rows, inputs, false, false)).toBeNull();
});

test("a running turn with no rows is thinking", () => {
  expect(threadActivityOf([], {}, true, false)).toBe("thinking");
});

test("a running turn whose last row is the user's own input is thinking", () => {
  expect(threadActivityOf([input("web-1")], { "web-1": started("web-1") }, true, false)).toBe("thinking");
});

test("a running reply with text is speaking", () => {
  const rows = [input("web-1"), reply("r1", [{ type: "text", text: "Looking" }])];
  expect(threadActivityOf(rows, { "web-1": started("web-1") }, true, false)).toBe("speaking");
});

test("a running reply whose last part is an open tool call acts out that tool", () => {
  const open = { type: "tool-call", tool_call_id: "c1", tool_name: "file_read", args: {} } as const;
  expect(threadActivityOf([input("web-1"), reply("r1", [open])], { "web-1": started("web-1") }, true, false)).toBe(
    "reading",
  );
});

test("a tool call that has its result is no longer running: the model reads it next", () => {
  const done = { type: "tool-call", tool_call_id: "c1", tool_name: "file_read", args: {}, result: "ok" } as const;
  expect(threadActivityOf([input("web-1"), reply("r1", [done])], { "web-1": started("web-1") }, true, false)).toBe(
    "thinking",
  );
});

test("a note row is not the reply: a running turn reads as thinking", () => {
  const note: ChatRow = {
    id: "n1",
    seq: 3,
    kind: "note",
    role: "system",
    turn_id: null,
    status: "complete",
    parts: [{ type: "text", text: "lapis: hi" }],
    created_at: at,
  };
  expect(threadActivityOf([input("web-1"), note], { "web-1": started("web-1") }, true, false)).toBe("thinking");
});
