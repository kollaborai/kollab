import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";
import { createServer } from "vite";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = path.join(repoRoot, "docs/screenshots");
const topics = [
  { id: "networks", title: "Kollab networks" },
  { id: "agents", title: "Agents" },
  { id: "phone-access", title: "WireGuard phone access" },
];

function makeFixtures() {
  let createdSession = null;
  let createPayload = null;
  let resolveCreate;
  const createRequest = new Promise((resolve) => {
    resolveCreate = resolve;
  });
  const requests = [];

  const remoteAgent = {
    name: "remote-coder",
    device: "remote-build",
    network: "learn-test",
    workspace: "/remote/demo",
    handle: "remote-session-1",
    state: "idle",
    last_seen: 1,
  };
  const network = {
    device: "learn-laptop",
    folder: "/learn-demo",
    live: true,
    remote: [remoteAgent],
    contacts: [],
    networks: [
      {
        id: "learn-test",
        name: "Learn Test Hub",
        trust: "trusted",
        live: true,
        devices: [
          {
            device: "remote-build",
            live: true,
            workspaces: [{ label: "/remote/demo", agents: [remoteAgent] }],
          },
        ],
      },
    ],
  };
  const localCoder = {
    name: "local-coder",
    identity: "local-coder",
    roster: "test",
    display_name: "Local Coder",
    avatar: { kind: "gem" },
    color: [36, 117, 190],
    agent: "coder",
    agent_type: "coder",
    profile: "default",
    available: true,
    active: false,
    pool: true,
  };
  const agents = { agents: [localCoder], live: [], active: [], available: ["local-coder"] };
  const profiles = {
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
      {
        name: "review",
        provider: "anthropic",
        model: "claude-haiku",
        base_url: "https://api.example.invalid/anthropic",
        reasoning_effort: "low",
      },
    ],
  };

  const session = () => ({
    sessions: createdSession ? [createdSession] : [],
    network,
    offline: [],
    hidden: [],
  });
  const remoteSession = {
    session_id: remoteAgent.handle,
    name: "Remote coder session",
    agent: "coder",
    identity: remoteAgent.name,
    profile: "default",
    workspace: remoteAgent.workspace,
    device: remoteAgent.device,
    network: remoteAgent.network,
    external: true,
  };

  async function respond(route) {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
    const method = request.method();
    requests.push({ method, endpoint });

    if (endpoint === "/sessions" && method === "POST") {
      createPayload = request.postDataJSON();
      createdSession = {
        session_id: "learn-regression-session",
        name: "Learn regression session",
        agent: createPayload.agent ?? "coder",
        identity: createPayload.identity ?? "local-coder",
        profile: createPayload.profile ?? "default",
        workspace: createPayload.workspace ?? "/learn-demo",
        external: false,
      };
      resolveCreate(createPayload);
      return json(route, createdSession);
    }
    if (endpoint === "/sessions" && method === "GET") return json(route, session());
    if (endpoint === "/profiles") return json(route, profiles);
    if (endpoint === "/rosters") {
      return json(route, { workspace: "/learn-demo", default: "test", rosters: [], problems: [] });
    }
    if (endpoint === "/teams") return json(route, { teams: [], problems: [] });
    if (endpoint === "/agents/bundles") return json(route, { bundles: [{ name: "coder", agent_type: "coder" }] });
    if (endpoint.startsWith("/agents")) return json(route, agents);
    if (endpoint === "/workspaces") {
      return json(route, { home: { path: "/learn-demo", name: "Learn demo", live: false }, recent: [] });
    }
    if (endpoint.endsWith("/history")) {
      return json(route, { history: [], source: "mirror", stale: false, last_turn_error: null });
    }
    if (endpoint.endsWith("/permissions")) return json(route, { pending_prompts: [], approval_mode: "trust_all" });
    if (endpoint.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    }
    if (endpoint.startsWith("/sessions/")) {
      const requestedId = endpoint.split("/")[2];
      return json(route, requestedId === remoteAgent.handle ? remoteSession : createdSession ?? {});
    }
    return json(route, {});
  }

  return {
    agents,
    createPayload: () => createPayload,
    createRequest,
    network,
    profiles,
    remoteAgent,
    requests,
    respond,
    session,
  };
}

async function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
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
  if (!address || typeof address === "string") throw new Error("Vite did not expose its local listener address.");
  return { server, baseUrl: `http://127.0.0.1:${address.port}` };
}

