import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { chromium } from "playwright-core";
import { createServer } from "vite";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = path.join(repoRoot, "docs/screenshots");
const topics = [
  { id: "networks", title: "Kollab networks" },
  { id: "agents", title: "Agents" },
  { id: "phone-access", title: "PhoneLink" },
];

export function makeFixtures(options = {}) {
  let createdSession = null;
  let createPayload = null;
  let resolveCreate;
  const createRequest = new Promise((resolve) => {
    resolveCreate = resolve;
  });
  const requests = [];
  let discoveryFailurePending = options.discoveryFailureOnce ?? false;
  let discoveryProgressPending = options.discoveryProgressOnce ?? false;
  let discoveryRequests = 0;

  const remoteAgent = {
    name: "remote-coder",
    device: "remote-build",
    network: "learn-test",
    workspace: "/remote/demo",
    handle: "remote-session-1",
    state: "idle",
    last_seen: 1,
  };
  const remoteAgents = [remoteAgent, ...(options.additionalRemoteAgents ?? [])];
  const network = {
    device: "learn-laptop",
    folder: "/learn-demo",
    live: true,
    remote: remoteAgents,
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
            workspaces: [{ label: "/remote/demo", agents: remoteAgents }],
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
  const poolEntries = options.poolEntries ?? [localCoder];
  const initialSessions = options.sessions ?? [];
  const offlineAgents = [
    ...(options.offline ?? []),
    ...(options.additionalOfflineAgents ?? []),
  ];
  const agents = { agents: poolEntries, live: [], active: [], available: poolEntries.map((entry) => entry.name) };
  const profileSwitches = [];
  const profiles = {
    active: "default",
    providers: [],
    // The loadout the terminal would start on; null when none is set (#957).
    default_loadout: options.defaultLoadout ?? null,
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
  let remoteSessionOpened = false;
  const session = () => {
    const sessions = createdSession ? [createdSession] : initialSessions;
    return {
      sessions: remoteSessionOpened && !sessions.some((item) => item.session_id === remoteSession.session_id)
        ? [...sessions, remoteSession]
        : sessions,
      network,
      offline: offlineAgents,
      hidden: [],
    };
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
    if (endpoint === "/profiles/choices") {
      discoveryRequests += 1;
      const more = discoveryProgressPending;
      discoveryProgressPending = false;
      const status = more || discoveryFailurePending ? "partial" : "ready";
      if (!more) discoveryFailurePending = false;
      return json(route, {
        choices: profiles.profiles.map(({ name, provider, model }) => ({
          profile: name, provider, model, effort: "",
        })),
        discovery: { review: { status, more } },
      });
    }
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
    }
    if (endpoint.endsWith("/permissions")) return json(route, { pending_prompts: [], approval_mode: "trust_all" });
    if (endpoint.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    }
    if (method === "POST" && endpoint.endsWith("/profile")) {
      // A session put on a pick answers with what it now runs, as the engine does.
      const body = request.postDataJSON();
      profileSwitches.push(body);
      return json(route, { ...(createdSession ?? {}), profile: body.name, model: body.model, effort: body.effort });
    }
    if (endpoint.startsWith("/sessions/")) {
      const requestedId = endpoint.split("/")[2];
      if (requestedId === remoteAgent.handle) {
        remoteSessionOpened = true;
        return json(route, remoteSession);
      }
      return json(route, createdSession ?? {});
    }
    return json(route, {});
  }

  return {
    agents,
    createPayload: () => createPayload,
    discoveryRequests: () => discoveryRequests,
    createRequest,
    network,
    profileSwitches: () => profileSwitches,
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

export async function openVite() {
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

export async function openPage(browser, baseUrl, fixtures, options = {}) {
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

async function until(check, message, timeoutMs = 15_000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
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

    const fixtures = makeFixtures({ discoveryFailureOnce: true, discoveryProgressOnce: true });
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
    // Help is a hover card on the control, never an inline link.
    assert.equal(await shell.page.getByRole("link", { name: /Learn about|Learn more/ }).count(), 0);
    const manageProfiles = shell.page.getByRole("button", { name: "Manage Profiles", exact: true });
    await manageProfiles.hover();
    await shell.page.getByRole("tooltip").filter({ hasText: "A profile picks the provider" }).first().waitFor({ state: "visible" });

    // Exercise App -> AppSidebar -> ProfilesDialog, not just the isolated dialog fixture.
    await manageProfiles.click();
    const profilesDialog = shell.page.getByRole("dialog", { name: "Profiles", exact: true });
    await profilesDialog.waitFor({ state: "visible" });
    await profilesDialog.getByText("default", { exact: true }).waitFor({ state: "visible" });
    await profilesDialog.getByText("openai_responses · gpt-6-luna", { exact: true }).waitFor({ state: "visible" });
    await profilesDialog.getByRole("button", { name: "Close", exact: true }).click();

    await shell.page.getByRole("button", { name: "Agents", exact: true }).first().click();
    const agentLibrary = shell.page.getByRole("dialog", { name: "Agents", exact: true });
    assert.equal(await agentLibrary.getByRole("link", { name: /Learn more about/ }).count(), 0);
    await agentLibrary.getByRole("button", { name: "Close", exact: true }).click();


    await shell.page.getByTestId("new-session-options").click();
    await shell.page.locator("#new-session-model").click();
    const loadingMore = shell.page.getByRole("status").filter({ hasText: "Loading more model options: review" });
    await loadingMore.waitFor({ state: "visible" });
    const discoveryWarning = shell.page.getByRole("status").filter({ hasText: "Some model options did not load: review" });
    await discoveryWarning.waitFor({ state: "visible" });
    await discoveryWarning.getByRole("button", { name: "Retry" }).click();
    await discoveryWarning.waitFor({ state: "hidden" });
    assert.equal(fixtures.discoveryRequests(), 3);
    await shell.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
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

    // createRequest signals POST arrival before its response and the session history load finish.
    // Wait for the launched chat before starting remote selection, so the flows cannot overlap.
    const sessionControls = shell.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await sessionControls.getByText("Learn regression session", { exact: true }).waitFor({
      state: "visible",
      timeout: 15_000,
    });

    // A live mocked peer demonstrates the existing Hub-to-remote-session selection path.
    const remoteAgentButton = shell.page.getByRole("button", { name: "Remote Coder Idle", exact: true });
    await remoteAgentButton.click();
    // The session panel is populated only after the remote session and its history load.
    const remoteSessionName = sessionControls.getByText("Remote coder session", { exact: true });
    try {
      await remoteSessionName.waitFor({ state: "visible", timeout: 15_000 });
    } catch (reason) {
      const diagnostic = {
        remoteButtonActive: await remoteAgentButton.getAttribute("data-active"),
        remoteButtonText: await remoteAgentButton.innerText(),
        sessionControlsText: await sessionControls.innerText().catch(() => null),
        remoteRequests: fixtures.requests.filter((request) => request.endpoint.includes("remote-session-1")),
        recentSessionRequests: fixtures.requests.filter((request) => request.endpoint.startsWith("/sessions")).slice(-10),
        bodyText: (await shell.page.locator("body").innerText()).slice(0, 4000),
      };
      console.error("Remote session readiness diagnostics:", JSON.stringify(diagnostic, null, 2));
      throw reason;
    }
    assert.equal(await remoteAgentButton.getAttribute("data-active"), "true");
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
    assert.equal(await topics.getByRole("button").count(), 11);
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
    await shell.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/");

    // Release updates have their own route, close control, and retry path.
    await shell.page.getByRole("button", { name: "What’s New", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/whats-new");
    await shell.page.getByRole("heading", { level: 1, name: "What’s New", exact: true }).waitFor();
    // The first release section: "Unreleased" between releases, "Kollab X.Y.Z" right after one (#887).
    await shell.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await shell.page.getByRole("button", { name: "Close What’s New", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");
    const changelogFailure = await openPage(browser, baseUrl, makeFixtures(), { failChangelogOnce: true });
    await changelogFailure.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await changelogFailure.page.getByRole("button", { name: "Retry", exact: true }).click();
    await changelogFailure.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await changelogFailure.context.close();

    const delayed = await openPage(browser, baseUrl, makeFixtures(), { delayChangelogMs: 900, viewport: { width: 390, height: 844 }, isMobile: true });
    await delayed.page.goto(`${baseUrl}/whats-new`, { waitUntil: "domcontentloaded" });
    await delayed.page.getByRole("status").getByText("Loading release notes…").waitFor({ state: "visible" });
    await delayed.page.getByRole("heading", { level: 2 }).first().waitFor({ state: "visible" });
    await delayed.context.close();

    // New Session starts on the default loadout, not on the active profile's own saved model (#957).
    const defaultLoadout = { name: "haiku-low", profile: "review", provider: "anthropic", model: "claude-haiku", effort: "low" };
    const onDefault = makeFixtures({ defaultLoadout });
    const defaultView = await openPage(browser, baseUrl, onDefault);
    await defaultView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await defaultView.page.getByTestId("new-session-options").click();
    // The active profile is "default" (gpt-6-luna); the form shows the loadout before Start.
    await defaultView.page.locator("#new-session-model", { hasText: "claude-haiku" }).waitFor({ state: "visible", timeout: 5000 });
    assert.doesNotMatch(await defaultView.page.locator("#new-session-model").innerText(), /gpt-6-luna/);
    await defaultView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const defaultPayload = await Promise.race([
      onDefault.createRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Default-loadout launch request did not arrive.")), 15000)),
    ]);
    assert.equal(defaultPayload.profile, "review");
    const switches = await until(() => onDefault.profileSwitches().length && onDefault.profileSwitches(), "The loadout's model never reached the session.");
    assert.deepEqual(switches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    await defaultView.context.close();

    // A default loadout on a profile that no longer exists leaves the form on the active profile.
    const staleFixtures = makeFixtures({ defaultLoadout: { ...defaultLoadout, profile: "deleted" } });
    const stale = await openPage(browser, baseUrl, staleFixtures);
    await stale.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await stale.page.getByTestId("new-session-options").click();
    await stale.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    await stale.context.close();

    if (process.env.LEARN_CAPTURE_SCREENSHOTS !== "0") {
      const captures = [
        { width: 390, height: 844, isMobile: true, id: "networks", title: "Kollab networks", file: "learn-page-390x844-networks.png" },
        { width: 820, height: 1180, isMobile: false, id: "agents", title: "Agents", file: "learn-page-820x1180-agents.png" },
        { width: 1280, height: 900, isMobile: false, id: "phone-access", title: "PhoneLink", file: "learn-page-1280x900-phone-access.png" },
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

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error.stack ?? error);
    process.exitCode = 1;
  });
}
