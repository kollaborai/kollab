// Issue #1358: when this page's turn ends, the chat must not flicker, draw a
// "2 / 2" branch picker, or reload whole (a blank frame and a scroll jump).
// Run after `npm ci`: npm run test:e2e:turn-settle
// Browser: CHROME_PATH if set, else playwright-core's chromium. The fixture
// uses a fake API; the test streams the page's send the final state the engine
// builds (the state the request sent, then the turn's provisional rows carrying
// their saved positions), holds the stream open as the engine does while it
// refreshes history, then ends it, and plays App's history reloads.
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

const text = (value) => [{ type: "text", text: value }];
const row = (index, role, value) => ({ id: `history-${index}`, role, content: text(value) });

/**
 * The engine's final state for a send of "hello" answered "Working": the rows
 * the request sent, then user-s1-<n> and assistant-s1-<n+1> mapped to their
 * saved rows (the engine sends the user row's words as a string).
 */
const finalState = (sent, saved, reply = saved + 1, replyContent = text("Working")) => {
  const at = sent.length;
  const messages = [
    ...sent,
    { id: `user-s1-${at}`, role: "user", content: "hello",
      metadata: { custom: { historySourceIndex: saved } } },
    { id: `assistant-s1-${at + 1}`, role: "assistant", content: replyContent,
      status: { type: "complete", reason: "stop" },
      metadata: { custom: { historySourceIndex: reply } } },
  ];
  const update = { type: "update-state", operations: [
    { type: "set", path: ["messages"], value: messages },
    { type: "set", path: ["synced"], value: { history: reply + 1, messages: messages.length } },
  ] };
  return `data: ${JSON.stringify(update)}\n\n`;
};
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

  /** Fresh page; `answer(saved)` streams its final state with "hello" saved at row `saved`, `finish()` ends it. */
  async function open() {
    const page = await browser.newPage();
    page.on("pageerror", (error) => browserErrors.push(error.message));
    await page.goto(`${origin}/tests/acceptance/turn-settle.html`, { waitUntil: "domcontentloaded" });
    await page.locator("#send").waitFor();
    const read = async () => ({
      assistant: await page.locator("#assistant").innerText(),
      users: await page.locator("#users").innerText(),
      branches: await page.locator("#branches .branched").count(),
      remounts: await page.locator("#remounts").innerText(),
    });
    const setHistory = (messages) => page.evaluate((m) => window.setHistory(m), messages);
    const waitFor = async (check, label) => {
      for (let n = 0; n < 100; n++) {
        if (check(await read())) return;
        await page.waitForTimeout(50);
      }
      assert.fail(`${label}: the page shows ${JSON.stringify(await read())}`);
    };
    const send = async () => {
      await page.locator("#send").click();
      await page.waitForFunction(() => document.getElementById("running")?.textContent === "running");
    };
    const answer = async (saved, reply, replyContent) => {
      await page.waitForFunction(() => window.transport !== undefined);
      const sent = await page.evaluate(() => window.transport.request.state?.messages ?? []);
      await page.evaluate((frame) => window.transport.push(frame), finalState(sent, saved, reply, replyContent));
    };
    const finish = async (saved) => {
      if (saved !== undefined) await answer(saved);
      await page.evaluate((frames) => { window.transport.push(frames); window.transport.close(); }, finishFrames);
      await page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
    };
    /** Every frame until `ms` passes must hold `check`. */
    const holds = async (check, label, ms = 600) => {
      for (let n = 0; n * 50 < ms; n++) {
        const shown = await read();
        if (!check(shown)) assert.fail(`${label}: the page shows ${JSON.stringify(shown)}`);
        await page.waitForTimeout(50);
      }
    };
    const emit = (event) => page.evaluate((e) => window.liveEvent(e), event);
    /** The id the page made for the send now streaming, and the rows its request sent. */
    const sending = async () => {
      await page.waitForFunction(() => window.transport !== undefined);
      return page.evaluate(() => ({
        id: window.transport.request.commands?.[0]?.message?.id,
        sent: window.transport.request.state?.messages ?? [],
      }));
    };
    const push = (frame) => page.evaluate((f) => window.transport.push(f), frame);
    const end = async () => {
      await page.evaluate((frames) => { window.transport.push(frames); window.transport.close(); }, finishFrames);
      await page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
    };
    return { page, read, setHistory, waitFor, holds, send, answer, finish, emit, sending, push, end };
  }

  const scenarios = {
    // The page's send waits behind another client's busy turn; a history sync
    // brings that turn's reply while the send is in flight. The run's final
    // state, built from what the request sent, does not carry it.
    async "keeps rows a mid-turn sync added when the run ends"() {
      const t = await open();
      await t.setHistory([row(0, "user", "busy")]);
      await t.waitFor((s) => s.users === "busy", "seeded");
      await t.send();
      await t.setHistory([row(0, "user", "busy"), row(1, "assistant", "Busy work")]);
      await t.waitFor((s) => s.assistant.includes("Busy work"), "mid-turn sync");
      await t.answer(2);
      await t.waitFor((s) => s.assistant.includes("Working"), "the final state");
      await t.holds((s) => s.assistant.includes("Busy work") && s.branches === 0,
        "the busy turn's reply while the engine refreshes history");
      await t.finish();
      await t.holds((s) => s.assistant.includes("Busy work") && s.assistant.includes("Working") &&
        s.users === "busy|hello" && s.branches === 0, "after the run ends");
      await t.page.close();
    },
    // The settle reload brings the saved copies of the rows the run drew.
    async "settles a plain turn in place, without reloading the chat"() {
      const t = await open();
      await t.send();
      await t.finish(0);
      await t.waitFor((s) => s.assistant === "Working" && s.users === "hello", "the reply");
      await t.setHistory([row(0, "user", "hello"), row(1, "assistant", "Working")]);
      await t.holds((s) => s.remounts === "0" && s.users === "hello" && s.assistant === "Working",
        "after the settle reload");
      await t.page.close();
    },
    // A run's stream ends early (an error) while the agent works on; the page
    // sends again, and the agent takes that into the same turn. Neither run's
    // rows are matched to saved history, so the page must not draw a send next
    // to its saved copy, and must take the saved turn once it is idle (#1359).
    async "shows each send once when a run ended before its turn did"() {
      const t = await open();
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const call = { type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" };
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [...first.sent,
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: [call], status: { type: "incomplete", reason: "error" } },
      ] }]));
      await t.end();
      await t.page.evaluate(() => { window.transport = undefined; });
      await t.page.locator("#send-what").click();
      const second = await t.sending();
      const saved = (index, content, requestId) => ({ id: `history-${index}`, role: "user",
        content: [{ type: "text", text: content }], metadata: { custom: { requestIds: [requestId] } } });
      await t.setHistory([saved(0, "hello", first.id), { id: "history-1", role: "assistant", content: [call] },
        saved(2, "what", second.id)]);
      await t.holds((s) => s.users === "hello|what", "while the second send runs", 800);
      await t.push(state([{ type: "set", path: ["messages"], value: [...second.sent,
        { id: `user-s1-${second.sent.length}`, role: "user", content: "what", metadata: { custom: { requestId: second.id } } },
        { id: `assistant-s1-${second.sent.length + 1}`, role: "assistant", content: text("done"), status: { type: "complete", reason: "stop" } },
      ] }, { type: "set", path: ["synced"], value: null }]));
      await t.end();
      await t.setHistory([saved(0, "hello", first.id), { id: "history-1", role: "assistant", content: [call] },
        saved(2, "what", second.id), row(3, "assistant", "done")]);
      await t.waitFor((s) => s.users === "hello|what" && s.assistant === "done", "the saved turn once idle");
      await t.holds((s) => s.users === "hello|what" && s.assistant === "done" && s.branches === 0, "after it settles");
      await t.page.close();
    },
    // A turn that ran a tool: the run drew its call inside the reply, and
    // history saves the call as a row of its own before the reply's words.
    async "settles a turn that ran a tool in place, with no second branch"() {
      const t = await open();
      await t.send();
      const call = { type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" };
      await t.answer(0, 2, [call, ...text("Working")]);
      await t.finish();
      await t.waitFor((s) => s.assistant.includes("Working"), "the reply");
      await t.setHistory([
        row(0, "user", "hello"),
        { id: "history-1", role: "assistant", content: [call] },
        row(2, "assistant", "Working"),
      ]);
      await t.holds((s) => s.remounts === "0" && s.branches === 0 && s.users === "hello" &&
        s.assistant.includes("Working"), "after the settle reload");
      await t.page.close();
    },
    // The page sends while watching another client's turn, whose live copy
    // sits in the state it sent. A message another client sends while the
    // page's turn runs goes above that turn, which stays last (#1364).
    async "keeps the page's turn last when the page sent while watching another turn"() {
      const t = await open();
      await t.emit({ type: "token", text: "Busy live", request_id: "r-busy" });
      await t.waitFor((s) => s.assistant.includes("Busy live"), "the watched turn");
      await t.send();
      // This page's send is saved at row 1 and another client's "ping" at 2.
      await t.setHistory([row(0, "user", "busy"), row(1, "user", "hello"), row(2, "user", "ping")]);
      await t.waitFor((s) => s.users.includes("ping"), "mid-turn sync");
      // The reply is saved after the ping, at row 3.
      await t.answer(1, 3);
      await t.finish();
      await t.waitFor((s) => s.assistant.includes("Working"), "the reply");
      await t.holds((s) => s.users === "busy|ping|hello", "the page's turn last after the run ends");
      await t.page.close();
    },
    async "settles a turn sent during a busy turn in place"() {
      const t = await open();
      await t.setHistory([row(0, "user", "busy")]);
      await t.waitFor((s) => s.users === "busy", "seeded");
      await t.send();
      await t.setHistory([row(0, "user", "busy"), row(1, "assistant", "Busy work")]);
      await t.waitFor((s) => s.assistant.includes("Busy work"), "mid-turn sync");
      await t.finish(2);
      await t.waitFor((s) => s.assistant.includes("Working"), "the reply");
      await t.setHistory([
        row(0, "user", "busy"), row(1, "assistant", "Busy work"),
        row(2, "user", "hello"), row(3, "assistant", "Working"),
      ]);
      await t.holds((s) => s.remounts === "0" && s.branches === 0 && s.users === "busy|hello" &&
        s.assistant.includes("Busy work") && s.assistant.includes("Working"), "after the settle reload");
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
