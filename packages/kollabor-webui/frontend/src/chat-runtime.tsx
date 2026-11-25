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
import { newInputId, type ChatPart, type ChatRow, type ChatStore } from "./chat-stream";
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
      return { type: "text", text: part.text };
    case "reasoning":
      return { type: "reasoning", text: part.text };
    case "tool-call":
      return {
        type: "tool-call",
        toolCallId: part.tool_call_id,
        toolName: part.tool_name,
        args: isRecord(part.args) ? (part.args as ReadonlyJSONObject) : {},
        argsText: JSON.stringify(part.args ?? {}),
        result: part.result,
        isError: part.is_error,
      };
    case "image":
      return { type: "image", image: part.url };
    case "prompt":
      return { type: "data-prompt", id: part.prompt_id, data: part.data };
    case "hub-note":
      // The thread draws hub notes from the store row; assistant-ui only needs a valid part.
      return { type: "data-hub-note", data: part };
  }
}

/** One stored row as the message assistant-ui renders. Status, attachments and metadata are not set. */
export function toThreadMessageLike(row: ChatRow): ThreadMessageLike {
  return {
    id: row.id,
    role: row.role,
    createdAt: new Date(row.created_at),
    content: row.parts.map(toPart),
  };
}

/** The repository for the rows, one message per row, in row order. */
export function toRepository(rows: readonly ChatRow[]): ExportedMessageRepository {
  return ExportedMessageRepository.fromArray(rows.map(toThreadMessageLike));
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
  // Memoized on the rows: a new repository makes assistant-ui sync the whole thread.
  const repository = useMemo(() => toRepository(snap.rows), [snap.rows]);
  return useExternalStoreRuntime(
    buildAdapter(store, { running: snap.running, hasEarlier: snap.hasEarlier }, repository),
  );
}
