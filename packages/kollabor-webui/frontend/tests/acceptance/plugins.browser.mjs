// Browser acceptance for plugin surfaces. All engine responses below are fake;
// this script never starts an engine or touches a real Kollab config/keyring.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  if (process.env.PLUGIN_SCREENSHOTS && process.env.PLUGIN_REFERENCE_URL) {
    const reference = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await reference.goto(process.env.PLUGIN_REFERENCE_URL);
    await reference.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, "failed-load-reference.png") });
    await reference.close();
  }
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let inventoryReads = 0;
  let confirmedInstall = null;
  let installSource = null;
  let cancelAttempts = 0;
  const brokenImports = [];
  let brokenDisabled = false;
  let fixtureRemoved = false;
  const commit = "a".repeat(40);
  const filesHash = "b".repeat(64);
  const preview = { job_id: "install-fixture", action: "install", status: "ready", commit, files_sha256: filesHash,
    plugin_id: "fixture-new", source: "https://github.com/example/fixture.git", ref: "main",
    manifest: { name: "Fixture New", version: "1.2.3", uses: ["agents", "agents:unattended", "secret:API_TOKEN", "run:git", "net:example.com"],
      services: { worker: { command: ["git", "status"], secrets: ["API_TOKEN"] } }, python: "python/plugin.py:Plugin",
      engine: "python/engine.py:make_router", web: { entry: "web/dist/index.js" }, contributes: { views: [{ id: "main" }], slash: ["fixture"] } } };
  const entry = { id: "fixture", name: "Fixture", version: "1.2.3", commit: "1234567890abcdef1234567890abcdef12345678", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: "web/dist/index.css", hash: "1" }, contributes: {
      menu: [{ id: "board", label: "Fixture Board", open: { page: "board" } }],
      views: [{ id: "fixture-view" }], pages: [{ id: "board" }], modals: [{ id: "details" }],
      slash: ["fixture"], renderers: [{ tag: "fixture_tag" }],
    } };
  const helloRoot = resolve("../../../bundles/plugins/hello");
  const helloManifest = JSON.parse(readFileSync(resolve(helloRoot, "kollab-plugin.json"), "utf8"));
  const hello = { ...helloManifest, kind: "installed", state: "ok", enabled: true, shadowed: false,
    settings: [], services: [], web: { ...helloManifest.web, hash: "hello" } };
  const crasher = { id: "crasher", name: "Crash Test", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: null, hash: "1" }, contributes: { views: [{ id: "crash-view" }] } };
  const broken = { id: "broken", name: "Broken", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: null, hash: "1" }, contributes: { slash: ["broken-command"], views: [{ id: "broken-view" }], menu: [{ id: "broken-row", label: "Broken Board", open: { page: "board" } }], pages: [{ id: "board" }] } };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: "/engine" } });
    if (url.pathname === "/engine/plugins") { inventoryReads++; return route.fulfill({ json: { schema: 1, plugins: [...(fixtureRemoved ? [] : [entry]), broken, crasher, hello] } }); }
    if (url.pathname === "/engine/plugins/install" && route.request().method() === "POST") {
      installSource = route.request().postDataJSON();
      if (!installSource?.source) return route.fulfill({ status: 422, json: { detail: "source is required" } });
      return route.fulfill({ status: 202, json: { job_id: "install-fixture", status: "staging" } });
    }
    if (url.pathname === "/engine/plugins/jobs/install-fixture" && route.request().method() === "DELETE") {
      cancelAttempts++;
      if (cancelAttempts === 1) return route.fulfill({ status: 503, json: { detail: "Cannot remove staged plugin files; try again" } });
      return route.fulfill({ json: { ...preview, status: "cancelled" } });
    }
    if (url.pathname === "/engine/plugins/jobs/install-fixture" && route.request().method() === "GET") return route.fulfill({ json: preview });
    if (url.pathname === "/engine/plugins/jobs/install-fixture/confirm" && route.request().method() === "POST") {
      confirmedInstall = route.request().postDataJSON();
      return route.fulfill({ json: { ...preview, status: "complete" } });
    }
    if (url.pathname === "/engine/plugins/fixture/enable" && route.request().method() === "POST") {
      entry.enabled = true;
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/engine/plugins/fixture/remove" && route.request().method() === "POST") {
      fixtureRemoved = true;
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/engine/plugins/fixture/disable" && route.request().method() === "POST") {
      entry.enabled = false;
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/engine/plugins/broken/disable" && route.request().method() === "POST") {
      brokenDisabled = true;
      broken.enabled = false;
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/plugins/broken/web/index.js") {
      brokenImports.push(url.href);
      return route.fulfill({ status: 500, contentType: "text/plain", body: "broken" });
    }
    if (url.pathname === "/plugins/hello/web/index.js" || url.pathname === "/plugins/hello/web/index.css") {
      const filename = url.pathname.endsWith(".js") ? "index.js" : "index.css";
      return route.fulfill({ contentType: filename.endsWith(".js") ? "text/javascript" : "text/css",
        body: readFileSync(resolve(helloRoot, "web/dist", filename), "utf8") });
    }
    if (url.pathname === "/plugins/crasher/web/index.js") return route.fulfill({ contentType: "text/javascript", body:
      `import React from 'react'; export default k => k.view({id:'crash-view', label:'Crash View', render:() => { ${url.searchParams.has("retry") ? `return React.createElement('p', null, 'Recovered view');` : `throw new Error('view exploded');`} }});` });
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
         k.settings({render:() => React.createElement("p", {className:"scope-probe"}, "Fixture custom settings")});
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
  await panel.getByRole("button", { name: "Hello View", exact: true }).click({ timeout: 5000 });
  await page.getByText("Hello from your plugin!", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Say Hello" }).click();
  await page.getByText("Hello count: 1", { exact: true }).waitFor();
  await panel.getByRole("button", { name: "Hello Page", exact: true }).click();
  await page.getByRole("heading", { name: "Hello Page" }).waitFor();
  await page.getByText("This page is provided by the Hello plugin.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await panel.getByRole("button", { name: "Crash View" }).click();
  const crashAlert = page.getByRole("alert");
  await crashAlert.waitFor({ timeout: 5000 });
  assert.match(await crashAlert.innerText(), /Crash Test · Failed to Render/, "render failure names the plugin");
  await page.getByRole("alert").getByRole("button", { name: "Retry" }).click();
  await page.getByText("Recovered view", { exact: true }).waitFor({ timeout: 5000 });
  await panel.getByRole("button", { name: "Fixture View" }).click();
  const viewBody = page.getByText("Fixture view body", { exact: true });
  await viewBody.waitFor();
  assert.equal(await viewBody.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "view has plugin-specific CSS scope");
  await panel.getByRole("button", { name: "Chat", exact: true }).click();
  await panel.getByRole("button", { name: /^Broken Failed$/ }).click({ timeout: 5000 });
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  await panel.getByRole("button", { name: "Chat", exact: true }).click();
  const chatInput = page.getByRole("textbox", { name: "Message input" });
  await chatInput.fill("/broken-command");
  const failedCommand = page.getByText("Broken Command", { exact: true });
  await failedCommand.waitFor({ timeout: 5000 });
  await failedCommand.click();
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await chatInput.fill("/broken-command args");
  await chatInput.press("Escape");
  await chatInput.press("Enter");
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await panel.getByRole("button", { name: /Broken Board/ }).click({ timeout: 5000 });
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  if (process.env.PLUGIN_SCREENSHOTS) await page.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, "failed-load-actual.png") });
  await page.getByText("View Error", { exact: true }).click();
  await page.getByRole("alert").locator("pre").waitFor();
  await Promise.all([
    page.waitForRequest(request => request.url().includes("/plugins/broken/web/index.js") && request.url().includes("retry="), { timeout: 5000 }),
    page.getByRole("alert").getByRole("button", { name: "Retry" }).click(),
  ]);
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  assert.ok(brokenImports.length >= 2, "retry imports the broken bundle again");
  assert.notEqual(brokenImports[0], brokenImports[1], "retry uses a new bundle URL");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("alert").getByRole("button", { name: "Turn Off" }).click();
  await page.getByText("Broken is off", { exact: true }).waitFor();
  assert.equal(brokenDisabled, true, "Turn Off calls the disable route");
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
  await settings.getByRole("button", { name: "Fixture", exact: true }).click();
  const fixtureRow = settings.getByRole("button", { name: "Fixture", exact: true }).locator("xpath=ancestor::section[1]");
  assert.doesNotMatch(await fixtureRow.innerText(), /v1\.2\.3|1234567890ab/, "settings rows hide version and commit identifiers");
  const customSettings = settings.getByText("Fixture custom settings", { exact: true });
  await customSettings.waitFor();
  assert.equal(await customSettings.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "custom settings have plugin-specific CSS scope");
  assert.equal(await customSettings.evaluate(node => getComputedStyle(node).color), "rgb(12, 34, 56)");
  await settings.getByRole("button", { name: "Add Plugin" }).click();
  const add = page.getByRole("dialog", { name: "Add Plugin" });
  await add.getByLabel("Git URL").fill("https://github.com/example/fixture.git");
  await add.getByRole("button", { name: "Review" }).click({ timeout: 5000 });
  await add.getByText("Fixture New", { exact: true }).waitFor();
  await add.getByText(commit, { exact: false }).waitFor();
  await add.getByText("https://github.com/example/fixture.git", { exact: false }).waitFor();
  await add.getByText("main", { exact: false }).first().waitFor();
  await add.getByText("API_TOKEN", { exact: false }).first().waitFor();
  await add.getByText(/refuses requests made through kollab/i).waitFor();
  await add.getByText(/plugin code.*runs as you/i).waitFor();
  await add.getByText(/without asking/i).waitFor();
  await add.getByText(/unreported.*not capped/i).waitFor();
  assert.deepEqual(installSource, { source: "https://github.com/example/fixture.git" });
  assert.equal(confirmedInstall, null, "preview must not install before confirmation");
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await add.getByText(/Cannot remove staged plugin files/).waitFor();
  assert.equal(await add.isVisible(), true, "failed cancellation remains visible and retryable");
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await add.waitFor({ state: "hidden" });
  assert.equal(cancelAttempts, 2);
  await settings.getByRole("button", { name: "Add Plugin" }).click();
  await add.getByLabel("Plugin Git URL").fill("https://github.com/example/fixture.git", { timeout: 5000 });
  await add.getByRole("button", { name: "Review" }).click();
  await add.getByText(filesHash, { exact: false }).waitFor();
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
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await page.getByRole("button", { name: "Session Controls" }).click();
  await sheet.getByRole("button", { name: "Fixture View" }).click();
  await page.getByText("Fixture view body", { exact: true }).waitFor();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  await settings.getByRole("tab", { name: "Plugins" }).click();
  await settings.getByRole("switch", { name: "Fixture enabled" }).click();
  const turnOff = page.getByRole("dialog", { name: "Turn off Fixture?" });
  await turnOff.getByRole("button", { name: "Turn Off" }).click();
  await turnOff.waitFor({ state: "hidden" });
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
  await page.getByRole("textbox", { name: "Message input" }).waitFor({ timeout: 5000 });
  assert.equal(await page.getByText("This plugin view is unavailable for this chat.").count(), 0, "disabled active view returns to Chat");
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  await settings.getByRole("tab", { name: "Plugins" }).click();
  await settings.getByRole("switch", { name: "Fixture enabled" }).click();
  await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="Fixture enabled"]')?.getAttribute('aria-checked') === 'true');
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
  await panel.getByRole("button", { name: "Fixture View" }).click();
  await page.getByText("Fixture view body", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  await settings.getByRole("tab", { name: "Plugins" }).click();
  await settings.getByRole("button", { name: "Fixture", exact: true }).click();
  await settings.getByRole("button", { name: "Remove", exact: true }).click();
  const remove = page.getByRole("dialog", { name: "Remove Fixture?" });
  await remove.getByRole("button", { name: "Remove", exact: true }).click();
  await remove.waitFor({ state: "hidden" });
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
  await page.getByRole("textbox", { name: "Message input" }).waitFor({ timeout: 5000 });
  assert.equal(await page.getByText("This plugin view is unavailable for this chat.").count(), 0, "removed active view returns to Chat");
  console.log("plugin browser acceptance: opt-out, view, menu, page, modal and mobile sheet passed");
} finally {
  await browser?.close();
  await vite.close();
}
