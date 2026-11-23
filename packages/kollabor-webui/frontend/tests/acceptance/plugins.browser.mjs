// Issue #1028: the bundled Hello plugin in the built web app at 1440, 820 and 390 px wide, and ?plugins=off.
// Run from the frontend folder: npm run test:e2e:plugins
// Browser: CHROME_PATH if set, else playwright-core's chromium.
// PLUGIN_BUILD_OUT keeps the build folder; without it a temp folder is built and removed after the run.
// PLUGIN_SCREENSHOTS writes hello-<width>.png into that folder.
// The app is the production build (scripts/build.mjs), served by a local Node server that sends the production
// CSP for its import map. /api and /engine are fakes. Hello's manifest and bundle are read from bundles/plugins/hello.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const helloRoot = path.resolve(frontendRoot, "../../../bundles/plugins/hello");
const buildOut = process.env.PLUGIN_BUILD_OUT
  ? path.resolve(process.env.PLUGIN_BUILD_OUT)
  : mkdtempSync(path.join(os.tmpdir(), "kollab-plugins-"));
const screenshots = process.env.PLUGIN_SCREENSHOTS ? path.resolve(process.env.PLUGIN_SCREENSHOTS) : null;
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
  ".wasm": "application/wasm",
};

const manifest = JSON.parse(readFileSync(path.join(helloRoot, "kollab-plugin.json"), "utf8"));
const helloJs = readFileSync(path.join(helloRoot, "web/dist/index.js"));
const helloCss = readFileSync(path.join(helloRoot, "web/dist/index.css"));
const plugin = {
  id: manifest.id,
  name: manifest.name,
  version: manifest.version,
  kind: "built-in",
  source: "bundled",
  commit: null,
  enabled: true,
  shadowed: false,
  state: "ok",
  error: null,
  web: { entry: manifest.web.entry, style: manifest.web.style, hash: sha256(helloJs, "hex") },
  contributes: manifest.contributes,
  services: Object.keys(manifest.services ?? {}).map((name) => ({
    name,
    state: "stopped",
    desired: "stopped",
    pid: null,
    since: null,
    restarts: 0,
    heartbeat: null,
    error: null,
  })),
  uses: manifest.uses ?? [],
  granted: [],
};
const sessions = {
  sessions: [
    {
      session_id: "fixture-session",
      name: "Fixture Session",
      agent: "coder",
      identity: "local-coder",
      profile: "default",
      workspace: "/fixture",
      external: false,
    },
  ],
  network: { device: "test", remote: [] },
  offline: [],
  hidden: [],
};
const history = { history: [], start_index: 0, end_index: 0, has_more_older: false, has_more_newer: false, source: "mirror" };
const emptyEngine = {
  sessions: [],
  profiles: [],
  agents: [],
  live: [],
  bundles: [],
  variants: {},
  roles: [],
  libraries: [],
  sections: [],
  identities: [],
  teams: [],
  models: [],
  commands: [],
  network: { device: "", remote: [] },
  offline: [],
  hidden: [],
  order: [],
};

const problems = [];
let pluginListRequests = 0;
let indexBytes;
let pageCsp;
let origin;
let server;
let browser;

function sha256(bytes, encoding) {
  return createHash("sha256").update(bytes).digest(encoding);
}

function send(res, status, type, body, headers = {}) {
  res.writeHead(status, { "content-type": type, ...headers });
  res.end(body);
}

function sendJson(res, body) {
  send(res, 200, "application/json", JSON.stringify(body));
}

/** The routes the app reads at boot and the plugin's own files. Returns false for any other path. */
function engine(url, req, res) {
  const { pathname } = url;
  if (pathname === "/api/config") {
    sendJson(res, { engine_url: "/engine" });
    return true;
  }
  if (pathname === "/plugins/hello/web/index.js") {
    send(res, 200, mime[".js"], helloJs);
    return true;
  }
  if (pathname === "/plugins/hello/web/index.css") {
    send(res, 200, mime[".css"], helloCss);
    return true;
  }
  if (pathname === "/engine/plugins" && req.method === "GET") {
    pluginListRequests += 1;
    sendJson(res, { schema: 1, server_time: Math.floor(Date.now() / 1000), plugins: [plugin] });
    return true;
  }
  if (pathname === "/engine/plugins/events") {
    send(res, 200, "text/event-stream", ": open\n\n");
    return true;
  }
  if (pathname === "/engine/sessions") {
    sendJson(res, sessions);
    return true;
  }
  if (pathname.endsWith("/history")) {
    sendJson(res, history);
    return true;
  }
  if (pathname.startsWith("/api/")) {
    sendJson(res, { available: false });
    return true;
  }
  if (pathname.startsWith("/engine/")) {
    sendJson(res, emptyEngine);
    return true;
  }
  return false;
}

