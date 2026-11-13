import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { chromium } from "playwright-core";
import { createServer } from "vite";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = process.env.LEARN_SCREENSHOT_DIR ?? path.join(os.tmpdir(), "kollab-learn-renderer-screenshots");
export function makeFixtures(options = {}) {
  let createdSession = null;
  let createPayload = null;
  let resolveCreate;
  const createRequest = new Promise((resolve) => {
    resolveCreate = resolve;
  });
  const requests = [];
  // The engine learns each harness in the background (#953): a harness stays "loading" (more) until it is learned,
  // and one that failed stays incomplete until its own retry or the picker's Retry (`?refresh=true`).
  let discoveryFailurePending = options.discoveryFailureOnce ?? false;
  let discoveryProgressHeld = options.discoveryProgressOnce ?? false;
  let discoveryRequests = 0;
  let refreshRequests = 0;

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
  // A number: that many profile switches fail, then they work. true: every one fails.
  let pickFailuresLeft = options.pickFails === true ? Infinity : (options.pickFails ?? 0);
  // Once held, profile switches wait until released, so a test can act while one is in flight.
  let pickHold = null;
  // The folders `GET /profiles?workspace=` was asked for, and a gate that holds those answers (#957).
  const profileListings = [];
  let folderHold = null;
  const holdFolderDefaults = () => {
    let release;
    folderHold = { promise: new Promise((resolve) => { release = resolve; }), release };
  };
  const releaseFolderDefaults = () => {
    folderHold?.release();
    folderHold = null;
  };
  const holdPicks = () => {
    let release;
    pickHold = { promise: new Promise((resolve) => { release = resolve; }), release };
  };
  const releasePicks = () => {
    pickHold?.release();
    pickHold = null;
  };
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
    requests.push({ method, endpoint, body: method === "POST" ? request.postData() : null });

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
    if (endpoint === "/profiles") {
      const folder = url.searchParams.get("workspace") ?? "";
      profileListings.push(folder);
      if (folder && folderHold) await folderHold.promise;
      // Each folder's own config can set its own default loadout; an unlisted folder has none.
      const byFolder = options.defaultLoadoutsByFolder;
      return json(route, byFolder ? { ...profiles, default_loadout: byFolder[folder] ?? null } : profiles);
    }
    if (endpoint === "/profiles/choices") {
      discoveryRequests += 1;
      if (url.searchParams.get("refresh") === "true") {
        refreshRequests += 1;
        discoveryFailurePending = false;
      }
      const more = discoveryProgressHeld;
      const status = more || discoveryFailurePending ? "partial" : "ready";
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
      const recent = (options.recentFolders ?? []).map((folder) => ({ path: folder, name: folder.split("/").pop(), live: 0 }));
      return json(route, { home: { path: "/learn-demo", name: "Learn demo", live: false }, recent });
    }
    if (endpoint === "/workspaces/browse") {
      const asked = url.searchParams.get("path") ?? "";
      return json(route, { path: asked, name: asked.split("/").pop(), parent: null, dirs: [] });
    }
    if (endpoint.endsWith("/history")) {
      const history = options.history ?? [];
      return json(route, {
        history,
        start_index: 0,
        end_index: history.length,
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
      if (pickHold) await pickHold.promise;
      if (pickFailuresLeft > 0) {
        pickFailuresLeft -= 1;
        return json(route, { detail: "The agent could not switch models." }, 502);
      }
      // A session put on a pick answers with what it now runs, as the engine does. A profile whose environment
      // sets the model answers with that one instead of the pick.
      const body = request.postDataJSON();
      return json(route, {
        ...(createdSession ?? {}),
        profile: body.name,
        model: options.environmentModels?.[body.name] ?? body.model,
        effort: body.effort,
      });
    }
    if (endpoint === "/config" && options.engineVersion) return json(route, { engine_version: options.engineVersion });
    if (endpoint === "/update/check" && options.update) {
      return json(route, { current_version: "0.0.1", available: true, release: options.update, notes: [] });
    }
    if (endpoint.startsWith("/sessions/")) {
      const requestedId = endpoint.split("/")[2];
      if (requestedId === remoteAgent.handle) {
        remoteSessionOpened = true;
        return json(route, remoteSession);
      }
      if (method === "POST" && endpoint.endsWith("/profile")) {
        // A session put on a pick answers with what it now runs, as the engine does.
        const body = request.postDataJSON();
        return json(route, { ...(createdSession ?? {}), profile: body.name, model: body.model, effort: body.effort });
      }
      return json(route, createdSession ?? {});
    }
    return json(route, {});
  }

  return {
    agents,
    createPayload: () => createPayload,
    discoveryRequests: () => discoveryRequests,
    finishDiscoveryProgress: () => {
      discoveryProgressHeld = false;
    },
    refreshRequests: () => refreshRequests,
    createRequest,
    network,
    // The bodies of the profile switches a page asked for, in order.
    profileSwitches: () =>
      requests
        .filter((request) => request.method === "POST" && request.endpoint.endsWith("/profile"))
        .map((request) => JSON.parse(request.body)),
    profiles,
    remoteAgent,
    requests,
    respond,
    session,
    holdPicks,
    releasePicks,
    holdFolderDefaults,
    releaseFolderDefaults,
    profileListings: () => profileListings,
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
    reducedMotion: options.reducedMotion ?? "no-preference",
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

async function until(check, message, timeoutMs = 60_000) {
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

async function verifyMobileLearnLayout(browser, baseUrl) {
  const failures = [];
  const checks = [
    {
      name: "mobile preview preserves readable native size and can pan",
      run: async (page) => {
        await page.goto(`${baseUrl}/learn?topic=networks`, { waitUntil: "domcontentloaded" });
        await waitForLesson(page, "Connect");
        const main = page.getByRole("main", { name: "Learn", exact: true });
        await main.getByRole("button", { name: "Enlarge product preview", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Product preview", exact: true });
        await dialog.waitFor();
        await dialog.evaluate(async (element) => {
          await Promise.all(element.getAnimations({ subtree: true }).map((animation) => animation.finished));
        });
        const image = dialog.locator("img");
        await image.evaluate((element) => element.decode());
        const size = await image.evaluate((element) => ({
          native: element.naturalWidth,
          rendered: element.getBoundingClientRect().width,
          viewport: innerWidth,
          scrollWidth: element.parentElement.scrollWidth,
          visibleWidth: element.parentElement.clientWidth,
        }));
        assert.ok(size.native > size.viewport, "this regression requires a product capture wider than the phone");
        assert.ok(size.rendered >= size.native - 1, `preview shrank from ${size.native}px to ${size.rendered}px`);
        assert.ok(size.scrollWidth > size.visibleWidth, "wide preview must pan inside its image viewport");
        const panned = await image.evaluate((element) => {
          element.parentElement.scrollLeft = 80;
          return element.parentElement.scrollLeft;
        });
        assert.ok(panned > 0, "preview cannot be panned horizontally");
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "hidden" });
        await waitForLesson(page, "Connect");
      },
    },
    {
      name: "mobile workflow labels remain fully visible and selectable",
      run: async (page) => {
        await page.goto(`${baseUrl}/learn?topic=devices`, { waitUntil: "domcontentloaded" });
        await waitForLesson(page, "Devices");
        const main = page.getByRole("main", { name: "Learn", exact: true });
        const tabs = main.getByRole("tablist", { name: "Devices workflows", exact: true });
        await tabs.waitFor();
        await page.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        const layout = await tabs.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return {
            visibleWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            labels: [...element.querySelectorAll('[role="tab"]')].map((tab) => {
              const box = tab.getBoundingClientRect();
              return { text: tab.textContent, left: box.left - bounds.left, right: box.right - bounds.left };
            }),
          };
        });
        assert.ok(layout.labels.length > 1, "this regression requires several workflows");
        assert.ok(layout.scrollWidth <= layout.visibleWidth + 1, "workflow choices extend beyond their visible row");
        for (const label of layout.labels) {
          assert.ok(label.left >= -1 && label.right <= layout.visibleWidth + 1, `${label.text} is clipped at phone width`);
        }
        const lastTab = tabs.getByRole("tab").last();
        await lastTab.click();
        await page.waitForFunction(() => document.querySelector('[aria-label="Devices workflows"] [role="tab"]:last-child')?.getAttribute("aria-selected") === "true");
      },
    },
  ];
  for (const check of checks) {
    const view = await openPage(browser, baseUrl, makeFixtures(), { viewport: { width: 390, height: 844 }, isMobile: true });
    try {
      await check.run(view.page);
      assert.deepEqual(view.pageErrors, []);
      assert.deepEqual(view.consoleErrors, []);
      assert.deepEqual(view.externalRequests, []);
      console.log(`PASS: ${check.name}`);
    } catch (error) {
      failures.push(error);
      console.error(`FAIL: ${check.name}: ${error.message}`);
    } finally {
      await view.context.close();
    }
  }
  if (failures.length) throw new AggregateError(failures, "Mobile Learn layout regressions");
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

    await verifyMobileLearnLayout(browser, baseUrl);

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
    await waitForLesson(shell.page, "Connect");
    await shell.page.keyboard.press("Escape");
    await shell.page.waitForFunction(() => location.pathname === "/" && document.activeElement?.id === "app-main-pane");

    await shell.page.getByTestId("new-session-options").click();
    // Help is a hover card on the control, never an inline link.
    assert.equal(await shell.page.getByRole("link", { name: /Learn about|Learn more/ }).count(), 0);
    const manageProfiles = shell.page.getByRole("button", { name: "Manage Profiles", exact: true });
    // Use the help control so the assertion is independent of hover dwell/animation timing.
    await manageProfiles.locator("..").getByRole("button", { name: "What is this?", exact: true }).click();
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
    // The shell asked for the list when it loaded (#953), so the picker opens on what the page already knows:
    // the engine is still learning one harness, which the picker says, and the list is not blank meanwhile.
    const loadingMore = shell.page.getByRole("status").filter({ hasText: "Loading more model options: review" });
    await loadingMore.waitFor({ state: "visible" });
    assert.equal(await shell.page.getByRole("status").filter({ hasText: "Loading models" }).count(), 0);
    // The harness finishes, but one of its models failed: the page keeps polling until the engine says it is done.
    fixtures.finishDiscoveryProgress();
    const discoveryWarning = shell.page.getByRole("status").filter({ hasText: "Some model options did not load: review" });
    await discoveryWarning.waitFor({ state: "visible" });
    // Opening the picker again asks quietly and never clears a failure on its own; only Retry (?refresh=true) does.
    assert.equal(fixtures.refreshRequests(), 0);
    await discoveryWarning.getByRole("button", { name: "Retry" }).click();
    await discoveryWarning.waitFor({ state: "hidden" });
    assert.equal(fixtures.refreshRequests(), 1);
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
      timeout: 60_000,
    });

    // A live mocked peer demonstrates the existing Hub-to-remote-session selection path.
    const remoteAgentButton = shell.page.getByRole("button", { name: "Remote Coder Idle", exact: true });
    await remoteAgentButton.click();
    // The session panel is populated only after the remote session and its history load.
    const remoteSessionName = sessionControls.getByText("Remote coder session", { exact: true });
    try {
      await remoteSessionName.waitFor({ state: "visible", timeout: 60_000 });
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
    await waitForLesson(shell.page, "Connect");
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
    await shell.page.goForward();
    await shell.page.waitForFunction(() => location.pathname === "/learn" && location.search === "?topic=agents");
    await waitForLesson(shell.page, "Agents");

    // Every current topic must load its JSON-backed workflow, not a blank placeholder.
    await shell.page.emulateMedia({ reducedMotion: "reduce" });

    for (const topicId of ["networks", "devices", "workspaces", "agents", "profiles", "context-compaction", "koordinator", "hub-messaging", "voice", "system-prompts", "phone-access"]) {
      await shell.page.goto(`${baseUrl}/learn?topic=${topicId}`, { waitUntil: "domcontentloaded" });
      const learn = shell.page.getByRole("main", { name: "Learn", exact: true });
      const heading = learn.getByRole("heading", { level: 1 });
      await heading.waitFor({ state: "visible" });
      assert.notEqual(await heading.innerText(), "That topic is not available", `${topicId} route unexpectedly unresolved`);
      await shell.page.getByRole("tabpanel").first().waitFor({ state: "visible" });
      assert.ok((await shell.page.getByRole("tabpanel").first().innerText()).trim().length > 0, `${topicId} workflow is empty`);
      const topicMedia = learn.locator("img, video").first();
      await topicMedia.waitFor({ state: "visible" });
      if (await topicMedia.evaluate((element) => element instanceof HTMLImageElement)) {
        await shell.page.waitForFunction((id) => {
          const image = document.querySelector('main[aria-label="Learn"] img');
          return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0 && image.src.endsWith(`/learn/${id}/overview.webp`);
        }, topicId);
      }

      const workflowTabs = learn.getByRole("tab");
      if (await workflowTabs.count() > 1) {
        await workflowTabs.first().focus();
        await shell.page.keyboard.press("ArrowRight");
        await shell.page.waitForFunction(() => document.querySelectorAll('main[aria-label="Learn"] [role="tab"]')[1]?.getAttribute("aria-selected") === "true");
        assert.equal(await workflowTabs.nth(1).getAttribute("aria-selected"), "true", `${topicId} workflow tabs are not keyboard navigable`);
      }
      const reducedMotion = await shell.page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      const video = shell.page.locator("main video").first();
      if (reducedMotion && await video.count()) {
        assert.equal(await video.getAttribute("autoplay"), null, `${topicId} video must not autoplay`);
        assert.equal(await video.evaluate((element) => element.paused), true, `${topicId} video unexpectedly plays on load`);
      }
    }
    await shell.page.emulateMedia({ reducedMotion: "no-preference" });

    // The walkthrough starts from a readable poster; reduced motion waits for explicit play.
    await shell.page.emulateMedia({ reducedMotion: "reduce" });
    await shell.page.goto(`${baseUrl}/learn?topic=networks`, { waitUntil: "domcontentloaded" });
    await waitForLesson(shell.page, "Connect");
    // Escape pressed as soon as the enlarged preview opens closes only that preview, never the Learn dialog.
    await shell.page.getByRole("button", { name: "Enlarge product preview", exact: true }).click();
    await shell.page.getByRole("dialog", { name: "Product preview", exact: true }).waitFor();
    await shell.page.keyboard.press("Escape");
    await shell.page.getByRole("dialog", { name: "Product preview", exact: true }).waitFor({ state: "hidden" });
    assert.equal(new URL(shell.page.url()).pathname, "/learn", "Escape on the enlarged preview also closed Learn");
    await waitForLesson(shell.page, "Connect");
    assert.equal(await shell.page.locator("main video").count(), 0);
    await shell.page.getByRole("button", { name: "Watch walkthrough", exact: true }).click();
    const walkthrough = shell.page.locator("main video");
    await walkthrough.waitFor();
    await walkthrough.evaluate(async (video) => {
      if (!video.readyState) await new Promise((resolve, reject) => { video.onloadedmetadata = resolve; video.onerror = reject; });
    });
    assert.equal(await walkthrough.evaluate((video) => video.paused && video.controls && video.playsInline), true);
    assert.equal(await walkthrough.getAttribute("autoplay"), null);
    assert.equal(await walkthrough.locator('track[kind="captions"]').count(), 1);
    await walkthrough.evaluate((video) => video.play());
    await shell.page.waitForFunction(() => document.querySelector("main video")?.currentTime > 0.2);
    await shell.page.emulateMedia({ reducedMotion: "no-preference" });
    await shell.page.emulateMedia({ reducedMotion: "reduce" });
    await shell.page.waitForFunction(() => document.querySelector("main video")?.paused);
    await shell.page.getByRole("button", { name: "Show preview", exact: true }).click();
    assert.equal(await shell.page.locator("main video").count(), 0);
    await shell.page.getByRole("button", { name: "Enlarge product preview", exact: true }).click();
    const enlargedPreview = shell.page.getByRole("dialog", { name: "Product preview", exact: true });
    await enlargedPreview.waitFor();
    await enlargedPreview.locator("img").evaluate((image) => image.decode());
    await shell.page.keyboard.press("Escape");
    await enlargedPreview.waitFor({ state: "hidden" });
    await waitForLesson(shell.page, "Connect");
    await shell.page.emulateMedia({ reducedMotion: "no-preference" });

    // A failed overview image has an actionable recovery and a real retry re-requests the asset.
    const mediaRecovery = await openPage(browser, baseUrl, makeFixtures());
    let failOverviewOnce = true;
    await mediaRecovery.page.route("**/learn/devices/overview.webp", async (route) => {
      if (failOverviewOnce) {
        failOverviewOnce = false;
        await route.abort();
      } else {
        await route.continue();
      }
    });
    await mediaRecovery.page.goto(`${baseUrl}/learn?topic=devices`, { waitUntil: "domcontentloaded" });
    await waitForLesson(mediaRecovery.page, "Devices");
    const mediaError = mediaRecovery.page.getByRole("group", { name: "Lesson media recovery" });
    await mediaError.getByRole("status").waitFor({ state: "visible" });
    await mediaError.getByRole("button", { name: "Try again" }).click();
    const restoredImage = mediaRecovery.page.locator('main img[src="/learn/devices/overview.webp"]');
    await restoredImage.waitFor({ state: "visible" });
    await mediaRecovery.page.waitForFunction(() => {
      const image = document.querySelector('main img[src="/learn/devices/overview.webp"]');
      return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0;
    });
    await mediaRecovery.context.close();


    await shell.page.goto(`${baseUrl}/learn?topic=not-a-topic`, { waitUntil: "domcontentloaded" });
    await shell.page.getByRole("heading", { name: "That topic is not available" }).waitFor({ state: "visible" });
    await shell.page.getByRole("button", { name: "Browse topics" }).click();
    await shell.page.waitForFunction(() => location.search === "?topic=networks");
    await shell.page.getByRole("button", { name: "Close Learn", exact: true }).click();
    await shell.page.waitForFunction(() => location.pathname === "/");

    // New Session starts on the default loadout, not on the active profile's own saved model (#957).
    const defaultLoadout = { name: "haiku-low", profile: "review", provider: "anthropic", model: "claude-haiku", effort: "low" };
    const onDefault = makeFixtures({ defaultLoadout });
    const defaultView = await openPage(browser, baseUrl, onDefault);
    await defaultView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await defaultView.page.getByTestId("new-session-options").click();
    // The active profile is "default" (gpt-6-luna); the form shows the loadout before Start.
    await defaultView.page.locator("#new-session-model", { hasText: "claude-haiku" }).waitFor({ state: "visible", timeout: 5000 });
    assert.doesNotMatch(await defaultView.page.locator("#new-session-model").innerText(), /gpt-6-luna/);
    const switchAnswered = defaultView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await defaultView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const defaultPayload = await Promise.race([
      onDefault.createRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Default-loadout launch request did not arrive.")), 15000)),
    ]);
    assert.equal(defaultPayload.profile, "review");
    const switches = await until(() => onDefault.profileSwitches().length && onDefault.profileSwitches(), "The loadout's model never reached the session.");
    assert.deepEqual(switches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    // The session adopted the pick: the engine's answer to the switch carries the loadout's model and level.
    // The app compares that answer with the pick and flags a session that kept something else.
    const answered = await (await switchAnswered).json();
    assert.equal(answered.model, "claude-haiku");
    assert.equal(answered.effort, "low");
    await defaultView.context.close();

    // A default loadout on a profile that no longer exists leaves the form on the active profile.
    const staleFixtures = makeFixtures({ defaultLoadout: { ...defaultLoadout, profile: "deleted" } });
    const stale = await openPage(browser, baseUrl, staleFixtures);
    await stale.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await stale.page.getByTestId("new-session-options").click();
    await stale.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    await stale.context.close();

    // A default loadout belongs to the folder a session starts in: the engine's folder may set one and another
    // folder its own. The form follows the folder picked, a deliberate model pick stays, and Start Session never
    // outruns the answer (the clean-room review of #961, round 3).
    const demoDefault = { name: "luna-max", profile: "default", provider: "openai_responses", model: "gpt-6-luna", effort: "max" };
    const otherFolderFixtures = (byFolder) =>
      makeFixtures({ defaultLoadoutsByFolder: byFolder, recentFolders: ["/learn-other"] });
    const chooseOtherFolder = async (page) => {
      await page.locator("#new-session-workspace").click();
      await page.locator('button[title="/learn-other"]').click();
    };
    const launchPayload = (fixture, what) =>
      Promise.race([
        fixture.createRequest,
        new Promise((_, reject) => setTimeout(() => reject(new Error(`${what} launch request did not arrive.`)), 15000)),
      ]);

    const perFolder = otherFolderFixtures({ "": demoDefault, "/learn-other": defaultLoadout });
    const folderView = await openPage(browser, baseUrl, perFolder);
    await folderView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await folderView.page.getByTestId("new-session-options").click();
    await folderView.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    await chooseOtherFolder(folderView.page);
    // The other folder sets claude-haiku: the form shows it, not the engine folder's gpt-6-luna.
    await folderView.page.locator("#new-session-model", { hasText: "claude-haiku" }).waitFor({ state: "visible", timeout: 5000 });
    assert.doesNotMatch(await folderView.page.locator("#new-session-model").innerText(), /gpt-6-luna/);
    assert.ok(perFolder.profileListings().includes("/learn-other"), "the other folder's default was never asked for");
    await folderView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const folderPayload = await launchPayload(perFolder, "Other-folder");
    assert.equal(folderPayload.workspace, "/learn-other");
    assert.equal(folderPayload.profile, "review");
    const folderSwitches = await until(() => perFolder.profileSwitches().length && perFolder.profileSwitches(), "The other folder's model never reached the session.");
    assert.deepEqual(folderSwitches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    await folderView.context.close();

    // A model picked by hand is not undone by a change of folder.
    const keptPick = otherFolderFixtures({ "/learn-other": demoDefault });
    const keptView = await openPage(browser, baseUrl, keptPick);
    await keptView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await keptView.page.getByTestId("new-session-options").click();
    await keptView.page.locator("#new-session-model").click();
    await keptView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await chooseOtherFolder(keptView.page);
    await keptView.page.waitForTimeout(500); // an answer that replaced the pick would be drawn by now
    assert.match(await keptView.page.locator("#new-session-model").innerText(), /claude-haiku/);
    await keptView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const keptPayload = await launchPayload(keptPick, "Kept-pick");
    assert.equal(keptPayload.workspace, "/learn-other");
    assert.equal(keptPayload.profile, "review");
    await keptView.context.close();

    // Start Session pressed before the new folder's default has arrived still starts on that default.
    const racing = otherFolderFixtures({ "": demoDefault, "/learn-other": defaultLoadout });
    const racingView = await openPage(browser, baseUrl, racing);
    await racingView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await racingView.page.getByTestId("new-session-options").click();
    await racingView.page.locator("#new-session-model", { hasText: "gpt-6-luna" }).waitFor({ state: "visible", timeout: 5000 });
    racing.holdFolderDefaults();
    await chooseOtherFolder(racingView.page);
    await racingView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    await racingView.page.waitForTimeout(300);
    assert.equal(racing.createPayload(), null, "Start Session outran the other folder's default");
    racing.releaseFolderDefaults();
    const racingPayload = await launchPayload(racing, "Racing");
    assert.equal(racingPayload.workspace, "/learn-other");
    assert.equal(racingPayload.profile, "review");
    const racingSwitches = await until(() => racing.profileSwitches().length && racing.profileSwitches(), "The other folder's model never reached the session.");
    assert.deepEqual(racingSwitches, [{ name: "review", model: "claude-haiku", effort: "low" }]);
    await racingView.context.close();

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

    // A pick that fails after New Session started the chat leaves it on another model: say so in one compact
    // line and let the person try again (#956).
    const failing = await openPage(browser, baseUrl, makeFixtures({ pickFails: 1 }));
    await failing.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await failing.page.getByTestId("new-session-options").click();
    await failing.page.locator("#new-session-model").click();
    await failing.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await failing.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    await failing.page.getByRole("complementary", { name: "Session Controls", exact: true })
      .getByText("Learn regression session", { exact: true }).waitFor({ state: "visible", timeout: 60_000 });
    const pickNotice = failing.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await pickNotice.waitFor({ state: "visible", timeout: 5_000 });
    const pickText = await pickNotice.innerText();
    assert.match(pickText, /Couldn't switch to claude-haiku\. .*could not switch models/);
    // What a person reads has no issue numbers, versions or build ids.
    assert.doesNotMatch(pickText, /#\d|\bv?\d+\.\d+\.\d+|[0-9a-f]{7,}/);
    assert.ok((await pickNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    // Try Again puts the session on the pick; the fixture answers it this time, so the notice goes away.
    await pickNotice.getByRole("button", { name: "Try Again", exact: true }).click();
    await pickNotice.waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(
      failing.fixture.requests.filter((request) => request.method === "POST" && request.endpoint.endsWith("/profile")).length,
      2,
    );
    await failing.context.close();

    // A page and an engine on different versions: one compact line with a Reload button. The versions
    // are a hover away, never in the line.
    const mismatch = await openPage(browser, baseUrl, makeFixtures({ engineVersion: "999.1.0+deadbeef" }));
    await mismatch.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const versionNotice = mismatch.page.getByRole("status")
      .filter({ has: mismatch.page.getByRole("button", { name: "Reload", exact: true }) });
    await versionNotice.waitFor({ state: "visible", timeout: 5_000 });
    assert.doesNotMatch(await versionNotice.innerText(), /999|deadbeef|\bv\d/);
    assert.match(await versionNotice.getAttribute("title"), /999\.1\.0\+deadbeef/);
    assert.ok((await versionNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    await versionNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await versionNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await mismatch.context.close();

    // The update notice is the same line, with its two actions.
    const updating = await openPage(browser, baseUrl, makeFixtures({ update: { version: "999.2.0", url: "https://example.invalid/notes" } }));
    await updating.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const updateNotice = updating.page.getByRole("status").filter({ hasText: "999.2.0" });
    await updateNotice.getByRole("button", { name: "Update now", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await updateNotice.getByRole("link", { name: "Release notes", exact: true }).waitFor({ state: "visible" });
    assert.ok((await updateNotice.boundingBox()).height <= 40, "the notice is one compact line at desktop width");
    await updateNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await updateNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await updating.context.close();

    // A Try Again still in flight when its notice is dismissed must not bring the notice back when it fails
    // (the clean-room review of #959).
    const heldRetry = makeFixtures({ pickFails: true });
    const heldView = await openPage(browser, baseUrl, heldRetry);
    await heldView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await heldView.page.getByTestId("new-session-options").click();
    await heldView.page.locator("#new-session-model").click();
    await heldView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await heldView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const heldNotice = heldView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await heldNotice.waitFor({ state: "visible", timeout: 60_000 });
    heldRetry.holdPicks();
    await heldNotice.getByRole("button", { name: "Try Again", exact: true }).click();
    await heldNotice.getByRole("button", { name: "Trying…", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await heldNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
    await heldNotice.waitFor({ state: "hidden", timeout: 5_000 });
    // The held switch now fails, and the page reloads the sessions right after its catch has run. Record the
    // order the page saw (the answer, then a session list request after it) so the assertion below waits for the
    // retry's own end: any earlier session poll would not do.
    const heldEvents = [];
    heldView.page.on("response", (response) => {
      if (response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile")) {
        heldEvents.push("answer");
      }
    });
    heldView.page.on("request", (request) => {
      if (request.method() === "GET" && new URL(request.url()).pathname.endsWith("/sessions")) heldEvents.push("list");
    });
    heldRetry.releasePicks();
    const heldListedAfterAnswer = () => {
      const answeredAt = heldEvents.indexOf("answer");
      return answeredAt >= 0 && heldEvents.indexOf("list", answeredAt) >= 0;
    };
    const heldDeadline = Date.now() + 10_000;
    while (!heldListedAfterAnswer()) {
      assert.ok(Date.now() < heldDeadline, "the held switch was never answered and followed by a session reload");
      await heldView.page.waitForTimeout(25);
    }
    await heldView.page.waitForTimeout(300); // the notice, if it came back, is drawn by now
    assert.equal(
      await heldView.page.getByRole("alert").filter({ hasText: "Couldn't switch to" }).count(),
      0,
      "a dismissed notice came back when its retry failed",
    );
    await heldView.context.close();

    // A model change made by hand that fails leaves the chat on the model it runs, so the notice for the failed
    // New Session pick stays: it and Try Again are the way back to that pick (the clean-room review of #959).
    const manualChange = makeFixtures({ pickFails: true });
    const manualView = await openPage(browser, baseUrl, manualChange);
    await manualView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await manualView.page.getByTestId("new-session-options").click();
    await manualView.page.locator("#new-session-model").click();
    await manualView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await manualView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const manualNotice = manualView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await manualNotice.waitFor({ state: "visible", timeout: 60_000 });
    const manualPanel = manualView.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await manualPanel.getByRole("button", { name: "Model", exact: true }).click();
    const manualAnswer = manualView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await manualView.page.getByRole("group", { name: "default", exact: true })
      .getByRole("option", { name: /gpt-6-luna/ }).click();
    assert.equal((await manualAnswer).status(), 502, "the model change made by hand was meant to fail");
    await manualView.page.waitForTimeout(400);
    await manualNotice.waitFor({ state: "visible", timeout: 5_000 });
    await manualNotice.getByRole("button", { name: "Try Again", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await manualView.context.close();

    // A model change made by hand that only partly applies (the profile's environment sets another model) leaves
    // the chat on something the notice for the failed New Session pick no longer describes: the notice goes
    // (the clean-room review of #959, round 2).
    const partialChange = makeFixtures({ pickFails: 1, environmentModels: { default: "environment-model" } });
    const partialView = await openPage(browser, baseUrl, partialChange);
    await partialView.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await partialView.page.getByTestId("new-session-options").click();
    await partialView.page.locator("#new-session-model").click();
    await partialView.page.getByRole("group", { name: "review", exact: true })
      .getByRole("option", { name: /claude-haiku/ }).click();
    await partialView.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
    const partialNotice = partialView.page.getByRole("alert").filter({ hasText: "Couldn't switch to claude-haiku" });
    await partialNotice.waitFor({ state: "visible", timeout: 60_000 });
    const partialPanel = partialView.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await partialPanel.getByRole("button", { name: "Model", exact: true }).click();
    const partialAnswer = partialView.page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/profile"),
    );
    await partialView.page.getByRole("group", { name: "default", exact: true })
      .getByRole("option", { name: /gpt-6-luna/ }).click();
    assert.equal((await partialAnswer).status(), 200, "the profile switch itself was meant to apply");
    await partialNotice.waitFor({ state: "hidden", timeout: 5_000 });
    await partialView.context.close();

    if (process.env.LEARN_CAPTURE_SCREENSHOTS !== "0") {
      const captures = [
        { width: 390, height: 844, isMobile: true, id: "networks", title: "Connect", file: "learn-page-390x844-networks.png" },
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
