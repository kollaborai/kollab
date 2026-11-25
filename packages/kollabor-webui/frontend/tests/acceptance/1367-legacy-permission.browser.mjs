// The legacy /app.js surface must carry an exact permission owner token.
// Run with:
//   CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node tests/acceptance/1367-legacy-permission.browser.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const legacyApp = path.resolve(
  frontendRoot,
  "../src/kollabor_webui/static/app.js",
);
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
});

try {
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ path: legacyApp });
  await page.evaluate(() => {
    window.permissionRequests = [];
    window.fetch = async (url, options) => {
      window.permissionRequests.push({ url: String(url), body: JSON.parse(options.body) });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };

    const panel = document.createElement("section");
    panel.innerHTML = `
      <span class="session-title"></span>
      <span class="session-status"></span>
      <span class="token-count"></span>
      <button class="close-btn"></button>
      <button class="minimize-btn"></button>
      <button class="popout-btn"></button>
      <div class="terminal-output"></div>
      <textarea class="terminal-input"></textarea>
      <button class="send-btn"></button>
    `;
    document.body.append(panel);

    const api = new EngineAPI("http://engine.test");
    const terminal = new TerminalSession("session-1", panel, api, {});
    terminal.printPermissionRequest({
      tool_id: "reused-tool",
      permission_id: "owner-a",
      tool_name: "Write file",
      risk_level: "medium",
      risk_reason: "test prompt A",
    });
    terminal.printPermissionRequest({
      tool_id: "reused-tool",
      permission_id: "owner-b",
      tool_name: "Write file",
      risk_level: "medium",
      risk_reason: "test prompt B",
    });
    terminal.printPermissionRequest({
      tool_id: "missing-owner",
      tool_name: "Unknown owner",
      risk_level: "high",
      risk_reason: "invalid legacy event",
    });
  });

  const prompts = page.locator(".terminal-output .permission");
  assert.equal(await prompts.count(), 3, "distinct owner tokens should render separately");
  assert.equal(
    await page.locator(".terminal-output .permission button").count(),
    4,
    "a prompt without owner identity must not expose answer buttons",
  );
  assert.match(
    await prompts.nth(2).innerText(),
    /cannot safely answer/i,
    "missing identity should be visible",
  );

  await page.locator('.permission[data-permission-id="owner-b"] .btn-approve').click();
  await page.locator('.permission[data-permission-id="owner-a"] .btn-deny').click();
  const responses = await page.evaluate(() => window.permissionRequests);
  assert.deepEqual(
    responses.map(({ body }) => body),
    [
      { tool_id: "reused-tool", permission_id: "owner-b", decision: "approve", scope: "once" },
      { tool_id: "reused-tool", permission_id: "owner-a", decision: "deny", scope: "once" },
    ],
    "the legacy client must send the clicked prompt's exact owner token",
  );
  assert.deepEqual(pageErrors, [], "legacy permission UI should not throw");
  console.log("PASS: legacy permission responses are bound to exact owner tokens");
} finally {
  await browser.close();
}
