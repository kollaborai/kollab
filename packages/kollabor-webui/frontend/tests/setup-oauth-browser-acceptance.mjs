// Browser proof for the ChatGPT setup sign-in step.
// Usage: node tests/setup-oauth-browser-acceptance.mjs [output directory]
// Stages waiting, signed-in, expired, denied and network-error states at 1440,
// 820 and 390 pixels in light and dark themes; saves 30 screenshots.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = process.argv[2] ?? path.join(os.tmpdir(), "setup-oauth-proof");
fs.mkdirSync(output, { recursive: true });
const server = await createServer({
  configFile: path.join(frontendRoot, "vite.config.ts"),
  root: frontendRoot,
  logLevel: "error",
  cacheDir: path.join(os.tmpdir(), `setup-oauth-vite-${process.pid}`),
  server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
});
await server.listen();
const address = server.httpServer?.address();
assert.ok(address && typeof address !== "string", "Vite did not expose its local listener");
const baseUrl = `http://127.0.0.1:${address.port}`;
let browser;
const states = ["waiting", "signed_in", "expired", "denied", "network_error"];
const widths = [1440, 820, 390];
const themes = ["light", "dark"];

try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
    args: process.platform === "linux" ? ["--no-sandbox"] : [],
  });
  for (const state of states) {
    for (const theme of themes) {
      for (const width of widths) {
        const page = await browser.newPage({
          viewport: { width, height: width === 390 ? 844 : 900 },
          deviceScaleFactor: 1,
        });
        await page.goto(`${baseUrl}/tests/setup-oauth-proof.html?status=${state}&theme=${theme}`);
        await page.getByRole("heading", { name: "Step 2 of 4: Sign In" }).waitFor();
        await page.getByText("ABCD-EFGH", { exact: true }).waitFor();
        await page.getByRole("link", { name: "Open Page" }).waitFor();
        const expected = {
          waiting: "Waiting for sign-in",
          signed_in: "Signed in as account-123",
          expired: "Code expired",
          denied: "Sign-in denied",
          network_error: "Network error while signing in",
        }[state];
        await page.getByRole("status").getByText(expected).waitFor();
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
          false,
          `horizontal overflow at ${width}px (${theme}, ${state})`,
        );
        const filename = `setup-chatgpt-${state}-${width}-${theme}.png`;
        await page.screenshot({ path: path.join(output, filename), fullPage: true });
        await page.close();
      }
    }
  }
  console.log(`ChatGPT setup screenshots: ${states.length * themes.length * widths.length} saved in ${output}`);
} finally {
  await browser?.close();
  await server.close();
}
