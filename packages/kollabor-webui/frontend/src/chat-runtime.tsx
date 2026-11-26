// The assistant-ui runtime over the chat store (#1451). The store owns the rows,
// the running state and the paging; assistant-ui mirrors the rows through a
// message repository, deletes included. Spec: docs/specs/webui-chat-single-stream.md.
// Shapes: ./chat-stream.ts. The store: ./chat-store.ts.

import { useMemo, useSyncExternalStore } from "react";
import {
  ExportedMessageRepository,
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
  type ExternalStoreAdapter,
  type TextMessagePart,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { ReadonlyJSONObject } from "assistant-stream/utils";
import {
  CHAT_STREAM_PERMISSION_PROMPT_PREVIEW_KIND,
  newInputId,
  type ChatPart,
  type ChatRow,
  type ChatStore,
  type TurnInfo,
} from "./chat-stream";
import { imageAttachmentAdapter } from "./image-attachment";
import type { ChatSnapshot, LiveStore } from "./chat-store";

/** The client prefix for the input ids the web UI picks: newInputId(WEB_INPUT_PREFIX). */
export const WEB_INPUT_PREFIX = "web";

type LikePart = Exclude<ThreadMessageLike["content"], string>[number];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isTextPart = (part: { readonly type: string }): part is TextMessagePart => part.type === "text";

function toPart(part: ChatPart): LikePart {
  switch (part.type) {
    case "text":
      return {
        type: "text",
        text: part.text,
        ...(part.chat_stream_content_preview || part.chat_stream_part_preview
          ? { providerMetadata: { kollab: {
              ...(part.chat_stream_content_preview ? { chat_stream_content_preview: part.chat_stream_content_preview } : {}),
              ...(part.chat_stream_part_preview ? { chat_stream_part_preview: part.chat_stream_part_preview } : {}),
            } } }
          : {}),
      };
    case "reasoning":
      return {
        type: "reasoning",
        text: part.text,
        ...(part.chat_stream_content_preview
          ? { providerMetadata: { kollab: { chat_stream_content_preview: part.chat_stream_content_preview } } }
          : {}),
      };
    case "tool-call":
      return {
        type: "tool-call",
        toolCallId: part.tool_call_id,
        toolName: part.tool_name,
        args: isRecord(part.args) ? (part.args as ReadonlyJSONObject) : {},
        argsText: JSON.stringify(part.args ?? {}),
        result: part.result,
        isError: part.is_error ?? undefined,
          ...(part.chat_stream_args_preview
          || part.chat_stream_result_preview
          ? { providerMetadata: { kollab: {
              ...(part.chat_stream_args_preview ? { chat_stream_args_preview: part.chat_stream_args_preview } : {}),
              ...(part.chat_stream_result_preview ? { chat_stream_result_preview: part.chat_stream_result_preview } : {}),
            } } }
          : {}),
      };
    case "image":
      return { type: "image", image: part.url };
    case "prompt":
      return {
        type: "data-prompt",
        id: part.prompt_id,
        data: part.chat_stream_part_preview
          ? { kind: CHAT_STREAM_PERMISSION_PROMPT_PREVIEW_KIND, marker: part.chat_stream_part_preview }
          : part.data,
      };
    case "hub-note":
      // The thread draws hub notes from the store row; assistant-ui only needs a valid part.
      return { type: "data-hub-note", data: part };
  }
}

/** One stored row as the message assistant-ui renders. */
export function toThreadMessageLike(row: ChatRow, turns: Readonly<Record<string, TurnInfo>> = {}): ThreadMessageLike {
  // assistant-ui's ThreadSystemMessage only accepts a single text part. Keep
  // structured system rows renderable by storing them as assistant messages and
  // routing them back through the system renderer using chatRowRole metadata.
  const hasStructuredSystemParts = row.role === "system" && row.parts.some((part) => part.type !== "text");
  return {
    id: row.id,
    role: hasStructuredSystemParts ? "assistant" : row.role,
    createdAt: new Date(row.created_at),
    content: row.parts.map(toPart),
    metadata: { custom: {
      chatRowKind: row.kind,
      chatRowStatus: row.status,
      chatTurnId: row.turn_id,
      chatRowRole: row.role,
      ...(row.chat_stream_parts_preview ? { chatStreamPartsPreview: row.chat_stream_parts_preview } : {}),
    } },
    ...(row.role === "assistant" && row.status === "failed"
      ? { status: { type: "incomplete" as const, reason: "error" as const } }
      : {}),
    ...(row.role === "assistant" && (row.status === "running" || row.status === "streaming") && row.turn_id !== null && turns[row.turn_id]?.state === "running"
      ? { status: { type: "running" as const } } : {}),
  };
}

/** The repository for the rows, one message per row, in row order. */
export function toRepository(rows: readonly ChatRow[], turns: Readonly<Record<string, TurnInfo>> = {}): ExportedMessageRepository {
  return ExportedMessageRepository.fromArray(rows.map((row) => toThreadMessageLike(row, turns)));
}

/** The text of a composed message: its text parts joined in order, other parts ignored. */
export function messageText(message: AppendMessage): string {
  const parts: readonly { readonly type: string }[] = message.content;
  return parts
    .filter(isTextPart)
    .map((part) => part.text)
    .join("");
}

/**
 * The adapter for assistant-ui. A send goes out as one input under a web id, with
 * the text untrimmed; blank text sends nothing. Stop cancels the turn.
 *
 * The message type stays the default, ThreadMessage: the repository already holds
 * converted messages. ExternalStoreAdapter<ThreadMessageLike> would require a convertMessage.
 */
export function buildAdapter(
  store: Pick<ChatStore, "send" | "stop" | "loadOlder">,
  flags: { running: boolean; hasEarlier: boolean },
  repository: ExportedMessageRepository,
): ExternalStoreAdapter {
  return {
    messageRepository: repository,
    isRunning: flags.running,
    hasEarlier: flags.hasEarlier,
    onLoadEarlier: () => store.loadOlder(),
    onCancel: () => store.stop(),
    adapters: { attachments: imageAttachmentAdapter },
    onNew: async (message) => {
      const text = messageText(message);
      if (text.trim() === "") return;
      await store.send({ id: newInputId(WEB_INPUT_PREFIX), text });
    },
  };
}

/** The assistant-ui runtime for the store: the thread shows its rows and follows its running state. */
export function useChatRuntime(store: LiveStore): AssistantRuntime {
  const snap: ChatSnapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  // Turn transitions can change message status without changing the rows.
  const repository = useMemo(() => toRepository(snap.rows, snap.turns), [snap.rows, snap.turns]);
  return useExternalStoreRuntime(
    buildAdapter(store, { running: snap.running, hasEarlier: snap.hasEarlier }, repository),
  );
}
