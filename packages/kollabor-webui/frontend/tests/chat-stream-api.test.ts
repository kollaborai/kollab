import { afterEach, expect, test, vi } from "vitest";
import { EngineApi } from "../src/api";
import { chatStreamCursorAfter } from "../src/chat-stream";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("chat stream reconnect carries sequence, conversation and daemon generation", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url.startsWith("http://engine/")) requests.push(url);
    return new Response(new ReadableStream({ start(controller) { controller.close(); } }), { status: 200 });
  });

  await new EngineApi("http://engine").streamChatEvents("session/one", {
    after: 7,
    conversation_uid: "conv-1",
    generation: "daemon-1",
  });

  expect(requests).toEqual([
    "http://engine/sessions/session%2Fone/stream?after=7&conversation_uid=conv-1&generation=daemon-1",
  ]);
});

test("initial chat stream request omits a replay cursor and receives a snapshot", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url.startsWith("http://engine/")) requests.push(url);
    return new Response(new ReadableStream({ start(controller) { controller.close(); } }), { status: 200 });
  });

  await new EngineApi("http://engine").streamChatEvents("session-1", null);

  expect(requests).toEqual(["http://engine/sessions/session-1/stream"]);
});

test("snapshot cursor uses next_seq minus one and keeps the generation", () => {
  expect(chatStreamCursorAfter({
    seq: 0,
    conversation_uid: "conv-1",
    generation: "daemon-1",
    type: "snapshot",
    data: { rows: [], next_seq: 9, older: null, turns: [], inputs: [] },
  })).toEqual({ after: 8, conversation_uid: "conv-1", generation: "daemon-1" });
});
