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
    // Each suite gets its own cache dir (the runner provisions a unique TMPDIR
    // per suite) so concurrent suites never share the dep-optimizer cache in
    // node_modules/.vite, which serializes cold starts and can trigger
    // outdated-optimize-dep reloads mid-suite (#1022).
    cacheDir: path.join(process.env.TMPDIR ?? os.tmpdir(), "kollab-vite-cache"),
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

export async function until(check, message, timeoutMs = 60_000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}

export async function waitForLesson(page, title) {
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

// Every learn-suite script launches Chromium the same way. CHROME_PATH, when
// set, selects the binary; otherwise playwright's own registry install is used
// (the CI install step populates it). Linux runners need --no-sandbox.
export async function launchAcceptanceBrowser() {
  const executablePath = process.env.CHROME_PATH;
  return chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: process.platform === "linux" ? ["--no-sandbox"] : [],
  });
}

async function main() {
  const captureCodeHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  console.log(`Learn browser acceptance code head: ${captureCodeHead}`);
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

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
