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
    const waitUsers = (text) => page.waitForFunction(
      (expected) => document.getElementById("users")?.textContent === expected, text, { timeout: 5000 },
    ).catch(async () => {
      assert.fail(`expected user messages "${text}" but the page shows "${await page.locator("#users").innerText()}"`);
    });
    // The running thread draws an empty assistant placeholder after the rows,
    // and adjacent assistant rows render as one message.
    const waitImported = (text) => page.waitForFunction(
      (expected) => document.getElementById("assistant")?.textContent?.includes(expected),
      text, { timeout: 5000 },
    ).catch(async () => {
      assert.fail(`expected an assistant row "${text}" but the page shows "${await shown()}"`);
    });
    return { page, emit, waitShown, waitUsers, waitImported, submit, finishSubmission };
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
    // Reconnect reconciliation can recover text the stream missed while the
    // local send owned the thread; the new canonical history must be applied
    // after that send ends.
    async "reconciles history refreshed during a pending submission"() {
      const t = await open();
      await t.emit(token("Alpha"));
      await t.waitShown("Alpha");
      await t.submit();
      await t.page.evaluate(() => window.refreshHistory());
      await t.page.waitForFunction(() => document.getElementById("history-count")?.textContent === "1");
      assert.equal(await t.page.locator("#running").innerText(), "running");
      await t.finishSubmission();
      await t.waitShown("AlphaBeta");
      await t.page.close();
    },
    // #1356: the engine saves a held send before its stream answers. A history
    // sync in that window brings the saved row; the pending copy must not
    // stay drawn beside it.
    async "shows a held send once after history saves it"() {
      const t = await open();
      await t.submit();
      await t.waitUsers("hello");
      await t.page.evaluate(() => window.refreshSentHistory());
      await t.waitImported("Working");
      await t.waitUsers("hello");
      await t.finishSubmission();
      await t.waitUsers("hello");
      await t.page.close();
    },
    // The pending copy's id follows the shown row count, so it moves when the
    // busy turn's rows sync first; the saved copy arriving later is still it.
    async "shows a held send once when its saved copy arrives in a later sync"() {
      const t = await open();
      await t.submit();
      await t.waitUsers("hello");
      await t.page.evaluate(() => window.refreshBusyHistory());
      await t.waitImported("Busy work");
      await t.page.evaluate(() => window.refreshSentHistory());
      await t.waitImported("Working");
      await t.waitUsers("busy|hello");
      await t.finishSubmission();
      await t.waitUsers("busy|hello");
      await t.page.close();
    },
    // An earlier message with the same text is not the saved copy of this send.
    async "keeps a held send beside an earlier identical message"() {
      const t = await open();
      await t.page.evaluate(() => window.seedSentHistory());
      await t.waitUsers("hello");
      await t.submit();
      await t.waitUsers("hello|hello");
      await t.page.evaluate(() => window.refreshSentHistory());
      await t.waitImported("Working");
      await t.waitUsers("hello|hello");
      await t.finishSubmission();
      await t.waitUsers("hello|hello");
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
