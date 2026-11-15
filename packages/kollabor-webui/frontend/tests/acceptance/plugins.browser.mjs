// Browser acceptance for plugin surfaces. All engine responses below are fake;
// this script never starts an engine or touches a real Kollab config/keyring.
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const vite = await createServer({ root, configFile: path.join(root, "vite.config.ts"), logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await vite.listen();
  const address = vite.httpServer?.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || chromium.executablePath() });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let inventoryReads = 0;
  const entry = { id: "fixture", name: "Fixture", kind: "installed", state: "ok", enabled: true, shadowed: false,
    web: { entry: "web/dist/index.js", style: null, hash: "1" }, contributes: {} };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/config") return route.fulfill({ json: { engine_url: "/engine" } });
    if (url.pathname === "/engine/plugins") { inventoryReads++; return route.fulfill({ json: { schema: 1, plugins: [entry] } }); }
    if (url.pathname === "/plugins/fixture/web/index.js") return route.fulfill({ contentType: "text/javascript", body:
      `import React from "react"; import { definePlugin } from "@kollab/plugin";
       export default definePlugin(k => {
         const body = () => React.createElement("p", null, "Fixture page body");
         const second = () => React.createElement("p", null, "Second tab body");
         const modal = () => React.createElement("p", null, "Fixture modal body");
         k.page({id:"board",title:"Fixture Board",tabs:[{id:"first",label:"First",render:body},{id:"second",label:"Second",render:second}]});
         k.menu("board",{count:{path:"/badge",refreshOn:"changed"}});
         k.view({id:"fixture-view",label:"Fixture View",needs_folder:true,render:() => React.createElement("p",null,"Fixture view body")});
         k.modal({id:"details",title:"Fixture Details",tabs:[{id:"info",label:"Info",render:modal}]});
         k.notice({text:"Fixture plugin notice",button:{label:"Open board",open:{page:"board"}}});
         k.notice({text:"Fixture modal notice",button:{label:"Open details",open:{modal:"details"}}});
       });` });
    if (url.pathname === "/engine/plugins/events") return route.fulfill({ contentType: "text/event-stream", body: ": open\n\n" });
    if (url.pathname === "/engine/plugins/fixture/api/badge") return route.fulfill({ json: { count: 4, attention: 1 } });
    if (url.pathname === "/engine/sessions") return route.fulfill({ json: { sessions: [{ session_id: "fixture-session", name: "Fixture Session", agent: "coder", identity: "local-coder", profile: "default", workspace: "/fixture", external: false }], network: { device: "test", remote: [] }, offline: [], hidden: [] } });
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: { history: [], start_index: 0, end_index: 0, has_more_older: false, has_more_newer: false, source: "mirror" } });
    if (url.pathname.startsWith("/engine/")) return route.fulfill({ json: { sessions: [], profiles: [], agents: [], live: [], bundles: [], identities: [], teams: [], models: [], commands: [], network: {device:"",remote:[]}, offline: [], hidden: [], order: [] } });
    return route.continue();
  });
  await page.goto(`${origin}/?plugins=off`, { waitUntil: "domcontentloaded" });
  await page.getByText("Plugins are off for this page", { exact: false }).waitFor({ timeout: 15000 });
  assert.equal(inventoryReads, 0, "page-wide opt-out must not fetch plugin inventory");
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  await page.getByText("Fixture plugin notice", { exact: true }).waitFor({ timeout: 15000 });
  assert.ok(inventoryReads > 0, "host must fetch inventory");
  const panel = page.getByRole("complementary", { name: "Session Controls" });
  await panel.waitFor({ timeout: 15000 });
  await panel.getByRole("button", { name: "Fixture View" }).click();
  await page.getByText("Fixture view body", { exact: true }).waitFor();
  await panel.getByRole("button", { name: "Chat", exact: true }).click();
  await panel.getByRole("button", { name: /Fixture Board/ }).click();
  await page.getByRole("heading", { name: "Fixture Board" }).waitFor({ timeout: 5000 });
  await page.getByText("Fixture page body", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "Second" }).click();
  await page.getByText("Second tab body", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Back to Chat" }).click();
  await page.getByRole("button", { name: "Open details" }).click();
  await page.getByRole("dialog", { name: "Fixture Details" }).getByText("Fixture modal body").waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "Fixture Details" }).waitFor({ state: "hidden" });
  console.log("plugin browser acceptance: opt-out, notice, page, tabs and modal passed");
} finally {
  await browser?.close();
  await vite.close();
}
