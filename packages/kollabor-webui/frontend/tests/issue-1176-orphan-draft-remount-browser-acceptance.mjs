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
const DRAFT_KEY = "kollab:webui:composer-draft:v1:local-coder:issue-1176-orphan-session";

function sentText(body) {
  assert.equal(typeof body.text, "string", "input transport must carry the composer text");
  return body.text;
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
    const executablePath = process.env.CHROME_PATH
      ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    browser = await chromium.launch({
      headless: true,
      ...(executablePath ? { executablePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });
    shell = await openPage(browser, baseUrl, makeFixtures({ sessions: [SESSION] }));
    await shell.page.route(
      (url) => url.pathname.endsWith(`/${SESSION_ID}/inputs`),
      async (route) => {
        const body = route.request().postDataJSON();
        resolveTransport(sentText(body));
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({ id: body.id, state: "queued" }),
        });
      },
    );

    await shell.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await shell.page.locator(`[data-sidebar="sidebar"] button[title*="${SESSION.name}"]`).click();
    const composer = shell.page.getByRole("textbox", { name: "Message input" });
    await composer.waitFor({ state: "visible" });
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

    const failures = [];
    const restored = await composer.inputValue();
    if (restored.includes("[image1]")) failures.push(`restored composer kept orphan token: ${JSON.stringify(restored)}`);
    await composer.press("Enter");
    const outgoing = await Promise.race([
      transportRequest,
      new Promise((_, reject) => setTimeout(() => reject(new Error("input transport was not called")), 15_000)),
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
