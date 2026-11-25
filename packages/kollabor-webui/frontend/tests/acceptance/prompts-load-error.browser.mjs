import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "../..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = path.resolve(
  process.env.PROMPTS_LOAD_ERROR_SCREENSHOT_DIR ??
    path.join(os.tmpdir(), "kollab-prompts-load-error"),
);
const widths = [390, 820, 1280];
const browserErrors = [];
const codeHead = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: repoRoot,
  encoding: "utf8",
}).trim();
const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  clearScreen: false,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
let browser;

try {
  await mkdir(screenshotDir, { recursive: true });
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });
  const page = await browser.newPage({ viewport: { width: widths[0], height: 844 } });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const requested = new URL(route.request().url());
    if (requested.origin !== origin) {
      await route.abort();
      return;
    }
    await route.continue();
  });

  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/tests/acceptance/prompts-load-error.html`, {
      waitUntil: "domcontentloaded",
    });
    const alert = page.getByRole("alert");
    await alert.waitFor({ state: "visible" });
    assert.match(await alert.textContent(), /Prompt library could not be loaded/);
    for (const id of ["prompts-sidebar", "prompts-sections", "prompts-target-select", "target-title"]) {
      assert.equal(await page.getByTestId(id).count(), 0, `${id} appeared during the load error`);
    }
    assert.equal(await page.getByPlaceholder("Filter Sections").count(), 0);
    assert.equal(await page.getByRole("tablist", { name: "Model variant" }).count(), 0);
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    assert.equal(overflow, false, `load error overflows at ${width}px`);
    const errorScreenshot = path.join(screenshotDir, `prompts-load-error-${width}.png`);
    await page.screenshot({ path: errorScreenshot, fullPage: true });

    await page.getByTestId("prompts-retry").click();
    await page.getByRole("button", { name: "Retrying…", exact: true }).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("alert").count(), 1, "error disappeared before retry succeeded");
    await page.getByTestId("prompts-sidebar").waitFor({ state: "attached" });
    assert.equal(await page.getByRole("alert").count(), 0);
    assert.equal(await page.getByTestId("target-title").textContent(), "Default");
    await page.getByRole("tablist", { name: "Model variant" }).waitFor({ state: "visible" });
    assert.equal(await page.evaluate(() => window.promptsLoadLibrariesCalls), 2);
    const loadedScreenshot = path.join(screenshotDir, `prompts-loaded-${width}.png`);
    await page.screenshot({ path: loadedScreenshot, fullPage: true });
    console.log(`${width}px: ${errorScreenshot}, ${loadedScreenshot}`);
  }

  assert.deepEqual(browserErrors, [], "browser emitted an error");
  console.log(`Prompt library load error browser proof passed at ${widths.join(", ")}px; head ${codeHead}`);
} finally {
  await browser?.close();
  await vite.close();
}
