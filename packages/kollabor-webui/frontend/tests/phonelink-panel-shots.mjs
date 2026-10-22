// Screenshots of Settings > PhoneLink (fingerprint, pending approval, last seen, revoke)
// at phone, tablet and desktop widths. The engine is a fixture; the UI is the built app.
//   node tests/phonelink-panel-shots.mjs <outDir>
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const out = process.argv[2] ?? "/tmp/phonelink-panel";
fs.mkdirSync(out, { recursive: true });
const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png" };
const server = http.createServer((req, res) => {
  const file = path.join(root, new URL(req.url, "http://x").pathname);
  const target = file.startsWith(root) && fs.existsSync(file) && fs.statSync(file).isFile() ? file : path.join(root, "index.html");
  res.writeHead(200, { "content-type": types[path.extname(target)] ?? "application/octet-stream" });
  fs.createReadStream(target).pipe(res);
}).listen(0, "127.0.0.1");
await new Promise((r) => server.on("listening", r));
const base = `http://127.0.0.1:${server.address().port}`;
const now = Math.floor(Date.now() / 1000);
const fingerprint = "25BD-160D-DC0A-C32E-60E6";
const qr = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 29 29"><rect width="29" height="29" fill="#fff"/><path d="M2 2h7v7H2zM20 2h7v7h-7zM2 20h7v7H2zM12 4h3v3h-3zM12 12h5v5h-5zM20 14h7v3h-7zM14 22h6v3h-6z" fill="#000"/></svg>';
const state = {
  state: "ready", message: "", network: "home", device: "mac",
  phones: [
    { id: "a".repeat(32), name: "My iPhone", linked: true, connected: false, expires_at: now, last_seen: now - 7 * 60, fingerprint, pending: [] },
    { id: "b".repeat(32), name: "Work Android", linked: false, connected: false, expires_at: now + 240, last_seen: null, fingerprint, pending: [{ key: "c".repeat(64), code: "6C18-4AD3-00A4" }] },
  ],
};
const answer = (url, method) => {
  const p = url.pathname.replace(/^\/api(?=\/)/, "");
  if (p === "/phonelink" || p === "/phonelink/refresh") return state;
  if (p === "/phonelink/phones" && method === "POST") return { id: "d".repeat(32), name: "Tablet", expires_at: now + 290, fingerprint, qr_svg: qr };
  const live = { session_id: "demo-session", name: "Demo", agent: "coder", identity: "local-coder", profile: "default", workspace: "/demo", external: false };
  if (p === "/sessions") return { sessions: [live], network: { device: "mac", folder: "/demo", live: true, remote: [], contacts: [], networks: [] }, offline: [], hidden: [] };
  if (p === "/sessions/demo-session") return live;
  if (p.endsWith("/history")) return { history: [], source: "mirror", stale: false, last_turn_error: null };
  if (p.endsWith("/permissions")) return { pending_prompts: [], approval_mode: "trust_all" };
  if (p === "/profiles") return { active: "default", providers: [], profiles: [{ name: "default", provider: "openai_responses", model: "gpt-6-luna" }] };
  if (p === "/phone") return { configured: false, peers: [], settings: {}, ready: false };
  return {};
};
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
for (const [name, width, height] of [["390", 390, 844], ["820", 820, 1180], ["1280", 1280, 860]]) {
  const context = await browser.newContext({ viewport: { width, height } });
  await context.addInitScript(() => localStorage.setItem("kollab.setup-guide.v1", "dismissed"));
  const page = await context.newPage();
  await page.route("**/*", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const engine = ["7433", "8080"].includes(url.port);
    if ((engine || url.origin === base) && url.pathname.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    if (engine || (url.origin === base && ["fetch", "xhr"].includes(request.resourceType())))
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(answer(url, request.method())) });
    return url.origin === base ? route.continue() : route.abort();
  });
  await page.goto(base);
  if (width < 800) await page.getByRole("button", { name: /sidebar/i }).first().click().catch(() => {});
  await page.getByText("Demo", { exact: false }).first().click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "Settings" }).first().click();
  await page.getByRole("tab", { name: "PhoneLink" }).click().catch(() => page.getByText("PhoneLink", { exact: true }).first().click());
  await page.getByText("Your Phones").waitFor();
  await page.getByRole("button", { name: "Link a Phone" }).click();
  await page.getByText("Computer fingerprint:").waitFor();
  await page.screenshot({ path: path.join(out, `phonelink-panel-${name}.png`) });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  console.log(name, overflow ? "OVERFLOW" : "no horizontal overflow");
  await context.close();
}
await browser.close();
server.close();
