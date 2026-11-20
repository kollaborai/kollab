// Run from the frontend directory: node tests/sidebar-footer-touch-target-browser-acceptance.mjs <artifact-dir>
// Opens the mocked shell at touch and desktop widths, checks footer targets and row fit, and saves screenshots.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const screenshotDir = process.argv[2]
  ? path.resolve(process.argv[2])
  : process.env.KOLLAB_BROWSER_ARTIFACT_DIR
    ? path.resolve(process.env.KOLLAB_BROWSER_ARTIFACT_DIR)
    : path.join(os.tmpdir(), `kollab-sidebar-footer-touch-targets-${process.pid}`);

const viewports = [
  { width: 390, height: 844, isMobile: true },
  { width: 820, height: 1180, isMobile: false },
  { width: 1280, height: 800, isMobile: false },
];

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    const chromePath = process.env.CHROME_PATH
      ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    browser = await chromium.launch({
      headless: true,
      ...(chromePath ? { executablePath: chromePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    fs.mkdirSync(screenshotDir, { recursive: true });
    const failures = [];
    for (const viewport of viewports) {
      const view = await openPage(browser, baseUrl, makeFixtures(), {
        viewport: { width: viewport.width, height: viewport.height },
        isMobile: viewport.isMobile,
      });
      try {
        await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
        if (viewport.width < 1024) {
          await view.page.locator('[data-sidebar="trigger"]').click();
        }

        const actions = view.page.locator('[data-sidebar="footer-actions"]');
        await actions.waitFor({ state: "visible" });
        await view.page.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        const layout = await actions.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          const footer = element.closest('[data-slot="sidebar-footer"]');
          const footerBounds = footer.getBoundingClientRect();
          return {
            actionWidth: element.clientWidth,
            actionHeight: element.clientHeight,
            actionScrollWidth: element.scrollWidth,
            footerTop: footerBounds.top,
            footerBottom: footerBounds.bottom,
            buttons: Array.from(element.querySelectorAll(":scope > button")).map((button, index) => {
              const rect = button.getBoundingClientRect();
              return {
                index,
                label: button.getAttribute("aria-label") || button.getAttribute("title") || button.textContent.trim(),
                width: rect.width,
                height: rect.height,
                left: rect.left - bounds.left,
                right: rect.right - bounds.left,
                top: rect.top - bounds.top,
                bottom: rect.bottom - bounds.top,
              };
            }),
            viewportWidth: window.innerWidth,
            documentWidth: document.documentElement.scrollWidth,
            viewportHeight: window.innerHeight,
          };
        });

        const screenshot = `sidebar-footer-touch-targets-${viewport.width}.png`;
        await view.page.screenshot({ path: path.join(screenshotDir, screenshot), animations: "disabled" });
        const sizes = layout.buttons.map((button) => `${button.width}x${button.height}`).join(", ");
        console.log(`${viewport.width}px footer buttons: ${sizes}; screenshot=${screenshot}`);

        assert.equal(layout.buttons.length, 8, `expected eight visible footer buttons at ${viewport.width}px`);
        for (const button of layout.buttons) {
          if (viewport.width < 1024 && (button.width < 44 || button.height < 44)) {
            failures.push(`${viewport.width}px button ${button.index + 1} is ${button.width}x${button.height}px`);
          }
          assert.ok(button.left >= -1 && button.right <= layout.actionWidth + 1,
            `footer button ${button.index + 1} extends beyond the action row at ${viewport.width}px`);
          assert.ok(button.top >= -1 && button.bottom <= layout.actionHeight + 1,
            `footer button ${button.index + 1} extends beyond the action row vertically at ${viewport.width}px`);
        }
        assert.ok(layout.actionScrollWidth <= layout.actionWidth + 1,
          `footer actions overflow horizontally at ${viewport.width}px`);
        assert.ok(layout.documentWidth <= layout.viewportWidth + 1,
          `the page overflows horizontally at ${viewport.width}px`);
        assert.ok(layout.footerTop >= -1 && layout.footerBottom <= layout.viewportHeight + 1,
          `sidebar footer does not fit in the viewport at ${viewport.width}px`);
        assert.deepEqual(view.pageErrors, [], view.pageErrors.join(" | "));
        assert.deepEqual(view.consoleErrors, [], view.consoleErrors.join(" | "));
        assert.equal(view.externalRequests.length, 0, "browser attempted an external request");
      } finally {
        await view.context.close();
      }
    }

    assert.ok(failures.length === 0,
      `footer touch targets are below 44x44px: ${failures.join("; ")}`);
    console.log("Sidebar footer touch target browser acceptance passed.");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
