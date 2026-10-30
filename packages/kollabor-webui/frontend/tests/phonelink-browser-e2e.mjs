// Real Chromium, real relay service, real signed app from a scratch PhoneLink host.
// Scratch ports + scratch HOME; never touches the user's engine or ~/.kollab.
//   node tests/phonelink-browser-e2e.mjs [--shots <dir>]
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../../..");
const shots = process.argv.includes("--shots") ? process.argv[process.argv.indexOf("--shots") + 1] : "";
import net from "node:net";
const freePort = () =>
  new Promise((resolve) => {
    const probe = net.createServer().listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
const relayPort = await freePort();
const controlPort = await freePort();
const origin = "https://relay.example";
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "phonelink-e2e-"));
const packages = ["", ...["ai", "tui", "events", "config", "agent", "plugins", "engine"].map((n) => `packages/kollabor-${n}/src`), "packages/kollabor-webui/src"];
const harness = spawn("python", ["tests/live/phonelink_harness.py", relayPort, controlPort, path.join(scratch, "s")], {
  cwd: repo,
  env: { ...process.env, HOME: scratch, KOLLAB_NO_KEYRING: "1", PYTHONPATH: packages.map((p) => path.join(repo, p)).join(":") },
  stdio: ["ignore", "pipe", "inherit"],
});
await new Promise((resolve, reject) => {
  harness.stdout.on("data", (chunk) => String(chunk).includes("ready") && resolve());
  harness.on("exit", () => reject(new Error("harness exited")));
});
console.error("harness ready");
const control = (method, route) => fetch(`http://127.0.0.1:${controlPort}${route}`, { method }).then((r) => r.json());

const waitForMetrics = async (predicate, timeout = 15_000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const metrics = await control("GET", "/metrics");
    if (predicate(metrics)) return metrics;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for synthetic PhoneLink history metrics.");
};

