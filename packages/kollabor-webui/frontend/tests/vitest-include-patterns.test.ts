import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { nodeTestSuites } from "../scripts/vitest-skips.ts";

// A per-file test list in vite.config.ts is a file every new test PR edits, so
// parallel PRs clash on it (#1474). Vitest finds its files by pattern instead.
test("vite.config.ts finds test files by pattern, not by a per-file list", () => {
  const config = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  const listed = config.match(/"(?:tests|src)\/[^"*]+\.test\.(?:ts|tsx|mjs)"/g) ?? [];
  assert.deepEqual(listed, []);
});

test("Vitest skips every Node suite, whichever quotes it imports node:test with", () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "vitest-skips-"));
  try {
    mkdirSync(path.join(root, "tests"));
    mkdirSync(path.join(root, "src"));
    writeFileSync(path.join(root, "tests/double.test.ts"), 'import { test } from "node:test";\n');
    writeFileSync(path.join(root, "tests/single.test.ts"), "import { test } from 'node:test';\n");
    writeFileSync(path.join(root, "tests/vitest.test.ts"), 'import { it } from "vitest";\n');
    assert.deepEqual(nodeTestSuites(root).sort(), ["tests/double.test.ts", "tests/single.test.ts"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
