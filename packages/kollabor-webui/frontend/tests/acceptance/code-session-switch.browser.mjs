// Uses synthetic session responses and never contacts an agent.
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const vite = await createServer({ root, configFile: path.join(root, "vite.config.ts"), logLevel: "error",
  server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await vite.listen();
  const address = vite.httpServer.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH ?? chromium.executablePath() });
  const page = await browser.newPage();
  let releaseB;
  const bGate = new Promise((resolve) => { releaseB = resolve; });
  let releaseSave;
  const saveGate = new Promise((resolve) => { releaseSave = resolve; });
  let bPending = false;
  let savePending = false;
  const requests = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: origin } });
    if (url.pathname.startsWith("/sessions/")) {
      requests.push({ path: url.pathname, root: url.searchParams.get("codeRoot") });
      const session = url.pathname.split("/")[2];
      if (url.pathname.endsWith("/code/folders")) {
        if (session === "session-c") return route.fulfill({ status: 503, json: { success: false } });
        if (session === "session-b") { bPending = true; await bGate; }
        const folder = session === "session-a" ? "/a" : "/b";
        return route.fulfill({ json: { success: true, data: { default: folder, folders: [{ path: folder, branch: "" }] } } });
      }
      if (session === "session-a" && url.pathname.endsWith("/fs/file") && route.request().method() === "PUT") {
        savePending = true;
        await saveGate;
        return route.fulfill({ json: { success: true, data: { version: "old-session-result" } } });
      }
      if (url.pathname.endsWith("/fs/tree")) return route.fulfill({ json: { success: true, data: { path: "/", tree: [] } } });
      return route.fulfill({ json: { success: true, data: { status: {}, entries: [] } } });
    }
    return route.continue();
  });
  await page.goto(`${origin}/tests/acceptance/code-session-switch.html`);
  await page.getByLabel("Code and Git folder").waitFor();
  assert.equal(await page.getByLabel("Code and Git folder").inputValue(), "/a");
  await page.evaluate(async () => {
    const { saveWorkspaceFile } = await import("/src/components/code/file-save.ts");
    window.pendingFixtureSave = saveWorkspaceFile("/a", "/a/file.txt", "draft");
  });
  await page.waitForTimeout(100);
  assert.equal(savePending, true);
  const epoch = await page.evaluate(async () => (await import("/src/components/code/workspace-tabs.ts")).editorOwnerEpoch());
  await page.getByRole("button", { name: "Switch session" }).click();
  await page.waitForFunction(() => document.body.textContent?.includes("Loading folders…"));
  await page.waitForTimeout(100);
  assert.equal(bPending, true);
  releaseSave();
  const oldSave = await page.evaluate(() => window.pendingFixtureSave);
  assert.equal(oldSave.ok, false);
  const newEpoch = await page.evaluate(async () => (await import("/src/components/code/workspace-tabs.ts")).editorOwnerEpoch());
  assert.ok(newEpoch > epoch);
  assert.equal(requests.some((request) => request.path.startsWith("/sessions/session-b/code/fs/") && request.root === "/a"), false);
  releaseB();
  await page.getByLabel("Code and Git folder").waitFor();
  assert.equal(await page.getByLabel("Code and Git folder").inputValue(), "/b");
  const bEpoch = await page.evaluate(async () => (await import("/src/components/code/workspace-tabs.ts")).editorOwnerEpoch());
  await page.getByRole("button", { name: "Switch to unavailable session" }).click();
  await page.getByRole("alert").waitFor();
  const failedEpoch = await page.evaluate(async () => (await import("/src/components/code/workspace-tabs.ts")).editorOwnerEpoch());
  assert.ok(failedEpoch > bEpoch);
  await page.getByRole("button", { name: "Leave editor" }).click();
  const unmountedEpoch = await page.evaluate(async () => (await import("/src/components/code/workspace-tabs.ts")).editorOwnerEpoch());
  assert.ok(unmountedEpoch > failedEpoch);
  console.log("session switch waits for the new agent folder before mounting Code");
} finally {
  if (browser) await browser.close();
  await vite.close();
}
