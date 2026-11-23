import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  clearScreen: false,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});

let browser;
let page;
try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });
  page = await browser.newPage({ viewport: { width: 820, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${address.port}/tests/acceptance/web-queue-panel.html`);
  await page.waitForFunction(() => window.__queuePanelTest !== undefined);

  const remove = page.getByRole("button", { name: "Remove queued message 1" });
  await remove.click();
  await page.waitForFunction(() => window.__queuePanelTest?.calls().length === 1);
  assert.deepEqual(await page.evaluate(() => window.__queuePanelTest?.calls()), [
    { kind: "remove", id: "fixture-request" },
  ]);
  assert.equal(await remove.isDisabled(), true, "pending removal can be duplicated");
  await page.evaluate(() => window.__queuePanelTest?.settleAction());
  await page.waitForFunction(() => !document.querySelector('[aria-label="Remove queued message 1"]')?.hasAttribute("disabled"));

  const sendNow = page.getByRole("button", { name: "Send 1 queued message now" });
  await sendNow.click();
  await page.waitForFunction(() => window.__queuePanelTest?.calls().length === 2);
  assert.deepEqual(await page.evaluate(() => window.__queuePanelTest?.calls()[1]), {
    kind: "send",
    id: "fixture-batch",
  });
  assert.equal(await sendNow.isDisabled(), true, "pending Send Now can be duplicated");
  await page.evaluate(() => window.__queuePanelTest?.settleAction());
  await page.waitForFunction(() => !document.querySelector('[aria-label="Send 1 queued message now"]')?.hasAttribute("disabled"));

  await page.evaluate(() => window.__queuePanelTest?.failNext());
  await sendNow.click();
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByRole("alert").innerText(), "Could not update the queue. Try again.");
  assert.equal((await page.locator("body").innerText()).includes("SECRET_ATTACHMENT_REF"), false);

  await page.evaluate(() => window.__queuePanelTest?.setDispatchAvailable(false));
  await page.waitForFunction(() => document.querySelector('[aria-label="Send 1 queued message now"]')?.hasAttribute("disabled"));
  assert.equal(await page.getByRole("button", { name: "Remove queued message 1" }).isDisabled(), false,
    "dispatch unavailability should not prevent explicit queue removal");

  await page.evaluate(() => window.__queuePanelTest?.setBusy(false));
  await page.waitForFunction(() => document.querySelector('[aria-label="Send 1 queued message now"]') === null);

  await page.evaluate(() => {
    window.__queuePanelTest?.setDispatchAvailable(true);
    window.__queuePanelTest?.setBusy(true);
    window.__queuePanelTest?.setSnapshot({
      scope_id: "fixture-scope",
      incarnation: "fixture-incarnation",
      revision: 2,
      batches: [1, 2].map((n) => ({
        batch_id: `fixture-batch-${n}`,
        sequence: n,
        status: "queued",
        request_ids: [`fixture-request-${n}`],
        claim_id: null,
        items: [{
          request_id: `fixture-request-${n}`,
          status: "queued",
          attempt_id: null,
          text_preview: `Queued message ${n}`,
          attachment_labels: [],
        }],
      })),
    });
  });
  const batchSendNow = page.getByRole("button", { name: "Send 1 queued message now" });
  await page.waitForFunction(() => document.querySelectorAll('[data-slot="web-queue-item"]').length === 2);
  assert.equal(await page.locator('[data-slot="web-queue-batch"]').count(), 0,
    "message rows should not be wrapped in repeated batch cards");
  assert.equal(await page.locator('[data-slot="web-queue-item"]').count(), 2);
  assert.equal(await batchSendNow.count(), 1, "multiple batches must expose one FIFO Send Now control");
  await batchSendNow.click();
  await page.waitForFunction(() => window.__queuePanelTest?.calls().length === 4);
  assert.deepEqual(await page.evaluate(() => window.__queuePanelTest?.calls()[3]), {
    kind: "send",
    id: "fixture-batch-1",
  }, "Send Now must target the first queued batch");
  assert.equal(await batchSendNow.isDisabled(), true, "a pending batch action must be globally serialized");
  assert.equal(await page.getByRole("button", { name: "Remove queued message 1" }).isDisabled(), true,
    "removal must not race an in-flight batch action");
  await page.evaluate(() => window.__queuePanelTest?.settleAction());
  await page.waitForFunction(() => !document.querySelector('[aria-label="Send 1 queued message now"]')?.hasAttribute("disabled"));

  await page.evaluate(() => window.__queuePanelTest?.setSnapshot({
    scope_id: "fixture-scope",
    incarnation: "fixture-incarnation",
    revision: 3,
    batches: [
      {
        batch_id: "fixture-ambiguous-first",
        sequence: 1,
        status: "in_doubt",
        request_ids: ["fixture-request-ambiguous"],
        claim_id: "fixture-claim-ambiguous",
        items: [{
          request_id: "fixture-request-ambiguous",
          status: "in_doubt",
          attempt_id: "fixture-attempt-ambiguous",
          text_preview: "Ambiguous earlier message",
          attachment_labels: [],
        }],
      },
      {
        batch_id: "fixture-later-batch",
        sequence: 2,
        status: "queued",
        request_ids: ["fixture-request-later"],
        claim_id: null,
        items: [{
          request_id: "fixture-request-later",
          status: "queued",
          attempt_id: null,
          text_preview: "Later queued message",
          attachment_labels: [],
        }],
      },
    ],
  }));
  await page.waitForFunction(() => document.querySelector('[data-slot="web-queue-status-dot"][data-status="in_doubt"]') !== null);
  const heldSendNow = page.locator('[data-slot="web-queue-send-now"]');
  assert.equal(await heldSendNow.count(), 1, "ambiguous head must block all later batch Send Now actions");
  assert.equal(await heldSendNow.isDisabled(), true);
  assert.equal(await heldSendNow.getAttribute("aria-label"),
    "Send Now unavailable: earlier message delivery status is unknown",
    "disabled control name must state why Send Now is blocked");
  assert.equal(await page.locator('[data-slot="web-queue-status-dot"][data-status="queued"]').count(), 1);
  assert.equal((await page.evaluate(() => window.__queuePanelTest?.calls())).length, 4,
    "an ambiguous head must not submit a later batch");

  assert.deepEqual(pageErrors, [], `browser errors: ${pageErrors.join(" | ")}`);
  console.log("PASS: queue callbacks, FIFO send gate, global pending guard, ambiguity hold, status dots");
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
  await vite.close();
}
