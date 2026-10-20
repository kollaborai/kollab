// Focused retained UI coverage for profile copying. Run after `npm ci`.
// Keep Playwright and its browser isolated from the frontend dependency tree:
// `npm install --prefix /tmp/kollab-257-playwright playwright-core`
// `PLAYWRIGHT_BROWSERS_PATH=/tmp/kollab-257-playwright-browsers \
//   /tmp/kollab-257-playwright/node_modules/.bin/playwright-core install chromium`
// Then set PLAYWRIGHT_CORE_PATH=/tmp/kollab-257-playwright/node_modules/playwright-core
// and PLAYWRIGHT_BROWSERS_PATH=/tmp/kollab-257-browsers when running this script.
// Set PROFILE_COPY_CAPTURE=1 to write screenshots under docs/media.
// The fixture uses a fake API and synthetic profile; no engine or key is used.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(
  process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core",
);

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "../..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const capture = process.env.PROFILE_COPY_CAPTURE === "1";
const screenshotDir = path.resolve(
  process.env.PROFILE_COPY_SCREENSHOT_DIR ?? path.join(repoRoot, "docs/media"),
);
const disclosure =
  "Eligible profiles copy saved credentials and hidden settings, including ACP runtime fields, server-side. Environment-backed API keys stay with the source; configure the new profile's API-key environment variable before copying. System-keyring references remain shared when used. OAuth and provisioned profiles cannot be copied. You can change the name and reasoning effort here.";
const widths = [390, 820, 1280];
const browserErrors = [];
const externalRequests = [];

const executablePath = process.env.CHROME_PATH ?? chromium.executablePath();
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
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage({ viewport: { width: widths[0], height: 844 } });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const requested = new URL(route.request().url());
    if (requested.origin !== origin) {
      externalRequests.push(requested.href);
      await route.abort();
      return;
    }
    await route.continue();
  });

  await page.goto(`${origin}/tests/acceptance/profile-copy.html`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByRole("heading", { name: "Profiles" }).waitFor();
  await page.getByRole("button", { name: "Copy demo-work" }).click();
  await page.getByRole("heading", { name: "Copy: demo-work" }).waitFor();
  await page.getByText(disclosure, { exact: true }).waitFor({ state: "visible" });
  assert.equal(await page.locator("#profile-api-key").inputValue(), "");
  const createButton = page.getByRole("button", { name: "Create", exact: true });
  await createButton.waitFor({ state: "visible" });
  assert.equal(
    await page.evaluate(() => window.profileCopyCreateCalls),
    0,
    "opening Copy must not create a profile before the user confirms with Create",
  );

  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 });
    const bounds = await page.evaluate(() => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) throw new Error("Profiles dialog is missing");
      const rect = dialog.getBoundingClientRect();
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        dialogLeft: rect.left,
        dialogRight: rect.right,
      };
    });
    assert.ok(bounds.documentWidth <= width, `document overflows at ${width}px: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.dialogLeft >= 0 && bounds.dialogRight <= width, `dialog is clipped at ${width}px: ${JSON.stringify(bounds)}`);
    if (capture) {
      await mkdir(screenshotDir, { recursive: true });
      await page.screenshot({
        path: path.join(screenshotDir, `profile-copy-mocked-${width}.png`),
        fullPage: true,
      });
    }
  }

  await createButton.click();
  await page.waitForFunction(() => window.profileCopyCreateCalls === 1);
  const submitted = await page.evaluate(() => window.profileCopyLastCreate);
  assert.equal(submitted.copy_from, "demo-work");
  assert.equal(submitted.name, "demo-work-copy");
  assert.equal(Object.hasOwn(submitted, "api_key"), false);
  assert.deepEqual(externalRequests, [], "fixture attempted an external request");
  assert.deepEqual(browserErrors, [], "browser reported a page or console error");

  console.log(
    `PASS profile copy disclosure + explicit Create; widths ${widths.join(", ")}px; mocked API; ${capture ? `screenshots: ${screenshotDir}` : "screenshots not captured"}`,
  );
} finally {
  await browser?.close();
  await vite.close();
}
