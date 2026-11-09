// Real-browser coverage for the Todo card under the Session Controls panel (issue #973): the card shows the
// agent's latest `todo:` list, opens and closes from its header, remembers the choice, sits on a filled surface
// with no line borders, never shows another agent's list, is absent when the chat has none, and in a short window
// keeps the panel's controls and the whole list within a person's reach, and its scroll box does not cut off the shadows.
// Run after `npm ci` and `npx playwright-core install chromium`:
//   npm run test:e2e:todo-card
// Browser resolution: CHROME_PATH if set, else the Chrome app on macOS, else playwright's own chromium.
import assert from "node:assert/strict";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION = {
  session_id: "todo-demo",
  name: "Todo demo",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/todo-demo",
  external: false,
};

const row = (role, content, metadata = {}) => ({
  role,
  content,
  thinking: null,
  timestamp: "2026-11-08T19:20:00.000000",
  metadata,
});

const FIRST_LIST = "todo:\n  [x] confirm scope\n  [ ] inspect unit states\n  [ ] inspect recent logs\n  [ ] assess the evidence";
const LATEST_LIST = [
  "todo:",
  "  [x] inspected unit state",
  "  [x] confirmed no journal entries",
  "  [x] read the health summary",
  "  [ ] check current host indicators to avoid treating old logs as proof of a live outage",
  "  [ ] report findings",
].join("\n");
const DONE_LIST = "todo:\n  [x] inspected both units\n  [x] checked the host\n  [x] assessed the evidence";
const OTHER_AGENTS_LIST =
  "<agent_hud>\n[hub:sapphire->local-coder]\n+ [hub channel: sapphire -> local-coder]\n  status: ok\n  todo:\n    [ ] someone else's task\n</agent_hud>";

// Sixty steps: far more than the card shows at once.
const LONG_LIST = ["todo:", ...Array.from({ length: 60 }, (_, index) => `  [${index < 20 ? "x" : " "}] step ${index + 1} of the long plan`)].join("\n");

const history = (...rows) => makeFixtures({ sessions: [SESSION], history: rows });
const withLists = () =>
  history(
    row("user", "check the two failed units"),
    row("assistant", FIRST_LIST),
    row("assistant", `Task update\n\n${LATEST_LIST}`),
    row("user", OTHER_AGENTS_LIST, { agent_hud: true, agent_hud_sources: ["hub"] }),
  );

async function openChat(browser, baseUrl, fixtures, { width = 1280, height = 900, isMobile = false } = {}) {
  const view = await openPage(browser, baseUrl, fixtures, { viewport: { width, height }, isMobile });
  await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  return view;
}

// Wide windows dock the panel; narrow ones keep it in a sheet the chat header opens.
async function openControls(page, width) {
  if (width >= 1024) {
    await page.getByRole("complementary", { name: "Session Controls" }).waitFor({ state: "visible", timeout: 10_000 });
    return;
  }
  await page.getByRole("button", { name: "Session Controls" }).click({ timeout: 10_000 });
  await page.getByRole("dialog").waitFor({ state: "visible" });
  // The sheet slides in; measure it where it comes to rest.
  await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState !== "running"), null, { timeout: 5_000 });
}

const todoCard = (page) => page.getByTestId("session-todo");
const todoToggle = (page) => todoCard(page).getByRole("button", { name: /^Todo/ });
const todoItems = (page) => todoCard(page).getByTestId("session-todo-item");

