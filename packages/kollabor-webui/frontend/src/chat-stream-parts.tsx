"use client";

import { useCallback, useEffect, useRef, useState, type FC } from "react";
import {
  CHAT_STREAM_PART_PAGE_SIZE,
  EngineApi,
  type ChatStreamPartsPage,
} from "@/api";
import { Button } from "@/components/ui/button";
import { useAuthenticatedImage } from "@/components/media/media-view";
import { ToolFallbackResult } from "@/components/assistant-ui/tool-fallback";
import { PermissionPromptData } from "@/components/PermissionTool";
import {
  ChatStreamContentPreview,
  chatStreamContentIdentity,
  fetchChatStreamContent,
  type ChatStreamContentPreviewMarker,
} from "./chat-stream-content";
import {
  isChatStreamContentPreviewMarker,
  isChatStreamPartPreviewMarker,
  isChatStreamPartsPreviewMarker,
  type ChatPart,
  type ChatStreamPartPreviewMarker,
  type ChatStreamPartsPreviewMarker,
} from "./chat-stream";

const chatStreamPartsApi = new EngineApi();
const RecoveredPermissionPrompt = PermissionPromptData as FC<{ id?: string; data: unknown }>;

export function chatStreamPartsIdentity(marker: ChatStreamPartsPreviewMarker): string {
  return [marker.session_id, marker.row_id, marker.output_revision].join("\u0000");
}

function validPage(
  page: ChatStreamPartsPage,
  marker: ChatStreamPartsPreviewMarker,
  offset: number,
): boolean {
  if (
    !page ||
    page.row_id !== marker.row_id ||
    page.output_revision !== marker.output_revision ||
    page.offset !== offset ||
    page.total_parts !== marker.part_count ||
    !Array.isArray(page.parts) ||
    !Number.isSafeInteger(page.next_offset) ||
    page.next_offset !== offset + page.parts.length ||
    page.next_offset > page.total_parts ||
    typeof page.done !== "boolean" ||
    page.done !== (page.next_offset === page.total_parts) ||
    (!page.done && page.parts.length === 0)
  ) {
    return false;
  }
  return page.parts.every((part, index) => validPartIdentity(part, marker, offset + index));
}

function validPartIdentity(
  part: unknown,
  marker: ChatStreamPartsPreviewMarker,
  expectedIndex: number,
): boolean {
  if (!isRecord(part)) return false;
  const partPreview = part.chat_stream_part_preview;
  if (partPreview !== undefined && partPreview !== null) {
    if (
      part.type !== "text" ||
      !isChatStreamPartPreviewMarker(partPreview) ||
      partPreview.session_id !== marker.session_id ||
      partPreview.row_id !== marker.row_id ||
      partPreview.part_index !== expectedIndex
    ) return false;
  }
  const contentPreview = part.chat_stream_content_preview;
  if (contentPreview !== undefined && contentPreview !== null) {
    if (
      !isChatStreamContentPreviewMarker(contentPreview) ||
      contentPreview.session_id !== marker.session_id ||
      contentPreview.row_id !== marker.row_id ||
      contentPreview.part_index !== expectedIndex
    ) return false;
  }
  if (part.type === "tool-call" && isRecord(part.args) && part.args.kind === "chat-stream-content-preview") {
    if (
      !isChatStreamContentPreviewMarker(part.args) ||
      part.args.field !== "args" ||
      part.args.session_id !== marker.session_id ||
      part.args.row_id !== marker.row_id ||
      part.args.part_index !== expectedIndex
    ) return false;
  }
  if (
    part.type === "tool-call" &&
    isRecord(part.result) &&
    part.result.kind === "chat-stream-tool-output-preview"
  ) {
    const result = part.result;
    if (
      result.session_id !== marker.session_id ||
      result.row_id !== marker.row_id ||
      result.tool_call_id !== part.tool_call_id ||
      typeof result.output_revision !== "string" ||
      !/^[0-9a-f]{64}$/.test(result.output_revision) ||
      !Number.isSafeInteger(result.output_bytes) ||
      (result.output_bytes as number) < 0 ||
      !Number.isSafeInteger(result.output_chars) ||
      (result.output_chars as number) < 0 ||
      (result.output_type !== "text" && result.output_type !== "json") ||
      typeof result.preview !== "string"
    ) return false;
  }
  return true;
}

