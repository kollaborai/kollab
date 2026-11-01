// Screenshots + assertions for the Gem Studio at phone, tablet and desktop
// widths, in dark and light tokens, global and focused (Properties) states,
// plus a coarse-pointer pass proving >=44px touch targets.
// The engine is a fixture; the UI is the built app (identity in BUILD.txt).
//   node tests/gem-studio-shots.mjs <outDir> [width] [width/theme/pointer/surface]
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
const out = process.argv[2];
if (!out) throw new Error("Pass an output directory for screenshots and build evidence.");
const only = process.argv[3];
const onlyCase = process.argv[4];
fs.mkdirSync(out, { recursive: true });

// Build identity: which head and which built artifact produced every shot.
const head = execSync("git rev-parse HEAD", { cwd: path.resolve(here, "../..") }).toString().trim();
const sha256 = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const assets = fs.readdirSync(path.join(root, "assets")).map((name) => `${name} ${sha256(path.join(root, "assets", name))}`);
fs.writeFileSync(
  path.join(out, "BUILD.txt"),
  [
    `built_at ${new Date().toISOString()}`,
    `git_head ${head}`,
    `git_status ${JSON.stringify(execSync("git status --porcelain --untracked-files=no", { cwd: path.resolve(here, "../..") }).toString().trim())}`,
    `GemStudio.tsx ${sha256(path.join(here, "../src/components/gems/GemStudio.tsx"))}`,
    `harness ${sha256(fileURLToPath(import.meta.url))}`,
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
if ([8080, 8081].includes(server.address().port)) throw new Error("Fixture selected a reserved port");
const base = `http://127.0.0.1:${server.address().port}`;
console.log(`fixture server ${base}`);

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
    const list = nodes[0]?.closest("[role='tablist'], [role='radiogroup']");
    return {
      oneRow: nodes
        .map((n) => Math.round(n.getBoundingClientRect().bottom))
        .every((b, _, a) => Math.abs(b - a[0]) <= 1),
      listHeight: list ? Math.round(list.getBoundingClientRect().height) : -1,
      boxes: nodes.map((n) => {
        const r = n.getBoundingClientRect();
        return { w: r.width, h: r.height };
      }),
      scroll: list ? { width: list.scrollWidth, client: list.clientWidth } : null,
    };
  });
