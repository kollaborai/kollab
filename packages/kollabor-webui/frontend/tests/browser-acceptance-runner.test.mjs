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

test("continues after a failure and isolates X11 credentials outside CI Xvfb mode", async () => {
  const { root, script } = await fixture("isolation", `
    console.log(JSON.stringify({
      secret: process.env.KOLLAB_TEST_SECRET ?? null,
      home: process.env.HOME,
      display: process.env.DISPLAY ?? null,
      xauthority: process.env.XAUTHORITY ?? null,
    }));
    if (process.env.HOME.includes('bad-')) process.exitCode = 3;
  `);
  const names = ["KOLLAB_TEST_SECRET", "DISPLAY", "XAUTHORITY", "KOLLAB_BROWSER_XVFB", "CI"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  process.env.KOLLAB_TEST_SECRET = "test-only-secret";
  process.env.DISPLAY = ":99";
  process.env.XAUTHORITY = "/tmp/kollab-runner-test.Xauthority";
  process.env.KOLLAB_BROWSER_XVFB = "1";
  process.env.CI = "false";
  try {
    const suites = [
      { name: "bad", script, requiresXvfb: true },
      { name: "good", script, requiresXvfb: true },
    ];
    const results = await runBrowserSuites({ suites, workers: 2, timeoutMs: 3000, root, artifacts: root });
    assert.deepEqual(results.map((result) => result.ok), [false, true]);
    assert.notEqual(results[0].artifacts, results[1].artifacts);
    const output = JSON.parse(await readFile(path.join(results[1].artifacts, "output.log"), "utf8"));
    assert.equal(output.secret, null);
    assert.notEqual(output.home, results[1].artifacts);
    assert.match(output.home, /kb-good-/);
    assert.equal(output.display, null, "local X11 display must stay outside the worker environment by default");
    assert.equal(output.xauthority, null, "local Xauthority credentials must stay outside the worker environment by default");
    assert.equal(JSON.parse(await readFile(path.join(root, "results.json"), "utf8")).length, 2);
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("forwards X11 credentials only to headed suites in CI Xvfb mode", async () => {
  const { root, script } = await fixture("xvfb-env", `
    console.log(JSON.stringify({
      secret: process.env.KOLLAB_TEST_SECRET ?? null,
      display: process.env.DISPLAY ?? null,
      xauthority: process.env.XAUTHORITY ?? null,
    }));
  `);
  const names = ["KOLLAB_TEST_SECRET", "DISPLAY", "XAUTHORITY", "KOLLAB_BROWSER_XVFB", "CI"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  process.env.KOLLAB_TEST_SECRET = "test-only-secret";
  process.env.DISPLAY = ":98";
  process.env.XAUTHORITY = "/tmp/xvfb-run-test.Xauthority";
  process.env.KOLLAB_BROWSER_XVFB = "1";
  process.env.CI = "true";
  try {
    const results = await runBrowserSuites({
      suites: [
        { name: "xvfb", script, requiresXvfb: true },
        { name: "headless", script },
      ],
      workers: 2,
      timeoutMs: 3000,
      root,
      artifacts: root,
    });
    const [headed, plain] = await Promise.all(results.map(async (result) => {
      assert.equal(result.ok, true);
      return JSON.parse(await readFile(path.join(result.artifacts, "output.log"), "utf8"));
    }));
    assert.equal(headed.display, ":98");
    assert.equal(headed.xauthority, "/tmp/xvfb-run-test.Xauthority");
    assert.equal(plain.display, null, "headless suites must not receive X11 credentials");
    assert.equal(plain.xauthority, null, "headless suites must not receive Xauthority");
    assert.equal(headed.secret, null, "Xvfb support must not widen general environment inheritance");
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("fails fast when the CI Xvfb wrapper has no Xauthority file", async () => {
  const { root, script } = await fixture("xvfb-missing-auth", "process.exit(0);");
  const names = ["DISPLAY", "XAUTHORITY", "KOLLAB_BROWSER_XVFB", "CI"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  process.env.DISPLAY = ":98";
  delete process.env.XAUTHORITY;
  process.env.KOLLAB_BROWSER_XVFB = "1";
  process.env.CI = "true";
  try {
    const [result] = await runBrowserSuites({
      suites: [{ name: "xvfb", script, requiresXvfb: true }],
      workers: 1,
      timeoutMs: 3000,
      root,
      artifacts: root,
    });
    assert.equal(result.ok, false);
    assert.match(result.error, /xvfb-run did not provide DISPLAY and XAUTHORITY/);
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
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
    { name: "gallery", script: "tests/gallery-browser-acceptance.mjs" },
    { name: "scrollbar-chrome", script: "tests/scrollbar-chrome-browser-acceptance.mjs" },
    { name: "agent-sidebar", script: "tests/agent-sidebar-glyph-browser-acceptance.mjs" },
    { name: "team-launch", script: "tests/team-launch-browser-acceptance.mjs" },
    { name: "remote-chat-lifecycle", script: "tests/remote-chat-lifecycle-browser-acceptance.mjs" },
    { name: "setup-guide", script: "tests/setup-guide-browser-acceptance.mjs" },
    { name: "whats-new", script: "tests/whats-new-browser-acceptance.mjs" },
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
  assert.deepEqual(
    browserSuites.filter((suite) => suite.requiresXvfb).map((suite) => suite.name),
    ["scrollbar-chrome"],
    "headed Linux suites must be explicitly marked for Xvfb",
  );
});

test("the scrollbar chrome browser proof runs in an explicit headed CI lane", async () => {
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
  const workflow = await readFile(path.join(repoRoot, ".github/workflows/ci.yml"), "utf8");
  assert.ok(
    /lane: core\s*\n\s*suites:[^\n]*\bscrollbar-chrome\b/.test(workflow),
    "scrollbar-chrome must run in an explicit support_webui_tests CI lane",
  );
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const headedSuites = [];
  for (const suite of browserSuites) {
    const source = await readFile(path.join(frontendRoot, suite.script), "utf8");
    if (/headless\s*:\s*false/.test(source)) headedSuites.push(suite.name);
  }
  const xvfbSuites = browserSuites.filter((suite) => suite.requiresXvfb).map((suite) => suite.name);
  assert.deepEqual(xvfbSuites, headedSuites, "every headed suite must be marked for Xvfb");
  const coreSuites = /lane: core\s*\n\s*suites:([^\n]+)/.exec(workflow)?.[1]
    .split(",")
    .map((name) => name.trim());
  assert.ok(coreSuites, "the core browser lane must declare its suite list");
  for (const name of xvfbSuites) {
    assert.ok(coreSuites.includes(name), `${name} requires Xvfb and must stay in the core lane`);
  }

  const installStep = workflow.match(/- name: Install virtual display for headed browser acceptance([\s\S]*?)(?=\n      - name:)/)?.[1];
  assert.ok(installStep, "the core lane must install Xvfb and Xauthority support");
  assert.match(
    installStep,
    /if: matrix\.lane == 'core'[\s\S]*sudo apt-get update && sudo apt-get install -y --no-install-recommends xvfb xauth/,
    "only the core lane should install its virtual display and Xauthority helper",
  );
  const browserStep = workflow.match(/- name: Run web UI browser acceptance([\s\S]*?)(?=\n      - name: Upload browser acceptance evidence)/)?.[1];
  assert.ok(browserStep, "the browser acceptance step must exist");
  const xvfbBranch = [...browserStep.matchAll(/if \[ "\$\{\{ matrix\.lane \}\}" = "core" \]; then([\s\S]*?)\n\s*else([\s\S]*?)\n\s*fi/g)]
    .find((match) => match[1].includes("KOLLAB_BROWSER_XVFB=1"));
  assert.ok(xvfbBranch, "the Xvfb wrapper must stay inside the core-only shell branch");
  assert.match(xvfbBranch[1], /KOLLAB_BROWSER_XVFB=1 xvfb-run -a/);
  assert.match(xvfbBranch[1], /test -n "\$DISPLAY" && test -s "\$XAUTHORITY"/);
  assert.match(xvfbBranch[2], /npm run test:e2e:parallel/);
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
