// Run after `npm ci`: npm run test:e2e:live-pending-input
// A local fake stream checks the pending input while a different client owns the running turn.
import assert from "node:assert/strict";
import {
  harnessRequests,
  harnessValue,
  openChatStoreHarness,
  publish,
  row,
  waitForHarnessValue,
} from "./chat-store-browser.mjs";

const harness = await openChatStoreHarness("live-pending-input");
const { page } = harness;
try {
  await publish(page, "input.state", { id: "remote-input", state: "started", turn_id: "remote-turn" });
  await publish(page, "turn.state", { turn_id: "remote-turn", state: "running", input_ids: ["remote-input"] });
  await publish(page, "row.upsert", row({
    id: "remote-input", kind: "input", role: "user", text: "A message from another client", turnId: "remote-turn",
  }));
  await publish(page, "row.upsert", row({
    id: "remote-reply", kind: "reply", role: "assistant", text: "Alpha", turnId: "remote-turn", status: "running",
  }));
  await waitForHarnessValue(page, "running", "running");
  await waitForHarnessValue(page, "assistant", "Alpha");

  const composer = page.getByRole("textbox", { name: "Message input" });
  await composer.fill("A page send during the remote turn");
  await page.getByRole("button", { name: "Send message while generating" }).click();
  await page.waitForFunction(() => window.chatHarness.requests.some((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST"));

  const send = (await harnessRequests(page)).find((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST");
  assert.ok(send && typeof send.body === "object" && send.body !== null);
  const inputId = send.body.id;
  assert.match(inputId, /^web-[0-9a-f]{24}$/);
  await waitForHarnessValue(page, "users", "A message from another client|A page send during the remote turn");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1,
    "the optimistic row is present once before its canonical row arrives");

  // The one stream carries both the remote reply and the queued input while this send is pending.
  await publish(page, "row.append", { id: "remote-reply", part: 0, text: "Beta" });
  await publish(page, "input.state", { id: inputId, state: "queued", turn_id: null });
  await publish(page, "row.upsert", row({
    id: inputId, kind: "input", role: "user", text: "A page send during the remote turn",
  }));
  await waitForHarnessValue(page, "assistant", "AlphaBeta");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1,
    "the stream row replaces the pending row without making a second bubble");
  await page.getByRole("button", { name: "Send Now" }).click();
  await page.waitForFunction((id) => window.chatHarness.requests.some((request) =>
    request.path === `/sessions/s1/inputs/${id}/send-now` && request.method === "POST"), inputId);

  await publish(page, "turn.state", { turn_id: "remote-turn", state: "ended", input_ids: ["remote-input"] });
  await publish(page, "input.state", { id: "remote-input", state: "ended", turn_id: "remote-turn", outcome: "completed" });
  await waitForHarnessValue(page, "running", "idle");
  assert.equal(await harnessValue(page, "users"), "A message from another client|A page send during the remote turn");
  assert.equal(await page.evaluate(() => window.chatHarness.threadMounts), 1,
    "applying the live rows does not remount the thread");
  assert.deepEqual(harness.pageErrors, []);
  console.log("PASS: a pending page input and a remote turn share one stream without duplicate rows.");
} finally {
  await harness.close();
}
