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

test("chat stream tool output reads one bounded, identity-bound chunk", async () => {
  const requests: Array<{ url: string; authorization: string | null }> = [];
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") {
      return new Response(JSON.stringify({ engine_url: "http://engine" }), { status: 200 });
    }
    requests.push({
      url,
      authorization: new Headers(init?.headers).get("Authorization"),
    });
    return new Response(JSON.stringify({
      row_id: "row/one",
      tool_call_id: "call&one",
      offset: 0,
      next_offset: 5,
      total_chars: 5,
      output_type: "text",
      content: "hello",
      done: true,
    }), { status: 200 });
  });

  const api = new EngineApi("http://engine");
  api.setToken("session-token");
  const chunk = await (api as unknown as {
    getChatStreamToolOutput: (
      sessionId: string,
      rowId: string,
      toolCallId: string,
      options: { offset: number; limit: number },
    ) => Promise<unknown>;
  }).getChatStreamToolOutput("session/one", "row/one", "call&one", { offset: 0, limit: 32768 });

  expect(chunk).toMatchObject({
    row_id: "row/one",
    tool_call_id: "call&one",
    offset: 0,
    next_offset: 5,
    total_chars: 5,
    output_type: "text",
    content: "hello",
    done: true,
  });
  expect(requests).toEqual([{
    url: "http://engine/sessions/session%2Fone/chat-stream/rows/row%2Fone/tool-output?tool_call_id=call%26one&offset=0&limit=32768",
    authorization: "Bearer session-token",
  }]);
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
