// Screenshots + assertions for the Gem Studio at phone, tablet and desktop
// widths, in dark and light tokens, global and focused (Properties) states,
// plus a coarse-pointer pass proving >=44px touch targets.
// The engine is a fixture; the UI is the built app (identity in BUILD.txt).
//   node tests/gem-studio-shots.mjs <outDir> [width]
// Proves issue #465 and the round-2 review: one-row activity tabs, highlighted
// Save, and 44px hit boxes on touch while the bar stays compact for mouse.
import crypto from "node:crypto";
import { execSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../dist");
const out = process.argv[2] ?? "/tmp/gem-studio-shots";
const only = process.argv[3];
fs.mkdirSync(out, { recursive: true });

// Build identity: which head and which built artifact produced every shot.
const head = execSync("git rev-parse HEAD", { cwd: path.resolve(here, "../..") }).toString().trim();
const sha256 = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").slice(0, 16);
const assets = fs.readdirSync(path.join(root, "assets")).map((name) => `${name} ${sha256(path.join(root, "assets", name))}`);
fs.writeFileSync(
  path.join(out, "BUILD.txt"),
  [
    `built_at ${new Date().toISOString()}`,
    `git_head ${head}`,
    `index.html ${sha256(path.join(root, "index.html"))}`,
    ...assets,
    "",
  ].join("\n"),
);
console.log(`build identity: head=${head} index=${sha256(path.join(root, "index.html"))} written to BUILD.txt`);

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
    { name: "sapphire", display_name: "Sapphire", avatar: { kind: "gem" }, color: [48, 96, 220], caste: "specialist", available: true, active: false, pool: true },
    { name: "lapis", display_name: "Lapis", avatar: { kind: "gem" }, color: [38, 84, 180], caste: "specialist", available: true, active: false, pool: true },
    { name: "peridot", display_name: "Peridot", avatar: { kind: "gem" }, color: [70, 160, 80], caste: "specialist", available: true, active: false, pool: true },
  ],
  live: [],
  available: ["sapphire", "lapis", "peridot"],
  active: [],
};
const appearance = { season: "auto", gems: {}, home: "/demo" };
const answer = (url, method) => {
  const pathname = url.pathname.replace(/^\/api(?=\/)/, "");
  if (pathname === "/agents") return pool;
  if (pathname === "/agents/appearance") return appearance;
  const live = {
    session_id: "demo-session",
    name: "Demo",
    agent: "coder",
    identity: "sapphire",
    profile: "default",
    workspace: "/demo",
    external: false,
  };
  if (pathname === "/sessions") {
    return {
      sessions: [live],
      network: { device: "mac", folder: "/demo", live: true, remote: [], contacts: [], networks: [] },
      offline: [],
      hidden: [],
    };
  }
  if (pathname === "/sessions/demo-session") return live;
  if (pathname.endsWith("/history")) return { history: [], source: "mirror", stale: false, last_turn_error: null };
  if (pathname.endsWith("/permissions")) return { pending_prompts: [], approval_mode: "trust_all" };
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
const rowRects = async (tabs) =>
  tabs.evaluateAll((nodes) => {
    const list = nodes[0]?.closest("[role='tablist']");
    return {
      oneRow: nodes
        .map((n) => Math.round(n.getBoundingClientRect().bottom))
        .every((b, _, a) => Math.abs(b - a[0]) <= 1),
      listHeight: list ? Math.round(list.getBoundingClientRect().height) : -1,
      boxes: nodes.map((n) => {
        const r = n.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height) };
      }),
      scroll: list ? { width: list.scrollWidth, client: list.clientWidth } : null,
    };
  });
const assertGlobalTabs = async (page, label, { maxListHeight }) => {
  const dialog = page.getByRole("dialog", { name: "Gem Studio" });
  const tabs = dialog.getByRole("tablist", { name: "Preview activity" }).getByRole("tab");
  await tabs.first().waitFor({ timeout: 8000 });
  const count = await tabs.count();
  if (count !== 7) failures.push(`${label}: expected 7 activity tabs, got ${count}`);
  const rows = await rowRects(tabs);
  if (!rows.oneRow) failures.push(`${label}: activity tabs wrapped onto multiple rows`);
  if (rows.listHeight < 0 || rows.listHeight > maxListHeight)
    failures.push(`${label}: activity tablist height ${rows.listHeight}px (max ${maxListHeight})`);
  return { rows, tabs };
};
const highlightOf = (page) =>
  page.evaluate(() => {
    const hex = getComputedStyle(document.documentElement).getPropertyValue("--highlight").trim().replace("#", "");
    const n = parseInt(hex.length === 3 ? hex.replace(/./g, (c) => c + c) : hex, 16);
    return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
  });

