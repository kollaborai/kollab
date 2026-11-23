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
  await page.goto(`http://127.0.0.1:${address.port}/tests/acceptance/web-queue-panel-mount.html`);
  await page.getByText("First queued message").waitFor();
  await page.getByText("Second queued message").waitFor();

  const sendNow = page.locator('[data-slot="web-queue-send-now"]');
  assert.equal(await sendNow.getAttribute("aria-label"), "Send 1 queued message now");
  await sendNow.click();
  await page.getByText("First queued message").waitFor();
  await page.getByText("Sending").waitFor();
  assert.equal(await sendNow.isDisabled(), true, "a dispatching head remained Send Now actionable");
  assert.equal(await page.getByText("Second queued message").count(), 1);

  await page.getByRole("button", { name: "Remove queued message 2" }).click();
  await page.getByText("Second queued message").waitFor({ state: "detached" });
  assert.equal(await page.locator('[data-slot="web-queue-item"]').count(), 1);

  const probe = await page.evaluate(() => ({
    sent: window.__queuePanelMountProbe?.sent(),
    removed: window.__queuePanelMountProbe?.removed(),
    requests: window.__queuePanelMountProbe?.requests() ?? [],
  }));
  assert.equal(probe.sent, true);
  assert.equal(probe.removed, true);
  const requests = probe.requests;
  assert.ok(requests.some(({ method, url }) => method === "POST" && url.includes("/batches/batch-first/send-now?conversation_uid=conversation-a")));
  assert.ok(requests.some(({ method, url }) => method === "DELETE" && url.includes("/items/request-second?conversation_uid=conversation-a&operation_id=remove-operation")));
  for (const request of requests) assert.equal(request.authorization, "Bearer queue-panel-test-token");
  for (const width of [390, 820]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    assert.equal(overflow, false, `mounted queue panel overflows horizontally at ${width}px`);
  }
  assert.deepEqual(pageErrors, [], `browser errors: ${pageErrors.join(" | ")}`);
  console.log("PASS: mounted queue controller sends FIFO batch, refreshes, removes one item, and keeps authenticated session scope");
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
  await vite.close();
}
