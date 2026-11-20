import assert from "node:assert/strict";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION_ID = "issue-1176-orphan-session";
const SESSION = {
  session_id: SESSION_ID,
  name: "Issue 1176 orphan session",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/learn-demo",
  external: false,
};
const DRAFT = "Describe this picture [image1]";

function sentText(body) {
  const commandMessage = body.commands?.find((command) => command.type === "add-message")?.message;
  const message = commandMessage ?? body.message ?? body.messages?.at(-1);
  const parts = message?.parts ?? message?.content;
  assert.ok(Array.isArray(parts), "assistant transport must carry user message parts");
  return parts.filter((part) => part.type === "text").map((part) => part.text).join("\n");
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  let shell;
  let resolveTransport;
  const transportRequest = new Promise((resolve) => {
    resolveTransport = resolve;
  });

  try {
    browser = await chromium.launch({
      headless: true,
      ...(process.platform === "darwin"
        ? { executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" }
        : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });
    shell = await openPage(browser, baseUrl, makeFixtures({ sessions: [SESSION] }));
    await shell.page.route(
      (url) => url.pathname.endsWith(`/${SESSION_ID}/assistant`),
      async (route) => {
        resolveTransport(sentText(route.request().postDataJSON()));
        await route.abort();
      },
    );

    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${SESSION.name}"]`).click();
    const composer = shell.page.getByRole("textbox", { name: "Message input" });
    await composer.fill(DRAFT);
    await shell.page.waitForFunction((value) => Object.values(localStorage).includes(value), DRAFT);
    await shell.page.reload({ waitUntil: "domcontentloaded" });
    await composer.waitFor({ state: "visible" });
    await shell.page.waitForFunction(
      () => (document.querySelector(".aui-composer-input")?.value.length ?? 0) > 0,
      undefined,
      { timeout: 5_000 },
    );
    await shell.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

    const failures = [];
    const restored = await composer.inputValue();
    if (restored.includes("[image1]")) failures.push(`restored composer kept orphan token: ${JSON.stringify(restored)}`);
    await composer.press("Enter");
    const outgoing = await Promise.race([
      transportRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("assistant transport was not called")), 15_000)),
    ]);
    if (outgoing.includes("[image1]")) failures.push(`outgoing message kept orphan token: ${JSON.stringify(outgoing)}`);
    if (failures.length) throw new Error(failures.join("\n"));
    console.log("PASS: restored draft without attachments drops its orphan image token before send.");
  } finally {
    await shell?.context.close();
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
