// Default-loadout regressions split out of the learn tour so the browser
// suites can run in parallel instead of one 6-minute script (#1022).
// Covers New Session starting on the folder's default loadout (#957) and the
// per-folder default answer races from the clean-room review of #961.
// Uses the real App and a synthetic engine API; it never contacts a real engine.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite, until } from "./learn-browser-acceptance.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn loadouts browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

    // New Session starts on the default loadout, not on the active profile's own saved model (#957).
    const defaultLoadout = { name: "haiku-low", profile: "review", provider: "anthropic", model: "claude-haiku", effort: "low" };
    const onDefault = makeFixtures({ defaultLoadout });
    const defaultView = await openPage(browser, baseUrl, onDefault);
    await defaultView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await defaultView.page.getByTestId("new-session-options").click();
    // The active profile is "default" (gpt-6-luna); the form shows the loadout before Start.
    await defaultView.page.locator("#new-session-model", { hasText: "claude-haiku" }).waitFor({ state: "visible", timeout: 5000 });
    assert.doesNotMatch(await defaultView.page.locator("#new-session-model").innerText(), /gpt-6-luna/);
    const switchAnswered = defaultView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await defaultView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const defaultPayload = await Promise.race([
      onDefault.createRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Default-loadout launch request did not arrive.")), 15000)),
    ]);
    assert.equal(defaultPayload.profile, "review");
    const switches = await until(() => onDefault.profileSwitches().length && onDefault.profileSwitches(), "The loadout's model never reached the session.");
    assert.deepEqual(switches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    // The session adopted the pick: the engine's answer to the switch carries the loadout's model and level.
    // The app compares that answer with the pick and flags a session that kept something else.
    const answered = await (await switchAnswered).json();
    assert.equal(answered.model, "claude-haiku");
    assert.equal(answered.effort, "low");
    await defaultView.context.close();

    // A default loadout on a profile that no longer exists leaves the form on the active profile.
    const staleFixtures = makeFixtures({ defaultLoadout: { ...defaultLoadout, profile: "deleted" } });
    const stale = await openPage(browser, baseUrl, staleFixtures);
    await stale.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await stale.page.getByTestId("new-session-options").click();
    await stale.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    await stale.context.close();

    // A default loadout belongs to the folder a session starts in: the engine's folder may set one and another
    // folder its own. The form follows the folder picked, a deliberate model pick stays, and Start Session never
    // outruns the answer (the clean-room review of #961, round 3).
    const demoDefault = { name: "luna-max", profile: "default", provider: "openai_responses", model: "gpt-6-luna", effort: "max" };
    const otherFolderFixtures = (byFolder) =>
      makeFixtures({ defaultLoadoutsByFolder: byFolder, recentFolders: ["/learn-other"] });
    const chooseOtherFolder = async (page) => {
      await page.locator("#new-session-workspace").click();
      await page.locator('button[title="/learn-other"]').click();
    };
    const launchPayload = (fixture, what) =>
      Promise.race([
        fixture.createRequest,
        new Promise((_, reject) => setTimeout(() => reject(new Error(`${what} launch request did not arrive.`)), 15000)),
      ]);

    const perFolder = otherFolderFixtures({ "": demoDefault, "/learn-other": defaultLoadout });
    const folderView = await openPage(browser, baseUrl, perFolder);
    await folderView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await folderView.page.getByTestId("new-session-options").click();
    await folderView.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    await chooseOtherFolder(folderView.page);
    // The other folder sets claude-haiku: the form shows it, not the engine folder's gpt-6-luna.
    await folderView.page.locator("#new-session-model", { hasText: "claude-haiku" }).waitFor({ state: "visible", timeout: 5000 });
    assert.doesNotMatch(await folderView.page.locator("#new-session-model").innerText(), /gpt-6-luna/);
    assert.ok(perFolder.profileListings().includes("/learn-other"), "the other folder's default was never asked for");
    await folderView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const folderPayload = await launchPayload(perFolder, "Other-folder");
    assert.equal(folderPayload.workspace, "/learn-other");
    assert.equal(folderPayload.profile, "review");
    const folderSwitches = await until(() => perFolder.profileSwitches().length && perFolder.profileSwitches(), "The other folder's model never reached the session.");
    assert.deepEqual(folderSwitches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    await folderView.context.close();

    // A model picked by hand is not undone by a change of folder.
    const keptPick = otherFolderFixtures({ "/learn-other": demoDefault });
    const keptView = await openPage(browser, baseUrl, keptPick);
    await keptView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await keptView.page.getByTestId("new-session-options").click();
    await keptView.page.locator("#new-session-model").click();
    await keptView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await chooseOtherFolder(keptView.page);
    await keptView.page.waitForTimeout(500); // an answer that replaced the pick would be drawn by now
    assert.match(await keptView.page.locator("#new-session-model").innerText(), /claude-haiku/);
    await keptView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const keptPayload = await launchPayload(keptPick, "Kept-pick");
    assert.equal(keptPayload.workspace, "/learn-other");
    assert.equal(keptPayload.profile, "review");
    await keptView.context.close();

    // Start Session pressed before the new folder's default has arrived still starts on that default.
    const racing = otherFolderFixtures({ "": demoDefault, "/learn-other": defaultLoadout });
    const racingView = await openPage(browser, baseUrl, racing);
    await racingView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await racingView.page.getByTestId("new-session-options").click();
    await racingView.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    racing.holdFolderDefaults();
    await chooseOtherFolder(racingView.page);
    await racingView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    await racingView.page.waitForTimeout(300);
    assert.equal(racing.createPayload(), null, "Start Session outran the other folder's default");
    racing.releaseFolderDefaults();
    const racingPayload = await launchPayload(racing, "Racing");
    assert.equal(racingPayload.workspace, "/learn-other");
    assert.equal(racingPayload.profile, "review");
    const racingSwitches = await until(() => racing.profileSwitches().length && racing.profileSwitches(), "The other folder's model never reached the session.");
    assert.deepEqual(racingSwitches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    await racingView.context.close();

    console.log("Learn loadouts browser acceptance passed; all API calls were mocked and no live engine was used.");
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
