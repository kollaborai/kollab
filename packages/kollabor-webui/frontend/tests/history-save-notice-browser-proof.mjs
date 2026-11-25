import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

import { makeFixtures, openPage, openVite } from "./learn-browser-acceptance.mjs";

const session = {
  session_id: "history-save-notice",
  name: "History save notice",
  agent: "coder",
  identity: "local-coder",
  profile: "default",
  workspace: "/history-save-notice",
  external: false,
};
const notices = [
  { type: "notice", message: "Conversation history not saved for this message." },
  { type: "notice", message: "Conversation history not saved for this reply." },
];
const { server, baseUrl } = await openVite();
const browser = await chromium.launch({ headless: true });
let view;
try {
  const screenshotDir = process.env.HISTORY_NOTICE_SCREENSHOT_DIR ?? "/tmp/kollab-history-notice-screenshots";
  const sizes = [
    { width: 390, height: 844, isMobile: true, screenshot: "history-save-notice-390.png" },
    { width: 820, height: 1180, isMobile: false, screenshot: "history-save-notice-820.png" },
    { width: 1280, height: 900, isMobile: false, screenshot: "history-save-notice-1280.png" },
  ];

  for (const size of sizes) {
    const fixtures = makeFixtures({ sessions: [session], events: notices });
    view = await openPage(browser, baseUrl, fixtures, {
      viewport: { width: size.width, height: size.height },
      isMobile: size.isMobile,
    });
    await view.page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    for (const notice of notices) {
      await view.page.getByText(`ℹ ${notice.message}`, { exact: false }).waitFor({ state: "visible" });
    }
    const screenshotPath = path.join(screenshotDir, size.screenshot);
    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
    await view.page.screenshot({ path: screenshotPath, animations: "disabled" });
    console.log(`Screenshot ${size.width}x${size.height}: ${path.basename(screenshotPath)}`);
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