export async function fetchChatStreamPartsPage(
  marker: ChatStreamPartsPreviewMarker,
  offset: number,
  api: Pick<EngineApi, "getChatStreamParts"> = chatStreamPartsApi,
): Promise<ChatStreamPartsPage> {
  if (!isChatStreamPartsPreviewMarker(marker)) {
    throw new Error("Chat stream parts preview has an invalid identity.");
  }
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > marker.part_count) {
    throw new RangeError("Chat stream parts offset is outside the row.");
  }
  const page = await api.getChatStreamParts(marker.session_id, marker.row_id, marker.output_revision, {
    offset,
    limit: CHAT_STREAM_PART_PAGE_SIZE,
  });
  if (!validPage(page, marker, offset)) {
    throw new Error("Chat stream parts page does not match the requested row.");
  }
  return page;
}

export async function fetchChatStreamPart(
  marker: ChatStreamPartPreviewMarker,
  api?: Parameters<typeof fetchChatStreamContent>[1],
): Promise<unknown> {
  if (!isChatStreamPartPreviewMarker(marker)) {
    throw new Error("Chat stream part preview has an invalid identity.");
  }
  const contentMarker: ChatStreamContentPreviewMarker = {
    kind: "chat-stream-content-preview",
    session_id: marker.session_id,
    row_id: marker.row_id,
    part_index: marker.part_index,
    field: "part",
    output_bytes: marker.output_bytes,
    output_chars: marker.output_chars,
    output_type: "json",
    output_revision: marker.output_revision,
    preview: marker.preview,
  };
  const serialized = await fetchChatStreamContent(contentMarker, api);
  return JSON.parse(serialized) as unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function partJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function RecoveredImage({ url }: { url: string }) {
  const protectedPath = url.startsWith("/") ? url : null;
  const authenticated = useAuthenticatedImage(protectedPath);
  const src = protectedPath ? authenticated : url;
  return <img src={typeof src === "string" ? src : undefined} alt="Recovered chat image" className="my-1 max-h-80 max-w-full rounded-md object-contain" />;
}

/** Read-only rendering for an exact structured part recovered from the stream row. */
export function RecoveredChatPart({ part }: { part: unknown }) {
  if (!isRecord(part)) return <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part)}</pre>;
  const textPreview = part.chat_stream_content_preview;
  if (
    (part.type === "text" || part.type === "reasoning") &&
    isChatStreamContentPreviewMarker(textPreview) &&
    textPreview.field === part.type
  ) {
    return (
      <ChatStreamContentPreview key={chatStreamContentIdentity(textPreview)} marker={textPreview}>
        {(content) => <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap text-xs">{content}</pre>}
      </ChatStreamContentPreview>
    );
  }
  if (part.type === "tool-call") {
    const argsPreview = isRecord(part.args) && isChatStreamContentPreviewMarker(part.args) && part.args.field === "args"
      ? part.args
      : undefined;
    return (
      <details>
        <summary className="cursor-pointer text-xs">Recovered tool {String(part.tool_name ?? "call")}</summary>
        <div className="space-y-2">
          <p className="text-muted-foreground text-xs">Arguments</p>
          {argsPreview ? (
            <ChatStreamContentPreview
              key={chatStreamContentIdentity(argsPreview)}
              marker={argsPreview}
              autoLoad={false}
              loadLabel="Load full arguments"
            >
              {(content) => <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{content}</pre>}
            </ChatStreamContentPreview>
          ) : <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part.args)}</pre>}
          {part.result !== undefined ? (
            <div>
              <p className="text-muted-foreground text-xs">Result</p>
              <ToolFallbackResult result={part.result} isError={part.is_error === true} />
            </div>
          ) : null}
        </div>
      </details>
    );
  }
  switch (part.type) {
    case "text":
    case "reasoning":
      return typeof part.text === "string"
        ? <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap text-xs">{part.text}</pre>
        : <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part)}</pre>;
    case "image":
      return typeof part.url === "string"
        ? <RecoveredImage url={part.url} />
        : <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part)}</pre>;
    case "hub-note":
      return (
        <div className="space-y-1 text-xs">
          {typeof part.text === "string" ? <p className="whitespace-pre-wrap">{part.text}</p> : null}
          <details>
            <summary>Hub message fields</summary>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap">{partJson(part)}</pre>
          </details>
        </div>
      );
    case "prompt":
      return (
        <div className="space-y-2">
          <RecoveredPermissionPrompt id={typeof part.prompt_id === "string" ? part.prompt_id : undefined} data={part.data} />
          <details>
            <summary className="cursor-pointer text-xs">Prompt data</summary>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part)}</pre>
          </details>
        </div>
      );
    default:
      return <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{partJson(part)}</pre>;
  }
}

