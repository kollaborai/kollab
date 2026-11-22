// Issue #1367 failing-first repro. Run with:
//   CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tests/acceptance/1367-handover.browser.mjs target
//   CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tests/acceptance/1367-handover.browser.mjs queue-control
//
// The browser uses the real EngineRuntimeProvider, Thread composer, Send Now
// handover and EngineApi.streamEvents parser. A local fake engine provides two
// separate SSE channels. The session-event producer is simulated and mirrors
// /sessions/{id}/events fanout (unfiltered); assistant-transport filtering is
// not copied into JavaScript: this harness imports and calls the current
// Engine route's _foreign_output function from Python before deciding whether
// a shared daemon event reaches request B's POST stream. It does not start the
// FastAPI server, daemon, provider, or history store.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const browserErrors = [];
const browserConsole = [];
const scenario = process.argv[2] ?? "target";
assert.ok(["target", "queue-control", "permission-handover"].includes(scenario), `unknown scenario: ${scenario}`);

const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  clearScreen: false,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
let browser;

const stateFrame = (messages) => `data: ${JSON.stringify({ type: "update-state", operations: [
  { type: "set", path: ["messages"], value: messages },
] })}\n\n`;
const finishFrames = 'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n';

function engineForeignOutput(events, requestIds, tagged = true) {
  // Directly import the live predicate from this checkout. The engine's
  // assistant route uses it to exclude output belonging only to another send.
  const python = [
    "import json, sys",
    "from kollabor_engine.routes.messages import _foreign_output",
    "payload = json.load(sys.stdin)",
    "result = [_foreign_output(event, set(payload['request_ids']), payload['tagged']) for event in payload['events']]",
    "print(json.dumps(result))",
  ].join("\n");
  const pythonPath = [
    path.join(repoRoot, "packages/kollabor-engine/src"),
    path.join(repoRoot, "packages/kollabor-ai/src"),
    path.join(repoRoot, "packages/kollabor-config/src"),
    process.env.PYTHONPATH,
  ].filter(Boolean).join(path.delimiter);
  const result = spawnSync(process.env.PYTHON ?? "python", ["-c", python], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: pythonPath },
    input: JSON.stringify({ events, request_ids: requestIds, tagged }),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `could not run the engine's current output filter: ${result.stderr || result.error || "no output"}`);
  return JSON.parse(result.stdout.trim());
}

