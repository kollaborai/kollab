import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PassThrough } from "node:stream";
import test from "node:test";
import { existsSync } from "node:fs";
import { browserSuites, DEFAULT_SUITE_TIMEOUT_MS, runBrowserSuites, suiteTimeoutMs } from "../scripts/browser-acceptance-runner.mjs";

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
    assert.notEqual(output.home, results[1].artifacts);
    assert.match(output.home, /kb-good-/);
    assert.equal(JSON.parse(await readFile(path.join(root, "results.json"), "utf8")).length, 2);
  } finally {
    delete process.env.KOLLAB_TEST_SECRET;
  }
});

test("times out the owned worker and reports failure", async () => {
  const { root, script } = await fixture("timeout", "setInterval(() => {}, 1000);");
  const results = await runBrowserSuites({
    suites: [{ name: "stalled", script }, { name: "after-timeout", script }],
    workers: 1,
    timeoutMs: 300,
    graceMs: 1000,
    root,
    artifacts: root,
  });
  assert.equal(results[0].ok, false);
  assert.equal(results[0].timedOut, true);
  assert.equal(results[0].cleanup, "complete");
  assert.equal(results[1].status, "skipped", "timeout must stop dispatch even when graceful cleanup succeeds");
});

test("unresolved cleanup stops dispatch, records skipped suites, and never claims cleanup", async () => {
  const { root, script } = await fixture("noncooperative", "void 0;");
  let child;
  let signals = 0;
  let spawned = 0;
  const fakeSpawn = () => {
    spawned += 1;
    child = new EventEmitter();
    child.pid = 45678;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.unref = () => {};
    return child;
  };
  try {
    const suites = [{ name: "resistant", script }, { name: "must-skip", script }];
    const results = await runBrowserSuites({
      suites,
      workers: 1,
      timeoutMs: 15,
      graceMs: 20,
      pollIntervalMs: 2,
      root,
      artifacts: root,
      spawnChild: fakeSpawn,
      signalGroup: () => { signals += 1; return { sent: true, detail: null }; },
      groupExists: () => true,
    });
    assert.equal(spawned, 1, "runner must not start the queued suite after timeout");
    assert.equal(signals, 1, "runner requests graceful shutdown exactly once");
    assert.equal(results[0].status, "unresolved-cleanup");
    assert.equal(results[0].cleanup, "unresolved");
    assert.equal(results[0].ok, false);
    assert.equal(results[0].processGroupId, 45678);
    assert.equal(results[1].status, "skipped");
    assert.equal(results[1].ok, false);
    const saved = JSON.parse(await readFile(path.join(root, "results.json"), "utf8"));
    assert.equal(saved[0].cleanup, "unresolved");
    assert.equal(saved[1].status, "skipped");
    assert.match(saved[0].error, /may still be running|remained/);
  } finally {
    child?.stdout.destroy();
    child?.stderr.destroy();
  }
});

test("a worker released from workspace setup after timeout cannot spawn", async () => {
  const { root, script } = await fixture("setup-race", "void 0;");
  let child;
  let spawned = 0;
  let markLateStarted;
  let releaseLateSetup;
  const lateStarted = new Promise((resolve) => { markLateStarted = resolve; });
  const lateSetup = new Promise((resolve) => { releaseLateSetup = resolve; });
  const prepareWorkspace = async ({ suite, artifacts }) => {
    if (suite.name === "late-setup") {
      markLateStarted();
      await lateSetup;
    }
    return {
      suiteArtifacts: await mkdtemp(path.join(artifacts, `${suite.name}-`)),
      home: await mkdtemp(path.join(os.tmpdir(), `kb-${suite.name}-`)),
    };
  };
  const fakeSpawn = () => {
    spawned += 1;
    child = new EventEmitter();
    child.pid = 56789;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.unref = () => {};
    return child;
  };
  try {
    const run = runBrowserSuites({
      suites: [
        { name: "times-out", script },
        { name: "late-setup", script },
        { name: "queued", script },
      ],
      workers: 2,
      timeoutMs: 30,
      graceMs: 10,
      pollIntervalMs: 2,
      root,
      artifacts: root,
      prepareWorkspace,
      spawnChild: fakeSpawn,
      signalGroup: () => {
        releaseLateSetup();
        return { sent: true, detail: null };
      },
      groupExists: () => true,
    });
    await lateStarted;
    const results = await run;
    assert.equal(spawned, 1, "the delayed worker must recheck abort before spawning");
    assert.equal(results[0].status, "unresolved-cleanup");
    assert.equal(results[1].status, "skipped");
    assert.equal(results[2].status, "skipped");
    const saved = JSON.parse(await readFile(path.join(root, "results.json"), "utf8"));
    assert.deepEqual(saved.map(({ status }) => status), ["unresolved-cleanup", "skipped", "skipped"]);
  } finally {
    child?.stdout.destroy();
    child?.stderr.destroy();
  }
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

test("the browser suite list is pinned and every suite script exists", () => {
  // A suite dropped or renamed in browserSuites would silently remove its
  // coverage from CI while everything stays green; this pins the list (#1022).
  const expected = [
    { name: "learn", script: "tests/learn-browser-acceptance.mjs" },
    { name: "learn-topics", script: "tests/learn-topics-browser-acceptance.mjs" },
    { name: "learn-loadouts", script: "tests/learn-loadouts-browser-acceptance.mjs" },
    { name: "learn-notices", script: "tests/learn-notices-browser-acceptance.mjs" },
    { name: "learn-retries", script: "tests/learn-retries-browser-acceptance.mjs" },
    { name: "profile-rows", script: "tests/acceptance/profile-rows.browser.mjs" },
    { name: "profile-copy", script: "tests/acceptance/profile-copy.browser.mjs" },
    { name: "agent-sidebar", script: "tests/agent-sidebar-glyph-browser-acceptance.mjs" },
    { name: "team-launch", script: "tests/team-launch-browser-acceptance.mjs" },
    { name: "remote-chat-lifecycle", script: "tests/remote-chat-lifecycle-browser-acceptance.mjs" },
  ];
  assert.deepEqual(
    browserSuites.map(({ name, script }) => ({ name, script })),
    expected,
    "the parallel browser suite list changed; update this pin deliberately",
  );
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  for (const suite of browserSuites) {
    assert.ok(existsSync(path.join(frontendRoot, suite.script)), `${suite.name} script is missing: ${suite.script}`);
  }
});

test("the default suite timeout leaves headroom over the slowest observed CI run", () => {
  // The monolithic learn suite took 231 to 381 s on ubuntu-latest before the #1022 split;
  // the limit must clear twice the slowest run any suite has shown.
  assert.ok(DEFAULT_SUITE_TIMEOUT_MS >= 2 * 381_000, `default ${DEFAULT_SUITE_TIMEOUT_MS} ms is too tight`);
});

test("suiteTimeoutMs uses the default and honors the env override", () => {
  assert.equal(suiteTimeoutMs({}), DEFAULT_SUITE_TIMEOUT_MS);
  assert.equal(suiteTimeoutMs({ KOLLAB_BROWSER_TIMEOUT_MS: "1234" }), 1234);
});
