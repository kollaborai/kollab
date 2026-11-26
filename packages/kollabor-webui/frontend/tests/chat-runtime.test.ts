// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/chat-stream/bad-gap.json";
import { applyChatStreamEvent, initialStreamState } from "../src/chat-store";
import type { ChatRow } from "../src/chat-stream";
import { messageHasRecoverableChatStreamPreview } from "../src/chat-stream-content";
import {
  WEB_INPUT_PREFIX,
  buildAdapter,
  messageText,
  toRepository,
  toThreadMessageLike,
} from "../src/chat-runtime";

const WHEN = "2026-11-25T10:00:00.000Z";

type Flags = Parameters<typeof buildAdapter>[1];
type Store = Parameters<typeof buildAdapter>[0];
type Append = Parameters<typeof messageText>[0];

function row(id: string, parts: ChatRow["parts"], role: ChatRow["role"] = "assistant"): ChatRow {
  return { id, seq: 1, kind: "reply", role, turn_id: null, status: "complete", parts, created_at: WHEN };
}

/** The content of an AppendMessage; the adapter reads nothing else from it. */
function appendOf(content: unknown[]): Append {
  return { content } as unknown as Append;
}

/** An adapter over a fake store whose methods are mocks. */
function adapterFor(flags: Flags) {
  const store = { send: vi.fn(), stop: vi.fn(), loadOlder: vi.fn() };
  const adapter = buildAdapter(store as unknown as Store, flags, toRepository([]));
  return { store, adapter };
}

describe("WEB_INPUT_PREFIX", () => {
  it("is 'web'", () => {
    expect(WEB_INPUT_PREFIX).toBe("web");
  });
});

