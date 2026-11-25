// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ChatRow } from "../src/chat-stream";
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
