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

async function openChat(page, baseUrl) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Start Session", exact: true }).click();
  const input = page.getByRole("textbox", { name: "Message input" });
  await input.waitFor({ state: "visible" });
  return input;
}

async function runFailureRecovery(browser, baseUrl, screenshots, failureMode = "502") {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  let fail = true;
  const catalogRequests = [];
  await view.page.route("**/sessions/learn-regression-session/commands", async (route) => {
    catalogRequests.push(route.request().url());
    if (fail) {
      fail = false;
      if (failureMode === "timeout") {
        await route.abort("timedout");
      } else {
        await route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ detail: "synthetic daemon unavailable" }) });
      }
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      session_id: "learn-regression-session",
      commands: [{ name: "session-only", description: "A command from this session" }],
    }) });
  });
  try {
    const input = await openChat(view.page, baseUrl);
    await input.fill("/session-only draft");
    const unavailable = view.page.getByRole("status").filter({ hasText: "Commands couldn't be loaded for this session" });
    await unavailable.waitFor({ state: "visible", timeout: 10_000 });
    await input.fill("/");
    assert.equal(await view.page.getByText("Show the welcome guide again", { exact: true }).count(), 1,
      "the welcome command must stay available while this session catalog is unavailable");
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0,
      "built-in commands must not be presented as confirmed session commands after a 502");
    await input.fill("/session-only draft");
    fs.mkdirSync(screenshots, { recursive: true });
    await view.page.screenshot({ path: path.join(screenshots, "slash-command-catalog-unavailable.png"), animations: "disabled" });
    for (const width of [820, 390]) {
      await view.page.setViewportSize({ width, height: width === 390 ? 844 : 1180 });
      const dimensions = await view.page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `horizontal overflow at ${width}px`);
      await view.page.screenshot({ path: path.join(screenshots, `slash-command-catalog-unavailable-${width}.png`), animations: "disabled" });
    }
    await unavailable.getByRole("button", { name: "Try Again", exact: true }).click();
    assert.equal(await input.inputValue(), "/session-only draft", "retry must preserve the typed composer draft");
    await input.fill("/");
    await view.page.getByText("A command from this session", { exact: true }).waitFor({ state: "visible" });
    await view.page.getByText("Show the welcome guide again", { exact: true }).waitFor({ state: "visible" });
    assert.ok(catalogRequests.length >= 2, "retry must cross the real session command request boundary");
    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.consoleErrors.filter((error) => !error.includes("status of 502") && !error.includes("net::ERR_TIMED_OUT")), []);
    console.log(`PASS: isolated ${failureMode}, visible retry, session command recovery, typed draft preserved`);
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
    const input = await openChat(view.page, baseUrl);
    await input.fill("/");
    await view.page.getByText("A command from this session", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0);
    assert.deepEqual(view.pageErrors, []);
    console.log("PASS: healthy session renders its server catalog");
  } finally {
    await view.context.close();
  }
}

async function runEmptyCatalog(browser, baseUrl) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  await view.page.route("**/sessions/learn-regression-session/commands", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ session_id: "learn-regression-session", commands: [] }),
  }));
  try {
    const input = await openChat(view.page, baseUrl);
    await input.fill("/");
    await view.page.getByRole("status").filter({ hasText: "No commands are available for this session" }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("Show the welcome guide again", { exact: true }).count(), 1,
      "the welcome command must stay available when this session has no registered commands");
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0,
      "an authoritative empty response must not expose built-in commands");
    assert.deepEqual(view.pageErrors, []);
    console.log("PASS: successful empty catalog stays empty");
  } finally {
    await view.context.close();
  }
}

async function runLoadingCatalog(browser, baseUrl) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  await view.page.route("**/sessions/learn-regression-session/commands", () => new Promise(() => {}));
  try {
    const input = await openChat(view.page, baseUrl);
    await input.fill("/");
    await view.page.getByRole("status").filter({ hasText: "Loading commands for this session" }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("Show the welcome guide again", { exact: true }).count(), 1,
      "the welcome command must stay available while this session catalog is loading");
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0,
      "loading must not expose unconfirmed built-in commands");
    assert.deepEqual(view.pageErrors, []);
    console.log("PASS: loading catalog keeps only the welcome command available");
  } finally {
    await view.context.close();
  }
}

