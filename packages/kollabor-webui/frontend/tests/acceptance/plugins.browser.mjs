// Browser acceptance for plugin surfaces. All engine responses below are fake;
// this script never starts an engine or touches a real Kollab config/keyring.
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const vite = await createServer({ root, configFile: path.join(root, "vite.config.ts"), logLevel: "error",
  cacheDir: path.join(process.env.KOLLAB_PLUGIN_TEST_DIR || process.env.HOME, "plugin-vite-cache"),
  server: { host: "127.0.0.1", port: 0, hmr: false } });
let browser;
try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || chromium.executablePath() });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let inventoryReads = 0;
  let confirmedInstall = null;
  const commit = "a".repeat(40);
  const filesHash = "b".repeat(64);
  const preview = { job_id: "install-fixture", action: "install", status: "ready", commit, files_sha256: filesHash,
    plugin_id: "fixture-new", manifest: { name: "Fixture New", version: "1.2.3", uses: { tools: ["file_read"], network: ["example.com"] }, services: { worker: {} } },
    changes: { added: ["web/dist/index.js"], modified: [], removed: [], new_grants: ["network:example.com"], new_dependencies: [] } };
  const entry = { id: "fixture", name: "Fixture", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: "web/dist/index.css", hash: "1" }, contributes: {
      menu: [{ id: "board", label: "Fixture Board", open: { page: "board" } }],
      views: [{ id: "fixture-view" }], pages: [{ id: "board" }], modals: [{ id: "details" }],
      slash: ["fixture"], renderers: [{ tag: "fixture_tag" }],
    } };
  const broken = { id: "broken", name: "Broken", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: null, hash: "1" }, contributes: { menu: [{ id: "broken-row", label: "Broken Board", open: { page: "board" } }], pages: [{ id: "board" }] } };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: "/engine" } });
    if (url.pathname === "/engine/plugins") { inventoryReads++; return route.fulfill({ json: { schema: 1, plugins: [entry, broken] } }); }
    if (url.pathname === "/engine/plugins/install/preview" && route.request().method() === "POST") return route.fulfill({ json: { job_id: "install-fixture", status: "staging" } });
    if (url.pathname === "/engine/plugins/jobs/install-fixture" && route.request().method() === "GET") return route.fulfill({ json: preview });
    if (url.pathname === "/engine/plugins/jobs/install-fixture/confirm" && route.request().method() === "POST") {
      confirmedInstall = route.request().postDataJSON();
      return route.fulfill({ json: { ...preview, status: "complete" } });
    }
    if (url.pathname === "/plugins/broken/web/index.js") return route.fulfill({ status: 500, contentType: "text/plain", body: "broken" });
    if (url.pathname === "/plugins/fixture/web/index.css") return route.fulfill({ contentType: "text/css", body: '[data-plugin="fixture"] .scope-probe { color: rgb(12, 34, 56); }' });
    if (url.pathname === "/plugins/fixture/web/index.js") return route.fulfill({ contentType: "text/javascript", body:
      `import React from "react"; import { definePlugin } from "@kollab/plugin";
       export default definePlugin(k => {
         const body = () => React.createElement("p", {className:"scope-probe"}, "Fixture page body");
         const second = () => React.createElement("p", null, "Second tab body");
         const modal = () => React.createElement("p", null, "Fixture modal body");
         k.page({id:"board",title:"Fixture Board",tabs:[{id:"first",label:"First",render:body},{id:"second",label:"Second",render:second}]});
         k.menu("board",{count:{path:"/badge",refreshOn:"changed"}});
         k.command({name:"fixture",description:"Run fixture",run:args=>k.toast("Fixture ran: "+args)});
         k.renderer({tag:"fixture_tag",render:props=>React.createElement("p",{"data-testid":"plugin-tag"},props.tag.content)});
         k.view({id:"fixture-view",label:"Fixture View",needs_folder:true,render:() => React.createElement("p",null,"Fixture view body")});
         k.modal({id:"details",title:"Fixture Details",tabs:[{id:"info",label:"Info",render:modal}]});
         k.notice({text:"Fixture plugin notice",button:{label:"Open board",open:{page:"board"}}});
         k.notice({text:"Fixture modal notice",button:{label:"Open details",open:{modal:"details"}}});
       });` });
    if (url.pathname === "/engine/prompts/libraries") return route.fulfill({ json: { libraries: [], roles: [] } });
    if (url.pathname === "/engine/plugins/events") return route.fulfill({ contentType: "text/event-stream", body: ": open\n\n" });
    if (url.pathname === "/engine/plugins/fixture/api/badge") return route.fulfill({ json: { text: "4", hint: "4 items need attention" } });
    if (url.pathname === "/engine/sessions") return route.fulfill({ json: { sessions: [{ session_id: "fixture-session", name: "Fixture Session", agent: "coder", identity: "local-coder", profile: "default", workspace: "/fixture", external: false }], network: { device: "test", remote: [] }, offline: [], hidden: [] } });
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: { history: [{ role: "assistant", content: "Before <fixture_tag><b>untrusted</b></fixture_tag> after", metadata: {} }], start_index: 0, end_index: 1, has_more_older: false, has_more_newer: false, source: "mirror" } });
    if (url.pathname.startsWith("/api/")) return route.fulfill({ json: { available: false } });
    if (url.pathname.startsWith("/engine/")) return route.fulfill({ json: { sessions: [], profiles: [], agents: [], live: [], bundles: [], variants: {}, roles: [], libraries: [], sections: [], identities: [], teams: [], models: [], commands: [], network: {device:"",remote:[]}, offline: [], hidden: [], order: [] } });
    return route.continue();
  });
  await page.goto(`${origin}/?plugins=off`, { waitUntil: "domcontentloaded" });
  await page.getByText("Plugins are off for this page", { exact: false }).waitFor({ timeout: 15000 });
  assert.equal(inventoryReads, 0, "page-wide opt-out must not fetch plugin inventory");
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  assert.ok(inventoryReads > 0, "host must fetch inventory");
  await page.getByTestId("plugin-tag").waitFor({ timeout: 5000 });
  assert.equal(await page.getByTestId("plugin-tag").locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "renderer has plugin-specific CSS scope");
  assert.equal(await page.getByTestId("plugin-tag").textContent(), "<b>untrusted</b>");
  assert.equal(await page.getByTestId("plugin-tag").locator("b").count(), 0, "tag content stays untrusted text");
  const input = page.getByRole("textbox", { name: "Message input" });
  await input.fill("/fixture test arguments");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page.getByText("Fixture ran: test arguments", { exact: true }).waitFor({ timeout: 5000 });
  assert.equal(await input.inputValue(), "", "plugin command clears the composer");
  const panel = page.getByRole("complementary", { name: "Session Controls" });
  await panel.waitFor({ timeout: 15000 });
  await panel.getByRole("button", { name: "Fixture View" }).click();
  const viewBody = page.getByText("Fixture view body", { exact: true });
  await viewBody.waitFor();
  assert.equal(await viewBody.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "view has plugin-specific CSS scope");
  await panel.getByRole("button", { name: "Chat", exact: true }).click();
  await panel.getByRole("button", { name: /Broken Board/ }).click({ timeout: 5000 });
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  const boardMenu = panel.getByRole("button", { name: /Fixture Board/ });
  await boardMenu.getByText("4", { exact: true }).waitFor({ timeout: 5000 });
  assert.equal(await boardMenu.getByText("4", { exact: true }).getAttribute("title"), "4 items need attention");
  await boardMenu.click();
  await page.getByRole("heading", { name: "Fixture Board" }).waitFor({ timeout: 5000 });
  const pageBody = page.getByText("Fixture page body", { exact: true });
  await pageBody.waitFor();
  assert.equal(await pageBody.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "page has plugin-specific CSS scope");
  assert.equal(await pageBody.evaluate(node => getComputedStyle(node).color), "rgb(12, 34, 56)", "plugin stylesheet applies within its scope");
  await page.getByRole("tab", { name: "Second" }).click();
  await page.getByText("Second tab body", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await page.getByRole("button", { name: "Open details" }).click();
  const modalBody = page.getByRole("dialog", { name: "Fixture Details" }).getByText("Fixture modal body");
  await modalBody.waitFor();
  assert.equal(await modalBody.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "modal has plugin-specific CSS scope");
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "Fixture Details" }).waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("tab", { name: "Plugins" }).click({ timeout: 5000 });
  await settings.getByText("Fixture", { exact: true }).waitFor();
  await settings.getByRole("button", { name: "Add Plugin" }).click();
  const add = page.getByRole("dialog", { name: "Add Plugin" });
  await add.getByLabel("Git URL").fill("https://github.com/example/fixture.git");
  await add.getByRole("button", { name: "Review" }).click({ timeout: 5000 });
  await add.getByText("Fixture New", { exact: true }).waitFor();
  await add.getByText(commit, { exact: false }).waitFor();
  await add.getByText("network:example.com", { exact: false }).waitFor();
  assert.equal(confirmedInstall, null, "preview must not install before confirmation");
  await add.getByRole("button", { name: "Install", exact: true }).click();
  await add.waitFor({ state: "hidden" });
  assert.deepEqual(confirmedInstall, { commit, files_sha256: filesHash });
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Session Controls" }).click();
  const sheet = page.getByRole("dialog", { name: "Session Controls" });
  await sheet.getByRole("button", { name: /Fixture Board/ }).click();
  await page.getByRole("heading", { name: "Fixture Board" }).waitFor();
  await sheet.waitFor({ state: "hidden", timeout: 3000 });
  console.log("plugin browser acceptance: opt-out, view, menu, page, modal and mobile sheet passed");
} finally {
  await browser?.close();
  await vite.close();
}
