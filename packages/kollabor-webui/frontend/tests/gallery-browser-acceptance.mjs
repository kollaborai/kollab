import assert from "node:assert/strict";
import fs from "node:fs";
import { chromium } from "playwright-core";

import { makeFixtures, openPage, openVite, until } from "./learn-browser-acceptance.mjs";

// #1088 browser acceptance: sent images render as thumbnails in the chat, open
// a full-size viewer, and the Gallery lists sent images and spilled tool
// outputs. Screenshots are opt-in: set GALLERY_SHOT_DIR to a directory to
// capture chat/gallery evidence shots there.

const SHOTS = process.argv[2] || process.env.GALLERY_SHOT_DIR;
const SESSION = "learn-regression-session";

// Distinct, labeled images keep the selected attachment visible in screenshots.
const IMAGE_A_BYTES = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#8b1e1e"/><text x="320" y="220" fill="white" font-size="72" text-anchor="middle">Image A</text></svg>',
);
const IMAGE_B_BYTES = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#1848c8"/><text x="320" y="220" fill="white" font-size="72" text-anchor="middle">Image B</text></svg>',
);

const MEDIA = [
  { ref: "20261114-210000-aaaa", mime: "image/svg+xml" },
  { ref: "20261114-210100-bbbb", mime: "image/svg+xml" },
];
const HISTORY = [
  { role: "user", content: "first [image1]", media: [MEDIA[0]] },
  { role: "assistant", content: "Got the first." },
  { role: "user", content: "second [image1]", media: [MEDIA[1]] },
  { role: "assistant", content: "Got the second." },
  {
    role: "user",
    content: "failed [image1], saved [image2]",
    media: [
      { ref: "", mime: "", failed: true },
      MEDIA[1],
    ],
  },
  { role: "assistant", content: "The saved image stays in slot two." },
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
// The session list has A when it is loaded. Later history in this same
// session contains B, but the list response is still A: this is the stale
// cache state that used to make B's thumbnail open A.
const STALE_HISTORY = [
  { role: "user", content: "first [image1]", media: [MEDIA[0]] },
  { role: "assistant", content: "Got the first." },
  { role: "user", content: "later [image1]", media: [MEDIA[1]] },
  { role: "user", content: "failed [image1]", media: [{ ref: "", mime: "image/png", failed: true }] },
  { role: "user", content: "missing [image1]" },
];
const ARTIFACT = {
  ref: "shell-exec-call1-aaaa.output",
  name: "shell-exec-call1-aaaa",
  bytes: 18,
  modified: "2026-11-14T21:00:00",
};

function wrapWithMedia(fixtures, { listedMedia = MEDIA, listFailure = false } = {}) {
  const original = fixtures.respond.bind(fixtures);
  const mediaListReads = [];
  async function respond(route) {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
    const method = request.method();
    if (method === "GET" && endpoint === "/media") {
      mediaListReads.push(url.searchParams.get("session_id"));
      if (listFailure) {
        return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION, media: listedMedia }),
      });
    }
    const mediaMatch = /^\/media\/([^/]+)$/.exec(endpoint);
    if (method === "GET" && mediaMatch) {
      if (MEDIA.some((entry) => entry.ref === mediaMatch[1])) {
        return route.fulfill({
          status: 200,
          contentType: "image/svg+xml",
          body: mediaMatch[1].endsWith("aaaa") ? IMAGE_A_BYTES : IMAGE_B_BYTES,
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
  return { ...fixtures, mediaListReads, respond };
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

async function assertMixedFailureRendering(page) {
  const chat = await page.locator("main").innerText();
  assert.ok(
    chat.includes("failed [image1], saved "),
    "the failed image token should stay literal beside the surviving thumbnail",
  );
  assert.equal((chat.match(/\[image1\]/g) ?? []).length, 1);
  assert.ok(!chat.includes("[image2]"), "the stored [image2] token should become a thumbnail");
}

async function openSessionPage(browser, baseUrl, options = {}, width = 0) {
  const fixtures = wrapWithMedia(
    makeFixtures({ history: options.history ?? HISTORY, ...(options.fixtures ?? {}) }),
    { listedMedia: options.listedMedia ?? MEDIA, listFailure: options.listFailure ?? false },
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
  // Two first-slot token thumbs, the surviving mixed-failure slot, and the
  // mirror-shaped row's part thumbnail (#1124 live gate, #1148 slot parity).
  assert.equal(await waitForThumbnails(page), 4, "expected four sent-image thumbnails");
  await assertMixedFailureRendering(page);

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

async function verifyStaleSessionViewer(shell, width) {
  const { page } = shell;
  const thumbs = page.getByRole("button", { name: "Open image" });
  assert.equal(await waitForThumbnails(page), 2, "expected both same-session history thumbnails");
  assert.equal(await page.getByText("[image1]", { exact: true }).count(), 2, "missing and failed slots should stay inert text");
  assert.deepEqual(shell.fixtures.mediaListReads, [SESSION], "the viewer should have one loaded session list");

  const imageBThumb = thumbs.nth(1).locator("img");
  await imageBThumb.evaluate((element) => element.decode());
  const imageBSrc = await imageBThumb.getAttribute("src");
  assert.ok(imageBSrc, "image B should be visibly loaded in its thumbnail");

  await thumbs.nth(1).click();
  const viewer = page.getByRole("dialog", { name: "Sent image", exact: true });
  await viewer.waitFor({ state: "visible" });
  const displayed = viewer.locator("img");
  await displayed.evaluate((element) => element.decode());
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/stale-viewer-${width}.png` });

  assert.equal(
    await displayed.getAttribute("src"),
    imageBSrc,
    "clicking image B must show B even when the loaded session list only contains image A",
  );
  assert.deepEqual(shell.fixtures.mediaListReads, [SESSION], "opening B must not remount or refresh the session list");
  assert.ok(await viewer.getByText("2 / 2", { exact: true }).isVisible(), "B should be appended after cached A");
  await page.keyboard.press("ArrowLeft");
  await viewer.getByText("1 / 2", { exact: true }).waitFor({ state: "visible" });
  assert.equal(
    await viewer.locator("img").getAttribute("src"),
    await thumbs.nth(0).locator("img").getAttribute("src"),
  );
  await page.keyboard.press("ArrowRight");
  await viewer.getByText("2 / 2", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await viewer.locator("img").getAttribute("src"), imageBSrc);
}

async function verifyFailedSessionMediaList(shell) {
  const { page } = shell;
  const thumbs = page.getByRole("button", { name: "Open image" });
  assert.equal(await waitForThumbnails(page), 2, "message-local thumbnails should render when the list request fails");
  assert.deepEqual(shell.fixtures.mediaListReads, [SESSION], "the failed session list should be requested once");

  const imageBThumb = thumbs.nth(1).locator("img");
  await imageBThumb.evaluate((element) => element.decode());
  const imageBSrc = await imageBThumb.getAttribute("src");
  await thumbs.nth(1).click();
  const viewer = page.getByRole("dialog", { name: "Sent image", exact: true });
  await viewer.waitFor({ state: "visible" });
  const displayed = viewer.locator("img");
  await displayed.evaluate((element) => element.decode());
  assert.equal(await displayed.getAttribute("src"), imageBSrc, "a failed refresh must fall back to the clicked row's image");
  assert.ok(await viewer.getByText("1 / 1", { exact: true }).isVisible(), "failed refresh should not add unrelated images");
}

async function verifySessionSwitch(browser, baseUrl) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  try {
    await page.goto(`${baseUrl}/tests/acceptance/media-session-switch.html`, { waitUntil: "domcontentloaded" });
    const media = page.getByTestId("session-media");
    await page.getByText("session-a-image", { exact: true }).waitFor({ state: "visible" });

    await page.getByRole("button", { name: "Switch session", exact: true }).click();
    await page.getByText("loading", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await media.getAttribute("data-leaked"), "no", "session A media must not remain visible while session B loads");
    assert.equal(await page.getByRole("button", { name: "Open image" }).count(), 0);

    await page.getByRole("button", { name: "Load session B media", exact: true }).click();
    await page.getByText("session-b-image", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await media.getAttribute("data-leaked"), "no");
    const thumb = page.getByRole("button", { name: "Open image", exact: true });
    await thumb.locator("img").evaluate((element) => element.decode());
    const expectedSrc = await thumb.locator("img").getAttribute("src");
    await thumb.click();
    const viewer = page.getByRole("dialog", { name: "Sent image", exact: true });
    await viewer.waitFor({ state: "visible" });
    await viewer.locator("img").evaluate((element) => element.decode());
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/session-switch.png` });
    assert.equal(await viewer.locator("img").getAttribute("src"), expectedSrc, "session B must open its own image");
    assert.ok(await viewer.getByText("1 / 1", { exact: true }).isVisible());
  } finally {
    await page.close();
  }
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
  const failures = [];
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

      const staleShell = await openSessionPage(browser, baseUrl, {
        history: STALE_HISTORY,
        listedMedia: [MEDIA[0]],
        openPage: { viewport: { width, height }, isMobile },
      }, width);
      try {
        await verifyStaleSessionViewer(staleShell, width);
      } catch (error) {
        failures.push(error);
      } finally {
        await staleShell.context.close();
      }

      if (width === 1280) {
        const failedListShell = await openSessionPage(browser, baseUrl, {
          history: STALE_HISTORY,
          listFailure: true,
          openPage: { viewport: { width, height }, isMobile },
        }, width);
        try {
          await verifyFailedSessionMediaList(failedListShell);
        } catch (error) {
          failures.push(error);
        } finally {
          await failedListShell.context.close();
        }
      }
      console.log(`gallery acceptance ok at ${width}px`);
    }
    try {
      await verifySessionSwitch(browser, baseUrl);
    } catch (error) {
      failures.push(error);
    }
    if (failures.length) throw new AggregateError(failures, "media viewer browser acceptance failed");
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
