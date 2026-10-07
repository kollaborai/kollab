// Run: node --test tests/tool-summary.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { summarizeToolCall } from "../src/utils/tool-summary.ts";

test("MCP filesystem tools show the file they touch", () => {
  assert.equal(
    summarizeToolCall("read_file", JSON.stringify({ path: "/repo/docs/notes.md" })),
    "Read File · notes.md",
  );
});

test("native file tools keep showing their file", () => {
  assert.equal(
    summarizeToolCall("file_read", JSON.stringify({ file: "src/app.py" })),
    "Read File · app.py",
  );
});

test("tools without a file argument keep their plain name", () => {
  assert.equal(summarizeToolCall("web_search", JSON.stringify({ query: "ruby" })), "Web Search");
});
