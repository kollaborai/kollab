import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const screenshotDir = path.resolve(
  process.env.LLM_PROVIDER_REMOVE_SCREENSHOT_DIR ?? path.join("/tmp", "kollab-llm-provider-removal-proof"),
);
const executablePath = process.env.CHROME_PATH ?? chromium.executablePath();
const widths = [390, 820, 1280, 1440];
const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  clearScreen: false,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
let browser;
const failures = [];

try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  browser = await chromium.launch({ headless: true, executablePath });
  await mkdir(screenshotDir, { recursive: true });
  const page = await browser.newPage({ viewport: { width: widths[0], height: 844 } });
  const origin = `http://127.0.0.1:${address.port}`;
  await page.route("**/*", async (route) => {
    if (new URL(route.request().url()).origin !== origin) {
      await route.abort();
      return;
    }
    await route.continue();
  });

  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/tests/acceptance/llm-provider-removal.html`, {
      waitUntil: "domcontentloaded",
    });
    const removeEmpty = page.getByRole("button", {
      name: "Remove Gemini provider Models — Gemini",
    });
    await removeEmpty.waitFor();
    await removeEmpty.click();
    await page.getByRole("status").getByText(/Press Remove Gemini provider again/).waitFor();
    await page.getByRole("button", { name: "Use gpt-model" }).click();
    assert.equal(await page.evaluate(() => window.llmProviderRemovalCalls), 0);
    await page.getByRole("button", { name: "Remove Gemini provider Models — Gemini" }).click();
    await page.getByRole("status").getByText(/Press Remove Gemini provider again/).waitFor();
    await page.getByRole("button", { name: "Confirm Models — Gemini" }).click();
    await page.getByRole("heading", { name: "Models — Gemini" }).waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => window.llmProviderRemovalCalls), 1);

    const remove = page.getByRole("button", { name: "Remove Anthropic provider claude-model" });
    await remove.waitFor();
    await remove.click();
    const confirmation = page.getByRole("status");
    await confirmation.waitFor();
    await page.getByRole("button", { name: "Use gpt-model" }).click();
    assert.equal(await page.evaluate(() => window.llmProviderRemovalCalls), 1);
    await page.getByRole("button", { name: "Remove Anthropic provider claude-model" }).click();
    await confirmation.waitFor();
    const confirmationText = await confirmation.innerText();
    if (!confirmationText.includes("Press Confirm again")) failures.push("confirmation copy must match the Confirm button");
    if (confirmationText.includes("Press Remove Anthropic provider again")) failures.push("confirmation copy still names the wrong action");
    if (width >= 820) {
      const row = page.getByRole("button", { name: "Confirm claude-model" }).locator("xpath=ancestor::li[1]");
      const details = await row.getByText("anthropic-cloud · claude-model").boundingBox();
      const confirmationBox = await confirmation.boundingBox();
      if (!(details && confirmationBox && confirmationBox.y >= details.y + details.height)) {
        failures.push(`confirmation should sit below provider names at ${width}px`);
      }
    }
    const confirmShot = `llm-provider-remove-${width}-confirm.png`;
    await page.screenshot({ path: path.join(screenshotDir, confirmShot), fullPage: true });
    await page.getByRole("button", { name: "Confirm claude-model" }).click();
    await page.getByRole("heading", { name: "Models — OpenAI" }).waitFor();
    assert.equal(await page.getByText("claude-model").count(), 0);
    assert.equal(await page.evaluate(() => window.llmProviderRemovalCalls), 2);
    const bounds = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    assert.ok(bounds.document <= width, `horizontal overflow at ${width}px: ${JSON.stringify(bounds)}`);
    const removedShot = `llm-provider-remove-${width}-removed.png`;
    await page.screenshot({ path: path.join(screenshotDir, removedShot), fullPage: true });
    console.log(`${width}px: saved ${confirmShot}, ${removedShot}; provider removed`);
  }
  assert.deepEqual(failures, [], "provider removal confirmation must match the control and preserve provider names");
} finally {
  await browser?.close();
  await vite.close();
}
