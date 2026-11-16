import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const SESSION = {
  session_id: "thread-scroll-regression",
  name: "Thread scroll regression",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/thread-scroll-regression",
  external: false,
};

const finalLine = "Distinctive final line stays readable.";
const history = [
  { role: "user", content: "Show the thread scroll control." },
  {
    role: "assistant",
    content: [...Array.from({ length: 48 }, (_, index) => `Earlier paragraph ${index + 1}.`), finalLine].join("\n\n"),
  },
];

const { server, baseUrl } = await openVite();
const browser = await chromium.launch({ headless: true });
let view;
try {
  const fixtures = makeFixtures({ sessions: [SESSION], history });
  view = await openPage(browser, baseUrl, fixtures, {
    viewport: { width: 390, height: 844 },
    isMobile: true,
  });
  await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });

  const viewport = view.page.locator('[data-slot="aui_thread-viewport"]');
  await viewport.waitFor({ state: "visible" });
  const finalParagraph = view.page.getByText(finalLine, { exact: true });
  await finalParagraph.waitFor({ state: "visible" });
  const control = view.page.getByRole("button", { name: "Scroll to Bottom" });

  await viewport.evaluate((node) => {
    node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight - 64);
    node.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await control.waitFor({ state: "visible" });
  await view.page.waitForTimeout(100);

  const layout = await view.page.evaluate(({ controlSelector, finalLineText }) => {
    const button = document.querySelector(controlSelector);
    const target = [...document.querySelectorAll("p")].find((node) => node.textContent.trim() === finalLineText);
    const buttonRect = button.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(target);
    const lineRects = [...range.getClientRects()].filter((rect) => rect.width && rect.height);
    const finalTextRect = lineRects.at(-1);
    return {
      button: { top: buttonRect.top, right: buttonRect.right, bottom: buttonRect.bottom, left: buttonRect.left },
      finalLine: { top: finalTextRect.top, right: finalTextRect.right, bottom: finalTextRect.bottom, left: finalTextRect.left },
      overlap: buttonRect.left < finalTextRect.right && buttonRect.right > finalTextRect.left && buttonRect.top < finalTextRect.bottom && buttonRect.bottom > finalTextRect.top,
      viewport: { width: innerWidth, height: innerHeight },
    };
  }, { controlSelector: ".aui-thread-scroll-to-bottom", finalLineText: finalLine });

  const screenshotDir = process.env.THREAD_SCROLL_SCREENSHOT_DIR ?? "/tmp/kollab-thread-scroll-screenshots";
  const screenshotPath = path.join(screenshotDir, "thread-scroll-to-bottom-phone-dark.png");
  fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
  await view.page.screenshot({ path: screenshotPath, animations: "disabled" });
  console.log(`Screenshot 390x844: ${path.basename(screenshotPath)}`);
  console.log(`Scroll-control geometry: ${JSON.stringify(layout)}`);
  assert.equal(layout.overlap, false, "scroll-to-bottom control covers the final message line");
  assert.deepEqual(view.pageErrors, []);
  assert.deepEqual(view.consoleErrors, []);
} finally {
  if (view) await view.context.close();
  await browser.close();
  await server.close();
}
