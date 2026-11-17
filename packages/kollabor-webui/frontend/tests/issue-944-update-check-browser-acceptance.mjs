import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const screenshotDir = process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-944-update-check");

async function main() {
  await fs.mkdir(screenshotDir, { recursive: true });
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    const { page } = await openPage(browser, baseUrl, makeFixtures());
    const lastSuccess = Date.now() / 1000 - 3600;
    let updateResponse = {
      current_version: "0.15.3",
      status: "failed",
      available: false,
      release: null,
      checked_at: null,
      last_successful_check: lastSuccess,
      retry_after_seconds: null,
      notes: [],
    };
    await page.route("**/api/update/check*", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(updateResponse),
    }));
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });

    const failure = page.getByRole("alert").filter({ hasText: "Couldn't check for updates." });
    await failure.waitFor({ state: "visible", timeout: 10_000 });
    assert.match(await failure.innerText(), /Last successful check:/);
    assert.equal(await page.getByText("Kollab is up to date.", { exact: false }).count(), 0);
    await failure.screenshot({ path: path.join(screenshotDir, "issue-944-update-check-failed.png") });

    for (const width of [390, 820]) {
      await page.setViewportSize({ width, height: 844 });
      const bounds = await failure.boundingBox();
      assert.ok(bounds, `failure notice is visible at ${width}px`);
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width, `notice fits ${width}px viewport`);
      await failure.screenshot({ path: path.join(screenshotDir, `issue-944-update-check-failed-${width}.png`) });
    }

    await page.setViewportSize({ width: 1280, height: 900 });
    updateResponse = {
      ...updateResponse,
      status: "current",
      checked_at: Date.now() / 1000,
      last_successful_check: Date.now() / 1000,
    };
    await failure.getByRole("button", { name: "Try Again", exact: true }).click();
    const current = page.getByRole("status").filter({ hasText: "Kollab is up to date." });
    await current.waitFor({ state: "visible", timeout: 5_000 });
    await failure.waitFor({ state: "hidden", timeout: 5_000 });
    await current.screenshot({ path: path.join(screenshotDir, "issue-944-update-check-recovered.png") });

    updateResponse = {
      ...updateResponse,
      status: "failed",
      available: false,
      checked_at: null,
      last_successful_check: lastSuccess,
    };
    await current.getByRole("button", { name: "Check Again", exact: true }).click();
    const refreshedFailure = page.getByRole("alert").filter({ hasText: "Still unable to check for updates." });
    await refreshedFailure.waitFor({ state: "visible", timeout: 5_000 });
    await current.waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(await page.getByText("Kollab is up to date.", { exact: false }).count(), 0);
    await refreshedFailure.screenshot({ path: path.join(screenshotDir, "issue-944-update-check-refresh-failed.png") });

    for (const [name, response, text] of [
      ["available", {
        current_version: "0.15.3", status: "available", available: true,
        release: { version: "99.0.0", tag_name: "v99.0.0", name: "Kollab 99", url: "https://example.invalid/release", is_prerelease: false },
        checked_at: Date.now() / 1000, last_successful_check: Date.now() / 1000,
        retry_after_seconds: null, notes: [],
      }, "Kollab 99.0.0 is available."],
      ["disabled", {
        current_version: "0.15.3", status: "disabled", available: false,
        release: null, checked_at: null, last_successful_check: null,
        retry_after_seconds: null, notes: [],
      }, "Update checks are off."],
      ["rate-limited", {
        current_version: "0.15.3", status: "failed", available: false,
        release: null, checked_at: null, last_successful_check: lastSuccess,
        retry_after_seconds: 60, notes: [],
      }, "GitHub is rate limiting checks"],
    ]) {
      const state = await openPage(browser, baseUrl, makeFixtures());
      await state.page.route("**/api/update/check*", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(response),
      }));
      await state.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      const notice = name === "disabled"
        ? state.page.getByRole("status").filter({ hasText: text })
        : state.page.getByRole(name === "available" ? "status" : "alert").filter({ hasText: text });
      await notice.waitFor({ state: "visible", timeout: 10_000 });
      await notice.screenshot({ path: path.join(screenshotDir, `issue-944-update-check-${name}.png`) });
      await state.context.close();
    }

    console.log(`Browser proof passed; screenshots: ${screenshotDir}`);
  } finally {
    await browser?.close();
    await server.close();
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error.stack ?? error);
    process.exitCode = 1;
  });
}
