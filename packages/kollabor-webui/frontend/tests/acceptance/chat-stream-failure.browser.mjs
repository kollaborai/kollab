// A stream failure is presented as a plain retry notice, never as a raw transport error.
import assert from "node:assert/strict";
import { openChatStoreHarness } from "./chat-store-browser.mjs";

const harness = await openChatStoreHarness("chat-stream-failure", {
  search: "fail-stream",
  expectedConnection: "failed",
});
const { page } = harness;
try {
  const notice = page.getByRole("alert");
  await notice.getByText("Couldn't load conversation history. Check the connection and retry.", { exact: true }).waitFor();
  assert.equal(await page.getByText("Stream ended abruptly", { exact: true }).count(), 0);
  await notice.getByRole("button", { name: "Retry" }).click();
  await page.waitForFunction(() => window.chatHarness.streamConnects === 2);
  await page.waitForFunction(() => document.getElementById("connection")?.textContent === "failed");
  await notice.getByText("Couldn't load conversation history. Check the connection and retry.", { exact: true }).waitFor();
  assert.equal(await page.getByText("Stream ended abruptly", { exact: true }).count(), 0);
  assert.deepEqual(harness.pageErrors, []);
  console.log("PASS: stream failure shows the plain retry notice and Retry reconnects.");
} finally {
  await harness.close();
}
