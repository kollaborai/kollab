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
  const revision = "a".repeat(64);
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
      output_revision: revision,
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
      outputRevision: string,
      options: { offset: number; limit: number },
    ) => Promise<unknown>;
  }).getChatStreamToolOutput("session/one", "row/one", "call&one", revision, { offset: 0, limit: 32768 });

  expect(chunk).toMatchObject({
    row_id: "row/one",
    tool_call_id: "call&one",
    output_revision: revision,
    offset: 0,
    next_offset: 5,
    total_chars: 5,
    output_type: "text",
    content: "hello",
    done: true,
  });
  expect(requests).toEqual([{
    url: `http://engine/sessions/session%2Fone/chat-stream/rows/row%2Fone/tool-output?tool_call_id=call%26one&output_revision=${revision}&offset=0&limit=32768`,
    authorization: "Bearer session-token",
  }]);
});

test("chat stream content reads bounded chunks for a row part and field", async () => {
  const requests: string[] = [];
  const revision = "b".repeat(64);
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/config") {
      return new Response(JSON.stringify({ engine_url: "http://engine" }), { status: 200 });
    }
    requests.push(url);
    return new Response(JSON.stringify({
      row_id: "row/one",
      part_index: 2,
      field: "reasoning",
      output_revision: revision,
      offset: 0,
      next_offset: 5,
      total_chars: 5,
      output_type: "text",
      content: "think",
      done: true,
    }), { status: 200 });
  });

  const api = new EngineApi("http://engine");
  const chunk = await (api as unknown as {
    getChatStreamContent: (
      sessionId: string,
      rowId: string,
      partIndex: number,
      field: "text" | "reasoning" | "args",
      outputRevision: string,
      options: { offset: number; limit: number },
    ) => Promise<unknown>;
  }).getChatStreamContent("session/one", "row/one", 2, "reasoning", revision, {
    offset: 0,
    limit: 32768,
  });

  expect(chunk).toMatchObject({
    row_id: "row/one",
    part_index: 2,
    field: "reasoning",
    output_revision: revision,
    offset: 0,
    next_offset: 5,
    total_chars: 5,
    output_type: "text",
    content: "think",
    done: true,
  });
  expect(requests).toEqual([
    `http://engine/sessions/session%2Fone/chat-stream/rows/row%2Fone/content?part_index=2&field=reasoning&output_revision=${revision}&offset=0&limit=32768`,
  ]);
});

test("chat stream content can recover a complete serialized part including structured fields", async () => {
  const requests: string[] = [];
  const revision = "f".repeat(64);
  const serializedPart = JSON.stringify({
    type: "image",
    url: "data:image/png;base64,large-payload",
  });
  vi.stubGlobal("fetch", async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/config") return new Response(JSON.stringify({ engine_url: "http://engine" }), { status: 200 });
    requests.push(url);
    return new Response(JSON.stringify({
      row_id: "row-structured",
      part_index: 4,
      field: "part",
      output_revision: revision,
      offset: 0,
      next_offset: Array.from(serializedPart).length,
      total_chars: Array.from(serializedPart).length,
      output_type: "json",
      content: serializedPart,
      done: true,
    }), { status: 200 });
  });

  const api = new EngineApi("http://engine");
  const chunk = await (api as unknown as {
    getChatStreamContent: (
      sessionId: string,
      rowId: string,
      partIndex: number,
      field: "part",
      outputRevision: string,
      options: { offset: number; limit: number },
    ) => Promise<unknown>;
  }).getChatStreamContent("session-1", "row-structured", 4, "part", revision, { offset: 0, limit: 32768 });

  expect(chunk).toMatchObject({
    row_id: "row-structured",
    part_index: 4,
    field: "part",
    output_type: "json",
    content: serializedPart,
    done: true,
  });
  expect(requests).toEqual([
    `http://engine/sessions/session-1/chat-stream/rows/row-structured/content?part_index=4&field=part&output_revision=${revision}&offset=0&limit=32768`,
  ]);
});

test("chat stream part pages are revision-bound and count-bounded", async () => {
  const requests: Array<{ url: string; authorization: string | null }> = [];
  const revision = "9".repeat(64);
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/config") return new Response(JSON.stringify({ engine_url: "http://engine" }), { status: 200 });
    requests.push({ url, authorization: new Headers(init?.headers).get("Authorization") });
    return new Response(JSON.stringify({
      row_id: "row-many-parts",
      offset: 20,
      next_offset: 22,
      total_parts: 75,
      output_revision: revision,
      parts: [{ type: "image", url: "data:image/png;base64,AAAA" }, { type: "hub-note", from: "peer", to: "", text: "note", observed: true }],
      done: false,
    }), { status: 200 });
  });

  const api = new EngineApi("http://engine");
  api.setToken("session-token");
  const page = await (api as unknown as {
    getChatStreamParts: (
      sessionId: string,
      rowId: string,
      outputRevision: string,
      options: { offset: number; limit: number },
    ) => Promise<unknown>;
  }).getChatStreamParts("session-1", "row-many-parts", revision, { offset: 20, limit: 2 });

  expect(page).toMatchObject({ row_id: "row-many-parts", offset: 20, next_offset: 22, total_parts: 75, output_revision: revision, done: false });
  expect(requests).toEqual([{
    url: `http://engine/sessions/session-1/chat-stream/rows/row-many-parts/parts?output_revision=${revision}&offset=20&limit=2`,
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
