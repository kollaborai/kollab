// Shows the API error while the deleted chat remains selected after a 500.
// Run: node tests/issue-1384-delete-error-browser-acceptance.mjs <output-folder>
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const session = {
  session_id: "issue-1384-delete-error",
  name: "Open chat",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/delete-error",
  external: false,
};
const agent = {
  name: "local-coder",
  identity: "local-coder",
  roster: "test",
  display_name: "Local Coder",
  avatar: { kind: "gem" },
  color: [36, 117, 190],
  agent: "coder",
  agent_type: "coder",
  profile: "default",
  available: true,
  active: false,
  pool: true,
};

async function main() {
  const outputDir = process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-1384-delete-error");
  fs.mkdirSync(outputDir, { recursive: true });
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: process.platform === "linux" ? ["--no-sandbox"] : [] });
    const fixtures = makeFixtures({ sessions: [session], poolEntries: [agent] });
    const view = await openPage(browser, baseUrl, fixtures, { viewport: { width: 1280, height: 900 } });
    try {
      await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
      await view.page.getByRole("button", { name: /^Local Coder/ }).first().click();
      await view.page.getByRole("textbox", { name: "Message input" }).waitFor();
      const row = view.page.locator("[data-sidebar-agent-row]").filter({ hasText: "Local Coder" }).first();
      await row.hover();
      await view.page.getByRole("button", { name: "Actions for Local Coder" }).click();
      await view.page.getByRole("menuitem", { name: "Delete Session" }).click();
      await view.page.getByRole("heading", { name: "Delete This Session?" }).waitFor();
      await view.page.route(
        (url) => url.pathname.endsWith(`/sessions/${session.session_id}`),
        (route) => route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Stored delete failure" }),
        }),
      );
      const deleting = view.page.waitForResponse((response) =>
        response.request().method() === "DELETE"
        && new URL(response.url()).pathname.endsWith(`/sessions/${session.session_id}`),
      );
      await view.page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
      assert.equal((await deleting).status(), 500);
      const alert = view.page.getByRole("alert");
      await alert.waitFor({ state: "visible" });
      assert.match(await alert.textContent(), /Stored delete failure/);
      await view.page.getByRole("textbox", { name: "Message input" }).waitFor({ state: "visible" });
      const screenshot = path.join(outputDir, "staged-delete-error.png");
      await view.page.screenshot({ path: screenshot, fullPage: true });
      assert.deepEqual(view.pageErrors, []);
      console.log(`delete error browser acceptance: ok (${screenshot})`);
    } finally {
      await view.context.close();
    }
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
