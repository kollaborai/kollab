import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const server = await createServer({
  configFile: path.join(frontendRoot, "vite.config.ts"),
  root: frontendRoot,
  logLevel: "error",
  server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
});
await server.listen();
const address = server.httpServer?.address();
assert.ok(address && typeof address !== "string", "Vite did not expose its local listener");
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  args: process.platform === "linux" ? ["--no-sandbox"] : [],
});
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${address.port}/tests/setup-oauth-panelhost-network-error.html`);
  await page.getByRole("button", { name: "Next" }).click();
  const signInStatus = page.locator("p[role=status]");
  await signInStatus.waitFor();
  await page.waitForTimeout(1200);
  assert.match(
    await signInStatus.innerText(),
    /Network error while signing in/,
    "the sign-in step should show the network error returned by oauth_poll",
  );
  console.log("PanelHost displays the polling network error in the sign-in step");
} finally {
  await browser.close();
  await server.close();
}
