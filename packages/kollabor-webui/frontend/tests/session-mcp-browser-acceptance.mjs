// Browser coverage for partial MCP status in the Session Controls panel (#923).
// It uses the real EngineApi request methods and a local HTTP fixture, never an engine or provider.
// Run from the frontend folder with: node tests/session-mcp-browser-acceptance.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");
const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = path.join(os.tmpdir(), "kollab-session-mcp");
await mkdir(screenshotDir, { recursive: true });
const server = await createServer({
  configFile: path.join(frontendRoot, "vite.config.ts"),
  root: frontendRoot,
  logLevel: "error",
  clearScreen: false,
  server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
});
let browser;

try {
  await server.listen();
  const address = server.httpServer?.address();
  assert.ok(address && typeof address !== "string", "Vite did not expose its local port");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const requests = [];
  page.on("pageerror", (error) => console.error(`PAGE_ERROR ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") console.error(`CONSOLE_ERROR ${message.text()}`);
  });
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    const pathname = url.pathname.replace(/^\/api(?=\/)/, "");
    if (pathname !== "/config" && !pathname.startsWith("/hub/") && !pathname.startsWith("/sessions/") && !pathname.startsWith("/mcp/")) {
      return route.continue();
    }
    if (pathname === "/config") return route.fulfill({ status: 200, json: {} });
    if (pathname === "/hub/agents") return route.fulfill({ status: 200, json: { agents: [] } });
    if (pathname === "/sessions/session-a/mcp") {
      return route.fulfill({
        status: 200,
        json: {
          session_id: "session-a",
          servers: { "session-only": { status: "connected", tools: ["search"] } },
        },
      });
    }
    if (pathname === "/mcp/servers") {
      return route.fulfill({ status: 503, json: { detail: "global config temporarily unavailable" } });
    }
    return route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${origin}/tests/acceptance/session-mcp.html`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "MCP Servers" }).waitFor({ timeout: 5000 }).catch(async () => {
    console.error(`FIXTURE_TEXT ${await page.locator("#root").innerText()}`);
    throw new Error("Session MCP fixture did not render");
  });
  await page.getByRole("button", { name: "MCP Servers" }).click();
  const dialog = page.getByRole("dialog", { name: "MCP Servers" });
  await dialog.getByText("session-only", { exact: true }).waitFor();
  assert.ok(requests.includes("/sessions/session-a/mcp"), "session MCP state must use the API endpoint");
  assert.ok(requests.includes("/mcp/servers"), "global definitions must use the API endpoint");

  // This is the red-first assertion: a failed global lookup must be visible and
  // must not describe the session-only row as configured by the agent.
  assert.ok(
    await dialog.getByText(/global.*unavailable|could not load global/i).count(),
    "show why global definitions are unavailable",
  );
  assert.equal(await dialog.getByText("configured by agent", { exact: true }).count(), 0);
  assert.equal(await dialog.getByRole("button", { name: "Edit session-only" }).count(), 0);
  assert.equal(await dialog.getByRole("button", { name: "Delete session-only" }).count(), 0);
  await page.screenshot({ path: path.join(screenshotDir, "session-mcp-global-failure.png"), fullPage: true });
  console.log("FAILURE_REPRODUCED session state rendered while global-definition API returned HTTP 503");
  console.log(`SCREENSHOT session-mcp-global-failure.png (${screenshotDir})`);
  console.log(`FRONTEND ${repoRoot}`);
} finally {
  await browser?.close();
  await server.close();
}
