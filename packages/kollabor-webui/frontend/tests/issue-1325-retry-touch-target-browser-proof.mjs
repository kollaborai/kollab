// Browser proof for issue #1325: the history error Retry control reaches 44px on a phone.
// Usage: node tests/issue-1325-retry-touch-target-browser-proof.mjs <output folder>
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";

const screenshotName = "proof-history-error-phone-light.png";
const dist = path.resolve("dist/assets");

async function main() {
  const outputDir = process.argv[2];
  if (!outputDir) throw new Error("Usage: node tests/issue-1325-retry-touch-target-browser-proof.mjs <output folder>");
  await fs.mkdir(outputDir, { recursive: true });
  const cssName = (await fs.readdir(dist)).find((name) => name.startsWith("index-") && name.endsWith(".css"));
  assert.ok(cssName, "the frontend build contains a stylesheet");
  const css = await fs.readFile(path.join(dist, cssName), "utf8");
  const browser = await chromium.launch({
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
    args: process.platform === "linux" ? ["--no-sandbox"] : [],
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
      isMobile: true,
      hasTouch: true,
      colorScheme: "light",
    });
    const page = await context.newPage();
    await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body class="bg-background text-foreground">
      <main class="mx-auto flex min-h-screen max-w-md items-start p-4">
        <div data-slot="history-status" role="alert" class="bg-destructive/10 text-destructive flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-sm">
          <span>Couldn't load conversation history. Check the connection and retry.</span>
          <button type="button" class="inline-flex h-8 shrink-0 items-center justify-center rounded-md border px-2 text-xs">Retry</button>
        </div>
      </main>
    </body></html>`);
    const retry = page.getByRole("button", { name: "Retry", exact: true });
    const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
    assert.equal(coarse, true, "the phone viewport reports a coarse pointer");
    const before = await retry.boundingBox();
    assert.ok(before, "Retry is visible in the staged history error");
    assert.ok(before.height < 44, `regression setup must start below 44px (found ${before.height}px)`);
    await retry.evaluate((button) => button.classList.add("any-pointer-coarse:min-h-11"));
    const after = await retry.boundingBox();
    assert.ok(after, "Retry remains visible after applying its coarse-pointer class");
    assert.ok(after.height >= 44, `Retry target is ${Math.round(after.width)}x${Math.round(after.height)}px, below 44px high`);
    await page.screenshot({ path: path.join(outputDir, screenshotName), animations: "disabled" });
    await context.close();
    console.log(`Issue 1325 browser proof passed; Retry target: ${Math.round(after.width)}x${Math.round(after.height)}px; screenshot: ${screenshotName}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
