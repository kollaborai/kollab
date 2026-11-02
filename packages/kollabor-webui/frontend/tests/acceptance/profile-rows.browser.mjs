// Focused retained UI coverage for the Profiles list rows (issue #578): at
// narrow widths the provider, model, and effort metadata must stay readable
// without hover while Test/Edit/Copy/Delete remain usable by touch.
// Run after `npm ci`. Keep Playwright and its browser isolated from the
// frontend dependency tree:
//   npm install --prefix /tmp/kollab-578-playwright playwright-core
//   npx --prefix /tmp/kollab-578-playwright playwright-core install chromium
// Then run with:
//   PLAYWRIGHT_CORE_PATH=/tmp/kollab-578-playwright/node_modules/playwright-core \
//   node tests/acceptance/profile-rows.browser.mjs
// Browser resolution: CHROME_PATH if set (any Chrome/Chromium binary works),
// else the chromium that `playwright-core install chromium` downloaded under
// PLAYWRIGHT_BROWSERS_PATH (default ~/Library/Caches/ms-playwright on macOS).
// Set PROFILE_ROWS_CAPTURE=1 to write screenshots under docs/media.
// This harness is retained local coverage, not a CI step: ci.yml's web UI job
// runs vitest, node:test and tsc, but no workflow installs a browser binary,
// and the sibling profile-copy acceptance harness follows the same precedent.
// The fixture uses a fake API and synthetic profiles; no engine or key is used.
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
const capture = process.env.PROFILE_ROWS_CAPTURE === "1";
const screenshotDir = path.resolve(
  process.env.PROFILE_ROWS_SCREENSHOT_DIR ?? path.join(repoRoot, "docs/media"),
);
const widths = [390, 820, 1280];
const themes = ["dark", "light"];
const longName = "deep-research-squad-profile-with-a-very-long-name";
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

  await page.goto(`${origin}/tests/acceptance/profile-rows.html`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByRole("heading", { name: "Profiles" }).waitFor();
  await page.getByText(longName).waitFor();

  const actionLabels = ["Test", "Edit", "Copy", "Delete"];

  for (const theme of themes) {
    await page.evaluate((mode) => {
      document.documentElement.classList.toggle("dark", mode === "dark");
    }, theme);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 844 });
      const bounds = await page.evaluate(() => {
        const dialog = document.querySelector('[role="dialog"]');
        if (!dialog) throw new Error("Profiles dialog is missing");
        const dialogRect = dialog.getBoundingClientRect();
        const metas = [...document.querySelectorAll('[data-testid="profile-meta"]')];
        if (metas.length !== 3) {
          throw new Error(`expected 3 profile rows, found ${metas.length}`);
        }
        const expected = window.profileRowsExpectedMeta ?? [];
        const metaBoxes = metas.map((el, index) => {
          const rect = el.getBoundingClientRect();
          return {
            text: el.textContent ?? "",
            expected: expected[index] ?? "",
            insideDialog:
              rect.left >= dialogRect.left - 1 &&
              rect.right <= dialogRect.right + 1,
            notClipped: el.scrollWidth <= el.clientWidth + 1,
          };
        });
        const labels = ["Test", "Edit", "Copy", "Delete"];
        const buttons = [...dialog.querySelectorAll("button")].filter((el) =>
          labels.some((label) =>
            (el.getAttribute("aria-label") ?? "").startsWith(`${label} `),
          ),
        );
        const buttonBoxes = buttons.map((el) => {
          const rect = el.getBoundingClientRect();
          return {
            label: el.getAttribute("aria-label") ?? "",
            visible: rect.width > 0 && rect.height > 0,
            touchable: rect.width >= 24 && rect.height >= 24,
            insideDialog:
              rect.left >= dialogRect.left - 1 &&
              rect.right <= dialogRect.right + 1,
          };
        });
        return {
          viewportWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          metaBoxes,
          buttonBoxes,
        };
      });
      assert.ok(
        bounds.documentWidth <= width,
        `${theme}: document overflows at ${width}px: ${JSON.stringify(bounds)}`,
      );
      bounds.metaBoxes.forEach((meta, index) => {
        assert.ok(meta.insideDialog, `${theme}: meta escapes dialog at ${width}px: ${meta.text}`);
        assert.ok(
          meta.notClipped,
          `${theme}: meta truncated at ${width}px: ${meta.text}`,
        );
        // The row must render the full provider · model · N effort line from
        // the fixture; a dropped piece changes the text and fails here.
        assert.equal(
          meta.text.trim(),
          meta.expected,
          `${theme}: row ${index} metadata incomplete at ${width}px: got "${meta.text.trim()}", expected "${meta.expected}"`,
        );
      });
      assert.equal(bounds.buttonBoxes.length, 12, `${theme}: expected 4 actions on 3 rows at ${width}px`);
      for (const button of bounds.buttonBoxes) {
        assert.ok(button.visible, `${theme}: action hidden at ${width}px: ${button.label}`);
        assert.ok(button.touchable, `${theme}: action too small for touch at ${width}px: ${button.label}`);
        assert.ok(button.insideDialog, `${theme}: action clipped at ${width}px: ${button.label}`);
      }
      if (capture) {
        await mkdir(screenshotDir, { recursive: true });
        await page.screenshot({
          path: path.join(screenshotDir, `profile-rows-${theme}-${width}.png`),
          fullPage: true,
        });
      }
    }
  }

  // Touch usability proof at the narrowest width: the Test action must remain
  // clickable on the long-name row at 390px.
  await page.setViewportSize({ width: widths[0], height: 844 });
  const before = await page.evaluate(() => window.profileRowsTestCalls);
  await page.getByRole("button", { name: `Test ${longName}` }).click();
  assert.equal(
    await page.evaluate(() => window.profileRowsTestCalls),
    before + 1,
    "Test action must stay clickable at 390px",
  );

  // Keyboard access: Tab must reach the row actions without a pointer.
  await page.getByRole("heading", { name: "Profiles" }).focus();
  let sawAction = false;
  for (let i = 0; i < 30 && !sawAction; i += 1) {
    await page.keyboard.press("Tab");
    sawAction = await page.evaluate(() => {
      const label = document.activeElement?.getAttribute("aria-label") ?? "";
      return /^(Test|Edit|Copy|Delete) /.test(label);
    });
  }
  assert.ok(sawAction, "Tab focus must reach the row actions");

  assert.deepEqual(externalRequests, [], "fixture attempted an external request");
  assert.deepEqual(browserErrors, [], "browser reported a page or console error");

  console.log(
    `PASS profile rows metadata readable + actions touch-usable; widths ${widths.join(", ")}px; themes ${themes.join(", ")}; mocked API; ${capture ? `screenshots: ${screenshotDir}` : "screenshots not captured"}`,
  );
} finally {
  await browser?.close();
  await vite.close();
}
