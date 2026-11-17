// Run with: node tests/slash-command-catalog-browser-acceptance.mjs <screenshot-directory>
// Uses the real Web UI with a synthetic engine API; no model provider or agent is needed.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  launchAcceptanceBrowser,
  makeFixtures,
  openPage,
  openVite,
} from "./learn-browser-acceptance.mjs";

const screenshotDir = path.resolve(process.argv[2] ?? ".slash-command-catalog-screenshots");

async function runFailureRecovery(browser, baseUrl, screenshots) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  let fail = true;
  const catalogRequests = [];
  await view.page.route("**/sessions/learn-regression-session/commands", async (route) => {
    catalogRequests.push(route.request().url());
    if (fail) {
      fail = false;
      await route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ detail: "synthetic daemon timeout" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      session_id: "learn-regression-session",
      commands: [{ name: "session-only", description: "A command from this session" }],
    }) });
  });
  try {
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByTestId("guided-setup-open").click();
    await view.page.getByRole("button", { name: "Get Started", exact: true }).click();
    await view.page.getByTestId("setup-step-agent").waitFor({ state: "visible" });
    await view.page.getByRole("button", { name: "Skip", exact: true }).click();
    await view.page.getByRole("button", { name: "Start Session", exact: true }).click();
    const input = view.page.getByRole("textbox", { name: "Message input" });
    await input.waitFor({ state: "visible" });
    await input.fill("/session-only draft");
    const unavailable = view.page.getByRole("status").filter({ hasText: "Commands couldn't be loaded for this session" });
    await unavailable.waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0,
      "built-in commands must not be presented as confirmed session commands after a 502");
    fs.mkdirSync(screenshots, { recursive: true });
    await view.page.screenshot({ path: path.join(screenshots, "slash-command-catalog-unavailable.png"), animations: "disabled" });
    await unavailable.getByRole("button", { name: "Try Again", exact: true }).click();
    assert.equal(await input.inputValue(), "/session-only draft", "retry must preserve the typed composer draft");
    await input.focus();
    await input.press("End");
    await view.page.getByText("A command from this session", { exact: true }).waitFor({ state: "visible" });
    assert.ok(catalogRequests.length >= 2, "retry must cross the real session command request boundary");
    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.consoleErrors, []);
    console.log("PASS: isolated 502, visible retry, session command recovery, typed draft preserved");
  } finally {
    await view.context.close();
  }
}

async function runHealthySession(browser, baseUrl) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  await view.page.route("**/sessions/learn-regression-session/commands", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ session_id: "learn-regression-session", commands: [
      { name: "session-only", description: "A command from this session" },
    ] }),
  }));
  try {
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByTestId("guided-setup-open").click();
    await view.page.getByRole("button", { name: "Get Started", exact: true }).click();
    await view.page.getByTestId("setup-step-agent").waitFor({ state: "visible" });
    await view.page.getByRole("button", { name: "Skip", exact: true }).click();
    await view.page.getByRole("button", { name: "Start Session", exact: true }).click();
    const input = view.page.getByRole("textbox", { name: "Message input" });
    await input.waitFor({ state: "visible" });
    await input.fill("/");
    await view.page.getByText("A command from this session", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0);
    assert.deepEqual(view.pageErrors, []);
    console.log("PASS: healthy session renders its server catalog");
  } finally {
    await view.context.close();
  }
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    await runFailureRecovery(browser, baseUrl, screenshotDir);
    await runHealthySession(browser, baseUrl);
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
