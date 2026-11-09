// Real-browser coverage for text typed into the message box while a chat's thread is rebuilt: when the agent's answer
// replaces the engine's saved copy, what was typed so far (and the keyboard focus) must survive; text the person cleared
// stays cleared; and a draft belongs to its own chat only.
// Run after `npm ci` and `npx playwright-core install chromium`:
//   npm run test:e2e:composer-draft-rebuild
// Browser resolution: CHROME_PATH if set, else the Chrome app on macOS, else playwright's own chromium.
import assert from "node:assert/strict";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const gem = (name, displayName) => ({
  name,
  identity: name,
  roster: "test",
  display_name: displayName,
  avatar: { kind: "gem" },
  color: [36, 117, 190],
  agent: "coder",
  agent_type: "coder",
  profile: "default",
  available: true,
  active: false,
  pool: true,
});
const session = (id, identity, name) => ({
  session_id: id,
  name,
  agent: "coder",
  identity,
  profile: "default",
  workspace: `/${id}`,
  external: false,
});
const FIRST = session("seed-demo", "local-coder", "Seed demo");
const SECOND = session("other-demo", "local-reviewer", "Other demo");
const POOL = [gem("local-coder", "Local Coder"), gem("local-reviewer", "Local Reviewer")];

const row = (role, content) => ({
  role,
  content,
  thinking: null,
  timestamp: "2026-11-08T19:20:00.000000",
  metadata: {},
});
const ASK = row("user", "check the two failed units");
const SAVED = [ASK, row("assistant", "Saved copy from disk")];
const ANSWERED = [ASK, row("assistant", "Answer from the agent")];
const OTHER = [row("user", "a second question"), row("assistant", "Reply in the other chat")];

// The engine serves a disk copy marked stale until the agent answers, then the agent's own history.
function pageOf(history, stale) {
  return {
    history,
    start_index: 0,
    end_index: history.length,
    has_more_older: false,
    older_cursor: null,
    has_more_newer: false,
    newer_cursor: null,
    source: stale ? "disk" : "mirror",
    stale,
    last_turn_error: null,
  };
}

async function openChat(browser, baseUrl, sessions, historyOf) {
  const view = await openPage(browser, baseUrl, makeFixtures({ sessions, poolEntries: POOL }), { viewport: { width: 1280, height: 900 } });
  // Routes added later win over the harness's own, which serves one fixed history.
  await view.page.route(
    (url) => /\/sessions\/[^/]+\/history$/.test(url.pathname),
    (route) => {
      const id = new URL(route.request().url()).pathname.split("/").at(-2);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(historyOf(id)) });
    },
  );
  await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  return view;
}

const messageBox = (page) => page.getByRole("textbox", { name: "Message input" });
const focused = (page) => messageBox(page).evaluate((box) => box === document.activeElement);
const sidebarRow = (page, name) => page.getByRole("button", { name: new RegExp(`^${name}`) }).first();

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

    // 1. Typing while the chat shows the saved copy: the agent's answer rebuilds the thread, the draft stays.
    let answered = false;
    const waiting = await openChat(browser, baseUrl, [FIRST], () => (answered ? pageOf(ANSWERED, false) : pageOf(SAVED, true)));
    try {
      const { page } = waiting;
      await page.getByText("Saved copy from disk").waitFor({ timeout: 10_000 });
      await messageBox(page).click();
      await messageBox(page).pressSequentially("half-written reply");
      assert.equal(await messageBox(page).inputValue(), "half-written reply");

      answered = true;
      await page.getByText("Answer from the agent").waitFor({ timeout: 10_000 });
      assert.equal(await page.getByText("Saved copy from disk").count(), 0, "the saved copy is still shown");

      assert.equal(await messageBox(page).inputValue(), "half-written reply", "the draft was lost when the agent's history replaced the saved copy");
      assert.ok(await focused(page), "the message box lost focus when the thread was rebuilt");
      await page.keyboard.type(" and more");
      assert.equal(await messageBox(page).inputValue(), "half-written reply and more", "typing after the rebuild went nowhere");
      assert.deepEqual(waiting.pageErrors, []);
      assert.deepEqual(waiting.externalRequests, []);
    } finally {
      await waiting.context.close();
    }

    // 2. Text the person deleted does not come back when the thread is rebuilt.
    let answeredAfterDelete = false;
    const cleared = await openChat(browser, baseUrl, [FIRST], () => (answeredAfterDelete ? pageOf(ANSWERED, false) : pageOf(SAVED, true)));
    try {
      const { page } = cleared;
      await page.getByText("Saved copy from disk").waitFor({ timeout: 10_000 });
      await messageBox(page).click();
      await messageBox(page).pressSequentially("typed then deleted");
      await page.keyboard.press("ControlOrMeta+A");
      await page.keyboard.press("Backspace");
      assert.equal(await messageBox(page).inputValue(), "");

      answeredAfterDelete = true;
      await page.getByText("Answer from the agent").waitFor({ timeout: 10_000 });
      assert.equal(await messageBox(page).inputValue(), "", "deleted text came back when the thread was rebuilt");
      assert.deepEqual(cleared.pageErrors, []);
    } finally {
      await cleared.context.close();
    }

    // 3. A draft belongs to its own chat: the other chat's box is empty, and the first chat has its text when reopened.
    const two = await openChat(browser, baseUrl, [FIRST, SECOND], (id) => pageOf(id === SECOND.session_id ? OTHER : ANSWERED, false));
    try {
      const { page } = two;
      // Which chat the page opens on its own is not this test's business; open the first one.
      await sidebarRow(page, "Local Coder").click();
      await page.getByText("Answer from the agent").waitFor({ timeout: 10_000 });
      await messageBox(page).click();
      await messageBox(page).pressSequentially("for the first chat only");

      await sidebarRow(page, "Local Reviewer").click();
      await page.getByText("Reply in the other chat").waitFor({ timeout: 10_000 });
      assert.equal(await messageBox(page).inputValue(), "", "a draft followed the person into another chat");

      await sidebarRow(page, "Local Coder").click();
      await page.getByText("Answer from the agent").waitFor({ timeout: 10_000 });
      assert.equal(await messageBox(page).inputValue(), "for the first chat only", "the first chat lost its draft while another chat was open");
      assert.deepEqual(two.pageErrors, []);
    } finally {
      await two.context.close();
    }
    console.log("composer draft rebuild browser acceptance: ok");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
