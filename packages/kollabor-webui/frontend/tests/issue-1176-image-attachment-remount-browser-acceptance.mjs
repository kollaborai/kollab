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
const MEDIA = { ref: "issue-1176-image", mime: "image/png" };
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const screenshotDir = process.env.ISSUE_1176_SCREENSHOT_DIR
  ?? path.join(os.tmpdir(), "kollab-issue-1176-screenshots");

function frame(value) {
  return `data: ${JSON.stringify(value)}\n\n`;
}

function sentMessage(body) {
  const commandMessage = body.commands?.find((command) => command.type === "add-message")?.message;
  const message = commandMessage ?? body.message ?? body.messages?.at(-1);
  const parts = message?.parts ?? message?.content;
  assert.ok(Array.isArray(parts), "assistant transport must carry user message parts");
  return { parts };
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
      (url) => url.pathname.endsWith(`/${SESSION_ID}/assistant`),
      async (route) => {
        try {
          const body = route.request().postDataJSON();
          const { parts } = sentMessage(body);
          resolveTransport({ parts });
          const text = parts.filter((part) => part.type === "text").map((part) => part.text).join("\n");
          const userMessage = {
            id: "issue-1176-user",
            role: "user",
            content: [{ type: "text", text }],
            metadata: { custom: { media: [MEDIA] } },
          };
          const response = [
            frame({ type: "part-start", part: { type: "text" } }),
            frame({
              type: "update-state",
              operations: [
                { type: "set", path: ["messages"], value: [userMessage] },
                { type: "set", path: ["synced"], value: null },
                { type: "set", path: ["usage"], value: { inputTokens: 1, outputTokens: 0, toolCalls: 0, stopReason: "stop" } },
              ],
            }),
            frame({ type: "part-finish", path: [0] }),
            frame({ type: "message-finish", finishReason: "stop" }),
            "data: [DONE]\n\n",
          ].join("");
          await route.fulfill({ status: 200, contentType: "text/event-stream", body: response });
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
    await composer.fill(DRAFT);
    await shell.page.waitForFunction((expected) => Object.values(localStorage).includes(expected), DRAFT);

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

    await composer.press("Enter");

    const sent = await Promise.race([
      transportRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("assistant transport was not called")), 15_000)),
    ]);
    const text = sent.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n");
    const tokenCount = (text.match(/\[image1\]/g) ?? []).length;
    if (tokenCount !== 1) failures.push(`sent text contained ${tokenCount} [image1] tokens: ${JSON.stringify(text)}`);
    const imageCount = sent.parts.filter((part) => part.type === "image").length;
    if (imageCount !== 1) failures.push(`send carried ${imageCount} image parts`);

    const thumbnail = shell.page.getByRole("button", { name: "Open image" });
    const thumbnailVisible = await thumbnail.first().waitFor({ state: "visible", timeout: 5_000 })
      .then(() => true, () => false);
    if (!thumbnailVisible) {
      failures.push("sent user message did not show an image thumbnail");
    } else {
      await thumbnail.first().locator("img").evaluate((image) => image.decode());
    }
    const messageCount = await shell.page.locator('[data-role="user"] .aui-user-message-content').count();
    if (messageCount === 0) {
      failures.push("sent user message bubble did not render");
    } else {
      const visibleMessage = await shell.page.locator('[data-role="user"] .aui-user-message-content').innerText();
      if (visibleMessage.includes("[image1]")) failures.push("sent bubble showed the raw image token");
    }

    await mkdir(screenshotDir, { recursive: true });
    await shell.page.screenshot({ path: path.join(screenshotDir, "issue-1176-image-message.png"), animations: "disabled" });
    for (const width of [390, 820]) {
      await shell.page.setViewportSize({ width, height: 900 });
      if (thumbnailVisible && !(await thumbnail.first().isVisible())) {
        failures.push(`sent image thumbnail was not visible at ${width}px`);
      }
      const horizontalOverflow = await shell.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (horizontalOverflow) failures.push(`chat has horizontal overflow at ${width}px`);
      await shell.page.screenshot({ path: path.join(screenshotDir, `issue-1176-image-message-${width}.png`), animations: "disabled" });
    }

    if (shell.pageErrors.length) failures.push(`browser page errors: ${JSON.stringify(shell.pageErrors)}`);
    if (shell.externalRequests.length) failures.push(`unexpected external requests: ${JSON.stringify(shell.externalRequests)}`);
    if (failures.length) throw new Error(failures.join("\n"));
    console.log("PASS: one restored image token and image part sent; thumbnail visible at 1280px, 820px, and 390px.");
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
