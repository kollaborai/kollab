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

  assert.deepEqual(pageErrors, [], `browser errors: ${pageErrors.join(" | ")}`);
  console.log("PASS: queue callbacks, pending guards, safe error, disabled states, idle state");
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
  await vite.close();
}
