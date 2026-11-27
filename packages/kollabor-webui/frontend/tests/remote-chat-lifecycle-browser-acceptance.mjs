// Deterministic browser regression for a remote chat across sends, session
// switches, reloads, and a remote read timeout (#880), on the one ordered chat
// stream (#1451). Uses the real App and a synthetic engine API; it never
// contacts a real relay.
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";
import { makeFixtures, openVite, until, waitForSessionReady } from "./learn-browser-acceptance.mjs";

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
const remoteButtonSelector = '[data-sidebar="sidebar"] button[title="Open Remote Coder on remote-build"]';
const prompt = "LIFECYCLE_REMOTE_PROMPT";
const reply = "LIFECYCLE_REMOTE_REPLY";
// The synthetic daemon's conversation: its rows and its ordered event log.
const conversation = { conversation_uid: "remote-conversation-880", generation: "remote-daemon-1" };
const history = [];
const log = [];
let lastSeq = 1;
const streamReads = [];
const transportRequests = [];
let snapshotsServed = 0;
let streamOutage = false;
const waiting = new Set();

function json(route, value, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

function emit(type, data) {
  lastSeq += 1;
  const row = type === "row.upsert" ? { ...data, seq: lastSeq } : data;
  if (type === "row.upsert") history.push(row);
  log.push({ ...conversation, seq: lastSeq, type, data: row });
}

function remoteTurn(inputId, text) {
  const turnId = "remote-turn-880";
  const turn = { turn_id: turnId, input_ids: [inputId] };
  const row = (id, kind, role, content, createdAt) => ({
    id, kind, role, turn_id: turnId, status: "completed", parts: [{ type: "text", text: content }], created_at: createdAt,
  });
  emit("input.state", { id: inputId, state: "queued", turn_id: null });
  emit("turn.state", { ...turn, state: "running" });
  emit("input.state", { id: inputId, state: "started", turn_id: turnId });
  emit("row.upsert", row(inputId, "input", "user", text, "2026-11-08T12:00:00Z"));
  emit("row.upsert", row("synthetic-reply-880", "reply", "assistant", reply, "2026-11-08T12:00:01Z"));
  emit("turn.state", { ...turn, state: "ended" });
  emit("input.state", { id: inputId, state: "ended", turn_id: turnId, outcome: "completed" });
  for (const wake of waiting) wake();
  waiting.clear();
}

// One stream request: a snapshot without a complete matching cursor, otherwise
// the events after it. A caught-up request is held until there is a new event.
async function serveStream(route) {
  const url = new URL(route.request().url());
  streamReads.push({ method: route.request().method(), url: url.href });
  if (streamOutage) {
    await json(route, { detail: "synthetic remote state RPC timeout" }, 502);
    return;
  }
  const after = url.searchParams.get("after");
  const matches = after !== null &&
    url.searchParams.get("conversation_uid") === conversation.conversation_uid &&
    url.searchParams.get("generation") === conversation.generation;
  let events;
  if (matches) {
    if (Number(after) >= lastSeq) await new Promise((resolve) => waiting.add(resolve));
    events = log.filter((event) => event.seq > Number(after));
  } else {
    snapshotsServed += 1;
    events = [{
      ...conversation,
      seq: lastSeq,
      type: "snapshot",
      data: { rows: structuredClone(history), next_seq: lastSeq + 1, older: null, turns: [], inputs: [] },
    }];
  }
  // The page may have moved on (session switch, reload) while the request waited.
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
  }).catch(() => {});
}

const rowTexts = (rows) => rows.map((row) => row.parts.map((part) => part.text).join(""));