const makeContext = async (browser, width, height, theme, extra = {}) => {
  const context = await browser.newContext({ viewport: { width, height }, ...extra });
  await context.addInitScript(() => localStorage.setItem("kollab.setup-guide.v1", "dismissed"));
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`${width}/${theme}: console error: ${message.text()}`);
  });
  page.on("pageerror", (error) => failures.push(`${width}/${theme}: page error: ${error.message}`));
  await page.route("**/*", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const engine = ["7433", "8080"].includes(url.port);
    if ((engine || url.origin === base) && url.pathname.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    }
    if (engine || (url.origin === base && ["fetch", "xhr"].includes(request.resourceType()))) {
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
  // The app pins the dark class (src/main.tsx); light is a token-level variant
  // captured by removing the class so both palettes are proven.
  if (theme === "light") await page.evaluate(() => document.documentElement.classList.remove("dark"));
  return { context, page };
};
const ensureSidebar = async (page) => {
  // The phone/tablet sidebar starts collapsed and the toggle click can stall
  // in a remount race, so retry until the sidebar target is on screen.
  const toggle = page.getByRole("button", { name: "Toggle Sidebar" }).first();
  for (let attempt = 0; attempt < 3; attempt++) {
    const target = page.getByRole("button", { name: "Gem Studio" }).first();
    if (await target.waitFor({ state: "visible", timeout: 2000 }).then(() => true).catch(() => false)) return true;
    if (!(await toggle.waitFor({ state: "visible", timeout: 2000 }).then(() => true).catch(() => false))) continue;
    await toggle.click({ timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(700);
  }
  return false;
};
const openStudio = async (page, label) => {
  // Whole-flow retry: verify the activity tabs are visible inside the same
  // window, and start each retry from a clean (Escape) state.
  const tabs = page
    .getByRole("dialog", { name: "Gem Studio" })
    .getByRole("tablist", { name: "Preview activity" })
    .getByRole("tab");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      if (!(await ensureSidebar(page))) throw new Error("sidebar did not open");
      const studioButton = page.getByRole("button", { name: "Gem Studio" }).first();
      await studioButton.waitFor({ state: "visible", timeout: 4000 });
      await studioButton.click({ timeout: 4000 });
      await tabs.first().waitFor({ state: "visible", timeout: 6000 });
      return true;
    } catch {
      await page.keyboard.press("Escape").catch(() => {});
      await page.waitForTimeout(600);
    }
  }
  console.log(`${label}: openStudio gave up after 3 attempts`);
  return false;
};
const openProperties = async (page, label) => {
  // Whole-flow retry: a stray click can select a session and dismiss the
  // dialog, so verify and measure inside the same window and return data.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.keyboard.press("Escape");
      await page.waitForTimeout(500);
      if (!(await ensureSidebar(page))) throw new Error("sidebar did not open");
      const row = page.getByRole("button", { name: /Sapphire.*demo/ }).first();
      await row.waitFor({ state: "visible", timeout: 4000 });
      // The double-click handler sits on the row's avatar span, not the button.
      await row.locator("span").first().dblclick({ timeout: 4000 });
      let dialog = page.getByRole("dialog", { name: "Sapphire" });
      if (!(await dialog.waitFor({ state: "visible", timeout: 3000 }).then(() => true).catch(() => false))) {
        // A closed studio stays mounted and hidden; pick the last dialog
        // that carries the Look tablist instead.
        dialog = page
          .getByRole("dialog")
          .filter({ has: page.getByRole("tablist", { name: "Look" }) })
          .last();
      }
      const lookTabs = dialog.getByRole("tablist", { name: "Look" }).getByRole("tab");
      await lookTabs.first().waitFor({ state: "visible", timeout: 6000 });
      return { count: await lookTabs.count(), look: await rowRects(lookTabs) };
    } catch {
      await page.waitForTimeout(600);
    }
  }
  console.log(`${label}: openProperties gave up after 3 attempts`);
  return null;
};
const runGlobal = async (browser, name, width, height, theme, opts = {}) => {
  const label = opts.touch ? `${name}/touch` : `${name}/${theme}`;
  let captured = false;
  for (let attempt = 0; attempt < 2 && !captured; attempt++) {
    const { context, page } = await makeContext(browser, width, height, theme, opts.extra ?? {});
    try {
      if (opts.touch) {
        const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
        if (!coarse) failures.push(`${label}: pointer emulation did not yield (pointer: coarse)`);
      }
      if (!(await openStudio(page, label))) throw new Error("Gem Studio did not open");
      const { rows } = await assertGlobalTabs(page, label, { maxListHeight: opts.touch ? 44 : 48 });
      const save = page.getByRole("dialog", { name: "Gem Studio" }).getByRole("button", { name: /^Save$/ });
      await save.waitFor({ timeout: 8000 });
      const saveBg = await save.evaluate((n) => getComputedStyle(n).backgroundColor);
      const highlight = await highlightOf(page);
      if (saveBg !== highlight) failures.push(`${label}: Save background ${saveBg}, expected ${highlight} (--highlight)`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      if (overflow) failures.push(`${label}: horizontal overflow`);
      if (name === "1280" && !opts.touch && rows.listHeight > 40)
        failures.push(`${label}: fine-pointer bar grew to ${rows.listHeight}px (compact look must stay)`);
      if (opts.touch) {
        for (const [i, box] of rows.boxes.entries()) {
          if (box.h < 43.5 || box.w < 43.5)
            failures.push(`${label}: activity tab ${i} hit box ${box.w}x${box.h}px (need >=44x44)`);
        }
        const list = page.getByRole("dialog", { name: "Gem Studio" }).getByRole("tablist", { name: "Preview activity" });
        const scrolled = await list.evaluate((n) => {
          if (n.scrollWidth <= n.clientWidth) return { overflow: false, moved: null };
          n.scrollLeft = 60;
          return { overflow: true, moved: n.scrollLeft > 0 };
        });
        if (scrolled.overflow === true && scrolled.moved !== true)
          failures.push(`${label}: activity tabs overflow but the scroller does not move`);
        await page.screenshot({ path: path.join(out, `gem-studio-${name}-touch.png`) });
        console.log(label, `listHeight=${rows.listHeight} boxes=${JSON.stringify(rows.boxes)} scroll=${JSON.stringify(scrolled)}`);
      } else {
        await page.screenshot({ path: path.join(out, `gem-studio-${name}-${theme}.png`) });
        console.log(label, `global tabs=7 oneRow=${rows.oneRow} listHeight=${rows.listHeight} saveBg=${saveBg}`);
      }
      captured = true;
    } catch (error) {
      console.log(`${label}: global attempt ${attempt + 1} failed: ${String(error.message).split("\n")[0]}`);
    } finally {
      await context.close();
    }
  }
  if (!captured) failures.push(`${label}: global capture failed after retries`);
  return captured;
};
const runProperties = async (browser, name, width, height, theme, opts = {}) => {
  const label = opts.touch ? `${name}/touch/properties` : `${name}/${theme}/properties`;
  let captured = false;
  for (let attempt = 0; attempt < 2 && !captured; attempt++) {
    const { context, page } = await makeContext(browser, width, height, theme, opts.extra ?? {});
    try {
      // Reproduce the proven sequence: studio open, then Escape to the row.
      if (!(await openStudio(page, label))) throw new Error("Gem Studio did not open");
      const result = await openProperties(page, label);
      if (!result) throw new Error("focused dialog did not open");
      const { count, look } = result;
      if (count !== 8) failures.push(`${label}: expected 8 Look tabs, got ${count}`);
      if (!look.oneRow) failures.push(`${label}: Look tabs wrapped onto multiple rows`);
      if (look.listHeight > 48) failures.push(`${label}: Look tablist height ${look.listHeight}px`);
      if (opts.touch) {
        for (const [i, box] of look.boxes.entries()) {
          if (box.h < 43.5 || box.w < 43.5)
            failures.push(`${label}: Look tab ${i} hit box ${box.w}x${box.h}px (need >=44x44)`);
        }
        await page.screenshot({ path: path.join(out, `gem-studio-${name}-touch-properties.png`) });
        console.log(label, `lookTabs=${count} oneRow=${look.oneRow} listHeight=${look.listHeight} lookBoxes=${JSON.stringify(look.boxes)}`);
      } else {
        await page.screenshot({ path: path.join(out, `gem-studio-${name}-${theme}-properties.png`) });
        console.log(label, `lookTabs=${count} oneRow=${look.oneRow} listHeight=${look.listHeight}`);
      }
      captured = true;
    } catch (error) {
      console.log(`${label}: properties attempt ${attempt + 1} failed: ${String(error.message).split("\n")[0]}`);
    } finally {
      await context.close();
    }
  }
  if (!captured) failures.push(`${label}: properties capture failed after retries`);
  return captured;
};

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  for (const [name, width, height] of [
    ["390", 390, 844],
    ["820", 820, 1180],
    ["1280", 1280, 860],
  ].filter(([name]) => !only || name === only)) {
    for (const theme of ["dark", "light"]) {
      await runGlobal(browser, name, width, height, theme);
      await runProperties(browser, name, width, height, theme);
    }
    if (name === "390") {
      // Coarse-pointer pass: every tab keeps a >=44px hit box (Apple HIG
      // 44pt), the underline look stays, and the scroller still reveals
      // every tab.
      const touch = { isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
      await runGlobal(browser, name, width, height, "dark", { touch: true, extra: touch });
      await runProperties(browser, name, width, height, "dark", { touch: true, extra: touch });
    }
  }
} finally {
  if (browser) await browser.close();
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}
if (failures.length) throw new Error(failures.join("; "));
console.log("gem studio shots: PASS");
