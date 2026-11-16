import assert from "node:assert/strict";
import fs from "node:fs";
import { chromium } from "playwright-core";

import { makeFixtures, openPage, openVite, until } from "./learn-browser-acceptance.mjs";

// #1088 browser acceptance: sent images render as thumbnails in the chat, open
// a full-size viewer, and the Gallery lists sent images and spilled tool
// outputs. Screenshots are opt-in: set GALLERY_SHOT_DIR to a directory to
// capture chat/gallery evidence shots there.

const SHOTS = process.env.GALLERY_SHOT_DIR;
const SESSION = "learn-regression-session";

// 1x1 PNG and JPEG look-a-like: enough for <img> to render and decode().
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const PNG_BLUE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

const MEDIA = [
  { ref: "20261114-210000-aaaa", mime: "image/png" },
  { ref: "20261114-210100-bbbb", mime: "image/png" },
];
const HISTORY = [
  { role: "user", content: "first [image1]", media: [MEDIA[0]] },
  { role: "assistant", content: "Got the first." },
  { role: "user", content: "second [image1]", media: [MEDIA[1]] },
  { role: "assistant", content: "Got the second." },
  // The live daemon-mirror shape (koordinator's live gate, #1124): the text
  // carries no token and the picture is a bytes-less image part. The engine
  // binds media refs onto such rows by part count; the thread must render
  // the thumbnail from the converted [image1] placeholder text.
  {
    role: "user",
    content: [
      { type: "text", text: "one more" },
      { type: "image", image: null },
    ],
    media: [MEDIA[0]],
  },
  { role: "assistant", content: "Got that too." },
];
const ARTIFACT = {
  ref: "shell-exec-call1-aaaa.output",
  name: "shell-exec-call1-aaaa",
  bytes: 18,
  modified: "2026-11-14T21:00:00",
};

function wrapWithMedia(fixtures) {
  const original = fixtures.respond.bind(fixtures);
  async function respond(route) {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
    const method = request.method();
    if (method === "GET" && endpoint === "/media") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION, media: MEDIA }),
      });
    }
    const mediaMatch = /^\/media\/([^/]+)$/.exec(endpoint);
    if (method === "GET" && mediaMatch) {
      if (MEDIA.some((entry) => entry.ref === mediaMatch[1])) {
        return route.fulfill({
          status: 200,
          contentType: "image/png",
          body: mediaMatch[1].endsWith("aaaa") ? PNG_BYTES : PNG_BLUE,
        });
      }
      return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    }
    if (method === "GET" && endpoint === `/sessions/${SESSION}/artifacts`) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION, artifacts: [ARTIFACT] }),
      });
    }
    if (method === "GET" && endpoint === `/sessions/${SESSION}/artifacts/${ARTIFACT.ref}`) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: SESSION,
          ref: ARTIFACT.ref,
          truncated: false,
          content: "line one\nline two",
        }),
      });
    }
    return original(route);
  }
  return { ...fixtures, respond };
}

async function launchAcceptanceBrowser() {
  const executablePath = process.env.CHROME_PATH;
  return chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
}

