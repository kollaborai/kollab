"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE,
  EngineApi,
  type ChatStreamContentChunk,
} from "@/api";
import {
  isChatStreamPartPreviewMarker,
  isChatStreamPartsPreviewMarker,
  isChatStreamContentPreviewMarker,
  type ChatStreamPartPreviewMarker,
  type ChatStreamPartsPreviewMarker,
  type ChatStreamContentPreviewMarker,
} from "./chat-stream";
import { Button } from "@/components/ui/button";
import { LoaderIcon } from "lucide-react";

export type { ChatStreamContentPreviewMarker } from "./chat-stream";

const chatStreamContentApi = new EngineApi();
const CHAT_STREAM_CONTENT_CACHE_LIMIT = 5;
const chatStreamContentCache = new Map<string, string>();
const pendingChatStreamContents = new Map<string, Promise<string>>();

export function chatStreamContentIdentity(marker: ChatStreamContentPreviewMarker): string {
  return [
    marker.session_id,
    marker.row_id,
    marker.part_index,
    marker.field,
    marker.output_revision,
  ].join("\u0000");
}

/** Keep later row.append text after the immutable content snapshot is hydrated. */
export function chatStreamStreamingText(
  marker: ChatStreamContentPreviewMarker,
  streamedText: string,
  hydratedContent: string | undefined,
): string {
  const streamedTail = streamedText.startsWith(marker.preview)
    ? streamedText.slice(marker.preview.length)
    : "";
  return `${hydratedContent ?? marker.preview}${streamedTail}`;
}