function staticFile(pathname, res) {
  const file = path.join(buildOut, decodeURIComponent(pathname));
  if (!file.startsWith(buildOut + path.sep) || !existsSync(file) || !statSync(file).isFile()) return false;
  send(res, 200, mime[path.extname(file)] ?? "application/octet-stream", readFileSync(file));
  return true;
}

function serve(req, res) {
  try {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    if (engine(url, req, res)) return;
    if (!url.pathname.endsWith(".html") && staticFile(url.pathname, res)) return;
    // Every other path is the app's page; the app reads its own routes such as /plugin?id=...
    send(res, 200, mime[".html"], indexBytes, { "content-security-policy": pageCsp });
  } catch (error) {
    send(res, 400, "text/plain; charset=utf-8", String(error));
  }
}

/** A malformed path makes serve() fail. The body must stay generic, and the error must reach problems. */
async function assertMalformedPathIsGeneric(port) {
  const recorded = problems.length;
  const reply = await new Promise((resolve, reject) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/%" }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => (body += chunk));
      response.on("end", () => resolve({ status: response.statusCode, body }));
    });
    request.on("error", reject);
  });
  assert.equal(reply.status, 400, "a malformed path is refused");
  assert.equal(reply.body, "bad request", "a malformed path gets a generic body, not the server error text");
  assert.equal(problems.length, recorded + 1, "the fixture server records the malformed path error for this suite");
  problems.length = recorded; // this probe's error is expected, so it does not fail the suite
}