describe("toThreadMessageLike", () => {
  it("keeps the row id and role, and builds createdAt from created_at", () => {
    const message = toThreadMessageLike(row("row-1", [], "user"));
    expect(message.id).toBe("row-1");
    expect(message.role).toBe("user");
    expect(message.createdAt).toEqual(new Date(WHEN));
  });

  it("carries the authoritative stream turn id in custom metadata", () => {
    const message = toThreadMessageLike({ ...row("reply-1", [], "assistant"), turn_id: "turn-1" });
    expect(message.metadata?.custom).toEqual({
      chatRowKind: "reply",
      chatRowStatus: "complete",
      chatTurnId: "turn-1",
      chatRowRole: "assistant",
    });
  });

  it("maps text and reasoning parts to parts of the same type", () => {
    const message = toThreadMessageLike(
      row("row-2", [
        { type: "reasoning", text: "checking the file" },
        { type: "text", text: "Done." },
      ]),
    );
    expect(message.content).toEqual([
      { type: "reasoning", text: "checking the file" },
      { type: "text", text: "Done." },
    ]);
  });

  it("preserves the full-text retrieval marker beside a bounded text part", () => {
    const marker = {
      kind: "chat-stream-content-preview",
      session_id: "session-1",
      row_id: "row-2",
      part_index: 0,
      field: "text",
      output_bytes: 40_000,
      output_chars: 40_000,
      output_type: "text",
      output_revision: "a".repeat(64),
      preview: "visible prefix",
    };
    const part = {
      type: "text" as const,
      text: "visible prefix",
      chat_stream_content_preview: marker,
    } as unknown as ChatRow["parts"][number];

    const message = toThreadMessageLike(row("row-2", [part]));

    expect(message.content[0]).toMatchObject({
      type: "text",
      text: "visible prefix",
      providerMetadata: { kollab: { chat_stream_content_preview: marker } },
    });
  });

  it("preserves the full-text retrieval marker beside a bounded reasoning part", () => {
    const marker = {
      kind: "chat-stream-content-preview",
      session_id: "session-1",
      row_id: "row-reasoning",
      part_index: 0,
      field: "reasoning",
      output_bytes: 40_000,
      output_chars: 40_000,
      output_type: "text",
      output_revision: "c".repeat(64),
      preview: "reasoning prefix",
    };
    const part = {
      type: "reasoning" as const,
      text: "reasoning prefix",
      chat_stream_content_preview: marker,
    } as unknown as ChatRow["parts"][number];

    const message = toThreadMessageLike(row("row-reasoning", [part]));

    expect(message.content[0]).toMatchObject({
      type: "reasoning",
      text: "reasoning prefix",
      providerMetadata: { kollab: { chat_stream_content_preview: marker } },
    });
  });

  it("passes the dedicated args marker through provider metadata without inspecting args", () => {
    const marker = {
      kind: "chat-stream-content-preview" as const,
      session_id: "session-args",
      row_id: "row-args",
      part_index: 0,
      field: "args" as const,
      output_bytes: 40_000,
      output_chars: 40_000,
      output_type: "json" as const,
      output_revision: "b".repeat(64),
      preview: '{"bounded":true}',
    };
    const markerShapedUserArgs = {
      kind: "chat-stream-content-preview",
      field: "args",
      application_value: "user supplied data",
    };
    const message = toThreadMessageLike(row("row-args", [{
      type: "tool-call",
      tool_call_id: "call-large",
      tool_name: "inspect",
      args: {},
      chat_stream_args_preview: marker,
    }]));
    const toolPart = message.content[0];
    expect(toolPart).toMatchObject({
      type: "tool-call",
      args: {},
      providerMetadata: { kollab: { chat_stream_args_preview: marker } },
    });
    expect(messageHasRecoverableChatStreamPreview(undefined, [toolPart])).toBe(true);

    const opaqueMessage = toThreadMessageLike(row("row-opaque", [{
      type: "tool-call",
      tool_call_id: "call-opaque",
      tool_name: "inspect",
      args: markerShapedUserArgs,
    }]));
    expect(opaqueMessage.content[0]).toMatchObject({ type: "tool-call" });
    expect(opaqueMessage.content[0]).not.toHaveProperty("providerMetadata");
    expect(opaqueMessage.content[0]).toMatchObject({
      argsText: JSON.stringify(markerShapedUserArgs),
    });
    expect(messageHasRecoverableChatStreamPreview(undefined, [opaqueMessage.content[0]])).toBe(false);
  });

  it("carries a row-parts page marker in message metadata and part recovery in provider metadata", () => {
    const partsPreview = {
      kind: "chat-stream-parts-preview",
      session_id: "session-1",
      row_id: "row-large",
      part_count: 120,
      output_bytes: 500_000,
      output_chars: 420_000,
      output_type: "json" as const,
      output_revision: "d".repeat(64),
    };
    const partPreview = {
      kind: "chat-stream-part-preview",
      session_id: "session-1",
      row_id: "row-large",
      part_index: 0,
      output_bytes: 50_000,
      output_chars: 30_000,
      output_type: "json" as const,
      output_revision: "e".repeat(64),
      preview: "[Large part. Expand to load full content.]",
    };
    const largeRow = {
      ...row("row-large", [{
        type: "text" as const,
        text: partPreview.preview,
        chat_stream_part_preview: partPreview,
      }]),
      chat_stream_parts_preview: partsPreview,
    } as unknown as ChatRow;

    const message = toThreadMessageLike(largeRow);

    expect(message.metadata?.custom).toMatchObject({ chatStreamPartsPreview: partsPreview });
    expect(message.content[0]).toMatchObject({
      type: "text",
      text: partPreview.preview,
      providerMetadata: { kollab: { chat_stream_part_preview: partPreview } },
    });
  });

  it("preserves an argument preview marker beside the tool call args", () => {
    const marker = {
      kind: "chat-stream-content-preview",
      session_id: "session-1",
      row_id: "row-3",
      part_index: 1,
      field: "args",
      output_bytes: 40_000,
      output_chars: 40_000,
      output_type: "json",
      output_revision: "b".repeat(64),
      preview: '{"command":"echo..."}',
    };
    const message = toThreadMessageLike(
      row("row-3", [{
        type: "tool-call",
        tool_call_id: "call-1",
        tool_name: "terminal",
        args: {},
        chat_stream_args_preview: marker,
      }]),
    );

    expect(message.content[0]).toMatchObject({
      type: "tool-call",
      args: {},
      providerMetadata: { kollab: { chat_stream_args_preview: marker } },
    });
  });

  it("maps a tool-call part to toolCallId, toolName, args, argsText and isError", () => {
    const message = toThreadMessageLike(
      row("row-3", [
        {
          type: "tool-call",
          tool_call_id: "call-1",
          tool_name: "search",
          args: { query: "kollab" },
          result: { hits: 2 },
          is_error: false,
        },
      ]),
    );
    expect(message.content).toEqual([
      {
        type: "tool-call",
        toolCallId: "call-1",
        toolName: "search",
        args: { query: "kollab" },
        argsText: '{"query":"kollab"}',
        result: { hits: 2 },
        isError: false,
      },
    ]);
  });

  it("normalizes a null tool error flag to undefined", () => {
    const message = toThreadMessageLike(
      row("nullable", [{ type: "tool-call", tool_call_id: "c", tool_name: "test", args: {}, is_error: null }]),
    );
    expect((message.content as { isError?: boolean }[])[0].isError).toBeUndefined();
  });

  it("preserves a failed local input status in custom metadata", () => {
    const message = toThreadMessageLike({
      ...row("send-failed", [{ type: "text", text: "keep this draft" }], "user"),
      status: "failed",
    });
    expect(message.metadata?.custom).toEqual({
      chatRowKind: "reply",
      chatRowStatus: "failed",
      chatTurnId: null,
      chatRowRole: "user",
    });
  });

  it("keeps structured system rows renderable as system rows", () => {
    const promptRow: ChatRow = {
      ...row("prompt-row", [{ type: "prompt", prompt_id: "permission-1", data: { command: "pytest" } }], "system"),
      kind: "prompt",
      status: "open",
    };
    const message = toThreadMessageLike(promptRow);

    expect(message.role).toBe("assistant");
    expect(message.content).toEqual([
      { type: "data-prompt", id: "permission-1", data: { command: "pytest" } },
    ]);
    expect(message.metadata?.custom).toMatchObject({ chatRowRole: "system", chatRowKind: "prompt" });
  });

  it("gives empty args when the tool-call args are not a plain object", () => {
    const message = toThreadMessageLike(
      row("row-4", [{ type: "tool-call", tool_call_id: "call-2", tool_name: "list", args: ["a"] }]),
    );
    expect(message.content).toEqual([
      { type: "tool-call", toolCallId: "call-2", toolName: "list", args: {}, argsText: '["a"]' },
    ]);
  });

  it("maps an image part to { type: 'image', image: url }", () => {
    const message = toThreadMessageLike(row("row-5", [{ type: "image", url: "data:image/png;base64,AAAA" }]));
    expect(message.content).toEqual([{ type: "image", image: "data:image/png;base64,AAAA" }]);
  });

  it("maps a prompt part to a data-prompt part carrying id and data", () => {
    const data = { question: "Proceed?", options: ["yes", "no"] };
    const message = toThreadMessageLike(row("row-6", [{ type: "prompt", prompt_id: "p-1", data }]));
    expect(message.content).toEqual([{ type: "data-prompt", id: "p-1", data }]);
  });
});

