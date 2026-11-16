// Synthetic Git responses; no repository is accessed or changed.
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
  let lists = 0;
  const applied = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: origin } });
    if (url.pathname === "/sessions/fixture/code/git") {
      const request = route.request().postDataJSON();
      if (request.action === "list_stashes") {
        lists++;
        const stashes = lists === 1
          ? [{ id: "0", message: "original", branch: "main", date: "today", commitHash: "a".repeat(40) },
            { id: "1", message: "missing identity", branch: "main", date: "today" }]
          : [{ id: "0", message: "new arrival", branch: "main", date: "today", commitHash: "b".repeat(40) },
            { id: "1", message: "original", branch: "main", date: "today", commitHash: "a".repeat(40) }];
        return route.fulfill({ json: { success: true, data: { ok: true, stashes } } });
      }
      assert.equal(request.action, "apply_stash");
      applied.push(request);
      return route.fulfill({ json: { success: true, data: { ok: true } } });
    }
    return route.continue();
  });
  await page.goto(`${origin}/tests/acceptance/stash-confirm.html`);
  await page.getByRole("button", { name: "Apply missing identity" }).click();
  await page.getByText("The stash identity is unavailable. Refresh the list before applying it.").waitFor();
  assert.equal(applied.length, 0);
  await page.getByRole("button", { name: "Apply original" }).click();
  await page.getByRole("dialog").getByText("original", { exact: true }).waitFor();
  await page.evaluate(() => document.querySelector('[aria-label="Refresh stash list"]').click());
  await page.getByRole("list").getByText("new arrival").waitFor();
  await page.getByRole("dialog").getByText("original", { exact: true }).waitFor();
  await page.getByRole("dialog").getByRole("button", { name: "Apply", exact: true }).click();
  assert.equal(applied.length, 1);
  assert.equal(applied[0].stashCommit, "a".repeat(40));
  assert.equal("stashId" in applied[0], false);
  console.log("stash confirmation retains selected commit across list refresh");
} finally {
  if (browser) await browser.close();
  await vite.close();
}
