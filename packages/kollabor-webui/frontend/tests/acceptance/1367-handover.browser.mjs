// Run with node tests/acceptance/1367-handover.browser.mjs.
// A remote turn can keep streaming while this page's input is being admitted.
import assert from "node:assert/strict";
import {
  harnessRequests,
  harnessValue,
  openChatStoreHarness,
  publish,
  row,
  waitForHarnessValue,
} from "./chat-store-browser.mjs";

const harness = await openChatStoreHarness("1367-handover");
const { page } = harness;
try {
  await publish(page, "input.state", { id: "watching-input", state: "started", turn_id: "watched-turn" });
  await publish(page, "turn.state", { turn_id: "watched-turn", state: "running", input_ids: ["watching-input"] });
  await publish(page, "row.upsert", row({
    id: "watching-input", kind: "input", role: "user", text: "A turn started elsewhere", turnId: "watched-turn",
  }));
  await publish(page, "row.upsert", row({
    id: "watched-reply", kind: "reply", role: "assistant", text: "Before send", turnId: "watched-turn", status: "running",
  }));
  await waitForHarnessValue(page, "running", "running");

  await page.getByRole("textbox", { name: "Message input" }).fill("A page send during handover");
  await page.getByRole("button", { name: "Send message while generating" }).click();
  await page.waitForFunction(() => window.chatHarness.requests.some((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST"));
  const send = (await harnessRequests(page)).find((request) =>
    request.path === "/sessions/s1/inputs" && request.method === "POST");
  assert.ok(send && typeof send.body === "object" && send.body !== null);
  const inputId = send.body.id;

  // No /assistant response or history refresh: the shared stream continues to own the transcript.
  await publish(page, "row.append", { id: "watched-reply", part: 0, text: " while admission is pending" });
  await publish(page, "input.state", { id: inputId, state: "joined", turn_id: "watched-turn" });
  await publish(page, "row.upsert", row({
    id: inputId, kind: "input", role: "user", text: "A page send during handover", turnId: "watched-turn", status: "running",
  }));
  await waitForHarnessValue(page, "assistant", "Before send while admission is pending");
  await waitForHarnessValue(page, "users", "A turn started elsewhere|A page send during handover");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1,
    "the joined input is shown once under the id selected by the page");

  // Permission prompts are assistant tool calls on the same ordered stream.
  // They must stay answerable while the page's queued input joins the remote turn.
  const allowArgs = {
    tool_id: "shell-allow", permission_id: "permission-allow", tool_name: "shell",
    tool_type: "command", risk_level: "high", input: { command: "ls" },
  };
  const denyArgs = {
    tool_id: "shell-deny", permission_id: "permission-deny", tool_name: "filesystem",
    tool_type: "file access", risk_level: "medium", input: { path: "/etc/shadow" },
  };
  const permissionParts = [
    { type: "tool-call", tool_call_id: "permission-call-allow", tool_name: "request_permission", args: allowArgs },
    { type: "tool-call", tool_call_id: "permission-call-deny", tool_name: "request_permission", args: denyArgs },
  ];
  await publish(page, "row.upsert", {
    ...row({ id: "permission-reply", kind: "tool", role: "assistant", turnId: "watched-turn", status: "running" }),
    parts: permissionParts,
  });
  const allowButton = page.getByRole("button", { name: "Allow once" });
  const denyButton = page.getByRole("button", { name: "Deny" });
  await allowButton.first().waitFor();
  await denyButton.first().waitFor();
  await allowButton.first().click();
  await page.waitForFunction(() => window.chatHarness.permissionResponses.length === 1);
  await denyButton.first().click();
  await page.waitForFunction(() => window.chatHarness.permissionResponses.length === 2);
  const permissionResponses = await page.evaluate(() => window.chatHarness.permissionResponses);
  assert.deepEqual(permissionResponses, [
    {
      toolCallId: "permission-call-allow",
      args: allowArgs,
      result: {
        decision: "approve", scope: "once", tool_id: "shell-allow", permission_id: "permission-allow",
      },
    },
    {
      toolCallId: "permission-call-deny",
      args: denyArgs,
      result: {
        decision: "deny", scope: "once", tool_id: "shell-deny", permission_id: "permission-deny",
      },
    },
  ], "each permission outcome keeps its tool call and permission owner from the streamed row");

  await publish(page, "row.upsert", {
    ...row({ id: "permission-reply", kind: "tool", role: "assistant", turnId: "watched-turn", status: "running" }),
    parts: [
      { ...permissionParts[0], result: permissionResponses[0].result },
      { ...permissionParts[1], result: permissionResponses[1].result },
    ],
  });
  await page.getByText("Allowed (once)", { exact: true }).waitFor();
  await page.getByText("Denied (once)", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Allow once" }).count(), 0,
    "a streamed permission result replaces the pending controls");
  assert.equal(await page.getByRole("button", { name: "Deny" }).count(), 0,
    "a streamed denial result is rendered as a settled outcome");

  await publish(page, "turn.state", { turn_id: "watched-turn", state: "ended", input_ids: ["watching-input", inputId] });
  await publish(page, "input.state", { id: "watching-input", state: "ended", turn_id: "watched-turn", outcome: "completed" });
  await publish(page, "input.state", { id: inputId, state: "ended", turn_id: "watched-turn", outcome: "completed" });
  await waitForHarnessValue(page, "running", "idle");
  assert.equal((await harnessValue(page, "ids")).split("|").filter((id) => id === inputId).length, 1);
  assert.equal(await page.evaluate(() => window.chatHarness.threadMounts), 1,
    "handover leaves the mounted transcript in place");
  assert.deepEqual(harness.pageErrors, []);
  console.log("PASS: streamed handover keeps the joined input and remote reply mounted once.");
} finally {
  await harness.close();
}
