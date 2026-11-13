// Notice regressions split out of the learn tour so the browser suites can
// run in parallel instead of one 6-minute script (#1022). Covers the release
// notes route (#887), the failed New Session pick line (#956), the dismissed
// retry and hand-made model changes (#959), version mismatch and update lines.
// Uses the real App and a synthetic engine API; it never contacts a real engine.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn notices browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

    // Release updates have their own route, close control, and retry path.
    const releaseView = await openPage(browser, baseUrl, makeFixtures());
    await releaseView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await releaseView.page.getByRole("button", { name: "Learn", exact: true }).waitFor({ state: "visible" });
    await releaseView.page.getByRole("button", { name: "What’s New", exact: true }).click();
    await releaseView.page.waitForFunction(() => location.pathname === "/whats-new");
    await releaseView.page.getByRole("heading", { level: 1, name: "What’s New", exact: true }).waitFor();
    // The first release section: "Unreleased" between releases, "Kollab X.Y.Z" right after one (#887).
    await releaseView.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await releaseView.page.getByRole("button", { name: "Close What’s New", exact: true }).click();
    await releaseView.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");
    await releaseView.context.close();

    const changelogFailure = await openPage(browser, baseUrl, makeFixtures(), { failChangelogOnce: true });
    await changelogFailure.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await changelogFailure.page.getByRole("button", { name: "Retry", exact: true }).click();
    await changelogFailure.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await changelogFailure.context.close();

    const delayed = await openPage(browser, baseUrl, makeFixtures(), { delayChangelogMs: 900, viewport: { width: 390, height: 844 }, isMobile: true });
    await delayed.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await delayed.page.getByRole("status").getByText("Loading release notes…").waitFor({ state: "visible" });
    await delayed.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await delayed.context.close();

    // A pick that fails after New Session started the chat leaves it on another model: say so in one compact
    // line and let the person try again (#956).
    const failing = await openPage(browser, baseUrl, makeFixtures({ pickFails: 1 }));
    await failing.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await failing.page.getByTestId("new-session-options").click();
    await failing.page.locator("#new-session-model").click();
    await failing.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await failing.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    await failing.page.getByRole("complementary", { name: "Session Controls", exact: true })
      .getByText("Learn regression session", { exact: true }).waitFor({ state: "visible", timeout: 60_000 });
    const pickNotice = failing.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await pickNotice.waitFor({ state: "visible", timeout: 5_000 });
    const pickText = await pickNotice.innerText();
    assert.match(pickText, /Couldn't switch to claude-haiku\. .*could not switch models/);
    // What a person reads has no issue numbers, versions or build ids.
    assert.doesNotMatch(pickText, /#\d|\bv?\d+\.\d+\.\d+|[0-9a-f]{7,}/);
    assert.ok((await pickNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    // Try Again puts the session on the pick; the fixture answers it this time, so the notice goes away.
    await pickNotice.getByRole("button", { name: "Try Again", exact: true }).click();
    await pickNotice.waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(
      failing.fixture.requests.filter((request) => request.method === "POST" && request.endpoint.endsWith("/profile")).length,
      2,
    );
    await failing.context.close();

    // A page and an engine on different versions: one compact line with a Reload button. The versions
    // are a hover away, never in the line.
    const mismatch = await openPage(browser, baseUrl, makeFixtures({ engineVersion: "999.1.0+deadbeef" }));
    await mismatch.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const versionNotice = mismatch.page.getByRole("status")
      .filter({ has: mismatch.page.getByRole("button", { name: "Reload", exact: true }) });
    await versionNotice.waitFor({ state: "visible", timeout: 5_000 });
    assert.doesNotMatch(await versionNotice.innerText(), /999|deadbeef|\bv\d/);
    assert.match(await versionNotice.getAttribute("title"), /999\.1\.0\+deadbeef/);
    assert.ok((await versionNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    await versionNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await versionNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await mismatch.context.close();

    // The update notice is the same line, with its two actions.
    const updating = await openPage(browser, baseUrl, makeFixtures({ update: { version: "999.2.0", url: "https://example.invalid/notes" } }));
    await updating.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const updateNotice = updating.page.getByRole("status").filter({ hasText: "999.2.0" });
    await updateNotice.getByRole("button", { name: "Update now", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await updateNotice.getByRole("link", { name: "Release notes", exact: true }).waitFor({ state: "visible" });
    assert.ok((await updateNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    await updateNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await updateNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await updating.context.close();

    // A Try Again still in flight when its notice is dismissed must not bring the notice back when it fails
    // (the clean-room review of #959).
    const heldRetry = makeFixtures({ pickFails: true });
    const heldView = await openPage(browser, baseUrl, heldRetry);
    await heldView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await heldView.page.getByTestId("new-session-options").click();
    await heldView.page.locator("#new-session-model").click();
    await heldView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await heldView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const heldNotice = heldView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await heldNotice.waitFor({ state: "visible", timeout: 60_000 });
    heldRetry.holdPicks();
    await heldNotice.getByRole("button", { name: "Try Again", exact: true }).click();
    await heldNotice.getByRole("button", { name: "Trying…", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await heldNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await heldNotice.waitFor({ state: "hidden", timeout: 5_000 });
    // The held switch now fails, and the page reloads the sessions right after its catch has run. Record the
    // order the page saw (the answer, then a session list request after it) so the assertion below waits for the
    // retry's own end: any earlier session poll would not do.
    const heldEvents = [];
    heldView.page.on("response", (response) => {
      if (response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile")) {
        heldEvents.push("answer");
      }
    });
    heldView.page.on("request", (request) => {
      if (request.method() === "GET" && new URL(request.url()).pathname.endsWith("/sessions")) heldEvents.push("list");
    });
    heldRetry.releasePicks();
    const heldListedAfterAnswer = () => {
      const answeredAt = heldEvents.indexOf("answer");
      return answeredAt >= 0 && heldEvents.indexOf("list", answeredAt) >= 0;
    };
    const heldDeadline = Date.now() + 10_000;
    while (!heldListedAfterAnswer()) {
      assert.ok(Date.now() < heldDeadline, "the held switch was never answered and followed by a session reload");
      await heldView.page.waitForTimeout(25);
    }
    await heldView.page.waitForTimeout(300); // the notice, if it came back, is drawn by now
    assert.equal(
      await heldView.page.getByRole("alert").filter({ hasText: "Couldn't switch to" }).count(),
      0,
      "a dismissed notice came back when its retry failed",
    );
    await heldView.context.close();

    // A model change made by hand that fails leaves the chat on the model it runs, so the notice for the failed
    // New Session pick stays: it and Try Again are the way back to that pick (the clean-room review of #959).
    const manualChange = makeFixtures({ pickFails: true });
    const manualView = await openPage(browser, baseUrl, manualChange);
    await manualView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await manualView.page.getByTestId("new-session-options").click();
    await manualView.page.locator("#new-session-model").click();
    await manualView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await manualView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const manualNotice = manualView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await manualNotice.waitFor({ state: "visible", timeout: 60_000 });
    const manualPanel = manualView.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await manualPanel.getByRole("button", { name: "Model", exact: true }).click();
    const manualAnswer = manualView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await manualView.page.getByRole("group", { name: "default", exact: true })
      .getByRole("option", { name: /gpt-6-luna/ }).click();
    assert.equal((await manualAnswer).status(), 502, "the model change made by hand was meant to fail");
    await manualView.page.waitForTimeout(400);
    await manualNotice.waitFor({ state: "visible", timeout: 5_000 });
    await manualNotice.getByRole("button", { name: "Try Again", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await manualView.context.close();

    // A model change made by hand that only partly applies (the profile's environment sets another model) leaves
    // the chat on something the notice for the failed New Session pick no longer describes: the notice goes
    // (the clean-room review of #959, round 2).
    const partialChange = makeFixtures({ pickFails: 1, environmentModels: { default: "environment-model" } });
    const partialView = await openPage(browser, baseUrl, partialChange);
    await partialView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await partialView.page.getByTestId("new-session-options").click();
    await partialView.page.locator("#new-session-model").click();
    await partialView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await partialView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const partialNotice = partialView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await partialNotice.waitFor({ state: "visible", timeout: 60_000 });
    const partialPanel = partialView.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await partialPanel.getByRole("button", { name: "Model", exact: true }).click();
    const partialAnswer = partialView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await partialView.page.getByRole("group", { name: "default", exact: true })
      .getByRole("option", { name: /gpt-6-luna/ }).click();
    assert.equal((await partialAnswer).status(), 200, "the profile switch itself was meant to apply");
    await partialNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await partialView.context.close();

    console.log("Learn notices browser acceptance passed; all API calls were mocked and no live engine was used.");
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
