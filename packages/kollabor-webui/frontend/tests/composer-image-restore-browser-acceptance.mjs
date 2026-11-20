// Real-browser coverage for images pasted or attached in the message box (issue #1103): the image stays in that
// chat's box, ready to send, after switching to another chat and back and after a full page reload, and no
// "paste it again" notice appears when it is still there.
// Run after `npm ci` and `npx playwright-core install chromium`:
//   npm run test:e2e:composer-image-restore
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
const pageOf = (history) => ({
  history,
  start_index: 0,
  end_index: history.length,
  has_more_older: false,
  older_cursor: null,
  has_more_newer: false,
  newer_cursor: null,
  source: "mirror",
  stale: false,
  last_turn_error: null,
});
const FIRST_HISTORY = [row("user", "first question"), row("assistant", "Reply in the first chat")];
const SECOND_HISTORY = [row("user", "second question"), row("assistant", "Reply in the second chat")];

// A real 1x1 PNG.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

async function openChat(browser, baseUrl, sessions) {
  const view = await openPage(browser, baseUrl, makeFixtures({ sessions, poolEntries: POOL }), { viewport: { width: 1280, height: 900 } });
  await view.page.route(
    (url) => /\/sessions\/[^/]+\/history$/.test(url.pathname),
    (route) => {
      const id = new URL(route.request().url()).pathname.split("/").at(-2);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pageOf(id === SECOND.session_id ? SECOND_HISTORY : FIRST_HISTORY)),
      });
    },
  );
  await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  return view;
}

const messageBox = (page) => page.getByRole("textbox", { name: "Message input" });
const thumbs = (page) => page.locator(".aui-composer-attachments img[alt='Attachment preview']");
const notice = (page) => page.locator(".aui-composer-orphan-notice");
const sidebarRow = (page, name) => page.getByRole("button", { name: new RegExp(`^${name}`) }).first();
const drafts = (page) =>
  page.evaluate(() =>
    Object.fromEntries(
      Object.entries(localStorage).filter(([key]) => key.includes("composer-draft")),
    ),
  );
const tokenCount = async (page) => ((await messageBox(page).inputValue()).match(/\[image\d+\]/g) ?? []).length;

async function attachImage(page) {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment" }).click();
  await (await chooser).setFiles({ name: "pasted.png", mimeType: "image/png", buffer: PNG });
  await thumbs(page).first().waitFor({ timeout: 10_000 });
}

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

    // 1. A full page reload keeps the image, the typed text, and shows no notice.
    const reload = await openChat(browser, baseUrl, [FIRST]);
    try {
      const { page } = reload;
      await page.getByText("Reply in the first chat").waitFor({ timeout: 10_000 });
      await attachImage(page);
      await messageBox(page).click();
      await page.keyboard.press("ControlOrMeta+End");
      await page.keyboard.type("what is this");
      assert.equal(await tokenCount(page), 1, "one image should show one token before the reload");

      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByText("Reply in the first chat").waitFor({ timeout: 10_000 });
      await thumbs(page).first().waitFor({ timeout: 10_000 });
      assert.equal(await thumbs(page).count(), 1, "the image did not come back after the reload");
      assert.equal(
        await tokenCount(page),
        1,
        `the reload left a wrong number of [imageN] tokens: ${JSON.stringify(await messageBox(page).inputValue())}`,
      );
      assert.match(await messageBox(page).inputValue(), /what is this/, "the typed text was lost on reload");
      assert.equal(await notice(page).count(), 0, "a 'paste it again' notice showed although the image was kept");
      assert.deepEqual(reload.pageErrors, []);
    } finally {
      await reload.context.close();
    }

    // 2. Leaving for another chat and coming back keeps the image in the first chat only.
    const two = await openChat(browser, baseUrl, [FIRST, SECOND]);
    try {
      const { page } = two;
      await sidebarRow(page, "Local Coder").click();
      await page.getByText("Reply in the first chat").waitFor({ timeout: 10_000 });
      await attachImage(page);

      await sidebarRow(page, "Local Reviewer").click();
      await page.getByText("Reply in the second chat").waitFor({ timeout: 10_000 });
      assert.equal(await thumbs(page).count(), 0, "the image followed the person into another chat");
      assert.equal(await tokenCount(page), 0, "an [imageN] token followed the person into another chat");

      await sidebarRow(page, "Local Coder").click();
      await page.getByText("Reply in the first chat").waitFor({ timeout: 10_000 });
      await thumbs(page).first().waitFor({ timeout: 10_000 });
      assert.equal(await thumbs(page).count(), 1, "the first chat lost its image while another chat was open");
      assert.equal(
        await tokenCount(page),
        1,
        `coming back left a wrong number of [imageN] tokens: ${JSON.stringify(await messageBox(page).inputValue())} drafts=${JSON.stringify(await drafts(page))} thumbs=${await thumbs(page).count()}`,
      );
      assert.equal(await notice(page).count(), 0, "a notice showed although the image was kept");
      assert.deepEqual(two.pageErrors, []);
    } finally {
      await two.context.close();
    }
    console.log("composer image restore browser acceptance: ok");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
