/**
 * Usage: node tests/chat-stream-contract-browser-acceptance.mjs [built-output-dir]
 *
 * Switches (every switch is listed in this header):
 *   [built-output-dir]  the output of `npm run build`. Its CSS styles the page, and the
 *                       screenshots go to <built-output-dir>/ui-proof/. Default: dist/.
 *   CHROME_PATH         optional Chromium executable. Default: playwright-core's bundled build.
 *
 * Stages the chat stream contract (src/chat-stream.ts) on a page: the shape check on every
 * event of the ten fixtures in tests/fixtures/chat-stream/, three probe events, and
 * newInputId(). It starts no Kollab process, engine or web server: Vite runs in middleware
 * mode and the page is set with setContent. Saves 1280x800 and 390x844 screenshots.
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

const fixtureRoot = path.join(frontendRoot, "tests/fixtures/chat-stream");
const fixtures = [];
for (const name of (await readdir(fixtureRoot)).filter((file) => file.endsWith(".json")).sort()) {
  const { description, expect, event_expect: eventExpect = {}, events } = JSON.parse(
    await readFile(path.join(fixtureRoot, name), "utf8"),
  );
  // Events listed in event_expect are built to fail the shape check.
  fixtures.push({ name, description, expect, events, valid: events.length - Object.keys(eventExpect).length });
}
assert.equal(fixtures.length, 10, "expected the ten chat stream fixtures");

const vite = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, "vite.config.ts"),
  server: { middlewareMode: true },
  appType: "custom",
});
let browser;
try {
  const { isChatStreamEvent, newInputId } = await vite.ssrLoadModule("/src/chat-stream.ts");

  const fixtureRows = fixtures.map((fixture) => ({
    ...fixture,
    accepted: fixture.events.filter((event) => isChatStreamEvent(event)).length,
  }));
  for (const row of fixtureRows) {
    assert.equal(
      row.accepted,
      row.valid,
      `${row.name}: ${row.accepted} event(s) pass the shape check, expected ${row.valid}`,
    );
  }

  const probes = [
    {
      id: "unknown-part",
      label: "Row with an unknown part type",
      expected: "rejected",
      event: {
        seq: 1,
        conversation_uid: "probe",
        generation: "daemon-1",
        type: "row.upsert",
        data: {
          id: "probe-row", seq: 1, kind: "input", role: "user", turn_id: null, status: "queued",
          parts: [{ type: "bogus" }], created_at: "2026-11-25T10:00:00Z",
        },
      },
    },
    {
      id: "started-without-turn",
      label: "Input started with no turn",
      expected: "rejected",
      event: {
        seq: 2,
        conversation_uid: "probe",
        generation: "daemon-1",
        type: "input.state",
        data: { id: "probe-input", state: "started", turn_id: null },
      },
    },
    {
      id: "queued-without-turn",
      label: "Input queued with no turn (control)",
      expected: "accepted",
      event: {
        seq: 3,
        conversation_uid: "probe",
        generation: "daemon-1",
        type: "input.state",
        data: { id: "probe-input", state: "queued", turn_id: null },
      },
    },
  ];
  const probeRows = probes.map((probe) => ({
    ...probe,
    result: isChatStreamEvent(probe.event) ? "accepted" : "rejected",
  }));
  for (const probe of probeRows) {
    assert.equal(probe.result, probe.expected, `probe ${probe.id} was ${probe.result}`);
  }

  const ids = [newInputId("web"), newInputId("web")];
  for (const id of ids) assert.match(id, /^web-[0-9a-f]{24}$/);
  assert.notEqual(ids[0], ids[1]);

  const fixtureItems = fixtureRows.map((row) =>
    createElement(
      "li",
      {
        key: row.name,
        "data-fixture": row.name,
        "data-events": String(row.events.length),
        "data-accepted": String(row.accepted),
        className: "space-y-1",
      },
      createElement("h3", { className: "text-sm font-medium" }, row.name),
      createElement("p", { className: "text-xs" }, row.description),
      createElement("p", { className: "text-xs" }, `Shape: ${row.accepted} of ${row.events.length} events accepted`),
      createElement(
        "p",
        { className: "text-xs" },
        `Stream problems expected: ${row.expect.length ? row.expect.join("; ") : "none"}`,
      ),
    ),
  );
  const probeItems = probeRows.map((probe) =>
    createElement(
      "li",
      { key: probe.id, "data-probe": probe.id, "data-result": probe.result, className: "text-sm" },
      `${probe.label}: ${probe.result}`,
    ),
  );
  const markup = renderToStaticMarkup(
    createElement(
      "main",
      { className: "bg-background p-4 text-foreground" },
      createElement(
        "div",
        { className: "mx-auto max-w-5xl space-y-6" },
        createElement("h1", { className: "text-base font-semibold" }, "Chat stream contract"),
        createElement(
          "p",
          { className: "text-xs" },
          "Shape checks from src/chat-stream.ts on every event of the fixtures in tests/fixtures/chat-stream/. " +
            "The stream problems listed under each fixture are checked by the Python contract test, not on this page.",
        ),
        createElement(
          "section",
          { "aria-label": "Fixtures", className: "space-y-3" },
          createElement("h2", { className: "text-sm font-medium" }, "Fixtures"),
          createElement("ul", { className: "space-y-4" }, ...fixtureItems),
        ),
        createElement(
          "section",
          { "aria-label": "Probes", className: "space-y-2" },
          createElement("h2", { className: "text-sm font-medium" }, "Probes"),
          createElement("ul", { className: "space-y-1" }, ...probeItems),
        ),
        createElement(
          "section",
          { "aria-label": "Input ids", className: "space-y-2" },
          createElement("h2", { className: "text-sm font-medium" }, "Input ids"),
          createElement("p", { className: "text-sm" }, `newInputId("web") gives ${ids[0]} and ${ids[1]}`),
        ),
      ),
    ),
  );

  await mkdir(proofRoot, { recursive: true });
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.setContent(
    `<!doctype html><html lang="en"><head><meta charset="utf-8"><style>${css}</style></head><body>${markup}</body></html>`,
  );
  for (const row of fixtureRows) {
    assert.equal(
      await page.locator(`[data-fixture="${row.name}"]`).getAttribute("data-accepted"),
      String(row.valid),
      `${row.name} renders the wrong shape count`,
    );
  }
  for (const probe of probeRows) {
    assert.equal(
      await page.locator(`[data-probe="${probe.id}"]`).getAttribute("data-result"),
      probe.result,
      `${probe.id} renders the wrong result`,
    );
  }

  for (const [width, height, filename] of [
    [1280, 800, "chat-stream-contract-1280x800.png"],
    [390, 844, "chat-stream-contract-390x844.png"],
  ]) {
    await page.setViewportSize({ width, height });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      false,
      `The contract page overflows horizontally at ${width}px`,
    );
    await page.screenshot({ path: path.join(proofRoot, filename), fullPage: true });
    process.stdout.write(`Saved ${filename}\n`);
  }
} finally {
  await browser?.close();
  await vite.close();
}
