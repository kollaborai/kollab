import assert from "node:assert/strict";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION_ID = "issue-1176-keystroke-session";
const SESSION = {
  session_id: SESSION_ID,
  name: "Issue 1176 keystroke session",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/learn-demo",
  external: false,
};
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const settle = (page) =>
  page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function openComposer(browser, baseUrl) {
  const shell = await openPage(browser, baseUrl, makeFixtures({ sessions: [SESSION] }), {
    viewport: { width: 1280, height: 900 },
  });
  await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${SESSION.name}"]`).click();
  const composer = shell.page.getByRole("textbox", { name: "Message input" });
  await composer.waitFor({ state: "visible" });
  return { shell, composer };
}

async function attachImage(page) {
  const before = await page.locator('.aui-composer-attachments [aria-label^="Image attachment"]').count();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({ name: "first.png", mimeType: "image/png", buffer: PNG });
  await page.waitForFunction(
    (expected) => document.querySelectorAll('.aui-composer-attachments [aria-label^="Image attachment"]').length === expected,
    before + 1,
  );
  await settle(page);
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      ...(process.platform === "darwin"
        ? { executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" }
        : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    const withImage = await openComposer(browser, baseUrl);
    try {
      await attachImage(withImage.shell.page);
      await withImage.composer.press("Shift+Enter");
      await settle(withImage.shell.page);
      assert.equal(
        await withImage.composer.inputValue(),
        "[image1]\n",
        "Shift+Enter after the managed image token must preserve the typed newline",
      );
    } finally {
      await withImage.shell.context.close();
    }

    const withoutImage = await openComposer(browser, baseUrl);
    try {
      await withoutImage.composer.pressSequentially("see [image1]");
      await settle(withoutImage.shell.page);
      assert.equal(
        await withoutImage.composer.inputValue(),
        "see [image1]",
        "a token typed without an attachment must remain user text",
      );
    } finally {
      await withoutImage.shell.context.close();
    }

    console.log("PASS: composer keystrokes preserve newlines and typed image-token text.");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