const assertGlobalTabs = async (page, label, { maxListHeight }) => {
  const dialog = page.getByRole("dialog", { name: "Gem Studio" });
  const tabs = dialog.getByRole("radiogroup", { name: "Preview activity" }).getByRole("radio");
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
  // Do not allow a fixture run to open a socket to any live local service.
  await page.routeWebSocket("**/*", (socket) => socket.close());
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
    .getByRole("radiogroup", { name: "Preview activity" })
    .getByRole("radio");
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
      return { dialog, count: await lookTabs.count(), look: await rowRects(lookTabs) };
    } catch {
      await page.waitForTimeout(600);
    }
  }
  console.log(`${label}: openProperties gave up after 3 attempts`);
  return null;
};
const verifyTabBar = async (page, dialog, barName, expectedCount, label, touch) => {
  const activity = barName === "Preview activity";
  const list = dialog.getByRole(activity ? "radiogroup" : "tablist", { name: barName });
  const tabs = list.getByRole(activity ? "radio" : "tab");
  const selectedAttribute = activity ? "aria-checked" : "aria-selected";
  const assertRelationship = async () => {
    const info = await list.evaluate((node, isActivity) => {
      const items = [...node.querySelectorAll(isActivity ? '[role="radio"]' : '[role="tab"]')];
      const selected = items.filter((item) => item.getAttribute(isActivity ? "aria-checked" : "aria-selected") === "true");
      if (isActivity) return { selectedCount: selected.length, orphanedControls: items.filter((item) => {
        const id = item.getAttribute("aria-controls");
        return id && !document.getElementById(id);
      }).length, tabCount: node.querySelectorAll('[role="tab"]').length };
      const item = selected[0];
      const panel = item && document.getElementById(item.getAttribute("aria-controls"));
      return { selectedCount: selected.length, role: panel?.getAttribute("role"),
        labelledBy: panel?.getAttribute("aria-labelledby"), itemId: item?.id, hidden: panel?.hidden };
    }, activity);
    if (info.selectedCount !== 1 || (activity && (info.orphanedControls || info.tabCount)) ||
        (!activity && (info.role !== "tabpanel" || info.labelledBy !== info.itemId || info.hidden)))
      failures.push(`${label}/${barName}: invalid selected control relationship ${JSON.stringify(info)}`);
  };
  await tabs.first().waitFor({ state: "visible", timeout: 8000 });
  if (await tabs.count() !== expectedCount) {
    failures.push(`${label}/${barName}: expected ${expectedCount} tabs, got ${await tabs.count()}`);
    return;
  }
  const rows = await rowRects(tabs);
  if (!rows.oneRow) failures.push(`${label}/${barName}: tabs wrapped`);
  if (touch) {
    for (const [i, box] of rows.boxes.entries()) {
      if (box.w < 43.99 || box.h < 43.99)
        failures.push(`${label}/${barName}: tab ${i} target ${box.w}x${box.h}, need >=44x44`);
    }
  }
  const measure = () => list.evaluate((node) => {
    const listRect = node.getBoundingClientRect();
    const dialogRect = node.closest('[role="dialog"]').getBoundingClientRect();
    const children = [...node.querySelectorAll('[role="tab"], [role="radio"]')];
    const rect = (item) => {
      const r = item.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    };
    return { list: rect(node), dialog: rect(node.closest('[role="dialog"]')), first: rect(children[0]),
      last: rect(children.at(-1)), scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
      scrollLeft: node.scrollLeft, nodeWidth: listRect.width, dialogWidth: dialogRect.width };
  });
  await list.evaluate((node) => { node.scrollLeft = 0; });
  await page.waitForTimeout(80);
  const first = await measure();
  const inside = (item, container) => item.left >= container.left - 0.75 && item.right <= container.right + 0.75;
  if (!inside(first.list, first.dialog)) failures.push(`${label}/${barName}: tablist spills outside the dialog`);
  if (!inside(first.first, first.list) || !inside(first.first, first.dialog))
    failures.push(`${label}/${barName}: first tab is clipped at initial scroll position`);
  if (first.scrollWidth > first.clientWidth + 1) {
    const box = await list.boundingBox();
    if (touch) {
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Input.synthesizeScrollGesture", {
        x: box.x + box.width * 0.8, y: box.y + box.height / 2,
        xDistance: -Math.min(200, box.width * 0.6), yDistance: 0,
        gestureSourceType: "touch", speed: 800,
      });
      await cdp.detach();
    } else {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(200, 0);
    }
    await page.waitForTimeout(150);
    const gestureScroll = await list.evaluate((node) => node.scrollLeft);
    if (gestureScroll <= 0) failures.push(`${label}/${barName}: ${touch ? "swipe" : "horizontal wheel"} did not scroll`);
    console.log(`${label}/${barName}/gesture`, JSON.stringify({ gestureScroll, touch }));
  }
  await list.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
  await page.waitForTimeout(80);
  const last = await measure();
  if (!inside(last.last, last.list) || !inside(last.last, last.dialog))
    failures.push(`${label}/${barName}: last tab cannot be fully revealed`);
  if (last.scrollWidth > last.clientWidth + 1 && last.scrollLeft <= 0)
    failures.push(`${label}/${barName}: overflowing tablist did not scroll`);
  if (touch) await tabs.last().tap();
  else await tabs.last().click();
  if (await tabs.last().getAttribute(selectedAttribute) !== "true")
    failures.push(`${label}/${barName}: last tab was not selected by ${touch ? "tap" : "click"}`);
  await assertRelationship();
  await list.evaluate((node) => { node.scrollLeft = 0; });
  if (touch) await tabs.first().tap();
  else await tabs.first().click();
  if (await tabs.first().getAttribute(selectedAttribute) !== "true")
    failures.push(`${label}/${barName}: first tab was not selected by ${touch ? "tap" : "click"}`);
  await assertRelationship();
  await tabs.first().focus();
  await page.waitForTimeout(100);
  await page.keyboard.press("End");
  await page.waitForTimeout(150); // Radix schedules keyboard focus after the key handler.
  const focusedLast = await tabs.last().evaluate((node) => document.activeElement === node);
  if (!focusedLast) failures.push(`${label}/${barName}: End did not focus the last tab`);
  if (await tabs.last().getAttribute(selectedAttribute) !== "true")
    failures.push(`${label}/${barName}: End did not select the last tab`);
  await assertRelationship();
  await page.keyboard.press("Home");
  await page.waitForTimeout(150);
  const focusedFirst = await tabs.first().evaluate((node) => document.activeElement === node);
  if (!focusedFirst) failures.push(`${label}/${barName}: Home did not focus the first tab`);
  if (await tabs.first().getAttribute(selectedAttribute) !== "true")
    failures.push(`${label}/${barName}: Home did not select the first tab`);
  await assertRelationship();
  await page.keyboard.press("Shift+Tab");
  await page.waitForTimeout(120);
  const previousFocus = await page.evaluate(() => ({ tag: document.activeElement?.tagName, text: document.activeElement?.textContent?.trim().slice(0, 60), label: document.activeElement?.getAttribute("aria-label") }));
  await page.keyboard.press("Tab");
  await page.waitForTimeout(80);
  const tabFocus = await tabs.first().evaluate((node) => document.activeElement === node);
  if (!tabFocus) failures.push(`${label}/${barName}: Tab did not return focus to the selected tab`);
  const focusRings = [];
  for (const [position, index] of [["first", 0], ["middle", Math.floor(expectedCount / 2)], ["last", expectedCount - 1]]) {
    await page.keyboard.press("Home");
    await page.waitForTimeout(120);
    for (let step = 0; step < index; step++) {
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(40);
    }
    await page.waitForTimeout(120);
    const focus = await tabs.nth(index).evaluate((node) => {
      const group = node.closest('[role="tablist"], [role="radiogroup"]');
      const item = node.getBoundingClientRect();
      const clip = group.getBoundingClientRect();
      return { focused: document.activeElement === node, focusVisible: node.matches(":focus-visible"),
        shadow: getComputedStyle(node).boxShadow,
        item: { left: item.left, right: item.right, top: item.top, bottom: item.bottom },
        clip: { left: clip.left, right: clip.right, top: clip.top, bottom: clip.bottom },
        contained: item.left >= clip.left - 1 && item.right <= clip.right + 1 &&
          item.top >= clip.top - 1 && item.bottom <= clip.bottom + 1 };
    });
    if (!focus.focused || !focus.focusVisible || !focus.shadow.includes("inset") || !focus.contained)
      failures.push(`${label}/${barName}: ${position} focus indicator is missing or clipped ${JSON.stringify(focus)}`);
    if (await tabs.nth(index).getAttribute(selectedAttribute) !== "true")
      failures.push(`${label}/${barName}: ${position} keyboard focus did not select its control`);
    await assertRelationship();
    focusRings.push({ position, ...focus });
    if (!touch) await list.screenshot({ path: path.join(out, `focus-${label.replaceAll("/", "-")}-${barName.replaceAll(" ", "-")}-${position}.png`) });
  }
  await list.evaluate((node) => { node.scrollLeft = 0; });
  console.log(`${label}/${barName}`, JSON.stringify({ boxes: rows.boxes, first, last, focusedFirst, focusedLast, previousFocus, tabFocus, focusRings }));
};

