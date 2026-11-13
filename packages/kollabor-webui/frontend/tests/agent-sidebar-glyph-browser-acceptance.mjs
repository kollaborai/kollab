import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";
import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = process.env.KOLLAB_BROWSER_ARTIFACT_DIR
  ? path.resolve(process.env.KOLLAB_BROWSER_ARTIFACT_DIR)
  : path.join(repoRoot, "docs/screenshots");
const agentTypes = [
  "coder",
  "community",
  "creative-writer",
  "data-analyst",
  "default",
  "kollabor",
  "koordinator",
  "maintainer",
  "native",
  "pr-shepherd",
  "release-planner",
  "research",
  "system",
  "tech-dude",
  "technical-writer",
  "triager",
];

function displayName(type) {
  return type.split("-").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}

function sidebarFixtures() {
  const poolEntries = agentTypes.map((type) => ({
    name: type,
    identity: type,
    roster: "sidebar-demo",
    display_name: `${displayName(type)} Agent`,
    avatar: { kind: "gem" },
    color: [68, 116, 153],
    agent: type === "default" ? "" : type,
    agent_type: type,
    profile: "default",
    available: true,
    active: true,
    pool: true,
  }));
  poolEntries.push({
    name: "remote-coder",
    identity: "remote-coder",
    roster: "sidebar-demo",
    display_name: "Remote Coordinator",
    avatar: { kind: "gem" },
    color: [105, 88, 157],
    agent: "koordinator",
    agent_type: "coordinator",
    profile: "default",
    available: true,
    active: true,
    pool: true,
  });

  const sessions = agentTypes.slice(0, 12).map((type, index) => ({
    session_id: `sidebar-session-${type}`,
    name: `${displayName(type)} Session`,
    identity: type,
    agent: type,
    profile: "default",
    workspace: index < 8 ? "/glyph-demo/alpha" : "/glyph-demo/beta",
    active: true,
  }));
  const offline = agentTypes.slice(12).map((type) => ({
    identity: type,
    workspace: "/glyph-demo/beta",
    last_ran: 1_760_000_000,
  }));
  const remoteOnlyAgent = {
    name: "remote-only-agent",
    device: "remote-build",
    network: "learn-test",
    workspace: "/remote/demo",
    handle: "remote-session-2",
    state: "idle",
    last_seen: 2,
  };
  const offlineWithoutType = {
    identity: "offline-without-type",
    display_name: "Offline Without Type",
    avatar: { kind: "gem" },
    color: [120, 120, 120],
    workspace: "/glyph-demo/beta",
    last_ran: 1_760_000_001,
  };
  return makeFixtures({
    poolEntries,
    sessions,
    offline,
    additionalOfflineAgents: [offlineWithoutType],
    additionalRemoteAgents: [remoteOnlyAgent],
  });
}

