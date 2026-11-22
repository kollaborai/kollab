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

  /**
   * Fresh page; `answer(saved)` streams its final state with "hello" saved at row `saved`, `finish()` ends it.
   * `open("?chat")` also draws the real chat, with its message box.
   */
  async function open(query = "") {
    const page = await browser.newPage();
    page.on("pageerror", (error) => browserErrors.push(error.message));
    await page.goto(`${origin}/tests/acceptance/turn-settle.html${query}`, { waitUntil: "domcontentloaded" });
    await page.locator("#send").waitFor();
    const read = async () => ({
      assistant: await page.locator("#assistant").innerText(),
      users: await page.locator("#users").innerText(),
      branches: await page.locator("#branches .branched").count(),
      remounts: await page.locator("#remounts").innerText(),
      errors: await page.locator("#errors").innerText(),
      tools: await page.locator("#tools").innerText(),
      ids: (await page.locator("#ids").innerText()).split("|").filter(Boolean),
    });
    const setHistory = (messages, shown) => page.evaluate(([m, w]) => window.setHistory(m, w ?? undefined), [messages, shown ?? null]);
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
    // rows are matched to saved history, so the page must not draw a send or
    // the reply next to its saved copy, and must take the saved turn once it
    // is idle, where it stands (#1359).
    async "shows each send once when a run ended before its turn did"() {
      const t = await open();
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const call = { type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" };
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [...first.sent,
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: [call] },
      ] }]));
      // No finish frame: the connection is gone, and the page says so.
      await t.page.evaluate(() => window.transport.close());
      await t.page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
      await t.waitFor((s) => s.errors === "1", "the dropped stream's error");
      await t.page.evaluate(() => { window.transport = undefined; });
      await t.page.locator("#send-what").click();
      const second = await t.sending();
      // That error was the last run's: it does not sit under the new message.
      await t.holds((s) => s.errors === "0", "once the next message is out", 300);
      const saved = (index, content, requestId) => ({ id: `history-${index}`, role: "user",
        content: [{ type: "text", text: content }], metadata: { custom: { requestIds: [requestId] } } });
      await t.setHistory([saved(0, "hello", first.id), { id: "history-1", role: "assistant", content: [call] },
        saved(2, "what", second.id)]);
      await t.holds((s) => s.users === "hello|what", "while the second send runs", 800);
      // The stream writes the reply; its row was saved a moment earlier.
      await t.push(state([{ type: "set", path: ["messages"], value: [...second.sent,
        { id: `user-s1-${second.sent.length}`, role: "user", content: "what", metadata: { custom: { requestId: second.id } } },
        { id: `assistant-s1-${second.sent.length + 1}`, role: "assistant", content: text("do") },
      ] }]));
      const history = [saved(0, "hello", first.id), { id: "history-1", role: "assistant", content: [call] },
        saved(2, "what", second.id), row(3, "assistant", "done")];
      await t.setHistory(history);
      await t.holds((s) => s.users === "hello|what" && !s.assistant.includes("done"), "the reply saved before the stream wrote it", 800);
      await t.push(state([{ type: "set", path: ["messages"], value: [...second.sent,
        { id: `user-s1-${second.sent.length}`, role: "user", content: "what", metadata: { custom: { requestId: second.id } } },
        { id: `assistant-s1-${second.sent.length + 1}`, role: "assistant", content: text("done"), status: { type: "complete", reason: "stop" } },
      ] }, { type: "set", path: ["synced"], value: null }]));
      await t.end();
      await t.setHistory([...history]);
      // The saved turn: the call's row, then the reply, each once, with no reload.
      const once = (s) => s.users === "hello|what" && s.assistant.split("done").length === 2 &&
        s.remounts === "0" && s.errors === "0";
      await t.waitFor(once, "the saved turn once idle");
      await t.holds(once, "after it settles");
      // The thread is on its sync point again: a later turn another client ran shows.
      await t.setHistory([...history, row(4, "user", "later"), row(5, "assistant", "Later answer")]);
      await t.waitFor((s) => s.users === "hello|what|later" && s.assistant.includes("Later answer") && s.remounts === "0",
        "a later sync");
      await t.page.close();
    },
    // The page loads only the newest saved rows. A turn with many tool calls
    // pushes its own send out of that window while it runs: the send keeps its
    // place, its saved rows do not land above it, and a later send's rows do
    // not move it to the end (#1359).
    async "keeps a long turn's first message in place after it left the loaded history"() {
      const t = await open();
      const done = { type: "complete", reason: "stop" };
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const call = (id) => ({ type: "tool-call", toolCallId: id, toolName: "terminal", args: {}, argsText: "{}", result: "ok" });
      const saved = (index, content, requestId) => ({ id: `history-${index}`, role: "user",
        content: [{ type: "text", text: content }], metadata: { custom: { requestIds: [requestId] } } });
      await t.send();
      const first = await t.sending();
      const drawn = (calls) => state([{ type: "set", path: ["messages"], value: [...first.sent,
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: calls.map(call) },
      ] }]);
      await t.push(drawn(["c1"]));
      // The send is saved at 0; its first call follows.
      await t.setHistory([saved(0, "hello", first.id), { id: "history-1", role: "assistant", content: [call("c1")], status: done }]);
      await t.waitFor((s) => s.tools === "c1" && s.users === "hello", "the first call");
      // The turn goes on, and the newest rows no longer reach back to the send.
      await t.push(drawn(["c1", "c2", "c3"]));
      await t.setHistory([{ id: "history-40", role: "assistant", content: [call("c2")], status: done },
        { id: "history-42", role: "assistant", content: [call("c3")], status: done }], { start: 40, total: 44 });
      await t.waitFor((s) => s.tools === "c1|c2|c3", "the run's calls");
      await t.holds((s) => s.tools === "c1|c2|c3" && s.users === "hello", "a window past the send, while the run streams");
      // The stream drops; the page sends again and the agent takes it into the turn.
      await t.page.evaluate(() => window.transport.close());
      await t.page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
      await t.page.evaluate(() => { window.transport = undefined; });
      await t.page.locator("#send-what").click();
      const second = await t.sending();
      const tail = [{ id: "history-42", role: "assistant", content: [call("c3")], status: done },
        saved(44, "what", second.id), { ...row(45, "assistant", "done"), status: done }];
      await t.push(state([{ type: "set", path: ["messages"], value: [...second.sent,
        { id: `user-s1-${second.sent.length}`, role: "user", content: "what", metadata: { custom: { requestId: second.id } } },
        { id: `assistant-s1-${second.sent.length + 1}`, role: "assistant", content: text("done"), status: done },
      ] }, { type: "set", path: ["synced"], value: null }]));
      await t.end();
      await t.setHistory(tail, { start: 42, total: 46 });
      const settled = (s) => s.users === "hello|what" && s.tools === "c1|c2|c3" &&
        s.assistant.split("done").length === 2 && s.remounts === "0";
      await t.waitFor(settled, "both messages in the order they were sent");
      await t.holds(settled, "after it settles");
      await t.page.close();
    },
    // The page's request dropped mid-turn and the page only watches from then
    // on: the saved turn takes the place of the rows its run left, in place.
    async "takes the saved turn in place after its stream dropped"() {
      const t = await open();
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const call = { type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}" };
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [...first.sent,
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: [call] },
      ] }]));
      // No finish frame: the connection is gone.
      await t.page.evaluate(() => window.transport.close());
      await t.page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
      await t.waitFor((s) => s.errors === "1", "the dropped stream's error");
      const saved = { id: "history-0", role: "user", content: [{ type: "text", text: "hello" }],
        metadata: { custom: { requestIds: [first.id] } } };
      // Rows as the page builds them from history: every reply has its status.
      const done = { type: "complete", reason: "stop" };
      // The call is saved, its result is not in yet.
      await t.setHistory([saved, { id: "history-1", role: "assistant", content: [call], status: done }]);
      await t.holds((s) => s.users === "hello" && s.remounts === "0", "while the turn goes on");
      await t.setHistory([saved, { id: "history-1", role: "assistant", content: [{ ...call, result: "ok" }], status: done },
        { ...row(2, "assistant", "Working"), status: done }]);
      const settled = (s) => s.users === "hello" && s.assistant.split("Working").length === 2 &&
        s.remounts === "0" && s.errors === "0";
      await t.waitFor(settled, "the saved turn");
      await t.holds(settled, "after it settles");
      await t.page.close();
    },
    // The page only watches a turn (another window's, or its own after its
    // stream dropped): it draws the turn from its events and from the history
    // that lands while it runs. Each tool call shows once, and the reply stays
    // on screen from its last word until its saved row takes its place (#1359).
    async "draws a watched turn's tool call once and keeps its reply through the end"() {
      const t = await open();
      const done = { type: "complete", reason: "stop" };
      const named = { request_ids: ["s1"] };
      await t.setHistory([row(0, "user", "go")]);
      await t.waitFor((s) => s.users === "go", "seeded");
      await t.emit({ type: "tool_start", tool_id: "c1", tool_name: "terminal: ls", input: {}, ...named });
      await t.waitFor((s) => s.tools === "c1", "the live tool call");
      await t.emit({ type: "tool_result", tool_id: "c1", output: "ok", ...named });
      // The round is saved: its row lands while the turn goes on.
      const call = { id: "history-1", role: "assistant", status: done,
        content: [{ type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" }] };
      await t.setHistory([row(0, "user", "go"), call]);
      await t.holds((s) => s.tools === "c1", "the saved call beside the live turn");
      await t.emit({ type: "token", text: "All done", ...named });
      await t.waitFor((s) => s.assistant.includes("All done"), "the live reply");
      await t.emit({ type: "turn_complete", ...named });
      await t.holds((s) => s.assistant.includes("All done"), "the reply after the turn ended");
      await t.setHistory([row(0, "user", "go"), call, { ...row(2, "assistant", "All done"), status: done }]);
      await t.holds((s) => s.assistant.split("All done").length === 2 && s.tools === "c1" && s.remounts === "0",
        "the saved reply in its place");
      await t.page.close();
    },
    // The page watches a turn that starts right after another one. The earlier
    // turn's reply reaches the page late, in a sync that lands after the new
    // reply's first words, and it contains them. The live reply keeps its
    // words: the saved row is the turn before it (#1359).
    async "keeps a watched reply's first words when the turn before it is saved late"() {
      const t = await open();
      const done = { type: "complete", reason: "stop" };
      await t.setHistory([row(0, "user", "one")]);
      await t.waitFor((s) => s.users === "one", "seeded");
      await t.emit({ type: "token", text: "The", request_ids: ["s2"] });
      await t.waitFor((s) => s.assistant === "The", "the live reply");
      await t.setHistory([
        row(0, "user", "one"), { ...row(1, "assistant", "The answer is 4"), status: done },
        { ...row(2, "user", "two"), metadata: { custom: { requestIds: ["s2"] } } },
      ]);
      await t.waitFor((s) => s.users === "one|two", "the late sync");
      await t.holds((s) => s.assistant === "The answer is 4|The", "the live reply after the sync", 300);
      await t.emit({ type: "token", text: " sky", request_ids: ["s2"] });
      await t.waitFor((s) => s.assistant === "The answer is 4|The sky", "the live reply going on");
      // Its own row, saved after its message, takes its place.
      await t.emit({ type: "turn_complete", request_ids: ["s2"] });
      await t.setHistory([
        row(0, "user", "one"), { ...row(1, "assistant", "The answer is 4"), status: done },
        { ...row(2, "user", "two"), metadata: { custom: { requestIds: ["s2"] } } },
        { ...row(3, "assistant", "The sky"), status: done },
      ]);
      await t.holds((s) => s.assistant === "The answer is 4|The sky" && s.remounts === "0", "the saved reply in its place");
      await t.page.close();
    },
    // The page watches a turn, then sends into it. The run's states carry the
    // rows its request sent, the watched turn's live row among them: the tool
    // call history saved meanwhile shows once, during the run and after it, and
    // no live row is left running when the turn ends (#1359, #1362).
    async "shows a watched turn's tool call once after the page sends into it"() {
      const t = await open();
      const done = { type: "complete", reason: "stop" };
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      await t.setHistory([row(0, "user", "go")]);
      await t.waitFor((s) => s.users === "go", "seeded");
      await t.emit({ type: "tool_start", tool_id: "c1", tool_name: "terminal: ls", input: {}, request_ids: ["s1"] });
      await t.waitFor((s) => s.tools === "c1", "the live tool call");
      await t.send();
      const sent = await t.sending();
      assert.ok(sent.sent.some((message) => String(message.id).startsWith("live-turn")), "the request sent the live row");
      const call = { id: "history-1", role: "assistant", status: done,
        content: [{ type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" }] };
      await t.setHistory([row(0, "user", "go"), call]);
      await t.holds((s) => s.tools === "c1", "the saved call during the run", 300);
      await t.push(state([{ type: "set", path: ["messages"], value: [...sent.sent,
        { id: `user-s1-${sent.sent.length}`, role: "user", content: "hello", metadata: { custom: { requestId: sent.id } } },
        { id: `assistant-s1-${sent.sent.length + 1}`, role: "assistant", content: text("Wor") },
      ] }]));
      await t.waitFor((s) => s.assistant.includes("Wor"), "the run's reply");
      await t.holds((s) => s.tools === "c1", "the run's next state");
      await t.emit({ type: "turn_complete", request_ids: ["s1", sent.id] });
      await t.finish(2);
      await t.setHistory([row(0, "user", "go"), call, row(2, "user", "hello"), { ...row(3, "assistant", "Working"), status: done }]);
      const settled = (s) => s.tools === "c1" && s.users === "go|hello" && s.assistant.split("Working").length === 2 &&
        s.remounts === "0";
      await t.waitFor(settled, "the turn once it ended");
      await t.holds(settled, "after it settles");
      await t.page.close();
    },
    // A shown row reads differently in the reloaded history: it changes where
    // it is, without the whole reload that blanked the chat (#1358).
    // A message sent while the page's turn runs goes to the agent at once: the
    // page lets its request go, without stopping the turn, and sends the
    // message in a request of its own. The agent takes it in at its next
    // step, and the reply goes on under the message (#1365).
    async "hands a message sent mid-turn to the agent at once"() {
      const t = await open();
      const done = { type: "complete", reason: "stop" };
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const said = (s) => s.assistant.split("|").filter(Boolean).join("|");
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: text("Wor") },
      ] }]));
      await t.waitFor((s) => s.assistant.includes("Wor"), "the run's reply");
      // Every list of rows the chat draws from here on, a frame-long one included.
      await t.page.evaluate(() => {
        const ids = document.getElementById("ids");
        window.drawn = [];
        new MutationObserver(() => window.drawn.push(ids.textContent))
          .observe(ids, { childList: true, characterData: true, subtree: true });
      });
      await t.page.locator("#send-what").click();
      await t.page.waitForFunction(() => window.transports.length === 2, null, { timeout: 3000 })
        .catch(() => assert.fail("the message waited behind the running turn"));
      const second = await t.page.evaluate(() => ({
        commands: window.transport.request.commands, sent: window.transport.request.state?.messages ?? [],
        let: window.transports[0].aborted, cancels: window.cancels,
        running: document.getElementById("running")?.textContent,
      }));
      assert.equal(second.commands.length, 1);
      assert.equal(second.commands[0].message.parts[0].text, "what");
      assert.equal(second.let, true, "the page let its first request go");
      assert.equal(second.cancels, 0, "the agent's turn was not stopped");
      assert.equal(second.running, "running");
      await t.holds((s) => s.errors === "0" && s.users === "hello|what" && said(s) === "Wor", "while the message is on its way", 200);
      // The message and the typing dots under it stay drawn through the
      // hand-over: a row taken down and drawn again blinks, and the chat
      // jumps for a frame.
      const drawn = (await t.page.evaluate(() => window.drawn)).map((ids) => ids.split("|").filter(Boolean));
      assert.equal(drawn.at(-1)?.length, 4, `the turn, the message and the dots under it: ${JSON.stringify(drawn)}`);
      drawn.forEach((rows, n) => assert.ok(n === 0 || drawn[n - 1].every((id) => rows.includes(id)),
        `a row was taken down during the hand-over: ${JSON.stringify(drawn)}`));
      // The engine admits it; the turn's reply goes on under it.
      const at = second.sent.length;
      await t.push(state([{ type: "set", path: ["messages"], value: [...second.sent,
        { id: `user-s1-${at}`, role: "user", content: "what", metadata: { custom: { requestId: second.commands[0].message.id } } },
        { id: `assistant-s1-${at + 1}`, role: "assistant", content: text("Sure") },
      ] }]));
      await t.waitFor((s) => s.users === "hello|what" && said(s) === "Wor|Sure", "the reply under the message");
      await t.end();
      await t.setHistory([
        { ...row(0, "user", "hello"), metadata: { custom: { requestIds: [first.id] } } },
        { ...row(1, "assistant", "Working"), status: done },
        { ...row(2, "user", "what"), metadata: { custom: { requestIds: [second.commands[0].message.id] } } },
        { ...row(3, "assistant", "Sure"), status: done },
      ]);
      await t.waitFor((s) => said(s) === "Working|Sure", "the saved turn");
      await t.holds((s) => s.users === "hello|what" && said(s) === "Working|Sure" && s.remounts === "0" &&
        s.branches === 0 && s.errors === "0", "after the settle reload");
      await t.page.close();
    },
    // Enter in the message box sends while the agent works, as the Send button
    // does: the message goes to the agent at once. Shift+Enter still starts a
    // new line, and Enter on an open slash menu still picks its row (#1365).
    async "sends on Enter while the agent works"() {
      const t = await open("?chat");
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: text("Wor") },
      ] }]));
      await t.waitFor((s) => s.assistant.includes("Wor"), "the run's reply");
      const box = t.page.getByLabel("Message input");
      const requests = () => t.page.evaluate(() => window.transports.length);
      await box.fill("one");
      await box.press("Shift+Enter");
      await box.pressSequentially("two");
      assert.equal(await box.inputValue(), "one\ntwo", "Shift+Enter starts a new line");
      assert.equal(await requests(), 1, "Shift+Enter sent nothing");
      await box.fill("");
      await box.pressSequentially("/con");
      await t.page.locator("[data-palette-list]").waitFor();
      await box.press("Enter");
      await t.page.locator("[data-palette-list]").waitFor({ state: "detached" });
      assert.match(await box.inputValue(), /^\/config/, "Enter picked the slash menu's row");
      assert.equal(await requests(), 1, "picking a slash menu row sent nothing");
      await box.fill("what");
      await box.press("Enter");
      await t.page.waitForFunction(() => window.transports.length === 2, null, { timeout: 3000 })
        .catch(() => assert.fail("Enter did not send the message"));
      const second = await t.page.evaluate(() => ({
        commands: window.transport.request.commands, cancels: window.cancels,
        running: document.getElementById("running")?.textContent,
      }));
      assert.equal(second.commands.length, 1);
      assert.equal(second.commands[0].message.parts[0].text, "what");
      assert.equal(second.cancels, 0, "the agent's turn was not stopped");
      assert.equal(second.running, "running");
      assert.equal(await box.inputValue(), "", "the message box is empty after the send");
      await t.waitFor((s) => s.users === "hello|what" && s.errors === "0", "the sent message");
      await t.page.close();
    },
    // Stop still stops the agent's turn, and a message waiting behind it is not sent.
    async "stops the agent's turn on Stop"() {
      const t = await open();
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: text("Wor") },
      ] }]));
      await t.waitFor((s) => s.assistant.includes("Wor"), "the run's reply");
      await t.page.locator("#stop").click();
      await t.page.waitForFunction(() => document.getElementById("running")?.textContent === "idle");
      const after = await t.page.evaluate(() => ({ cancels: window.cancels, requests: window.transports.length }));
      assert.deepEqual(after, { cancels: 1, requests: 1 });
      await t.page.close();
    },
    // A message sent before the engine has answered the page's last request
    // waits behind that request (see the scenario above for one sent after).
    // A history sync then adds other senders' messages to the thread: the
    // waiting message keeps its row. A row whose id changes is taken down and
    // built again, with the typing dots under it, and the chat scrolls to it
    // anew (#1359).
    async "keeps a waiting message's row while rows are added ahead of it"() {
      const t = await open();
      await t.send();
      await t.sending();
      await t.page.locator("#send-what").click();
      await t.waitFor((s) => s.users === "hello|what", "the waiting message");
      const waiting = (await t.read()).ids;
      assert.equal(waiting.length, 3, `the message on its way, the waiting one and the dots under it: ${waiting}`);
      const kept = (s) => waiting.every((id) => s.ids.includes(id));
      await t.setHistory([row(0, "user", "ping"), row(1, "user", "pong")]);
      await t.waitFor((s) => s.users.includes("pong"), "the other senders' messages");
      await t.holds((s) => kept(s) && s.users === "ping|pong|hello|what" &&
        s.remounts === "0", "the waiting message's row after the sync", 300);
      assert.equal(await t.page.evaluate(() => window.transports.length), 1, "the first request still streams");
      await t.page.close();
    },
    async "takes a changed row in place, without reloading the chat"() {
      const t = await open();
      await t.setHistory([row(0, "user", "go"), row(1, "assistant", "First words"), row(2, "user", "next")]);
      await t.waitFor((s) => s.assistant === "First words", "seeded");
      await t.setHistory([row(0, "user", "go"), row(1, "assistant", "Second words"), row(2, "user", "next")]);
      await t.waitFor((s) => s.assistant === "Second words", "the changed row");
      // A row history no longer has goes, and a new one shows.
      await t.setHistory([row(0, "user", "go"), row(1, "assistant", "Second words"), row(2, "assistant", "More")]);
      await t.waitFor((s) => s.users === "go" && s.assistant.includes("More"), "the dropped and the new row");
      await t.holds((s) => s.remounts === "0", "without a reload");
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
    // A reply that starts with a tool call stays where it is when its turn is
    // reloaded from history, as after a message sent mid-turn or a dropped
    // stream (the engine could not match the turn's rows). Its first saved
    // row is the call alone. A reply put under a new id is taken down and
    // built again: it fades in anew, the chat jumps, and the row it replaced
    // stays as a second branch (the "2 / 2" picker) (#1359).
    async "keeps a reply that starts with a tool call in place when its turn is reloaded"() {
      const t = await open();
      const state = (operations) => `data: ${JSON.stringify({ type: "update-state", operations })}\n\n`;
      const call = { type: "tool-call", toolCallId: "c1", toolName: "terminal", args: {}, argsText: "{}", result: "ok" };
      await t.send();
      const first = await t.sending();
      await t.push(state([{ type: "set", path: ["messages"], value: [
        { id: "user-s1-0", role: "user", content: "hello", metadata: { custom: { requestId: first.id } } },
        { id: "assistant-s1-1", role: "assistant", content: [call, ...text("Working")],
          status: { type: "complete", reason: "stop" } },
      ] }]));
      await t.waitFor((s) => s.assistant.includes("Working"), "the reply");
      await t.end();
      const drawn = (await t.read()).ids;
      assert.deepEqual(drawn, ["user-s1-0", "assistant-s1-1"]);
      // Another sender's message saved after the turn shows the reload is in.
      await t.setHistory([
        { ...row(0, "user", "hello"), metadata: { custom: { requestIds: [first.id] } } },
        { id: "history-1", role: "assistant", content: [call] },
        row(2, "assistant", "Working"),
        row(3, "user", "ping"),
      ]);
      await t.waitFor((s) => s.users === "hello|ping", "the reloaded history");
      await t.holds((s) => s.ids.join("|") === [...drawn, "history-3"].join("|") && s.tools === "c1" &&
        s.assistant === "Working" && s.remounts === "0" && s.branches === 0 && s.errors === "0", "after the reload", 400);
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