/** A valid text placeholder that recovers its original structured part on demand. */
export function ChatStreamPartPreview({ marker }: { marker: ChatStreamPartPreviewMarker }) {
  const contentMarker: ChatStreamContentPreviewMarker = {
    kind: "chat-stream-content-preview",
    session_id: marker.session_id,
    row_id: marker.row_id,
    part_index: marker.part_index,
    field: "part",
    output_bytes: marker.output_bytes,
    output_chars: marker.output_chars,
    output_type: "json",
    output_revision: marker.output_revision,
    preview: marker.preview,
  };
  return (
    <ChatStreamContentPreview
      key={chatStreamContentIdentity(contentMarker)}
      marker={contentMarker}
      autoLoad={false}
      loadLabel="Expand full part"
    >
      {(serialized) => {
        try {
          return <RecoveredChatPart part={JSON.parse(serialized) as ChatPart} />;
        } catch {
          return <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{serialized}</pre>;
        }
      }}
    </ChatStreamContentPreview>
  );
}

/** Paged viewer for a row whose ordinary projected parts exceed the event byte budget. */
export function ChatStreamPartsPreview({ marker }: { marker: ChatStreamPartsPreviewMarker }) {
  const markerRef = useRef(marker);
  markerRef.current = marker;
  const identity = chatStreamPartsIdentity(marker);
  const [parts, setParts] = useState<unknown[]>([]);
  const [offset, setOffset] = useState(0);
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);

  useEffect(() => {
    setParts([]);
    setOffset(0);
    setStarted(false);
    setDone(false);
    setLoading(false);
    setError(null);
    pending.current = null;
  }, [identity]);

  const load = useCallback(async () => {
    if (pending.current === identity || done) return;
    pending.current = identity;
    const requestIdentity = identity;
    const requestOffset = offset;
    setStarted(true);
    setLoading(true);
    setError(null);
    try {
      const page = await fetchChatStreamPartsPage(markerRef.current, requestOffset);
      if (chatStreamPartsIdentity(markerRef.current) !== requestIdentity) return;
      setParts((current) => [...current, ...page.parts]);
      setOffset(page.next_offset);
      setDone(page.done);
    } catch (reason) {
      if (chatStreamPartsIdentity(markerRef.current) === requestIdentity) {
        setError(partsErrorText(reason));
      }
    } finally {
      if (pending.current === requestIdentity) pending.current = null;
      if (chatStreamPartsIdentity(markerRef.current) === requestIdentity) setLoading(false);
    }
  }, [done, identity, offset]);

  return (
    <section data-slot="chat-stream-parts-preview" className="space-y-2 rounded-md border border-border/60 p-2 text-xs">
      {!started ? <p>This row has many parts. Load them a page at a time.</p> : null}
      {parts.map((part, index) => (
        <div key={`${identity}:${index}`} data-chat-recovered-part={index}>
          {isRecord(part) && isChatStreamPartPreviewMarker(part.chat_stream_part_preview)
            ? <ChatStreamPartPreview marker={part.chat_stream_part_preview} />
            : <RecoveredChatPart part={part} />}
        </div>
      ))}
      {loading ? <p role="status">Loading chat parts…</p> : null}
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
      {!done ? (
        <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => void load()}>
          {error ? "Retry" : started ? "Load more parts" : "Load full row"}
        </Button>
      ) : null}
      {done ? <p role="status">All {parts.length} chat parts loaded.</p> : null}
    </section>
  );
}

function partsErrorText(error?: unknown): string {
  if (error && typeof error === "object" && "status" in error && (error as { status?: unknown }).status === 409) {
    return "The row changed while loading. Wait for the updated row, then retry.";
  }
  return "Full row content could not be loaded. Check the connection and retry.";
}
