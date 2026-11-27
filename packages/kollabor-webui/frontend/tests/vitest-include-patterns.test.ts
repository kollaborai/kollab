import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// A per-file test list in vite.config.ts is a file every new test PR edits, so
// parallel PRs clash on it (#1474). Vitest finds its files by pattern instead.
test("vite.config.ts finds test files by pattern, not by a per-file list", () => {
  const config = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  const listed = config.match(/"(?:tests|src)\/[^"*]+\.test\.(?:ts|tsx|mjs)"/g) ?? [];
  assert.deepEqual(listed, []);
});