/** Whether assistant-ui's Copy action would omit recoverable stream content. */
export function messageHasRecoverableChatStreamPreview(
  custom: unknown,
  parts: unknown,
): boolean {
  if (chatStreamPartsPreviewFromCustom(custom)) return true;
  if (!Array.isArray(parts)) return false;
  return parts.some((value) => {
    if (!isRecord(value)) return false;
    const providerMarker = chatStreamContentPreviewFromMetadata(
      value.providerMetadata,
      "text",
    ) ?? chatStreamContentPreviewFromMetadata(value.providerMetadata, "reasoning");
    const argsMarker = chatStreamArgsPreviewFromMetadata(value.providerMetadata);
    return Boolean(
      providerMarker ||
      argsMarker ||
      chatStreamPartPreviewFromMetadata(value.providerMetadata) ||
      isRecoverableMarker(value.chat_stream_content_preview) ||
      isRecoverableMarker(value.chat_stream_part_preview) ||
      isRecoverableMarker(value.chat_stream_args_preview) ||
      isRecoverableMarker(value.result),
    );
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRecoverableMarker(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return value.kind === "chat-stream-content-preview" ||
    value.kind === "chat-stream-part-preview" ||
    value.kind === "chat-stream-parts-preview" ||
    value.kind === "chat-stream-tool-output-preview";
}

function characterCount(value: string): number {
  return Array.from(value).length;
}

function validChunk(chunk: ChatStreamContentChunk): boolean {
  return Boolean(
    chunk &&
    typeof chunk.row_id === "string" &&
    Number.isSafeInteger(chunk.part_index) &&
    (chunk.field === "text" || chunk.field === "reasoning" || chunk.field === "args" || chunk.field === "part") &&
    typeof chunk.output_revision === "string" &&
    Number.isSafeInteger(chunk.offset) &&
    Number.isSafeInteger(chunk.next_offset) &&
    Number.isSafeInteger(chunk.total_chars) &&
    (chunk.output_type === "text" || chunk.output_type === "json") &&
    typeof chunk.content === "string" &&
    typeof chunk.done === "boolean",
  );
}

/** Fetch and join bounded slices for one exact stream part and content revision. */
export async function fetchChatStreamContent(
  marker: ChatStreamContentPreviewMarker,
  api: Pick<EngineApi, "getChatStreamContent"> = chatStreamContentApi,
): Promise<string> {
  if (!isChatStreamContentPreviewMarker(marker)) {
    throw new Error("Chat stream content preview has an invalid identity.");
  }
  const key = chatStreamContentIdentity(marker);
  if (chatStreamContentCache.has(key)) return chatStreamContentCache.get(key)!;
  const pending = pendingChatStreamContents.get(key);
  if (pending) return pending;

  const load = (async () => {
    try {
      let content = "";
      let offset = 0;
      for (;;) {
        const chunk = await api.getChatStreamContent(
          marker.session_id,
          marker.row_id,
          marker.part_index,
          marker.field,
          marker.output_revision,
          { offset, limit: CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE },
        );
        if (
          !validChunk(chunk) ||
          chunk.row_id !== marker.row_id ||
          chunk.part_index !== marker.part_index ||
          chunk.field !== marker.field ||
          chunk.output_revision !== marker.output_revision ||
          chunk.offset !== offset ||
          chunk.total_chars !== marker.output_chars ||
          chunk.output_type !== marker.output_type
        ) {
          throw new Error("Chat stream content chunk does not match the requested part.");
        }

        const chunkChars = characterCount(chunk.content);
        const nextOffset = offset + chunkChars;
        const isEmptyFinalChunk =
          marker.output_chars === 0 && offset === 0 && chunk.done && chunk.content === "";
        if (
          chunk.next_offset !== nextOffset ||
          (!isEmptyFinalChunk && chunk.next_offset <= offset) ||
          chunkChars > CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE ||
          chunk.next_offset > marker.output_chars ||
          chunk.done !== (chunk.next_offset === marker.output_chars)
        ) {
          throw new Error("Chat stream content chunk has an invalid offset range.");
        }

        content += chunk.content;
        offset = chunk.next_offset;
        if (chunk.done) break;
      }
      if (characterCount(content) !== marker.output_chars) {
        throw new Error("Chat stream content ended before all characters were received.");
      }
      if (marker.field === "args" || marker.field === "part") {
        try {
          JSON.parse(content);
        } catch {
          throw new Error(marker.field === "args"
            ? "Recovered tool arguments are not valid JSON."
            : "Recovered chat part is not valid JSON.");
        }
      }
      chatStreamContentCache.set(key, content);
      if (chatStreamContentCache.size > CHAT_STREAM_CONTENT_CACHE_LIMIT) {
        chatStreamContentCache.delete(chatStreamContentCache.keys().next().value as string);
      }
      return content;
    } finally {
      pendingChatStreamContents.delete(key);
    }
  })();
  pendingChatStreamContents.set(key, load);
  return load;
}

/** Read the stream marker carried as assistant-ui provider metadata. */
export function chatStreamContentPreviewFromMetadata(
  providerMetadata: unknown,
  field: "text" | "reasoning",
): ChatStreamContentPreviewMarker | undefined {
  if (!providerMetadata || typeof providerMetadata !== "object" || Array.isArray(providerMetadata)) {
    return undefined;
  }
  const namespace = (providerMetadata as Record<string, unknown>).kollab;
  if (!namespace || typeof namespace !== "object" || Array.isArray(namespace)) return undefined;
  const marker = (namespace as Record<string, unknown>).chat_stream_content_preview;
  return isChatStreamContentPreviewMarker(marker) && marker.field === field ? marker : undefined;
}

/** Read the dedicated tool-argument preview marker without inspecting tool args. */
export function chatStreamArgsPreviewFromMetadata(
  providerMetadata: unknown,
): ChatStreamContentPreviewMarker | undefined {
  if (!providerMetadata || typeof providerMetadata !== "object" || Array.isArray(providerMetadata)) {
    return undefined;
  }
  const namespace = (providerMetadata as Record<string, unknown>).kollab;
  if (!namespace || typeof namespace !== "object" || Array.isArray(namespace)) return undefined;
  const marker = (namespace as Record<string, unknown>).chat_stream_args_preview;
  return isChatStreamContentPreviewMarker(marker) && marker.field === "args" ? marker : undefined;
}

export function chatStreamPartPreviewFromMetadata(providerMetadata: unknown): ChatStreamPartPreviewMarker | undefined {
  if (!providerMetadata || typeof providerMetadata !== "object" || Array.isArray(providerMetadata)) return undefined;
  const namespace = (providerMetadata as Record<string, unknown>).kollab;
  if (!namespace || typeof namespace !== "object" || Array.isArray(namespace)) return undefined;
  const marker = (namespace as Record<string, unknown>).chat_stream_part_preview;
  return isChatStreamPartPreviewMarker(marker) ? marker : undefined;
}

export function chatStreamPartsPreviewFromCustom(custom: unknown): ChatStreamPartsPreviewMarker | undefined {
  if (!custom || typeof custom !== "object" || Array.isArray(custom)) return undefined;
  const marker = (custom as Record<string, unknown>).chatStreamPartsPreview;
  return isChatStreamPartsPreviewMarker(marker) ? marker : undefined;
}

function errorText(error: unknown): string {
  if (
    error && typeof error === "object" &&
    "status" in error && (error as { status?: unknown }).status === 409
  ) {
    return "The content changed while loading. Wait for the updated row, then retry.";
  }
  return "Full content could not be loaded. Check the connection and retry.";
}

/** Automatically hydrate clipped text, or render a lazy preview inside expanded tool details. */
export function ChatStreamContentPreview({
  marker,
  autoLoad = true,
  loadLabel = "Load full content",
  children,
}: {
  marker: ChatStreamContentPreviewMarker;
  autoLoad?: boolean;
  loadLabel?: string;
  children: (content: string) => ReactNode;
}) {
  const markerRef = useRef(marker);
  markerRef.current = marker;
  const cacheKey = chatStreamContentIdentity(marker);
  const [content, setContent] = useState(() => chatStreamContentCache.get(cacheKey));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);

  const load = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      setContent(await fetchChatStreamContent(markerRef.current));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [cacheKey]);

  useEffect(() => {
    if (autoLoad && content === undefined && error === null) void load();
  }, [autoLoad, content, error, load]);

  return (
    <div data-slot="chat-stream-content-preview" className="space-y-1">
      {children(content ?? marker.preview)}
      {!autoLoad && content === undefined && error === null && !loading ? (
        <Button type="button" size="sm" variant="outline" onClick={() => void load()}>
          {loadLabel}
        </Button>
      ) : null}
      {loading ? (
        <p role="status" className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <LoaderIcon className="size-3.5 animate-spin" aria-hidden="true" />
          Loading complete content…
        </p>
      ) : null}
      {error ? (
        <div className="flex flex-wrap items-center gap-2">
          <span role="alert" className="text-destructive text-xs">{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={() => void load()}>
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Render the pinned full snapshot plus row.append deltas received after it. */
export function ChatStreamStreamingContent({
  marker,
  text,
  children,
}: {
  marker: ChatStreamContentPreviewMarker;
  text: string;
  children: (content: string) => ReactNode;
}) {
  return (
    <ChatStreamContentPreview key={chatStreamContentIdentity(marker)} marker={marker}>
      {(content) => children(chatStreamStreamingText(marker, text, content))}
    </ChatStreamContentPreview>
  );
}
