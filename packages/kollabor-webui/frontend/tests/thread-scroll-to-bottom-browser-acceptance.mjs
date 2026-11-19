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
  const screenshotDir = process.env.THREAD_SCROLL_SCREENSHOT_DIR ?? "/tmp/kollab-thread-scroll-screenshots";
  const sizes = [
    { width: 390, height: 844, isMobile: true, screenshot: "thread-scroll-to-bottom-phone-dark.png" },
    { width: 820, height: 1180, isMobile: false, screenshot: "thread-scroll-to-bottom-tablet-dark.png" },
    { width: 1280, height: 900, isMobile: false, screenshot: "thread-scroll-to-bottom-desktop-dark.png" },
  ];

  for (const size of sizes) {
    const fixtures = makeFixtures({ sessions: [SESSION], history });
    view = await openPage(browser, baseUrl, fixtures, {
      viewport: { width: size.width, height: size.height },
      isMobile: size.isMobile,
    });
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });

    const viewport = view.page.locator('[data-slot="aui_thread-viewport"]');
    await viewport.waitFor({ state: "visible" });
    await view.page.getByText(finalLine, { exact: true }).waitFor({ state: "visible" });
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
        buttonClass: button.className,
        finalLine: { top: finalTextRect.top, right: finalTextRect.right, bottom: finalTextRect.bottom, left: finalTextRect.left },
        overlap: buttonRect.left < finalTextRect.right && buttonRect.right > finalTextRect.left && buttonRect.top < finalTextRect.bottom && buttonRect.bottom > finalTextRect.top,
        viewport: { width: innerWidth, height: innerHeight },
      };
    }, { controlSelector: ".aui-thread-scroll-to-bottom", finalLineText: finalLine });

    const screenshotPath = path.join(screenshotDir, size.screenshot);
    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
    await view.page.screenshot({ path: screenshotPath, animations: "disabled" });
    console.log(`Screenshot ${size.width}x${size.height}: ${path.basename(screenshotPath)}`);
    console.log(`Scroll-control geometry: ${JSON.stringify(layout)}`);
    assert.match(layout.buttonClass, /(?:^|\s)rounded-md(?:\s|$)/, "scroll-to-bottom control uses the standard button radius");
    assert.doesNotMatch(layout.buttonClass, /(?:^|\s)rounded-full(?:\s|$)/, "scroll-to-bottom control is not fully rounded");
    assert.equal(layout.overlap, false, `scroll-to-bottom control covers the final message line at ${size.width}px`);
    assert.deepEqual(view.pageErrors, []);
    assert.deepEqual(view.consoleErrors, []);
    await view.context.close();
    view = null;
  }
} finally {
  if (view) await view.context.close();
  await browser.close();
  await server.close();
}
