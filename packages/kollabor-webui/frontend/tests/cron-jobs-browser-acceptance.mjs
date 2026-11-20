import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";
import { createServer } from "vite";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const outDir = process.env.CRON_SHOT_DIR ?? "/tmp/cron-jobs-vision";
fs.mkdirSync(outDir, { recursive: true });

const VIEWPORTS = [
  { width: 390, height: 844, isMobile: true },
  { width: 820, height: 1180, isMobile: false },
  { width: 1280, height: 900, isMobile: false },
];

const BASE_SECONDS = 1_800_000_000;

function cronJob(over = {}) {
  return {
    agent: "lapis",
    id: "job-alpha12",
    target: "koordinator",
    message: "check build status and report the rollup",
    interval_seconds: 3600,
    interval: "1h",
    next_fire: BASE_SECONDS + 3600,
    next_in_seconds: 3600,
    recurring: true,
    mode: "recurring",
    created_at: BASE_SECONDS - 7200,
    last_error: null,
    ...over,
  };
}

const POPULATED_VIEW = {
  jobs: [
    cronJob(),
    cronJob({
      agent: "nephrite",
      id: "job-beta34",
      target: "lapis@fixture-device",
      message: "send the 30m standup pulse",
      interval_seconds: 1800,
      interval: "30m",
      next_fire: BASE_SECONDS + 900,
      next_in_seconds: 900,
    }),
    cronJob({
      agent: "bismuth",
      id: "job-gamma56",
      target: "koordinator",
      message: "one-shot reminder to file the QA report",
      interval_seconds: 0,
      interval: "one-shot",
      next_fire: BASE_SECONDS + 240,
      next_in_seconds: 240,
      recurring: false,
      mode: "one-shot",
      last_error: "previous fire failed: peer socket closed",
    }),
  ],
  count: 3,
  agents_responded: ["lapis", "nephrite", "bismuth"],
  agents_unavailable: [],
  agents_errors: { zircon: "local-operator gate refused the cron inspection" },
};

const EMPTY_REFUSED_VIEW = {
  jobs: [],
  count: 0,
  agents_responded: [],
  agents_unavailable: ["sapphire"],
  agents_errors: { sapphire: "local-operator gate refused the cron inspection" },
};

function session() {
  return {
    session_id: "cron-regression-session",
    name: "Cron regression session",
    agent: "coder",
    identity: "moonstone",
    profile: "default",
    workspace: "/cron-demo",
    device: "fixture-device",
    network: null,
    external: false,
  };
}

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function makeFixtures(view) {
  async function respond(route) {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
    const method = request.method();
    if (endpoint === "/hub/cron-jobs") return json(route, view);
    if (endpoint === "/sessions" && method === "POST") return json(route, session());
    if (endpoint === "/sessions" && method === "GET")
      return json(route, {
        sessions: [session()],
        offline: [],
        network: null,
        hidden: [],
        order: [],
        order_revision: 0,
        order_warning: null,
      });
    if (endpoint === "/profiles")
      return json(route, {
        active: "default",
        providers: [],
        profiles: [
          {
            name: "default",
            provider: "openai_responses",
            model: "gpt-6-luna",
            base_url: "https://api.example.invalid/v1",
            reasoning_effort: "max",
          },
        ],
      });
    if (endpoint === "/rosters")
      return json(route, { workspace: "/cron-demo", default: "test", rosters: [], problems: [] });
    if (endpoint === "/teams") return json(route, { teams: [], problems: [] });
    if (endpoint === "/agents/bundles")
      return json(route, { bundles: [{ name: "coder", agent_type: "coder" }] });
    if (endpoint.startsWith("/agents"))
      return json(route, { agents: [], live: [], active: [], available: [] });
    if (endpoint === "/prompts/libraries")
      return json(route, { libraries: [], roles: ["default"] });
    if (endpoint.startsWith("/prompts/") && endpoint.endsWith("/sections"))
      return json(route, { sections: [] });
    if (endpoint.startsWith("/prompts/") && endpoint.endsWith("/composition"))
      return json(route, { variants: [] });
    if (endpoint === "/workspaces")
      return json(route, { home: { path: "/cron-demo", name: "Cron demo", live: false }, recent: [] });
    if (endpoint.endsWith("/history"))
      return json(route, {
        history: [],
        start_index: 0,
        end_index: 0,
        has_more_older: false,
        older_cursor: null,
        has_more_newer: false,
        newer_cursor: null,
        source: "mirror",
        stale: false,
        last_turn_error: null,
      });
    if (endpoint.endsWith("/permissions"))
      return json(route, { pending_prompts: [], approval_mode: "trust_all" });
    if (endpoint.endsWith("/events"))
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    if (endpoint.startsWith("/sessions/")) return json(route, session());
    return json(route, {});
  }
  return { respond };
}

