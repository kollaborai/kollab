import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

test("Learn browser event tests finish without timing out their child process", () => {
  const testFile = fileURLToPath(new URL("./learn-browser-events.test.ts", import.meta.url));
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [
    "--test",
    "--test-name-pattern=session readiness bounds a stalled history body",
    testFile,
  ], {
    cwd: process.cwd(),
    env,
    encoding: "utf8",
    timeout: 20_000,
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
});
