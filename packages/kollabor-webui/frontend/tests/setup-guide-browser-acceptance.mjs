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

    const firstRun = await openPage(browser, baseUrl, makeFixtures());
    await firstRun.context.addInitScript(() => localStorage.removeItem("kollab.setup-guide.v1"));
    await firstRun.page.route("**/sessions*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
      }),
    );
    await firstRun.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await firstRun.page.getByRole("heading", { name: "Welcome to Kollab", exact: true }).waitFor({ state: "visible", timeout: 8000 });
    assert.deepEqual(firstRun.pageErrors, [], "the setup guide should open on a clean first run without errors");
    await firstRun.context.close();

    const view = await openPage(browser, baseUrl, makeFixtures(), {
      viewport: { width: 390, height: 844 },
      isMobile: true,
    });
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
    const animationPause = await view.page.addStyleTag({
      content: '[data-slot="dialog-content"] { animation-play-state: paused !important; }',
    });
    await view.page.getByRole("button", { name: "Guided setup", exact: true }).click();
    const openingOpacity = await view.page.getByRole("dialog").evaluate((element) => getComputedStyle(element).opacity);
    assert.equal(
      openingOpacity,
      "1",
      `the guide should fully cover the staged setup surface while opening (opacity: ${openingOpacity})`,
    );
    await animationPause.evaluate((element) => element.remove());
    await view.page.getByRole("heading", { name: "Join a network", exact: true }).waitFor({ state: "visible" });
    const guide = view.page.getByRole("dialog");
    await guide.evaluate((element) =>
      Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => undefined))),
    );
    const guideBounds = await guide.boundingBox();
    const openBounds = await view.page.getByTestId("setup-guide-next").boundingBox();
    assert.ok(guideBounds && openBounds);
    const bottomGap = guideBounds.y + guideBounds.height - (openBounds.y + openBounds.height);
    assert.ok(
      bottomGap >= 12,
      `the active guide should cover the staged setup action row below its footer (gap: ${bottomGap}px)`,
    );
    await fs.mkdir(screenshotDir, { recursive: true });
    await view.page.screenshot({ path: path.join(screenshotDir, "guide-network-phone-dark.png"), fullPage: true });
    await view.page.getByTestId("setup-guide-next").click();
    await view.page.getByTestId("setup-step-agent").waitFor({ state: "visible" });
    assert.equal(await view.page.getByTestId("setup-step-agent").getAttribute("aria-current"), "step");
    await view.page.keyboard.press("Escape");
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

    const pending = await openPage(browser, baseUrl, makeFixtures(), {
      initialGuideState: JSON.stringify({ step: "welcome", dismissed: true }),
    });
    let releaseProfiles;
    const profilesGate = new Promise((resolve) => {
      releaseProfiles = resolve;
    });
    try {
      await pending.page.route("**/sessions*", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
      }));
      const startupProfiles = pending.page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/profiles",
        { timeout: 8000 },
      );
      await pending.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await startupProfiles;
      await pending.page.route("**/profiles", async (route) => {
        await profilesGate;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ active: "default", profiles: [{ name: "default", provider: "auto" }] }),
        });
      });
      await pending.page.getByTestId("guided-setup-open").click();
      await pending.page.getByRole("heading", { name: "Welcome to Kollab", exact: true }).waitFor({ state: "visible" });
      assert.equal(
        await pending.page.getByRole("button", { name: "Open provider setup", exact: true }).isVisible(),
        true,
        "provider setup should remain directly reachable while /profiles is pending",
      );
      const start = pending.page.getByTestId("setup-guide-next");
      assert.equal(await start.isDisabled(), false, "Get Started should remain available while /profiles is pending");
      let setupPanelRequests = 0;
      pending.page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/setup/panels/setup") setupPanelRequests += 1;
      });
      await start.click();
      await pending.page.waitForTimeout(200);
      assert.equal(setupPanelRequests, 0, "Setup should wait until /profiles identifies the provider");
      assert.equal(await pending.page.getByTestId("setup-step-network").getAttribute("aria-current"), "step");
      const setupPanelRequest = pending.page.waitForRequest(
        (request) => new URL(request.url()).pathname === "/setup/panels/setup",
        { timeout: 5000 },
      );
      releaseProfiles();
      await setupPanelRequest;
      assert.equal(setupPanelRequests, 1);
      assert.equal(await pending.page.getByTestId("setup-step-network").getAttribute("aria-current"), "step");
    } finally {
      releaseProfiles();
      await pending.context.close();
    }

    const existingProvider = await openPage(browser, baseUrl, makeFixtures(), {
      initialGuideState: JSON.stringify({ step: "welcome", dismissed: true }),
    });
    let releaseExistingProfiles;
    const existingProfilesGate = new Promise((resolve) => {
      releaseExistingProfiles = resolve;
    });
    try {
      await existingProvider.page.route("**/sessions*", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
      }));
      const startupProfiles = existingProvider.page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/profiles",
        { timeout: 8000 },
      );
      await existingProvider.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await startupProfiles;
      await existingProvider.page.route("**/profiles", async (route) => {
        await existingProfilesGate;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ active: "work", profiles: [{ name: "work", provider: "openai" }] }),
        });
      });
      const guideProfiles = existingProvider.page.waitForRequest(
        (request) => new URL(request.url()).pathname === "/profiles",
        { timeout: 5000 },
      );
      await existingProvider.page.getByTestId("guided-setup-open").click();
      await guideProfiles;
      await existingProvider.page.getByRole("heading", { name: "Welcome to Kollab", exact: true }).waitFor({ state: "visible" });
      const start = existingProvider.page.getByTestId("setup-guide-next");
      assert.equal(await start.isDisabled(), false, "Get Started should remain available while /profiles is pending");
      let setupPanelRequests = 0;
      existingProvider.page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/setup/panels/setup") setupPanelRequests += 1;
      });
      await start.click();
      await existingProvider.page.waitForTimeout(200);
      assert.equal(setupPanelRequests, 0, "a pending profile check must not open Setup for an existing provider");
      const response = existingProvider.page.waitForResponse(
        (item) => new URL(item.url()).pathname === "/profiles" && item.request().method() === "GET",
        { timeout: 5000 },
      );
      releaseExistingProfiles();
      await response;
      await existingProvider.page.waitForTimeout(200);
      assert.equal(setupPanelRequests, 0, "an existing provider must not open Settings > Setup");
      assert.equal(await existingProvider.page.getByTestId("setup-step-network").getAttribute("aria-current"), "step");
    } finally {
      releaseExistingProfiles();
      await existingProvider.context.close();
    }

    const failedProfiles = await openPage(browser, baseUrl, makeFixtures(), {
      initialGuideState: JSON.stringify({ step: "welcome", dismissed: true }),
    });
    try {
      await failedProfiles.page.route("**/sessions*", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sessions: [], network: { live: false, remote: [], networks: [] } }),
      }));
      const startupProfiles = failedProfiles.page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/profiles",
        { timeout: 8000 },
      );
      await failedProfiles.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await startupProfiles;
      await failedProfiles.page.route("**/profiles", (route) => route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "temporary profile failure" }),
      }));
      const guideProfiles = failedProfiles.page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/profiles",
        { timeout: 5000 },
      );
      await failedProfiles.page.getByTestId("guided-setup-open").click();
      const guideProfilesResponse = await guideProfiles;
      assert.equal(guideProfilesResponse.status(), 503);
      await failedProfiles.page.getByRole("heading", { name: "Welcome to Kollab", exact: true }).waitFor({ state: "visible" });
      let setupPanelRequests = 0;
      failedProfiles.page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/setup/panels/setup") setupPanelRequests += 1;
      });
      const start = failedProfiles.page.getByTestId("setup-guide-next");
      assert.equal(await start.isDisabled(), false, "Get Started should remain available after a profile request error");
      await start.click();
      await failedProfiles.page.waitForTimeout(200);
      assert.equal(setupPanelRequests, 0, "a failed profile check must not be treated as an unconfigured profile");
      assert.equal(await failedProfiles.page.getByTestId("setup-step-network").getAttribute("aria-current"), "step");
      const recovery = failedProfiles.page.getByRole("button", { name: "Open provider setup", exact: true });
      assert.equal(await recovery.isVisible(), true, "provider setup should remain directly reachable after /profiles fails");
      const setupPanelRequest = failedProfiles.page.waitForRequest(
        (request) => new URL(request.url()).pathname === "/setup/panels/setup",
        { timeout: 5000 },
      );
      await recovery.click();
      await setupPanelRequest;
      assert.equal(setupPanelRequests, 1, "the recovery action should open Settings > Setup");
    } finally {
      await failedProfiles.context.close();
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