async function openVite() {
  const server = await createServer({
    configFile: path.join(frontendRoot, "vite.config.ts"),
    root: frontendRoot,
    logLevel: "error",
    clearScreen: false,
    server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
  });
  await server.listen();
  const address = server.httpServer?.address();
  if (!address || typeof address === "string")
    throw new Error("Vite did not expose its local listener address.");
  return { server, baseUrl: `http://127.0.0.1:${address.port}` };
}

function resolveChromePath() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  return undefined;
}

async function openPage(browser, baseUrl, view, options = {}) {
  const context = await browser.newContext({
    viewport: options.viewport ?? { width: 1280, height: 900 },
    deviceScaleFactor: 1,
    isMobile: options.isMobile ?? false,
    hasTouch: options.isMobile ?? false,
  });
  await context.addInitScript(() => {
    localStorage.setItem("kollab.setup-guide.v1", "dismissed");
  });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(`${error.message}\n${(error.stack ?? "").split("\n").slice(1, 5).join("\n")}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const fixtures = makeFixtures(view);
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const isEngine = url.port === "7433" || url.port === "8080";
    const isSameOriginFetch = url.origin === baseUrl && ["fetch", "xhr"].includes(request.resourceType());
    if (isEngine || isSameOriginFetch) return fixtures.respond(route);
    if (url.origin === baseUrl) return route.continue();
    return route.abort();
  });
  return { context, page, pageErrors, consoleErrors };
}

async function openCronTab(page, pageErrors = []) {
  // The fixture session auto-opens on boot (GET /sessions returns one live
  // session, App activates it). Just wait for the composer; no manual create.
  await page.getByRole("textbox").first().waitFor({ state: "visible", timeout: 20000 });
  await page.waitForTimeout(800);
  await page.getByRole("textbox").first().waitFor({ state: "visible", timeout: 15000 });
  const settingsBtn = page.getByRole("button", { name: "Settings" }).first();
  if (!(await settingsBtn.isVisible().catch(() => false))) {
    // Narrow viewports keep the sidebar behind a sheet trigger; open it first.
    const trigger = page.getByRole("button", { name: /Toggle Sidebar/ }).first();
    await trigger.click({ timeout: 5000 });
    await settingsBtn.waitFor({ state: "visible", timeout: 5000 });
  }
  await settingsBtn.click();
  await page.getByRole("tabpanel", { name: "Session" }).waitFor({ state: "visible" });
  await page.getByRole("tab", { name: "Cron Jobs" }).click();
  await page.getByRole("tabpanel", { name: "Cron Jobs" }).waitFor({ state: "visible" });
}

async function main() {
  const head = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Cron browser acceptance code head: ${head}`);
  const { server, baseUrl } = await openVite();
  const executablePath = resolveChromePath();
  const browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: process.platform === "linux" ? ["--no-sandbox"] : [],
  });
  const shots = [];
  try {
    for (const mode of ["populated", "empty-refused"]) {
      const view = mode === "populated" ? POPULATED_VIEW : EMPTY_REFUSED_VIEW;
      for (const vp of VIEWPORTS) {
        const { context, page, pageErrors, consoleErrors } = await openPage(browser, baseUrl, view, {
          viewport: { width: vp.width, height: vp.height },
          isMobile: vp.isMobile,
        });
        await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
        await openCronTab(page, pageErrors);
        if (mode === "populated") {
          // Breakpoints render one variant: table cells (>=768px) or cards (dd).
          const cellSel =
            vp.width < 768
              ? "dd:has-text('check build status and report the rollup')"
              : "td:has-text('check build status and report the rollup')";
          await page.locator(cellSel).first().waitFor({ state: "visible" });
        } else {
          await page.getByRole("alert").first().waitFor({ state: "visible" });
        }
        const file = path.join(outDir, `cron-${mode}-${vp.width}.png`);
        await page.screenshot({ path: file, animations: "disabled", fullPage: false });
        shots.push(file);
        assert.deepEqual(pageErrors, [], `page errors at ${mode}@${vp.width}: ${pageErrors.join("; ")}`);
        const realErrors = consoleErrors.filter((line) => !line.includes("favicon"));
        assert.deepEqual(realErrors, [], `console errors at ${mode}@${vp.width}: ${realErrors.join("; ")}`);
        await context.close();
        console.log(`captured ${file}`);
      }
    }
  } finally {
    await browser.close();
    await server.close();
  }
  console.log(`PASS: ${shots.length} screenshots at head ${head}`);
  for (const file of shots) console.log(`  ${file}`);
}

main().catch((error) => {
  console.error("FAIL:", error);
  process.exit(1);
});
