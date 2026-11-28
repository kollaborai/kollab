// Phone-width regression proof for a long MCP server name beside its status badge.
// Run from the frontend folder: node tests/issue-1440-mcp-title-browser-acceptance.mjs <output folder>
// Saves issue-1440-mcp-title-390x844.png.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";
import { openVite } from "./learn-browser-acceptance.mjs";

const outputDir = path.resolve(process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-1440"));
fs.mkdirSync(outputDir, { recursive: true });
const { server, baseUrl } = await openVite();
let browser;
try {
  browser = await chromium.launch({ args: process.platform === "linux" ? ["--no-sandbox"] : [] });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await page.goto(`${baseUrl}/tests/issue-1440-mcp-title.fixture.html`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /MCP Servers/ }).click();
  const title = page.getByText("production-mcp-server-with-a-very-long-name-that-keeps-going-across-the-phone-screen");
  const status = page.getByText("Connected", { exact: true });
  await status.waitFor({ state: "visible" });
  const layout = await page.evaluate(() => {
    const title = [...document.querySelectorAll("span")].find((item) => item.textContent?.startsWith("production-mcp-server-with-a-very-long-name"));
    const status = [...document.querySelectorAll("[data-slot='badge']")].find((item) => item.textContent?.trim() === "Connected");
    if (!title || !status) throw new Error("server title or status badge is missing");
    const titleBox = title.getBoundingClientRect();
    const statusBox = status.getBoundingClientRect();
    return { titleRight: titleBox.right, statusLeft: statusBox.left, statusRight: statusBox.right, titleWidth: titleBox.width, titleScrollWidth: title.scrollWidth, viewportWidth: innerWidth };
  });
  assert.ok(layout.titleWidth > 0, "the long server title is visible");
  assert.ok(layout.titleRight <= layout.statusLeft, `title overlaps status: ${JSON.stringify(layout)}`);
  assert.ok(layout.statusRight <= layout.viewportWidth, `status is clipped beyond the phone viewport: ${JSON.stringify(layout)}`);
  assert.ok(layout.titleScrollWidth > layout.titleWidth, `long title should truncate before status: ${JSON.stringify(layout)}`);
  await page.screenshot({ path: path.join(outputDir, "issue-1440-mcp-title-390x844.png"), animations: "disabled" });
  console.log("Phone layout passed: issue-1440-mcp-title-390x844.png");
} finally {
  await browser?.close();
  await server.close();
}
