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
      localStorage.setItem("kollab.setup-guide.v1", JSON.stringify({ step: "provider", dismissed: false }));
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
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
