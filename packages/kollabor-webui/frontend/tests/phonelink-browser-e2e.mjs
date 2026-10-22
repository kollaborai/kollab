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

console.error("launching");
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined, args: ["--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks,BlockInsecurePrivateNetworkRequests"] });
let failed = null;
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  const page = await context.newPage();
  const fetched = [];
  page.on("console", (m) => m.type() === "error" && console.error("page:", m.text()));
  page.on("pageerror", (e) => console.error("pageerror:", e.message));
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
  await page.locator("#root > *").first().waitFor({ timeout: 60000 });
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
  fetched.length = 0;
  await page.goto(`${origin}/relay/v1/phone/`);
  await page.locator("#root > *").first().waitFor({ timeout: 60000 });
  assert.ok(fetched.every((p) => !p.endsWith(".gz")), "relaunch uses the verified copy on the phone");

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
  await page.locator("#root > *").first().waitFor({ timeout: 60000 });
  assert.equal(await damaged(), false, "online launch replaced the damaged copy with the computer's signed app");

  // Damaged and the computer unreachable: nothing runs.
  await tamper();
  await page.addInitScript(() => { window.WebSocket = class { constructor() { throw new Error("computer out of reach"); } }; });
  await page.goto(`${origin}/relay/v1/phone/`);
  await page.getByText("did not match its signature").waitFor({ timeout: 20000 });
  assert.equal(await page.locator("#root > *").count(), 0, "tampered copy did not run");
  await shot("phonelink-refused-390");

  await control("DELETE", `/phones/${offer.id}`);
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
