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
