// Reproduce two complete state events queued together while Send Now waits
// for the running request's first state update.
// Run with:
//   CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tests/acceptance/1377-handover.browser.mjs
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

try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => browserErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${address.port}/tests/acceptance/1367-handover.html`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByLabel("Message input").waitFor();
  await page.waitForFunction(() => window.sessionEventOpen && window.sessionEventTransport !== undefined);

  const frameFor = (requestIndex, assistantText) => page.evaluate(({ index, assistantText: text }) => {
    const transport = window.transports[index];
    const command = transport.request.commands.find((item) => item.type === "add-message");
    const requestId = command.message.id;
    const prior = transport.request.state.messages ?? [];
    const messages = [
      ...prior,
      { id: `user-${requestId}`, role: "user", content: command.message.parts[0].text,
        metadata: { custom: { requestId } } },
      { id: `assistant-${requestId}`, role: "assistant", content: [{ type: "text", text }],
        metadata: { custom: { requestId } } },
    ];
    return `data: ${JSON.stringify({ type: "update-state", operations: [
      { type: "set", path: ["messages"], value: messages },
    ] })}\n\n`;
  }, { index: requestIndex, assistantText });

  const box = page.getByLabel("Message input");
  await box.fill("request A");
  await box.press("Enter");
  await page.waitForFunction(() => window.transports.length === 1);
  await box.fill("request B");
  await box.press("Enter");
  await page.getByText("Queued", { exact: true }).waitFor();
  // Send Now is requested while A's command is still in transit. The first
  // state frame settles A; the second is already queued behind it in the body.
  // When A settles, the runtime schedules the handover in a microtask while
  // assistant-ui's decoder can already have read the next frame.
  await page.getByRole("button", { name: "Send Now" }).click();
  const first = await frameFor(0, "A before handover.");
  const late = await page.evaluate(() => {
    const transport = window.transports[0];
    const command = transport.request.commands.find((item) => item.type === "add-message");
    const requestId = command.message.id;
    const messages = [
      ...(transport.request.state.messages ?? []),
      { id: `user-${requestId}`, role: "user", content: command.message.parts[0].text,
        metadata: { custom: { requestId } } },
      { id: `assistant-${requestId}`, role: "assistant",
        content: [{ type: "text", text: "A before handover. LATE OLD-STREAM EVENT" }],
        metadata: { custom: { requestId } } },
    ];
    return `data: ${JSON.stringify({ type: "update-state", operations: [
      { type: "set", path: ["messages"], value: messages },
    ] })}\n\n`;
  });
  await page.evaluate(({ firstFrame, lateFrame }) => {
    window.transports[0].push(firstFrame);
    window.transports[0].push(lateFrame);
  }, { firstFrame: first, lateFrame: late });
  await page.waitForFunction(() => window.transports.length === 2);

  const afterHandover = await page.evaluate(() => {
    const state = window.transports[1].request.state;
    const bCommands = window.transports.flatMap((transport) => transport.request.commands ?? [])
      .filter((command) => command.type === "add-message" && command.message.parts
        .map((part) => part.text ?? "").join("") === "request B");
    const assistantText = document.getElementById("assistant")?.textContent ?? "";
    return {
      requestCount: window.transports.length,
      bCommandCount: bCommands.length,
      bCommandText: window.transports[1].request.commands
        .filter((command) => command.type === "add-message")
        .map((command) => command.message.parts.map((part) => part.text ?? "").join("")),
      savedFirstState: JSON.stringify(state).includes("A before handover."),
      lateInRequest: JSON.stringify(state).includes("LATE OLD-STREAM EVENT"),
      lateInUi: assistantText.includes("LATE OLD-STREAM EVENT"),
      firstStateOccurrences: assistantText.split("A before handover.").length - 1,
      oldTransportCancelled: window.transports[0].aborted,
    };
  });
  assert.deepEqual(afterHandover, {
    requestCount: 2,
    bCommandCount: 1,
    bCommandText: ["request B"],
    savedFirstState: true,
    lateInRequest: false,
    lateInUi: false,
    firstStateOccurrences: 1,
    oldTransportCancelled: true,
  },
  "handover keeps A's accepted state, sends B exactly once, and drops the next queued state");
  assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
  await page.close();
  console.log("PASS 1377 handover consumer boundary");
} finally {
  if (browser) await browser.close();
  await vite.close();
}