// until() from the learn harness treats a truthy check as done; counts come
// back as Promises, which are always truthy, so it never retries. This
// variant awaits the check and polls until it is non-zero.
async function untilCount(check, message, timeoutMs = 20_000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function waitForThumbnails(page) {
  return untilCount(
    () => page.getByRole("button", { name: "Open image" }).count(),
    "sent-image thumbnails never rendered in the chat",
  );
}

async function assertNoRawTokens(page) {
  const chat = await page.locator("main").innerText();
  assert.ok(!chat.includes("[image1]"), "the chat still shows the raw [image1] token");
  assert.ok(!chat.includes("[image2]"), "the chat still shows the raw [image2] token");
}

async function openSessionPage(browser, baseUrl, options = {}, width = 0) {
  const fixtures = wrapWithMedia(
    makeFixtures({ history: HISTORY, ...(options.fixtures ?? {}) }),
  );
  const shell = await openPage(browser, baseUrl, fixtures, options.openPage ?? {});
  await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  // Narrow screens collapse the sidebar; the session flow needs it open.
  const toggle = shell.page.getByRole("button", { name: "Toggle Sidebar", exact: true });
  if (await toggle.isVisible().catch(() => false)) await toggle.click();
  await shell.page.getByRole("button", { name: "Learn", exact: true }).waitFor({ state: "visible" });
  await shell.page.getByText(/Learn Test Hub/).waitFor({ state: "visible" });
  await shell.page.getByTestId("new-session-options").click();
  await shell.page.getByRole("button", { name: "Start Session", exact: true }).last().click();
  // Narrow screens show the chat without the Session Controls pane; the chat
  // itself (its history thumbnails) is the readiness signal there.
  const chatReady = shell.page.getByRole("button", { name: "Open image" }).first();
  if (width >= 1024) {
    const sessionControls = shell.page.getByRole("complementary", { name: "Session Controls", exact: true });
    await sessionControls.getByText("Learn regression session", { exact: true }).waitFor({
      state: "visible",
      timeout: 60_000,
    });
  }
  await chatReady.waitFor({ state: "visible", timeout: 60_000 });
  return { ...shell, fixtures };
}

async function verifyViewer(shell) {
  const { page } = shell;
  const thumbs = page.getByRole("button", { name: "Open image" });
  // Two token thumbs from the first row plus the mirror-shaped row's part
  // thumbnail (#1124 live gate).
  assert.equal(await waitForThumbnails(page), 3, "expected three sent-image thumbnails");
  await assertNoRawTokens(page);

  await thumbs.first().click();
  const viewer = page.getByRole("dialog", { name: "Sent image", exact: true });
  await viewer.waitFor({ state: "visible" });
  const image = viewer.locator("img");
  await image.evaluate((element) => element.decode());
  assert.ok(
    await viewer.getByText("1 / 2", { exact: true }).isVisible(),
    "the viewer should start at the first image",
  );
  await page.keyboard.press("ArrowRight");
  await viewer.getByText("2 / 2", { exact: true }).waitFor({ state: "visible" });
  await page.keyboard.press("ArrowLeft");
  await viewer.getByText("1 / 2", { exact: true }).waitFor({ state: "visible" });
  await page.keyboard.press("Escape");
  await viewer.waitFor({ state: "hidden" });
}

// The view tabs render with a stable testid in both the desktop rail and the
// narrow-screen sheet (the sheet opens from the shell header's Session
// Controls button); picking a tab closes the sheet again.
async function openTab(page, tabId) {
  const tab = page.getByTestId(`${tabId}-tab`);
  if (await tab.isVisible().catch(() => false)) {
    await tab.click();
    return;
  }
  await page.getByRole("button", { name: "Session Controls" }).click();
  await page.getByTestId(`${tabId}-tab`).click();
}

async function verifyGallery(shell, width) {
  const { page } = shell;
  await openTab(page, "gallery");
  const sent = page.getByRole("heading", { name: "Sent images", exact: true });
  await sent.waitFor({ state: "visible" });
  // The closing sheet can leave aria-hidden on the app root for a beat, so
  // count the thumbs by CSS here instead of the role query.
  await untilCount(
    () => page.locator('button[aria-label="Open image"]').count(),
    "gallery grid never rendered",
  );
  const artifactRow = page.getByTestId(`artifact-${ARTIFACT.name}`);
  await artifactRow.waitFor({ state: "visible" });
  await artifactRow.click();
  await page.getByText("line one\nline two", { exact: true }).waitFor({ state: "visible" });
  assert.ok(
    await page.getByRole("button", { name: "Download" }).isVisible(),
    "the artifact download button is missing",
  );
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/gallery-${width}.png` });
  // Click a gallery image: the viewer opens from the gallery too.
  await page.getByRole("button", { name: "Open image" }).first().click();
  const viewer = page.getByRole("dialog", { name: "Sent image", exact: true });
  await viewer.waitFor({ state: "visible" });
  await page.keyboard.press("Escape");
  await viewer.waitFor({ state: "hidden" });
  await openTab(page, "chat");
  await sent.waitFor({ state: "hidden" });
}

async function main() {
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

    for (const [width, height, isMobile] of [
      [390, 844, true],
      [820, 1180, true],
      [1280, 900, false],
    ]) {
      const shell = await openSessionPage(browser, baseUrl, {
        openPage: { viewport: { width, height }, isMobile },
      }, width);
      await verifyViewer(shell);
      if (SHOTS) await shell.page.screenshot({ path: `${SHOTS}/chat-${width}.png` });
      await verifyGallery(shell, width);
      await shell.context.close();
      console.log(`gallery acceptance ok at ${width}px`);
    }
    console.log(SHOTS ? `#1088 gallery browser acceptance PASS; screenshots in ${SHOTS}` : "#1088 gallery browser acceptance PASS");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
