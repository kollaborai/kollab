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
assert.ok(["target", "queue-control", "permission-handover", "permission-stop-owner", "permission-outcome"].includes(scenario), `unknown scenario: ${scenario}`);

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

  async function open(query = "") {
    const page = await browser.newPage();
    page.on("pageerror", (error) => browserErrors.push(error.message));
    page.on("console", (message) => browserConsole.push(`${message.type()}: ${message.text()}`));
    await page.goto(`${origin}/tests/acceptance/1367-handover.html${query}`, { waitUntil: "domcontentloaded" });
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

  if (scenario === "permission-outcome") {
    const outcome = await open("?permissions=outcome");
    const prompt = outcome.page.getByText("Permission required: terminal", { exact: true });
    await prompt.waitFor();
    await outcome.page.evaluate(() => window.setPermissionSnapshot("s1", []));
    await outcome.page.evaluate((event) => window.sessionEventTransport.push(event), {
      type: "permission_denied",
      tool_id: "shared-tool-id",
      permission_id: "owner-outcome",
      request_id: "parent-outcome",
    });
    await prompt.waitFor({ state: "detached", timeout: 3000 });
    assert.equal(await outcome.page.getByRole("button", { name: "Allow once", exact: true }).count(), 0,
      "an exact foreign permission outcome removes the resolved answer card");
    assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
    await outcome.page.close();
    console.log(`PASS ${scenario}`);
  } else if (scenario === "permission-stop-owner") {
    const t = await open("?permissions=live");
    await t.page.getByText("Permission required: terminal", { exact: true }).waitFor();

    const stop = t.page.getByRole("button", { name: "Stop generating", exact: true });
    await stop.waitFor({ timeout: 3000 });
    await stop.click();
    await t.page.waitForFunction(() => window.permissionCancellations.length === 1);
    assert.deepEqual(await t.page.evaluate(() => window.permissionCancellations), [{
      sessionId: "s1",
      authorization: "Bearer fixture-token",
      body: { request_id: "parent-live" },
    }], "the permission Stop uses the top-level live/restored parent owner");

    const ambiguous = await open("?permissions=ambiguous");
    await ambiguous.page.getByText("Permission required: terminal", { exact: true }).waitFor();
    assert.equal(await ambiguous.page.getByRole("button", { name: "Stop generating", exact: true }).count(), 0,
      "the unqualified Stop control is unavailable when distinct request owners coexist");

    const parked = await open("?permissions=parked");
    await parked.page.getByText("Permission required: terminal", { exact: true }).waitFor();
    assert.equal(await parked.page.getByRole("button", { name: "Stop generating", exact: true }).count(), 0,
      "a parked prompt does not show a Stop control that cannot cancel its completed turn");
    assert.equal(await parked.page.getByRole("button", { name: "Allow once", exact: true }).count(), 1,
      "the parked permission card remains explicitly answerable");

    assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
    await t.page.close();
    await ambiguous.page.close();
    await parked.page.close();
    console.log(`PASS ${scenario}`);
  } else if (scenario === "target") {
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
      permission_id: "permission-owner-token-a",
      tool_name: "terminal", tool_type: "command", input: { cmd: "echo needs approval" },
      risk_level: "medium", risk_reason: "This command needs approval.",
      details: {
        source: "acp", request_id: "permission-owner-a", permission_id: "permission-owner-token-a",
      },
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
      permission_id: "permission-owner-token-a",
      request_id: first.id,
      tool_name: "terminal",
      tool_type: "command",
      input: { cmd: "echo needs approval" },
      risk_level: "medium",
      risk_reason: "This command needs approval.",
      details: {
        source: "acp",
        request_id: "permission-owner-a",
        permission_id: "permission-owner-token-a",
        acp_options: [{ optionId: "allow-once-a", name: "Allow once", kind: "allow_once" }],
      },
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
    for (const label of ["Allow once"]) {
      const button = t.page.getByRole("button", { name: label, exact: true });
      assert.equal(await button.count(), 1, `${label} appears once on the prompt`);
      assert.equal(await button.isEnabled(), true, `${label} requires an explicit user choice`);
    }
    const beforeAnswer = await t.read();
    assert.equal(beforeAnswer.running, "running", "request B remains active while the permission prompt is shown");
    assert.equal(await t.page.getByText("Allowed (once)", { exact: true }).count(), 0,
      "the permission remains unanswered until the user chooses");
    assert.equal((await t.page.evaluate(() => window.permissionResponses)).length, 0,
      "the engine has not received an answer before the user chooses");
    assert.deepEqual(beforeAnswer.transports[1], { closed: false, aborted: false, pushCount: 0 },
      "B's assistant transport remains open and does not receive A's filtered event");

    const allowOnce = t.page.getByRole("button", { name: "Allow once", exact: true });
    await allowOnce.evaluate((button) => { button.click(); button.click(); });
    await t.page.getByText("Allowed (once)", { exact: true }).waitFor();
    assert.equal(await prompt.count(), 1, "answering does not duplicate the permission row");
    assert.equal(await allowOnce.isDisabled(), true, "the same prompt cannot be answered twice");
    const allowedResponses = await t.page.evaluate(() => window.permissionResponses);
    assert.equal(allowedResponses.length, 1,
      `the permission decision reaches the engine API exactly once despite a repeated click; ` +
        JSON.stringify({ ids: (await t.read()).ids, reads: await t.page.evaluate(() => window.permissionReads) }));
    assert.deepEqual(allowedResponses[0], {
      sessionId: "s1",
      method: "POST",
      authorization: "Bearer fixture-token",
      body: {
        tool_id: "permission-tool-a", permission_id: "permission-owner-token-a",
        decision: "approve", scope: "once", option_id: "allow-once-a",
      },
      handoverTransportOpen: true,
    }, "the authenticated response resolves A's permission while B's POST remains open");
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
    assert.ok(!(await t.read()).tools.includes("permission_permission-tool-a_permission-owner-token-a"),
      "a resolved prompt is removed from the thread state");

    const stalePermission = {
      ...pendingPermission,
      // Reuse A's source tool ID with a fresh owner token. A client that keys
      // only by tool_id must not send this denial/approval to the new owner.
      tool_id: "permission-tool-a",
      permission_id: "permission-owner-token-stale",
      tool_name: "file_write",
      request_id: `${first.id}-stale`,
      details: {
        source: "acp", request_id: "permission-owner-stale", permission_id: "permission-owner-token-stale",
      },
    };
    await t.page.evaluate((value) => window.refreshPermissions([value]), stalePermission);
    const stalePrompt = t.page.getByText("Permission required: file_write", { exact: true });
    await stalePrompt.waitFor();
    const staleAllow = t.page.getByRole("button", { name: "Allow once", exact: true });
    assert.equal(await staleAllow.isEnabled(), true, "a pending prompt is answerable before its server snapshot changes");
    await t.page.evaluate(({ sessionId, prompt }) => window.setPermissionSnapshot(sessionId, [prompt]), {
      sessionId: "s1",
      prompt: {
        ...stalePermission,
        request_id: `${first.id}-different-owner`,
        permission_id: "permission-owner-token-different",
      },
    });
    await staleAllow.click();
    await t.page.getByText("This permission request is no longer pending.", { exact: true }).waitFor();
    assert.equal(await staleAllow.isDisabled(), true, "a server-side owner mismatch disables the stale decision");
    assert.equal((await t.page.evaluate(() => window.permissionResponses)).length, 1,
      "the stale prompt sends no permission response");
    assert.equal((await t.page.evaluate(() => window.permissionReads)).at(-1)?.authorization, "Bearer fixture-token",
      "stale ownership was checked through the authenticated API");
    await t.page.evaluate(() => window.refreshPermissions([]));
    await stalePrompt.waitFor({ state: "detached" });

    const denyPermission = {
      ...pendingPermission,
      tool_id: "permission-tool-deny",
      permission_id: "permission-owner-token-deny",
      request_id: `${first.id}-deny`,
      tool_name: "shell",
      details: {
        source: "acp", request_id: "permission-owner-deny", permission_id: "permission-owner-token-deny",
      },
    };
    await t.page.evaluate((value) => window.refreshPermissions([value]), denyPermission);
    const denyPrompt = t.page.getByText("Permission required: shell", { exact: true });
    await denyPrompt.waitFor();
    const deny = t.page.getByRole("button", { name: "Deny", exact: true });
    await deny.evaluate((button) => { button.click(); button.click(); });
    await t.page.getByText("Denied (once)", { exact: true }).waitFor();
    const decisions = await t.page.evaluate(() => window.permissionResponses);
    assert.equal(decisions.length, 2, "deny reaches the API once despite a repeated click");
    assert.deepEqual(decisions[1], {
      sessionId: "s1",
      method: "POST",
      authorization: "Bearer fixture-token",
      body: {
        tool_id: "permission-tool-deny", permission_id: "permission-owner-token-deny",
        decision: "deny", scope: "once",
      },
      handoverTransportOpen: true,
    }, "denial is sent to the matching session while request B remains open");

    await t.page.evaluate((value) => window.refreshPermissions([value]), stalePermission);
    const switchingPrompt = t.page.getByText("Permission required: file_write", { exact: true });
    await switchingPrompt.waitFor();
    const responsesBeforeSwitch = (await t.page.evaluate(() => window.permissionResponses)).length;
    const readsBeforeSwitch = (await t.page.evaluate(() => window.permissionReads)).length;
    await t.page.evaluate(() => window.pausePermissionRead("s1"));
    await t.page.getByRole("button", { name: "Allow once", exact: true }).click();
    await t.page.waitForFunction((before) => window.permissionReads.length > before, readsBeforeSwitch);

    // App keys the runtime by session. A later session must not inherit this
    // permission card or its answer control from the prior run.
    await t.page.evaluate(() => window.switchSession("s2"));
    await t.page.waitForFunction(() => window.sessionEventOpen && window.sessionEventTransport !== undefined);
    await prompt.waitFor({ state: "detached" });
    await switchingPrompt.waitFor({ state: "detached" });
    await t.page.evaluate(() => window.releasePermissionRead());
    await t.page.waitForTimeout(50);
    assert.equal((await t.page.evaluate(() => window.permissionResponses)).length, responsesBeforeSwitch,
      "a session switch during the ownership check cannot send a decision");
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