console.error("launching");
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined, args: ["--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks,BlockInsecurePrivateNetworkRequests"] });
let failed = null;
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  const page = await context.newPage();
  const fetched = [];

  const network = [];
  page.on("console", (m) => m.type() === "error" && console.error("page:", m.text()));
  page.on("pageerror", (e) => console.error("pageerror:", e.message));

  const httpErrors = [];
  page.on("response", (response) => {
    if (response.status() >= 400) httpErrors.push({ status: response.status(), url: response.url() });
  });

  page.on("requestfailed", (request) => network.push({ failed: request.url(), reason: request.failure()?.errorText }));
  page.on("websocket", (socket) => {
    network.push({ websocket: socket.url() });
    socket.on("framesent", (frame) => network.push({ sent: frame.payload?.length ?? 0 }));
    socket.on("framereceived", (frame) => network.push({ received: frame.payload?.length ?? 0 }));
    socket.on("close", () => network.push({ websocket_closed: true }));
  });
  // The page keeps its https origin (so the browser sends the right Origin header);
  // its requests and websocket are mapped to the scratch relay on loopback.
  await page.route(`${origin}/**`, async (route) => {
    const url = new URL(route.request().url());
    fetched.push(url.pathname);
    const response = await fetch(`http://127.0.0.1:${relayPort}${url.pathname}${url.search}`);
    const headers = Object.fromEntries(response.headers);
    // Test-only: allow the loopback socket this harness maps the relay's websocket to.
    if (headers["content-security-policy"])
      headers["content-security-policy"] = headers["content-security-policy"].replace("connect-src 'self'", `connect-src 'self' ws://127.0.0.1:${relayPort}`);
    await route.fulfill({ status: response.status, headers, body: Buffer.from(await response.arrayBuffer()) });
  });
  await context.addInitScript((port) => {
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url, protocols) {
        super(String(url).replace("wss://relay.example", `ws://127.0.0.1:${port}`), protocols);
      }
    };
  }, relayPort);
  // PHONE_E2E_SEED_LINK=1 pairs the way older loaders did (the raw seed in localStorage),
  // then relaunches with sealed keys allowed: the link moves into WebCrypto keys.
  const seedLink = process.env.PHONE_E2E_SEED_LINK === "1";
  let sealKeys = !seedLink;
  await context.exposeFunction("e2eSealKeys", () => sealKeys);
  await context.addInitScript(() => {
    const importKey = crypto.subtle.importKey.bind(crypto.subtle);
    crypto.subtle.importKey = async (format, ...rest) => {
      if (format === "pkcs8" && !(await window.e2eSealKeys())) throw new Error("older loader");
      return importKey(format, ...rest);
    };
  });
  const link = () =>
    page.evaluate(async () => {
      const identity = JSON.parse(localStorage.getItem("kollab.phonelink.identity.v1") ?? "null");
      const keys = await new Promise((resolve, reject) => {
        const open = indexedDB.open("kollab-phonelink-keys", 1);
        open.onupgradeneeded = () => open.result.createObjectStore("keys");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const store = open.result.transaction("keys").objectStore("keys");
          const ids = store.getAllKeys();
          const record = store.get(identity?.id ?? "-");
          record.onerror = () => reject(record.error);
          record.onsuccess = () => {
            const value = record.result;
            resolve({ ids: ids.result.length, sign: value?.sign.extractable ?? null, box: value?.box.extractable ?? null });
            open.result.close();
          };
        };
      });
      return { seed: typeof identity?.seed === "string", key: identity?.key ?? null, ...keys };
    });
  const shot = async (name) => shots && (fs.mkdirSync(shots, { recursive: true }), page.screenshot({ path: path.join(shots, `${name}.png`) }));

  console.error("page ready");
  const offer = await control("POST", "/offer");
  await page.goto(`${origin}/relay/v1/phone/#${offer.fragment}`);
  await page.getByText("Your Computer's Fingerprint").waitFor().catch(async (e) => { console.error("BODY:", await page.locator("body").innerText()); throw e; });
  const text = await page.locator("main").innerText();
  assert.match(text, /[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}/, "12-character pairing code");
  assert.ok(text.includes(offer.fingerprint), "phone shows the computer's fingerprint");
  const status = await control("GET", "/status");
  assert.equal(status.phones[0].fingerprint, offer.fingerprint, "computer and phone agree");
  await shot("phonelink-pairing-390");

  await control("POST", "/approve");
  await page.locator("#root > *").first().waitFor({ timeout: Number(process.env.PHONE_E2E_TIMEOUT_MS ?? 60000) }).catch(async (error) => {
    console.error("APP BODY:", await page.locator("body").innerText());
    console.error("APP HTML:", (await page.locator("#root").innerHTML()).slice(0, 4_000));
    console.error("RELAY FETCHES:", JSON.stringify(fetched));
    console.error("NETWORK:", JSON.stringify(network));
    console.error("HOST STATUS:", JSON.stringify(await control("GET", "/status")));
    throw error;
  });
  const paired = await link();
  console.error("paired link:", JSON.stringify(paired));
  if (seedLink) {
    assert.equal(paired.seed, true, "an older loader's link keeps its raw seed");
    assert.equal(paired.sign, null, "and has no sealed keys");
  } else {
    assert.equal(paired.seed, false, "a new link stores no seed");
    assert.match(paired.key ?? "", /^[0-9a-f]{64}$/, "only its public key");
    assert.deepEqual([paired.sign, paired.box, paired.ids], [false, false, 1], "one link, non-extractable keys");
  }
  const historyViewport = page.locator('[data-slot="aui_thread-viewport"]');
  if (!(await historyViewport.count())) {
    await page.getByText("Synthetic PhoneLink History", { exact: true }).first().click();
  }
  await historyViewport.waitFor({ state: "visible", timeout: 15000 });
  await page.getByText("synthetic-history-170", { exact: false }).first().waitFor({
    state: "attached",
    timeout: 15000,
  });
  const initialText = (await historyViewport.textContent()) ?? "";
  assert.ok(initialText.includes("synthetic-history-170"), "initial page starts at the newest 50-message window");
  assert.ok(!initialText.includes("synthetic-history-169"), "older messages wait for scroll-back");
  assert.ok(initialText.includes("Tool Result"), "historical tool output remains available in the thread");
  assert.ok(!initialText.includes("SYNTHETIC_TOOL_OUTPUT_END"), "full tool output is not loaded eagerly");

  let historyMetrics = await waitForMetrics((metrics) =>
    metrics.history_starts.some((path) => path.includes("/history?") && !path.includes("before=") && !path.includes("after=")) &&
    metrics.history_reads.some((read) => read.path.includes("/history?") && !read.path.includes("before=") && !read.path.includes("after=")),
  );
  const firstHistoryPath = historyMetrics.history_starts.find(
    (path) => path.includes("/history?") && !path.includes("before=") && !path.includes("after="),
  );
  const firstHistoryRead = historyMetrics.history_reads.filter((read) => read.path === firstHistoryPath);
  assert.equal(firstHistoryRead.length, 1, "a bounded first page fits in one coalesced PhoneLink read");
  assert.ok(firstHistoryRead[0].bytes <= 150_000, "first-page JSON stays under the byte cap");
  assert.equal(firstHistoryRead[0].items, 50);
  assert.equal(firstHistoryRead[0].end, true);

  assert.equal(historyMetrics.history_output_ref, true, "the tool result has a signed lazy-output reference");

  const cachedHistory = await page.evaluate(async () => {
    const databases = await indexedDB.databases();
    if (!databases.some((database) => database.name === "kollab-history-pages-v1")) {
      return { exists: false, sessions: [] };
    }
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("kollab-history-pages-v1");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const sessions = await new Promise((resolve, reject) => {
      const transaction = database.transaction("sessions", "readonly");
      const request = transaction.objectStore("sessions").getAll();
      request.onsuccess = () => resolve(request.result.map((entry) => ({
        sessionId: entry.sessionId,
        pages: entry.pages.map((page) => ({
          start_index: page.start_index,
          end_index: page.end_index,
          items: page.history.length,
          has_newer_cursor: typeof page.newer_cursor === "string",
        })),
      })));
      request.onerror = () => reject(request.error);
    });
    database.close();
    return { exists: true, sessions };
  });
  assert.ok(cachedHistory.exists, "history cache uses persistent IndexedDB, not only memory");
  assert.ok(cachedHistory.sessions.some((entry) => entry.sessionId === "synthetic-phone-history"));

  const cachedSession = cachedHistory.sessions.find((entry) => entry.sessionId === "synthetic-phone-history");
  assert.ok(cachedSession.pages.some((page) => page.has_newer_cursor), "the latest cached page carries its refresh cursor");

  historyMetrics = await waitForMetrics((metrics) =>
    metrics.history_reads.some((read) => read.path.includes("before=") && read.end),
  );
  await historyViewport.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await historyViewport.hover();
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.mouse.wheel(0, -2500);
    if (await page.getByText("synthetic-history-169", { exact: false }).count()) break;
  }
  await page.getByText("synthetic-history-169", { exact: false }).first().waitFor({
    state: "attached",
    timeout: 15000,
  });

  const expandToolResult = page.locator('[data-slot="tool-fallback-trigger"]').last();
  await expandToolResult.waitFor({ state: "visible", timeout: 15000 });
  await expandToolResult.scrollIntoViewIfNeeded();
  await expandToolResult.click();
  try {
    await page.waitForFunction(() =>
      document.querySelector('[data-slot="history-output-preview"] pre')?.textContent?.includes("SYNTHETIC_TOOL_OUTPUT_START"),
      null,
      { timeout: 15000 },
    );
  } catch (error) {
    console.error("TOOL RESULT HTML:", await page.locator('[data-slot="tool-fallback-result"]').last().innerHTML());
    console.error("TOOL PAGE TEXT:", (await historyViewport.textContent())?.slice(-2_000));
    console.error("TOOL METRICS:", JSON.stringify(await control("GET", "/metrics")));
    throw error;
  }
  // Expanding the card is the trigger: the full output loads with no second click.
  await page.waitForFunction(() =>
    document.querySelector('[data-slot="history-output-preview"] pre')?.textContent?.includes("SYNTHETIC_TOOL_OUTPUT_END"),
    null,
    { timeout: 60000 },
  );
  historyMetrics = await waitForMetrics((metrics) => metrics.tool_output_reads.length > 0);
  assert.ok(historyMetrics.tool_output_reads.length > 0, "expanding a tool requests the large output lazily");


  await page.waitForTimeout(1500);
  const scripts = fetched.filter((p) => p.endsWith(".js") || p.endsWith(".gz"));
  assert.deepEqual([...new Set(scripts)], ["/relay/v1/phone/loader.js"], "relay supplied only the loader");
  assert.ok(await page.evaluate(() => [...document.querySelectorAll("style")].some((style) => style.textContent.length > 1000)), "app styles ran");
  await shot("phonelink-app-390");
  for (const [w, h] of [[820, 1180], [1280, 800]]) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await shot(`phonelink-app-${w}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const seen = (await control("GET", "/status")).phones[0].last_seen;
  assert.ok(seen > 0, "computer records when the phone was last seen");

  // Next launch: no fragment, nothing new from the relay but the loader.
  sealKeys = true;
  fetched.length = 0;
  await page.goto(`${origin}/relay/v1/phone/`);
  await page.locator("#root > *").first().waitFor({ timeout: Number(process.env.PHONE_E2E_TIMEOUT_MS ?? 60000) }).catch(async (error) => {
    console.error("APP BODY:", await page.locator("body").innerText());
    console.error("APP HTML:", (await page.locator("#root").innerHTML()).slice(0, 4_000));
    console.error("RELAY FETCHES:", JSON.stringify(fetched));
    console.error("NETWORK:", JSON.stringify(network));
    console.error("HOST STATUS:", JSON.stringify(await control("GET", "/status")));
    throw error;
  });
  assert.ok(fetched.every((p) => !p.endsWith(".gz")), "relaunch uses the verified copy on the phone");
  const relaunched = await link();
  console.error("relaunched link:", JSON.stringify(relaunched));
  assert.equal(relaunched.seed, false, "the relaunched link stores no seed");
  assert.match(relaunched.key ?? "", /^[0-9a-f]{64}$/);
  if (!seedLink) assert.equal(relaunched.key, paired.key, "the same link");
  assert.deepEqual([relaunched.sign, relaunched.box, relaunched.ids], [false, false, 1], "sealed, non-extractable");

  await page.getByText("synthetic-history-170", { exact: false }).first().waitFor({
    state: "attached",
    timeout: 15000,
  });

  let reopenedMetrics;
  try {
    reopenedMetrics = await waitForMetrics((metrics) =>
      metrics.history_starts.some((request) => request.includes("after=")),
    );
  } catch (error) {
    console.error("REOPEN METRICS:", JSON.stringify(await control("GET", "/metrics")));
    console.error("REOPEN IDB:", JSON.stringify(await page.evaluate(() => indexedDB.databases())));
    throw error;
  }
  const refreshPath = reopenedMetrics.history_starts.find((request) => request.includes("after="));
  const refreshRead = reopenedMetrics.history_reads.find((read) => read.path === refreshPath);
  assert.ok(refreshRead, "reopening checks only history newer than the cached cursor");
  assert.equal(refreshRead.items, 0, "unchanged history returns no duplicate messages");

  // A modified copy on the phone. Online, the computer's signed app replaces it.
  const tamper = () =>
    page.evaluate(
      () =>
        new Promise((resolve) => {
          const open = indexedDB.open("kollab-phonelink-app", 1);
          open.onsuccess = () => {
            const store = open.result.transaction("app", "readwrite").objectStore("app");
            const get = store.get("current");
            get.onsuccess = () => {
              get.result.files["phone-app.js.gz"][10] ^= 1;
              store.put(get.result, "current").onsuccess = () => resolve(true);
            };
          };
        }),
    );
  const damaged = () =>
    page.evaluate(
      () =>
        new Promise((resolve) => {
          const open = indexedDB.open("kollab-phonelink-app", 1);
          open.onsuccess = () => {
            const get = open.result.transaction("app").objectStore("app").get("current");
            get.onsuccess = () => crypto.subtle.digest("SHA-256", get.result.files["phone-app.js.gz"]).then((d) => {
              const hex = [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
              resolve(get.result.manifest.files.find((f) => f.name === "phone-app.js.gz").sha256 !== hex);
            });
          };
        }),
    );
  await tamper();
  assert.equal(await damaged(), true);
  await page.goto(`${origin}/relay/v1/phone/`);
  await page.locator("#root > *").first().waitFor({ timeout: Number(process.env.PHONE_E2E_TIMEOUT_MS ?? 60000) }).catch(async (error) => {
    console.error("APP BODY:", await page.locator("body").innerText());
    console.error("APP HTML:", (await page.locator("#root").innerHTML()).slice(0, 4_000));
    console.error("RELAY FETCHES:", JSON.stringify(fetched));
    console.error("NETWORK:", JSON.stringify(network));
    console.error("HOST STATUS:", JSON.stringify(await control("GET", "/status")));
    throw error;
  });
  assert.equal(await damaged(), false, "online launch replaced the damaged copy with the computer's signed app");

  // Damaged and the computer unreachable: nothing runs.
  await tamper();
  await page.addInitScript(() => { window.WebSocket = class { constructor() { throw new Error("computer out of reach"); } }; });
  await page.goto(`${origin}/relay/v1/phone/`);
  await page.getByText("did not match its signature").waitFor({ timeout: 20000 });
  assert.equal(await page.locator("#root > *").count(), 0, "tampered copy did not run");
  await shot("phonelink-refused-390");

  await control("DELETE", `/phones/${offer.id}`);
  assert.ok(
    httpErrors.every((error) => error.url.endsWith("/api/update/check")),
    `unexpected HTTP errors: ${JSON.stringify(httpErrors)}`,
  );
  const lazyOutputBytes = historyMetrics.tool_output_reads.reduce(
    (total, read) => total + read.bytes,
    0,
  );
  console.log("HISTORY_E2E", JSON.stringify({
    firstPageBytes: firstHistoryRead[0].bytes,
    firstPageItems: firstHistoryRead[0].items,
    firstPageReadPackets: firstHistoryRead.length,
    prefetchedOlderPages: historyMetrics.history_starts.filter((path) => path.includes("before=")).length,
    lazyOutputBytes,
    reopenedAfterCursor: refreshPath.includes("after="),
    reopenedNewItems: refreshRead.items,
  }));
  console.log("PASS phonelink browser e2e");
} catch (error) {
  failed = error;
} finally {
  await browser.close();
  harness.kill();
  fs.rmSync(scratch, { recursive: true, force: true });
}
if (failed) {
  console.error(failed);
  process.exit(1);
}
