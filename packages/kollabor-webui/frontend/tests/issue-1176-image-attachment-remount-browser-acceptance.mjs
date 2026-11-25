import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION_ID = "issue-1176-image-session";
const SESSION = {
  session_id: SESSION_ID,
  name: "Issue 1176 image session",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/learn-demo",
  external: false,
};
// Inline placement survives draft restore but is not a standalone restore token.
const DRAFT = "Describe this picture [image1]";
const DRAFT_KEY = "kollab:webui:composer-draft:v1:local-coder:issue-1176-image-session";
const MEDIA = { ref: "issue-1176-image", mime: "image/png" };
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const screenshotDir = process.env.ISSUE_1176_SCREENSHOT_DIR
  ?? path.join(os.homedir(), "backups/claude-work/lanes-1451/artifacts/issue-1176-screenshots");

function sentMessage(body) {
  assert.equal(typeof body.text, "string", "input transport must carry the composer text");
  assert.ok(Array.isArray(body.attachments), "input transport must carry attachment metadata");
  return body;
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  let shell;
  let rejectTransport;
  let resolveTransport;
  const transportRequest = new Promise((resolve, reject) => {
    resolveTransport = resolve;
    rejectTransport = reject;
  });

  try {
    const executablePath = process.env.CHROME_PATH
      ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    browser = await chromium.launch({
      headless: true,
      ...(executablePath ? { executablePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    const fixtureBase = makeFixtures({ sessions: [SESSION] });
    const fixtureRespond = fixtureBase.respond.bind(fixtureBase);
    const fixtures = {
      ...fixtureBase,
      respond: async (route) => {
        const request = route.request();
        const endpoint = new URL(request.url()).pathname.replace(/^\/api(?=\/)/, "");
        if (request.method() === "GET" && endpoint === "/media") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ session_id: SESSION_ID, media: [MEDIA] }),
          });
        }
        if (request.method() === "GET" && endpoint === `/media/${MEDIA.ref}`) {
          return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
        }
        return fixtureRespond(route);
      },
    };
    shell = await openPage(browser, baseUrl, fixtures, { viewport: { width: 1280, height: 900 } });
    await shell.page.route(
      (url) => url.pathname.endsWith(`/${SESSION_ID}/inputs`),
      async (route) => {
        try {
          const body = sentMessage(route.request().postDataJSON());
          resolveTransport(body);
          await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify({ id: body.id, state: "queued" }),
          });
        } catch (error) {
          rejectTransport(error);
          await route.abort();
        }
      },
    );

    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${SESSION.name}"]`).click();
    const composer = shell.page.getByRole("textbox", { name: "Message input" });
    await composer.waitFor({ state: "visible" });
    await attachImage(shell.page, "first.png");
    await composer.fill("Describe this picture");
    await shell.page.waitForFunction(
      (expected) => Object.values(localStorage).some((value) => value.includes(expected)),
      "Describe this picture",
    );
    await shell.page.evaluate(
      ({ key, value }) => localStorage.setItem(key, value),
      { key: DRAFT_KEY, value: DRAFT },
    );

    await shell.page.reload({ waitUntil: "domcontentloaded" });
    const sessionRow = shell.page.locator(`[data-sidebar="sidebar"] button[title*="${SESSION.name}"]`);
    await sessionRow.waitFor({ state: "visible" });
    if (await sessionRow.getAttribute("data-active") !== "true") await sessionRow.click();
    await composer.waitFor({ state: "visible" });
    await shell.page.waitForFunction(
      () => (document.querySelector(".aui-composer-input")?.value.length ?? 0) > 0,
      undefined,
      { timeout: 5_000 },
    );
    await shell.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await shell.page.waitForFunction(
      () => document.querySelectorAll('.aui-composer-attachments [aria-label^="Image attachment"]').length === 1,
    );

    const restoredText = await composer.inputValue();
    const failures = [];
    const restoredTokenCount = (restoredText.match(/\[image1\]/g) ?? []).length;
    if (restoredTokenCount !== 1) {
      failures.push(`restored composer had ${restoredTokenCount} [image1] tokens: ${JSON.stringify(restoredText)}`);
    }
    if (restoredText !== "[image1]\nDescribe this picture ") {
      failures.push(`restored composer text changed unexpectedly: ${JSON.stringify(restoredText)}`);
    }

    const restoredAttachment = shell.page.locator('.aui-composer-attachments [aria-label^="Image attachment"]');
    await restoredAttachment.waitFor({ state: "visible" });
    await mkdir(screenshotDir, { recursive: true });
    await shell.page.screenshot({ path: path.join(screenshotDir, "issue-1176-image-message.png"), animations: "disabled" });
    for (const width of [390, 820]) {
      await shell.page.setViewportSize({ width, height: 900 });
      await restoredAttachment.waitFor({ state: "visible" });
      const horizontalOverflow = await shell.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (horizontalOverflow) failures.push(`chat has horizontal overflow at ${width}px`);
      await shell.page.screenshot({ path: path.join(screenshotDir, `issue-1176-image-message-${width}.png`), animations: "disabled" });
    }
    await composer.press("Enter");
    const sent = await Promise.race([
      transportRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("input transport was not called")), 15_000)),
    ]);
    const text = sent.text;
    const tokenCount = (text.match(/\[image1\]/g) ?? []).length;
    if (tokenCount !== 1) failures.push(`sent text contained ${tokenCount} [image1] tokens: ${JSON.stringify(text)}`);
    const attachmentCount = sent.attachments.length;
    if (attachmentCount !== 1) failures.push(`send carried ${attachmentCount} image attachments`);
    if (sent.attachments[0]?.content_type !== "image/png" || !sent.attachments[0]?.data_url?.startsWith("data:image/png;base64,")) {
      failures.push("the restored image was not sent as a PNG data URL");
    }
    if (!/^web-[0-9a-f]{24}$/.test(sent.id)) failures.push(`input id was not stable web id: ${sent.id}`);

    if (shell.pageErrors.length) failures.push(`browser page errors: ${JSON.stringify(shell.pageErrors)}`);
    if (shell.externalRequests.length) failures.push(`unexpected external requests: ${JSON.stringify(shell.externalRequests)}`);
    if (failures.length) throw new Error(failures.join("\n"));
    console.log("PASS: one restored image token and one image attachment are sent under a stable input id.");
    console.log("Screenshots: issue-1176-image-message.png, issue-1176-image-message-820.png, issue-1176-image-message-390.png");
  } finally {
    await shell?.context.close();
    await browser?.close();
    await server.close();
  }
}

async function attachImage(page, name) {
  const attachmentCount = await page.locator('.aui-composer-attachments [aria-label^="Image attachment"]').count();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name, mimeType: "image/png", buffer: PNG });
  await page.waitForFunction(
    (expected) => document.querySelectorAll('.aui-composer-attachments [aria-label^="Image attachment"]').length === expected,
    attachmentCount + 1,
  );
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
