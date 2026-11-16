// Deterministic browser regression for remote chat history across sends,
// session switches, reloads, and a remote state/history timeout (#880).
// Uses the real App and a synthetic engine API; it never contacts a real relay.
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";
import { makeFixtures, openVite } from "./learn-browser-acceptance.mjs";

const timeout = 15_000;
const localSession = {
  session_id: "local-lifecycle-session",
  name: "Local Lifecycle Session",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/learn-demo",
  active: true,
};
const remoteId = "remote-session-1";
const prompt = "LIFECYCLE_REMOTE_PROMPT";
const reply = "LIFECYCLE_REMOTE_REPLY";
const history = [];
const historyReads = [];
const transportRequests = [];
let failNextHistoryRead = false;

function json(route, value, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

function historyPage() {
  return {
    history: structuredClone(history),
    conversation_uid: "remote-conversation-880",
    start_index: 0,
    end_index: history.length,
    has_more_older: false,
    older_cursor: null,
    has_more_newer: false,
    newer_cursor: null,
    source: "mirror",
    stale: false,
    last_turn_error: null,
  };
}

function sseFrame(chunk) {
  return `data: ${JSON.stringify(chunk)}\n\n`;
}

async function cachedRemoteHistory(page) {
  return page.evaluate(async (sessionId) => {
    const databases = await indexedDB.databases();
    if (!databases.some((database) => database.name === "kollab-history-pages-v1")) return null;
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("kollab-history-pages-v1");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const item = await new Promise((resolve, reject) => {
      const request = database.transaction("sessions", "readonly").objectStore("sessions").get(sessionId);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    return item?.pages?.flatMap((entry) => entry.history ?? []) ?? [];
  }, remoteId);
}

async function openPageInContext(context, baseUrl, fixtures) {
  await context.addInitScript(() => localStorage.setItem("kollab.setup-guide.v1", "dismissed"));
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const externalRequests = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const engine = url.port === "7433" || url.port === "8080";
    const sameOriginFetch = url.origin === baseUrl && ["fetch", "xhr"].includes(request.resourceType());
    if (engine || sameOriginFetch) return fixtures.respond(route);
    if (url.origin === baseUrl) return route.continue();
    externalRequests.push(url.href);
    return route.abort();
  });
  await page.route("**/*remote-session-1/history*", async (route) => {
    historyReads.push({ method: route.request().method(), url: route.request().url() });
    if (failNextHistoryRead) {
      failNextHistoryRead = false;
      await json(route, { detail: "synthetic remote state RPC timeout" }, 502);
      return;
    }
    await json(route, historyPage());
  });
  await page.route("**/*remote-session-1/events", () => new Promise(() => {}));
  await page.route("**/*remote-session-1/assistant", async (route) => {
    const request = route.request().postDataJSON();
    transportRequests.push(request);
    const content = request.commands?.find((command) => command.type === "add-message")
      ?.message?.parts?.find((part) => part.type === "text")?.text;
    assert.equal(content, prompt, "UI transport must send the exact submitted user text");
    history.push({ role: "user", content: prompt, timestamp: "2026-11-08T12:00:00Z" });
    history.push({ role: "assistant", content: reply, timestamp: "2026-11-08T12:00:01Z" });
    const body = [
      sseFrame({ type: "part-start", part: { type: "text" } }),
      sseFrame({ type: "text-delta", path: [0], textDelta: reply }),
      sseFrame({
        type: "update-state",
        operations: [
          { type: "set", path: ["messages"], value: [
            { id: "synthetic-user-880", role: "user", content: prompt },
            { id: "synthetic-assistant-880", role: "assistant", content: [{ type: "text", text: reply }] },
          ] },
          { type: "set", path: ["synced"], value: null },
          { type: "set", path: ["usage"], value: { inputTokens: 1, outputTokens: 1, toolCalls: 0, stopReason: "end_turn" } },
        ],
      }),
      sseFrame({ type: "part-finish", path: [0] }),
      sseFrame({ type: "message-finish", finishReason: "stop" }),
      "data: [DONE]\n\n",
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });
  return { page, pageErrors, consoleErrors, externalRequests };
}

async function main() {
  const { server, baseUrl } = await openVite();
  let context;
  let profileDir;
  let shell;
  let fixtures;
  let launchOptions;
  try {
    profileDir = await mkdtemp(path.join(os.tmpdir(), "kollab-remote-chat-lifecycle-"));
    launchOptions = {
      headless: true,
      ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
      viewport: { width: 1280, height: 900 },
    };
    context = await chromium.launchPersistentContext(profileDir, launchOptions);
    fixtures = makeFixtures({ sessions: [localSession] });
    shell = await openPageInContext(context, baseUrl, fixtures);
    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const controls = shell.page.getByRole("complementary", { name: "Session Controls" });
    await controls.getByText(localSession.name, { exact: true }).waitFor({ timeout });

    await shell.page.locator('[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]').click();
    await controls.getByText("Remote coder session", { exact: true }).waitFor({ timeout });
    const composer = shell.page.getByRole("textbox", { name: "Message input" });
    await composer.fill(prompt);
    await composer.press("Enter");
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.equal(transportRequests.length, 1, "one remote assistant-transport request must be sent");
    assert.deepEqual(history.map((row) => row.content), [prompt, reply]);

    // Switching away and back exercises the history fetch on session open.
    await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${localSession.name}"]`).click();
    await shell.page.locator('[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]').click();
    await controls.getByText("Remote coder session", { exact: true }).waitFor({ timeout });
    await shell.page.waitForTimeout(500);
    console.log("remote reselection observation:", JSON.stringify({
      activeRows: await shell.page.locator('[data-sidebar="sidebar"] button[data-active="true"]').evaluateAll((rows) => rows.map((row) => row.getAttribute("title"))),
      remoteSessionRequests: fixtures.requests.filter((request) => request.endpoint.includes(remoteId)),
      sessionListReads: fixtures.requests.filter((request) => request.endpoint === "/sessions" && request.method === "GET").length,
      composerPlaceholder: await shell.page.locator(".aui-composer-input").getAttribute("placeholder"),
    }));
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });

    // Reopening the page exercises the saved browser history cache. A failed
    // remote read must not erase the conversation that was already verified.
    failNextHistoryRead = true;
    await shell.page.reload({ waitUntil: "domcontentloaded" });
    await controls.getByText(localSession.name, { exact: true }).waitFor({ timeout });
    await shell.page.locator('[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]').click();
    await shell.page.locator(".aui-composer-input").waitFor({ state: "visible", timeout });
    await shell.page.waitForFunction(() =>
      document.querySelector(".aui-composer-input")?.getAttribute("placeholder") === "Message Remote Coder…",
      null,
      { timeout },
    );
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.ok(historyReads.length >= 3, `expected open/switch/reopen history reads, got ${historyReads.length}`);
    const cachedHistory = await cachedRemoteHistory(shell.page);
    assert.deepEqual(cachedHistory?.map((row) => row.content), [prompt, reply], "IndexedDB must retain the verified remote conversation");
    assert.ok(shell.externalRequests.length === 0, `unexpected external calls: ${shell.externalRequests.join(", ")}`);
    assert.deepEqual(shell.pageErrors, [], shell.pageErrors.join(" | "));
    const unmodeledConsoleErrors = shell.consoleErrors.filter((error) =>
      !error.includes("Failed to load resource: the server responded with a status of 502 (Bad Gateway)"),
    );
    assert.deepEqual(unmodeledConsoleErrors, [], unmodeledConsoleErrors.join(" | "));

    // Close and relaunch Chromium with the same isolated profile. Force another
    // history 502 so the visible reply must come from the persisted browser cache.
    await context.close();
    failNextHistoryRead = true;
    context = await chromium.launchPersistentContext(profileDir, launchOptions);
    shell = await openPageInContext(context, baseUrl, fixtures);
    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await shell.page.getByRole("complementary", { name: "Session Controls" })
      .getByText(localSession.name, { exact: true }).waitFor({ timeout });
    const acrossRestart = await cachedRemoteHistory(shell.page);
    assert.deepEqual(acrossRestart?.map((row) => row.content), [prompt, reply], "browser recreation must preserve the isolated IndexedDB history");
    await shell.page.locator('[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]').click();
    await shell.page.waitForFunction(() =>
      document.querySelector(".aui-composer-input")?.getAttribute("placeholder") === "Message Remote Coder…",
      null,
      { timeout },
    );
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.ok(historyReads.length >= 4, `expected a history read after browser recreation, got ${historyReads.length}`);
    assert.ok(shell.externalRequests.length === 0, `unexpected external calls after recreation: ${shell.externalRequests.join(", ")}`);
    assert.deepEqual(shell.pageErrors, [], shell.pageErrors.join(" | "));
    const restartConsoleErrors = shell.consoleErrors.filter((error) =>
      !error.includes("Failed to load resource: the server responded with a status of 502 (Bad Gateway)"),
    );
    assert.deepEqual(restartConsoleErrors, [], restartConsoleErrors.join(" | "));

    console.log(`PASS remote chat lifecycle: exact send/reply, switch away/back, reload + Chromium profile recreation with two synthetic history 502s; ${historyReads.length} history reads; dynamic Vite port ${new URL(baseUrl).port}; no external calls`);
  } catch (error) {
    const diagnostics = {
      historyReads,
      transportRequests,
      pageErrors: shell?.pageErrors,
      consoleErrors: shell?.consoleErrors,
      remoteSessionRequests: fixtures.requests.filter((request) => request.endpoint.includes(remoteId)),
      sessionListReads: fixtures.requests.filter((request) => request.endpoint === "/sessions" && request.method === "GET").length,
      sidebarRows: await shell?.page.locator('[data-sidebar="sidebar"] button').evaluateAll((rows) => rows.map((row) => ({ text: row.innerText, title: row.getAttribute("title"), active: row.getAttribute("data-active") }))).catch(() => []),
      body: await shell?.page.locator("body").innerText().catch(() => "<unavailable>"),
    };
    console.error("remote chat lifecycle failure:", JSON.stringify(diagnostics, null, 2));
    await shell?.page.screenshot({ path: path.join(process.env.KOLLAB_BROWSER_ARTIFACT_DIR || os.tmpdir(), "remote-chat-lifecycle-failure.png") }).catch(() => {});
    throw error;
  } finally {
    await context?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
