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
    const screenshot = join(artifactDir, `system-prompts-mocked-${width}.png`);
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

  const bundleRows = page.locator('[data-testid="prompt-row"]');
  assert.ok(
    (await bundleRows.count()) > 1,
    "fixture should include multiple prompt bundles",
  );
  const lastBundleRow = bundleRows.last();
  await lastBundleRow.focus();
  await page.keyboard.press("Shift+Tab");
  const focusedBundleRow = page.locator(":focus");
  assert.equal(await focusedBundleRow.getAttribute("data-testid"), "prompt-row");
  assert.equal(
    await focusedBundleRow.evaluate((element) => element.matches(":focus-visible")),
    true,
  );
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const focusedSectionRow = page.locator(":focus");
  assert.equal(await focusedSectionRow.getAttribute("data-testid"), "section-row");
  assert.equal(
    await focusedSectionRow.evaluate((element) => element.matches(":focus-visible")),
    true,
  );
  const editor = page.getByRole("textbox", { name: "Identity" });
  await editor.waitFor();
  await page.locator('[data-testid="section-row"]').last().focus();
  for (let tab = 0; tab < 5; tab += 1) {
    if (await editor.evaluate((element) => element === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  assert.equal(
    await editor.evaluate((element) => element === document.activeElement),
    true,
    "Keyboard tab order should reach the labeled prompt editor",
  );
  assert.equal(
    await editor.evaluate((element) => element.matches(":focus-visible")),
    true,
  );
  assert.equal(await editor.getAttribute("aria-labelledby"), "prompt-editor-title");

  const mutationPage = await openHarness(browser, "?delay-save=1&delay-remove=1");
  const mutationEditor = mutationPage.getByRole("textbox", { name: "Identity" });
  const mutationStatus = mutationPage.getByTestId("status-message");
  await mutationEditor.waitFor();
  await mutationEditor.fill("Saved by the retained browser acceptance test.");
  await mutationPage.getByRole("button", { name: "Save override" }).click();
  await mutationStatus.filter({ hasText: "Saving override…" }).waitFor();
  await mutationPage.getByText("Override saved.", { exact: true }).waitFor();
  assert.equal(await mutationEditor.inputValue(), "Saved by the retained browser acceptance test.");
  assert.equal(
    await mutationPage.evaluate(() => window.__systemPromptsFixture.saved["coder/identity"]),
    "Saved by the retained browser acceptance test.",
  );

  await mutationEditor.fill("Unsaved draft that should require confirmation.");
  await mutationPage.getByTestId("delete-override").click();
  const cancelDialog = mutationPage.getByRole("alertdialog");
  await cancelDialog
    .getByRole("heading", { name: "Remove this prompt override?" })
    .waitFor();
  await cancelDialog.getByText(/Your saved override and any unsaved edits/).waitFor();
  await cancelDialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepEqual(
    await mutationPage.evaluate(() => window.__systemPromptsFixture.deleted),
    [],
  );
  assert.equal(
    await mutationPage.evaluate(() => window.__systemPromptsFixture.saved["coder/identity"]),
    "Saved by the retained browser acceptance test.",
  );
  assert.equal(
    await mutationEditor.inputValue(),
    "Unsaved draft that should require confirmation.",
  );

  await mutationPage.getByTestId("delete-override").click();
  const confirmDialog = mutationPage.getByRole("alertdialog");
  await confirmDialog.getByRole("button", { name: "Remove override" }).click();
  await mutationStatus.filter({ hasText: "Removing override…" }).waitFor();
  await mutationPage.getByText(
    "Override removed; the shipped section shows through.",
    { exact: true },
  ).waitFor();
  assert.match(await mutationEditor.inputValue(), /You are Coder/);
  const removalState = await mutationPage.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
    deleted: window.__systemPromptsFixture.deleted,
  }));
  assert.equal(removalState.saved, undefined);
  assert.deepEqual(removalState.deleted, ["coder/identity"]);

  const saveFailurePage = await openHarness(browser, "?delay-save=1&fail-save=1");
  const saveFailureEditor = saveFailurePage.getByTestId("section-content");
  await saveFailureEditor.waitFor();
  await saveFailureEditor.fill("Draft retained after delayed save failure.");
  await saveFailurePage.getByRole("button", { name: "Save override" }).click();
  const saveFailureStatus = saveFailurePage.getByTestId("status-message");
  await saveFailureStatus.filter({ hasText: "Saving override…" }).waitFor();
  const pendingSaveSection = saveFailurePage.locator(
    '[data-testid="section-row"][data-key="skills"]',
  );
  const pendingSaveBundle = saveFailurePage.locator('[data-testid="prompt-row"]').nth(1);
  assert.equal(await pendingSaveSection.isDisabled(), true);
  assert.equal(await pendingSaveBundle.isDisabled(), true);
  await pendingSaveSection.dispatchEvent("click");
  await pendingSaveBundle.dispatchEvent("click");
  assert.equal(
    await saveFailurePage.locator('[data-testid="section-row"][data-key="identity"]').getAttribute("aria-current"),
    "true",
  );
  assert.equal(
    await saveFailurePage.locator('[data-testid="prompt-row"][data-bundle="coder"]').getAttribute("aria-current"),
    "true",
  );
  await saveFailureStatus.filter({
    hasText: "Simulated PUT failure: permission denied",
  }).waitFor();
  assert.ok((await saveFailureStatus.getAttribute("class")).includes("text-destructive"));
  assert.equal(await saveFailureEditor.inputValue(), "Draft retained after delayed save failure.");
  const saveFailureState = await saveFailurePage.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
  }));
  assert.deepEqual(saveFailureState, { saved: undefined });

  const failurePage = await openHarness(browser, "?fail-delete=1&delay-remove=1");
  const failureEditor = failurePage.getByTestId("section-content");
  await failureEditor.waitFor();
  await failureEditor.fill("Saved override for delayed delete failure.");
  await failurePage.getByRole("button", { name: "Save override" }).click();
  await failurePage.getByText("Override saved.", { exact: true }).waitFor();
  await failureEditor.fill("Draft retained after delayed delete failure.");
  await failurePage.getByTestId("delete-override").click();
  const failureDialog = failurePage.getByRole("alertdialog");
  await failureDialog.getByRole("button", { name: "Remove override" }).click();
  const failureStatus = failurePage.getByTestId("status-message");
  await failureStatus.filter({ hasText: "Removing override…" }).waitFor();
  const pendingDeleteSection = failurePage.locator(
    '[data-testid="section-row"][data-key="skills"]',
  );
  const pendingDeleteBundle = failurePage.locator('[data-testid="prompt-row"]').nth(1);
  assert.equal(await pendingDeleteSection.isDisabled(), true);
  assert.equal(await pendingDeleteBundle.isDisabled(), true);
  await pendingDeleteSection.dispatchEvent("click");
  await pendingDeleteBundle.dispatchEvent("click");
  assert.equal(
    await failurePage.locator('[data-testid="section-row"][data-key="identity"]').getAttribute("aria-current"),
    "true",
  );
  assert.equal(
    await failurePage.locator('[data-testid="prompt-row"][data-bundle="coder"]').getAttribute("aria-current"),
    "true",
  );
  await failureStatus.filter({ hasText: "Simulated DELETE failure: permission denied" }).waitFor();
  assert.ok((await failureStatus.getAttribute("class")).includes("text-destructive"));
  assert.equal(await failureEditor.inputValue(), "Draft retained after delayed delete failure.");
  const failureState = await failurePage.evaluate(() => ({
    saved: window.__systemPromptsFixture.saved["coder/identity"],
    deleted: window.__systemPromptsFixture.deleted,
  }));
  assert.deepEqual(failureState, {
    saved: "Saved override for delayed delete failure.",
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
  await removeRefreshPage.getByTestId("delete-override").click();
  await removeRefreshPage
    .getByRole("alertdialog")
    .getByRole("button", { name: "Remove override" })
    .click();
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
    saveFailure: saveFailureState,
    deleteFailure: failureState,
    errorAndRaceScenarios: "passed",
    pageErrors,
    consoleErrors,
  }, null, 2));
} finally {
  await browser?.close();
  await stopVite();
}
