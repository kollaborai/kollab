// Browser acceptance for plugin surfaces. All engine responses below are fake;
// this script never starts an engine or touches a real Kollab config/keyring.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";
import { createServer, preview } from "vite";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");

async function captureReview(page, name) {
  const directory = process.env.PLUGIN_SCREENSHOTS;
  if (!directory) return;
  const viewport = page.viewportSize();
  const wasDark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
  for (const width of [1440, 820, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.waitForTimeout(350);
    for (const theme of ["dark", "light"]) {
      await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), theme === "dark");
      const overflow = await page.evaluate(() => {
        const dialogs = [...document.querySelectorAll('[role="dialog"]')];
        return { document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          body: document.body.scrollWidth - document.body.clientWidth,
          dialog: dialogs.length ? Math.max(...dialogs.map(dialog => dialog.scrollWidth - dialog.clientWidth)) : null };
      });
      console.log(`${name} overflow ${width}px ${theme}: ${JSON.stringify(overflow)}`);
      await page.screenshot({ path: resolve(directory, `${name}-${width}-${theme}.png`) });
    }
  }
  await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), wasDark);
  if (viewport) await page.setViewportSize(viewport);
  await page.waitForTimeout(350);
}
const builtDirectory = process.env.PLUGIN_BUILT_DIR;
const serverSource = builtDirectory ? readFileSync(path.resolve(root, "../src/kollabor_webui/server.py"), "utf8") : "";
const importMapHash = serverSource.match(/_PLUGIN_IMPORT_MAP_HASH\s*=\s*"(sha256-[^"]+)"/)?.[1];
if (builtDirectory && !importMapHash) throw new Error("Cannot determine the WebUI import-map CSP hash");
const vite = builtDirectory
  ? await preview({ root, configFile: false, logLevel: "error", build: { outDir: builtDirectory },
    preview: { host: "127.0.0.1", port: 0, headers: { "Content-Security-Policy": `script-src 'self' '${importMapHash}'` } } })
  : await createServer({ root, configFile: path.join(root, "vite.config.ts"), logLevel: "error",
    cacheDir: path.join(process.env.KOLLAB_PLUGIN_TEST_DIR || process.env.HOME, "plugin-vite-cache"),
    server: { host: "127.0.0.1", port: 0, hmr: false } });
