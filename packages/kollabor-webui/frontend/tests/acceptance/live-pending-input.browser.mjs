// Issue #593: a window watching a turn it did not start must keep that turn's
// output that arrives while its own submission is pending (the assistant
// transport marks the thread running until the daemon answers).
// Run after `npm ci`: npm run test:e2e:live-pending-input
// Browser: CHROME_PATH if set, else playwright-core's chromium. The fixture
// uses a fake API and synthetic events; no engine, provider or key is used.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const browserErrors = [];

const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  clearScreen: false,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
let browser;
let exitCode = 0;

const token = (text, request_id = "r1") => ({ type: "token", text, request_id });
const finishFrames = 'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n';

try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });

  /** Fresh page; its submission is held until `release()`. */
  async function open() {
    const page = await browser.newPage();
    page.on("pageerror", (error) => browserErrors.push(error.message));
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route("**/sessions/s1/assistant", async (route) => {
      await held;
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: finishFrames,
      });
    });
    await page.goto(`${origin}/tests/acceptance/live-pending-input.html`, { waitUntil: "domcontentloaded" });
    await page.locator("#send").waitFor();
    const emit = (event) => page.evaluate((e) => window.liveEvent(e), event);
    const shown = () => page.locator("#assistant").innerText();
    const waitShown = (text) => page.waitForFunction(
      (expected) => document.getElementById("assistant")?.textContent === expected, text, { timeout: 5000 },
    ).catch(async () => {
      assert.fail(`expected "${text}" but the page shows "${await shown()}"`);
    });
    const submit = async () => {
      await page.locator("#send").click();
      await page.waitForFunction(() => document.getElementById("running")?.textContent === "running");
    };
    const finishSubmission = async () => {
      release();
      await page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
    };
    return { page, emit, waitShown, submit, finishSubmission };
  }

  const scenarios = {
    // The issue's reproduction: Beta arrives while the submission is pending.
    async "keeps output received while a submission is pending"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Beta"));
      await t.finishSubmission();
      await t.emit(token("Gamma"));
      await t.waitShown("AlphaBetaGamma");
      await t.page.close();
    },
    // Beta is the last thing the turn says: nothing later pushes it out.
    async "shows output received during the pending window once it ends"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Beta"));
      await t.finishSubmission();
      await t.waitShown("AlphaBeta");
      await t.page.close();
    },
    // Another request's events during the window must not join this turn.
    async "does not mix another request into the visible turn"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Other", "r2"));
      await t.emit(token("Beta"));
      await t.finishSubmission();
      await t.emit(token("Gamma"));
      await t.waitShown("AlphaBetaGamma");
      await t.page.close();
    },
    // The turn ends while the submission is pending: the text stays, once.
    async "keeps a turn that completes during the pending window"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Beta"));
      await t.emit({ type: "turn_complete", request_id: "r1" });
      await t.finishSubmission();
      await t.emit(token("late"));
      await t.waitShown("AlphaBeta");
      await t.page.close();
    },
    // Cancellation is terminal for this request even though this page's send
    // still owns the thread. Its buffered text is shown once; a late token is
    // not allowed to restart the cancelled reply.
    async "keeps a followed reply cancelled during the pending window"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Beta"));
      await t.emit({ type: "turn_cancelled", request_id: "r1" });
      await t.finishSubmission();
      await t.emit(token("late"));
      await t.waitShown("AlphaBeta");
      await t.page.close();
    },
    // A queued request has its own identity. It cannot replace the followed
    // request while our send is pending, but can start normally once idle.
    async "keeps a queued request separate from the followed reply"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit({ type: "turn_complete", request_id: "r1" });
      await t.emit(token("Queued", "r2"));
      await t.finishSubmission();
      assert.equal(await t.page.locator("#assistant").innerText(), "Alpha");
      await t.page.close();
    },
    // A followed stream can close without a terminal event. The text remains
    // live and can continue if that same request resumes later.
    async "retains a followed reply when its terminal event is missing"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.emit(token("Beta"));
      await t.finishSubmission();
      await t.waitShown("AlphaBeta");
      await t.emit(token("Gamma"));
      await t.waitShown("AlphaBetaGamma");
      await t.page.close();
    },
    // Output of a turn this page never saw start is not guessed at.
    async "ignores events of an unseen turn while pending"() {
      const t = await open();
      await t.submit();
      await t.emit(token("Beta"));
      await t.finishSubmission();
      await t.emit(token("Gamma"));
      await t.waitShown("Gamma");
      await t.page.close();
    },
  };

  for (const [name, run] of Object.entries(scenarios)) {
    try {
      await run();
      console.log(`PASS ${name}`);
    } catch (error) {
      exitCode = 1;
      console.log(`FAIL ${name}: ${error.message}`);
    }
  }
  if (browserErrors.length) {
    exitCode = 1;
    console.log(`browser errors:\n${browserErrors.join("\n")}`);
  }
} finally {
  await browser?.close();
  await vite.close();
}
process.exit(exitCode);
