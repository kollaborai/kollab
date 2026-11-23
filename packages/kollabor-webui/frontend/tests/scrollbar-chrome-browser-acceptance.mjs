// #1174 browser acceptance: visible scrollbar chrome is hidden across every
// scroll surface the app ships — native containers (both axes, nested),
// the document scroller, the shared Radix ScrollArea/ScrollBar (vertical and
// horizontal), a real Radix Dialog (portal), the production EditorPane,
// and the real TerminalViewer (xterm) over a
// stub connection with synthetic output — while wheel, keyboard, and
// programmatic scrolling keep working and layout gains no reserved gutter.
//
// Chrome visibility needs classic (space-consuming) scrollbars: macOS defaults
// to overlay scrollbars that are already invisible, which would make the red
// proof vacuous. The launcher therefore disables the overlay-scrollbars
// feature; the suite also proves scrolling survives the fix.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { openVite, until } from "./learn-browser-acceptance.mjs";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CHROME_FALLBACKS = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium-browser",
];

async function launchBrowser() {
  // macOS (and this headless shell) default to overlay scrollbars, which are
  // already invisible: a red proof against them would be vacuous. A headed
  // Chromium with the overlay feature disabled renders real classic
  // scrollbars, so chrome visibility is genuinely measurable on both sides.
  const args = ["--disable-features=OverlayScrollbar"];
  if (process.platform === "linux") args.push("--no-sandbox", "--ozone-platform=x11");
  args.push("--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding");
  const common = { headless: false, args };
  if (process.env.CHROME_PATH) {
    return chromium.launch({ ...common, executablePath: process.env.CHROME_PATH });
  }
  try {
    return await chromium.launch(common);
  } catch (error) {
    const fallback = CHROME_FALLBACKS.find((p) => existsSync(p));
    if (!fallback) throw error;
    console.log(`playwright registry unavailable (${error.message}); falling back to ${fallback}`);
    return chromium.launch({ ...common, executablePath: fallback });
  }
}

async function openFixture(browser, baseUrl) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  page.setDefaultNavigationTimeout(60_000);
  page.on("pageerror", (e) => console.error(`pageerror: ${e.message}`));
  await page.goto(`${baseUrl}/tests/scrollbar-chrome-fixture.html`, { waitUntil: "domcontentloaded" });
  await page.bringToFront();
  await until(() => page.evaluate(() => window.__ready === true), "fixture page never became ready");
  return { context, page };
}

async function probes(page) {
  return page.evaluate(() => window.__scrollbarProbes());
}

function assertChromeHidden(name, box) {
  const detail = JSON.stringify(box);
  // Radix unmounts its scrollbar while idle: nothing rendered = no chrome.
  if (!box.present) return;
  assert.ok(
    box.display === "none" || box.visibility === "hidden" || box.opacity === "0" ||
      (box.width <= 1 && box.height <= 1),
    `${name}: scrollbar chrome still visible (${detail})`,
  );
}

function assertThicknessZero(name, t) {
  // DOM client/offset dimensions are integers; computed borders can be
  // fractional under desktop scaling. A classic scrollbar is many pixels.
  assert.ok(Math.abs(t.v) < 1, `${name}: vertical scrollbar chrome still reserves ${t.v}px`);
  assert.ok(Math.abs(t.h) < 1, `${name}: horizontal scrollbar chrome still reserves ${t.h}px`);
}

async function wheelDelta(page, locator, dx, dy, read) {
  await locator.hover();
  const before = await read();
  await page.mouse.wheel(dx, dy);
  await until(async () => (await read()) !== before, "wheel input did not change the scroll position", 3000);
  const after = await read();
  return after - before;
}

