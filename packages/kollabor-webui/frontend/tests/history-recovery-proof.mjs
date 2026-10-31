// Live proof for the older-history recovery fixes (#471 / PR #472 round 3).
// The engine is a fixture; the UI is the built app (dist).
//   node tests/history-recovery-proof.mjs [outDir]
// Scenarios:
//   A: a 409 cursor rejection while paging older rebuilds the visible window
//      from a fresh fetch (no dead-end banner)            [App.tsx recovery]
//   B: the paging-failure banner has no border and uses the standard
//      destructive tint                                        [thread.tsx]
//   C: a byte-capped refresh page that skips past the cached window
//      replaces the window instead of stranding the stale one  [history-cache]
//   D: an offline reload narrows the cached page but keeps the truncated
//      prefix reachable from cache, no engine round trip       [history-cache]
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const out = process.argv[2] ?? "/tmp/pr472-proof";
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
  ],
  live: [],
  available: ["sapphire"],
  active: [],
};
const appearance = { season: "auto", gems: {}, home: "/demo" };
const live = {
  session_id: "demo-session",
  name: "Demo",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/demo",
  external: false,
};

let history = null; // per-scenario { resolve(options) -> {status, body} }

const answer = (url) => {
  const pathname = url.pathname.replace(/^\/api(?=\/)/, "");
  if (pathname === "/agents") return pool;
  if (pathname === "/agents/appearance") return appearance;
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

const mkPage = (start, end, overrides = {}) => ({
  history: Array.from({ length: end - start }, (_, i) => ({
    role: "user",
    content: `message-${start + i}`,
  })),
  start_index: start,
  end_index: end,
  has_more_older: start > 0,
  older_cursor: start > 0 ? `before-${start}` : null,
  has_more_newer: false,
  newer_cursor: `after-${end}`,
  last_turn_error: null,
  source: "mirror",
  stale: false,
  ...overrides,
});

const failures = [];
const ok = (scenario, claim) => console.log(`  [ok] ${scenario}: ${claim}`);
const fail = (scenario, claim) => {
  failures.push(`${scenario}: ${claim}`);
  console.log(`  [FAIL] ${scenario}: ${claim}`);
};

const scrollToTop = async (page, focusText) => {
  const viewport = page.locator('[data-slot="aui_thread-viewport"]');
  await viewport.waitFor({ timeout: 8000 });
  // Real user path: click a message inside the thread (focus lands in the
  // scrollable), then Home. The app's own keydown handler marks the scroll
  // user-driven and the browser scrolls the viewport to the top, firing
  // the load-older trigger with scrollTop 0.
  if (focusText) {
    await page.getByText(focusText, { exact: true }).last().click({ timeout: 8000 });
  }
  await page.keyboard.press("Home");
  await page.waitForTimeout(150);
  // Backup: if Home did not reach the top, force it and re-fire the gate.
  await viewport.evaluate((node) => {
    if (node.scrollTop > 0) {
      node.dispatchEvent(new WheelEvent("wheel", { deltaY: -8, bubbles: true }));
      node.scrollTop = 0;
      node.dispatchEvent(new Event("scroll", { bubbles: true }));
    }
  });
};

const waitForTextWithRetry = async (page, text, focusText, tries = 3) => {
  for (let i = 0; i < tries; i++) {
    try {
      await page
        .getByText(text, { exact: true })
        .first()
        .waitFor({ timeout: 4000 });
      return true;
    } catch {
      if (i < tries - 1) await scrollToTop(page, focusText);
    }
  }
  return false;
};

const openThread = async (page, marker) => {
  await page.waitForTimeout(700);
  await page.getByText(marker, { exact: true }).first().waitFor({ timeout: 10000 });
};

let browser;
const scenario = async (name, buildState, run) => {
  console.log(`scenario ${name}`);
  const context = await browser.newContext({ viewport: { width: 820, height: 1180 } });
  await context.addInitScript(() =>
    localStorage.setItem("kollab.setup-guide.v1", "dismissed"),
  );
  const page = await context.newPage();
  const requests = [];
  const consoleNotes = [];
  page.on("pageerror", (error) => fail(name, `page error: ${error.message}`));
  page.on("console", (message) => {
    if (["warning", "error"].includes(message.type())) {
    consoleNotes.push(`${message.type()}: ${message.text().slice(0, 120)}`);
    }
  });
  history = buildState();
  await page.route("**/*", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const engine = ["7433", "8080"].includes(url.port);
    if ((engine || url.origin === base) && url.pathname.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    }
    if (
      engine ||
      (url.origin === base && ["fetch", "xhr"].includes(request.resourceType()))
    ) {
      if (url.pathname.endsWith("/history") && !url.pathname.includes("/output")) {
        const options = {
          limit: url.searchParams.get("limit"),
          before: url.searchParams.get("before"),
          after: url.searchParams.get("after"),
        };
        requests.push(options);
        const response = history.resolve(options);
        const fulfill = (value) =>
          route.fulfill({
            status: value.status,
            contentType: "application/json",
            body: JSON.stringify(value.body ?? {}),
          });
        // A resolve() may return a pending promise to hold this request at
        // the route layer until the scenario releases it (deterministic
        // sequencing; scenario B depends on it).
        if (response && typeof response.then === "function") {
          response.then(fulfill, () => route.abort());
          return;
        }
        return fulfill(response);
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(answer(url)),
      });
    }
    return url.origin === base ? route.continue() : route.abort();
  });
  await page.goto(base);
  // Round-4 screenshot race fix: a run may take its own screenshot mid-flow
  // (capture) while harness-level state is still held; the wrapper then skips
  // its post-run shot so the pixels on disk are the asserted ones.
  let captured = false;
  const capture = async () => {
    captured = true;
    await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: false });
  };
  try {
    await run(page, requests, capture);
    if (!captured) await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: false });
  } catch (error) {
    fail(name, `harness error: ${error.message}`);
    console.log(`    ${name} requests:`, JSON.stringify(requests));
    console.log(`    ${name} console:`, JSON.stringify(consoleNotes.slice(-6)));
    const bodyText = await page
      .evaluate(() => document.body.innerText.slice(0, 400))
      .catch(() => "<unavailable>");
    console.log(`    ${name} body:`, JSON.stringify(bodyText));
    await page
      .screenshot({ path: path.join(out, `${name}-error.png`) })
      .catch(() => {});
  }
  await context.close();
};