async function openPage(browser, baseUrl, fixtures, options = {}) {
  const viewport = options.viewport ?? { width: 1280, height: 900 };
  const context = await browser.newContext({
    viewport,
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
  const externalRequests = [];
  let changelogFailurePending = options.failChangelogOnce ?? false;
  let changelogDelayMs = options.delayChangelogMs ?? 0;
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (decodeURIComponent(url.href).includes("virtual:kollab-changelog")) {
      if (changelogFailurePending) {
        changelogFailurePending = false;
        return route.abort();
      }
      if (changelogDelayMs > 0) {
        const delay = changelogDelayMs;
        changelogDelayMs = 0;
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    const isEngine = url.port === "7433" || url.port === "8080";
    const isSameOriginFetch = url.origin === baseUrl && ["fetch", "xhr"].includes(request.resourceType());
    if (isEngine || isSameOriginFetch) return fixtures.respond(route);
    if (url.origin === baseUrl) return route.continue();
    externalRequests.push(url.href);
    return route.abort();
  });

  return { context, externalRequests, fixture: fixtures, page, pageErrors, consoleErrors };
}

async function waitForLesson(page, title) {
  await page.getByRole("heading", { level: 1, name: title, exact: true }).waitFor({ state: "visible" });
}

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    const executablePath = process.env.CHROME_PATH;
    browser = await chromium.launch({
      headless: true,
      ...(executablePath ? { executablePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    const fixtures = makeFixtures();
    const shell = await openPage(browser, baseUrl, fixtures);
    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await shell.page.getByRole("button", { name: "Learn", exact: true }).waitFor({ state: "visible" });
    await shell.page.getByText(/Learn Test Hub/).waitFor({ state: "visible" });

    await shell.page.getByText("Remote Coder", { exact: true }).waitFor({ state: "visible" });

    // Regress startup, Hub listing, agent launch, and profile selection with explicit API fixtures.
    // Learn lives with the footer icons; contextual help stays in its related controls.
    await shell.page.getByRole("button", { name: "Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/learn");
    await waitForLesson(shell.page, "Kollab networks");
    await shell.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");

    await shell.page.getByTestId("new-session-options").click();
    await shell.page.getByRole("link", { name: "Learn about profiles", exact: true }).click();
    await shell.page.waitForFunction(() => location.search === "?topic=profiles");
    await waitForLesson(shell.page, "Provider profiles");
    await shell.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");

    await shell.page.getByRole("button", { name: "Agents", exact: true }).first().click();
    const agentLibrary = shell.page.getByRole("dialog", { name: "Agents", exact: true });
    assert.equal(await agentLibrary.getByRole("link", { name: /Learn more about/ }).count(), 0);
    await agentLibrary.getByRole("button", { name: "Close", exact: true }).click();


    await shell.page.getByTestId("new-session-options").click();
    await shell.page.locator("#new-session-model").click();
    await shell.page.getByRole("option", { name: /review/ }).click();
    await shell.page.locator("#new-session-gem").click();
    await shell.page.getByRole("option", { name: /Local Coder/ }).click();
    const createWait = fixtures.createRequest;
    await shell.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const createPayload = await Promise.race([
      createWait,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Session launch request did not arrive.")), 15000)),
    ]);
    assert.equal(createPayload.profile, "review");
    assert.equal(createPayload.agent, "coder");
    assert.equal(createPayload.identity, "local-coder");

    // A live mocked peer demonstrates the existing Hub-to-remote-session selection path.
    await shell.page.getByRole("button", { name: "Remote Coder Idle", exact: true }).click();
    await shell.page.waitForFunction(() => document.body.innerText.includes("Remote coder session"), { timeout: 15000 });
    assert.ok(fixtures.requests.some((request) => request.endpoint === "/sessions/remote-session-1"));

    // Learn route entry/exit must preserve the existing main pane and restore keyboard focus.
    await shell.page.getByRole("button", { name: "Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/learn");
    await waitForLesson(shell.page, "Kollab networks");
    await shell.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");

    // Topic navigation is a reference flow without completion controls.
    await shell.page.getByRole("button", { name: "Learn", exact: true }).click();
    const topics = shell.page.getByRole("navigation", { name: "Learn topics" });
    assert.equal(await topics.getByRole("button").count(), 10);
    await topics.getByRole("button", { name: "Agents", exact: true }).focus();
    await shell.page.keyboard.press("Enter");
    await shell.page.waitForFunction(() => location.search === "?topic=agents");
    await waitForLesson(shell.page, "Agents");
    assert.equal(await shell.page.evaluate(() => document.activeElement?.tagName), "H1");
    assert.equal(await shell.page.getByRole("progressbar").count(), 0);
    assert.equal(await shell.page.getByRole("button", { name: /Mark complete|Mark incomplete|Previous lesson|Next lesson/ }).count(), 0);
    assert.equal(await shell.page.getByText("Unreleased", { exact: true }).count(), 0);
    await shell.page.reload({ waitUntil: "domcontentloaded" });
    await waitForLesson(shell.page, "Agents");
    await shell.page.goBack();
    await shell.page.waitForFunction(() => location.pathname === "/learn" && location.search === "");
    await shell.page.goto(`${baseUrl}/learn?topic=not-a-topic`, { waitUntil: "domcontentloaded" });
    await shell.page.getByRole("heading", { name: "That topic is not available" }).waitFor({ state: "visible" });
    await shell.page.getByRole("button", { name: "Browse topics" }).click();
    await shell.page.waitForFunction(() => location.search === "?topic=networks");

    // Release updates have their own route, close control, and retry path.
    await shell.page.getByRole("button", { name: "What’s New", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/whats-new");
    await shell.page.getByRole("heading", { level: 1, name: "What’s New", exact: true }).waitFor();
    await shell.page.getByText("Unreleased", { exact: true }).waitFor();
    await shell.page.getByRole("button", { name: "Close What’s New", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");
    const changelogFailure = await openPage(browser, baseUrl, makeFixtures(), { failChangelogOnce: true });
    await changelogFailure.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await changelogFailure.page.getByRole("button", { name: "Retry", exact: true }).click();
    await changelogFailure.page.getByText("Unreleased", { exact: true }).waitFor({ state: "visible" });
    await changelogFailure.context.close();

    const delayed = await openPage(browser, baseUrl, makeFixtures(), { delayChangelogMs: 900, viewport: { width: 390, height: 844 }, isMobile: true });
    await delayed.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await delayed.page.getByRole("status").getByText("Loading release notes…").waitFor({ state: "visible" });
    await delayed.page.getByText("Unreleased", { exact: true }).waitFor({ state: "visible" });
    await delayed.context.close();

    if (process.env.LEARN_CAPTURE_SCREENSHOTS !== "0") {
      const captures = [
        { width: 390, height: 844, isMobile: true, id: "networks", title: "Kollab networks", file: "learn-page-390x844-networks.png" },
        { width: 820, height: 1180, isMobile: false, id: "agents", title: "Agents", file: "learn-page-820x1180-agents.png" },
        { width: 1280, height: 900, isMobile: false, id: "phone-access", title: "WireGuard phone access", file: "learn-page-1280x900-phone-access.png" },
      ];
      for (const capture of captures) {
        const captureFixtures = makeFixtures();
        const view = await openPage(browser, baseUrl, captureFixtures, { viewport: { width: capture.width, height: capture.height }, isMobile: capture.isMobile });
        await view.page.goto(`${baseUrl}/learn?topic=${capture.id}`, { waitUntil: "domcontentloaded" });
        await waitForLesson(view.page, capture.title);

        await view.page.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        const dimensions = await view.page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
        assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `horizontal overflow at ${capture.width}px`);
        const imagePath = path.join(screenshotDir, capture.file);
        fs.mkdirSync(path.dirname(imagePath), { recursive: true });
        await view.page.screenshot({ path: imagePath, animations: "disabled" });
        assert.deepEqual(view.pageErrors, []);
        assert.deepEqual(view.consoleErrors, []);
        await view.context.close();
        console.log(`Screenshot ${capture.width}x${capture.height}: ${capture.file}`);
      }
    }

    assert.ok(shell.externalRequests.length === 0, "browser route unexpectedly accessed an external origin");
    assert.ok(shell.pageErrors.length === 0, shell.pageErrors.join(" | "));
    console.log(`Mocked API requests: ${shell.fixture.requests.length}`);
    console.log("Learn browser acceptance passed; all API calls were mocked and no live engine was used.");
  } finally {
    await browser.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