let browser;
try {
  if (!builtDirectory) await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || chromium.executablePath() });
  if (process.env.PLUGIN_SCREENSHOTS && process.env.PLUGIN_REFERENCE_URL) {
    const reference = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await reference.goto(process.env.PLUGIN_REFERENCE_URL);
    await reference.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, `${process.env.PLUGIN_REFERENCE_NAME || "failed-load"}-reference.png`) });
    await reference.close();
  }
  if (process.env.PLUGIN_SCREENSHOTS && process.env.PLUGIN_REFERENCE_DIR) {
    const reference = await browser.newPage({ viewport: { width: 1440, height: 800 } });
    for (const name of ["SettingsPlugins-running", "AddPlugin-add", "AddPlugin-update", "StateFailed-load", "StateFailed-render", "StateStopped-noHeartbeat", "StateStopped-crashed", "StateOff-stopping", "StateEmpty-pluginsOff", "ChatTab-default", "Modal-running"]) {
      await reference.goto(pathToFileURL(resolve(process.env.PLUGIN_REFERENCE_DIR, `${name}.html`)).href);
      for (const width of [1440, 820, 390]) {
        await reference.setViewportSize({ width, height: 800 });
        for (const theme of ["dark", "light"]) {
          await reference.locator(".kx").evaluate((node, light) => node.classList.toggle("light", light), theme === "light");
          await reference.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, `reference-${name}-${width}-${theme}.png`) });
        }
      }
    }
    await reference.close();
  }
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const cspViolations = [];
  page.on("console", message => { if (/content security policy/i.test(message.text())) cspViolations.push(message.text()); });
  let inventoryReads = 0;
  let confirmedInstall = null;
  let confirmedUpdate = null;
  let installSource = null;
  let cancelAttempts = 0;
  const brokenImports = [];
  let brokenDisabled = false;
  let brokenDisableCalls = 0;
  let stopRetryCalls = 0;
  let partialStopFailure = false;
  let failCrashLog = true;
  let fixtureRemoved = false;
  let folderless = false;
  // Deliberately skew the engine clock to exercise a remote browser with a different clock.
  const engineNow = () => Date.now() / 1000 + 10_000;
  const commit = "a".repeat(40);
  const filesHash = "b".repeat(64);
  const previousCommit = "c".repeat(40);
  const preview = { job_id: "install-fixture", action: "install", status: "ready", commit, files_sha256: filesHash,
    plugin_id: "fixture-new", source: "https://github.com/example/fixture.git", ref: "main",
    manifest: { name: "Fixture New", version: "1.2.3", uses: ["agents", "agents:unattended", "secret:API_TOKEN", "run:git", "net:example.com"],
      services: { worker: { command: ["git", "status"], secrets: ["API_TOKEN"] } }, python: "python/plugin.py:Plugin",
      engine: "python/engine.py:make_router", web: { entry: "web/dist/index.js" }, contributes: { views: [{ id: "main" }], slash: ["fixture"] } } };
  const entry = { id: "fixture", name: "Fixture", version: "1.2.3", commit: "1234567890abcdef1234567890abcdef12345678", kind: "installed", state: "ok", enabled: true, shadowed: false,
    services: [{ name: "worker", state: "crash-stopped", desired: "stopped", restarts: 5 }],
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
  const damaged = { ...entry, id: "damaged", name: "Damaged", state: "changed-on-disk", error: "Plugin files changed since approval", web: null, contributes: {} };
  const broken = { id: "broken", name: "Broken", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: null, hash: "1" }, contributes: { slash: ["broken-command"], views: [{ id: "broken-view" }], menu: [{ id: "broken-row", label: "Broken Board", open: { page: "board" } }], pages: [{ id: "board" }] } };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: "/engine" } });
    if (url.pathname === "/engine/plugins") { inventoryReads++; return route.fulfill({ json: { schema: 1, server_time: engineNow(), plugins: [...(fixtureRemoved ? [] : [entry]), broken, crasher, damaged, hello] } }); }
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
    if (url.pathname === "/engine/plugins/fixture/update" && route.request().method() === "POST") {
      return route.fulfill({ status: 202, json: { job_id: "update-fixture", status: "staging" } });
    }
    if (url.pathname === "/engine/plugins/jobs/update-fixture" && route.request().method() === "GET") {
      return route.fulfill({ json: { ...preview, job_id: "update-fixture", action: "update", plugin_id: "fixture", previous_commit: previousCommit, changed_folders: ["web/dist", "python"] } });
    }
    if (url.pathname === "/engine/plugins/jobs/update-fixture/confirm" && route.request().method() === "POST") {
      confirmedUpdate = route.request().postDataJSON();
      return route.fulfill({ json: { ...preview, job_id: "update-fixture", action: "update", status: "complete" } });
    }
    if (url.pathname === "/engine/plugins/fixture/services/worker/log" && route.request().method() === "GET") {
      if (failCrashLog) return route.fulfill({ status: 503, json: { detail: "Log is unavailable; retry" } });
      return route.fulfill({ contentType: "text/plain", body: "redacted fixture crash log" });
    }
    if (url.pathname === "/engine/plugins/fixture/services/worker/stop" && route.request().method() === "POST") {
      stopRetryCalls++;
      return route.fulfill({ json: { accepted: true } });
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
      if (partialStopFailure) {
        entry.state = "stop-failed";
        entry.services[0].state = "stop-failed";
        entry.services[0].error = "Could not stop this process. Retry stopping it.";
        return route.fulfill({ status: 409, json: { detail: "Could not stop background processes. The plugin is off; retry stopping them." } });
      }
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/engine/plugins/broken/disable" && route.request().method() === "POST") {
      brokenDisableCalls++;
      if (brokenDisableCalls === 1) return route.fulfill({ status: 409, json: { detail: "Wait for active runs to finish" } });
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
         k.notice({text:"Fixture view notice",button:{label:"Open folder view",open:{view:"fixture-view"}}});
       });` });
    if (url.pathname === "/engine/prompts/libraries") return route.fulfill({ json: { libraries: [], roles: [] } });
    if (url.pathname === "/engine/plugins/events") return route.fulfill({ contentType: "text/event-stream", body: ": open\n\n" });
    if (url.pathname === "/engine/plugins/fixture/api/badge") return route.fulfill({ json: { text: "4", hint: "4 items need attention" } });
    if (url.pathname === "/engine/sessions") return route.fulfill({ json: { sessions: [{ session_id: "fixture-session", name: "Fixture Session", agent: "coder", identity: "local-coder", profile: "default", workspace: folderless ? "" : "/fixture", external: false }], network: { device: "test", remote: [] }, offline: [], hidden: [] } });
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: { history: [{ role: "assistant", content: "Before <fixture_tag><b>untrusted</b></fixture_tag> after <hello_note>Welcome</hello_note>", metadata: {} }], start_index: 0, end_index: 1, has_more_older: false, has_more_newer: false, source: "mirror" } });
    if (url.pathname.startsWith("/api/")) return route.fulfill({ json: { available: false } });
    if (url.pathname.startsWith("/engine/")) return route.fulfill({ json: { sessions: [], profiles: [], agents: [], live: [], bundles: [], variants: {}, roles: [], libraries: [], sections: [], identities: [], teams: [], models: [], commands: [], network: {device:"",remote:[]}, offline: [], hidden: [], order: [] } });
    return route.continue();
  });
  const initialResponse = await page.goto(`${origin}/?plugins=off`, { waitUntil: "domcontentloaded" });
  if (builtDirectory) assert.equal(initialResponse?.headers()["content-security-policy"], `script-src 'self' '${importMapHash}'`);
  await page.getByText("Plugins are off for this page", { exact: false }).waitFor({ timeout: 15000 });
  assert.equal(inventoryReads, 0, "page-wide opt-out must not fetch plugin inventory");
  folderless = true;
  entry.state = "stopping";
  entry.services[0].state = "stopping";
  entry.services[0].stop_deadline = engineNow() + 719;
  entry.services.push({ name: "pulse", state: "running", desired: "running", restarts: 0,
    heartbeat_s: 60, heartbeat: engineNow() - 370, since: engineNow() - 600 });
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  const folderlessPanel = page.getByRole("complementary", { name: "Session Controls" });
  await folderlessPanel.waitFor({ timeout: 15000 });
  await folderlessPanel.getByRole("button", { name: /Fixture Board/ }).getByText("Stopping · 12 min left", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  const stoppingSettings = page.getByRole("dialog", { name: "Settings" });
  await stoppingSettings.getByRole("tab", { name: "Plugins" }).click();
  const stoppingDetails = stoppingSettings.getByRole("region", { name: "Fixture details" });
  await stoppingDetails.getByText("Waiting for the plugin to stop.", { exact: true }).waitFor({ timeout: 5000 });
  await stoppingDetails.getByText("worker", { exact: true }).locator("..").getByText("Stopping · 12 min left", { exact: true }).waitFor({ timeout: 5000 });
  await stoppingDetails.getByText("pulse", { exact: true }).locator("..").getByText("Running · No Heartbeat for 6 min", { exact: true }).waitFor({ timeout: 5000 });
  await captureReview(page, "stopping-heartbeat");
  assert.equal(await stoppingDetails.getByRole("switch", { name: "Fixture enabled" }).isDisabled(), true, "cannot toggle while stopping");
  entry.services.pop();
  entry.state = "ok";
  entry.services[0].state = "running";
  partialStopFailure = true;
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  const partialSettings = page.getByRole("dialog", { name: "Settings" });
  await partialSettings.getByRole("tab", { name: "Plugins" }).click();
  await partialSettings.getByRole("switch", { name: "Fixture enabled" }).click();
  const partialDialog = page.getByRole("dialog", { name: "Turn off Fixture?" });
  await partialDialog.getByRole("button", { name: "Turn Off", exact: true }).click();
  await partialDialog.waitFor({ state: "hidden", timeout: 5000 });
  await partialSettings.getByRole("alert").getByText(/Could not stop background processes/).waitFor();
  await partialSettings.getByRole("button", { name: "Retry Stop", exact: true }).waitFor();
  partialStopFailure = false;
  entry.enabled = false;
  entry.state = "stop-failed";
  entry.services[0].state = "stop-failed";
  entry.services[0].error = "Could not stop this process. Retry stopping it.";
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  const stopFailureSettings = page.getByRole("dialog", { name: "Settings" });
  await stopFailureSettings.getByRole("tab", { name: "Plugins" }).click();
  const stopFailure = stopFailureSettings.getByRole("region", { name: "Fixture details" });
  await stopFailure.getByText("Failed to Stop", { exact: true }).first().waitFor({ timeout: 5000 });
  await stopFailure.getByText("Could not stop this process. Retry stopping it.", { exact: true }).waitFor();
  await stopFailure.getByRole("button", { name: "Retry Stop", exact: true }).click();
  assert.equal(stopRetryCalls, 1, "a failed stop can be retried while the plugin remains off");
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent?.trim() === "Retry Stop" && !button.disabled));
  entry.enabled = true;
  delete entry.services[0].error;
  entry.state = "ok";
  entry.services[0].state = "crash-stopped";
  delete entry.services[0].stop_deadline;
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  const crashNotice = page.getByRole("status").filter({ hasText: /Fixture: worker.*stopped after 5 crashes/ });
  await crashNotice.waitFor({ timeout: 15000 });
  await crashNotice.getByRole("button", { name: "View Log", exact: true }).click();
  const crashLog = page.getByRole("dialog", { name: "worker Log" });
  await crashLog.getByRole("alert").getByText("Log is unavailable; retry", { exact: true }).waitFor();
  await crashLog.getByRole("button", { name: "Close" }).click();
  failCrashLog = false;
  await crashNotice.getByRole("button", { name: "View Log", exact: true }).click();
  await crashLog.getByText("redacted fixture crash log", { exact: true }).waitFor();
  await crashLog.getByRole("button", { name: "Close" }).click();
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  await folderlessPanel.getByRole("button", { name: /Fixture Board/ }).getByText("Stopped", { exact: true }).waitFor({ timeout: 5000 });
  assert.equal(await folderlessPanel.getByRole("button", { name: "Fixture View", exact: true }).count(), 0, "folder-required tab is hidden without a folder");
  await folderlessPanel.getByRole("button", { name: "Hide Session Panel" }).click();
  assert.equal(await folderlessPanel.getByRole("button", { name: "Fixture View", exact: true }).count(), 0, "folder-required tab stays hidden in collapsed controls");
  await folderlessPanel.getByRole("button", { name: "Show Session Panel" }).click();
  await page.getByRole("button", { name: "Open folder view" }).click();
  await page.getByText("Open a chat with a folder to use this view.", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByRole("textbox", { name: "Message input" }).waitFor({ timeout: 5000 });
  assert.equal(await page.getByText("This plugin view is unavailable for this chat.").count(), 0, "folderless direct navigation stays on Chat");
  folderless = false;
  entry.services[0].state = "running";
  entry.services[0].heartbeat_s = 60;
  entry.services[0].heartbeat = engineNow() - 370;
  entry.services[0].since = engineNow() - 600;
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  const stalePanel = page.getByRole("complementary", { name: "Session Controls" });
  await stalePanel.getByRole("button", { name: /Fixture Board/ }).getByText("No Heartbeat", { exact: true }).waitFor({ timeout: 5000 });
  await stalePanel.getByRole("button", { name: "Hide Session Panel" }).click();
  await stalePanel.getByRole("button", { name: "Fixture Board No Heartbeat", exact: true }).waitFor({ timeout: 5000 });
  await stalePanel.getByRole("button", { name: "Show Session Panel" }).click();
  delete entry.services[0].heartbeat_s;
  delete entry.services[0].heartbeat;
  delete entry.services[0].since;
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  assert.ok(inventoryReads > 0, "host must fetch inventory");
  await page.getByTestId("plugin-tag").waitFor({ timeout: 5000 });
  await page.getByText("Hello note: Welcome", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByText("Hello plugin is ready.", { exact: true }).waitFor();
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
  await panel.getByRole("button", { name: "Hide Session Panel" }).click();
  const collapsedFailed = panel.getByRole("button", { name: "Broken Board Failed", exact: true });
  await collapsedFailed.waitFor({ timeout: 5000 });
  await collapsedFailed.click();
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  await captureReview(page, "failed-load");
  await page.getByRole("button", { name: "Back to Chat" }).click();
  const collapsedView = panel.getByRole("button", { name: "Fixture View", exact: true });
  await collapsedView.waitFor({ timeout: 5000 });
  await collapsedView.hover();
  await page.getByRole("tooltip").getByText("Fixture View", { exact: true }).waitFor();
  await collapsedView.click();
  await page.getByText("Fixture view body", { exact: true }).waitFor();
  await captureReview(page, "plugin-tab");
  await panel.getByRole("button", { name: "Show Session Panel" }).click();
  await panel.getByRole("button", { name: "Fixture View" }).click();
  await panel.getByRole("button", { name: "Hello View", exact: true }).click({ timeout: 5000 });
  await page.getByText("Hello from your plugin!", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Say Hello" }).click();
  await page.getByText("Hello count: 1", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Open Hello Dialog" }).click();
  const helloModal = page.getByRole("dialog", { name: "Hello Dialog" });
  await helloModal.getByText("Hello from the dialog.", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await helloModal.waitFor({ state: "hidden" });
  await panel.getByRole("button", { name: "Hello Page", exact: true }).click();
  await page.getByRole("heading", { name: "Hello Page" }).waitFor();
  await page.getByText("This page is provided by the Hello plugin.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  const helloInput = page.getByRole("textbox", { name: "Message input" });
  await helloInput.fill("/hello-web World");
  await helloInput.press("Escape");
  await helloInput.press("Enter");
  await page.getByText("Hello, World!", { exact: true }).waitFor();
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
  const brokenImportBeforeRetry = brokenImports.at(-1);
  await Promise.all([
    page.waitForRequest(request => request.url().includes("/plugins/broken/web/index.js") && request.url().includes("retry="), { timeout: 5000 }),
    page.getByRole("alert").getByRole("button", { name: "Retry" }).click(),
  ]);
  await page.getByText("Broken · Failed to Load", { exact: true }).waitFor();
  assert.ok(brokenImports.length >= 2, "retry imports the broken bundle again");
  assert.notEqual(brokenImports.at(-1), brokenImportBeforeRetry, "retry uses a new bundle URL");
  await page.getByRole("alert").getByRole("button", { name: "Turn Off" }).click();
  const failureTurnOff = page.getByRole("dialog", { name: "Turn off Broken?" });
  await failureTurnOff.getByText(/Background processes may take time to stop/).waitFor({ timeout: 5000 });
  await failureTurnOff.getByRole("button", { name: "Turn Off", exact: true }).click();
  await failureTurnOff.getByText("Wait for active runs to finish", { exact: false }).waitFor();
  assert.equal(brokenDisabled, false, "failed turn off remains actionable");
  await failureTurnOff.getByRole("button", { name: "Turn Off", exact: true }).click();
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
  await captureReview(page, "plugin-page");
  await page.getByRole("tab", { name: "Second" }).click();
  await page.getByText("Second tab body", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await page.getByRole("button", { name: "Open details" }).click();
  const modalBody = page.getByRole("dialog", { name: "Fixture Details" }).getByText("Fixture modal body");
  await modalBody.waitFor();
  assert.equal(await modalBody.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "modal has plugin-specific CSS scope");
  await captureReview(page, "plugin-modal");
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "Fixture Details" }).waitFor({ state: "hidden" });
  entry.services[0].state = "crash-stopped";
  delete entry.services[0].stop_deadline;
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText(/Fixture: worker.*stopped after 5 crashes/).waitFor({ timeout: 15000 });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("tab", { name: "Plugins" }).click({ timeout: 5000 });
  const damagedListItem = settings.getByRole("button", { name: "Damaged", exact: true });
  assert.match(await damagedListItem.innerText(), /Changed on Disk/, "inventory changed-on-disk state is distinct from load failure");
  await damagedListItem.click();
  await settings.getByRole("region", { name: "Damaged details" }).getByText("Changed on Disk", { exact: true }).waitFor({ timeout: 5000 });
  await settings.getByRole("button", { name: "Hello", exact: true }).click();
  await settings.getByText("Hello plugin settings example.", { exact: true }).waitFor();
  await settings.getByRole("button", { name: "Fixture", exact: true }).click();
  if (process.env.PLUGIN_SCREENSHOTS) await page.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, "settings-actual.png") });
  const fixtureRow = settings.getByRole("region", { name: "Fixture details" });
  await fixtureRow.waitFor({ timeout: 5000 });
  await fixtureRow.getByText("Stopped After 5 Crashes", { exact: true }).waitFor({ timeout: 5000 });
  await captureReview(page, "stopped-service");
  assert.equal(await settings.getByText("Hello plugin settings example.", { exact: true }).count(), 0, "only the selected plugin shows details");
  const listBox = await settings.getByRole("navigation", { name: "Plugin list" }).boundingBox();
  const detailBox = await fixtureRow.boundingBox();
  assert.ok(listBox && detailBox && listBox.x + listBox.width <= detailBox.x, "desktop uses separate list and details columns");
  assert.doesNotMatch(await fixtureRow.innerText(), /v1\.2\.3|1234567890ab/, "settings rows hide version and commit identifiers");
  const customSettings = settings.getByText("Fixture custom settings", { exact: true });
  await customSettings.waitFor();
  assert.equal(await customSettings.locator('xpath=ancestor::*[@data-plugin="fixture"]').count(), 1, "custom settings have plugin-specific CSS scope");
  assert.equal(await customSettings.evaluate(node => getComputedStyle(node).color), "rgb(12, 34, 56)");
  await settings.getByRole("button", { name: "Add Plugin" }).click();
  const add = page.getByRole("dialog", { name: "Add Plugin" });
  await captureReview(page, "add-plugin");
  await add.getByLabel("Git URL").fill("https://github.com/example/fixture.git#other");
  await page.setViewportSize({ width: 390, height: 800 });
  await add.getByLabel("Git URL").waitFor({ timeout: 5000 });
  await page.waitForTimeout(350); // allow the responsive dialog transition to settle
  const mobileSettingsBox = await page.locator('[data-slot="dialog-content"]').first().boundingBox();
  assert.ok(mobileSettingsBox && mobileSettingsBox.width >= 389 && mobileSettingsBox.height >= 799,
    "plugin settings retain their full-screen mobile layout");
  assert.equal(await add.getByLabel("Git URL").inputValue(), "https://github.com/example/fixture.git#other", "open install input survives the mobile breakpoint");
  await page.setViewportSize({ width: 1280, height: 800 });
  await add.getByLabel("Git URL").waitFor({ timeout: 5000 });
  assert.equal(await add.getByLabel("Git URL").inputValue(), "https://github.com/example/fixture.git#other", "open install input survives returning to desktop");
  await add.getByLabel("Branch or Tag").fill("release/demo", { timeout: 5000 });
  await add.getByRole("button", { name: "Review" }).click({ timeout: 5000 });
  await add.getByText("Put the branch or tag in one field, not both.").waitFor();
  assert.equal(installSource, null, "ambiguous branch must not start a job");
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
  await page.setViewportSize({ width: 390, height: 800 });
  await add.getByText(commit, { exact: false }).waitFor({ timeout: 5000 });
  await page.setViewportSize({ width: 1280, height: 800 });
  await add.getByText(commit, { exact: false }).waitFor({ timeout: 5000 });
  assert.equal(confirmedInstall, null, "resizing keeps the staged review without confirming it");
  if (process.env.PLUGIN_SCREENSHOTS) await page.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, "install-review-actual.png") });
  await captureReview(page, "install-review");
  assert.deepEqual(installSource, { source: "https://github.com/example/fixture.git#release/demo" });
  assert.equal(confirmedInstall, null, "preview must not install before confirmation");
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await add.getByText(/Cannot remove staged plugin files/).waitFor();
  assert.equal(await add.isVisible(), true, "failed cancellation remains visible and retryable");
  await add.getByRole("button", { name: "Cancel", exact: true }).click();
  await add.waitFor({ state: "hidden" });
  assert.equal(cancelAttempts, 2);
  await settings.getByRole("button", { name: "Add Plugin" }).click();
  await add.getByLabel("Git URL").fill("https://github.com/example/fixture.git", { timeout: 5000 });
  await add.getByRole("button", { name: "Review" }).click();
  await add.getByText(filesHash, { exact: false }).waitFor();
  await add.getByRole("button", { name: "Install", exact: true }).click();
  await add.waitFor({ state: "hidden" });
  assert.deepEqual(confirmedInstall, { commit, files_sha256: filesHash });
  await fixtureRow.getByRole("button", { name: "Update", exact: true }).click();
  const update = page.getByRole("dialog", { name: "Update Plugin" });
  await update.getByText(previousCommit, { exact: false }).waitFor();
  await update.getByText("web/dist", { exact: false }).waitFor();
  assert.equal(confirmedUpdate, null, "update preview must not apply before confirmation");
  await update.getByRole("button", { name: "Update", exact: true }).click();
  await update.waitFor({ state: "hidden" });
  assert.deepEqual(confirmedUpdate, { commit, files_sha256: filesHash });
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    await page.waitForTimeout(650); // Allow the mobile sheet's 500 ms entrance animation to finish.
    const overflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      body: document.body.scrollWidth - document.body.clientWidth }));
    assert.ok(overflow.document <= 0 && overflow.body <= 0, `settings overflow at ${width}px: ${JSON.stringify(overflow)}`);
    const bounds = await settings.boundingBox();
    assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `settings dialog fits at ${width}px: ${JSON.stringify(bounds)}`);
    if (process.env.PLUGIN_SCREENSHOTS) {
      console.log(`settings overflow ${width}px: ${JSON.stringify(overflow)}`);
      const wasDark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
      for (const theme of ["dark", "light"]) {
        await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), theme === "dark");
        await page.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, `settings-${width}-${theme}.png`) });
      }
      await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), wasDark);
    }
  }
  if (process.env.PLUGIN_SCREENSHOTS) {
    for (const width of [390, 820]) {
      await page.setViewportSize({ width, height: 800 });
      await page.waitForTimeout(650);
      const overflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        body: document.body.scrollWidth - document.body.clientWidth }));
      console.log(`settings overflow ${width}px: ${JSON.stringify(overflow)}`);
      const wasDark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
      for (const theme of ["dark", "light"]) {
        await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), theme === "dark");
        await page.screenshot({ path: resolve(process.env.PLUGIN_SCREENSHOTS, `settings-${width}-${theme}.png`) });
      }
      await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), wasDark);
    }
  }
  await page.setViewportSize({ width: 1280, height: 800 });
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
  if (builtDirectory) assert.deepEqual(cspViolations, [], "built page must not produce CSP violations");
  console.log(`plugin browser acceptance (${builtDirectory ? "production CSP" : "development"}): opt-out, view, menu, page, modal and mobile sheet passed`);
} finally {
  await browser?.close();
  if (builtDirectory) await new Promise((resolveClose, rejectClose) => vite.httpServer.close(error => error ? rejectClose(error) : resolveClose()));
  else await vite.close();
}