async function cachedRemoteRows(page) {
  return page.evaluate(async (sessionId) => {
    const databases = await indexedDB.databases();
    if (!databases.some((database) => database.name === "kollab-chat-rows-v1")) return null;
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("kollab-chat-rows-v1");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const item = await new Promise((resolve, reject) => {
      const request = database.transaction("sessions", "readonly").objectStore("sessions").get(sessionId);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    return item?.rows ?? [];
  }, remoteId);
}

async function openPageInContext(context, baseUrl, fixtures) {
  await context.addInitScript(() => {
    localStorage.setItem("kollab.setup-guide.v1", JSON.stringify({ step: "done", dismissed: true }));
    localStorage.setItem("kollab.whats-new.v1", JSON.stringify({ seenVersion: "999.999.999" }));
  });
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
  await page.route(/\/sessions\/remote-session-1\/stream(\?|$)/, serveStream);
  await page.route("**/*remote-session-1/events", () => new Promise(() => {}));
  await page.route(/\/sessions\/remote-session-1\/inputs$/, async (route) => {
    const request = route.request().postDataJSON();
    transportRequests.push(request);
    assert.equal(request.text, prompt, "UI transport must send the exact submitted user text");
    await json(route, { id: request.id, state: "queued" }, 202);
    remoteTurn(request.id, request.text);
  });
  return { page, pageErrors, consoleErrors, externalRequests };
}

async function waitForLocalSession(page) {
  await waitForSessionReady(page, {
    sidebarTitleIncludes: localSession.name,
    sessionHeading: "Local Coder",
    sessionName: localSession.name,
    timeout,
  });
}

async function selectRemoteSession(page) {
  await waitForSessionReady(page, {
    sidebarTitleIncludes: "Open Remote Coder on remote-build",
    sessionHeading: "Remote Coder",
    sessionName: "Remote coder session",
    historyPath: `/sessions/${remoteId}/stream`,
    composerPlaceholder: "Message Remote Coder…",
    clickTarget: page.locator(remoteButtonSelector),
    timeout,
  });
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
    await waitForLocalSession(shell.page);

    await selectRemoteSession(shell.page);
    const composer = shell.page.getByRole("textbox", { name: "Message input" });
    await composer.fill(prompt);
    await composer.press("Enter");
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.equal(transportRequests.length, 1, "one remote input request must be sent");
    assert.deepEqual(rowTexts(history), [prompt, reply]);

    // Switching away and back exercises the stream snapshot on session open.
    await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${localSession.name}"]`).click();
    await waitForLocalSession(shell.page);
    await selectRemoteSession(shell.page);
    console.log("remote reselection observation:", JSON.stringify({
      activeRows: await shell.page.locator('[data-sidebar="sidebar"] button[data-active="true"]').evaluateAll((rows) => rows.map((row) => row.getAttribute("title"))),
      remoteSessionRequests: fixtures.requests.filter((request) => request.endpoint.includes(remoteId)),
      sessionListReads: fixtures.requests.filter((request) => request.endpoint === "/sessions" && request.method === "GET").length,
      composerPlaceholder: await shell.page.locator(".aui-composer-input").getAttribute("placeholder"),
    }));
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });

    // Reopening the page exercises the saved browser rows. A failed remote read
    // must not erase the conversation that was already verified.
    streamOutage = true;
    let servedBeforeOutage = snapshotsServed;
    await shell.page.reload({ waitUntil: "domcontentloaded" });
    await waitForLocalSession(shell.page);
    await selectRemoteSession(shell.page);
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    await shell.page.getByText(prompt, { exact: true }).first().waitFor({ timeout });
    assert.equal(snapshotsServed, servedBeforeOutage, "the reply shown during the outage must come from the saved rows");
    assert.ok(streamReads.length >= 3, `expected open/switch/reopen stream reads, got ${streamReads.length}`);
    const cachedRows = await cachedRemoteRows(shell.page);
    assert.deepEqual(cachedRows && rowTexts(cachedRows), [prompt, reply], "IndexedDB must retain the verified remote conversation");

    // The remote answers again: the reconnect's snapshot replaces the saved rows in place.
    streamOutage = false;
    await until(() => snapshotsServed > servedBeforeOutage, "the chat must reconnect after the outage", timeout);
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.equal(await shell.page.getByText(reply, { exact: true }).count(), 1, "the reply must stay one bubble");
    assert.ok(shell.externalRequests.length === 0, `unexpected external calls: ${shell.externalRequests.join(", ")}`);
    assert.deepEqual(shell.pageErrors, [], shell.pageErrors.join(" | "));
    const unmodeledConsoleErrors = shell.consoleErrors.filter((error) =>
      !error.includes("Failed to load resource: the server responded with a status of 502 (Bad Gateway)"),
    );
    assert.deepEqual(unmodeledConsoleErrors, [], unmodeledConsoleErrors.join(" | "));

    // Close and relaunch Chromium with the same isolated profile. Force another
    // stream 502 so the visible reply must come from the persisted browser rows.
    await context.close();
    streamOutage = true;
    servedBeforeOutage = snapshotsServed;
    const readsBeforeRestart = streamReads.length;
    context = await chromium.launchPersistentContext(profileDir, launchOptions);
    shell = await openPageInContext(context, baseUrl, fixtures);
    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await waitForLocalSession(shell.page);
    const acrossRestart = await cachedRemoteRows(shell.page);
    assert.deepEqual(acrossRestart && rowTexts(acrossRestart), [prompt, reply], "browser recreation must preserve the isolated IndexedDB history");
    await selectRemoteSession(shell.page);
    await shell.page.getByText(reply, { exact: true }).first().waitFor({ timeout });
    assert.equal(snapshotsServed, servedBeforeOutage, "the reply after recreation must come from the saved rows");
    assert.ok(streamReads.length >= 4 && streamReads.length > readsBeforeRestart,
      `expected a stream read after browser recreation, got ${streamReads.length}`);
    assert.ok(shell.externalRequests.length === 0, `unexpected external calls after recreation: ${shell.externalRequests.join(", ")}`);
    assert.deepEqual(shell.pageErrors, [], shell.pageErrors.join(" | "));
    const restartConsoleErrors = shell.consoleErrors.filter((error) =>
      !error.includes("Failed to load resource: the server responded with a status of 502 (Bad Gateway)"),
    );
    assert.deepEqual(restartConsoleErrors, [], restartConsoleErrors.join(" | "));

    console.log(`PASS remote chat lifecycle: exact send/reply, switch away/back, reload + Chromium profile recreation with two synthetic stream 502 outages; ${streamReads.length} stream reads; dynamic Vite port ${new URL(baseUrl).port}; no external calls`);
  } catch (error) {
    const diagnostics = {
      streamReads,
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
