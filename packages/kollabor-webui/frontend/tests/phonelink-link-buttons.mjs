// Screenshots + keyboard/contrast checks for Open on This Phone and Copy Link under the PhoneLink QR
// at phone, tablet and desktop widths. The engine is a fixture; the UI is the built app.
//   node tests/phonelink-link-buttons.mjs <outDir>
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
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
  if (p === "/phonelink/phones" && method === "POST") return { id: "d".repeat(32), name: "Tablet", expires_at: now + 290, fingerprint, qr_svg: qr, link: "https://relay.example/relay/v1/phone/#v=1&id=dddd&room=eeee&host=ffff&expires=1" };
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
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
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
  await page.getByRole("link", { name: "Open on This Phone" }).waitFor();
  const open = page.getByRole("link", { name: "Open on This Phone" });
  assert.equal(await open.getAttribute("target"), "_blank");
  assert.equal(await open.getAttribute("rel"), "noopener noreferrer");
  assert.match(await open.getAttribute("href"), /^https:\/\/relay\.example\/relay\/v1\/phone\/#v=1/);
  await page.screenshot({ path: path.join(out, `phonelink-link-${name}.png`) });
  // Keyboard: Tab from the link reaches Copy Link; Enter and Space both copy.
  await open.focus();
  await page.keyboard.press("Tab");
  const copy = page.getByRole("button", { name: "Copy Link" });
  assert.equal(await copy.evaluate((el) => el === document.activeElement), true, "Tab reaches Copy Link");
  for (const key of ["Enter", "Space"]) {
    await page.getByRole("button", { name: /Copy Link|Copied/ }).focus();
    await page.keyboard.press(key);
    await page.getByRole("button", { name: "Copied" }).waitFor();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /#v=1&id=dddd/);
    await page.waitForTimeout(100);
    await page.evaluate(() => navigator.clipboard.writeText(""));
    await page.getByRole("button", { name: "Cancel Invitation" }).click();
    await page.getByRole("button", { name: "Link a Phone" }).click();
    await page.getByRole("link", { name: "Open on This Phone" }).waitFor();
  }
  const contrast = await page.evaluate(() => {
    const rgba = (c) => { const g = document.createElement("canvas").getContext("2d", { willReadFrequently: true }); g.clearRect(0, 0, 1, 1); g.fillStyle = c; g.fillRect(0, 0, 1, 1); return [...g.getImageData(0, 0, 1, 1).data]; };
    const lum = (c) => { const v = rgba(c).slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
    const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (rgba(c)[3] > 250) return c; } return "rgb(0,0,0)"; };
    const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return ((x + 0.05) / (y + 0.05)).toFixed(2); };
    return [...document.querySelectorAll("a[href^='https://relay.example'], button")].filter((e) => /Open on This Phone|Copy Link/.test(e.textContent)).map((e) => ({ label: e.textContent.trim(), text: ratio(getComputedStyle(e).color, bgOf(e)), size: getComputedStyle(e).fontSize }))
      .concat([...document.querySelectorAll("p")].filter((e) => /works for 5 minutes/.test(e.textContent)).map((e) => ({ label: "note", text: ratio(getComputedStyle(e).color, bgOf(e)), size: getComputedStyle(e).fontSize })));
  });
  console.log(name, JSON.stringify(contrast));
  for (const c of contrast) assert.ok(Number(c.text) >= 4.5, `${c.label} contrast ${c.text} < 4.5`);
  // Focus ring on each new control, reached by keyboard: colour vs the page behind it.
  const rings = [];
  await open.focus();
  await page.keyboard.press("Tab"); // Copy Link, then Shift+Tab back so both are keyboard-focused
  for (const label of ["Copy Link", "Open on This Phone"]) {
    if (label === "Open on This Phone") await page.keyboard.press("Shift+Tab");
    await page.waitForTimeout(400); // box-shadow transitions
    rings.push(await page.evaluate((text) => {
      const el = document.activeElement;
      const rgba = (c) => { const g = document.createElement("canvas").getContext("2d", { willReadFrequently: true }); g.clearRect(0, 0, 1, 1); g.fillStyle = c; g.fillRect(0, 0, 1, 1); return [...g.getImageData(0, 0, 1, 1).data]; };
      const lum = (v) => { const [r, g, b] = v.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      let bg = [0, 0, 0, 255];
      for (let e = el.parentElement; e; e = e.parentElement) { const c = rgba(getComputedStyle(e).backgroundColor); if (c[3] > 250) { bg = c; break; } }
      const shadow = getComputedStyle(el).boxShadow.match(/rgba?\([^)]*\)|color\([^)]*\)|oklab\([^)]*\)|oklch\([^)]*\)/g) || [];
      const color = shadow.map(rgba).find((c) => c[3] > 0) ?? rgba(getComputedStyle(el).outlineColor);
      const a = color[3] / 255;
      const over = color.slice(0, 3).map((v, i) => v * a + bg[i] * (1 - a));
      const [x, y] = [lum(over), lum(bg)].sort((m, n) => n - m);
      return { label: text, focused: el.textContent.trim(), ring: ((x + 0.05) / (y + 0.05)).toFixed(2) };
    }, label));
  }
  console.log(name, "focus ring vs page", JSON.stringify(rings));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  console.log(name, overflow ? "OVERFLOW" : "no horizontal overflow");
  await context.close();
}
await browser.close();
server.close();
