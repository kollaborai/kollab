// Screenshots of the Gem Studio at phone, tablet and desktop widths.
// The engine is a fixture; the UI is the built app.
//   node tests/gem-studio-shots.mjs <outDir>
// Proves issue #465: the activity tabs stay on one row at 390px and the
// Save button wears the highlight accent (styles.css --highlight).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const out = process.argv[2] ?? "/tmp/gem-studio-shots";
const only = process.argv[3]; // optional width filter: node tests/gem-studio-shots.mjs <outDir> <width>
fs.mkdirSync(out, { recursive: true });
const types = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};
const server = http
  .createServer((req, res) => {
    const file = path.join(root, new URL(req.url, "http://x").pathname);
    const target =
      file.startsWith(root) && fs.existsSync(file) && fs.statSync(file).isFile()
        ? file
        : path.join(root, "index.html");
    res.writeHead(200, {
      "content-type": types[path.extname(target)] ?? "application/octet-stream",
    });
    fs.createReadStream(target).pipe(res);
  })
  .listen(0, "127.0.0.1");
await new Promise((resolve) => server.on("listening", resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const pool = {
  agents: [
    {
      name: "sapphire",
      display_name: "Sapphire",
      avatar: { kind: "gem" },
      color: [48, 96, 220],
      caste: "specialist",
      available: true,
      active: false,
      pool: true,
    },
    {
      name: "lapis",
      display_name: "Lapis",
      avatar: { kind: "gem" },
      color: [38, 84, 180],
      caste: "specialist",
      available: true,
      active: false,
      pool: true,
    },
    {
      name: "peridot",
      display_name: "Peridot",
      avatar: { kind: "gem" },
      color: [70, 160, 80],
      caste: "specialist",
      available: true,
      active: false,
      pool: true,
    },
  ],
  live: [],
  available: ["sapphire", "lapis", "peridot"],
  active: [],
};
const appearance = { season: "auto", gems: {}, home: "/demo" };

const answer = (url, method) => {
  const pathname = url.pathname.replace(/^\/api(?=\/)/, "");
  // /agents, /agents?scope=... — the pool (not /agents/appearance & friends).
  if (pathname === "/agents") return pool;
  if (pathname === "/agents/appearance") return appearance;
  const live = {
    session_id: "demo-session",
    name: "Demo",
    agent: "coder",
    identity: "local-coder",
    profile: "default",
    workspace: "/demo",
    external: false,
  };
  if (pathname === "/sessions") {
    return {
      sessions: [live],
      network: {
        device: "mac",
        folder: "/demo",
        live: true,
        remote: [],
        contacts: [],
        networks: [],
      },
      offline: [],
      hidden: [],
    };
  }
  if (pathname === "/sessions/demo-session") return live;
  if (pathname.endsWith("/history")) {
    return { history: [], source: "mirror", stale: false, last_turn_error: null };
  }
  if (pathname.endsWith("/permissions")) {
    return { pending_prompts: [], approval_mode: "trust_all" };
  }
  if (pathname === "/profiles") {
    return {
      active: "default",
      providers: [],
      profiles: [{ name: "default", provider: "openai_responses", model: "gpt-6-luna" }],
    };
  }
  return {};
};

const failures = [];
let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  for (const [name, width, height] of [
    ["390", 390, 844],
    ["820", 820, 1180],
    ["1280", 1280, 860],
  ].filter(([name]) => !only || name === only)) {
    const context = await browser.newContext({ viewport: { width, height } });
    await context.addInitScript(() =>
      localStorage.setItem("kollab.setup-guide.v1", "dismissed"),
    );
    const page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") {
        failures.push(`${name}px: browser console error: ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => failures.push(`${name}px: page error: ${error.message}`));
    await page.route("**/*", (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const engine = ["7433", "8080"].includes(url.port);
      if (
        (engine || url.origin === base) &&
        url.pathname.endsWith("/events")
      ) {
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        });
      }
      if (
        engine ||
        (url.origin === base && ["fetch", "xhr"].includes(request.resourceType()))
      ) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(answer(url, request.method())),
        });
      }
      return url.origin === base ? route.continue() : route.abort();
    });
    await page.goto(base);
    await page.waitForTimeout(800);
    const studioButton = page.getByRole("button", { name: "Gem Studio" }).first();
    // The sidebar is a sheet on phones and may start collapsed on tablets:
    // open it whenever the studio button is not already on screen.
    const toggle = page.getByRole("button", { name: "Toggle Sidebar" }).first();
    const hasToggle = await toggle
      .waitFor({ state: "visible", timeout: 3000 })
      .then(() => true)
      .catch(() => false);
    if (hasToggle && !(await studioButton.isVisible().catch(() => false))) {
      await toggle.click();
      await page.waitForTimeout(600);
    }
    await studioButton.waitFor({ state: "visible", timeout: 8000 });
    // The sidebar animation can swallow the first hit; retry a few times.
    let opened = false;
    for (let attempt = 0; attempt < 3 && !opened; attempt++) {
      try {
        await studioButton.click({ timeout: 4000 });
        opened = true;
      } catch {
        await page.waitForTimeout(1000);
      }
    }
    if (!opened) failures.push(`${name}px: could not open the Gem Studio`);
    // The mobile sidebar sheet is also role=dialog; scope by the studio title.
    const dialog = page.getByRole("dialog", { name: "Gem Studio" });
    await dialog.waitFor({ timeout: 8000 });
    const activities = dialog.getByRole("tablist", { name: "Preview activity" });
    await activities.waitFor({ timeout: 8000 });
    const tabs = activities.getByRole("tab");
    const tabCount = await tabs.count();
    if (tabCount !== 7) failures.push(`${name}px: expected 7 activity tabs, got ${tabCount}`);
    // One row: every trigger shares a bottom edge, and the list is not two rows tall.
    const rows = await tabs.evaluateAll((nodes) => {
      const bottoms = nodes.map((n) => Math.round(n.getBoundingClientRect().bottom));
      const list = nodes[0]?.closest("[role='tablist']");
      return {
        oneRow: bottoms.every((b) => Math.abs(b - bottoms[0]) <= 1),
        listHeight: list ? Math.round(list.getBoundingClientRect().height) : -1,
      };
    });
    if (!rows.oneRow) failures.push(`${name}px: activity tabs wrapped onto multiple rows`);
    if (rows.listHeight < 0 || rows.listHeight > 48)
      failures.push(`${name}px: activity tablist height ${rows.listHeight}px suggests wrapping`);
    // The Save button wears the highlight accent token, whichever theme is live
    // (light #3f6b0c, dark #78be21).
    const save = dialog.getByRole("button", { name: /^Save$/ });
    await save.waitFor({ timeout: 8000 });
    const saveBg = await save.evaluate((n) => getComputedStyle(n).backgroundColor);
    const highlight = await page.evaluate(() => {
      const hex = getComputedStyle(document.documentElement)
        .getPropertyValue("--highlight")
        .trim()
        .replace("#", "");
      const n = parseInt(hex.length === 3 ? hex.replace(/./g, (c) => c + c) : hex, 16);
      return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
    });
    if (saveBg !== highlight)
      failures.push(`${name}px: Save background ${saveBg}, expected ${highlight} (--highlight)`);
    await page.screenshot({ path: path.join(out, `gem-studio-${name}.png`) });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    );
    if (overflow) failures.push(`${name}px: horizontal overflow`);
    console.log(
      name,
      `tabs=${tabCount} oneRow=${rows.oneRow} listHeight=${rows.listHeight} saveBg=${saveBg}`,
      overflow ? "OVERFLOW" : "no horizontal overflow",
    );
    await context.close();
  }
} finally {
  if (browser) await browser.close();
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}
if (failures.length) throw new Error(failures.join("; "));
console.log("gem studio shots: PASS");
