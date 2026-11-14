// Synthetic file responses; no workspace is modified.
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const vite = await createServer({ root, configFile: path.join(root, "vite.config.ts"), logLevel: "error",
  cacheDir: mkdtempSync(path.join(tmpdir(), "kollab-overwrite-vite-")),
  server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await vite.listen();
  const address = vite.httpServer.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH ?? chromium.executablePath() });
  for (const width of [1280, 820, 390]) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  const writes = [];
  const errors = [];
  let delayNext = false;
  let delayedStatus = 409;
  let releaseDelayed;
  let signalDelayed;
  let delayedStarted = new Promise((resolve) => { signalDelayed = resolve; });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: origin } });
    if (url.pathname === "/sessions/fixture/code/fs/file") {
      if (route.request().method() === "GET") return route.fulfill({ json: { success: true, data: {
        content: "external", version: "external-v", fileIdentity: "c".repeat(64), rootIdentity: "b".repeat(64),
      } } });
      const body = route.request().postDataJSON();
      const filePath = url.searchParams.get("path");
      writes.push({ path: filePath, force: body.force === true });
      if (body.force && body.expectedFileIdentity !== undefined) {
        assert.equal(body.expectedFileIdentity, "c".repeat(64));
        assert.equal(body.expectedConfirmedVersion, "external-v");
      }
      if (delayNext) {
        delayNext = false;
        await new Promise((resolve) => { releaseDelayed = resolve; signalDelayed(); });
        if (delayedStatus === 502) return route.fulfill({ status: 502, json: { success: false,
          error: { code: "daemon_unreachable", message: "No reply." } } });
      }
      return body.force
        ? route.fulfill({ json: { success: true, data: { version: "saved", fileIdentity: "c".repeat(64),
          rootIdentity: "b".repeat(64) } } })
        : route.fulfill({ status: 409, json: { success: false, error: {
          code: "stale_write", message: "Changed on disk",
        } } });
    }
    return route.continue();
  });
  await page.goto(`${origin}/tests/acceptance/overwrite-target.html`);
  await page.locator(".monaco-editor textarea").first().waitFor({ timeout: 30000 });
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor();
  const warningBox = await page.getByText("Disk changed; overwrite replaces its contents.").boundingBox();
  const overwriteBox = await page.getByRole("button", { name: "Overwrite", exact: true }).boundingBox();
  assert.ok(warningBox && warningBox.x >= 0 && warningBox.x + warningBox.width <= width);
  assert.ok(overwriteBox && overwriteBox.x >= 0 && overwriteBox.x + overwriteBox.width <= width);
  // A successful save through another control invalidates the old confirmation.
  await page.evaluate(async () => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    const store = useEditorStore.getState();
    store.markSaved("/fixture/a.txt", "a draft");
    store.updateContent("/fixture/a.txt", "a revised draft");
  });
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor({ state: "hidden" });
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor();
  const switchTo = (name) => page.evaluate(async (path) => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    const store = useEditorStore.getState();
    store.setActiveFile(store.activePaneId, path);
  }, `/fixture/${name}.txt`);
  await switchTo("b");
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor({ state: "hidden" });
  await switchTo("a");
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor();
  await page.evaluate(async () => {
    const { setEditorOwner } = await import("/src/components/code/workspace-tabs.ts");
    setEditorOwner("other:/workspace", "other", "other-device");
    setEditorOwner("fixture:/fixture", "fixture", "fixture-device");
  });
  await switchTo("b");
  await switchTo("a");
  await page.getByText("The file changed on disk. Save again before overwriting.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Overwrite", exact: true }).count(), 0);
  await switchTo("b");
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Overwrite", exact: true }).click();
  await page.waitForFunction(async () => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    const file = useEditorStore.getState().fileCache.get("/fixture/b.txt");
    return file?.savedContent === "b draft";
  });
  await switchTo("a");
  delayNext = true;
  await page.keyboard.press("Control+s");
  await delayedStarted;
  await switchTo("b");
  await page.evaluate(async () => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    const store = useEditorStore.getState();
    store.markSaved("/fixture/a.txt", "a revised draft");
    store.updateContent("/fixture/a.txt", "a later draft");
  });
  const delayedResponse = page.waitForResponse((response) => response.url().includes("/code/fs/file") && response.status() === 409);
  releaseDelayed();
  await delayedResponse;
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole("button", { name: "Overwrite", exact: true }).count(), 0);
  await switchTo("a");
  assert.equal(await page.getByRole("button", { name: "Overwrite", exact: true }).count(), 0);
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Overwrite", exact: true }).waitFor();
  await page.getByText("Disk changed; overwrite replaces its contents.").click();
  delayedStarted = new Promise((resolve) => { signalDelayed = resolve; });
  delayedStatus = 502;
  delayNext = true;
  await page.keyboard.press("Control+s");
  await delayedStarted;
  await page.evaluate(async () => {
    const { setEditorOwner } = await import("/src/components/code/workspace-tabs.ts");
    const { unmountPane } = await import("/tests/acceptance/overwrite-target.fixture.tsx");
    setEditorOwner("other:/workspace", "other", "other-device");
    unmountPane();
  });
  const unknownResponse = page.waitForResponse((response) => response.url().includes("/code/fs/file") && response.status() === 502);
  releaseDelayed();
  await unknownResponse;
  await page.waitForFunction(async () => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    return Boolean(useEditorStore.getState().fileCache.get("/fixture/a.txt")?.saveNotice);
  });
  await page.evaluate(async () => {
    const { setEditorOwner } = await import("/src/components/code/workspace-tabs.ts");
    const { mountPane } = await import("/tests/acceptance/overwrite-target.fixture.tsx");
    setEditorOwner("fixture:/fixture", "fixture", "fixture-device");
    mountPane();
  });
  const uncertainty = page.getByRole("button", { name: /The save may have completed while the workspace changed; check the file before retrying/ });
  await uncertainty.waitFor();
  const uncertaintyBox = await uncertainty.boundingBox();
  assert.ok(uncertaintyBox && uncertaintyBox.x >= 0 && uncertaintyBox.x + uncertaintyBox.width <= width);
  assert.deepEqual(writes, [{ path: "/fixture/a.txt", force: false }, { path: "/fixture/a.txt", force: false },
    { path: "/fixture/b.txt", force: false }, { path: "/fixture/b.txt", force: true },
    { path: "/fixture/a.txt", force: false }, { path: "/fixture/a.txt", force: false },
    { path: "/fixture/a.txt", force: false }]);
  assert.deepEqual(errors, []);
  // Repeat the uncertain outcome through the tab save control, then change owner and unmount.
  await page.reload();
  await page.locator(".monaco-editor textarea").first().waitFor({ timeout: 30000 });
  delayedStarted = new Promise((resolve) => { signalDelayed = resolve; });
  delayedStatus = 502;
  delayNext = true;
  await page.locator('button[aria-label="Save file"]').first().click({ force: true });
  await delayedStarted;
  await page.evaluate(async () => {
    const { setEditorOwner } = await import("/src/components/code/workspace-tabs.ts");
    const { unmountPane } = await import("/tests/acceptance/overwrite-target.fixture.tsx");
    setEditorOwner("other:/workspace", "other", "other-device");
    unmountPane();
  });
  const tabResponse = page.waitForResponse((response) => response.url().includes("/code/fs/file") && response.status() === 502);
  releaseDelayed();
  await tabResponse;
  await page.waitForTimeout(200);
  await page.evaluate(async () => {
    const { setEditorOwner } = await import("/src/components/code/workspace-tabs.ts");
    const { mountPane } = await import("/tests/acceptance/overwrite-target.fixture.tsx");
    setEditorOwner("fixture:/fixture", "fixture", "fixture-device");
    mountPane();
  });
  const tabNotice = page.getByRole("button", { name: /The save may have completed while the workspace changed; check the file before retrying/ });
  await tabNotice.waitFor({timeout: 3000});
  await tabNotice.click();
  await tabNotice.waitFor({ state: "hidden" });
  assert.equal(await page.getByRole("alert").filter({ hasText: "save may have completed" }).count(), 0);
  // Editor and tab saves of the same path must not send overlapping writes.
  await page.reload();
  await page.locator(".monaco-editor textarea").first().waitFor({ timeout: 30000 });
  delayedStarted = new Promise((resolve) => { signalDelayed = resolve; });
  delayedStatus = 502;
  delayNext = true;
  const beforeOverlap = writes.length;
  await page.keyboard.press("Control+s");
  await delayedStarted;
  await page.locator('button[aria-label="Save file"]').first().click({ force: true });
  await page.waitForTimeout(150);
  assert.equal(writes.length, beforeOverlap + 1);
  const pendingResponse = page.waitForResponse((response) => response.url().includes("/code/fs/file") && response.status() === 502);
  releaseDelayed();
  await pendingResponse;
  await page.getByText("No reply. The save may have completed; check the file before retrying.").waitFor();
  await page.close();
  console.log(`overwrite stays bound to the conflicted buffer at ${width}px`);
  }
} finally {
  if (browser) await browser.close();
  await vite.close();
}
