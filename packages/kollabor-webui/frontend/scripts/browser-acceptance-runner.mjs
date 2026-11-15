import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const browserSuites = Object.freeze([
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
]);

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function cleanEnvironment(home, chromePath, artifacts) {
  const inherited = ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "CI"];
  const env = Object.fromEntries(inherited.filter((name) => process.env[name]).map((name) => [name, process.env[name]]));
  return {
    ...env,
    HOME: home,
    USERPROFILE: home,
    TMPDIR: home,
    KOLLAB_NO_KEYRING: "1",
    KOLLAB_BROWSER_ARTIFACT_DIR: artifacts,
    LEARN_CAPTURE_SCREENSHOTS: "0",
    PROFILE_ROWS_CAPTURE: "0",
    PROFILE_COPY_CAPTURE: "0",
    ...(chromePath ? { CHROME_PATH: chromePath } : {}),
  };
}

function signalOwnedGroup(child) {
  if (process.platform === "win32") {
    return { sent: false, detail: "graceful process-tree signaling is unavailable on Windows" };
  }
  try {
    process.kill(-child.pid, "SIGTERM");
    return { sent: true, detail: null };
  } catch (error) {
    if (error.code === "ESRCH") return { sent: true, detail: null };
    return { sent: false, detail: `could not signal owned process group: ${error.message}` };
  }
}