// How far an unchecked box stands out from the card surface it sits on, as the WCAG contrast ratio of the two sRGB
// colors. The canvas turns any CSS color (the oklch tokens, alpha mixes) into sRGB, laid over the card's own surface.
// With no border to draw it, a box the same shade as the card reads as a missing marker.
const boxContrast = (page) =>
  page.evaluate(() => {
    const card = document.querySelector('[data-testid="session-todo"]');
    const box = card.querySelector('[role="img"][aria-label="Open"]');
    const context = Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
    const paint = (color) => {
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
    };
    const read = () => [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
    paint(getComputedStyle(document.body).backgroundColor);
    paint(getComputedStyle(card).backgroundColor);
    const surface = read();
    paint(getComputedStyle(box).backgroundColor);
    const boxed = read();
    const luminance = ([r, g, b]) => {
      const linear = (value) => ((value /= 255) <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
    };
    const [lighter, darker] = [luminance(surface), luminance(boxed)].sort((a, b) => b - a);
    return { ratio: (lighter + 0.05) / (darker + 0.05), surface, boxed };
  });
// The thread's own unchecked task box measures 1.66 against the page (dark theme); anything under 1.5 is hard to see.
const MIN_BOX_CONTRAST = 1.5;

async function waitForCard(page) {
  try {
    await todoCard(page).waitFor({ state: "visible", timeout: 8_000 });
  } catch {
    assert.fail("no Todo card appeared under the Session Controls panel");
  }
}

// A person can use an element when all of it is inside the window and nothing covers or clips it there.
const shown = (locator) =>
  locator.evaluate((node) => {
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) return false;
    if (box.top < 0 || box.left < 0 || box.bottom > window.innerHeight || box.right > window.innerWidth) return false;
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return !!hit && (hit === node || node.contains(hit));
  });

// Scrolls the way a person does, with the mouse wheel over `over`, until `target` is shown. Scrolling by script reaches
// into boxes that clip with no scrollbar and no wheel, so it would call an unreachable row reachable.
async function wheelTo(page, over, target) {
  for (let step = 0; step < 14; step += 1) {
    if (await shown(target)) return true;
    const spot = await over.evaluate((node) => {
      const box = node.getBoundingClientRect();
      const [top, bottom] = [Math.max(box.top, 0), Math.min(box.bottom, window.innerHeight)];
      const [left, right] = [Math.max(box.left, 0), Math.min(box.right, window.innerWidth)];
      return bottom > top && right > left ? { x: (left + right) / 2, y: (top + bottom) / 2 } : null;
    });
    if (!spot) return false;
    await page.mouse.move(spot.x, spot.y);
    await page.mouse.wheel(0, 300);
    // Chrome keeps a wheel gesture on the box it began scrolling until the wheel rests, so a list that has reached its
    // end would hold every later tick. A person's next flick is a new gesture; so is this one.
    await page.waitForTimeout(600);
  }
  return shown(target);
}

// How many pixels of two PNG screenshots of one clip differ by at least `floor` (of 255) in some color channel, read through a canvas.
const differingPixels = (page, a, b, floor) =>
  page.evaluate(async ([first, second, least]) => {
    const read = async (data) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${data}`)).blob());
      const context = new OffscreenCanvas(bitmap.width, bitmap.height).getContext("2d");
      context.drawImage(bitmap, 0, 0);
      return context.getImageData(0, 0, bitmap.width, bitmap.height).data;
    };
    const [x, y] = await Promise.all([read(first), read(second)]);
    let count = 0;
    for (let i = 0; i < x.length; i += 4) {
      if (Math.max(Math.abs(x[i] - y[i]), Math.abs(x[i + 1] - y[i + 1]), Math.abs(x[i + 2] - y[i + 2])) >= least) count += 1;
    }
    return count;
  }, [a, b, floor]);

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    const executablePath = process.env.CHROME_PATH
      ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    browser = await chromium.launch({
      headless: true,
      ...(executablePath ? { executablePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    // 1. The latest list wins, from the agent's own messages only.
    const wide = await openChat(browser, baseUrl, withLists());
    try {
      await openControls(wide.page, 1280);
      await waitForCard(wide.page);
      assert.equal(await todoToggle(wide.page).getAttribute("aria-expanded"), "true", "an unfinished list starts open");
      assert.match(await todoToggle(wide.page).innerText(), /3 of 5/, "the header counts done items of all");
      const items = await todoItems(wide.page).evaluateAll((nodes) =>
        nodes.map((node) => ({ done: node.getAttribute("data-done"), text: node.textContent.trim() })),
      );
      assert.deepEqual(items.map((item) => item.done), ["true", "true", "true", "false", "false"]);
      assert.equal(items[3].text, "check current host indicators to avoid treating old logs as proof of a live outage");
      const cardText = await todoCard(wide.page).innerText();
      assert.ok(!cardText.includes("someone else's task"), `another agent's list leaked in: ${cardText}`);
      assert.ok(!cardText.includes("confirm scope"), `an older list leaked in: ${cardText}`);

      // Under the panel, on a filled surface, with no line anywhere in it.
      const look = await wide.page.evaluate(() => {
        const card = document.querySelector('[data-testid="session-todo"]');
        const panel = document.querySelector('aside[aria-label="Session Controls"]');
        const lines = [card, ...card.querySelectorAll("*")].filter((node) =>
          ["Top", "Right", "Bottom", "Left"].some((side) => parseFloat(getComputedStyle(node)[`border${side}Width`]) > 0),
        );
        const struck = [...card.querySelectorAll('[data-done="true"]')].map(
          (node) => getComputedStyle(node.querySelector("[data-todo-text]")).textDecorationLine,
        );
        return {
          below: card.getBoundingClientRect().top >= panel.getBoundingClientRect().bottom - 1,
          background: getComputedStyle(card).backgroundColor,
          page: getComputedStyle(document.body).backgroundColor,
          lines: lines.map((node) => node.tagName),
          struck,
          width: [card.getBoundingClientRect().width, panel.getBoundingClientRect().width],
        };
      });
      assert.ok(look.below, `the card is not under the panel: ${JSON.stringify(look)}`);
      assert.notEqual(look.background, "rgba(0, 0, 0, 0)", "the card has no background");
      assert.notEqual(look.background, look.page, "the card is not a surface of its own");
      assert.deepEqual(look.lines, [], "the card draws a line border");
      assert.ok(look.struck.length === 3 && look.struck.every((line) => line.includes("line-through")), "done items are not struck through");
      assert.ok(Math.abs(look.width[0] - look.width[1]) <= 1, `the card is not as wide as the panel: ${look.width}`);
      const wideBox = await boxContrast(wide.page);
      assert.ok(wideBox.ratio >= MIN_BOX_CONTRAST, `unchecked boxes do not stand out from the card: ${JSON.stringify(wideBox)}`);

      // The header opens and closes it, and the choice is remembered.
      await todoToggle(wide.page).click();
      assert.equal(await todoToggle(wide.page).getAttribute("aria-expanded"), "false");
      assert.equal(await todoItems(wide.page).count(), 0, "a closed card still lists its items");
      await wide.page.reload({ waitUntil: "domcontentloaded" });
      await openControls(wide.page, 1280);
      await waitForCard(wide.page);
      assert.equal(await todoToggle(wide.page).getAttribute("aria-expanded"), "false", "the closed choice was not remembered");
      await todoToggle(wide.page).click();
      assert.equal(await todoToggle(wide.page).getAttribute("aria-expanded"), "true");
      assert.equal(await todoItems(wide.page).count(), 5);
      assert.deepEqual(wide.pageErrors, []);
      assert.deepEqual(wide.externalRequests, []);
    } finally {
      await wide.context.close();
    }

    // 2. A finished list starts closed; a chat with no list has no card.
    const finished = await openChat(browser, baseUrl, history(row("user", "go"), row("assistant", `Done.\n\n${DONE_LIST}`)));
    try {
      await openControls(finished.page, 1280);
      await waitForCard(finished.page);
      assert.equal(await todoToggle(finished.page).getAttribute("aria-expanded"), "false", "a finished list starts open");
      assert.match(await todoToggle(finished.page).innerText(), /3 of 3/);
      await todoToggle(finished.page).click();
      assert.equal(await todoItems(finished.page).count(), 3);
    } finally {
      await finished.context.close();
    }

    const none = await openChat(browser, baseUrl, history(row("user", "hi"), row("assistant", "hello there")));
    try {
      await openControls(none.page, 1280);
      await none.page.getByText("Model and Profile").waitFor({ state: "visible" });
      assert.equal(await todoCard(none.page).count(), 0, "a chat with no list shows a Todo card");
    } finally {
      await none.context.close();
    }

    // 2b. A list written as an example inside code, wherever Markdown puts that code (a list item's fence, a tilde
    // fence under a nested item, an indented block), is not the agent's list: the card keeps the one before it.
    const EXAMPLES = [
      "How a list looks:",
      "",
      "- ```",
      "  todo:",
      "    [ ] example in a list item's fence",
      "  ```",
      "",
      "1. step one",
      "   - ~~~",
      "     todo:",
      "       [ ] example in a nested item's tilde fence",
      "     ~~~",
      "",
      "And indented:",
      "",
      "    todo:",
      "      [ ] example in an indented block",
    ].join("\n");
    const sampled = await openChat(browser, baseUrl, history(row("user", "go"), row("assistant", FIRST_LIST), row("assistant", EXAMPLES)));
    try {
      await openControls(sampled.page, 1280);
      await waitForCard(sampled.page);
      const texts = await todoItems(sampled.page).evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
      assert.deepEqual(
        texts,
        ["confirm scope", "inspect unit states", "inspect recent logs", "assess the evidence"],
        `an example list inside code became the card's list: ${JSON.stringify(texts)}`,
      );
      assert.deepEqual(sampled.pageErrors, []);
    } finally {
      await sampled.context.close();
    }

    // 3. Narrow windows keep it in the Session Controls sheet, inside the window.
    for (const { width, height, isMobile } of [{ width: 390, height: 844, isMobile: true }, { width: 820, height: 1180, isMobile: false }]) {
      const narrow = await openChat(browser, baseUrl, withLists(), { width, height, isMobile });
      try {
        await openControls(narrow.page, width);
        await waitForCard(narrow.page);
        assert.equal(await todoItems(narrow.page).count(), 5, `${width}px: the list is not shown in the sheet`);
        const fits = await narrow.page.evaluate(() => {
          const card = document.querySelector('[data-testid="session-todo"]').getBoundingClientRect();
          return { overflow: document.documentElement.scrollWidth - window.innerWidth, left: card.left, right: card.right, width: window.innerWidth };
        });
        assert.ok(fits.overflow <= 0, `${width}px: the page overflows: ${JSON.stringify(fits)}`);
        assert.ok(fits.left >= 0 && fits.right <= fits.width, `${width}px: the card leaves the window: ${JSON.stringify(fits)}`);
        const sheetBox = await boxContrast(narrow.page);
        assert.ok(sheetBox.ratio >= MIN_BOX_CONTRAST, `${width}px: unchecked boxes do not stand out from the card: ${JSON.stringify(sheetBox)}`);
      } finally {
        await narrow.context.close();
      }
    }
    // 4. A long list in a short window: the panel's controls and the whole list stay within reach.
    const problems = [];
    for (const { width, height, where } of [
      { width: 1280, height: 300, where: "docked panel" },
      { width: 820, height: 300, where: "sheet" },
    ]) {
      const short = await openChat(browser, baseUrl, history(row("user", "plan it"), row("assistant", `Plan\n\n${LONG_LIST}`)), { width, height });
      try {
        const page = short.page;
        const check = (ok, message) => ok || problems.push(`${where}, ${height}px tall: ${message}`);
        if (where === "sheet") await openControls(page, width);
        await waitForCard(page);
        const panel = page.getByRole("complementary", { name: "Session Controls" }).or(page.getByRole("dialog"));
        const size = await todoCard(page).evaluate((node) => ({ card: node.getBoundingClientRect().height, window: window.innerHeight }));
        check(size.card <= size.window, `the card is ${Math.round(size.card)}px tall, taller than the window, so its header and the end of its list cannot be on screen together`);
        check(await wheelTo(page, panel, panel.getByTestId("chat-tab")), "the Chat button at the top of the panel cannot be brought on screen");
        check(await wheelTo(page, panel, panel.getByRole("button", { name: "Clear History" })), "the panel's last row, Clear History, cannot be brought on screen");
        check(await wheelTo(page, panel, todoToggle(page)), "the Todo header cannot be brought on screen");
        check(await wheelTo(page, todoCard(page).locator("ul"), todoItems(page).last()), "the last of the 60 steps cannot be brought on screen");
        assert.deepEqual(short.pageErrors, []);
      } finally {
        await short.context.close();
      }
    }
    assert.deepEqual(problems, [], "a long list in a short window leaves something out of reach");
    // 5. The column's scroll box leaves the panel's and the card's shadows room. A box that hugs them cuts the shadow off at
    // its edge, which the light theme (not shipped, but in the tokens) shows: compare the column with and without the clip.
    const lit = await openChat(browser, baseUrl, withLists(), { width: 1280, height: 900 });
    try {
      await openControls(lit.page, 1280);
      await waitForCard(lit.page);
      await lit.page.evaluate(() => document.documentElement.classList.remove("dark"));
      const column = lit.page.locator('aside[aria-label="Session Controls"]').locator("xpath=..");
      const box = await column.boundingBox();
      const clip = { x: Math.max(box.x - 40, 0), y: Math.max(box.y - 40, 0), width: Math.min(box.width + 80, 1280 - Math.max(box.x - 40, 0)), height: box.height + 80 };
      const shoot = async (overflowY) => {
        await column.evaluate((node, value) => { node.style.overflowY = value; }, overflowY);
        await lit.page.waitForTimeout(150);
        return (await lit.page.screenshot({ clip })).toString("base64");
      };
      const clipped = await shoot("");
      const free = await shoot("visible");
      // A cut shadow is thousands of pixels along the column's edges; antialiasing at an icon's edge can move a couple.
      const cut = await differingPixels(lit.page, clipped, free, 4);
      assert.ok(cut <= 8, `the column's scroll box cuts the shadows off in the light theme: ${cut} pixels differ by 4 of 255 or more from an unclipped column`);
    } finally {
      await lit.context.close();
    }
    console.log("todo card browser acceptance: ok");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
