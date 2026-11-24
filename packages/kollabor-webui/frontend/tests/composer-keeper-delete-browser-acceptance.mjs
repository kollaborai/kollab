// Verifies that deleting a session drops its in-memory composer text without
// disturbing another session's draft. The replacement deliberately reuses the
// deleted id so stale in-memory text becomes visible in a scratch browser.
// Run: node tests/composer-keeper-delete-browser-acceptance.mjs <output-folder>
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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

const DELETED = session("composer-keeper-delete", "local-coder", "Deleted session");
const PRESERVED = session("composer-keeper-preserved", "local-reviewer", "Preserved session");
const POOL = [gem("local-coder", "Local Coder"), gem("local-reviewer", "Local Reviewer")];
const messageBox = (page) => page.getByRole("textbox", { name: "Message input" });
const sidebarRow = (page, name) => page.getByRole("button", { name: new RegExp(`^${name}`) }).first();

async function main() {
  const outputDir = process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-981-browser-proof");
  fs.mkdirSync(outputDir, { recursive: true });

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

    let sessions = [DELETED, PRESERVED];
    const fixtures = makeFixtures({ sessions, poolEntries: POOL });
    const view = await openPage(browser, baseUrl, fixtures, { viewport: { width: 1280, height: 900 } });
    try {
      await view.page.route(
        (url) => {
          const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
          return endpoint === "/sessions" || /^\/sessions\/[^/]+$/.test(endpoint);
        },
        async (route) => {
          const request = route.request();
          const url = new URL(request.url());
          const endpoint = url.pathname.replace(/^\/api(?=\/)/, "");
          if (endpoint === "/sessions" && request.method() === "GET") {
            return route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify({ ...fixtures.session(), sessions }),
            });
          }
          if (endpoint === "/sessions" && request.method() === "POST") {
            const body = request.postDataJSON();
            const replacement = {
              ...DELETED,
              name: "Replacement session",
              agent: body.agent ?? "coder",
              identity: DELETED.identity,
              profile: body.profile ?? "default",
              workspace: body.workspace ?? DELETED.workspace,
            };
            sessions = [replacement, ...sessions];
            return route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(replacement),
            });
          }
          if (endpoint === `/sessions/${DELETED.session_id}` && request.method() === "DELETE") {
            sessions = sessions.filter((item) => item.session_id !== DELETED.session_id);
            return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
          }
          return route.fallback();
        },
      );
      await view.page.route(
        (url) => /\/sessions\/[^/]+\/history$/.test(url.pathname),
        (route) => route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            history: [],
            start_index: 0,
            end_index: 0,
            has_more_older: false,
            older_cursor: null,
            has_more_newer: false,
            newer_cursor: null,
            source: "mirror",
            stale: false,
            last_turn_error: null,
          }),
        }),
      );
      await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });

      await sidebarRow(view.page, "Local Coder").click();
      await messageBox(view.page).waitFor();
      await messageBox(view.page).fill("draft from deleted session");

      await sidebarRow(view.page, "Local Reviewer").click();
      await messageBox(view.page).fill("draft from preserved session");

      const deletedRow = view.page.locator("[data-sidebar-agent-row]").filter({ hasText: "Local Coder" }).first();
      await deletedRow.hover();
      await view.page.getByRole("button", { name: "Actions for Local Coder" }).click();
      await view.page.getByRole("menuitem", { name: "Delete Session" }).click();
      await view.page.getByRole("heading", { name: "Delete This Session?" }).waitFor();
      const deleting = view.page.waitForResponse((response) =>
        response.request().method() === "DELETE"
        && new URL(response.url()).pathname.endsWith(`/sessions/${DELETED.session_id}`),
      );
      const refreshing = view.page.waitForResponse((response) =>
        response.request().method() === "GET"
        && new URL(response.url()).pathname.replace(/^\/api(?=\/)/, "") === "/sessions",
      );
      await view.page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
      assert.equal((await deleting).status(), 200);
      await refreshing;
      await deletedRow.waitFor({ state: "detached" });
      assert.equal(await messageBox(view.page).inputValue(), "draft from preserved session");

      // This proof targets the in-memory keeper. Remove the separate durable
      // draft for the deleted conversation so it cannot mask the keeper's state.
      const draftKey = await view.page.evaluate(async ({ identity, sessionId }) => {
        const { composerDraftStorageKey } = await import("/src/composer-draft.ts");
        return composerDraftStorageKey(identity, sessionId);
      }, { identity: DELETED.identity, sessionId: DELETED.session_id });
      assert.ok(draftKey);
      await view.page.evaluate((key) => localStorage.removeItem(key), draftKey);
      assert.equal(await view.page.evaluate((key) => localStorage.getItem(key), draftKey), null);

      await view.page.getByRole("button", { name: "New Session" }).click();
      await view.page.getByText("Replacement session").waitFor();
      assert.equal(
        await messageBox(view.page).inputValue(),
        "",
        "a deleted session's text returned when its id was reused",
      );

      const screenshot = path.join(outputDir, "composer-keeper-deleted-session.png");
      await view.page.screenshot({ path: screenshot, fullPage: true });
      assert.deepEqual(view.pageErrors, []);
      console.log(`composer keeper delete browser acceptance: ok (${screenshot})`);
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