try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });

  async function open() {
    const page = await browser.newPage();
    page.on("pageerror", (error) => browserErrors.push(error.message));
    page.on("console", (message) => browserConsole.push(`${message.type()}: ${message.text()}`));
    await page.goto(`${origin}/tests/acceptance/1367-handover.html`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("Message input").waitFor({ timeout: 5000 });
    await page.waitForFunction(() => window.sessionEventOpen && window.sessionEventTransport !== undefined,
      null, { timeout: 5000 }).catch(async (error) => {
      const state = await page.evaluate(() => ({ requests: window.mockEngineRequests,
        open: window.sessionEventOpen, channels: Boolean(window.sessionEventTransport) }));
      assert.fail(`the event stream did not open: ${JSON.stringify(state)}; page errors=${JSON.stringify(browserErrors)}; console=${JSON.stringify(browserConsole)}; ${error}`);
    });
    const waitFor = async (predicate, label, timeoutMs = 4000) => {
      const stopAt = Date.now() + timeoutMs;
      while (Date.now() < stopAt) {
        const value = await page.evaluate(() => ({
          running: document.getElementById("running")?.textContent,
          assistant: document.getElementById("assistant")?.textContent,
          tools: document.getElementById("tools")?.textContent,
          ids: document.getElementById("ids")?.textContent,
          eventCount: window.sessionEventsDelivered.length,
          branches: document.querySelectorAll("#branches .branched").length,
        }));
        if (predicate(value)) return value;
        await page.waitForTimeout(50);
      }
      assert.fail(`${label}: ${JSON.stringify(await page.evaluate(() => ({
        running: document.getElementById("running")?.textContent,
        assistant: document.getElementById("assistant")?.textContent,
        tools: document.getElementById("tools")?.textContent,
        ids: document.getElementById("ids")?.textContent,
        eventCount: window.sessionEventsDelivered.length,
        branches: document.querySelectorAll("#branches .branched").length,
      })))}`);
    };
    const send = async (text) => {
      const box = page.getByLabel("Message input");
      await box.fill(text);
      await box.press("Enter");
    };
    const request = async (index) => page.evaluate((i) => {
      const value = window.transports[i];
      const command = value?.request.commands?.find((item) => item.type === "add-message");
      return {
        id: command?.message?.id,
        text: command?.message?.parts?.map((part) => part.text ?? "").join(""),
        sent: value?.request.state?.messages ?? [],
      };
    }, index);
    const pushResponse = async (index, assistantText) => {
      const current = await request(index);
      const at = current.sent.length;
      const messages = [
        ...current.sent,
        { id: `user-s1-${at}`, role: "user", content: current.text,
          metadata: { custom: { requestId: current.id } } },
        { id: `assistant-s1-${at + 1}`, role: "assistant", content: [{ type: "text", text: assistantText }],
          metadata: { custom: { requestId: current.id } } },
      ];
      await page.evaluate(({ i, frame }) => window.transports[i].push(frame), { i: index, frame: stateFrame(messages) });
    };
    const read = () => page.evaluate(() => ({
      running: document.getElementById("running")?.textContent,
      assistant: document.getElementById("assistant")?.textContent ?? "",
      tools: document.getElementById("tools")?.textContent ?? "",
      ids: (document.getElementById("ids")?.textContent ?? "").split("|").filter(Boolean),
      eventCount: window.sessionEventsDelivered.length,
      branches: document.querySelectorAll("#branches .branched").length,
      delivered: window.sessionEventsDelivered.map((event) => `${event.request_id}:${event.type}`),
      transports: window.transports.map((transport) => ({
        closed: transport.closed, aborted: transport.aborted, pushCount: transport.pushCount,
      })),
    }));
    return { page, waitFor, send, request, pushResponse, read };
  }

  if (scenario === "target") {
    const t = await open();
    await t.send("request A");
    await t.page.waitForFunction(() => window.transports.length === 1);
    const first = await t.request(0);
    assert.ok(first.id, "the local request A has a message/request id");
    assert.equal(first.text, "request A");
    await t.pushResponse(0, "A before handover.");
    await t.waitFor((s) => s.running === "running" && s.assistant.includes("A before handover."),
      "request A is visibly streaming");

    await t.send("request B");
    await t.page.getByText("Queued", { exact: true }).waitFor();
    await t.page.getByRole("button", { name: "Send Now" }).click();
    await t.page.waitForFunction(() => window.transports.length === 2);
    const second = await t.request(1);
    assert.ok(second.id && second.id !== first.id, "Send Now starts local request B with its own id");
    assert.equal(second.text, "request B");

    const lateEvents = [
      { type: "tool_start", request_id: first.id, tool_id: "late-a-tool", tool_name: "terminal", input: { cmd: "late" } },
      { type: "token", request_id: first.id, text: "A late session text." },
    ];
    const filteredFromB = engineForeignOutput(lateEvents, [second.id], true);
    assert.deepEqual(filteredFromB, [true, true],
      "the current engine route must filter both request A outputs from request B's assistant stream");
    const assistantBEvents = lateEvents.filter((_, index) => !filteredFromB[index]);
    assert.deepEqual(assistantBEvents, [],
      "no request A output should be routed to request B's assistant transport");

    // This is the session event producer, not the assistant POST response:
    // the current /events route fans these same events to its subscriber
    // without _foreign_output filtering. Only events admitted by the current
    // engine predicate would be routed to the assistant transport; this target
    // pair is rejected there and therefore goes only over the session channel.
    await t.page.evaluate(({ sessionEvents, assistantEvents }) => {
      for (const event of sessionEvents) window.sessionEventTransport.push(event);
      for (const event of assistantEvents) window.transport.push(JSON.stringify(event));
    }, { sessionEvents: lateEvents, assistantEvents: assistantBEvents });
    await t.waitFor((s) => s.eventCount === 2, "both old-request events crossed the session SSE channel");
    const targetPath = path.join(process.env.REPRO_ARTIFACT_DIR ?? "/tmp", "1367-target-before-completion.png");
    await t.page.screenshot({ path: targetPath });
    const shown = await t.read();
    const occurrences = (value, needle) => value.split(needle).length - 1;
    // Failing-first: both events must appear exactly once while B's assistant
    // POST remains open. On the current base, runtime.tsx drops them because
    // local A never established liveTurn.requestId on the session-event path.
    assert.deepEqual({
      running: shown.running,
      toolIds: shown.tools.split("|").filter(Boolean),
      lateTextOccurrences: occurrences(shown.assistant, "A late session text."),
      delivered: shown.delivered,
      bTransport: shown.transports[1],
      branches: shown.branches,
    }, {
      running: "running",
      toolIds: ["late-a-tool"],
      lateTextOccurrences: 1,
      delivered: [`${first.id}:tool_start`, `${first.id}:token`],
      bTransport: { closed: false, aborted: false, pushCount: 0 },
      branches: 0,
    }, "request A's filtered-from-B tool/text events render once from the session stream before B completes");

    // Once B itself is named, its assistant POST becomes the one output path.
    // The session event must not append a duplicate beside that POST snapshot.
    const bOwnedEvent = {
      type: "token", request_id: first.id, request_ids: [first.id, second.id], text: "B-owned text.",
    };
    assert.deepEqual(engineForeignOutput([bOwnedEvent], [second.id], true), [false],
      "the engine's current predicate allows a steered event through B's assistant stream");
    await t.page.evaluate((event) => window.sessionEventTransport.push(event), bOwnedEvent);
    await t.waitFor((s) => s.eventCount === 3, "the shared event stream reports B's accepted request id");
    const b = await t.request(1);
    await t.pushResponse(1, "B-owned text.");
    const afterBState = await t.read();
    assert.deepEqual({
      running: afterBState.running,
      lateToolOccurrences: afterBState.tools.split("|").filter((id) => id === "late-a-tool").length,
      lateTextOccurrences: occurrences(afterBState.assistant, "A late session text."),
      bTextOccurrences: occurrences(afterBState.assistant, "B-owned text."),
      liveBeforeB: afterBState.ids.indexOf(`live-turn:${first.id}`) < afterBState.ids.indexOf(`user-s1-${b.sent.length}`),
      branches: afterBState.branches,
    }, {
      running: "running",
      lateToolOccurrences: 1,
      lateTextOccurrences: 1,
      bTextOccurrences: 1,
      liveBeforeB: true,
      branches: 0,
    }, "the handover row stays in order while B's own stream takes over without a duplicate");
    assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
    await t.page.close();
    console.log(`PASS ${scenario}`);
  } else if (scenario === "queue-control") {
    const t = await open();
    await t.send("request A");
    await t.page.waitForFunction(() => window.transports.length === 1);
    const first = await t.request(0);
    assert.ok(first.id, "the local request A has a message/request id");
    await t.pushResponse(0, "A continues while B waits.");
    await t.waitFor((s) => s.running === "running" && s.assistant.includes("A continues while B waits."),
      "request A remains visibly active");

    await t.send("request B");
    await t.page.getByText("Queued", { exact: true }).waitFor();
    const controlEvent = { type: "token", request_id: first.id, text: "A allowed output." };
    assert.deepEqual(engineForeignOutput([controlEvent], [first.id], true), [false],
      "the current engine filter permits request A output on request A's assistant stream");
    await t.pushResponse(0, "A allowed output.");
    const whileQueued = await t.read();
    assert.equal(whileQueued.transports.length, 1, "normal queue does not start B before A ends");
    assert.equal(whileQueued.transports[0].aborted, false, "normal queue leaves A's assistant transport open");
    assert.equal(whileQueued.running, "running");
    assert.ok(whileQueued.assistant.includes("A allowed output."), "A's own assistant transport keeps rendering");

    await t.page.evaluate((frames) => {
      window.transports[0].push(frames);
      window.transports[0].close();
    }, finishFrames);
    await t.page.waitForFunction(() => window.transports.length === 2, null, { timeout: 4000 });
    const second = await t.request(1);
    assert.equal(second.text, "request B", "normal queue starts B after A's finish frame");
    assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
    await t.page.close();
    console.log(`PASS ${scenario}`);
  } else {
    const t = await open();
    await t.send("request A");
    await t.page.waitForFunction(() => window.transports.length === 1);
    const first = await t.request(0);
    assert.ok(first.id, "the local request A has a message/request id");
    await t.pushResponse(0, "A continues while B waits.");
    await t.waitFor((s) => s.running === "running" && s.assistant.includes("A continues while B waits."),
      "request A remains visibly active");

    await t.send("request B");
    await t.page.getByText("Queued", { exact: true }).waitFor();
    await t.page.getByRole("button", { name: "Send Now" }).click();
    await t.page.waitForFunction(() => window.transports.length === 2);
    const second = await t.request(1);
    assert.ok(second.id && second.id !== first.id, "Send Now starts local request B with its own id");

    const foreignEvent = {
      type: "permission_request", request_id: "foreign-request", tool_id: "foreign-tool-id",
      tool_name: "foreign-tool", input: { cmd: "echo foreign" },
    };
    const promptEvent = {
      type: "permission_request", request_id: first.id, tool_id: "permission-tool-a",
      tool_name: "terminal", tool_type: "command", input: { cmd: "echo needs approval" },
      risk_level: "medium", risk_reason: "This command needs approval.",
      details: { source: "acp", request_id: "permission-owner-a" },
    };
    assert.deepEqual(engineForeignOutput([promptEvent], [second.id], true), [true],
      "the engine filters request A's permission event from request B's assistant stream");
    await t.page.evaluate((events) => {
      for (const event of events) window.sessionEventTransport.push(event);
    }, [foreignEvent, promptEvent]);
    await t.waitFor((s) => s.eventCount === 2, "foreign and prior-request permission events cross the session stream");
    await t.page.getByText("Permission required: terminal", { exact: true }).waitFor({ state: "detached" });
    assert.equal(await t.page.getByText("Permission required: foreign-tool", { exact: true }).count(), 0,
      "a foreign permission event does not create a prompt");

    // App's permission refresh builds this synthetic requires-action row
    // without a history index; publishing it through the provider reproduces
    // the exact mid-turn snapshot that the running thread must retain.
    const pendingPermission = {
      tool_id: "permission-tool-a",
      tool_name: "terminal",
      tool_type: "command",
      input: { cmd: "echo needs approval" },
      risk_level: "medium",
      risk_reason: "This command needs approval.",
      details: { source: "acp", request_id: "permission-owner-a" },
    };
    await t.page.evaluate((prompt) => window.refreshPermissions([prompt]), pendingPermission);
    const prompt = t.page.getByText("Permission required: terminal", { exact: true });
    await prompt.waitFor();
    assert.equal(await prompt.count(), 1, "the pending prompt is rendered exactly once");
    for (let refresh = 0; refresh < 2; refresh += 1) {
      await t.page.evaluate((value) => window.refreshPermissions([value]), pendingPermission);
      await t.page.waitForTimeout(50);
      assert.equal(await prompt.count(), 1, "repeated authoritative refreshes do not duplicate the prompt");
    }
    assert.equal(await t.page.getByText("Permission required: foreign-tool", { exact: true }).count(), 0,
      "the refresh does not invent a prompt for a foreign event");
    for (const label of ["Allow once", "Allow session", "Deny"]) {
      const button = t.page.getByRole("button", { name: label, exact: true });
      assert.equal(await button.count(), 1, `${label} appears once on the prompt`);
      assert.equal(await button.isEnabled(), true, `${label} requires an explicit user choice`);
    }
    const beforeAnswer = await t.read();
    assert.equal(beforeAnswer.running, "running", "request B remains active while the permission prompt is shown");
    assert.equal(await t.page.getByText("Allowed (once)", { exact: true }).count(), 0,
      "the permission remains unanswered until the user chooses");
    assert.deepEqual(beforeAnswer.transports[1], { closed: false, aborted: false, pushCount: 0 },
      "B's assistant transport remains open and does not receive A's filtered event");

    const allowOnce = t.page.getByRole("button", { name: "Allow once", exact: true });
    await allowOnce.click();
    await t.page.getByText("Allowed (once)", { exact: true }).waitFor();
    assert.equal(await prompt.count(), 1, "answering does not duplicate the permission row");
    assert.equal(await allowOnce.isDisabled(), true, "the same prompt cannot be answered twice");
    const afterAnswer = await t.read();
    assert.equal(await t.page.getByText("Allowed (once)", { exact: true }).count(), 1,
      "the explicit choice is shown on the permission prompt");
    assert.equal(afterAnswer.running, "running", "B remains active after the explicit answer");
    assert.equal(afterAnswer.transports.length, 2, "answering does not silently start another request");

    // The next authoritative permissions refresh says this prompt is resolved.
    // Its transient row must be removed from both the thread and the replay
    // rows that every later B snapshot merges back in.
    await t.page.evaluate(() => window.refreshPermissions([]));
    await prompt.waitFor({ state: "detached" });
    assert.equal(await t.page.getByRole("button", { name: "Allow once", exact: true }).count(), 0,
      "a resolved synthetic prompt is no longer answerable");
    assert.ok(!(await t.read()).tools.includes("permission_permission-tool-a"),
      "a resolved prompt is removed from the thread state");

    const stalePermission = {
      ...pendingPermission,
      tool_id: "permission-tool-stale",
      tool_name: "file_write",
      details: { source: "acp", request_id: "permission-owner-stale" },
    };
    await t.page.evaluate((value) => window.refreshPermissions([value]), stalePermission);
    const stalePrompt = t.page.getByText("Permission required: file_write", { exact: true });
    await stalePrompt.waitFor();
    assert.equal(await t.page.getByRole("button", { name: "Allow once", exact: true }).isEnabled(), true,
      "a later pending prompt is answerable before switching sessions");

    // App keys the runtime by session. A later session must not inherit this
    // permission card or its answer control from the prior run.
    await t.page.evaluate(() => window.switchSession("s2"));
    await t.page.waitForFunction(() => window.sessionEventOpen && window.sessionEventTransport !== undefined);
    await prompt.waitFor({ state: "detached" });
    await stalePrompt.waitFor({ state: "detached" });
    assert.equal(await t.page.getByRole("button", { name: "Allow once", exact: true }).count(), 0,
      "the next session has no stale answer control");
    const afterSwitch = await t.read();
    assert.equal(afterSwitch.running, "idle", "the next session starts idle");
    assert.ok(!afterSwitch.ids.some((id) => id.startsWith("permission-")),
      "the next session starts without the prior synthetic prompt row");
    assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
    await t.page.close();
    console.log(`PASS ${scenario}`);
  }
} finally {
  await browser?.close();
  await vite.close();
}
