import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const screenshotDir = process.env.SETUP_GUIDE_SCREENSHOT_DIR ?? path.join(os.tmpdir(), "kollab-setup-guide-acceptance");

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    const view = await openPage(browser, baseUrl, makeFixtures());
    await view.page.route("**/sessions*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
      }),
    );
    await view.context.addInitScript(() => {
      localStorage.setItem("kollab.setup-guide.v1", JSON.stringify({ step: "network", dismissed: false }));
    });
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByRole("button", { name: "Guided setup", exact: true }).click();
    await view.page.getByRole("heading", { name: "Join a network", exact: true }).waitFor({ state: "visible" });
    await view.page.getByTestId("setup-guide-next").click();
    await view.page.getByTestId("setup-step-agent").waitFor({ state: "visible" });
    assert.equal(await view.page.getByTestId("setup-step-agent").getAttribute("aria-current"), "step");
    await view.page.keyboard.press("Escape");
    await fs.mkdir(screenshotDir, { recursive: true });
    await view.page.screenshot({ path: path.join(screenshotDir, "setup-guide-resolved-step.png"), fullPage: true });
    assert.deepEqual(view.pageErrors, [], "the setup guide should not throw while advancing");
    console.log(`PASS: saved setup-guide-resolved-step.png to ${screenshotDir}`);
    await view.context.close();

    const fresh = await openPage(browser, baseUrl, makeFixtures(), {
      initialGuideState: JSON.stringify({ step: "welcome", dismissed: true }),
    });
    try {
      await fresh.page.route("**/profiles", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ active: "default", profiles: [{ name: "default", provider: "auto" }] }),
        }),
      );
      await fresh.page.route("**/sessions*", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
        }),
      );
      await fresh.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await fresh.page.getByRole("button", { name: "Guided setup", exact: true }).click();
      await fresh.page.getByRole("heading", { name: "Welcome to Kollab", exact: true }).waitFor({ state: "visible" });
      const setupPanelRequest = fresh.page.waitForRequest(
        (request) => new URL(request.url()).pathname === "/setup/panels/setup",
        { timeout: 5000 },
      );
      await fresh.page.getByTestId("setup-guide-next").click();
      await setupPanelRequest;
      assert.equal(await fresh.page.getByTestId("setup-step-network").getAttribute("aria-current"), "step");
    } finally {
      await fresh.context.close();
    }
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