async function run() {
  const { server, baseUrl } = await openVite();
  let browser;
  const failures = [];
  const check = async (name, fn) => {
    try {
      await fn();
      console.log(`PASS: ${name}`);
    } catch (error) {
      failures.push(error);
      console.error(`FAIL: ${name}: ${error.message}`);
    }
  };

  try {
    browser = await launchBrowser();
    const { context, page } = await openFixture(browser, baseUrl);

    await check("terminal feed renders scrollback", async () => {
      await until(() => page.evaluate(() => typeof window.__termFeed === "function"), "terminal connection never mounted");
      await page.getByText("feed-terminal").click();
      await until(
        () =>
          page.evaluate(() => {
            const rows = document.querySelector('[data-testid="terminal-box"] .xterm-rows');
            return rows?.textContent.includes("terminal line");
          }),
        "xterm never painted the synthetic scrollback feed",
      );
    });

    await check("xterm wheel scrolls scrollback content", async () => {
      // xterm 6 does not scroll the DOM viewport at all: wheel input scrolls
      // the internal buffer and repaints rows, so the honest proof of
      // scrolling is a visible content change, not scrollTop.
      const firstLineBefore = await page.evaluate(() => {
        const rows = document.querySelector('[data-testid="terminal-box"] .xterm-rows');
        return rows ? rows.firstElementChild?.textContent ?? "" : "";
      });
      await page.getByTestId("terminal-box").hover();
      await page.mouse.wheel(0, -900);
      await page.waitForTimeout(400);
      const firstLineAfter = await page.evaluate(() => {
        const rows = document.querySelector('[data-testid="terminal-box"] .xterm-rows');
        return rows ? rows.firstElementChild?.textContent ?? "" : "";
      });
      assert.notEqual(
        firstLineAfter,
        firstLineBefore,
        `xterm content did not scroll on wheel (before=${JSON.stringify(firstLineBefore)} after=${JSON.stringify(firstLineAfter)})`,
      );
    });

    await check("production monaco editor mounted", async () => {
      await until(
        () => page.evaluate(() => Boolean(document.querySelector('[data-testid="editor-box"] .monaco-editor'))),
        "monaco editor never mounted",
      );
    });

    await check("dialog opens for probing", async () => {
      await page.getByText("open-dialog").click();
      await page.getByTestId("dialog-scroll").waitFor({ timeout: 10_000 });
    });

    const p1 = await probes(page);
    await check("native both-axis chrome hidden", () => assertThicknessZero("native-both", p1["native-both"]));
    await check("borderless scroller chrome hidden", () => assertThicknessZero("native-borderless", p1["native-borderless"]));
    await check("nested outer chrome hidden", () => assertThicknessZero("native-outer", p1["native-outer"]));
    await check("nested inner chrome hidden", () => assertThicknessZero("native-inner", p1["native-inner"]));
    await check("document chrome hidden", () => assertThicknessZero("document", p1.document));
    await check("document scrolls with hidden chrome", async () => {
      const result = await page.evaluate(() => {
        const documentHeight = document.documentElement.scrollHeight;
        const viewportHeight = document.documentElement.clientHeight;
        window.scrollTo(0, Number.MAX_SAFE_INTEGER);
        return { documentHeight, viewportHeight, scrollY: window.scrollY };
      });
      assert.ok(
        result.documentHeight > result.viewportHeight && result.scrollY > 0,
        `document did not scroll (${JSON.stringify(result)})`,
      );
      await page.evaluate(() => window.scrollTo(0, 0));
    });
    await check("dialog scroller chrome hidden", () => assertThicknessZero("dialog-scroll", p1["dialog-scroll"]));
    await check("xterm viewport chrome hidden", () => assertThicknessZero("xterm", p1.xterm));
    await check("radix vertical scrollbar hidden", () => assertChromeHidden("radix-v-scrollbar", p1["radix-v-scrollbar"]));
    await check("radix horizontal scrollbar hidden", () => assertChromeHidden("radix-h-scrollbar", p1["radix-h-scrollbar"]));
    await check("monaco vertical slider hidden", () =>
      assert.ok(!p1["monaco-v-scrollbar"], `monaco vertical slider still visible: ${JSON.stringify(p1["monaco-v-scrollbar"])}`));
    await check("monaco horizontal slider hidden", () =>
      assert.ok(!p1["monaco-h-scrollbar"], `monaco horizontal slider still visible: ${JSON.stringify(p1["monaco-h-scrollbar"])}`));
    await check("enabled monaco minimap slider hidden", () =>
      assert.ok(!p1["monaco-minimap-slider"], `monaco minimap slider still visible: ${JSON.stringify(p1["monaco-minimap-slider"])}`));
    await check("hidden chrome leaves no reserved gutter", () => {
      assert.equal(p1["native-borderless-no-gutter"], true, "borderless scroller reserves a gutter for the scrollbar");
    });

    await check("dialog closes before scroll checks", async () => {
      await page.keyboard.press("Escape");
      await page.getByTestId("dialog-scroll").waitFor({ state: "detached", timeout: 10_000 });
    });

    // Scrolling survives: wheel on every surface.
    const r = (sel, prop) => () =>
      page.evaluate(
        ([sel, prop]) => document.querySelector(sel)[prop],
        [sel, prop],
      );
    const editorVisibleLines = () =>
      page.evaluate(() =>
        Array.from(document.querySelectorAll('[data-testid="editor-box"] .view-line'))
          .slice(0, 4)
          .map((line) => line.textContent ?? "")
          .join("\n"),
      );

    await check("wheel scrolls native vertical", async () => {
      const d = await wheelDelta(page, page.getByTestId("native-both"), 0, 240, r('[data-testid="native-both"]', "scrollTop"));
      assert.ok(d > 0, `vertical wheel scrolled by ${d}`);
    });
    await check("wheel scrolls native horizontal", async () => {
      const d = await wheelDelta(page, page.getByTestId("native-both"), 240, 0, r('[data-testid="native-both"]', "scrollLeft"));
      assert.ok(d > 0, `horizontal wheel scrolled by ${d}`);
    });
    await check("wheel scrolls nested inner", async () => {
      const d = await wheelDelta(page, page.getByTestId("native-nested-inner"), 0, 240, r('[data-testid="native-nested-inner"]', "scrollTop"));
      assert.ok(d > 0, `nested wheel scrolled by ${d}`);
    });
    await check("wheel scrolls radix viewport (scroll survives hidden scrollbar)", async () => {
      await page.getByTestId("radix-v").hover();
      await until(() => page.locator('[data-testid="radix-v"] [data-slot="scroll-area-viewport"]').evaluate(
        (viewport) => getComputedStyle(viewport).overflowY === "scroll",
      ), "Radix viewport never enabled scrolling");
      const d = await wheelDelta(page, page.getByTestId("radix-v"), 0, 240, r('[data-testid="radix-v"] [data-slot="scroll-area-viewport"]', "scrollTop"));
      const state = await page.locator('[data-testid="radix-v"] [data-slot="scroll-area-viewport"]').evaluate((vp) => {
        const box = vp.getBoundingClientRect();
        return { top: vp.scrollTop, height: vp.scrollHeight, client: vp.clientHeight,
          box: { x: box.x, y: box.y, width: box.width, height: box.height }, viewport: [innerWidth, innerHeight],
          target: document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.outerHTML.slice(0, 180) };
      });
      assert.ok(d > 0, `radix viewport wheel scrolled by ${d}: ${JSON.stringify(state)}`);
    });
    await check("wheel scrolls radix horizontal", async () => {
      const d = await wheelDelta(page, page.getByTestId("radix-h"), 240, 0, () =>
        page.evaluate(() => document.querySelector('[data-testid="radix-h"] [data-radix-scroll-area-viewport]').scrollLeft),
      );
      assert.ok(d > 0, `radix horizontal wheel scrolled by ${d}`);
    });
    await check("wheel scrolls dialog scroller", async () => {
      await page.getByText("open-dialog").click();
      await page.getByTestId("dialog-scroll").waitFor({ timeout: 10_000 });
      const d = await wheelDelta(page, page.getByTestId("dialog-scroll"), 0, 240, r('[data-testid="dialog-scroll"]', "scrollTop"));
      assert.ok(d > 0, `dialog wheel scrolled by ${d}`);
      await page.keyboard.press("Escape");
      await page.getByTestId("dialog-scroll").waitFor({ state: "detached" });
    });
    await check("wheel scrolls monaco", async () => {
      await page.getByTestId("editor-box").hover();
      const before = await editorVisibleLines();
      await page.mouse.wheel(0, 240);
      await page.waitForTimeout(150);
      const after = await editorVisibleLines();
      assert.notEqual(after, before, "monaco visible lines did not change after wheel scrolling");
    });

    // Keyboard scrolling on focusable scrollers + editor.
    await check("keyboard scrolls native container", async () => {
      const container = page.getByTestId("native-both");
      await page.bringToFront();
      await container.focus();
      const before = await r('[data-testid="native-both"]', "scrollTop")();
      await container.press("PageDown");
      await until(async () => (await r('[data-testid="native-both"]', "scrollTop")()) > before,
        "keyboard did not scroll the focused native container", 3000);
      const after = await r('[data-testid="native-both"]', "scrollTop")();
      assert.ok(after - before > 0, `keyboard scrolled by ${after - before}`);
    });
    await check("keyboard scrolls monaco", async () => {
      const input = page.locator('[data-testid="editor-box"] .monaco-editor :is(.inputarea, .native-edit-context)');
      // Put the cursor in the visible text, rather than focusing an offscreen
      // cursor left behind by the preceding wheel check.
      await page.locator('[data-testid="editor-box"] .monaco-editor').click({ position: { x: 100, y: 80 } });
      const before = await editorVisibleLines();
      await input.press("PageDown");
      await until(async () => (await editorVisibleLines()) !== before, "monaco did not scroll after PageDown", 3000);
      const after = await editorVisibleLines();
      assert.notEqual(after, before, "monaco visible lines did not change after PageDown");
    });

    // Programmatic navigation + anchor stability.
    await check("programmatic scroll and anchor stability", async () => {
      const result = await page.evaluate(() => {
        const box = document.querySelector('[data-testid="native-both"]');
        const before = box.getBoundingClientRect().width;
        box.scrollTop = 999999;
        const max = box.scrollTop;
        const anchor = document.getElementById("native-anchor").getBoundingClientRect();
        const boxRect = box.getBoundingClientRect();
        return {
          max,
          scrollable: box.scrollHeight - box.clientHeight,
          widthBefore: before,
          widthAfter: box.getBoundingClientRect().width,
          anchorVisible: anchor.bottom <= boxRect.bottom + 1 && anchor.top >= boxRect.top - 1,
          radixReachable: (() => {
            const vp = document.querySelector('[data-testid="radix-v"] [data-slot="scroll-area-viewport"]');
            vp.scrollTop = 999999;
            return vp.scrollTop > 0 && Math.abs(vp.scrollTop - (vp.scrollHeight - vp.clientHeight)) < 2;
          })(),
        };
      });
      assert.ok(result.scrollable > 0, "container must actually overflow");
      assert.ok(result.max > 0, "programmatic scroll must reach content");
      assert.ok(result.anchorVisible, "anchor escaped the viewport after scrolling");
      assert.equal(result.widthBefore, result.widthAfter, "layout shifted during scroll");
      assert.ok(result.radixReachable, "radix viewport must still reach its content end");
    });

    // Interactive chrome capture: Radix scrollbars mount on hover, Monaco
    // sliders fade in on scroll — measure during the visibility window.
    await check("interactive chrome capture", async () => {
      await page.getByTestId("radix-v").hover();
      await page.waitForTimeout(200);
      await page.getByTestId("radix-h").hover();
      await page.waitForTimeout(200);
      await page.mouse.move(10, 10);
      await page.getByTestId("editor-box").hover();
      await page.mouse.wheel(0, 200);
      await page.waitForTimeout(120);
      await page.getByTestId("terminal-box").hover();
      await page.mouse.wheel(0, -200);
      await page.waitForTimeout(120);
    });
    const p1i = await probes(page);
    await check("radix vertical scrollbar hidden while hovered", () => assertChromeHidden("radix-v-scrollbar", p1i["radix-v-scrollbar"]));
    await check("radix horizontal scrollbar hidden while hovered", () => assertChromeHidden("radix-h-scrollbar", p1i["radix-h-scrollbar"]));
    await check("monaco vertical slider hidden while scrolling", () =>
      assert.ok(!p1i["monaco-v-scrollbar"], `monaco vertical slider visible during scroll: ${JSON.stringify(p1i["monaco-v-scrollbar"])}`));
    await check("monaco horizontal slider hidden while scrolling", () =>
      assert.ok(!p1i["monaco-h-scrollbar"], `monaco horizontal slider visible during scroll: ${JSON.stringify(p1i["monaco-h-scrollbar"])}`));
    await check("xterm scrollbar and thumb hidden while scrolling", () => {
      assertChromeHidden("xterm vertical scrollbar", p1i["xterm-scrollbar"]);
      assertChromeHidden("xterm vertical slider", p1i["xterm-slider"]);
    });

    const p2 = await probes(page);
    await check("geometry stable after scrolling", () => assertThicknessZero("native-both", p2["native-both"]));

    await check("production diff editor mounts through EditorPane", async () => {
      await page.evaluate(() => window.__setEditorVariant?.("diff"));
      await until(
        () => page.evaluate(() => Boolean(document.querySelector('[data-testid="editor-box"] .monaco-diff-editor'))),
        "production diff editor never mounted",
      );
      await until(
        () => page.evaluate(() => document.querySelectorAll('[data-testid="editor-box"] .monaco-diff-editor .monaco-scrollable-element').length >= 2),
        "production diff editor scroll views never mounted",
      );
      await page.waitForTimeout(250);
      const box = await page.getByTestId("editor-box").boundingBox();
      assert.ok(box, "production diff editor has no layout box");
      await page.mouse.move(box.x + box.width * 0.75, box.y + box.height / 2);
      await page.mouse.wheel(0, 240);
      await page.waitForTimeout(150);
    });
    const diffProbes = await probes(page);
    await check("production diff vertical slider hidden", () =>
      assert.ok(!diffProbes["monaco-diff-v-scrollbar"], `diff vertical slider visible: ${JSON.stringify(diffProbes["monaco-diff-v-scrollbar"])}`));
    await check("production diff horizontal slider hidden", () =>
      assert.ok(!diffProbes["monaco-diff-h-scrollbar"], `diff horizontal slider visible: ${JSON.stringify(diffProbes["monaco-diff-h-scrollbar"])}`));

    // A single stylesheet owns the policy. Removing only its scrollbar rules
    // must reveal real chrome on the same production components. This catches
    // missing selectors, non-overflowing fixtures and a second policy hidden
    // in editor options; none may silently count as successful coverage.
    await check("removing the stylesheet policy reveals every scrollbar implementation", async () => {
      // Reload to start in the normal editor; then visit the diff editor once.
      // Disposing a diff model while remounting its normal sibling is unrelated
      // to scrollbar rendering and is outside this fixture's contract.
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.bringToFront();
      await until(() => page.evaluate(() => window.__ready === true), "fixture did not reload");
      await until(() => page.evaluate(() => typeof window.__termFeed === "function"), "terminal connection never mounted");
      await page.getByText("feed-terminal").click();
      const removed = await page.evaluate(() => {
        let count = 0;
        const removePolicy = (sheet) => {
          for (let i = sheet.cssRules.length - 1; i >= 0; i--) {
            const rule = sheet.cssRules[i];
            if (rule.selectorText && (
              rule.selectorText.includes("scrollbar") ||
              rule.selectorText.includes("minimap-slider") ||
              rule.style.getPropertyValue("scrollbar-width") === "none"
            )) {
              sheet.deleteRule(i);
              count++;
            } else if (rule.cssRules) removePolicy(rule);
          }
        };
        const style = document.querySelector('style[data-vite-dev-id$="/src/styles.css"]');
        if (!style?.sheet) throw new Error("production stylesheet is missing");
        removePolicy(style.sheet);
        return count;
      });
      assert.ok(removed > 0, "no production scrollbar policy was removed");
      const native = await probes(page);
      assert.ok(native.document.v > 0, "classic document scrollbar must be measurable without the policy");
      assert.ok(native["native-both"].v > 0 && native["native-both"].h > 0,
        "both native scrollbar axes must be measurable without the policy");

      const visible = async (selector) => {
        const locator = page.locator(selector);
        await until(() => locator.count(), `scrollbar implementation is missing: ${selector}`, 3000);
        await until(() => locator.evaluateAll((elements) => elements.some((element) => {
          const style = getComputedStyle(element);
          const box = element.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" &&
            Number(style.opacity) > 0 && box.width > 0 && box.height > 0;
        })), `scrollbar did not become visible without the policy: ${selector}`, 3000);
      };
      for (const id of ["radix-v", "radix-h"]) {
        await page.getByTestId(id).hover();
        await visible(`[data-testid="${id}"] [data-slot="scroll-area-scrollbar"]`);
      }
      await page.getByTestId("terminal-box").hover();
      await page.mouse.wheel(0, -240);
      await visible('[data-testid="terminal-box"] .xterm-scrollable-element > .scrollbar.vertical');
      await visible('[data-testid="terminal-box"] .xterm-scrollable-element > .scrollbar.vertical > .slider');

      for (const variant of ["normal", "diff"]) {
        await page.evaluate((variant) => window.__setEditorVariant(variant), variant);
        await until(() => page.locator('[data-testid="editor-box"] .monaco-editor').count(), "editor did not mount");
        const box = await page.getByTestId("editor-box").boundingBox();
        await page.mouse.move(box.x + box.width * 0.75, box.y + box.height / 2);
        await page.mouse.wheel(240, 240);
        await visible('[data-testid="editor-box"] .monaco-scrollable-element > .scrollbar.vertical');
        await visible('[data-testid="editor-box"] .monaco-scrollable-element > .scrollbar.horizontal');
        if (variant === "normal") await visible('[data-testid="editor-box"] .minimap-slider');
      }
    });

    await context.close();
  } finally {
    await server.close();
    if (browser) await browser.close();
  }
  if (failures.length) {
    throw new AggregateError(failures, `${failures.length} scrollbar-chrome check(s) failed`);
  }
  console.log("scrollbar-chrome browser acceptance: PASS");
}

run().catch((error) => {
  console.error(error.message ?? error);
  process.exit(1);
});
