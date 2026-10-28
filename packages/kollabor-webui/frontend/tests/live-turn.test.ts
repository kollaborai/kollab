import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyLiveEvent,
  emptyLiveTurn,
  endsLiveTurn,
  historyReplacesLiveTurn,
  isLiveMessage,
  LIVE_MESSAGE_ID,
  liveMessage,
  replacedLiveTurn,
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

test("a turn without a request id ends the live message at its terminal event", () => {
  for (const type of ["turn_complete", "error"]) assert.equal(endsLiveTurn(type), true, type);
  for (const type of ["token", "tool_start", "tool_result", "acp_update", "hub_message", "turn_cancelled"]) {
    assert.equal(endsLiveTurn(type), false, type);
  }
  for (const type of ["turn_complete", "error", "turn_cancelled"]) {
    const turn = applyLiveEvent(applyLiveEvent(emptyLiveTurn(), token("partial")), { type });
    assert.equal(liveMessage(turn), null, type);
  }
});

const asked = (text: string, request_id = "r1") => ({ type: "token", text, request_id });
const reply = (requestId?: string) => ({
  role: "assistant",
  ...(requestId ? { metadata: { custom: { requestId } } } : {}),
});

test("a stopped ACP turn keeps its text, no longer running, until history replaces it", () => {
  let turn = applyLiveEvent(emptyLiveTurn(), asked("chunk 1. "));
  turn = applyLiveEvent(turn, asked("chunk 2. "));
  // The engine announces the stop before the daemon stored the reply.
  turn = applyLiveEvent(turn, { type: "turn_cancelled" });
  const stopped = liveMessage(turn);
  assert.deepEqual(stopped?.content, [{ type: "text", text: "chunk 1. chunk 2. " }]);
  assert.deepEqual(stopped?.status, { type: "incomplete", reason: "cancelled" });
  // Nothing the agent sends after the stop is shown, and the text stays the same.
  assert.equal(applyLiveEvent(turn, asked("late. ")), turn);
  assert.equal(applyLiveEvent(turn, { type: "turn_cancelled" }), turn);
  // The turn's own terminal event arrives after the stop: still the same text.
  assert.equal(applyLiveEvent(turn, { type: "turn_complete", request_id: "r1" }), turn);
  // Only the history that carries the stored reply takes it away, in that import.
  assert.equal(historyReplacesLiveTurn(turn, []), false);
  assert.equal(historyReplacesLiveTurn(turn, [{ role: "user" }]), false);
  assert.equal(historyReplacesLiveTurn(turn, [reply()]), true);
  const replaced = replacedLiveTurn(turn);
  assert.equal(liveMessage(replaced), null);
  assert.equal(applyLiveEvent(replaced, asked("late. ")), replaced);
  assert.equal(applyLiveEvent(replaced, { type: "turn_complete", request_id: "r1" }), replaced);
});

test("a finished or failed ACP turn keeps its text until history replaces it", () => {
  const live = applyLiveEvent(emptyLiveTurn(), asked("hello"));
  assert.deepEqual(liveMessage(live)?.status, { type: "running" });
  const done = applyLiveEvent(live, { type: "turn_complete", request_id: "r1" });
  assert.deepEqual(liveMessage(done)?.status, { type: "complete", reason: "stop" });
  assert.deepEqual(liveMessage(applyLiveEvent(live, { type: "error", request_id: "r1" }))?.status,
    { type: "incomplete", reason: "error" });
});

test("a running turn is replaced only by a row with its own request id", () => {
  const live = applyLiveEvent(emptyLiveTurn(), asked("b1. ", "B"));
  // Turn A's stored reply lands while B streams: B stays.
  assert.equal(historyReplacesLiveTurn(live, [reply("A")]), false);
  assert.equal(historyReplacesLiveTurn(live, [reply()]), false);
  assert.equal(historyReplacesLiveTurn(live, [reply("B")]), true);
});

test("another request's terminal event never ends the live turn, its first chunk is kept", () => {
  // Turn A's terminal event lands after turn B already streamed its first chunk.
  let turn = applyLiveEvent(emptyLiveTurn(), asked("a. ", "A"));
  turn = applyLiveEvent(turn, asked("b1. ", "B"));
  assert.deepEqual(liveMessage(turn)?.content, [{ type: "text", text: "b1. " }]);
  assert.equal(applyLiveEvent(turn, { type: "turn_complete", request_id: "A" }), turn);
  assert.equal(applyLiveEvent(turn, { type: "error", request_id: "A" }), turn);
  turn = applyLiveEvent(turn, asked("b2. ", "B"));
  assert.deepEqual(liveMessage(turn)?.content, [{ type: "text", text: "b1. b2. " }]);
});

test("a queued turn waits behind the stopped one until its stored reply lands", () => {
  let turn = applyLiveEvent(applyLiveEvent(emptyLiveTurn(), asked("a. ", "A")), { type: "turn_cancelled" });
  const stopped = liveMessage(turn);
  turn = applyLiveEvent(turn, asked("b1. ", "B"));
  turn = applyLiveEvent(turn, asked("b2. ", "B"));
  assert.deepEqual(liveMessage(turn), stopped);  // the stopped text never blinks out
  // Its stored reply lands: the queued turn shows from its first chunk, running.
  const next = replacedLiveTurn(turn);
  assert.deepEqual(liveMessage(next)?.content, [{ type: "text", text: "b1. b2. " }]);
  assert.deepEqual(liveMessage(next)?.status, { type: "running" });
  assert.equal(next.requestId, "B");
});

test("a cancelled turn's late tool result never restarts the live message", () => {
  // The engine marks a result that arrives after turn_cancelled (#305).
  const late = { type: "tool_result", tool_id: "a", success: true, output: "done", cancelled: true };
  const started = applyLiveEvent(emptyLiveTurn(), { type: "tool_start", tool_id: "a", tool_name: "terminal" });
  assert.equal(applyLiveEvent(started, late), started);
  assert.equal(liveMessage(applyLiveEvent(emptyLiveTurn(), { ...late, type: "token", text: "x" })), null);
});
