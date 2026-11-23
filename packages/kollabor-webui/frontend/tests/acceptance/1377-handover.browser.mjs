// Reproduce a transport event arriving in the Send Now click task, after the
// click handler schedules endRun but before its queued microtask runs.
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
  const first = await frameFor(0, "A before handover.");
  await page.evaluate((frame) => window.transports[0].push(frame), first);
  await page.getByText("A before handover.", { exact: true }).waitFor();

  await box.fill("request B");
  await box.press("Enter");
  await page.getByText("Queued", { exact: true }).waitFor();

  // This is a full second state event in the already pending old response.
  // The capture listener enqueues it before React handles the button; React's
  // handler then schedules endRun in a microtask during the same click task.
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
  await page.evaluate((frame) => {
    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target.closest("button") : null;
      if (target?.textContent?.trim() === "Send Now") window.transports[0].push(frame);
    }, { capture: true, once: true });
  }, late);
  await page.getByRole("button", { name: "Send Now" }).click();
  await page.waitForFunction(() => window.transports.length === 2);

  const leakedIntoNextRequest = await page.evaluate(() => JSON.stringify(window.transports[1].request.state)
    .includes("LATE OLD-STREAM EVENT"));
  assert.equal(leakedIntoNextRequest, false,
    "a response event queued at Send Now must not enter the old request state or B's request body");
  assert.equal(await page.getByText("A before handover. LATE OLD-STREAM EVENT", { exact: true }).count(), 0,
    "the old response's queued state never appears after handover");
  assert.deepEqual(browserErrors, [], "the browser reports no runtime errors");
  await page.close();
  console.log("PASS 1377 handover consumer boundary");
} finally {
  if (browser) await browser.close();
  await vite.close();
}
