import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { runBrowserSuites } from "../scripts/browser-acceptance-runner.mjs";

async function fixture(name, source) {
  const root = await mkdtemp(path.join(os.tmpdir(), `kollab-runner-${name}-`));
  const script = path.join(root, "fixture.mjs");
  await writeFile(script, source);
  return { root, script };
}

test("continues after a failure and does not pass unrelated secrets to workers", async () => {
  const { root, script } = await fixture("isolation", `
    console.log(JSON.stringify({ secret: process.env.KOLLAB_TEST_SECRET ?? null, home: process.env.HOME }));
    if (process.env.HOME.includes('bad-')) process.exitCode = 3;
  `);
  process.env.KOLLAB_TEST_SECRET = "test-only-secret";
  try {
    const suites = [{ name: "bad", script }, { name: "good", script }];
    const results = await runBrowserSuites({ suites, workers: 2, timeoutMs: 3000, root, artifacts: root });
    assert.deepEqual(results.map((result) => result.ok), [false, true]);
    assert.notEqual(results[0].artifacts, results[1].artifacts);
    const output = JSON.parse(await readFile(path.join(results[1].artifacts, "output.log"), "utf8"));
    assert.equal(output.secret, null);
    assert.equal(output.home, results[1].artifacts);
    assert.equal(JSON.parse(await readFile(path.join(root, "results.json"), "utf8")).length, 2);
  } finally {
    delete process.env.KOLLAB_TEST_SECRET;
  }
});

test("times out the owned worker and reports failure", async () => {
  const { root, script } = await fixture("timeout", "setInterval(() => {}, 1000);");
  const results = await runBrowserSuites({ suites: [{ name: "stalled", script }], workers: 1, timeoutMs: 300, root, artifacts: root });
  assert.equal(results[0].ok, false);
  assert.equal(results[0].timedOut, true);
});

test("rejects invalid worker count and duplicate suites before spawning", async () => {
  const { root, script } = await fixture("validation", "process.exit(0);");
  const suite = { name: "same", script };
  await assert.rejects(runBrowserSuites({ suites: [suite], workers: 0, timeoutMs: 1, root, artifacts: root }), /workers/);
  await assert.rejects(runBrowserSuites({ suites: [suite, suite], workers: 2, timeoutMs: 1, root, artifacts: root }), /unique/);
});

test("runs independent workers concurrently", async () => {
  const { root } = await fixture("parallel", "");
  const scripts = [];
  for (const name of ["first", "second"]) {
    const peer = name === "first" ? "second" : "first";
    const script = path.join(root, `${name}.mjs`);
    const marker = path.join(root, name);
    const peerMarker = path.join(root, peer);
    await writeFile(script, `
      import { existsSync, writeFileSync } from 'node:fs';
      writeFileSync(${JSON.stringify(marker)}, 'started');
      const deadline = Date.now() + 2000;
      const timer = setInterval(() => {
        if (existsSync(${JSON.stringify(peerMarker)})) { clearInterval(timer); process.exit(0); }
        if (Date.now() > deadline) { clearInterval(timer); process.exit(1); }
      }, 10);
    `);
    scripts.push({ name, script });
  }
  const results = await runBrowserSuites({ suites: scripts, workers: 2, timeoutMs: 3000, root, artifacts: root });
  assert.ok(results.every((result) => result.ok));
});
