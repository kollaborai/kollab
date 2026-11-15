// The dismissed-retry and hand-made model change scenarios from the
// clean-room reviews of #959, split out of the notice suite so both stay under
// one CI worker wave (#1022). Uses the real App and a synthetic engine API.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn retries browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

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

    console.log("Learn retries browser acceptance passed; all API calls were mocked and no live engine was used.");
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
