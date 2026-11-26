// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getChatStreamContent: vi.fn(),
  getChatStreamParts: vi.fn(),
}));

vi.mock("@/api", () => ({
  EngineApi: class {
    getChatStreamContent = mocks.getChatStreamContent;
    getChatStreamParts = mocks.getChatStreamParts;
  },
  CHAT_STREAM_PART_PAGE_SIZE: 32,
  CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE: 32768,
}));

vi.mock("@/components/media/media-view", () => ({
  useAuthenticatedImage: (path?: string | null) => path ? `/authenticated${path}` : null,
}));

import {
  ChatStreamPartPreview,
  ChatStreamPartsPreview,
  RecoveredChatPart,
  fetchChatStreamPartsPage,
} from "./chat-stream-parts";
import { canExportChatStreamMarkdown } from "./chat-stream-content";
import type { ChatStreamPartPreviewMarker, ChatStreamPartsPreviewMarker } from "./chat-stream";

const revision = "a".repeat(64);
function partsMarker(rowId = "row-paged", outputRevision = revision): ChatStreamPartsPreviewMarker {
  return {
    kind: "chat-stream-parts-preview",
    session_id: "session-1",
    row_id: rowId,
    part_count: 3,
    output_bytes: 400_000,
    output_chars: 360_000,
    output_type: "json",
    output_revision: outputRevision,
  };
}

function partMarker(rowId = "row-part", partIndex = 4): ChatStreamPartPreviewMarker {
  return {
    kind: "chat-stream-part-preview",
    session_id: "session-1",
    row_id: rowId,
    part_index: partIndex,
    output_bytes: 30_000,
    output_chars: 25_000,
    output_type: "json",
    output_revision: "b".repeat(64),
    preview: "[Large part. Expand to load full content.]",
  };
}

function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  return { host, root, render: (next: ReactNode) => act(async () => root.render(next)), unmount: () => act(async () => root.unmount()) };
}

function contentChunk(rowId: string, partIndex: number, revisionValue: string, content: string) {
  const totalChars = Array.from(content).length;
  return {
    row_id: rowId,
    part_index: partIndex,
    field: "part",
    output_revision: revisionValue,
    offset: 0,
    next_offset: totalChars,
    total_chars: totalChars,
    output_type: "json",
    content,
    done: true,
  };
}

