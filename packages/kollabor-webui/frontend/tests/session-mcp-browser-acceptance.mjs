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
const screenshotDir = path.join(os.tmpdir(), "kollab-session-mcp");
await mkdir(screenshotDir, { recursive: true });
const sessionServer = (name) => ({
  session_id: name,
  servers: { "session-only": { status: "connected", tools: ["search"] } },
});
const sessionStates = {
  "session-a": sessionServer("session-a"),
  "session-b": { session_id: "session-b", servers: { "session-b-only": { status: "disconnected" } } },
};
let failSession = false;
let failDefinitions = true;
let definitions = { "global-server": { type: "stdio", command: "global-command", description: "Global fixture" } };
let denyDisconnect = false;
let heldSessionA = null;
let releaseSessionA = null;
let initialHold = null;
let releaseInitial = null;
const holdSessionA = () => new Promise((resolve) => { releaseSessionA = resolve; });
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
      if (initialHold) await initialHold;
      if (heldSessionA) await heldSessionA;
      if (failSession) return route.fulfill({ status: 503, json: { detail: "session state temporarily unavailable" } });
      return route.fulfill({ status: 200, json: sessionStates["session-a"] });
    }
    if (pathname === "/sessions/session-b/mcp") return route.fulfill({ status: 200, json: sessionStates["session-b"] });
    if (pathname === "/mcp/servers") {
      if (initialHold) await initialHold;
      if (failDefinitions) return route.fulfill({ status: 503, json: { detail: "global config temporarily unavailable" } });
      return route.fulfill({ status: 200, json: { servers: definitions } });
    }
    if (pathname === "/sessions/session-a/mcp/session-only/disconnect" && denyDisconnect) {
      return route.fulfill({ status: 403, json: { detail: "permission denied by fixture" } });
    }
    return route.fulfill({ status: 200, json: {} });
  });

  initialHold = new Promise((resolve) => { releaseInitial = resolve; });
  await page.goto(`${origin}/tests/acceptance/session-mcp.html`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "MCP Servers" }).waitFor({ timeout: 5000 }).catch(async () => {
    console.error(`FIXTURE_TEXT ${await page.locator("#root").innerText()}`);
    throw new Error("Session MCP fixture did not render");
  });
  await page.getByRole("button", { name: "MCP Servers" }).click();
  const dialog = page.getByRole("dialog", { name: "MCP Servers" });
  await dialog.getByText("Loading session MCP status…").waitFor();
  await dialog.getByText("Loading global MCP definitions…").waitFor();
  releaseInitial();
  initialHold = null;
  await dialog.getByText("session-only", { exact: true }).waitFor();
  assert.ok(requests.includes("/sessions/session-a/mcp"), "session MCP state must use the API endpoint");
  assert.ok(requests.includes("/mcp/servers"), "global definitions must use the API endpoint");

  // This is the red-first assertion: a failed global lookup must be visible and
  // must not describe the session-only row as configured by the agent.
  assert.ok(
    await dialog.getByText(/global.*unavailable|could not load global/i).count(),
    "show why global definitions are unavailable",
  );
  assert.ok((await dialog.innerText()).includes("global unavailable · session-only unknown"));
  assert.equal(await dialog.getByText("configured by agent", { exact: true }).count(), 0);
  assert.equal(await dialog.getByRole("button", { name: "Edit session-only" }).isDisabled(), true);
  assert.equal(await dialog.getByRole("button", { name: "Delete session-only" }).isDisabled(), true);
  await page.screenshot({ path: path.join(screenshotDir, "session-mcp-global-failure.png"), fullPage: true });

  // Retry recovers the failed source, exposes known ownership, and leaves valid
  // connection controls behind the same permission-checked API boundary.
  failDefinitions = false;
  await dialog.getByRole("button", { name: "Retry MCP data" }).click();
  await dialog.getByText("global-server", { exact: true }).waitFor();
  assert.ok((await dialog.innerText()).includes("1 connected · 1 global · 1 session-only"));
  assert.equal(await dialog.getByRole("button", { name: "Edit global-server" }).isEnabled(), true);
  assert.equal(await dialog.getByRole("button", { name: "Delete global-server" }).isEnabled(), true);
  assert.equal(await dialog.getByRole("button", { name: "Disconnect" }).isEnabled(), true);
  denyDisconnect = true;
  await dialog.getByRole("button", { name: "Disconnect" }).click();
  assert.ok(requests.includes("/sessions/session-a/mcp/session-only/disconnect"));
  assert.equal(await dialog.getByRole("button", { name: "Disconnect" }).isEnabled(), true);

  // Session-status failure leaves known global definitions visible and blocks
  // connection actions while keeping known global edit authority usable.
  failSession = true;
  await dialog.getByRole("button", { name: "Refresh" }).click();
  await dialog.getByText(/Session MCP status unavailable/).waitFor();
  assert.ok((await dialog.innerText()).includes("1 global · session-only unknown"));
  assert.equal(await dialog.getByRole("button", { name: "Connect" }).isDisabled(), true);
  assert.equal(await dialog.getByRole("button", { name: "Edit global-server" }).isEnabled(), true);

  // Both endpoints can fail together, then recover on the same clear retry.
  failDefinitions = true;
  await dialog.getByRole("button", { name: "Refresh" }).click();
  await dialog.getByText(/Global MCP definitions unavailable/).waitFor();
  await dialog.getByText(/Session MCP status unavailable/).waitFor();
  failSession = false;
  failDefinitions = false;
  await dialog.getByRole("button", { name: "Retry MCP data" }).click();
  await dialog.getByRole("button", { name: "Disconnect" }).waitFor();
  assert.equal(await dialog.getByText(/MCP status unavailable/).count(), 0);

  // A successful empty response from both sources is the only state that says
  // there are no configured servers.
  definitions = {};
  sessionStates["session-a"] = { session_id: "session-a", servers: {} };
  await dialog.getByRole("button", { name: "Refresh" }).click();
  await dialog.getByText("No MCP servers configured.").waitFor();

  // The panel reports loading, switches to the new session, and ignores an old
  // response that arrives after the new session's data.
  sessionStates["session-a"] = sessionServer("session-a");
  heldSessionA = holdSessionA();
  await dialog.getByRole("button", { name: "Refresh" }).click();
  await dialog.getByText("Loading session MCP status…").waitFor();
  await page.keyboard.press("Escape");
  await page.getByTestId("switch-session").click();
  await page.getByRole("button", { name: "MCP Servers" }).click();
  const sessionBDialog = page.getByRole("dialog", { name: "MCP Servers" });
  await sessionBDialog.getByText("session-b-only", { exact: true }).waitFor();
  releaseSessionA();
  heldSessionA = null;
  await page.waitForTimeout(50);
  assert.equal(await sessionBDialog.getByText("session-only", { exact: true }).count(), 0);
  assert.ok(requests.includes("/sessions/session-b/mcp"));

  console.log("PASS MCP source failures, empty success, retry, session switch, stale response, and permission boundary");
  console.log(`SCREENSHOT session-mcp-global-failure.png (${screenshotDir})`);
} finally {
  await browser?.close();
  await server.close();
}
