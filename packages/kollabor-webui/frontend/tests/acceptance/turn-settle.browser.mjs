// Run after `npm ci`: npm run test:e2e:turn-settle
// The canonical input row replaces the pending row, and the ordered turn event settles the composer.
import assert from "node:assert/strict";
import {
  harnessRequests,
  harnessValue,
  openChatStoreHarness,
  publish,
  row,
  waitForHarnessValue,
} from "./chat-store-browser.mjs";

const harness = await openChatStoreHarness("turn-settle");
const { page } = harness;
try {
  const composer = page.getByRole("textbox", { name: "Message input" });
  await composer.fill("Keep one stable input row");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.waitForFunction(() => window.chatHarness.requests.some((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST"));

  const send = (await harnessRequests(page)).find((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST");
  assert.ok(send && typeof send.body === "object" && send.body !== null);
  const inputId = send.body.id;
  assert.match(inputId, /^web-[0-9a-f]{24}$/);
  await waitForHarnessValue(page, "users", "Keep one stable input row");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1,
    "the client shows the input before stream acknowledgement");

  await publish(page, "row.upsert", row({
    id: inputId, kind: "input", role: "user", text: "Keep one stable input row", turnId: "turn-1", status: "running",
  }));
  await publish(page, "input.state", { id: inputId, state: "started", turn_id: "turn-1" });
  await publish(page, "turn.state", { turn_id: "turn-1", state: "running", input_ids: [inputId] });
  await publish(page, "row.upsert", row({
    id: "reply-1", kind: "reply", role: "assistant", text: "The answer", turnId: "turn-1", status: "running",
  }));
  await publish(page, "row.append", { id: "reply-1", part: 0, text: " is 42." });
  await waitForHarnessValue(page, "assistant", "The answer is 42.");
  await waitForHarnessValue(page, "running", "running");

  await publish(page, "turn.state", { turn_id: "turn-1", state: "ended", input_ids: [inputId] });
  await publish(page, "input.state", { id: inputId, state: "ended", turn_id: "turn-1", outcome: "completed" });
  await waitForHarnessValue(page, "running", "idle");
  assert.equal(await harnessValue(page, "users"), "Keep one stable input row");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1,
    "the saved input keeps its original id through turn completion");
  assert.equal(await page.evaluate(() => window.chatHarness.threadMounts), 1,
    "settling a turn does not remount the thread");
  assert.deepEqual(harness.pageErrors, []);
  console.log("PASS: a streamed turn settles in place and preserves the input id.");
} finally {
  await harness.close();
}