describe("chat stream part recovery", () => {
  beforeEach(() => {
    mocks.getChatStreamContent.mockReset();
    mocks.getChatStreamParts.mockReset();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("renders a recovered structured part through the production preview path", async () => {
    const part = { type: "hub-note", from: "peer", to: "", text: "recovered hub note", observed: true };
    const serialized = JSON.stringify(part);
    const marker = {
      ...partMarker(),
      output_bytes: new TextEncoder().encode(serialized).length,
      output_chars: Array.from(serialized).length,
    };
    mocks.getChatStreamContent.mockResolvedValueOnce(
      contentChunk(marker.row_id, marker.part_index, marker.output_revision, serialized),
    );

    const ui = mount();
    try {
      await ui.render(<ChatStreamPartPreview marker={marker} />);
      await act(async () => {
        ui.host.querySelector("button")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(mocks.getChatStreamContent).toHaveBeenCalledWith(
        "session-1", "row-part", 4, "part", marker.output_revision, { offset: 0, limit: 32768 },
      );
      expect(ui.host.textContent).toContain("recovered hub note");
      expect(ui.host.textContent).toContain("Hub message fields");
    } finally {
      await ui.unmount();
    }
  });

  it("maps a verified recovered queue image to the authenticated attachment route", async () => {
    const part = {
      type: "image",
      url: "kollab-queue-image:?conversation_uid=conversation%2F1&request_id=request%2F2&ref=attachment%2F3",
    };
    const serialized = JSON.stringify(part);
    const marker = {
      ...partMarker("row-queue-image", 1),
      output_bytes: new TextEncoder().encode(serialized).length,
      output_chars: Array.from(serialized).length,
    };
    mocks.getChatStreamContent.mockResolvedValueOnce(
      contentChunk(marker.row_id, marker.part_index, marker.output_revision, serialized),
    );

    const ui = mount();
    try {
      await ui.render(<ChatStreamPartPreview marker={marker} />);
      await act(async () => {
        ui.host.querySelector("button")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(ui.host.querySelector("img")?.getAttribute("src")).toBe(
        "/authenticated/sessions/session-1/web-message-queue/items/request%2F2/attachments/attachment%2F3?conversation_uid=conversation%2F1",
      );
    } finally {
      await ui.unmount();
    }
  });

  it("rejects a page from a different row revision before rendering it", async () => {
    const marker = partsMarker();
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: "c".repeat(64),
      parts: [{ type: "text", text: "stale" }],
      done: false,
    });

    await expect(fetchChatStreamPartsPage(marker, 0)).rejects.toThrow("does not match");
  });

  it("rejects nested part previews that point at a different row or part index", async () => {
    const marker = partsMarker("row-nested");
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [{
        type: "text",
        text: "[Large part]",
        chat_stream_part_preview: { ...partMarker("another-row", 0), session_id: marker.session_id },
      }],
      done: false,
    });

    await expect(fetchChatStreamPartsPage(marker, 0)).rejects.toThrow("does not match");
  });

  it("accepts a recoverable oversized permission prompt part in a paged row", async () => {
    const marker = partsMarker("row-prompt-preview");
    const promptPreview = {
      ...partMarker(marker.row_id, 0),
      session_id: marker.session_id,
    };
    const prompt = {
      type: "prompt",
      prompt_id: "permission-1",
      data: { tool_id: "tool-1", request_id: "request-1" },
      chat_stream_part_preview: promptPreview,
    };
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [prompt],
      done: false,
    });

    await expect(fetchChatStreamPartsPage(marker, 0)).resolves.toMatchObject({ parts: [prompt] });
  });

  it("rejects tool result previews whose top-level identity does not match the paged tool call", async () => {
    const marker = partsMarker("row-tool-result");
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [{
        type: "tool-call",
        tool_call_id: "call-expected",
        tool_name: "lookup",
        args: {},
        chat_stream_result_preview: {
          kind: "chat-stream-tool-output-preview",
          session_id: marker.session_id,
          row_id: marker.row_id,
          tool_call_id: "call-from-another-part",
          output_bytes: 80_000,
          output_chars: 80_000,
          output_type: "text",
          output_revision: "d".repeat(64),
          preview: "partial output",
        },
      }],
      done: false,
    });

    await expect(fetchChatStreamPartsPage(marker, 0)).rejects.toThrow("does not match");
  });

  it("accepts a top-level tool result preview while leaving the result absent", async () => {
    const marker = partsMarker("row-top-level-result");
    const outputPreview = {
      kind: "chat-stream-tool-output-preview",
      session_id: marker.session_id,
      row_id: marker.row_id,
      tool_call_id: "call-result",
      output_bytes: 80_000,
      output_chars: 80_000,
      output_type: "text",
      output_revision: "e".repeat(64),
      preview: "bounded result",
    };
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [{
        type: "tool-call", tool_call_id: "call-result", tool_name: "inspect", args: {},
        chat_stream_result_preview: outputPreview,
      }],
      done: false,
    });

    await expect(fetchChatStreamPartsPage(marker, 0)).resolves.toMatchObject({
      parts: [{ result: undefined, chat_stream_result_preview: outputPreview }],
    });
  });

  it("keeps marker-shaped tool args opaque and validates the dedicated args preview", async () => {
    const marker = partsMarker("row-args-marker");
    const opaqueArgs = {
      kind: "chat-stream-content-preview",
      field: "args",
      app_value: "ordinary user data",
    };
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [{
        type: "tool-call", tool_call_id: "call-opaque", tool_name: "inspect", args: opaqueArgs,
      }],
      done: false,
    });
    await expect(fetchChatStreamPartsPage(marker, 0)).resolves.toMatchObject({
      parts: [{ args: opaqueArgs }],
    });

    const argsPreview = {
      kind: "chat-stream-content-preview" as const,
      session_id: marker.session_id,
      row_id: marker.row_id,
      part_index: 0,
      field: "args" as const,
      output_bytes: 40_000,
      output_chars: 40_000,
      output_type: "json" as const,
      output_revision: "c".repeat(64),
      preview: '{"large":',
    };
    mocks.getChatStreamParts.mockResolvedValueOnce({
      row_id: marker.row_id,
      offset: 0,
      next_offset: 1,
      total_parts: marker.part_count,
      output_revision: marker.output_revision,
      parts: [{
        type: "tool-call", tool_call_id: "call-large", tool_name: "inspect", args: {},
        chat_stream_args_preview: argsPreview,
      }],
      done: false,
    });
    await expect(fetchChatStreamPartsPage(marker, 0)).resolves.toMatchObject({
      parts: [{ chat_stream_args_preview: argsPreview }],
    });
  });

  it("recovers tool args from the dedicated top-level args preview marker", async () => {
    const marker = {
      kind: "chat-stream-content-preview" as const,
      session_id: "session-1",
      row_id: "row-args-view",
      part_index: 0,
      field: "args" as const,
      output_bytes: 22,
      output_chars: 22,
      output_type: "json" as const,
      output_revision: "d".repeat(64),
      preview: '{"secret":"preview"}',
    };
    mocks.getChatStreamContent.mockResolvedValueOnce({
      row_id: marker.row_id,
      part_index: marker.part_index,
      field: "args",
      output_revision: marker.output_revision,
      offset: 0,
      next_offset: 22,
      total_chars: 22,
      output_type: "json",
      content: '{"secret":"recovered"}',
      done: true,
    });
    const ui = mount();
    try {
      await ui.render(<RecoveredChatPart part={{
        type: "tool-call", tool_call_id: "call-args", tool_name: "inspect", args: {},
        chat_stream_args_preview: marker,
      }} />);
      await act(async () => {
        ui.host.querySelector("button")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(mocks.getChatStreamContent).toHaveBeenCalledWith(
        marker.session_id, marker.row_id, marker.part_index, "args", marker.output_revision,
        { offset: 0, limit: 32768 },
      );
      expect(ui.host.textContent).toContain("recovered");
      expect(ui.host.textContent).not.toContain("preview");
    } finally {
      await ui.unmount();
    }
  });

  it("loads a row a page at a time and renders image, hub-note, and prompt parts", async () => {
    const marker = partsMarker("row-render");
    mocks.getChatStreamParts
      .mockResolvedValueOnce({
        row_id: marker.row_id,
        offset: 0,
        next_offset: 2,
        total_parts: 3,
        output_revision: marker.output_revision,
        parts: [
          { type: "image", url: "data:image/png;base64,AAAA" },
          { type: "hub-note", from: "peer", to: "", text: "paged hub content", observed: true },
        ],
        done: false,
      })
      .mockResolvedValueOnce({
        row_id: marker.row_id,
        offset: 2,
        next_offset: 3,
        total_parts: 3,
        output_revision: marker.output_revision,
        parts: [{ type: "prompt", prompt_id: "p1", data: { payload: "paged prompt content" } }],
        done: true,
      });

    const ui = mount();
    try {
      await ui.render(<ChatStreamPartsPreview marker={marker} />);
      expect(ui.host.textContent).toContain("Load full row");
      await act(async () => {
        ui.host.querySelector("button")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(mocks.getChatStreamParts).toHaveBeenNthCalledWith(
        1, "session-1", marker.row_id, marker.output_revision, { offset: 0, limit: 32 },
      );
      expect(ui.host.querySelector("img[alt='Recovered chat image']")).not.toBeNull();
      expect(ui.host.textContent).toContain("paged hub content");
      expect(ui.host.textContent).toContain("Load more parts");

      await act(async () => {
        const button = Array.from(ui.host.querySelectorAll("button")).find((candidate) => candidate.textContent === "Load more parts");
        button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(mocks.getChatStreamParts).toHaveBeenNthCalledWith(
        2, "session-1", marker.row_id, marker.output_revision, { offset: 2, limit: 32 },
      );
      expect(ui.host.textContent).toContain("paged prompt content");
      expect(ui.host.textContent).toContain("All 3 chat parts loaded.");
    } finally {
      await ui.unmount();
    }
  });
});

describe("chat stream markdown export", () => {
  it("hides export when message text is only a bounded preview", () => {
    const text = [{
      type: "text",
      text: "preview only",
      providerMetadata: {
        kollab: {
          chat_stream_content_preview: {
            kind: "chat-stream-content-preview",
            session_id: "session-1",
            row_id: "row-clipped",
            part_index: 0,
            field: "text",
            output_bytes: 200_000,
            output_chars: 200_000,
            output_type: "text",
            output_revision: "a".repeat(64),
            preview: "preview only",
          },
        },
      },
    }];
    expect(canExportChatStreamMarkdown({}, text)).toBe(false);
  });

  it("hides export for a whole-row paging placeholder", () => {
    expect(canExportChatStreamMarkdown({
      chatStreamPartsPreview: {
        kind: "chat-stream-parts-preview",
        session_id: "session-1",
        row_id: "row-paged",
        part_count: 400,
        output_bytes: 10_000_000,
        output_chars: 9_000_000,
        output_type: "json",
        output_revision: "a".repeat(64),
      },
    }, [{ type: "text", text: "[Large chat row. Expand to load all content.]" }])).toBe(false);
  });

  it("keeps markdown export available when all text is inline", () => {
    expect(canExportChatStreamMarkdown({}, [{ type: "text", text: "complete answer" }])).toBe(true);
  });
});
