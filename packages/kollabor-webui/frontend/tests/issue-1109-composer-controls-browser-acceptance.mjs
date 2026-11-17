// Run from this directory: node tests/issue-1109-composer-controls-browser-acceptance.mjs <output folder>
// Covers 390/820/1440px, light/dark themes, and idle/running composer controls.
// Saves one screenshot for each of the 12 combinations in the output folder.
// CHROME_PATH may select a browser executable; otherwise use Chrome or Playwright Chromium.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { chromium } from "playwright-core";

import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION = {
  session_id: "issue-1109-composer-layout",
  name: "Composer layout regression",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/composer-layout-regression",
  external: false,
};

const HISTORY = [
  { role: "user", content: "Keep a draft visible while the assistant works." },
  { role: "assistant", content: "The turn is still running." },
];

const DRAFT = [
  "The first line stays in this draft while the current assistant turn is still running.",
  "The second line is long enough to wrap on a narrow screen and fill the composer.",
  "The third line keeps useful text near the right edge of the visible input area.",
  "The fourth line checks that controls do not cover the last readable characters.",
  "The fifth line remains visible at the bottom after the textarea scrolls to the end.",
  "The sixth line gives the composer enough content to reach its maximum height.",
].join("\n");

const SIZES = [
  { width: 390, height: 844, isMobile: true },
  { width: 820, height: 1180, isMobile: false },
  { width: 1440, height: 1000, isMobile: false },
];
const THEMES = ["light", "dark"];
const STATES = ["idle", "running"];

async function main() {
  const screenshotDir = path.resolve(process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-1109-composer"));
  fs.mkdirSync(screenshotDir, { recursive: true });

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

    for (const size of SIZES) {
      for (const theme of THEMES) {
        for (const state of STATES) {
          let releaseHeldTurn;
          const heldTurn = new Promise((resolve) => { releaseHeldTurn = resolve; });
          const fixtures = makeFixtures({ sessions: [SESSION], history: HISTORY });
          const view = await openPage(browser, baseUrl, fixtures, {
            viewport: { width: size.width, height: size.height },
            isMobile: size.isMobile,
          });

          try {
            if (state === "running") {
              await view.page.route(
                (url) => new URL(url).pathname.endsWith(`/sessions/${SESSION.session_id}/assistant`),
                async (route) => {
                  assert.equal(route.request().method(), "POST");
                  await heldTurn;
                  return route.fulfill({
                    status: 200,
                    contentType: "text/event-stream",
                    body: 'data: {"type":"message-finish","finishReason":"stop","path":[]}\n\ndata: [DONE]\n\n',
                  });
                },
              );
            }
            await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
            await view.page.evaluate((isDark) => document.documentElement.classList.toggle("dark", isDark), theme === "dark");

            const input = view.page.getByRole("textbox", { name: "Message input" });
            await input.waitFor({ state: "visible" });
            if (state === "running") {
              await input.fill("Start a held turn without a provider.");
              await view.page.getByRole("button", { name: "Send message" }).click();
              await view.page.getByRole("button", { name: "Stop generating" }).waitFor({ state: "visible" });
            } else {
              await view.page.getByRole("button", { name: "Send message" }).waitFor({ state: "visible" });
            }

            await input.fill(DRAFT);
            await input.evaluate((node) => { node.scrollTop = node.scrollHeight; });
            await view.page.waitForTimeout(100);

            const layout = await view.page.evaluate(() => {
              const inputNode = document.querySelector('textarea[aria-label="Message input"]');
              const inputRect = inputNode.getBoundingClientRect();
              const style = getComputedStyle(inputNode);
              const content = {
                left: inputRect.left + Number.parseFloat(style.paddingLeft),
                right: inputRect.right - Number.parseFloat(style.paddingRight),
                top: inputRect.top + Number.parseFloat(style.paddingTop),
                bottom: inputRect.bottom - Number.parseFloat(style.paddingBottom),
              };
              const controls = [...document.querySelectorAll(
                '[aria-label="Send message"], [aria-label="Send message while generating"], [aria-label="Stop generating"]',
              )].filter((node) => node.getClientRects().length).map((node) => {
                const rect = node.getBoundingClientRect();
                return {
                  label: node.getAttribute("aria-label"),
                  left: rect.left,
                  right: rect.right,
                  top: rect.top,
                  bottom: rect.bottom,
                };
              });
              const overlaps = controls.filter((control) =>
                control.left < content.right && control.right > content.left
                && control.top < content.bottom && control.bottom > content.top,
              );
              return {
                viewport: { width: innerWidth, height: innerHeight },
                input: {
                  left: inputRect.left,
                  right: inputRect.right,
                  top: inputRect.top,
                  bottom: inputRect.bottom,
                  scrollHeight: inputNode.scrollHeight,
                  clientHeight: inputNode.clientHeight,
                  scrollTop: inputNode.scrollTop,
                },
                content,
                controls,
                overlaps,
              };
            });

            assert.ok(layout.input.scrollHeight > layout.input.clientHeight, "the six-line draft should exercise the scrollable composer");
            if (state === "running") {
              assert.deepEqual(
                layout.controls.map((control) => control.label).sort(),
                ["Send message while generating", "Stop generating"].sort(),
                "a running turn should show both send and stop controls",
              );
            } else {
              assert.deepEqual(layout.controls.map((control) => control.label), ["Send message"]);
            }

            const screenshotName = `issue-1109-composer-${size.width}-${theme}-${state}.png`;
            await view.page.screenshot({ path: path.join(screenshotDir, screenshotName), animations: "disabled" });
            console.log(`Screenshot ${size.width}px ${theme} ${state}: ${screenshotName}`);
            console.log(`Composer control geometry: ${JSON.stringify(layout)}`);
            assert.deepEqual(layout.overlaps, [], `draft text overlaps a visible control at ${size.width}px in ${theme} ${state}`);
            assert.deepEqual(view.pageErrors, []);
            assert.deepEqual(view.consoleErrors, []);
            assert.deepEqual(view.externalRequests, []);
          } finally {
            releaseHeldTurn?.();
            await view.context.close();
          }
        }
      }
    }
    console.log("issue 1109 composer controls browser acceptance: ok");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