describe("toRepository", () => {
  it("tracks running assistant rows through turn end without marking completed rows active", () => {
    const active = { ...row("active", [
      { type: "reasoning" as const, text: "thinking" },
      { type: "tool-call" as const, tool_call_id: "c", tool_name: "test", args: {} },
    ]), turn_id: "t1", status: "running" };
    const completed = { ...row("done", []), turn_id: "t1", status: "complete" };
    const running = { t1: { turn_id: "t1", state: "running" as const, input_ids: [] } };
    const ended = { t1: { ...running.t1, state: "ended" as const } };
    expect(toRepository([completed, active], running).messages.map((m) => m.message.status.type)).toEqual(["complete", "running"]);
    expect(toRepository([active], ended).messages[0].message.status.type).not.toBe("running");
  });

  it("replays a contract streaming row through its turn lifecycle", () => {
    const [snapshot, streaming] = fixture.events;
    const turn = { turn_id: "t1", state: "running", input_ids: [] };
    let state = initialStreamState;
    for (const event of [snapshot, streaming, { seq: 3, conversation_uid: "conv-1", generation: "fixture-daemon-1", type: "turn.state", data: turn }]) {
      const result = applyChatStreamEvent(state, event);
      expect(result.outcome.kind).toBe("applied");
      state = result.state;
    }
    expect(toRepository(state.rows, state.turns).messages[0].message.status.type).toBe("running");
    const ended = applyChatStreamEvent(state, {
      seq: 4, conversation_uid: "conv-1", generation: "fixture-daemon-1", type: "turn.state", data: { ...turn, state: "ended" },
    });
    expect(ended.outcome.kind).toBe("applied");
    expect(toRepository(ended.state.rows, ended.state.turns).messages[0].message.status.type).toBe("complete");
  });

  it("returns one message per row, in row order", () => {
    const repository = toRepository([row("a", []), row("b", []), row("c", [])]);
    expect(repository.messages.map((item) => item.message.id)).toEqual(["a", "b", "c"]);
  });
});