async function main() {
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    const chromePath = process.env.CHROME_PATH
      ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    browser = await chromium.launch({
      headless: true,
      ...(chromePath ? { executablePath: chromePath } : {}),
      args: process.platform === "linux" ? ["--no-sandbox"] : [],
    });

    const captures = [
      { width: 390, height: 844, isMobile: true, file: "agent-sidebar-glyph-390.png" },
      { width: 820, height: 1180, isMobile: false, file: "agent-sidebar-glyph-820.png" },
      { width: 1280, height: 900, isMobile: false, file: "agent-sidebar-glyph-1280.png" },
    ];
    let expectedOrder;

    for (const capture of captures) {
      const view = await openPage(
        browser,
        baseUrl,
        sidebarFixtures(),
        { viewport: { width: capture.width, height: capture.height }, isMobile: capture.isMobile },
      );
      try {
        await view.page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
        const trigger = view.page.locator('[data-sidebar="trigger"]');
        const glyphsLocator = view.page.locator("[data-agent-type-glyph]");
        if (capture.width < 1024) {
          await trigger.waitFor({ state: "visible" });
          await trigger.click();
        }
        await glyphsLocator.first().waitFor({ state: "visible" });
        const expectedTypes = agentTypes;
        await view.page.waitForFunction((expected) => {
          const labels = Array.from(document.querySelectorAll("[data-agent-type-glyph]"))
            .map((glyph) => glyph.getAttribute("aria-label"));
          return expected.every((type) => labels.includes(type));
        }, expectedTypes);

        const glyphs = await glyphsLocator.evaluateAll((nodes) => nodes.map((node) => ({
          type: node.getAttribute("aria-label"),
          title: node.getAttribute("title"),
          role: node.getAttribute("role"),
          glyph: node.textContent,
          parent: node.parentElement,
        })).map(({ type, title, role, glyph, parent }) => {
          const name = parent?.querySelector(":scope > span");
          const nameRect = name?.getBoundingClientRect();
          const glyphRect = parent?.lastElementChild?.getBoundingClientRect();
          const row = parent?.closest("button");
          return {
            type,
            title,
            role,
            glyph,
            nameHeight: nameRect?.height ?? 0,
            glyphLeft: glyphRect?.left ?? 0,
            nameRight: nameRect?.right ?? 0,
            rowHeight: row?.getBoundingClientRect().height ?? 0,
          };
        }));
        const labels = glyphs.map((glyph) => glyph.type);
        for (const type of expectedTypes) {
          assert.ok(labels.includes(type), `${type} glyph missing at ${capture.width}px`);
        }
        assert.equal(glyphs.length, expectedTypes.length, `unexpected glyph count at ${capture.width}px`);

        const remoteRows = await view.page
          .locator('[data-sidebar="sidebar"] button[title*=" on remote-build"]')
          .evaluateAll((rows) =>
            rows.map((row) => ({
              title: row.getAttribute("title"),
              glyphCount: row.querySelectorAll("[data-agent-type-glyph]").length,
            })),
          );
        assert.deepEqual(
          remoteRows.map(({ title }) => title).sort(),
          ["Open Remote Coordinator on remote-build", "Open Remote Only Agent on remote-build"].sort(),
          "both the colliding and remote-only rows should be present",
        );
        assert.ok(
          remoteRows.every(({ glyphCount }) => glyphCount === 0),
          "remote rows must not infer a type glyph from local roster names",
        );

        const untypedOfflineRows = view.page
          .locator('[data-sidebar="sidebar"] button')
          .filter({ hasText: "Offline Without Type" });
        assert.equal(await untypedOfflineRows.count(), 1, "offline-only persona should be present");
        assert.equal(
          await untypedOfflineRows.locator("[data-agent-type-glyph]").count(),
          0,
          "offline persona without type metadata must not be assigned an inferred glyph",
        );
        const nonGlyphRowHeight = await untypedOfflineRows.evaluate((row) =>
          row.getBoundingClientRect().height,
        );
        assert.ok(nonGlyphRowHeight > 0, "offline baseline row must have a measurable height");

        for (const glyph of glyphs) {
          assert.equal(
            glyph.rowHeight,
            nonGlyphRowHeight,
            `glyph row height changed for ${glyph.type} at ${capture.width}px`,
          );
          assert.equal(glyph.title, glyph.type, `title mismatch for ${glyph.type}`);
          assert.equal(glyph.role, "img", `missing image role for ${glyph.type}`);
          assert.equal(Array.from(glyph.glyph ?? "").length, 1, `glyph is not one character for ${glyph.type}`);
          assert.ok(glyph.nameHeight <= 22, `name line wrapped for ${glyph.type} at ${capture.width}px`);
          assert.ok(glyph.glyphLeft >= glyph.nameRight - 1, `glyph is not after its name for ${glyph.type}`);
        }

        const sidebarText = await view.page.locator('[data-sidebar="sidebar"]').innerText();
        assert.match(sidebarText, /alpha/i, "first workspace-folder group is missing");
        assert.match(sidebarText, /beta/i, "second workspace-folder group is missing");
        const order = labels.join(",");
        if (expectedOrder === undefined) expectedOrder = order;
        else assert.equal(order, expectedOrder, `agent order changed at ${capture.width}px`);

        const dimensions = await view.page.evaluate(() => ({
          width: innerWidth,
          documentWidth: document.documentElement.scrollWidth,
        }));
        assert.ok(dimensions.documentWidth <= dimensions.width + 1, `horizontal overflow at ${capture.width}px`);
        await view.page.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        fs.mkdirSync(screenshotDir, { recursive: true });
        await view.page.screenshot({ path: path.join(screenshotDir, capture.file), animations: "disabled" });
        assert.deepEqual(view.pageErrors, [], view.pageErrors.join(" | "));
        assert.deepEqual(view.consoleErrors, [], view.consoleErrors.join(" | "));
        assert.equal(view.externalRequests.length, 0, "browser attempted an external API request");
        console.log(`Captured ${capture.width}x${capture.height}: ${capture.file} (${glyphs.length} glyphs)`);
      } finally {
        await view.context.close();
      }
    }
    console.log("Sidebar glyph browser acceptance passed with mocked API data.");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