const capture = async (browser, name, width, height, theme, surface, touch) => {
  const pointer = touch ? "coarse" : "fine";
  const label = `${name}/${theme}/${pointer}/${surface}`;
  const extra = touch ? { isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : {};
  const { context, page } = await makeContext(browser, width, height, theme, extra);
  try {
    const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
    if (coarse !== touch) failures.push(`${label}: unexpected pointer media query ${coarse}`);
    if (!(await openStudio(page, label))) throw new Error("Gem Studio did not open");
    let dialog = page.getByRole("dialog", { name: "Gem Studio" });
    if (surface === "properties") {
      const result = await openProperties(page, label);
      if (!result) throw new Error("Properties did not open");
      dialog = result.dialog;
    }
    await page.waitForTimeout(900); // Let the dialog animation settle before measuring.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) failures.push(`${label}: document has horizontal overflow`);
    const save = dialog.getByRole("button", { name: /^Save$/ });
    await save.waitFor({ state: "visible", timeout: 8000 });
    const saveBg = await save.evaluate((node) => getComputedStyle(node).backgroundColor);
    const saveRect = await save.boundingBox();
    if (!saveRect || saveRect.x < 0 || saveRect.x + saveRect.width > width ||
        saveRect.y < 0 || saveRect.y + saveRect.height > height)
      failures.push(`${label}: Save is outside the viewport`);
    if (saveBg !== await highlightOf(page)) failures.push(`${label}: Save does not use the highlight token`);
    await page.screenshot({ path: path.join(out, `gem-studio-${name}-${theme}-${pointer}-${surface}.png`) });
    if (width === 390) {
      const scroll = await dialog.getByRole("radiogroup", { name: "Preview activity" }).evaluate((node) => {
        let parent = node.parentElement;
        while (parent && getComputedStyle(parent).overflowY !== "auto") parent = parent.parentElement;
        if (!parent) return null;
        const before = parent.scrollTop;
        parent.scrollTop = parent.scrollHeight;
        const result = { scrollTop: parent.scrollTop, max: parent.scrollHeight - parent.clientHeight };
        parent.scrollTop = before;
        return result;
      });
      if (!scroll || (scroll.max > 1 && scroll.scrollTop < scroll.max - 1))
        failures.push(`${label}: dialog content cannot scroll to the bottom`);
      console.log(`${label}/vertical-scroll`, JSON.stringify(scroll));
    }
    await verifyTabBar(page, dialog, "Preview activity", 7, label, touch);
    if (width === 390) {
      const activity = dialog.getByRole("radiogroup", { name: "Preview activity" });
      await activity.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
      await page.waitForTimeout(100);
      await page.screenshot({ path: path.join(out, `gem-studio-${name}-${theme}-${pointer}-${surface}-last.png`) });
      await activity.evaluate((node) => { node.scrollLeft = 0; });
    }
    await verifyTabBar(page, dialog, "Look", surface === "properties" ? 8 : 6, label, touch);
  } catch (error) {
    failures.push(`${label}: ${String(error.message).split("\n")[0]}`);
  } finally {
    await context.close();
  }
};

// A focused non-gem has no activity row: Look is the first control in the
// dialog, so its tablist must also cycle correctly at the focus boundary.
const captureNonGem = async (browser) => {
  const label = "390/dark/coarse/non-gem";
  const oldAvatar = pool.agents[0].avatar;
  pool.agents[0].avatar = { kind: "initials" };
  const { context, page } = await makeContext(browser, 390, 844, "dark", {
    isMobile: true, hasTouch: true, deviceScaleFactor: 2,
  });
  try {
    if (!(await openStudio(page, label))) throw new Error("Gem Studio did not open");
    const result = await openProperties(page, label);
    if (!result) throw new Error("Properties did not open");
    await page.waitForTimeout(900);
    if (await result.dialog.getByRole("radiogroup", { name: "Preview activity" }).count())
      failures.push(`${label}: a non-gem unexpectedly has an activity row`);
    await page.screenshot({ path: path.join(out, "gem-studio-390-dark-coarse-non-gem.png") });
    await verifyTabBar(page, result.dialog, "Look", 3, label, true);
  } catch (error) {
    failures.push(`${label}: ${String(error.message).split("\n")[0]}`);
  } finally {
    await context.close();
    pool.agents[0].avatar = oldAvatar;
  }
};

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  for (const [name, width, height] of [
    ["390", 390, 844], ["820", 820, 1180], ["1440", 1440, 900],
  ].filter(([name]) => !only || name === only)) {
    for (const theme of ["dark", "light"]) {
      for (const touch of [false, true]) {
        for (const surface of ["global", "properties"]) {
          if (onlyCase && `${name}/${theme}/${touch ? "coarse" : "fine"}/${surface}` !== onlyCase) continue;
          await capture(browser, name, width, height, theme, surface, touch);
        }
      }
    }
  }
  if ((!only || only === "390") && (!onlyCase || onlyCase === "390/dark/coarse/non-gem"))
    await captureNonGem(browser);
} finally {
  if (browser) await browser.close();
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}
if (failures.length) throw new Error(failures.join("; "));
console.log("gem studio shots: PASS");