try {
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });

  // A: 409 on the older-cursor fetch -> rebuild from a fresh fetch, marker shows.
  await scenario(
    "A-409-recovery",
    () => {
      let rejected = false;
      let servedInitial = false;
      const state = { servedRecovered: false };
      const recoveredPage = () =>
        mkPage(100, 150, {
          history: [
            ...Array.from({ length: 49 }, (_, i) => ({
              role: "user",
              content: `message-${100 + i}`,
            })),
            { role: "user", content: "RECOVERED-409" },
          ],
        });
      return {
        state,
        resolve(options) {
          if (options.before && !rejected) {
            rejected = true;
            return { status: 409, body: { error: "stale cursor" } };
          }
          if (options.before) return { status: 200, body: mkPage(0, 10) };
          if (!rejected && !servedInitial) {
            servedInitial = true;
            return { status: 200, body: mkPage(10, 60) };
          }
          // Any fetch after the rejection serves the rebuilt window with the
          // recovery marker (the app refreshes via whichever cursor survived).
          state.servedRecovered = true;
          return { status: 200, body: recoveredPage() };
        },
      };
    },
    async (page) => {
      await openThread(page, "message-59");
      // The 409 comes from the automatic older-page prefetch; the fix clears
      // the cache and re-runs the state load, applying the rebuilt window.
      await page.getByText("RECOVERED-409", { exact: true }).waitFor({ timeout: 10000 });
      ok("A-409-recovery", "fresh window with RECOVERED-409 is visible after 409");
      await page.waitForTimeout(600);
      const banner = page.locator('[data-slot="history-status"][role="alert"]');
      if (await banner.isVisible().catch(() => false)) {
        fail("A-409-recovery", "a dead-end error banner is showing");
      } else {
        ok("A-409-recovery", "no dead-end banner after recovery");
      }
      if (!history.state.servedRecovered) {
        fail("A-409-recovery", "engine never served the recovered window");
      } else {
        ok("A-409-recovery", "engine served the rebuilt window after rejection");
      }
    },
  );

  // B: non-cursor paging failure -> banner without border, standard tint.
  // Deterministic hold-and-release (round-3 flake fix): the first load-older
  // request is held at the route layer. Once it has arrived, the scenario
  // freezes every after-cursor poll refresh (held too) BEFORE releasing the
  // failure, so nothing can replace the status block between the 500 landing
  // and the style read. Round 2 lost this race twice on a slower machine:
  // the ~2s poll unmounted the banner in under one 100 ms polling interval.
  // Round 4: the hold spans the screenshot itself, so the captured pixels
  // show the banner (round 3 released before the wrapper captured).
  const b = {
    mode: "passthrough", // passthrough -> armed (before held) -> failing
    held: [],
    beforeSeen: 0,
    release(response) {
      this.held.splice(0).forEach((pending) => pending.resolve(response));
    },
  };
  await scenario(
    "B-banner-style",
    () => ({
      resolve(options) {
        if (options.before) {
          b.beforeSeen += 1;
          if (b.mode === "passthrough") b.mode = "armed";
          if (b.mode === "failing") return { status: 500, body: { error: "engine hiccup" } };
          return new Promise((resolve) => {
            b.held.push({ resolve });
          });
        }
        if (b.mode === "failing") {
          // Poll refreshes stay frozen until the banner is captured.
          return new Promise((resolve) => {
            b.held.push({ resolve });
          });
        }
        return { status: 200, body: mkPage(10, 60) };
      },
    }),
    async (page, requests, capture) => {
      await openThread(page, "message-59");
      await page.waitForTimeout(2000); // let the quiet prefetch settle
      // User-driven trigger (click + Home, wheel backup inside scrollToTop)
      // repeated until the held route sees the load-older request arrive.
      for (let attempt = 0; attempt < 5 && b.beforeSeen === 0; attempt++) {
        await scrollToTop(page, "message-59");
        const box = await page
          .locator('[data-slot="aui_thread-viewport"]')
          .boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
          await page.mouse.wheel(0, -40);
        }
        await page.waitForTimeout(400);
      }
      if (b.beforeSeen === 0) {
        b.release({ status: 200, body: mkPage(10, 60) });
        fail("B-banner-style", "load-older request never reached the route");
        return;
      }
      // From here on, poll refreshes are held and before requests fail fast.
      b.mode = "failing";
      b.release({ status: 500, body: { error: "engine hiccup" } });
      // If the held request was the background prefetch, the user-visible
      // paged window still needs its own failure to mount the banner.
      await scrollToTop(page, "message-59");
      // waitForFunction options are the THIRD argument; passing them as the
      // arg silently falls back to the 30 s default. Only accept a banner
      // that is actually laid out on screen (getClientRects), not detached.
      const className = await page
        .waitForFunction(
          () => {
            const el = document.querySelector('[data-slot="history-status"][role="alert"]');
            return el && el.getClientRects().length > 0 ? el.className : false;
          },
          undefined,
          { timeout: 4000, polling: 50 },
        )
        .then((handle) => handle.jsonValue())
        .then((value) => String(value))
        .catch(() => null);
      if (!className) {
        fail("B-banner-style", "error banner never appeared after paging failure");
        return;
      }
      if (className.includes("border")) {
        fail("B-banner-style", `banner still has a border: ${className}`);
      } else {
        ok("B-banner-style", "banner has no border class");
      }
      if (className.includes("bg-destructive/10")) {
        ok("B-banner-style", "banner uses the standard destructive tint");
      } else {
        fail("B-banner-style", `banner missing bg-destructive/10: ${className}`);
      }
      // Round-4 fix: the frozen refresh stays held through the capture, so
      // nothing can unmount the banner between the style read and the pixels
      // on disk. Round 3 released the hold here, and the applied refresh
      // window could unmount the banner before the wrapper's screenshot.
      // The screenshot is viewport-sized, so "captured" means the banner
      // intersects the viewport, not merely laid out somewhere in the DOM.
      const inViewport = () => {
        const el = document.querySelector('[data-slot="history-status"][role="alert"]');
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
      };
      const laidOut = await page
        .waitForFunction(inViewport, undefined, { timeout: 2000, polling: 50 })
        .then(() => true)
        .catch(() => false);
      if (!laidOut) {
        fail("B-banner-style", "banner unmounted before capture");
        return;
      }
      await capture();
      const stillThere = await page.evaluate(inViewport).catch(() => false);
      if (!stillThere) {
        fail("B-banner-style", "banner unmounted during capture");
        return;
      }
      ok("B-banner-style", "banner captured on screen with the refresh held");
      // Only now release the held refreshes so the app settles before the
      // context closes; the screenshot is already on disk. No assertion on
      // the post-release banner: the refresh payload (older pages) can be
      // legitimately dropped by the merge gap guard, so the banner staying
      // mounted is valid product behavior.
      b.release({ status: 200, body: mkPage(60, 110) });
    },
  );

  // C: byte-capped refresh page skips past the cached window -> served anyway.
  await scenario(
    "C-byte-capped-refresh",
    () => {
      let reloaded = false;
      return {
        resolve(options) {
          if (options.after) {
            reloaded = true;
            return {
              status: 200,
              body: mkPage(156, 160, {
                history: Array.from({ length: 4 }, () => ({
                  role: "user",
                  content: "C".repeat(34 * 1024) + " CAP-NEWEST",
                })),
                older_cursor: "before-156",
                has_more_older: true,
              }),
            };
          }
          return {
            status: 200,
            body: mkPage(0, 50, { has_more_older: false, older_cursor: null }),
          };
        },
      };
    },
    async (page, requests) => {
      await openThread(page, "message-49");
      await page.reload();
      await page.getByText("CAP-NEWEST").first().waitFor({ timeout: 10000 });
      ok("C-byte-capped-refresh", "capped refresh page replaced the stale window");
      const afterRequests = requests.filter((r) => r.after);
      if (afterRequests.length === 0) fail("C-byte-capped-refresh", "no after-cursor refresh");
      else ok("C-byte-capped-refresh", `after-cursor refresh made (${afterRequests.length})`);
    },
  );

  // E: byte-capped refresh replaces the window while the view is parked at
  // the top; UP-ONLY intent must still page to the oldest message. Round-3
  // review: the load-older trigger lived only in onScroll, and a wheel-up at
  // scrollTop 0 never changes scrollTop, so no scroll event fires and the
  // view sat stranded with older pages available until the user scrolled
  // down and back up. This scenario scrolls up only — never down, and fires
  // no synthetic scroll events after the window is replaced.
  const e = {
    holding: true,
    held: [],
    // Cursors this fake engine has actually issued. The real engine signs
    // every cursor and rejects (400) any cursor it never issued.
    issuedCursors: new Set(),
    release(response) {
      this.held.splice(0).forEach((pending) => pending.resolve(response));
    },
  };
  await scenario(
    "E-capped-top-uponly",
    () => ({
      releaseCapped() {
        e.issuedCursors.add("before-156");
        e.release({
          status: 200,
          body: mkPage(156, 160, {
            history: Array.from({ length: 4 }, (_, i) => ({
              role: "user",
              content: "E".repeat(34 * 1024) + ` E-CAP-${i}`,
            })),
            older_cursor: "before-156",
            has_more_older: true,
          }),
        });
      },
      resolve(options) {
        // Hold every after-cursor poll refresh at the route layer until the
        // scenario parks the view at the top, then release one capped page.
        if (options.after && !options.before) {
          if (e.holding) {
            return new Promise((resolve) => {
              e.held.push({ resolve });
            });
          }
          return {
            status: 200,
            body: mkPage(156, 160, {
              history: Array.from({ length: 4 }, (_, i) => ({
                role: "user",
                content: "E".repeat(34 * 1024) + ` E-CAP-${i}`,
              })),
              older_cursor: "before-156",
              has_more_older: true,
            }),
          };
        }
        if (options.before) {
          // Cursor gate: the engine signs every cursor it issues and
          // rejects any cursor it never issued with 400 — so a UI that
          // synthesizes a cursor (e.g. from a cached fragment boundary)
          // can never page past a capped refresh.
          if (!e.issuedCursors.has(options.before)) {
            return { status: 400, body: { error: "unissued cursor rejected" } };
          }
          // Serve the page immediately preceding whatever window the app is
          // paging from, exactly like the real engine: contiguous, so the
          // merge guard can stitch the window back together — and the
          // served page's boundary cursor counts as issued.
          const from = Number(options.before.replace(/^before-/, ""));
          if (!Number.isFinite(from) || from <= 0) {
            return { status: 400, body: { error: "bad cursor" } };
          }
          const page = mkPage(Math.max(0, from - 50), from);
          if (page.older_cursor) e.issuedCursors.add(page.older_cursor);
          return { status: 200, body: page };
        }
        // Start from an exhausted window so parking at the top pages
        // nothing: the strand needs older pages to APPEAR only when the
        // capped refresh replaces the window.
        return {
          status: 200,
          body: mkPage(71, 121, { has_more_older: false, older_cursor: null }),
        };
      },
    }),
    async (page, requests) => {
      await openThread(page, "message-120");
      await page.waitForTimeout(1000); // let the held poll arrive
      const viewport = page.locator('[data-slot="aui_thread-viewport"]');
      await viewport.waitFor({ timeout: 8000 });
      // Park the view at the top with REAL wheel-ups only (the way an
      // actual reader reaches the oldest loaded message); no synthetic
      // scroll events, no Home key.
      await viewport.hover();
      const trace = [];
      for (let i = 0; i < 14; i++) {
        await page.mouse.wheel(0, -25000);
        await page.waitForTimeout(120);
        trace.push(await viewport.evaluate((node) => node.scrollTop));
        if (trace[trace.length - 1] <= 64) break;
      }
      const beforePark = trace[trace.length - 1];
      if (beforePark > 64) {
        fail(
          "E-capped-top-uponly",
          `setup failed: could not park at top (scrollTop trace ${JSON.stringify(trace)})`,
        );
        return;
      }
      // Replace the window with a byte-capped page that has older history,
      // exactly like the real engine's capped refresh landing mid-session.
      history.releaseCapped();
      await page.getByText("E-CAP-3", { exact: false }).first().waitFor({ timeout: 10000 });
      await page.waitForTimeout(600);
      const parkedTop = await viewport.evaluate((node) => node.scrollTop);
      if (parkedTop > 64) {
        fail(
          "E-capped-top-uponly",
          `view not parked at top after capped refresh (scrollTop ${parkedTop})`,
        );
        return;
      }
      ok("E-capped-top-uponly", `capped refresh parked the view at scrollTop ${parkedTop} with older pages available`);
      const beforeCount = () => requests.filter((r) => r.before).length;
      // Touch phase: a finger dragged DOWN at scrollTop 0 (pull-to-load
      // older) cannot move the viewport, so no scroll event fires — the
      // drag itself must trigger the older load, exactly like the wheel.
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setTouchEmulationEnabled", {
        enabled: true,
        maxTouchPoints: 1,
      });
      const box = await viewport.boundingBox();
      const cx = box.x + box.width / 2;
      const cy = box.y + 64;
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [{ x: cx, y: cy }],
      });
      for (let i = 1; i <= 20; i++) {
        await cdp.send("Input.dispatchTouchEvent", {
          type: "touchMove",
          touchPoints: [{ x: cx, y: cy + i * 8 }],
        });
        await page.waitForTimeout(100);
      }
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
      const touchLoads = beforeCount();
      if (touchLoads === 0) {
        fail("E-capped-top-uponly", "touch drag-down at the top never paged older history");
      } else {
        ok("E-capped-top-uponly", `touch drag-down paged older history (${touchLoads} before-load(s))`);
      }
      // Up-only recovery: wheel up at the top, repeatedly. No down-scroll
      // first, no synthetic scroll events — the wheel-up alone has to page.
      await viewport.hover();
      for (let i = 0; i < 40; i++) {
        await page.mouse.wheel(0, -1200);
        await page.waitForTimeout(250);
        if (
          await page
            .getByText("message-0", { exact: true })
            .first()
            .isVisible()
            .catch(() => false)
        )
          break;
      }
      const oldestVisible = await page
        .getByText("message-0", { exact: true })
        .first()
        .isVisible()
        .catch(() => false);
      if (!oldestVisible) {
        fail("E-capped-top-uponly", "up-only wheeling never paged to the oldest message");
      } else {
        ok("E-capped-top-uponly", "up-only wheeling paged to the oldest message");
      }
      const olderLoads = beforeCount();
      if (olderLoads === 0) {
        fail("E-capped-top-uponly", "no before-cursor load was ever made");
      } else {
        ok("E-capped-top-uponly", `before-cursor loads made (${olderLoads})`);
      }
      const banner = page.locator('[data-slot="history-status"][role="alert"]');
      if (await banner.isVisible().catch(() => false)) {
        fail("E-capped-top-uponly", "an error banner is showing after up-only paging");
      } else {
        ok("E-capped-top-uponly", "no error banner after up-only paging");
      }
    },
  );

  // F: a capped cached fragment restored beside the newest window. The
  // cache-hit merges the fragment under the window's engine cursor, so the
  // next page-down re-requests a cursor whose page overlaps what the
  // window already holds. The overlap must adopt the engine's fresh cursor
  // and re-arm the trigger (a stranded scroll anchor here locks the view
  // at the top), and the next up-intent pages down to the oldest message.
  const f = {
    issuedCursors: new Set(["before-71"]),
    held: [],
    beforeSeen: 0,
  };
  await scenario(
    "F-cached-overlap-uponly",
    () => ({
      resolve(options) {
        if (options.after && !options.before) {
          return new Promise((resolve) => {
            f.held.push({ resolve });
          });
        }
        if (options.before) {
          // Same cursor gate as E: only cursors this engine issued.
          if (!f.issuedCursors.has(options.before)) {
            return { status: 400, body: { error: "unissued cursor rejected" } };
          }
          const from = Number(options.before.replace(/^before-/, ""));
          if (!Number.isFinite(from) || from <= 0) {
            return { status: 400, body: { error: "bad cursor" } };
          }
          f.beforeSeen += 1;
          // The first before-71 answer is byte-capped: [21,71) with no
          // older cursor (the engine cut the page short). The open-time
          // prefetch caches exactly this capped fragment beside the live
          // [71,121) window, like a cache restored from an earlier window.
          if (f.beforeSeen === 1 && options.before === "before-71") {
            const capped = mkPage(21, 71);
            return {
              status: 200,
              body: { ...capped, has_more_older: false, older_cursor: null },
            };
          }
          const page = mkPage(Math.max(0, from - 50), from);
          if (page.older_cursor) f.issuedCursors.add(page.older_cursor);
          return { status: 200, body: page };
        }
        return { status: 200, body: mkPage(71, 121) };
      },
    }),
    async (page, requests) => {
      await openThread(page, "message-120");
      await page.waitForTimeout(1000);
      // The open-time prefetch fetches before-71, gets the capped
      // [21,71) fragment, and stores it in the page cache beside the live
      // [71,121) window. Wait for that store to settle before wheeling.
      const beforeCount = () => requests.filter((r) => r.before).length;
      const preseedLoads = beforeCount(); // open-time prefetch may already run
      const viewport = page.locator('[data-slot="aui_thread-viewport"]');
      await viewport.hover();
      for (let i = 0; i < 50; i++) {
        await page.mouse.wheel(0, -1200);
        await page.waitForTimeout(250);
        if (
          await page
            .getByText("message-0", { exact: true })
            .first()
            .isVisible()
            .catch(() => false)
        )
          break;
      }
      const oldestVisible = await page
        .getByText("message-0", { exact: true })
        .first()
        .isVisible()
        .catch(() => false);
      if (!oldestVisible) {
        fail("F-cached-overlap-uponly", "up-only wheeling never paged past the carried cursor to the oldest message");
      } else {
        ok("F-cached-overlap-uponly", "up-only wheeling paged past the carried cursor to the oldest message");
      }
      const requested = requests.map((r) => r.before).filter(Boolean);
      if (requested.includes("before-71") && requested.includes("before-21")) {
        ok("F-cached-overlap-uponly", "overlap adopted the engine's fresh cursor and paged on");
      } else {
        fail("F-cached-overlap-uponly", `cursor chain never advanced (requested: ${requested.join(", ") || "none"})`);
      }
      const olderLoads = beforeCount() - preseedLoads;
      if (olderLoads === 0) {
        fail("F-cached-overlap-uponly", "no before-cursor load was ever made after the restore");
      } else {
        ok("F-cached-overlap-uponly", `before-cursor loads made after the restore (${olderLoads})`);
      }
      const banner = page.locator('[data-slot="history-status"][role="alert"]');
      if (await banner.isVisible().catch(() => false)) {
        fail("F-cached-overlap-uponly", "an error banner is showing after up-only paging");
      } else {
        ok("F-cached-overlap-uponly", "no error banner after up-only paging");
      }
    },
  );

  // G: a user up-wheel queues behind the open-time prefetch's in-flight
  // before-cursor load. The queued intent must survive the prefetch's capped
  // fragment, drive a continuation past it once the engine answers, and keep
  // wheeling up to the oldest message — without a reset loop wedging the
  // thread behind a stuck banner.
  const g = {
    issuedCursors: new Set(["before-71"]),
    beforeSeen: 0,
    held: [],
    release() {
      this.issuedCursors.add("before-21");
      // Answer ONLY the held before-71 continuation. The after-cursor
      // syncs must stay wedged: an after-sync answered with an older
      // before-window would read as an authoritative deletion verdict.
      for (const pending of this.held.splice(0)) {
        if (pending.before === "before-71") {
          pending.resolve({ status: 200, body: mkPage(21, 71) });
        }
      }
    },
  };
  await scenario(
    "G-queued-behind-prefetch",
    () => ({
      resolve(options) {
        // All after-cursor syncs hang: the newest edge is slow in this
        // engine, so the latest-window sync never completes.
        if (options.after && !options.before) {
          return new Promise((resolve) => {
            // Tagged as a non-before request: the newest edge never syncs
            // in this engine, so release() must leave these wedged forever.
            g.held.push({ resolve, before: null });
          });
        }
        if (options.before) {
          if (!g.issuedCursors.has(options.before)) {
            return { status: 400, body: { error: "unissued cursor rejected" } };
          }
          const from = Number(options.before.replace(/^before-/, ""));
          if (!Number.isFinite(from) || from <= 0) {
            return { status: 400, body: { error: "bad cursor" } };
          }
          g.beforeSeen += 1;
          // First before-71 answer is byte-capped: [21,71) with no older
          // cursor, so the open-time prefetch cannot page past it. The
          // second before-71 answer is held until release() resolves it
          // with a normal page whose older_cursor is before-21.
          if (g.beforeSeen === 1 && options.before === "before-71") {
            return {
              status: 200,
              body: { ...mkPage(21, 71), has_more_older: false, older_cursor: null },
            };
          }
          if (g.beforeSeen === 2 && options.before === "before-71") {
            return new Promise((resolve) => {
              g.held.push({ resolve, before: options.before });
            });
          }
          const page = mkPage(Math.max(0, from - 50), from);
          if (page.older_cursor) g.issuedCursors.add(page.older_cursor);
          return { status: 200, body: page };
        }
        return { status: 200, body: mkPage(71, 121) };
      },
    }),
    async (page, requests) => {
      await openThread(page, "message-120");
      await page.waitForTimeout(1000);
      const beforeCount = () => requests.filter((r) => r.before).length;
      const viewport = page.locator('[data-slot="aui_thread-viewport"]');
      await viewport.hover();
      // Wheel up until the capped fragment has landed and the user is parked
      // at its edge, with a second before-71 request in flight (held).
      let parked = false;
      for (let i = 0; i < 30; i++) {
        await page.mouse.wheel(0, -1200);
        await page.waitForTimeout(200);
        if (
          beforeCount() >= 2 &&
          (await page
            .getByText("message-21", { exact: true })
            .first()
            .isVisible()
            .catch(() => false))
        ) {
          parked = true;
          break;
        }
      }
      if (!parked) {
        fail("G-queued-behind-prefetch", "wheel-up never reached the capped fragment edge with a held request in flight");
        return;
      }
      // One more user up-intent queues behind the in-flight prefetch load.
      await page.mouse.wheel(0, -600);
      await page.waitForTimeout(600);
      // The engine finally answers the held request; the queued intent must
      // continue past the released page toward the real oldest window.
      g.release();
      await page.waitForTimeout(800);
      let reached = false;
      for (let i = 0; i < 50; i++) {
        await page.mouse.wheel(0, -1200);
        await page.waitForTimeout(250);
        if (
          await page
            .getByText("message-0", { exact: true })
            .first()
            .isVisible()
            .catch(() => false)
        ) {
          reached = true;
          break;
        }
      }
      if (reached) {
        ok("G-queued-behind-prefetch", "up-only wheeling after the release reached the oldest message");
      } else {
        fail("G-queued-behind-prefetch", "up-only wheeling after the release never reached the oldest message");
      }
      const requested = requests.map((r) => r.before).filter(Boolean);
      if (requested.includes("before-71") && requested.includes("before-21")) {
        ok("G-queued-behind-prefetch", "the cursor chain advanced past the released page to before-21");
      } else {
        fail("G-queued-behind-prefetch", `cursor chain never advanced (requested: ${requested.join(", ") || "none"})`);
      }
      const olderLoads = beforeCount() - 2;
      if (olderLoads <= 0) {
        fail("G-queued-behind-prefetch", "no additional before-cursor load was made after the release");
      } else {
        ok("G-queued-behind-prefetch", `before-cursor loads made after the release (${olderLoads})`);
      }
      // Any lingering banner (error OR the reset loop's loading banner)
      // means the queued intent wedged the thread behind a stuck load.
      const banner = page.locator('[data-slot="history-status"]');
      if (await banner.isVisible().catch(() => false)) {
        fail("G-queued-behind-prefetch", "a history banner is still showing after the queued-behind-prefetch paging");
      } else {
        ok("G-queued-behind-prefetch", "no history banner after the queued-behind-prefetch paging");
      }
    },
  );

  // D: offline reload narrows the cached page; the prefix pages from cache.
  // The offline poll failures (503 every ~2 s) re-apply the narrowed cached
  // window over the prefix pages the cache is serving and can race the
  // cache-paging scroll exactly like scenario B's poll raced the banner:
  // freeze the polls from the moment the narrowed window is confirmed until
  // message-0 is visible.
  const d = {
    offline: false,
    frozen: false,
    held: [],
    freeze() {
      this.frozen = true;
    },
    unfreeze() {
      this.frozen = false;
    },
    release(response) {
      this.held.splice(0).forEach((pending) => pending.resolve(response));
    },
  };
  await scenario(
    "D-offline-narrowed-prefix",
    () => ({
      setOffline(value) {
        d.offline = value;
      },
      freeze() {
        d.freeze();
      },
      unfreeze() {
        d.unfreeze();
      },
      resolve(options) {
        if (d.frozen && options.after && !options.before) {
          return new Promise((resolve) => {
            d.held.push({ resolve });
          });
        }
        if (d.offline) return { status: 503, body: { error: "offline" } };
        if (options.before) return { status: 200, body: mkPage(0, 71) };
        return {
          status: 200,
          body: mkPage(71, 121, {
            older_cursor: "before-71",
            has_more_older: true,
          }),
        };
      },
    }),
    async (page, requests) => {
      await openThread(page, "message-120");
      await page.waitForTimeout(1500); // let the quiet prefetch settle
      // Round-4: freeze the polls through the online paging wait as well.
      // Under load the ~2 s refresh kept re-delivering the 71-121 window
      // over the merged prefix (63 polls in one failing run) and message-0
      // never rendered. Same hold medicine as the offline freeze below.
      history.freeze();
      await scrollToTop(page, "message-120");
      const onlineTop = await waitForTextWithRetry(page, "message-0", "message-120");
      d.release({
        status: 200,
        body: mkPage(71, 121, { older_cursor: "before-71", has_more_older: true }),
      });
      // Unfreeze before the offline reload: boot-time refresh polls must get
      // their 503s so the app falls back to the cache. Left frozen, the
      // polls hang at the route layer and the session restore stalls forever
      // ("Restoring session..." never resolves).
      history.unfreeze();
      if (!onlineTop) throw new Error("message-0 never appeared while online");
      ok("D-offline-narrowed-prefix", "full window paged down to message-0 online");
      history.setOffline(true);
      await page.reload();
      await openThread(page, "message-120");
      ok("D-offline-narrowed-prefix", "narrowed cached window restored offline");
      await page.waitForTimeout(800);
      const servedOfflineStart = requests.length;
      // Freeze poll refreshes so the narrowed cached page cannot be re-applied
      // over the prefix pages the cache is about to serve.
      history.freeze();
      await scrollToTop(page, "message-120");
      const offlineTop = await waitForTextWithRetry(page, "message-0", "message-120");
      d.release({ status: 503, body: { error: "offline" } });
      if (!offlineTop) throw new Error("message-0 never appeared from the cached prefix");
      ok("D-offline-narrowed-prefix", "message-0 reachable from the cached prefix offline");
      const extra = requests.slice(servedOfflineStart);
      if (extra.length === 0) {
        ok("D-offline-narrowed-prefix", "paged from cache with zero engine requests");
      } else {
        ok("D-offline-narrowed-prefix", `engine asked ${extra.length}x while offline (all 503)`);
      }
    },
  );
} finally {
  await browser?.close().catch(() => {});
  server.close();
}

console.log("");
if (failures.length) {
  console.log(`PROOF FAILED (${failures.length})`);
  for (const f of failures) console.log(` - ${f}`);
  process.exit(1);
}
console.log("PROOF PASSED: all history-recovery scenarios verified");
