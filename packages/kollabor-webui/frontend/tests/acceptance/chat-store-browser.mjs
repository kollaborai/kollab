import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export async function openChatStoreHarness(fixture, options = {}) {
  const vite = await createServer({
    root: frontendRoot,
    configFile: path.join(frontendRoot, "vite.config.ts"),
    clearScreen: false,
    logLevel: "error",
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  let browser;
  try {
    await vite.listen();
    const address = vite.httpServer?.address();
    assert.ok(address && typeof address !== "string", "Vite did not bind a local port");
    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.CHROME_PATH ?? chromium.executablePath(),
    });
    const page = await browser.newPage(options);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const suffix = options.search ? `?${options.search}` : "";
    await page.goto(`http://127.0.0.1:${address.port}/tests/acceptance/${fixture}.html${suffix}`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      (expected) => document.getElementById("connection")?.textContent === expected,
      options.expectedConnection ?? "live",
    );
    await page.getByRole("textbox", { name: "Message input" }).waitFor();
    await page.waitForFunction(() => (window.chatHarness?.threadMounts ?? 0) === 1);
    return {
      page,
      pageErrors,
      async close() {
        await page.close();
        await browser?.close();
        await vite.close();
      },
    };
  } catch (error) {
    await browser?.close();
    await vite.close();
    throw error;
  }
}

export const row = ({ id, kind, role, text, turnId = null, status = "complete" }) => ({
  id,
  seq: 0,
  kind,
  role,
  turn_id: turnId,
  status,
  parts: text === undefined ? [] : [{ type: "text", text }],
  created_at: "2026-11-25T00:00:00.000Z",
});

export function publish(page, type, data) {
  return page.evaluate(({ eventType, eventData }) => window.chatHarness.publish(eventType, eventData), {
    eventType: type,
    eventData: data,
  });
}

export async function harnessValue(page, id) {
  return page.locator(`#${id}`).textContent();
}

export async function waitForHarnessValue(page, id, expected) {
  await page.waitForFunction(({ probeId, value }) => document.getElementById(probeId)?.textContent === value, {
    probeId: id,
    value: expected,
  });
}

export async function harnessRequests(page) {
  return page.evaluate(() => window.chatHarness.requests);
}
