import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyLiveEvent,
  emptyLiveTurn,
  isLiveMessage,
  LIVE_MESSAGE_ID,
  liveMessage,
} from "../src/live-turn.ts";

const token = (text: string) => ({ type: "token", text });

test("tokens accumulate into one streaming text part", () => {
  let turn = emptyLiveTurn();
  turn = applyLiveEvent(turn, token("hello "));
  turn = applyLiveEvent(turn, token("world"));
  const message = liveMessage(turn);
  assert.ok(message);
  assert.equal(message.id, LIVE_MESSAGE_ID);
  assert.equal(message.role, "assistant");
  assert.deepEqual(message.status, { type: "running" });
  assert.deepEqual(message.content, [{ type: "text", text: "hello world" }]);
});

test("a tool call between tokens keeps the arrival order", () => {
  let turn = emptyLiveTurn();
  turn = applyLiveEvent(turn, token("running "));
  turn = applyLiveEvent(turn, {
    type: "tool_start",
    tool_id: "t1",
    tool_name: "bash",
    input: { command: "ls" },
  });
  turn = applyLiveEvent(turn, token("ls"));
  turn = applyLiveEvent(turn, { type: "tool_result", tool_id: "t1", success: true, output: "files" });
  const parts = liveMessage(turn)?.content as Array<Record<string, unknown>>;
  assert.deepEqual(parts, [
    { type: "text", text: "running " },
    {
      type: "tool-call",
      toolCallId: "t1",
      toolName: "bash",
      args: { command: "ls" },
      argsText: '{"command":"ls"}',
      result: { success: true, output: "files", metadata: {} },
      isError: undefined,
      isPreliminary: false,
    },
    { type: "text", text: "ls" },
  ]);
});

test("acp_update builds a harness card and later updates merge into it", () => {
  let turn = emptyLiveTurn();
  turn = applyLiveEvent(turn, {
    type: "acp_update",
    update: { sessionUpdate: "tool_call", toolCallId: "call-1", title: "Edit file", rawInput: { path: "a.ts" } },
  });
  turn = applyLiveEvent(turn, {
    type: "acp_update",
    update: { sessionUpdate: "tool_call_update", toolCallId: "call-1", status: "completed", rawOutput: "ok" },
  });
  const parts = liveMessage(turn)?.content as Array<Record<string, unknown>>;
  assert.equal(parts.length, 1);
  const card = parts[0];
  assert.equal(card.toolName, "harness_tool");
  assert.equal(card.toolCallId, "call-1");
  assert.equal(card.isPreliminary, false);
  assert.equal(card.isError, false);
  assert.deepEqual(card.result, {
    success: true,
    output: "ok",
    metadata: {
      acp: {
        sessionUpdate: "tool_call_update",
        toolCallId: "call-1",
        title: "Edit file",
        rawInput: { path: "a.ts" },
        status: "completed",
        rawOutput: "ok",
      },
    },
  });
});

test("acp_passthrough tool events and non-tool updates carry nothing", () => {
  let turn = emptyLiveTurn();
  turn = applyLiveEvent(turn, { type: "tool_start", acp_passthrough: true, tool_id: "t9", tool_name: "x" });
  turn = applyLiveEvent(turn, { type: "acp_update", update: { sessionUpdate: "agent_message_chunk" } });
  turn = applyLiveEvent(turn, { type: "hub_message" });
  turn = applyLiveEvent(turn, { type: "history_updated" });
  assert.equal(liveMessage(turn), null);
});

test("nothing renders before the first token or tool", () => {
  assert.equal(liveMessage(emptyLiveTurn()), null);
});

test("clearing the live turn drops exactly the ephemeral message", () => {
  const shown = [
    { id: "history-0", role: "user", content: "hi" },
    { id: LIVE_MESSAGE_ID, role: "assistant", content: [{ type: "text", text: "stream" }] },
  ];
  const kept = shown.filter((message) => !isLiveMessage(message));
  assert.deepEqual(kept, [{ id: "history-0", role: "user", content: "hi" }]);

  // The same events rebuild one live message, never a second copy.
  let turn = emptyLiveTurn();
  turn = applyLiveEvent(turn, token("stream"));
  const live = liveMessage(turn);
  assert.deepEqual([...kept, live].filter(isLiveMessage).length, 1);
});
