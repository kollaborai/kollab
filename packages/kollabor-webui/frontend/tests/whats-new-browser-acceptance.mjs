// Built App proof with synthetic release/API data. No server, engine, or socket
// is opened: every browser request is fulfilled or aborted by Playwright.
// Usage: node tests/whats-new-browser-acceptance.mjs <capture-directory>
// Optional: CHROME_PATH points to an installed Chromium executable.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { launchAcceptanceBrowser, makeFixtures } from "./learn-browser-acceptance.mjs";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(frontend, "dist");
const output = path.resolve(process.argv[2] ?? path.join(frontend, "dist/whats-new-proof"));
mkdirSync(output, { recursive: true });
const origin = "http://kollab.test";
const setupKey = "kollab.setup-guide.v1";
const seenKey = "kollab.whats-new.v1";
const markdown = `# Changelog
## [0.22.1] - 2026-11-16
### Added
- Added 1
- Added 2
- Added 3
- Added 4
### Changed
- Changed 1
- Changed 2
- Changed 3
- Changed 4
### Fixed
- Fixed 1
## [0.22.0] - 2026-11-14
### Added
- Previous feature
`;
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };

async function open(browser, { returning = true, width = 1280, defaultWriteDuringStartup = false } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  await context.addInitScript(({ returning, setupKey, seenKey }) => {
    if (localStorage.getItem("proof.initialized")) return;
    localStorage.setItem("proof.initialized", "true");
    if (returning) {
      localStorage.setItem(setupKey, JSON.stringify({ step: "welcome", dismissed: true }));
      localStorage.setItem(seenKey, JSON.stringify({ seenVersion: "0.22.0" }));
    }
  }, { returning, setupKey, seenKey });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // Fail closed for WebSockets as well as HTTP. No live endpoint is contacted.
  await page.routeWebSocket(/.*/, (socket) => socket.close());
  const fixtures = makeFixtures();
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/config" && defaultWriteDuringStartup) {
      // Reproduce a child writing its default after App renders, before startup
      // finishes. Startup eligibility must belong to App's initial snapshot.
      await page.evaluate((key) => localStorage.setItem(key, JSON.stringify({ step: "welcome", dismissed: false })), setupKey);
    }
    if (["fetch", "xhr"].includes(route.request().resourceType())) return fixtures.respond(route);
    if (url.origin !== origin) return route.abort();
    if (/kollab-changelog[^/]*\.js$/.test(url.pathname)) {
      return route.fulfill({ contentType: "text/javascript", body: `export default ${JSON.stringify(markdown)};` });
    }
    const relative = url.pathname === "/" || url.pathname === "/whats-new" ? "index.html" : url.pathname.slice(1);
    const file = path.resolve(dist, relative);
    if (!file.startsWith(`${dist}${path.sep}`)) return route.abort();
    try {
      return route.fulfill({ contentType: types[path.extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    } catch {
      return route.abort();
    }
  });
  await page.goto(origin, { waitUntil: "networkidle" });
  return { context, page, errors };
}

const browser = await launchAcceptanceBrowser();
try {
  for (const width of [1280, 820, 390]) {
    const { context, page, errors } = await open(browser, { width });
    const dialog = page.getByRole("dialog", { name: "What’s New in 0.22.1", exact: true });
    assert.deepEqual(errors, []);
    await dialog.waitFor();
    assert.equal(await dialog.locator("li").count(), 6);
    assert.match(await dialog.innerText(), /And 2 more\./);
    assert.equal(await dialog.getByText("Changed 3", { exact: true }).count(), 0);
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    await page.screenshot({ path: path.join(output, `dialog-${width}.png`) });
    assert.deepEqual(errors, []);
    await context.close();
  }
  for (const action of ["Got It", "Close What’s New", "Escape", "outside", "See All Changes"]) {
    const { context, page, errors } = await open(browser);
    const dialog = page.getByRole("dialog", { name: "What’s New in 0.22.1", exact: true });
    await dialog.waitFor();
    if (action === "Escape") await page.keyboard.press("Escape");
    else if (action === "outside") await page.mouse.click(5, 5);
    else await dialog.getByRole("button", { name: action, exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(await page.evaluate((key) => localStorage.getItem(key), seenKey), '{"seenVersion":"0.22.1"}');
    if (action === "See All Changes") {
      await page.waitForURL(`${origin}/whats-new`);
      await page.getByRole("heading", { name: "What’s New", exact: true }).waitFor();
    }
    await page.reload({ waitUntil: "networkidle" });
    assert.equal(await dialog.count(), 0);
    assert.deepEqual(errors, []);
    await context.close();
  }
  for (const defaultWriteDuringStartup of [false, true]) {
    const { context, page, errors } = await open(browser, { returning: false, defaultWriteDuringStartup });
    await page.getByRole("dialog", { name: "Welcome to Kollab", exact: true }).waitFor();
    assert.equal(await page.getByRole("dialog", { name: "What’s New in 0.22.1", exact: true }).count(), 0);
    assert.equal(await page.evaluate((key) => localStorage.getItem(key), seenKey), null);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(output, `first-run-${defaultWriteDuringStartup ? "write" : "pristine"}.png`) });
    await context.close();
  }
  const head = execFileSync("git", ["rev-parse", "HEAD"], { cwd: frontend, encoding: "utf8" }).trim();
  console.log(`What's New built-App proof passed at ${head}: three widths, five dismissal/navigation paths with reload, pristine and concurrent-write first run. Synthetic release/API; no live service.`);
} finally {
  await browser.close();
}
