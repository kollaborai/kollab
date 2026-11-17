import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchAcceptanceBrowser, makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(frontendRoot, "../../..");
const packageVersion = JSON.parse(readFileSync(path.join(frontendRoot, "package.json"), "utf8")).version;
const screenshotDir = path.resolve(process.argv[2] ?? path.join(repoRoot, "artifacts/version-identity"));

async function main() {
  mkdirSync(screenshotDir, { recursive: true });
  const bundleSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" }).trim();
  const staleSha = bundleSha.startsWith("deadbeef") ? "cafebabe" : "deadbeef";
  const { server, baseUrl } = await openVite();
  let browser;
  try {
    browser = await launchAcceptanceBrowser();

    const synchronized = await openPage(browser, baseUrl, makeFixtures({ engineVersion: `${packageVersion}+${bundleSha.slice(0, 8)}` }));
    await synchronized.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await synchronized.page.getByRole("button", { name: "Learn", exact: true }).waitFor({ state: "visible" });
    await synchronized.page.getByRole("button", { name: "Reload", exact: true }).waitFor({ state: "hidden" });
    await synchronized.page.screenshot({ path: path.join(screenshotDir, "version-build-synced.png"), fullPage: true });
    await synchronized.context.close();

    const mismatch = await openPage(browser, baseUrl, makeFixtures({ engineVersion: `${packageVersion}+${staleSha}` }));
    await mismatch.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const notice = mismatch.page.getByRole("status").filter({
      has: mismatch.page.getByRole("button", { name: "Reload", exact: true }),
    });
    await notice.waitFor({ state: "visible", timeout: 5_000 });
    assert.match(await notice.getAttribute("title"), new RegExp(`build ${bundleSha.slice(0, 8)}.*engine v${packageVersion}\\+${staleSha}`));
    await mismatch.page.screenshot({ path: path.join(screenshotDir, "version-build-mismatch-desktop.png"), fullPage: true });
    await mismatch.context.close();

    const mobile = await openPage(browser, baseUrl, makeFixtures({ engineVersion: `${packageVersion}+${staleSha}` }), {
      viewport: { width: 390, height: 844 },
      isMobile: true,
    });
    await mobile.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const mobileNotice = mobile.page.getByRole("status").filter({
      has: mobile.page.getByRole("button", { name: "Reload", exact: true }),
    });
    await mobileNotice.waitFor({ state: "visible", timeout: 5_000 });
    const overflow = await mobile.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert.equal(overflow, false, "the mismatch notice must not create horizontal overflow at 390px");
    await mobile.page.screenshot({ path: path.join(screenshotDir, "version-build-mismatch-390.png"), fullPage: true });
    await mobile.context.close();

    console.log(`Version identity browser acceptance passed; screenshots: ${screenshotDir}`);
  } finally {
    await browser?.close();
    await server.close();
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error.stack ?? error);
    process.exitCode = 1;
  });
}
