// Run: node --test tests/terminal-dialect.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { terminalDialectToMarkdown } from "../src/utils/terminal-dialect.ts";

test("terminal checkboxes become task items", () => {
  assert.equal(
    terminalDialectToMarkdown("todo:\n[x] read notes.txt\n[ ] run ls -la"),
    "todo:\n- [x] read notes.txt\n- [ ] run ls -la",
  );
});

test("a label and its item on one line split into a list", () => {
  assert.equal(
    terminalDialectToMarkdown("todo: [x] ls -la lists 1 entry"),
    "todo:\n\n- [x] ls -la lists 1 entry",
  );
});

test("code fences, status tags and prose are left alone", () => {
  const text = "status: [ok] build passed\n```\n[ ] not a task\n```\nsee [x] above";
  assert.equal(terminalDialectToMarkdown(text), text);
});
