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

test("tilde fences are left alone", () => {
  assert.deepEqual(todoBlocks("~~~\ntodo:\n  [x] inside a tilde fence\n~~~"), []);
});

// A fence closes only on a line of the same character that is at least as long as the one that opened it, with
// nothing after it (CommonMark), so an example fence inside a longer one, or one of the other character, is text.
test("a shorter fence inside a longer one does not close it", () => {
  const text = ["````md", "```", "todo:", "  [x] inside the outer fence", "```", "````"].join("\n");

  assert.deepEqual(todoBlocks(text), []);
});

test("a fence of the other character does not close a fence", () => {
  assert.deepEqual(todoBlocks(["~~~", "```", "todo:", "  [x] inside the tilde fence", "```", "~~~"].join("\n")), []);
  assert.deepEqual(todoBlocks(["```", "~~~", "todo:", "  [x] inside the backtick fence", "~~~", "```"].join("\n")), []);
});

test("a fence with text after it does not close a fence", () => {
  assert.deepEqual(todoBlocks(["```", "```js", "todo:", "  [x] still inside", "```"].join("\n")), []);
});

test("a longer fence closes a fence", () => {
  const text = ["```", "code", "`````", "", "todo:", "  [x] after the fence"].join("\n");

  assert.deepEqual(todoBlocks(text).map((block) => todoItems(block).length), [1]);
});

test("an unclosed fence hides everything after it", () => {
  assert.deepEqual(todoBlocks("```\ntodo:\n  [x] the reply was cut off in a fence"), []);
});

test("backticks on one line are inline code, not a fence", () => {
  const text = "```code``` is how it was written\n\ntodo:\n  [x] a\n  [ ] b";

  assert.deepEqual(todoBlocks(text).map((block) => todoItems(block).length), [2]);
});

test("a fence line ends a block and the text after it is read again", () => {
  const text = ["todo:", "  [x] a", "```", "code", "```", "", "todo:", "  [ ] b"].join("\n");

  assert.deepEqual(todoBlocks(text).map((block) => todoItems(block).map((item) => item.text)), [["a"], ["b"]]);
});

// Markdown reads a fence wherever a container holds it: on a list item's own line (its text and its close are indented
// by the item's content offset), under a numbered item and a nested bullet, or in a quote. An indented block after a
// blank line is code too. The thread draws all of these as code, so none of them is the agent's list.
const REAL = ["todo:", "  [x] the real list"].join("\n");

test("a fence opened on a list item line is code, and so is its indented close", () => {
  const text = ["- ```", "  todo:", "    [ ] example only", "  ```", "", REAL].join("\n");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("a tilde fence opened on a list item line is code too", () => {
  const text = ["* ~~~", "  todo:", "    [ ] example only", "  ~~~", "", REAL].join("\n");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("a fence under a numbered item and a nested bullet is code", () => {
  const text = ["1. step one", "   - ```md", "     todo:", "       [ ] example only", "     ```", "", REAL].join("\n");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("a fence in a quote is code", () => {
  const text = ["> ```", "> todo:", ">   [ ] example only", "> ```", "", REAL].join("\n");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("an indented block after a blank line is code", () => {
  assert.deepEqual(todoBlocks(["Plan:", "", "    todo:", "      [ ] example only", "", REAL].join("\n")), [REAL]);
  assert.deepEqual(todoBlocks(["Plan:", "", "\ttodo:", "\t  [ ] example only", "", REAL].join("\n")), [REAL]);
});

test("a list item that is not code still holds a list, next to a fence", () => {
  const text = ["```", "code", "```", "", "- notes", "  todo:", "    [x] inside the item"].join("\n");

  assert.deepEqual(todoItems(todoBlocks(text)[0]).map((item) => item.text), ["inside the item"]);
});

test("a fence with Windows line endings hides its text and the list after it is read", () => {
  const text = ["```", "todo:", "  [ ] example only", "```", "", REAL].join("\r\n");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("a fence with old Mac line endings hides its text and the list after it is read", () => {
  const text = ["```", "todo:", "  [ ] example only", "```", "", REAL].join("\r");

  assert.deepEqual(todoBlocks(text), [REAL]);
});

test("the blocks of a text are a fresh array on every call", () => {
  todoBlocks(REAL).push("not a block");

  assert.deepEqual(todoBlocks(REAL), [REAL]);
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

test("an example list in a list item's fence is not the latest list", () => {
  const example = ["- ```", "  todo:", "    [ ] example only", "  ```"].join("\n");

  assert.equal(latestTodoBlock([assistant(OLD), assistant(`how a list looks:\n\n${example}`)]), OLD);
});

test("hub notes and user messages never count as the agent's own list", () => {
  const messages = [assistant(OLD), hubNote("todo:\n  [ ] someone else's task"), user("todo:\n  [ ] typed by a person")];

  assert.equal(latestTodoBlock(messages), OLD);
});

test("no block anywhere is an empty string", () => {
  assert.equal(latestTodoBlock([]), "");
  assert.equal(latestTodoBlock([user("hi"), assistant("hello")]), "");
});
