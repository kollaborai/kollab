import assert from "node:assert/strict";
import {
  harnessRequests,
  openChatStoreHarness,
} from "./chat-store-browser.mjs";

const harness = await openChatStoreHarness("chat-send-retry");
const { page } = harness;
try {
  const composer = page.getByRole("textbox", { name: "Message input" });
  const sendButton = page.getByRole("button", { name: "Send message" });
  const userRows = page.locator('[data-slot="aui_user-message-root"]');
  const failures = [];

  await composer.fill("uncertain draft");
  await sendButton.click();
  await page.getByText("Could not send this message. Keep it in the composer and retry.", { exact: true }).waitFor();
  const failedRow = userRows.filter({ hasText: "uncertain draft" });
  await failedRow.waitFor();
  const failureStatus = await failedRow.getByRole("alert").count();
  if (failureStatus !== 1) failures.push("a failed optimistic input has no visible status in its message row");

  await sendButton.click();
  await page.waitForFunction(() => window.chatHarness.requests.filter((request) => request.path.endsWith("/inputs")).length === 2);
  const uncertainRequests = (await harnessRequests(page)).filter((request) => request.path.endsWith("/inputs"));
  if (JSON.stringify(uncertainRequests[0]?.body) !== JSON.stringify(uncertainRequests[1]?.body)) {
    failures.push("retry changed the input id or payload after an ambiguous POST failure");
  }
  const acceptedInputIds = await page.evaluate(() => window.chatHarness.acceptedInputIds);
  if (acceptedInputIds.length !== 1) failures.push(`ambiguous retry admitted ${acceptedInputIds.length} distinct inputs`);
  await page.waitForFunction(() => document.getElementById("users")?.textContent?.includes("uncertain draft"));
  const renderedUsers = await page.locator("#users").textContent();
  if (renderedUsers !== "uncertain draft") {
    failures.push(`same-id retry rendered duplicate user rows: ${renderedUsers}`);
  }
  if (await failedRow.getByRole("alert").count() !== 0) {
    failures.push("the same-id retry left the failed status on the accepted input row");
  }

  await page.evaluate(() => window.chatHarness.publish("row.upsert", {
    id: "goal-reply-row",
    seq: 0,
    kind: "reply",
    role: "assistant",
    turn_id: "goal-turn-1",
    status: "complete",
    parts: [
      { type: "text", text: "goal result" },
      { type: "tool-call", tool_call_id: "goal-tool-call-1", tool_name: "goal_probe", args: { goal_id: "goal-1" }, result: { ok: true } },
    ],
    created_at: "2026-11-25T00:00:00.000Z",
  }));
  await page.waitForFunction(() => document.getElementById("assistant")?.textContent?.includes("goal result"), null, { timeout: 3000 }).catch(() => {});
  const assistantCard = page.locator('[data-slot="aui_assistant-message-root"][data-chat-row-id="goal-reply-row"][data-chat-turn-id="goal-turn-1"]');
  if (await assistantCard.count() !== 1) {
    failures.push("assistant row does not expose its authoritative row and turn ids");
  }
  const toolCard = page.locator('[data-chat-tool-call-id="goal-tool-call-1"][data-chat-row-id="goal-reply-row"][data-chat-turn-id="goal-turn-1"]');
  if (await toolCard.count() !== 1) {
    failures.push("tool card does not expose its authoritative tool-call, row, and turn ids");
  }

  await composer.fill("explicit rejection");
  await sendButton.click();
  await page.getByText("Could not send this message. Keep it in the composer and retry.", { exact: true }).waitFor();
  const rejection = (await harnessRequests(page)).filter((request) => request.path.endsWith("/inputs")).at(-1);
  await composer.fill("edited after rejection");
  await sendButton.click();
  await page.waitForFunction(() => window.chatHarness.requests.filter((request) => request.path.endsWith("/inputs")).length === 4);
  const inputRequests = (await harnessRequests(page)).filter((request) => request.path.endsWith("/inputs"));
  if (inputRequests[3]?.body?.text !== "edited after rejection") {
    failures.push("editing the retained composer draft did not send the edited payload");
  }
  if (rejection?.body?.id === inputRequests[3]?.body?.id) {
    failures.push("editing a rejected draft reused its previous input id");
  }
  if (failures.length) throw new Error(failures.join("\n"));
  console.log("PASS: retries retain the original input id and payload, failed rows are visible, and edits use a new id.");
} finally {
  await harness.close();
}
