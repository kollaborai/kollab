import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const screenshotDir = path.resolve(process.argv[2] ?? path.join(os.tmpdir(), "pr-chain-sidebar-visibility"));
const buttonName = { name: "PR Chain", exact: true };

async function capture(page, name) {
  await fs.mkdir(screenshotDir, { recursive: true });
  await page.screenshot({ path: path.join(screenshotDir, name), animations: "disabled" });
}

async function waitForRead(readCount, currentReadCount, timeoutMs = 1000) {
  if (readCount() > currentReadCount) return true;
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      readWaiters.delete(onRead);
      resolve(false);
    }, timeoutMs);
    const onRead = () => {
      if (readCount() <= currentReadCount) return;
      clearTimeout(timeout);
      readWaiters.delete(onRead);
      resolve(true);
    };
    readWaiters.add(onRead);
  });
}

const readWaiters = new Set();

async function openSidebarAt(page, width) {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
  if (width < 768 && (await page.locator('[data-sidebar="sidebar"]:visible').count()) === 0) {
    await page.getByRole("button", { name: "Toggle Sidebar" }).first().click();
    await page.locator('[data-sidebar="sidebar"]:visible').first().waitFor({ state: "visible" });
  }
}

async function checkButtonAt(page, width, visible, screenshot) {
  await openSidebarAt(page, width);
  const button = page.getByRole("button", buttonName);
  if (visible) {
    await button.waitFor({ state: "visible" });
    const bounds = await button.boundingBox();
    assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width, `PR Chain button fits in the ${width}px viewport`);
    await capture(page, screenshot);
  } else {
    await page.waitForTimeout(80);
    await capture(page, screenshot);
    assert.equal(await button.count(), 0, `PR Chain button is absent at ${width}px without the data folder`);
  }
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  let rootFound = false;
  let prChainReads = 0;
  try {
    browser = await launchAcceptanceBrowser();
    const view = await openPage(browser, baseUrl, makeFixtures());
    await view.page.route("**/pr-chain", async (route) => {
      prChainReads += 1;
      for (const wake of readWaiters) wake();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ root_found: rootFound, items: [], events: [], truncated: false }),
      });
    });

    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByRole("button", { name: "What’s New", exact: true }).waitFor({ state: "visible" });
    await waitForRead(() => prChainReads, 0);
    await view.page.waitForTimeout(80);
    await checkButtonAt(view.page, 1280, false, "pr-chain-sidebar-no-data-1280.png");
    await checkButtonAt(view.page, 390, false, "pr-chain-sidebar-no-data-390.png");
    await checkButtonAt(view.page, 820, false, "pr-chain-sidebar-no-data-820.png");

    rootFound = true;
    const beforePresentRead = prChainReads;
    await view.page.setViewportSize({ width: 1280, height: 900 });
    await view.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    assert.equal(
      await waitForRead(() => prChainReads, beforePresentRead),
      true,
      "sidebar refreshes runner-folder availability when the page becomes visible",
    );
    await view.page.getByRole("button", buttonName).waitFor({ state: "visible" });
    await capture(view.page, "pr-chain-sidebar-present-1280.png");
    await view.page.getByRole("button", buttonName).click();
    await view.page.getByText(/Nothing is tracked yet/).waitFor({ state: "visible" });
    await capture(view.page, "pr-chain-dialog-present-1280.png");

    await view.page.keyboard.press("Escape");
    await checkButtonAt(view.page, 390, true, "pr-chain-sidebar-present-390.png");
    await checkButtonAt(view.page, 820, true, "pr-chain-sidebar-present-820.png");

    assert.deepEqual(view.pageErrors, [], "the page should not report runtime errors");
    assert.deepEqual(view.externalRequests, [], "the proof should not call external services");
    console.log("PR Chain sidebar browser acceptance PASS; screenshots saved: no-data and present at 1280px, 390px, 820px; dialog at 1280px.");
    await view.context.close();
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
