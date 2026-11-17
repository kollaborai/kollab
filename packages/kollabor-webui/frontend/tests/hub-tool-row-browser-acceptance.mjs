// Browser proof for the completed MCP Hub tool row, rendered with no engine, provider, or agent.
// Run from the frontend folder: node tests/hub-tool-row-browser-acceptance.mjs <output folder>
// Saves the collapsed row at desktop, tablet, and phone widths, plus its expanded arguments.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";
import { openVite } from "./learn-browser-acceptance.mjs";

const outputDir = path.resolve(
  process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-1175-tool-row"),
);
const THREAD_IDS = [
  "0123456789abcdef0123456789abcdef",
  "abcdef0123456789abcdef0123456789",
  "1234567890abcdef1234567890abcdef",
  "fedcba9876543210fedcba9876543210",
];
const calls = [
  { title: "Hub Message", to: "Sapphire" },
  { title: "Hub Reply", to: "Sapphire" },
  { title: "Hub Broadcast" },
];

async function main() {
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

    const captures = [
      { width: 1280, height: 900, isMobile: false, file: "hub-tool-row-1280x900.png" },
      { width: 820, height: 1180, isMobile: false, file: "hub-tool-row-820x1180.png" },
      { width: 390, height: 844, isMobile: true, file: "hub-tool-row-390x844.png" },
    ];
    for (const capture of captures) {
      const context = await browser.newContext({
        viewport: { width: capture.width, height: capture.height },
        deviceScaleFactor: 1,
        isMobile: capture.isMobile,
        hasTouch: capture.isMobile,
      });
      const page = await context.newPage();
      const pageErrors = [];
      const consoleErrors = [];
      const failedResponses = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") {
          consoleErrors.push(`${message.text()} (${message.location().url})`);
        }
      });
      page.on("response", (response) => {
        if (response.status() >= 400) {
          failedResponses.push(`${response.status()} ${new URL(response.url()).pathname}`);
        }
      });
      try {
        await page.goto(`${baseUrl}/tests/hub-tool-row.fixture.html`, { waitUntil: "domcontentloaded" });
        const rows = page.locator('[data-slot="tool-fallback-trigger"]');
        await rows.first().waitFor({ state: "visible", timeout: 10_000 });
        assert.equal(await rows.count(), calls.length, "one visible row per Hub tool");

        for (let index = 0; index < calls.length; index += 1) {
          const row = rows.nth(index);
          const visibleText = await row.innerText();
          assert.ok(visibleText.includes(calls[index].title), `readable title missing: ${visibleText}`);
          assert.ok(visibleText.includes("Completed"), `completion state missing: ${visibleText}`);
          assert.ok(!visibleText.includes("Other"), `generic category shown: ${visibleText}`);
          assert.ok(!visibleText.includes("mcp__"), `raw MCP name shown: ${visibleText}`);
          for (const id of THREAD_IDS) {
            assert.ok(!visibleText.includes(id), `internal id shown in collapsed row: ${visibleText}`);
          }
          if (calls[index].to) assert.ok(visibleText.includes(calls[index].to), `recipient missing: ${visibleText}`);
        }

        const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
        assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `horizontal overflow at ${capture.width}px`);
        await page.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        await page.screenshot({
          path: path.join(outputDir, capture.file),
          animations: "disabled",
        });
        console.log(`Screenshot ${capture.width}x${capture.height}: ${capture.file}`);

        if (capture.width === 1280) {
          await rows.first().click();
          const args = page.locator('[data-slot="tool-fallback-args"]').first();
          await args.waitFor({ state: "visible", timeout: 5_000 });
          assert.ok((await args.innerText()).includes(THREAD_IDS[0]), "expanded arguments retain the thread id");
          await page.screenshot({
            path: path.join(outputDir, "hub-tool-row-details-1280x900.png"),
            animations: "disabled",
          });
          console.log("Screenshot 1280x900: hub-tool-row-details-1280x900.png");
        }

        assert.deepEqual(pageErrors, []);
        assert.deepEqual(failedResponses, [], `failed responses: ${failedResponses.join(", ")}`);
        assert.deepEqual(consoleErrors, []);
      } finally {
        await context.close();
      }
    }
    console.log("Hub tool row browser acceptance passed; component-only fixture used.");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
