import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("Learn acceptance helpers load within the deadline test child budget", () => {
  const helperUrl = new URL("./learn-browser-acceptance.mjs", import.meta.url).href;
  const child = spawnSync(process.execPath, ["--input-type=module", "--eval", `
    await import(${JSON.stringify(helperUrl)});
    console.log("learn acceptance helpers loaded");
  `], {
    encoding: "utf8",
    timeout: 1_500,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.match(child.stdout, /learn acceptance helpers loaded/);
});
