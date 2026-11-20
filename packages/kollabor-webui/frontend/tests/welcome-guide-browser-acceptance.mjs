// Run with: node tests/welcome-guide-browser-acceptance.mjs <screenshot-directory>
// Uses the real Web UI with a synthetic engine API; no model provider or agent is needed.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  launchAcceptanceBrowser,
  makeFixtures,
  openPage,
  openVite,
} from "./learn-browser-acceptance.mjs";

const screenshotDir = path.resolve(process.argv[2] ?? ".welcome-guide-screenshots");
const dimensions = [
  { width: 1280, height: 900, label: "desktop", isMobile: false },
  { width: 820, height: 1180, label: "820", isMobile: false },
  { width: 390, height: 844, label: "390", isMobile: true },
];

async function preparePage(browser, baseUrl, width, height, isMobile, theme) {
  const view = await openPage(browser, baseUrl, makeFixtures(), {
    viewport: { width, height },
    isMobile,
  });
  await view.page.emulateMedia({ colorScheme: theme });
  await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await view.page.evaluate((color) => {
    document.documentElement.classList.toggle("dark", color === "dark");
  }, theme);
  await view.page.getByTestId("guided-setup-open").waitFor({ state: "visible" });
  await view.page.waitForFunction(() => {
    const button = document.querySelector('[data-testid="guided-setup-open"]');
    return button instanceof HTMLButtonElement && !button.disabled;
  });
  return view;
}

async function dismissGuideAndStartChat(page) {
  await page.getByTestId("guided-setup-open").click();
  await page.getByRole("dialog").waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Get Started", exact: true }).click();
  await page.getByTestId("setup-step-agent").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelector('[data-testid="setup-step-agent"]')?.getAttribute("aria-current") === "step");
  const guide = page.getByRole("dialog");
  const activeStep = guide.locator('[data-testid^="setup-step-"][aria-current="step"]');
  await activeStep.waitFor({ state: "visible" });
  const stepBeforeDismissal = await activeStep.getAttribute("data-testid");
  await guide.getByRole("button", { name: "Skip", exact: true }).click();
  const notice = page.getByRole("status").filter({
    hasText: "Welcome guide closed. Type /welcome in a chat to open it again.",
  });
  await notice.waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll("button")].find((item) => item.textContent?.includes("Start Session"));
    return button instanceof HTMLButtonElement && !button.disabled;
  });
  await page.getByRole("button", { name: "Start Session", exact: true }).click();
  await page.getByRole("textbox", { name: "Message input" }).waitFor({ state: "visible" });
  return { notice, stepBeforeDismissal };
}

async function captureRecovery(browser, baseUrl, capture, theme) {
  const view = await preparePage(
    browser,
    baseUrl,
    capture.width,
    capture.height,
    capture.isMobile,
    theme,
  );
  try {
    const sentFrames = [];
    view.page.on("websocket", (socket) => {
      socket.on("framesent", (frame) => {
        if (typeof frame.payload === "string") sentFrames.push(frame.payload);
      });
    });
    const { notice, stepBeforeDismissal } = await dismissGuideAndStartChat(view.page);
    await view.page.screenshot({
      path: path.join(screenshotDir, `welcome-guide-notice-${capture.label}-${theme}.png`),
      animations: "disabled",
    });

    const input = view.page.getByRole("textbox", { name: "Message input" });
    await input.fill("/welcome");
    await view.page.getByText("Show the welcome guide again", { exact: true }).waitFor({ state: "visible" });
    await view.page.screenshot({
      path: path.join(screenshotDir, `welcome-guide-palette-${capture.label}-${theme}.png`),
      animations: "disabled",
    });
    await input.press("Enter");
    const guide = view.page.getByRole("dialog");
    await guide.waitFor({ state: "visible" });
    await guide.locator(`[data-testid="${stepBeforeDismissal}"][aria-current="step"]`).waitFor({ state: "visible" });
    assert.ok(
      sentFrames.every((frame) => !frame.toLowerCase().includes("/welcome")),
      "the local /welcome command was sent to the agent",
    );
    assert.equal(await view.page.getByText("/welcome", { exact: true }).count(), 0);
    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.consoleErrors, []);
    assert.deepEqual(view.externalRequests, []);
    console.log(`PASS: ${capture.label}px ${theme}; notice, palette, resumed ${stepBeforeDismissal}, no agent message`);

    if (capture.label === "desktop" && theme === "dark") {
      await view.page.keyboard.press("Escape");
      await notice.waitFor({ state: "visible" });
      await input.fill("/welcome");
      await input.press("Enter");
      await guide.waitFor({ state: "visible" });
      await guide.getByRole("button", { name: "Skip setup", exact: true }).click();
      await notice.waitFor({ state: "visible" });
      await input.fill("/welcome");
      await input.press("Enter");
      await guide.waitFor({ state: "visible" });
      await view.page.mouse.click(1, 1);
      await guide.waitFor({ state: "hidden" });
      await notice.waitFor({ state: "visible" });
      await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
      await notice.waitFor({ state: "hidden" });
      console.log("PASS: Escape, close button, outside click, and notice dismissal");
    }
  } finally {
    await view.context.close();
  }
}

async function verifyFinishDoesNotShowNotice(browser, baseUrl) {
  const view = await openPage(browser, baseUrl, makeFixtures(), {
    initialGuideState: JSON.stringify({ step: "done", dismissed: false }),
  });
  try {
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByTestId("guided-setup-open").waitFor({ state: "visible" });
    await view.page.waitForFunction(() => {
      const button = document.querySelector('[data-testid="guided-setup-open"]');
      return button instanceof HTMLButtonElement && !button.disabled;
    });
    await view.page.getByTestId("guided-setup-open").click();
    const guide = view.page.getByRole("dialog");
    await guide.waitFor({ state: "visible" });
    await guide.getByRole("heading", { name: "You're set", exact: true }).waitFor({ state: "visible" });
    await guide.getByRole("button", { name: "Finish", exact: true }).click();
    await guide.waitFor({ state: "hidden" });
    assert.equal(
      await view.page.getByText("Welcome guide closed. Type /welcome in a chat to open it again.", { exact: true }).count(),
      0,
    );
    console.log("PASS: Finish closes the guide without a dismissal notice");
  } finally {
    await view.context.close();
  }
}

async function main() {
  fs.mkdirSync(screenshotDir, { recursive: true });
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    for (const capture of dimensions) {
      for (const theme of ["dark", "light"]) {
        await captureRecovery(browser, baseUrl, capture, theme);
      }
    }
    await verifyFinishDoesNotShowNotice(browser, baseUrl);
    console.log(`PASS: 12 welcome guide screenshots saved in ${screenshotDir}`);
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
