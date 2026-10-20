#!/usr/bin/env node
import assert from "node:assert/strict";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const viteEntry = resolve(frontendRoot, "node_modules/vite/bin/vite.js");
const origin = "http://127.0.0.1:4174";
const harnessUrl = `${origin}/tests/acceptance/system-prompts.harness.html`;
const artifactDir = resolve(
  process.env.PROMPTS_ARTIFACT_DIR ?? join(tmpdir(), "system-prompts-browser"),
);
const vite = spawn(
  process.execPath,
  [viteEntry, "--host", "127.0.0.1", "--port", "4174", "--strictPort"],
  { cwd: frontendRoot, stdio: ["ignore", "pipe", "pipe"] },
);
let viteOutput = "";
let spawnError = null;
for (const stream of [vite.stdout, vite.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    viteOutput = `${viteOutput}${chunk}`.slice(-8000);
  });
}
vite.on("error", (error) => {
  spawnError = error;
});

const sleep = (milliseconds) => new Promise((resolvePromise) => {
  setTimeout(resolvePromise, milliseconds);
});

async function waitForHarness() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (spawnError) throw spawnError;
    if (vite.exitCode !== null) {
      throw new Error(`Vite exited before serving the harness.\n${viteOutput}`);
    }
    try {
      const response = await fetch(harnessUrl);
      if (response.ok) return;
    } catch {
      // The dev server is still starting.
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for Vite at ${harnessUrl}.\n${viteOutput}`);
}

async function stopVite() {
  if (vite.exitCode !== null) return;
  const exited = once(vite, "exit").catch(() => undefined);
  vite.kill("SIGTERM");
  await Promise.race([exited, sleep(3000)]);
}


const pageErrors = [];
const consoleErrors = [];

async function openHarness(browser, query = "", width = 1280) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto(`${harnessUrl}${query}`, { waitUntil: "networkidle" });
  return page;
}

await mkdir(artifactDir, { recursive: true });
let browser;
try {
  await waitForHarness();
  browser = await chromium.launch({ headless: true });

  const page = await openHarness(browser, "", 390);
  await page.getByTestId("section-content").waitFor();
  await page.getByText("Project", { exact: true }).waitFor();

  const layouts = [];
  for (const width of [390, 820, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await sleep(100);
    const layout = await page.evaluate(() => {
      const panelIds = ["prompts-sidebar", "prompts-sections", "prompts-editor"];
      const panels = Object.fromEntries(panelIds.map((id) => {
        const element = document.querySelector(`[data-testid="${id}"]`);
        if (!element) throw new Error(`Missing panel: ${id}`);
        const rect = element.getBoundingClientRect();
        return [id, {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        }];
      }));
      return {
        viewport: window.innerWidth,
        documentWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
        editorVisible: Boolean(document.querySelector('[data-testid="section-content"]')),
        panels,
      };
    });
    assert.equal(layout.documentWidth, width, `Horizontal overflow at ${width}px`);
    assert.ok(layout.editorVisible, `Editor missing at ${width}px`);
    if (width === 390) {
      assert.ok(layout.panels["prompts-sections"].y > layout.panels["prompts-sidebar"].y);
      assert.ok(layout.panels["prompts-editor"].y > layout.panels["prompts-sections"].y);
    } else {
      assert.equal(layout.panels["prompts-sections"].y, layout.panels["prompts-sidebar"].y);
      assert.equal(layout.panels["prompts-editor"].y, layout.panels["prompts-sidebar"].y);
    }
    const screenshot = join(artifactDir, `system-prompts-${width}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    layouts.push({ ...layout, screenshot });
  }

  await page.getByRole("button", { name: "Preview" }).click();
  await page.waitForFunction(
    () => document.querySelector('[data-testid="preview-rendered"]')?.textContent?.includes("You are Coder"),
    null,
    { timeout: 5000 },
  );
  await page.getByRole("button", { name: "Back to section" }).click();

  const editor = page.getByTestId("section-content");
  await editor.fill("Saved by the retained browser acceptance test.");
  await page.getByRole("button", { name: "Save override" }).click();
  await page.getByText("Override saved.", { exact: true }).waitFor();
  assert.equal(await editor.inputValue(), "Saved by the retained browser acceptance test.");
  assert.equal(
    await page.evaluate(() => window.__systemPromptsFixture.saved["coder/identity"]),
    "Saved by the retained browser acceptance test.",
  );

  await page.getByRole("button", { name: "Remove override" }).click();
  await page.getByText(
    "Override removed; the shipped section shows through.",
    { exact: true },
  ).waitFor();
  assert.match(await editor.inputValue(), /You are Coder/);
  const removalState = await page.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
    deleted: window.__systemPromptsFixture.deleted,
  }));
  assert.equal(removalState.saved, undefined);
  assert.deepEqual(removalState.deleted, ["coder/identity"]);

  const failurePage = await openHarness(browser, "?fail-delete=1");
  const failureEditor = failurePage.getByTestId("section-content");
  await failureEditor.waitFor();
  await failureEditor.fill("Keep this override when delete fails.");
  await failurePage.getByRole("button", { name: "Save override" }).click();
  await failurePage.getByText("Override saved.", { exact: true }).waitFor();
  await failurePage.getByRole("button", { name: "Remove override" }).click();
  const failureStatus = failurePage.getByTestId("status-message");
  await failureStatus.filter({ hasText: "Simulated DELETE failure: permission denied" }).waitFor();
  assert.ok((await failureStatus.getAttribute("class")).includes("text-destructive"));
  assert.equal(await failureEditor.inputValue(), "Keep this override when delete fails.");
  const failureState = await failurePage.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
    deleted: window.__systemPromptsFixture.deleted,
  }));
  assert.deepEqual(failureState, {
    saved: "Keep this override when delete fails.",
    deleted: [],
  });

  const readOnlyPage = await openHarness(browser);
  await readOnlyPage.locator('[data-testid="section-row"][data-key="hooks"]').click();
  await readOnlyPage.waitForFunction(
    () => document.querySelector('[data-testid="section-content"]')?.value.includes("Hook system snapshot"),
  );
  assert.equal(await readOnlyPage.getByTestId("delete-override").count(), 0);

  const racePage = await openHarness(browser, "?delay-section=skills&delay-ms=400");
  const raceEditor = racePage.getByTestId("section-content");
  await racePage.waitForFunction(
    () => document.querySelector('[data-testid="section-content"]')?.value.includes("You are Coder"),
  );
  await racePage.locator('[data-testid="section-row"][data-key="skills"]').click();
  await racePage.waitForFunction(
    () => document.querySelector('[data-key="skills"]')?.getAttribute("aria-current") === "true",
  );
  await racePage.locator('[data-testid="section-row"][data-key="output-style"]').click();
  await racePage.waitForFunction(
    () => document.querySelector('[data-testid="section-content"]')?.value.includes("Keep each line under"),
  );
  await sleep(450);
  assert.match(await raceEditor.inputValue(), /Keep each line under/);

  const sectionFailurePage = await openHarness(browser, "?fail-section=identity");
  await sectionFailurePage.getByTestId("section-row").first().waitFor();
  const sectionFailureStatus = sectionFailurePage.getByTestId("status-message");
  await sectionFailureStatus.filter({ hasText: "Simulated section load failure: identity" }).waitFor();
  assert.ok((await sectionFailureStatus.getAttribute("class")).includes("text-destructive"));

  const previewFailurePage = await openHarness(browser, "?fail-preview=1");
  await previewFailurePage.getByTestId("section-content").waitFor();
  await previewFailurePage.getByRole("button", { name: "Preview" }).click();
  const previewFailureStatus = previewFailurePage.getByTestId("status-message");
  await previewFailureStatus.filter({ hasText: "Simulated preview failure: engine unavailable" }).waitFor();
  assert.equal(await previewFailurePage.getByTestId("preview-rendered").count(), 0);
  assert.ok((await previewFailureStatus.getAttribute("class")).includes("text-destructive"));

  const saveRefreshPage = await openHarness(browser, "?fail-refresh=save");
  const saveRefreshEditor = saveRefreshPage.getByTestId("section-content");
  await saveRefreshEditor.waitFor();
  await saveRefreshEditor.fill("Save succeeded before refresh failed.");
  await saveRefreshPage.getByRole("button", { name: "Save override" }).click();
  const saveRefreshStatus = saveRefreshPage.getByTestId("status-message");
  await saveRefreshStatus.filter({ hasText: "Override saved, but refresh failed:" }).waitFor();
  assert.match(await saveRefreshStatus.innerText(), /Simulated section refresh failure: save/);
  assert.equal(
    await saveRefreshPage.evaluate(() => window.__systemPromptsFixture.saved["coder/identity"]),
    "Save succeeded before refresh failed.",
  );

  const removeRefreshPage = await openHarness(browser, "?fail-refresh=remove");
  const removeRefreshEditor = removeRefreshPage.getByTestId("section-content");
  await removeRefreshEditor.waitFor();
  await removeRefreshEditor.fill("Override is removed before refresh fails.");
  await removeRefreshPage.getByRole("button", { name: "Save override" }).click();
  await removeRefreshPage.getByText("Override saved.", { exact: true }).waitFor();
  await removeRefreshPage.getByRole("button", { name: "Remove override" }).click();
  const removeRefreshStatus = removeRefreshPage.getByTestId("status-message");
  await removeRefreshStatus.filter({ hasText: "Override removed, but refresh failed:" }).waitFor();
  assert.equal(await removeRefreshPage.getByTestId("delete-override").count(), 0);
  assert.equal(await removeRefreshEditor.inputValue(), "");
  const removeRefreshState = await removeRefreshPage.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
    deleted: window.__systemPromptsFixture.deleted,
  }));
  assert.equal(removeRefreshState.saved, undefined);
  assert.deepEqual(removeRefreshState.deleted, ["coder/identity"]);


  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  console.log(JSON.stringify({
    api: "fixtureTransport mock; no engine contacted",
    layouts,
    successfulSaveAndRemove: removalState,
    deleteFailure: failureState,
    errorAndRaceScenarios: "passed",
    pageErrors,
    consoleErrors,
  }, null, 2));
} finally {
  await browser?.close();
  await stopVite();
}
