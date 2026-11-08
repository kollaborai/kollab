import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const browserSuites = Object.freeze([
  { name: "learn", script: "tests/learn-browser-acceptance.mjs" },
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

/** Run disjoint browser scripts in isolated homes, bounded workers, and owned process groups. */
export async function runBrowserSuites({ suites, workers, timeoutMs, root, artifacts, chromePath }) {
  if (!Number.isInteger(workers) || workers < 1 || workers > 4) throw new Error("workers must be an integer from 1 to 4");
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("timeout must be positive");
  if (!suites.length || suites.some((suite) => !/^[a-z0-9][a-z0-9-]{0,63}$/.test(suite.name)) ||
      new Set(suites.map((suite) => suite.name)).size !== suites.length) {
    throw new Error("browser suites must have unique names and cannot be empty");
  }
  const results = new Array(suites.length);
  let next = 0;
  const worker = async () => {
    while (next < suites.length) {
      const index = next++;
      const suite = suites[index];
      const suiteArtifacts = await mkdtemp(path.join(artifacts, `${suite.name}-`));
      // Chromium creates a Unix socket below TMPDIR; long CI artifact paths
      // can exceed the kernel socket path limit.
      const tempRoot = process.platform === "linux" ? "/tmp" : os.tmpdir();
      const home = await mkdtemp(path.join(tempRoot, `kb-${suite.name}-`));
      let output = Buffer.alloc(0);
      const collect = (data) => {
        output = Buffer.concat([output, Buffer.from(data)]).subarray(-1_000_000);
      };
      const started = Date.now();
      const child = spawn(process.execPath, [suite.script], {
        cwd: root,
        env: cleanEnvironment(home, chromePath, suiteArtifacts),
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout.on("data", collect);
      child.stderr.on("data", collect);
      let timedOut = false;
      let timer;
      let cleanupTimer;
      const exit = new Promise((resolve) => {
        child.once("error", (error) => resolve({ code: null, signal: null, error: error.message }));
        child.once("close", (code, signal) => resolve({ code, signal }));
        timer = setTimeout(() => {
          timedOut = true;
          try {
            if (process.platform === "win32") child.kill("SIGTERM");
            else process.kill(-child.pid, "SIGTERM");
          } catch (error) {
            if (error.code !== "ESRCH") collect(`Could not stop owned test: ${error.message}\n`);
          }
          cleanupTimer = setTimeout(() => {
            collect(`Owned test process ${child.pid} did not exit after SIGTERM.\n`);
            child.stdout.destroy();
            child.stderr.destroy();
            child.unref();
            resolve({ code: null, signal: null, error: "worker did not exit after SIGTERM" });
          }, 5000);
        }, timeoutMs);
      });
      const ended = await exit;
      clearTimeout(timer);
      clearTimeout(cleanupTimer);
      const text = output.toString("utf8");
      await writeFile(path.join(suiteArtifacts, "output.log"), text);
      results[index] = {
        name: suite.name,
        ok: ended.code === 0 && !timedOut,
        code: ended.code,
        signal: ended.signal,
        timedOut,
        error: ended.error ?? null,
        seconds: (Date.now() - started) / 1000,
        artifacts: suiteArtifacts,
      };
      console.log(`${results[index].ok ? "PASS" : "FAIL"} ${suite.name} (${results[index].seconds.toFixed(1)}s) ${suiteArtifacts}`);
      if (!results[index].ok) console.error(text.slice(-8000));
    }
  };
  await Promise.all(Array.from({ length: Math.min(workers, suites.length) }, worker));
  await writeFile(path.join(artifacts, "results.json"), `${JSON.stringify(results, null, 2)}\n`);
  return results;
}

async function main() {
  const { chromium } = await import("playwright-core");
  const workers = Number(process.env.KOLLAB_BROWSER_WORKERS ?? "2");
  const timeoutMs = Number(process.env.KOLLAB_BROWSER_TIMEOUT_MS ?? "180000");
  const artifacts = process.env.KOLLAB_BROWSER_RESULTS_DIR
    ? path.resolve(process.env.KOLLAB_BROWSER_RESULTS_DIR)
    : await mkdtemp(path.join(os.tmpdir(), "kollab-browser-acceptance-"));
  await mkdir(artifacts, { recursive: true });
  const chromePath = process.env.CHROME_PATH || chromium.executablePath();
  const results = await runBrowserSuites({ suites: browserSuites, workers, timeoutMs, root: frontendRoot, artifacts, chromePath });
  console.log(`Browser acceptance results: ${path.join(artifacts, "results.json")}`);
  if (results.some((result) => !result.ok)) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