describe("messageText", () => {
  it("joins the text parts of an AppendMessage and ignores other parts", () => {
    const message = appendOf([
      { type: "text", text: "Look at " },
      { type: "image", image: "data:image/png;base64,AAAA" },
      { type: "text", text: "this" },
    ]);
    expect(messageText(message)).toBe("Look at this");
  });
});

describe("buildAdapter", () => {
  it("onNew sends one input whose id is web- plus 24 hex digits, with the composed text untrimmed", async () => {
    const { store, adapter } = adapterFor({ running: false, hasEarlier: false });
    await adapter.onNew(appendOf([{ type: "text", text: "  first " }, { type: "text", text: "second  " }]));
    expect(store.send).toHaveBeenCalledTimes(1);
    expect(store.send).toHaveBeenCalledWith({
      id: expect.stringMatching(/^web-[0-9a-f]{24}$/),
      text: "  first second  ",
    });
  });

  it("onNew sends nothing for whitespace-only text", async () => {
    const { store, adapter } = adapterFor({ running: false, hasEarlier: false });
    await adapter.onNew(appendOf([{ type: "text", text: " \n\t " }]));
    expect(store.send).not.toHaveBeenCalled();
  });

  it("onCancel calls store.stop and onLoadEarlier calls store.loadOlder", async () => {
    const { store, adapter } = adapterFor({ running: true, hasEarlier: true });
    await adapter.onCancel?.();
    await adapter.onLoadEarlier?.();
    expect(store.stop).toHaveBeenCalledTimes(1);
    expect(store.loadOlder).toHaveBeenCalledTimes(1);
  });

  it("passes isRunning and hasEarlier from the flags it is given", () => {
    const { adapter } = adapterFor({ running: true, hasEarlier: false });
    expect(adapter.isRunning).toBe(true);
    expect(adapter.hasEarlier).toBe(false);
  });
});

describe("hub-note parts", () => {
  it("map to a data-hub-note part, so a hub note row never carries an undefined part", () => {
    const part = { type: "hub-note" as const, from: "lapis", to: "", text: "build is green", observed: true, kind: "broadcast" };
    const like = toThreadMessageLike({
      id: "hub-1",
      seq: 3,
      kind: "note",
      role: "system",
      turn_id: null,
      status: "done",
      parts: [part],
      created_at: "2026-11-25T00:00:00Z",
    });
    expect(like.content).toEqual([{ type: "data-hub-note", data: part }]);
  });
});
