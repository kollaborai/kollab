// Run: node --test tests/todo-list.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { latestTodoBlock, todoBlocks, todoItems } from "../src/utils/todo-list.ts";

const BLOCK = ["todo:", "  [x] inspected both units", "  [ ] checked current host state"].join("\n");

test("a todo label with checkbox lines under it is one block; other labels are not", () => {
  const text = [
    "Task complete",
    "",
    BLOCK,
    "",
    "assessment:",
    "  [warn] systemd is degraded",
    "  [ok] nothing was restarted",
  ].join("\n");

  assert.deepEqual(todoBlocks(text), [BLOCK]);
});

test("items keep their state and their text", () => {
  assert.deepEqual(todoItems(BLOCK), [
    { done: true, text: "inspected both units" },
    { done: false, text: "checked current host state" },
  ]);
});

test("the one-line form is a block too", () => {
  const text = "status:\n  ok\n\ntodo: [x] checked hub status and work queue\n\nnext:\n  go";

  const [block] = todoBlocks(text);

  assert.equal(block, "todo: [x] checked hub status and work queue");
  assert.deepEqual(todoItems(block), [{ done: true, text: "checked hub status and work queue" }]);
});

test("every todo block of a text comes out in order, and the label is case-insensitive", () => {
  const text = ["todo:", "  [ ] first", "", "work, work", "", "Todo:", "  [x] first", "  [ ] second"].join("\n");

  assert.deepEqual(todoBlocks(text).map((block) => todoItems(block).length), [1, 2]);
});

test("a checklist under another label is not a todo list", () => {
  assert.deepEqual(todoBlocks("plan:\n  [x] a\n  [ ] b"), []);
});

test("a todo label without checkbox lines is not a block", () => {
  assert.deepEqual(todoBlocks("todo:\nnothing written yet"), []);
  assert.deepEqual(todoBlocks("todo: finish the report"), []);
});

test("code fences are left alone", () => {
  assert.deepEqual(todoBlocks("```\ntodo:\n  [x] inside a fence\n```"), []);
});

test("a blank line between the label and its first item is tolerated, one after the items ends the block", () => {
  const text = "todo:\n\n  [x] a\n  [ ] b\n\n  [ ] not part of it";

  assert.deepEqual(todoItems(todoBlocks(text)[0]).map((item) => item.text), ["a", "b"]);
});

test("a wrapped item continues on the next, deeper indented line", () => {
  const block = [
    "todo:",
    "  [ ] check a few current read-only host indicators to avoid",
    "      treating old logs as proof of a live outage",
    "  [ ] report findings",
  ].join("\n");

  assert.deepEqual(todoBlocks(block), [block]);
  assert.deepEqual(todoItems(block).map((item) => item.text), [
    "check a few current read-only host indicators to avoid treating old logs as proof of a live outage",
    "report findings",
  ]);
});

const message = (role: string, texts: string[], custom: Record<string, unknown> = {}) => ({
  role,
  parts: texts.map((text) => ({ type: "text", text })),
  metadata: { custom },
});
const assistant = (...texts: string[]) => message("assistant", texts);
const user = (text: string) => message("user", [text]);
const hubNote = (text: string) =>
  message("assistant", [text], { hub: { from: "sapphire", to: "lapis", text, observed: false } });

const OLD = "todo:\n  [x] one\n  [ ] two\n  [ ] three\n  [ ] four";
const NEW = "todo:\n  [x] one\n  [x] two\n  [ ] three";

test("the latest list is the last block of the newest message that has one", () => {
  const messages = [assistant(OLD), user("go on"), assistant("working on it"), assistant("done with step two", NEW)];

  assert.equal(latestTodoBlock(messages), NEW);
});

test("a newer message without a list does not hide the list", () => {
  assert.equal(latestTodoBlock([assistant(NEW), user("thanks"), assistant("anytime")]), NEW);
});

test("inside one message the last text part that holds a block wins", () => {
  const parts = [{ type: "text", text: OLD }, { type: "tool-call" }, { type: "text", text: NEW }];

  assert.equal(latestTodoBlock([{ role: "assistant", parts }]), NEW);
});

test("hub notes and user messages never count as the agent's own list", () => {
  const messages = [assistant(OLD), hubNote("todo:\n  [ ] someone else's task"), user("todo:\n  [ ] typed by a person")];

  assert.equal(latestTodoBlock(messages), OLD);
});

test("no block anywhere is an empty string", () => {
  assert.equal(latestTodoBlock([]), "");
  assert.equal(latestTodoBlock([user("hi"), assistant("hello")]), "");
});
