// Browser proof that the voice type source's repository reference opens the moved wire contract.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const screenshotDir = path.resolve(process.argv[2] ?? path.join(os.tmpdir(), "kollab-voice-wire-contract-proof"));
const sourcePath = path.join(frontendRoot, "src/voice/types.ts");
const source = await readFile(sourcePath, "utf8");
const reference = source.match(/^\/\/ Wire shapes from (\S+)\.$/m)?.[1];
assert.ok(reference, "voice type source must name its wire contract");
const contractPath = path.resolve(repoRoot, reference);
assert.ok(contractPath.startsWith(`${repoRoot}${path.sep}`), "contract reference must stay in the repository");

const server = createServer(async (request, response) => {
  const pathname = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
  if (pathname === "/") {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(`<!doctype html><html><body><a href="/${reference}">Voice wire contract</a></body></html>`);
    return;
  }
  if (pathname !== `/${reference}`) {
    response.writeHead(404).end("Not found");
    return;
  }
  try {
    const contract = await readFile(contractPath, "utf8");
    response.writeHead(200, { "content-type": "text/markdown; charset=utf-8" });
    response.end(contract);
  } catch {
    response.writeHead(404).end("Contract not found");
  }
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string", "proof server did not bind a local port");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  });
  await mkdir(screenshotDir, { recursive: true });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${address.port}/`, { waitUntil: "domcontentloaded" });
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded" }),
    page.getByRole("link", { name: "Voice wire contract" }).click(),
  ]);
  const body = await page.locator("body").innerText();
  assert.match(body, /voice_session_id/);
  assert.match(body, /control_session_id/);
  assert.equal(new URL(page.url()).pathname, `/${reference}`);
  await page.screenshot({ path: path.join(screenshotDir, "voice-wire-contract.png"), fullPage: true });
  console.log(`Voice wire-contract browser proof passed: ${reference}`);
} finally {
  await browser?.close();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}
