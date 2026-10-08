// Run: node --test tests/format-content.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { formatContent } from "../src/utils/format-content.ts";

test("a multi-line output's last line carries no separator comma", () => {
  const text = formatContent(
    JSON.stringify({ success: true, output: "total 8\nnotes.txt", error: "" }),
  );
  const lines = text.split("\n");
  assert.ok(lines.includes("    notes.txt"), text);
  assert.ok(lines.includes('  "success": true,'), text);
  assert.ok(lines.includes('  "error": ""'), text);
});