async function runLegacyFallback(browser, baseUrl) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  await view.page.route("**/sessions/learn-regression-session/commands", (route) => route.fulfill({
    status: 404,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Not Found" }),
  }));
  try {
    const input = await openChat(view.page, baseUrl);
    await input.fill("/help");
    await view.page.getByText("This engine does not provide a live command catalog", { exact: false }).waitFor({ state: "visible" });
    await view.page.getByText("Show available commands and usage", { exact: true }).waitFor({ state: "visible" });
    console.log("PASS: unsupported endpoint uses an explicitly labeled compatibility catalog");
  } finally {
    await view.context.close();
  }
}

async function runMissingSession404(browser, baseUrl) {
  const fixtures = makeFixtures();
  const view = await openPage(browser, baseUrl, fixtures);
  await view.page.route("**/sessions/learn-regression-session/commands", (route) => route.fulfill({
    status: 404,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Session not found" }),
  }));
  try {
    const input = await openChat(view.page, baseUrl);
    await input.fill("/");
    await view.page.getByRole("status").filter({ hasText: "Commands couldn't be loaded for this session" }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("Show available commands and usage", { exact: true }).count(), 0,
      "a missing session must not be mistaken for an older engine");
    console.log("PASS: missing-session 404 stays unavailable");
  } finally {
    await view.context.close();
  }
}

async function runSessionSwitchStaleResponse(browser, baseUrl) {
  const localSession = {
    session_id: "local-catalog-session",
    name: "Local Catalog Session",
    agent: "coder",
    identity: "local-coder",
    profile: "default",
    workspace: "/learn-demo",
    active: true,
  };
  const fixtures = makeFixtures({ sessions: [localSession] });
  const view = await openPage(browser, baseUrl, fixtures);
  let localCatalogCalls = 0;
  let releaseStale;
  const staleGate = new Promise((resolve) => { releaseStale = resolve; });
  await view.page.route("**/sessions/local-catalog-session/commands", async (route) => {
    localCatalogCalls += 1;
    if (localCatalogCalls === 1) {
      await staleGate;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        session_id: "local-catalog-session",
        commands: [{ name: "stale-local", description: "A stale local command" }],
      }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      session_id: "local-catalog-session",
      commands: [{ name: "local-current", description: "The current local command" }],
    }) });
  });
  await view.page.route("**/sessions/remote-session-1/commands", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ session_id: "remote-session-1", commands: [
      { name: "remote-current", description: "The current remote command" },
    ] }),
  }));
  try {
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await view.page.getByText(localSession.name, { exact: true }).waitFor({ state: "visible" });
    await view.page.waitForFunction(() => document.querySelector('[data-testid="guided-setup-open"]') === null);
    await view.page.waitForFunction(() => document.querySelectorAll('[data-sidebar="sidebar"] button').length > 0);
    await view.page.waitForFunction(() => document.querySelector('[data-sidebar="sidebar"] button[title*="Local Catalog Session"]') !== null);
    const composer = view.page.getByRole("textbox", { name: "Message input" });
    await composer.fill("/");
    const localRequestDeadline = Date.now() + 10_000;
    while (localCatalogCalls === 0) {
      assert.ok(Date.now() < localRequestDeadline, "local catalog request did not start");
      await view.page.waitForTimeout(25);
    }
    await view.page.locator('[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]').click();
    await view.page.getByRole("textbox", { name: "Message input" }).fill("/");
    await view.page.getByText("The current remote command", { exact: true }).waitFor({ state: "visible" });
    releaseStale();
    await view.page.waitForResponse((response) => response.url().includes("/sessions/local-catalog-session/commands"));
    await view.page.waitForTimeout(100);
    assert.equal(await view.page.getByText("A stale local command", { exact: true }).count(), 0,
      "a late response from the previous session must not replace the selected session catalog");
    await view.page.locator('[data-sidebar="sidebar"] button[title*="Local Catalog Session"]').click();
    await view.page.getByRole("textbox", { name: "Message input" }).fill("/");
    await view.page.getByText("The current local command", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await view.page.getByText("A stale local command", { exact: true }).count(), 0);
    console.log("PASS: late catalog response is ignored after session switch");
  } finally {
    releaseStale?.();
    await view.context.close();
  }
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    await runFailureRecovery(browser, baseUrl, screenshotDir);
    await runFailureRecovery(browser, baseUrl, screenshotDir, "timeout");
    await runHealthySession(browser, baseUrl);
    await runEmptyCatalog(browser, baseUrl);
    await runLoadingCatalog(browser, baseUrl);
    await runLegacyFallback(browser, baseUrl);
    await runMissingSession404(browser, baseUrl);
    await runSessionSwitchStaleResponse(browser, baseUrl);
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