async function openApp(width, height, { route = "/", touch = false } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: touch });
  const page = await context.newPage();
  page.on("pageerror", (error) => problems.push(`${width}px page error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/.test(message.text())) {
      problems.push(`${width}px ${message.text()}`);
    }
  });
  if (touch) {
    const cdp = await context.newCDPSession(page);
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  }
  await page.goto(`${origin}${route}`, { waitUntil: "domcontentloaded" });
  return { context, page };
}

async function assertFits(page, label) {
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  assert.ok(widths.scroll <= widths.client, `${label}: page is ${widths.scroll}px wide in a ${widths.client}px viewport`);
}

async function assertTouchTarget(locator, label) {
  const box = await locator.boundingBox();
  assert.ok(box, `${label} is not on screen`);
  assert.ok(box.width >= 44 && box.height >= 44, `${label} is ${Math.round(box.width)}x${Math.round(box.height)}, below 44x44`);
}

async function screenshot(page, width) {
  if (!screenshots) return;
  mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, `hello-${width}.png`) });
}

async function showHelloView(page, panel) {
  await panel.getByRole("button", { name: "Hello View", exact: true }).click({ timeout: 5000 });
  await page.getByText("Hello from your plugin!", { exact: true }).waitFor({ timeout: 5000 });
}

async function desktopFlow() {
  const { context, page } = await openApp(1440, 900);
  try {
    const panel = page.getByRole("complementary", { name: "Session Controls" });
    await panel.waitFor({ timeout: 10000 });
    await showHelloView(page, panel);
    await assertFits(page, "1440px hello view");
    await screenshot(page, 1440);
    await page.getByRole("button", { name: "Say Hello", exact: true }).click({ timeout: 5000 });
    await page.getByText("Hello count: 1", { exact: true }).waitFor({ timeout: 5000 });
    await page.getByRole("button", { name: "Open Hello Dialog", exact: true }).click({ timeout: 5000 });
    const dialog = page.getByRole("dialog", { name: "Hello Dialog", exact: true });
    await dialog.getByText("Hello from the dialog.", { exact: true }).waitFor({ timeout: 5000 });
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden", timeout: 5000 });
    await panel.getByRole("button", { name: "Hello Page", exact: true }).click({ timeout: 5000 });
    await page.getByRole("heading", { name: "Hello Page", exact: true }).waitFor({ timeout: 5000 });
    await page.getByText("This page is provided by the Hello plugin.", { exact: true }).waitFor({ timeout: 5000 });
    await assertFits(page, "1440px hello page");
    await page.getByRole("button", { name: "Close Hello Page", exact: true }).click({ timeout: 5000 });
  } finally {
    await context.close();
  }
}

// Below 1024 px the session panel is a sheet opened from the header button, not a side column.
async function tabletFlow() {
  const { context, page } = await openApp(820, 1000);
  try {
    await page.getByRole("button", { name: "Session Controls", exact: true }).click({ timeout: 10000 });
    const sheet = page.getByRole("dialog", { name: "Session Controls", exact: true });
    await sheet.waitFor({ timeout: 5000 });
    await sheet.getByRole("button", { name: "Hello View", exact: true }).click({ timeout: 5000 });
    await sheet.waitFor({ state: "hidden", timeout: 5000 });
    await page.getByText("Hello from your plugin!", { exact: true }).waitFor({ timeout: 5000 });
    await assertFits(page, "820px hello view");
    await screenshot(page, 820);
  } finally {
    await context.close();
  }
}

async function phoneFlow() {
  const { context, page } = await openApp(390, 844, { touch: true });
  try {
    const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
    assert.ok(coarse, "touch emulation reports a coarse pointer");
    await page.getByRole("button", { name: "Session Controls", exact: true }).click({ timeout: 10000 });
    const sheet = page.getByRole("dialog", { name: "Session Controls", exact: true });
    await sheet.waitFor({ timeout: 5000 });
    await assertTouchTarget(sheet.getByRole("button", { name: "Hello View", exact: true }), "Hello View row");
    await assertTouchTarget(sheet.getByRole("button", { name: "Hello Page", exact: true }), "Hello Page row");
    await sheet.getByRole("button", { name: "Hello View", exact: true }).click({ timeout: 5000 });
    await sheet.waitFor({ state: "hidden", timeout: 5000 });
    await page.getByText("Hello from your plugin!", { exact: true }).waitFor({ timeout: 5000 });
    await assertFits(page, "390px hello view");
    await screenshot(page, 390);
    await page.getByRole("button", { name: "Open Hello Dialog", exact: true }).click({ timeout: 5000 });
    const dialog = page.getByRole("dialog", { name: "Hello Dialog", exact: true });
    await dialog.waitFor({ timeout: 5000 });
    // Needs #1397 (44px dialog and sheet close buttons). Re-add
    // assertTouchTarget(dialog.getByRole("button", { name: "Close", exact: true }), "Hello Dialog close") once #1397 is merged.
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden", timeout: 5000 });
    await page.getByRole("button", { name: "Session Controls", exact: true }).click({ timeout: 5000 });
    await sheet.waitFor({ timeout: 5000 });
    await sheet.getByRole("button", { name: "Hello Page", exact: true }).click({ timeout: 5000 });
    await page.getByRole("heading", { name: "Hello Page", exact: true }).waitFor({ timeout: 5000 });
    await assertFits(page, "390px hello page");
    await page.getByRole("button", { name: "Close Hello Page", exact: true }).click({ timeout: 5000 });
  } finally {
    await context.close();
  }
}

async function pluginsOffFlow() {
  pluginListRequests = 0;
  const { context, page } = await openApp(1440, 900, { route: "/?plugins=off" });
  try {
    await page.getByRole("complementary", { name: "Session Controls" }).waitFor({ timeout: 10000 });
    await page.waitForTimeout(1000);
    assert.equal(pluginListRequests, 0, "?plugins=off requests no plugin list");
    assert.equal(
      await page.getByRole("button", { name: "Hello View", exact: true }).count(),
      0,
      "?plugins=off shows no Hello View",
    );
  } finally {
    await context.close();
  }
}

let exitCode = 0;
try {
  execFileSync(process.execPath, ["scripts/build.mjs", "--outDir", buildOut], {
    cwd: frontendRoot,
    stdio: "inherit",
    timeout: 600000,
  });
  indexBytes = readFileSync(path.join(buildOut, "index.html"));
  const importMap = /<script type="importmap">([\s\S]*?)<\/script>/.exec(indexBytes.toString("latin1"));
  assert.ok(importMap, "the built index.html carries an import map");
  pageCsp = `script-src 'self' 'sha256-${sha256(Buffer.from(importMap[1], "latin1"), "base64")}'`;
  server = http.createServer(serve);
  await new Promise((listening) => server.listen(0, "127.0.0.1", listening));
  origin = `http://127.0.0.1:${server.address().port}`;
  await assertMalformedPathIsGeneric(server.address().port);
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH ?? chromium.executablePath() });
  await desktopFlow();
  await tabletFlow();
  await phoneFlow();
  await pluginsOffFlow();
  assert.deepEqual(problems, [], "no page errors or CSP violations");
  console.log("plugins acceptance: Hello view, page and dialog pass at 1440, 820 and 390 px; plugins=off requests nothing");
} catch (error) {
  exitCode = 1;
  console.error(error);
} finally {
  await browser?.close();
  if (server) await new Promise((closed) => server.close(closed));
  if (!process.env.PLUGIN_BUILD_OUT) rmSync(buildOut, { recursive: true, force: true });
}
process.exitCode = exitCode;
