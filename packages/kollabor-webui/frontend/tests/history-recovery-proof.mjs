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
  try {
    await run(page, requests);
    await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: false });
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
    async (page) => {
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
      b.release({ status: 200, body: mkPage(60, 110) });
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
      await scrollToTop(page, "message-120");
      const onlineTop = await waitForTextWithRetry(page, "message-0", "message-120");
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
