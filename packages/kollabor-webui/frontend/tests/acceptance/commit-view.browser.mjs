// Synthetic Git responses; no repository or file is modified.
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
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: origin } });
    if (url.pathname === "/sessions/fixture/code/git") {
      const request = route.request().postDataJSON();
      const data = request.action === "status" ? { branch: "main", upstream: null, ahead: 0, behind: 0, files: [] }
        : request.action === "list_branches" ? { current: "main", branches: [] }
        : request.action === "log" ? { entries: [{ hash: "abcd1234", shortHash: "abcd", message: "commit fixture",
          author: "Test", date: "today", refs: "" }] }
        : request.action === "show_commit" ? { content: "fixture patch" } : {};
      return route.fulfill({ json: { success: true, data } });
    }
    if (url.pathname.includes("/code/fs/")) throw new Error("unexpected file request");
    return route.continue();
  });
  await page.goto(`${origin}/tests/acceptance/commit-view.html`);
  await page.getByRole("button", { name: "Log", exact: true }).click();
  await page.getByTitle("Open commit diff").click();
  await page.locator(".monaco-editor textarea").first().waitFor({ timeout: 30000 });
  await page.locator(".monaco-editor textarea").first().focus();
  await page.keyboard.type("should-not-appear");
  const state = await page.evaluate(async () => {
    const { useEditorStore } = await import("/src/components/code/editor-store.ts");
    const cache = useEditorStore.getState().fileCache;
    return { real: cache.get("/fixture/.diffs/abcd.diff")?.content,
      view: cache.get("\0commit:abcd1234"), layout: localStorage.getItem("kollab:code-editor:v1") };
  });
  assert.equal(state.real, "real file");
  assert.equal(state.view.content, "fixture patch");
  assert.equal(state.view.virtual, true);
  assert.equal(state.layout.includes("commit:abcd1234"), false);
  assert.deepEqual(errors, []);
  console.log("commit view is isolated and read-only in Monaco");
} finally {
  if (browser) await browser.close();
  await vite.close();
}
