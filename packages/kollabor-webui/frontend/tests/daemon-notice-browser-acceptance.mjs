/**
 * Usage: node tests/daemon-notice-browser-acceptance.mjs [built-output-dir]
 * Renders the real notice component without starting Kollab, its engine, or a web server.
 * Saves desktop, 820px, and 390px screenshots in <built-output-dir>/ui-proof/.
 */
import assert from "node:assert/strict";
import { mkdir, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputRoot = path.resolve(process.argv[2] ?? path.join(frontendRoot, "dist"));
const proofRoot = path.join(outputRoot, "ui-proof");
const assetRoot = path.join(outputRoot, "assets");
const cssAssets = (await readdir(assetRoot)).filter((name) => name.endsWith(".css"));
assert.ok(cssAssets.length, `No built CSS found under ${assetRoot}; run npm run build first.`);
const css = (await Promise.all(cssAssets.map((name) => readFile(path.join(assetRoot, name), "utf8")))).join("\n");

const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  server: { middlewareMode: true },
  appType: "custom",
});
let browser;
try {
  const [{ DaemonNotice }, { agentVersionNotice }] = await Promise.all([
    vite.ssrLoadModule("/src/components/code/DaemonNotice.tsx"),
    vite.ssrLoadModule("/src/components/shell/AgentVersionNotice.tsx"),
  ]);
  const notice = agentVersionNotice({
    engineVersion: "0.22.0+aaaa1111",
    agent: { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w", version: "" },
    restartAgents: false,
  });
  assert.ok(notice);
  const recoveryProps = {
    notice,
    fallbackName: "lapis",
    busy: false,
    onRestart: () => {},
  };
  const panels = ["Terminal", "Code", "Git"].map((name) =>
    createElement(
      "section",
      { key: name, "aria-label": name, className: "min-w-0 space-y-1" },
      createElement("h2", { className: "text-xs font-medium text-muted-foreground" }, name),
      createElement(DaemonNotice, recoveryProps),
    ),
  );
  const markup = renderToStaticMarkup(
    createElement(
      "main",
      { className: "bg-background p-4 text-foreground" },
      createElement(
        "div",
        { className: "mx-auto max-w-5xl space-y-4" },
        createElement("h1", { className: "text-sm font-medium" }, "Older daemon recovery"),
        createElement("div", { className: "grid grid-cols-1 gap-4 md:grid-cols-3" }, ...panels),
      ),
    ),
  );

  await mkdir(proofRoot, { recursive: true });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.setContent(`<!doctype html><html lang="en"><head><meta charset="utf-8"><style>${css}</style></head><body>${markup}</body></html>`);

  const instruction = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";
  for (const name of ["Terminal", "Code", "Git"]) {
    const panel = page.getByRole("region", { name });
    assert.equal(await panel.getByText(instruction).count(), 1, `${name} is missing the named stop-and-start action`);
    assert.equal(await panel.getByText("Retry").count(), 0, `${name} offers Retry for daemon_outdated`);
  }

  for (const [width, height, filename] of [
    [1280, 800, "daemon-notice-1280x800.png"],
    [820, 900, "daemon-notice-820x900.png"],
    [390, 844, "daemon-notice-390x844.png"],
  ]) {
    await page.setViewportSize({ width, height });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      false,
      `The notice overflows horizontally at ${width}px`,
    );
    const bounds = await page.locator("main").boundingBox();
    assert.ok(bounds);
    await page.screenshot({
      path: path.join(proofRoot, filename),
      clip: { x: 0, y: 0, width, height: Math.ceil(bounds.y + bounds.height) },
    });
    process.stdout.write(`Saved ${filename}\n`);
  }
} finally {
  await browser?.close();
  await vite.close();
}
