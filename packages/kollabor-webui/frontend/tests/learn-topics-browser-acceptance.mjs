// The 11-topic tour, walkthrough video behavior, media recovery and the
// unknown-topic route, split out of the learn shell suite so no browser suite
// outruns one CI worker wave (#1022). Uses the real App and a synthetic engine
// API; it never contacts a real engine.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite, waitForLesson } from "./learn-browser-acceptance.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn topics browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();
    const fixtures = makeFixtures();
    const view = await openPage(browser, baseUrl, fixtures);
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });

    // Every current topic must load its JSON-backed workflow, not a blank placeholder.
    await view.page.emulateMedia({ reducedMotion: "reduce" });

    for (const topicId of ["networks", "devices", "workspaces", "agents", "profiles", "context-compaction", "koordinator", "hub-messaging", "voice", "system-prompts", "phone-access"]) {
      await view.page.goto(`${baseUrl}/learn?topic=${topicId}`, { waitUntil: "domcontentloaded" });
      const learn = view.page.getByRole("main", { name: "Learn", exact: true });
      const heading = learn.getByRole("heading", { level: 1 });
      await heading.waitFor({ state: "visible" });
      assert.notEqual(await heading.innerText(), "That topic is not available", `${topicId} route unexpectedly unresolved`);
      await view.page.getByRole("tabpanel").first().waitFor({ state: "visible" });
      assert.ok((await view.page.getByRole("tabpanel").first().innerText()).trim().length > 0, `${topicId} workflow is empty`);
      const topicMedia = learn.locator("img, video").first();
      await topicMedia.waitFor({ state: "visible" });
      if (await topicMedia.evaluate((element) => element instanceof HTMLImageElement)) {
        await view.page.waitForFunction((id) => {
          const image = document.querySelector('main[aria-label="Learn"] img');
          return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0 && image.src.endsWith(`/learn/${id}/overview.webp`);
        }, topicId);
      }

      const workflowTabs = learn.getByRole("tab");
      if (await workflowTabs.count() > 1) {
        await workflowTabs.first().focus();
        await view.page.keyboard.press("ArrowRight");
        await view.page.waitForFunction(() => document.querySelectorAll('main[aria-label="Learn"] [role="tab"]')[1]?.getAttribute("aria-selected") === "true");
        assert.equal(await workflowTabs.nth(1).getAttribute("aria-selected"), "true", `${topicId} workflow tabs are not keyboard navigable`);
      }
      const reducedMotion = await view.page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      const video = view.page.locator("main video").first();
      if (reducedMotion && await video.count()) {
        assert.equal(await video.getAttribute("autoplay"), null, `${topicId} video must not autoplay`);
        assert.equal(await video.evaluate((element) => element.paused), true, `${topicId} video unexpectedly plays on load`);
      }
    }
    await view.page.emulateMedia({ reducedMotion: "no-preference" });

    // The walkthrough starts from a readable poster; reduced motion waits for explicit play.
    await view.page.emulateMedia({ reducedMotion: "reduce" });
    await view.page.goto(`${baseUrl}/learn?topic=networks`, { waitUntil: "domcontentloaded" });
    await waitForLesson(view.page, "Connect");
    // Escape pressed as soon as the enlarged preview opens closes only that preview, never the Learn dialog.
    await view.page.getByRole("button", { name: "Enlarge product preview", exact: true }).click();
    await view.page.getByRole("dialog", { name: "Product preview", exact: true }).waitFor();
    await view.page.keyboard.press("Escape");
    await view.page.getByRole("dialog", { name: "Product preview", exact: true }).waitFor({ state: "hidden" });
    assert.equal(new URL(view.page.url()).pathname, "/learn", "Escape on the enlarged preview also closed Learn");
    await waitForLesson(view.page, "Connect");
    assert.equal(await view.page.locator("main video").count(), 0);
    await view.page.getByRole("button", { name: "Watch walkthrough", exact: true }).click();
    const walkthrough = view.page.locator("main video");
    await walkthrough.waitFor();
    await walkthrough.evaluate(async (video) => {
      if (!video.readyState) await new Promise((resolve, reject) => { video.onloadedmetadata = resolve; video.onerror = reject; });
    });
    assert.equal(await walkthrough.evaluate((video) => video.paused && video.controls && video.playsInline), true);
    assert.equal(await walkthrough.getAttribute("autoplay"), null);
    assert.equal(await walkthrough.locator('track[kind="captions"]').count(), 1);
    await walkthrough.evaluate((video) => video.play());
    await view.page.waitForFunction(() => document.querySelector("main video")?.currentTime > 0.2);
    await view.page.emulateMedia({ reducedMotion: "no-preference" });
    await view.page.emulateMedia({ reducedMotion: "reduce" });
    await view.page.waitForFunction(() => document.querySelector("main video")?.paused);
    await view.page.getByRole("button", { name: "Show preview", exact: true }).click();
    assert.equal(await view.page.locator("main video").count(), 0);
    await view.page.getByRole("button", { name: "Enlarge product preview", exact: true }).click();
    const enlargedPreview = view.page.getByRole("dialog", { name: "Product preview", exact: true });
    await enlargedPreview.waitFor();
    await enlargedPreview.locator("img").evaluate((image) => image.decode());
    await view.page.keyboard.press("Escape");
    await enlargedPreview.waitFor({ state: "hidden" });
    await waitForLesson(view.page, "Connect");
    await view.page.emulateMedia({ reducedMotion: "no-preference" });

    // A failed overview image has an actionable recovery and a real retry re-requests the asset.
    const mediaRecovery = await openPage(browser, baseUrl, makeFixtures());
    let failOverviewOnce = true;
    await mediaRecovery.page.route("**/learn/devices/overview.webp", async (route) => {
      if (failOverviewOnce) {
        failOverviewOnce = false;
        await route.abort();
      } else {
        await route.continue();
      }
    });
    await mediaRecovery.page.goto(`${baseUrl}/learn?topic=devices`, { waitUntil: "domcontentloaded" });
    await waitForLesson(mediaRecovery.page, "Devices");
    const mediaError = mediaRecovery.page.getByRole("group", { name: "Lesson media recovery" });
    await mediaError.getByRole("status").waitFor({ state: "visible" });
    await mediaError.getByRole("button", { name: "Try again" }).click();
    const restoredImage = mediaRecovery.page.locator('main img[src="/learn/devices/overview.webp"]');
    await restoredImage.waitFor({ state: "visible" });
    await mediaRecovery.page.waitForFunction(() => {
      const image = document.querySelector('main img[src="/learn/devices/overview.webp"]');
      return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0;
    });
    await mediaRecovery.context.close();


    await view.page.goto(`${baseUrl}/learn?topic=not-a-topic`, { waitUntil: "domcontentloaded" });
    await view.page.getByRole("heading", { name: "That topic is not available" }).waitFor({ state: "visible" });
    await view.page.getByRole("button", { name: "Browse topics" }).click();
    await view.page.waitForFunction(() => location.search === "?topic=networks");
    await view.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await view.page.waitForFunction(() => location.pathname === "/");

    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.consoleErrors, []);
    assert.deepEqual(view.externalRequests, []);
    await view.context.close();
    console.log("Learn topics browser acceptance passed; all API calls were mocked and no live engine was used.");
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