function ownedGroupExists(child) {
  if (process.platform === "win32") return null;
  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") return true;
    throw error;
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function prepareWorkerWorkspace({ suite, artifacts }) {
  const suiteArtifacts = await mkdtemp(path.join(artifacts, `${suite.name}-`));
  // Chromium creates a Unix socket below TMPDIR; long artifact paths can
  // exceed the kernel socket path limit.
  const tempRoot = process.platform === "linux" ? "/tmp" : os.tmpdir();
  const home = await mkdtemp(path.join(tempRoot, `kb-${suite.name}-`));
  return { suiteArtifacts, home };
}

// Per-suite limit. The monolithic learn suite took 231 to 381 s on CI runners before it was split into
// parallel suites (#1022); 900 s still clears twice the slowest run any suite has shown.
export const DEFAULT_SUITE_TIMEOUT_MS = 900_000;

export function suiteTimeoutMs(env = process.env) {
  return Number(env.KOLLAB_BROWSER_TIMEOUT_MS ?? String(DEFAULT_SUITE_TIMEOUT_MS));
}

/** Run disjoint browser scripts in isolated homes, bounded workers, and owned process groups. */
export async function runBrowserSuites({
  suites,
  workers,
  timeoutMs,
  root,
  artifacts,
  chromePath,
  graceMs = 5000,
  pollIntervalMs = 100,
  spawnChild = spawn,
  signalGroup = signalOwnedGroup,
  groupExists = ownedGroupExists,
  prepareWorkspace = prepareWorkerWorkspace,
}) {
  if (!Number.isInteger(workers) || workers < 1 || workers > 4) throw new Error("workers must be an integer from 1 to 4");
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("timeout must be positive");
  if (!Number.isFinite(graceMs) || graceMs < 0) throw new Error("grace period cannot be negative");
  if (!Number.isFinite(pollIntervalMs) || pollIntervalMs <= 0) throw new Error("poll interval must be positive");
  if (!suites.length || suites.some((suite) => !/^[a-z0-9][a-z0-9-]{0,63}$/.test(suite.name)) ||
      new Set(suites.map((suite) => suite.name)).size !== suites.length) {
    throw new Error("browser suites must have unique names and cannot be empty");
  }

  const results = new Array(suites.length);
  const active = new Set();
  let next = 0;
  let abort = null;

  const requestAbort = (origin, reason) => {
    if (abort) return;
    abort = { origin, reason };
    for (const record of active) record.stop(origin);
  };

  const runSuite = async (index) => {
    const suite = suites[index];
    const started = Date.now();
    let workspace;
    try {
      workspace = await prepareWorkspace({ suite, artifacts });
    } catch (error) {
      return {
        name: suite.name, status: "failed", ok: false, code: null, signal: null,
        timedOut: false, cleanup: "not-started", error: error.message,
        seconds: (Date.now() - started) / 1000, artifacts: null,
      };
    }
    const { suiteArtifacts, home } = workspace;
    let output = Buffer.alloc(0);
    const collect = (data) => {
      output = Buffer.concat([output, Buffer.from(data)]).subarray(-1_000_000);
    };
    // Another worker may have timed out while this worker awaited its isolated
    // directories. The scheduler reservation alone does not authorize spawn.
    if (abort) {
      const result = {
        name: suite.name,
        status: "skipped",
        ok: false,
        timedOut: false,
        cleanup: "not-started",
        error: `not started because scheduling stopped after ${abort.origin}`,
        seconds: (Date.now() - started) / 1000,
        artifacts: suiteArtifacts,
      };
      await writeFile(path.join(suiteArtifacts, "output.log"), "");
      console.log(`SKIP ${suite.name} (${result.seconds.toFixed(1)}s) ${suiteArtifacts}`);
      return result;
    }
    let child;
    try {
      child = spawnChild(process.execPath, [suite.script], {
        cwd: root,
        env: cleanEnvironment(home, chromePath, suiteArtifacts),
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      return {
        name: suite.name, status: "failed", ok: false, code: null, signal: null,
        timedOut: false, cleanup: "not-started", error: error.message,
        seconds: (Date.now() - started) / 1000, artifacts: suiteArtifacts,
      };
    }
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);

    let exitInfo;
    let exitSettled = false;
    let timer;
    let resolveExit;
    let resolveClose;
    let resolveCancel;
    let stopInfo = null;
    const exitPromise = new Promise((resolve) => { resolveExit = resolve; });
    const closePromise = new Promise((resolve) => { resolveClose = resolve; });
    const cancelPromise = new Promise((resolve) => { resolveCancel = resolve; });
    child.once("error", (error) => {
      if (exitSettled) return;
      exitSettled = true;
      exitInfo = { code: null, signal: null, error: error.message };
      resolveExit({ kind: "exit", info: exitInfo });
    });
    child.once("exit", (code, signal) => {
      if (exitSettled) return;
      exitSettled = true;
      exitInfo = { code, signal, error: null };
      resolveExit({ kind: "exit", info: exitInfo });
    });
    child.once("close", () => resolveClose());

    const record = {
      name: suite.name,
      stop: (origin) => {
        if (stopInfo) return;
        stopInfo = { origin, requestedAt: Date.now() };
        stopInfo.signal = signalGroup(child);
        resolveCancel({ kind: "cancel", origin });
      },
    };
    active.add(record);

    const timeoutPromise = new Promise((resolve) => {
      timer = setTimeout(() => resolve({ kind: "timeout" }), timeoutMs);
    });
    let first = await Promise.race([exitPromise, timeoutPromise, cancelPromise]);
    if (first.kind === "timeout") {
      record.timedOut = true;
      requestAbort(suite.name, "worker timeout");
      first = { kind: "timeout" };
    }

    // Normal leader exit is not sufficient: a browser/server descendant may
    // still occupy the owned process group. Give it the same graceful window.
    let groupState = null;
    let cleanup = "unverified";
    const deadline = Date.now() + graceMs;
    if (process.platform !== "win32") {
      while (Date.now() <= deadline) {
        try {
          groupState = groupExists(child);
        } catch (error) {
          groupState = null;
          record.cleanupError = error.message;
          requestAbort(suite.name, "could not verify owned process-group cleanup");
          break;
        }
        if (exitSettled && groupState === false) {
          cleanup = "complete";
          break;
        }
        if (exitSettled && groupState === true && !stopInfo) {
          record.descendantLeak = true;
          requestAbort(suite.name, "worker exited while its process group remained alive");
        }
        if (Date.now() < deadline) await delay(Math.min(pollIntervalMs, Math.max(1, deadline - Date.now())));
      }
      if (cleanup !== "complete") cleanup = "unresolved";
      if (cleanup === "complete") {
        const closed = await Promise.race([
          closePromise.then(() => true),
          delay(Math.max(0, deadline - Date.now())).then(() => false),
        ]);
        if (!closed) cleanup = "unresolved";
      }
    } else {
      cleanup = exitSettled ? "leader-exited-descendants-unverified" : "unresolved-unverified";
      if (!exitSettled && !stopInfo) requestAbort(suite.name, "worker timeout; Windows process-tree cleanup cannot be verified");
    }
    clearTimeout(timer);
    active.delete(record);

    const timedOut = Boolean(record.timedOut || first.kind === "timeout");
    const peerAborted = Boolean(stopInfo && stopInfo.origin !== suite.name);
    const unresolved = cleanup === "unresolved" || cleanup === "unresolved-unverified";
    const failed = !exitSettled || exitInfo?.error || exitInfo?.code !== 0 || timedOut || peerAborted ||
      cleanup !== "complete" || unresolved || record.descendantLeak;
    const status = unresolved ? "unresolved-cleanup"
      : timedOut ? "timeout"
        : peerAborted ? "aborted"
          : failed ? "failed" : "passed";
    const ended = exitInfo ?? { code: null, signal: null, error: null };
    const result = {
      name: suite.name,
      status,
      ok: !failed,
      code: ended.code,
      signal: ended.signal,
      timedOut,
      cleanup,
      processId: child.pid ?? null,
      ...(process.platform !== "win32" && child.pid ? { processGroupId: child.pid } : {}),
      ...(stopInfo?.signal && !stopInfo.signal.sent ? { termination: stopInfo.signal.detail } : {}),
      ...(record.cleanupError ? { cleanupError: record.cleanupError } : {}),
      ...(abort ? { abortReason: abort.reason } : {}),
      error: ended.error ?? (unresolved ? "owned process group or child streams remained after graceful termination window" : null),
      seconds: (Date.now() - started) / 1000,
      artifacts: suiteArtifacts,
    };
    await writeFile(path.join(suiteArtifacts, "output.log"), output.toString("utf8"));
    console.log(`${result.ok ? "PASS" : "FAIL"} ${suite.name} (${result.seconds.toFixed(1)}s) ${suiteArtifacts}`);
    if (!result.ok) {
      console.error(output.toString("utf8").slice(-8000));
      if (unresolved) {
        console.error(`UNRESOLVED CLEANUP: ${suite.name} process ${result.processId ?? "unknown"}` +
          `${result.processGroupId ? ` (group ${result.processGroupId})` : ""}; graceful shutdown was not confirmed. ` +
          "The process tree may still be running; no force termination was attempted.");
      } else if (cleanup === "leader-exited-descendants-unverified") {
        console.error(`CLEANUP UNVERIFIED: ${suite.name} leader exited, but Windows descendant cleanup could not be confirmed.`);
      }
    }
    // An unresolved process cannot be force-stopped under the repository's
    // safety rules. Close our readers and detach only after the explicit
    // failure has been persisted, so CI can upload evidence and exit.
    if (unresolved || cleanup === "leader-exited-descendants-unverified") {
      child.stdout.destroy();
      child.stderr.destroy();
      child.unref();
    }
    return result;
  };

  const worker = async () => {
    while (next < suites.length && !abort) {
      const index = next++;
      results[index] = await runSuite(index);
      if (results[index].status === "unresolved-cleanup") {
        requestAbort(suites[index].name, "owned process group did not exit after graceful shutdown");
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(workers, suites.length) }, worker));
  for (let index = 0; index < suites.length; index += 1) {
    if (!results[index]) {
      results[index] = {
        name: suites[index].name,
        status: "skipped",
        ok: false,
        timedOut: false,
        cleanup: "not-started",
        error: `not started because scheduling stopped after ${abort?.origin ?? "an earlier failure"}`,
        artifacts: null,
      };
    }
  }
  await writeFile(path.join(artifacts, "results.json"), `${JSON.stringify(results, null, 2)}\n`);
  return results;
}

async function main() {
  const { chromium } = await import("playwright-core");
  const workers = Number(process.env.KOLLAB_BROWSER_WORKERS ?? "2");
  const timeoutMs = suiteTimeoutMs();
  const artifacts = process.env.KOLLAB_BROWSER_RESULTS_DIR
    ? path.resolve(process.env.KOLLAB_BROWSER_RESULTS_DIR)
    : await mkdtemp(path.join(os.tmpdir(), "kollab-browser-acceptance-"));
  await mkdir(artifacts, { recursive: true });
  const chromePath = process.env.CHROME_PATH || chromium.executablePath();
  // CI fans the suites out across parallel webui lane jobs (#1022); a lane
  // names its suites and anything unknown fails fast instead of silently
  // running nothing.
  const selected = process.env.KOLLAB_BROWSER_SUITES;
  const suites = selected
    ? selected.split(",").map((name) => name.trim()).filter(Boolean).map((name) => {
        const suite = browserSuites.find((entry) => entry.name === name);
        if (!suite) throw new Error(`KOLLAB_BROWSER_SUITES names unknown suite '${name}'`);
        return suite;
      })
    : browserSuites;
  if (!suites.length) throw new Error("KOLLAB_BROWSER_SUITES selected no suites");
  const results = await runBrowserSuites({ suites, workers, timeoutMs, root: frontendRoot, artifacts, chromePath });
  console.log(`Browser acceptance results: ${path.join(artifacts, "results.json")}`);
  if (results.some((result) => !result.ok)) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
