import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("session readiness helper loads without starting browser tooling", () => {
  const helperUrl = new URL("./learn-browser-acceptance.mjs", import.meta.url).href;
  const script = `
    await import(${JSON.stringify(helperUrl)});
    console.log("session readiness helper loaded");
  `;
  const child = spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
    encoding: "utf8",
    timeout: 1_500,
  });

  assert.equal(child.status, 0, child.error?.message || child.stderr || child.stdout);
  assert.match(child.stdout, /session readiness helper loaded/);
});
