import {
  AssistantRuntimeProvider,
  SimpleImageAttachmentAdapter,
  type AssistantTransportConnectionMetadata,
  type ImageMessagePart,
  type ThreadMessageLike,
  type TextMessagePart,
  unstable_createMessageConverter as createMessageConverter,
  useAssistantTransportRuntime,
  useAuiState,
} from "@assistant-ui/react";
import type { ReadonlyJSONObject } from "assistant-stream/utils";
import {
  cancelPendingPermission,
  pendingPermissions,
  permissionOwner,
  promptParkedSinceShown,
  samePendingPermission,
  type PendingPermission,
} from "@/utils/permission-cancel";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";
import type {
  EngineApi,
  HistoryMessage,
  PermissionPrompt,
  Session,
  SessionEvent,
  TurnError,
} from "./api";
import { isToolOutputBatch, stripAgentHud } from "./api";
import { GoalCancellationTracker } from "./utils/goal-cancel";
import {
  dropTransientTurnDuplicates,
  historyHasAssistantRequestId,
  historyMessageIndex,
  mergeCompactionHistorySnapshot,
  mergeMissingHistoryMessages,
  reconcileBoundaryToolResults,
  hasUnsettledRows,
  inFlightSendIdsFor,
  pendingSendIds,
  rememberSavedSends,
  shownMessagesChanged,
  withoutInFlightSends,
  withSavedCopies,
  rebaseOntoHistory,
  withoutOwnTurnRows,
} from "./history-merge";
import { historyContentToThreadContent } from "./history-content";
import {
  composerDraftStorageKey,
  completeComposerDraftSubmission,
  takeComposerDraftSubmission,
  type ComposerDraftSubmission,
} from "./composer-draft";
import {
  apartFromLive,
  applyLiveEvent,
  emptyLiveTurn,
  endsLiveTurn,
  eventNamesRequest,
  historyReplacesLiveTurn,
  isLiveMessage,
  liveMessage,
  replacedLiveTurn,
  withoutSavedParts,
  type LiveTurn,
} from "./live-turn";
import { parseHubMessageHeader, splitAgentHud, splitObservationReplies, type HubNote } from "./hub-notes";
import { PermissionToolUI } from "./components/PermissionTool";
import {
  historyTurnTiming,
  newTurnClock,
  observeRun,
  turnTimings,
  type TurnClock,
} from "./turn-timing";
import { VoiceModeProvider } from "./voice-mode";

export type EngineState = {
  sessionId: string;
  conversationUid?: string | null;
  sessions: Session[];
  messages: ThreadMessageLike[];
  /**
   * The first `messages` entries show the daemon history's first `history`
   * messages. Set when the history loads, and by the engine when a turn this
   * page ran ends; a later reload appends only what lies past it.
   */
  synced?: { history: number; messages: number; start?: number; authoritative?: boolean; olderWindow?: boolean };
  /** A private goal result may have interleaved with another history write. */
  goalHistoryReloadRequired?: boolean;
  error?: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
    toolCalls: number;
    stopReason?: string;
  };
};

const messageConverter = createMessageConverter<ThreadMessageLike>((message) => message);
// The converter reads convertConfig off a message: "none" ends its join group.
type JoinableMessage = ThreadMessageLike & { convertConfig?: { joinStrategy?: "concat-content" | "none" } };
export const imageAttachmentAdapter = new SimpleImageAttachmentAdapter();

const PermissionCancellationContext = createContext<{
  cancel: (expected: readonly PendingPermission[]) => Promise<void>;
  cancelling: boolean;
} | null>(null);

export function usePendingPermissionCancellation() {
  const control = useContext(PermissionCancellationContext);
  const messages = useAuiState((state) => state.thread.messages);
  const expected = pendingPermissions(messages);
  return control && expected.length ? {
    cancel: () => control.cancel(expected),
    cancelling: control.cancelling,
  } : null;
}

/**
 * A user turn's hub messages (the agent HUD the daemon puts on top of it, see
 * hub-notes.ts) and what is left: what the user typed. The HUD rides on the
 * first text part.
 */
function withHubNotes(content: ThreadMessageLike["content"], source?: unknown): {
  notes: HubNote[];
  content: ThreadMessageLike["content"];
} {
  if (typeof content === "string") {
    const { notes, rest } = source === "observation" && content.startsWith("Reply from ")
      ? splitObservationReplies(content) : splitAgentHud(content);
    return { notes, content: rest };
  }
  const first = content.findIndex((part) => part.type === "text");
  if (first < 0) return { notes: [], content };
  const text = (content[first] as TextMessagePart).text;
  const { notes, rest } = source === "observation" && text.startsWith("Reply from ")
    ? splitObservationReplies(text) : splitAgentHud(text);
  return {
    notes,
    content: content
      .map((part, index) => (index === first ? { ...part, text: rest } : part))
      .filter((part) => part.type !== "text" || part.text),
  };
}

function historyToMessages(
  history: HistoryMessage[],
  pendingPermissions: PermissionPrompt[],
  turnError?: TurnError | null,
  historyStartIndex = 0,
  sessionId = "",
): ThreadMessageLike[] {
  type RestoredToolCall = {
    type: "tool-call";
    toolCallId: string;
    toolName: string;
    args: ReadonlyJSONObject;
    argsText: string;
    result?: unknown;
    isError?: boolean;
    isPreliminary?: boolean;
    timing?: { startedAt: number; completedAt: number };
  };

  const asRecord = (value: unknown): Record<string, unknown> | null => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    return value as Record<string, unknown>;
  };

  const asString = (value: unknown): string | null =>
    typeof value === "string" && value.trim() ? value : null;

  const parseArguments = (
    value: unknown,
  ): { args: ReadonlyJSONObject; argsText: string } => {
    if (typeof value === "string") {
      try {
        const parsed = JSON.parse(value) as unknown;
        return {
          args: (asRecord(parsed) || {}) as ReadonlyJSONObject,
          argsText: value,
        };
      } catch {
        return { args: {}, argsText: value };
      }
    }
    const args = (asRecord(value) || {}) as ReadonlyJSONObject;
    return { args, argsText: JSON.stringify(args) };
  };

  const restoredCalls = (
    message: HistoryMessage,
    sourceIndex: number,
  ): RestoredToolCall[] => {
    const metadata = asRecord(message.metadata);
    const rawCalls = metadata?.tool_calls;
    if (!Array.isArray(rawCalls)) return [];
    const harnessCards = new Map<string, Record<string, unknown>>();
    const updates = metadata?.acp_updates;
    if (Array.isArray(updates)) {
      for (const rawUpdate of updates) {
        const update = asRecord(rawUpdate);
        if (update?.kind !== "tool_call" && update?.kind !== "tool_call_update") continue;
        const payload = asRecord(update.payload);
        const id = asString(payload?.toolCallId) || asString(payload?.tool_call_id);
        if (!id || !payload) continue;
        harnessCards.set(id, {
          ...harnessCards.get(id),
          ...Object.fromEntries(Object.entries(payload).filter(([, value]) => value != null)),
        });
      }
    }

    return rawCalls.flatMap((rawCall, callIndex) => {
      const call = asRecord(rawCall);
      if (!call) return [];
      const functionValue = asRecord(call.function);
      const toolCallId =
        asString(call.id) ||
        asString(call.tool_call_id) ||
        `history-${sourceIndex}-tool-${callIndex}`;
      const storedResult = asRecord(call.result);
      const harness: Record<string, unknown> | null =
        asRecord(asRecord(call.metadata)?.acp) ||
        asRecord(asRecord(storedResult?.metadata)?.acp) ||
        harnessCards.get(toolCallId) ||
        (metadata?.provider === "acp" ? {
          toolCallId,
          title: call.name,
          status: call.status,
          rawInput: call.input,
          rawOutput: call.result,
        } : null);
      // Vendor titles are display text, never native tool-UI routing names.
      const toolName = harness ? "harness_tool" :
        asString(functionValue?.name) ||
        asString(call.name) ||
        asString(call.tool_name) ||
        "tool";
      const rawArguments =
        functionValue?.arguments ?? call.arguments ?? call.input ?? call.args;
      const { args, argsText } = parseArguments(harness?.rawInput ?? rawArguments ?? {});
      const result = harness ? {
        success: harness.status !== "failed",
        output: harness.rawOutput ?? storedResult?.output ?? call.result ?? harness.content ?? "",
        metadata: { acp: harness },
      } : call.result;
      return [{
        type: "tool-call",
        toolCallId,
        toolName,
        ...(result !== undefined ? { result, isError: harness?.status === "failed" || Boolean(call.is_error) } : {}),
        ...(harness ? { isPreliminary: harness.status !== "completed" && harness.status !== "failed" } : {}),
        args,
        argsText,
      }];
    });
  };

  const toolResultId = (message: HistoryMessage): string | null => {
    const metadata = asRecord(message.metadata);
    return (
      asString(metadata?.tool_call_id) ||
      asString(metadata?.toolCallId) ||
      asString(metadata?.tool_id)
    );
  };

  const toolResultIsError = (
    message: HistoryMessage,
  ): boolean | undefined => {
    const metadata = asRecord(message.metadata);
    for (const key of [
      "is_error",
      "isError",
      "tool_output_is_error",
    ]) {
      if (typeof metadata?.[key] === "boolean") return metadata[key] as boolean;
    }
    if (metadata?.success === false) return true;
    return undefined;
  };

  const compactionMessage = (message: HistoryMessage): string => {
    const metadata = asRecord(message.metadata);
    const round = typeof metadata?.compaction_round === "number"
      ? metadata.compaction_round
      : typeof metadata?.compaction_round === "string"
        ? Number(metadata.compaction_round)
        : null;
    const preCount = typeof metadata?.pre_message_count === "number"
      ? metadata.pre_message_count
      : typeof metadata?.pre_message_count === "string"
        ? Number(metadata.pre_message_count)
        : null;
    const postCount = typeof metadata?.post_message_count === "number"
      ? metadata.post_message_count
      : typeof metadata?.post_message_count === "string"
        ? Number(metadata.post_message_count)
        : null;
    const roundText = Number.isFinite(round) ? ` (round ${round})` : "";
    const countText = Number.isFinite(preCount) && Number.isFinite(postCount)
      ? `: ${preCount} -> ${postCount} messages`
      : "";
    return `Context compacted${roundText}${countText}.`;
  };

  const messages: ThreadMessageLike[] = [];
  const callsById = new Map<string, RestoredToolCall>();
  // The ACP request that produced a reply: a passive window swaps its live
  // copy of that reply for this row (see historyReplacesLiveTurn).
  const replyMetadata = (metadata: Record<string, unknown> | null) => {
    const requestId = asString(metadata?.request_id);
    const voiceTurnId = asString(metadata?.voice_turn_id);
    const custom = {
      ...(requestId ? { requestId } : {}),
      ...(voiceTurnId ? { voiceTurnId } : {}),
    };
    return Object.keys(custom).length ? { metadata: { custom } } : {};
  };
  const restoredStatus = (metadata: Record<string, unknown> | null): ThreadMessageLike["status"] => {
    const reason = asString(metadata?.stop_reason);
    if (metadata?.provider === "acp" && reason && reason !== "end_turn") {
      return {
        type: "incomplete",
        reason: reason === "cancelled" ? "cancelled" :
          reason === "max_tokens" || reason === "max_turn_requests" ? "length" :
            reason === "error" || reason === "failed" ? "error" : "other",
      };
    }
    return { type: "complete", reason: "stop" };
  };

  // The reply time survives a reload: each turn is timed by the history's own
  // timestamps, from the user's message to the final reply. assistant-ui joins
  // a turn's adjacent assistant messages and keeps only the first one's
  // metadata, so the timing goes on the turn's first assistant message.
  let turnStartedAt: string | null | undefined;
  let turnTools = 0;
  let firstReply: number | undefined;
  let finalReplyAt: string | null | undefined;
  const closeTurn = () => {
    const timing = historyTurnTiming(turnStartedAt, finalReplyAt, turnTools);
    const first = firstReply === undefined ? undefined : messages[firstReply];
    if (firstReply !== undefined && first && timing) {
      messages[firstReply] = { ...first, metadata: { ...first.metadata, timing } };
    }
    turnStartedAt = undefined;
    turnTools = 0;
    firstReply = undefined;
    finalReplyAt = undefined;
  };
  // The failed turn's reply carries the error (assistant-ui keeps the first
  // status of the replies it joins); a turn that failed before replying gets
  // a reply of its own.
  const showTurnError = () => {
    if (!turnError) return;
    const reply = firstReply === undefined ? undefined : messages[firstReply];
    const status = reply?.status?.type === "incomplete" && reply.status.reason === "cancelled"
      ? { ...reply.status, error: turnError.message }
      : { type: "incomplete", reason: "error", error: turnError.message } as const;
    if (firstReply !== undefined && reply) messages[firstReply] = { ...reply, status };
    else {
      messages.push({
        id: `history-${historyStartIndex + turnError.history_length}-error`,
        role: "assistant",
        content: "",
        status,
      });
    }
  };

  // A voice turn can start with its reply (the greeting has no user message),
  // and assistant-ui joins adjacent assistant messages: the reply before a new
  // voice turn ends its group, so the two turns show apart.
  let lastReply: { index: number; voiceTurn: string | null } | undefined;
  const startReply = (metadata: Record<string, unknown> | null) => {
    const voiceTurn = asString(metadata?.voice_turn_id) ??
      (metadata?.interrupted === true ? asString(metadata?.turn_id) : null);
    if (voiceTurn && lastReply && lastReply.voiceTurn !== voiceTurn) {
      messages[lastReply.index] = {
        ...messages[lastReply.index],
        convertConfig: { joinStrategy: "none" },
      } as JoinableMessage;
      closeTurn();
    }
    return voiceTurn;
  };
  const pushReply = (message: ThreadMessageLike, voiceTurn: string | null) => {
    messages.push(message);
    lastReply = { index: messages.length - 1, voiceTurn };
    firstReply ??= messages.length - 1;
  };

  const toolOutputResult = (message: HistoryMessage) => {
    const metadata = asRecord(message.metadata);
    const reference = asString(metadata?.history_output_ref);
    const preview = historyContentToThreadContent(message.content);
    return reference && sessionId
      ? { kind: "history-output-preview", sessionId, reference, preview }
      : preview;
  };


  history.forEach((message, pageIndex) => {
    const sourceIndex = historyStartIndex + pageIndex;
    if (pageIndex === turnError?.history_length) showTurnError();
    const metadata = asRecord(message.metadata);
    // Initialization context remains in model history and Trajectory, not Chat.
    if (metadata?.system_initialization === true) return;
    if (
      metadata?.context_compaction === true ||
      metadata?.context_compaction === "true"
    ) {
      messages.push({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content: compactionMessage(message),
        status: { type: "complete", reason: "stop" },
        metadata: {
          custom: {
            contextCompaction: true,
            compactionRound: typeof metadata.compaction_round === "number"
              ? metadata.compaction_round : null,
            preMessageCount: typeof metadata.pre_message_count === "number"
              ? metadata.pre_message_count : null,
            postMessageCount: typeof metadata.post_message_count === "number"
              ? metadata.post_message_count : null,
          },
        },
      });
      return;
    }

    if (isToolOutputBatch(message)) return;

    if (message.role === "user") {
      // Before the HUD check: a status-only turn still ends the one before.
      closeTurn();
      turnStartedAt = message.timestamp;
      const rawPrompt = historyContentToThreadContent(message.content);
      const { notes, content } = withHubNotes(rawPrompt, metadata?.source);
      // What other agents sent this one, as messages from their gems.
      notes.forEach((note, noteIndex) => {
        messages.push({
          id: `history-${sourceIndex}-hub-${noteIndex}`,
          role: "system",
          content: note.text,
          metadata: { custom: { hub: { ...note, rawText: note.rawText || (typeof rawPrompt === "string" ? rawPrompt : JSON.stringify(rawPrompt)) }, rawPrompt } },
        });
      });
      // A turn that was only agent status stays in the Trajectory tab.
      if (!content.length) return;
      lastReply = undefined;  // a shown prompt already ends the reply's group
      // The stored copies of the images this row sent (#1088): the bubble's
      // [imageN] tokens render as thumbnails from these refs.
      const media = Array.isArray(message.media)
        ? message.media.filter((entry) => entry && typeof entry.ref === "string")
        : [];
      // The sends this row holds, by the ids their pages made (#1359): one per
      // operator input, or the row's own id (an ACP admission).
      const operatorInputs = Array.isArray(metadata?.operator_inputs) ? metadata.operator_inputs : [];
      const requestIds = [...new Set([
        ...operatorInputs.map((record) => asString(asRecord(record)?.request_id)),
        asString(metadata?.request_id),
      ].filter((id): id is string => id !== null))];
      // The daemon saves sends that arrive together as one row: show a row
      // per send, as each was sent, when the row reads as exactly their words.
      const sends = operatorInputs.flatMap((record) => {
        const text = asString(asRecord(record)?.text);
        return text ? [{ text, requestId: asString(asRecord(record)?.request_id) }] : [];
      });
      // The row as the daemon wrote each send's words (content_to_text): its
      // parts on lines of their own, each picture as a token, after the agent
      // HUD. Pictures are counted from the saved row itself, not its shown
      // text: once shown, a typed [image1] reads like a stored picture. Each send
      // numbers its own pictures from 1, so tokens compare as [image]. In
      // order, the row's pictures as shown: an image part, or null for a token.
      const TOKEN = /\[image\d+\]/g;
      const pictures: Array<ImageMessagePart | null> = [];
      const rawLines = (Array.isArray(message.content) ? message.content : [message.content]).map((rawPart) => {
        if (typeof rawPart === "string") return rawPart;
        const part = asRecord(rawPart);
        if (part?.type === "text" && typeof part.text === "string") return part.text;
        if (part?.type !== "image") return null;
        const shown = historyContentToThreadContent([part]);
        pictures.push(Array.isArray(shown) && shown[0]?.type === "image" ? shown[0] as ImageMessagePart : null);
        return "[image]";
      });
      // As the engine binds media (count_image_tokens): image parts are the
      // pictures; a row with none keeps them as tokens, its media list bound
      // one entry per token.
      if (!pictures.length) pictures.push(...media.map(() => null));
      const shownText = rawLines.every((line) => line !== null)
        ? stripAgentHud(rawLines.join("\n")).replace(TOKEN, "[image]") : null;
      const sentText = sends.map((send) => send.text.replace(TOKEN, "[image]")).join("\n");
      // Every token a send wrote is one of the row's pictures, in order: a
      // typed [image1] with no picture of its own leaves the row whole.
      const tokens = sends.reduce((count, send) => count + (send.text.match(TOKEN)?.length ?? 0), 0);
      let pictureAt = 0;
      const pieceContent = (text: string) => text.split(/(\[image\d+\])/).reduce<Array<TextMessagePart | ImageMessagePart>>(
        (parts, segment) => {
          const token = /^\[image\d+\]$/.test(segment);
          const picture = token ? pictures[pictureAt] : undefined;
          // A token keeps its number across the whole row, where the media list is.
          const words = token ? `[image${++pictureAt}]` : segment;
          const last = parts.at(-1);
          if (picture) parts.push(picture);
          else if (last?.type === "text") parts[parts.length - 1] = { type: "text", text: last.text + words };
          else if (words) parts.push({ type: "text", text: words });
          return parts;
        }, []);
      if (sends.length > 1 && sends.length === operatorInputs.length && tokens === pictures.length &&
          shownText?.trim() === sentText.trim()) {
        sends.forEach((send, index) => {
          const piece = pieceContent(send.text);
          // A piece showing a picture as its token keeps the row's media list.
          const custom = {
            ...(media.length && piece.some((part) => part.type === "text" && /\[image\d+\]/.test(part.text))
              ? { media } : {}),
            ...(send.requestId ? { requestIds: [send.requestId] } : {}),
          };
          messages.push({
            // Every piece has an id of its own: the engine maps a send's row to
            // the saved row's index, so history-<n> would read as shown.
            id: `history-${sourceIndex}-send-${index}`,
            role: "user",
            content: piece,
            ...(Object.keys(custom).length ? { metadata: { custom } } : {}),
          });
        });
        return;
      }
      const custom = {
        ...(media.length ? { media } : {}),
        ...(requestIds.length ? { requestIds } : {}),
      };
      messages.push({
        id: `history-${sourceIndex}`,
        role: "user",
        content,
        ...(Object.keys(custom).length ? { metadata: { custom } } : {}),
      });
      return;
    }

    if (message.role === "assistant") {
      const calls = restoredCalls(message, sourceIndex);
      if (!calls.length) {
        const content = historyContentToThreadContent(message.content);
        // Nothing to show: the agent chose not to answer (e.g. a hub message
        // that was only an acknowledgement).
        if (!content.length) return;
        const voiceTurn = startReply(metadata);
        pushReply({
          id: `history-${sourceIndex}`,
          role: "assistant",
          content,
          status: restoredStatus(metadata),
          ...replyMetadata(metadata),
        }, voiceTurn);
        finalReplyAt = message.timestamp;
        return;
      }
      // A step with tool calls; the turn's final reply comes after it.
      const voiceTurn = startReply(metadata);
      turnTools += calls.length;
      finalReplyAt = undefined;

      const content: Array<
        | { type: "text"; text: string }
        | RestoredToolCall
      > = [];
      const assistantContent = historyContentToThreadContent(message.content);
      if (typeof assistantContent === "string" && assistantContent) {
        content.push({ type: "text", text: assistantContent });
      } else if (Array.isArray(assistantContent)) {
        content.push(
          ...assistantContent.filter(
            (part): part is { type: "text"; text: string } =>
              part.type === "text",
          ),
        );
      }
      for (const call of calls) {
        callsById.set(call.toolCallId, call);
        content.push(call);
      }
      pushReply({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content,
        status: restoredStatus(metadata),
        ...replyMetadata(metadata),
      }, voiceTurn);
      return;
    }

    if (message.role === "tool") {
      const callId = toolResultId(message);
      const existingCall = callId ? callsById.get(callId) : undefined;
      if (existingCall) {
        existingCall.result = toolOutputResult(message);
        existingCall.isError = toolResultIsError(message);
        // History keeps only how long the call ran (seconds), not when; the
        // elapsed hook and the group total read just completedAt - startedAt.
        const runSeconds = asRecord(message.metadata)?.tool_execution_time;
        if (typeof runSeconds === "number" && runSeconds > 0) {
          existingCall.timing = {
            startedAt: 0,
            completedAt: Math.round(runSeconds * 1000),
          };
        }
        return;
      }

      // Keep an unmatched result visible rather than silently dropping a tool
      // message from a provider-specific history format.
      const fallbackCall: RestoredToolCall = {
        type: "tool-call",
        toolCallId: callId || `history-${sourceIndex}-tool-result`,
        toolName:
          asString(asRecord(message.metadata)?.tool_name) || "tool result",
        args: {},
        argsText: "{}",
        result: toolOutputResult(message),
        isError: toolResultIsError(message),
      };
      messages.push({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content: [fallbackCall],
        status: { type: "complete", reason: "stop" },
        ...(callId ? { metadata: { custom: { historyToolFallback: callId } } } : {}),
      });
    }
  });
  if (turnError && turnError.history_length >= history.length) showTurnError();
  closeTurn();

  // A daemon does not re-emit permission_request after a browser reload. Keep
  // the prompt as a pending tool call so assistant-ui can render it and its
  // addResult callback can submit the answer through assistant-transport.
  for (const [index, prompt] of pendingPermissions.entries()) {
    const toolCallId = `permission_${prompt.tool_id}`;
    messages.push({
      id: `permission-${prompt.tool_id}-${index}`,
      role: "assistant",
      content: [
        {
          type: "tool-call",
          toolCallId,
          toolName: "request_permission",
          args: JSON.parse(JSON.stringify({
            tool_id: prompt.tool_id,
            tool_name: prompt.tool_name,
            tool_type: prompt.tool_type || null,
            risk_level: prompt.risk_level || null,
            risk_reason: prompt.risk_reason || null,
            input: prompt.input || {},
            details: prompt.details || {},
            acp_options: prompt.details?.acp_options || [],
          })),
          argsText: JSON.stringify(prompt),
        },
      ],
      status: { type: "requires-action", reason: "tool-calls" },
    } satisfies ThreadMessageLike);
  }
  return messages;
}

type MidTurn = {
  rows: readonly ThreadMessageLike[];
  /** The provisional ids of the sends in flight, in order (inFlightSendIdsFor). */
  sendIds?: readonly string[];
  /**
   * The watched turn's live row as it stands now, or null once history shows
   * all of it. The run's states carry the row its request sent, which would
   * bring back the parts a sync took (#1359).
   */
  live?: { id: string; message: ThreadMessageLike | null } | null;
  /** The row id of each send still waiting behind the running turn, by request id (pendingSendIds). */
  waiting?: Map<string, string>;
};
const NO_MID_TURN: MidTurn = { rows: [] };

/**
 * Rows a history sync added while this page's run owned the thread, merged
 * around the run's own rows, which stay last (#1358, #1364).
 */
const withMidTurnRows = (messages: ThreadMessageLike[], midTurn: MidTurn): ThreadMessageLike[] => {
  if (!midTurn.rows.length) return messages;
  // The sync imported them too: take them out and merge them back in order,
  // so a pending send's row is placed after them.
  const midTurnIds = new Set(midTurn.rows.map((row) => row.id));
  return aroundLive(messages.filter((row) => !midTurnIds.has(row.id)),
    (rows) => mergeMissingHistoryMessages(rows, [...midTurn.rows]).messages);
};

const converter = (
  state: EngineState,
  metadata: AssistantTransportConnectionMetadata,
  midTurn: MidTurn = NO_MID_TURN,
) => {
  // Stream timing rides on the assistant message's metadata, where
  // `useMessageTiming` reads it (see turn-timing.ts).
  const live = midTurn.live;
  const stored = state.messages.flatMap((message) => {
    if (live && message.id === live.id) return live.message ? [live.message] : [];
    const timing =
      message.role === "assistant" && message.id
        ? turnTimings.get(message.id)
        : undefined;
    return [timing
      ? { ...message, metadata: { ...message.metadata, timing } }
      : message];
  });
  // A send in flight keeps the id its request was sent with: rows a sync adds
  // meanwhile change the row count, and a new id makes assistant-ui take the
  // row for a new turn and scroll it to the top under the reader (#1364).
  // That id is the one the engine gives the row, so it stays through the
  // first streamed state. Sends still waiting number after it and keep their
  // ids while they wait (pendingSendIds).
  const inFlight = (midTurn.sendIds ?? []).filter((id) => !state.messages.some((message) => message.id === id));
  const sends = metadata.pendingCommands.flatMap((command) => (command.type === "add-message" ? [command] : []));
  const requestIds = sends.map((command) =>
    ("id" in command.message && typeof command.message.id === "string" ? command.message.id : undefined));
  const ids = pendingSendIds(requestIds, inFlight, state.messages, state.sessionId, midTurn.waiting ?? new Map());
  const optimistic = sends.map((command, index) => {
    const parts: Array<TextMessagePart | ImageMessagePart> = command.message.parts.map(
      (part) =>
        part.type === "text"
          ? { type: "text" as const, text: part.text }
          : { type: "image" as const, image: part.image },
    );
    const requestId = requestIds[index];
    return {
      id: ids[index],
      role: "user" as const,
      content: parts,
      // The engine follows the send by this id into saved history (#1359).
      metadata: { isOptimistic: true, ...(requestId === undefined ? {} : { custom: { requestId } }) },
    } satisfies ThreadMessageLike;
  });

  return {
    // assistant-stream requires JSON state; EngineState is JSON-compatible at
    // runtime, while ThreadMessageLike's rich union is intentionally narrower
    // than the transport's structural JSON type.
    state: JSON.parse(JSON.stringify(state)),
    messages: messageConverter.toThreadMessages(
      apartFromLive(withMidTurnRows([...stored, ...optimistic], midTurn)),
      metadata.isSending,
      // The engine clears an error with an empty string, which assistant-ui
      // would draw as an empty error box. An error still there while a request
      // is out belongs to the run before it: the engine clears it on admission,
      // and until then it would sit under the new message.
      { error: metadata.isSending ? undefined : state.error || undefined },
    ),
    isRunning: metadata.isSending,
  };
};

type ThreadReadyRuntime = {
  thread: {
    getState: () => { messages: readonly ThreadMessageLike[] };
    subscribe: (callback: () => void) => () => void;
  };
};

/**
 * Merges rows as if a watched turn's live row were not there, then puts it
 * back before the row that followed it. The state a page's own send carries
 * keeps that row before the send's rows: merged around it, history rows would
 * land ahead of the send out of order (#1359); moved to the end, it would
 * change the turn the chat anchors to the top and scroll the reader (#1364).
 */
const aroundLive = <T extends { id?: unknown }>(messages: readonly T[], merge: (rows: T[]) => T[]): T[] => {
  const live = messages.flatMap((message, index) => isLiveMessage(message)
    ? [{ row: message, next: messages.slice(index + 1).find((later) => !isLiveMessage(later)) }] : []);
  const merged = merge(messages.filter((message) => !isLiveMessage(message)));
  for (const { row, next } of live) {
    const at = next ? merged.indexOf(next) : -1;
    if (at < 0) merged.push(row); else merged.splice(at, 0, row);
  }
  return merged;
};

const ownsPendingInput = (thread: {
  isRunning: boolean;
  messages: readonly { role: string; metadata?: { isOptimistic?: boolean } }[];
}) => thread.isRunning || thread.messages.some((message) =>
  message.role === "user" && message.metadata?.isOptimistic);

function InitialMessagesGate({
  runtime,
  messages,
  children,
}: {
  runtime: ThreadReadyRuntime;
  messages: readonly ThreadMessageLike[];
  children: ReactNode;
}) {
  const [ready, setReady] = useState(messages.length === 0);

  useEffect(() => {
    if (ready) return;
    let commit: number | undefined;
    const finish = () => {
      if (commit !== undefined) return;
      // Give the provider's assistant-ui adapter one commit to publish the
      // bound thread state before Thread reads its empty-state selector.
      commit = window.setTimeout(() => setReady(true), 50);
    };
    const check = () => {
      if (runtime.thread.getState().messages.length >= messages.length) finish();
    };

    // The remote thread runtime is bound by AssistantRuntimeProvider after
    // this component mounts. Wait for that binding before mounting Thread so
    // its initial empty-state selector cannot stick after a full reload.
    // Event-driven, not polled: a hidden tab clamps each chained timer to
    // ~1 s, so the old 20-step poll kept a reloaded session blank for 20 s.
    const unsubscribe = runtime.thread.subscribe(check);
    const fallback = window.setTimeout(finish, 250);
    check();
    return () => {
      unsubscribe();
      window.clearTimeout(fallback);
      if (commit !== undefined) window.clearTimeout(commit);
    };
  }, [messages.length, runtime, ready]);

  return ready ? children : null;
}

export function EngineRuntimeProvider({
  api,
  sessionId,
  agentIdentity,
  initialState,
  onStale,
  liveEvents,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  agentIdentity?: string;
  initialState: EngineState;
  /** Reload the thread whole, optionally only while a caller's guard holds. */
  onStale?: (stillCurrent?: () => boolean) => void | Promise<void>;
  /** Session-stream events, fed by App's followEvents loop. */
  liveEvents?: { current: ((event: SessionEvent) => void) | null };
  children: ReactNode;
}) {
  const turnRef = useRef<TurnClock>(newTurnClock());
  const runStartRef = useRef(0);
  const cancellingPermission = useRef(false);
  const goalCancellation = useRef(new GoalCancellationTracker());
  const mounted = useRef(true);
  const runVersion = useRef(0);
  const draftKey = composerDraftStorageKey(agentIdentity, sessionId);
  const pendingDraftSubmissions = useRef<ComposerDraftSubmission[]>([]);
  // The provisional row ids of the messages this page's running turn sent
  // (#1356). A turn that stops at a permission prompt keeps them for the
  // request that answers it; any other end clears them.
  const inFlightSendIds = useRef<ReadonlySet<string>>(new Set());
  // History rows a sync added while this page's run owned the thread (#1358).
  // Each state the run streams is built from what its request sent, so it
  // leaves them out: drawn without them for a frame, and assistant-ui keeps the
  // dropped rows as a second branch (the "2 / 2" picker). The converter adds
  // them back until the run lets go and they are folded into the state.
  const midTurnRows = useRef<ThreadMessageLike[]>([]);
  // The live row a sync changed while this page's run owned the thread (see MidTurn.live).
  const midTurnLive = useRef<MidTurn["live"]>(null);
  // The row ids of the sends waiting behind this page's running turn (see MidTurn.waiting).
  const waitingSendIds = useRef(new Map<string, string>());
  // The commands of the request now streaming, from the moment it is sent
  // until it ends; null between requests (see the hand-over below).
  const streamingRun = useRef<WeakSet<object> | null>(null);
  // A message waits behind the streaming request, and all that request sent has arrived.
  const handOverDue = useRef(false);
  // The page let its request go to send the message waiting behind it (see onCancel).
  const handingOver = useRef(false);
  const convert = useCallback((state: EngineState, metadata: AssistantTransportConnectionMetadata) => {
    const run = streamingRun.current;
    const pending = metadata.pendingCommands;
    handOverDue.current = !!run && metadata.isSending && pending.length > 0 &&
      pending.every((command) => command.type === "add-message" && !run.has(command));
    return converter(state, metadata, {
      rows: midTurnRows.current, sendIds: [...inFlightSendIds.current], live: midTurnLive.current,
      waiting: waitingSendIds.current,
    });
  }, []);
  const reloadRequestedAt = useRef<number | null>(null);
  const [settledRuns, setSettledRuns] = useState(0);
  const [permissionCancellationPending, setPermissionCancellationPending] = useState(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const runtime = useAssistantTransportRuntime({
    initialState,
    api: api.assistantUrl(sessionId),
    protocol: "assistant-transport",
    converter: convert,
    adapters: { attachments: imageAttachmentAdapter },
    headers: async () => {
      // Refresh before every transport request. Engine restarts rotate the
      // bearer token; assistant-ui's fetch hook does not retry 401 responses,
      // so obtaining the current token here preserves the one-retry contract
      // without exposing a stale token to the stream endpoint.
      await api.refreshToken();
      const token = api.getToken();
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return headers;
    },
    prepareSendCommandsRequest: (body) => {
      runVersion.current += 1;
      streamingRun.current = new WeakSet<object>(body.commands);
      handingOver.current = false;
      inFlightSendIds.current = inFlightSendIdsFor(body, sessionId, inFlightSendIds.current);
      // A new message starts a new turn; a tool answer continues the last one.
      if (body.commands.some((command) => command.type === "add-message")) {
        turnRef.current = newTurnClock();
        const draftSubmission = takeComposerDraftSubmission(draftKey);
        if (draftSubmission) pendingDraftSubmissions.current.push(draftSubmission);
      }
      runStartRef.current = performance.now();
      const goalReplyId = goalCancellation.current.prepare(body.commands);
      return { ...body, sessionId, ...(goalReplyId ? { goal_reply_id: goalReplyId } : {}) };
    },
    onResponse: (response) => {
      const draftSubmission = pendingDraftSubmissions.current.shift();
      if (response.ok) completeComposerDraftSubmission(draftSubmission);
      if (response.status === 401) {
        void api.refreshToken();
        return;
      }
      if (!response.ok) return;
      // The transport drops text deltas (the thread renders turn-end state),
      // so read the wire frames off a clone for real stream timing. Timing is
      // a nicety: nothing here may fail the run.
      try {
        observeRun(
          response.clone(),
          turnRef.current,
          runStartRef.current,
        ).catch(() => undefined);
      } catch {
        // A body that cannot be cloned just has no timing.
      }
    },
    onError: async (error, { updateState }) => {
      streamingRun.current = null;
      inFlightSendIds.current = new Set();
      pendingDraftSubmissions.current.shift();
      updateState((state) => ({ ...state, error: error.message }));
    },
    onCancel: ({ commands, updateState, error }) => {
      streamingRun.current = null;
      inFlightSendIds.current = new Set();
      // The page let its request go so the message waiting behind it reaches
      // the agent now (see the hand-over below). The agent's turn goes on:
      // send the message, and stop nothing.
      if (handingOver.current && !error) {
        const send = (runtime.thread.getState().extras as { sendCommand?: (command: unknown) => void } | undefined)
          ?.sendCommand;
        for (const command of commands) send?.(command);
        return;
      }
      // A failed request lands here too, after onError: keep the error it
      // set, and leave the daemon's turn alone (a dropped phone connection
      // must not stop the agent's work).
      if (error) return;
      pendingDraftSubmissions.current.shift();
      // A goal control can run while a different model turn is active. Stop
      // only the request that this transport owns.
      const goal = goalCancellation.current.cancel(commands);
      if (goal.goal) {
        if (goal.id) void api.cancelOwned(sessionId, goal.id).catch(() => undefined);
      } else {
        void api.cancel(sessionId).catch(() => undefined);
      }
      updateState((state) => ({ ...state, error: "Run cancelled" }));
    },
    onFinish: () => {
      streamingRun.current = null;
      handingOver.current = false;
      const last = runtime.thread.getState().messages.at(-1);
      if (last?.status?.type !== "requires-action") inFlightSendIds.current = new Set();
      goalCancellation.current.finish();
      setSettledRuns((count) => count + 1);
    },
  });

  // A message sent while this page's turn runs waits behind the request that
  // streams the turn: assistant-ui sends it when that request ends, which is
  // when the whole turn does. The daemon takes a message into the running
  // turn at its next step, as it does for another client's, so let the
  // request go and send the message now (#1365). The engine closes a request
  // the page drops without stopping the daemon; the next request streams the
  // rest of the turn under the new message, and the part the dropped request
  // had not shown comes with saved history. Only once all the streaming
  // request sent has arrived: a message of its own still on its way would be
  // lost with it.
  useEffect(() => runtime.thread.subscribe(() => {
    if (!handOverDue.current || handingOver.current) return;
    queueMicrotask(() => {
      if (!mounted.current || !handOverDue.current || handingOver.current) return;
      if (!runtime.thread.getState().isRunning) return;
      handOverDue.current = false;
      handingOver.current = true;
      runtime.thread.cancelRun();
    });
  }), [runtime]);

  useEffect(() => {
    // A goal's result is private to its requesting stream, but its history can
    // interleave with another client. Do not call that history synchronized
    // until it has actually been reloaded. Defer until any queued run starts,
    // and recheck after the load so a later user turn is never unmounted.
    const version = runVersion.current;
    const stillCurrent = () => mounted.current && runVersion.current === version
      && !runtime.thread.getState().isRunning;
    const timer = window.setTimeout(() => {
      const thread = runtime.thread.getState();
      const state = (thread.extras as { state?: EngineState } | undefined)?.state;
      if (!stillCurrent() || !state?.goalHistoryReloadRequired || !onStale
          || reloadRequestedAt.current === version) return;
      reloadRequestedAt.current = version;
      void Promise.resolve(onStale(stillCurrent)).catch(() => undefined).finally(() => {
        if (reloadRequestedAt.current === version) reloadRequestedAt.current = null;
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [settledRuns, runtime, onStale]);

  const cancelPermission = useCallback(async (expected: readonly PendingPermission[]) => {
    const current = () => mounted.current && !runtime.thread.getState().isRunning
      ? pendingPermissions(runtime.thread.getState().messages) : [];
    if (cancellingPermission.current) return;
    if (!samePendingPermission(expected, current())) {
      if (mounted.current && !runtime.thread.getState().isRunning) onStale?.();
      return;
    }
    cancellingPermission.current = true;
    setPermissionCancellationPending(true);
    try {
      // A permission ends the HTTP transport but leaves the daemon waiting.
      // Cancel that same session even after reload or an external client's turn.
      await cancelPendingPermission({
        expected, current,
        readPending: async () => (await api.getPermissions(sessionId)).pending_prompts.flatMap((prompt) => {
          const owner = permissionOwner(prompt);
          return owner ? [owner] : [];
        }),
        cancel: (requestId) => api.cancelOwned(sessionId, requestId),
      });
      if (mounted.current && !runtime.thread.getState().isRunning) onStale?.();
    } catch (error) {
      if (samePendingPermission(expected, current())) {
        const shown = (runtime.thread.getState().extras as { state?: EngineState } | undefined)?.state;
        if (shown) runtime.thread.importExternalState({
          ...shown,
          error: error instanceof Error ? error.message : "Unable to stop the turn",
        });
      }
    } finally {
      cancellingPermission.current = false;
      if (mounted.current) setPermissionCancellationPending(false);
    }
  }, [api, sessionId, onStale, runtime]);

  // A turn this page did not start (another window, the agent's terminal, a
  // peer's message) streams through App's event subscription. Its events fold
  // into one ephemeral assistant message so a passive window watches the
  // reply arrive; only the matching canonical history row replaces it. A page
  // that runs the turn itself ignores these.
  const liveTurn = useRef<LiveTurn>(emptyLiveTurn());
  const settleTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Events folded into liveTurn while this page's own run kept the thread busy.
  const unshownLive = useRef(false);
  const showPendingLive = useRef<() => void>(() => undefined);
  useEffect(() => {
    if (!liveEvents) return;
    const importLiveState = (messages: ThreadMessageLike[]) => {
      const shown = (runtime.thread.getState().extras as { state?: EngineState } | undefined)?.state;
      if (!shown) return;
      runtime.thread.importExternalState({
        ...shown,
        messages,
        synced: shown.synced ? { ...shown.synced, messages: messages.length } : undefined,
      });
    };
    const showLiveTurn = (next: LiveTurn) => {
      const shown = (runtime.thread.getState().extras as { state?: EngineState } | undefined)?.state;
      if (!shown) return;
      // Keep this live copy outside synced bookkeeping until history carries
      // the matching reply. App retries after terminal events; replacing by an
      // unrelated assistant row could silently lose a delayed voice reply.
      const kept = shown.messages.filter((message) => !isLiveMessage(message));
      const live = liveMessage(next);
      if (!live && kept.length === shown.messages.length) return;
      // A live row already shown is updated where it is: moved to the end it
      // would change the turn the chat anchors and scroll the reader (#1364).
      const at = shown.messages.findIndex(isLiveMessage);
      importLiveState(!live ? kept : at < 0 ? [...kept, live]
        : [...kept.slice(0, at), live, ...kept.slice(at)]);
      // Completed replies stay visible until canonical history catches up.
      // Preserve the prior bounded cleanup for cancellation and error states.
      clearTimeout(settleTimer.current);
      if (next.settled && next.settled !== "complete" && live) {
        settleTimer.current = setTimeout(() => {
          if (liveTurn.current.parts !== next.parts || !liveTurn.current.settled) return;
          liveTurn.current = replacedLiveTurn(liveTurn.current);  // a held turn starts now
          const state = (runtime.thread.getState().extras as { state?: EngineState } | undefined)?.state;
          if (!state) return;
          const held = liveMessage(liveTurn.current);
          const kept = state.messages.filter((m) => !isLiveMessage(m));
          importLiveState(held ? [...kept, held] : kept);
        }, 5000);
      }
    };
    showPendingLive.current = () => {
      if (!unshownLive.current) return;
      unshownLive.current = false;
      showLiveTurn(liveTurn.current);
    };
    liveEvents.current = (event: SessionEvent) => {
      const thread = runtime.thread.getState();
      if (thread.isRunning && goalCancellation.current.isActive()
          && event.type === "history_updated"
          && typeof event.conversation_uid === "string"
          && initialState.conversationUid
          && event.conversation_uid !== initialState.conversationUid) {
        // A terminal switched this daemon's conversation while the goal was
        // in flight. Abort this page's stream now; Stop is still scoped to the
        // goal handle, and the ensuing history refresh remounts the new one.
        runtime.thread.cancelRun();
        return;
      }
      if (thread.isRunning) {
        // This page's own submission is pending (a held steering admission, a
        // queued send), so the thread takes no import now. A turn already on
        // screen goes on: fold only its own request's events into the live
        // state and show them when the run ends. Save notices are accepted
        // before the streamed reply has established its request id.
        const visible = liveTurn.current;
        // The turn this page was watching when it sent ends with the page's
        // own: its terminal event settles the live row, whose output the
        // page's stream draws from here on (#1362).
        const ends = !!visible.requestId && (endsLiveTurn(String(event.type ?? "")) || event.type === "turn_cancelled") &&
          eventNamesRequest(event, visible.requestId);
        if (!ends && event.type !== "notice" && (!visible.requestId || event.request_id !== visible.requestId)) return;
        if (event.type === "notice" && visible.requestId && event.request_id
            && event.request_id !== visible.requestId) return;
        const folded = applyLiveEvent(visible, event);
        if (folded === visible) return;
        liveTurn.current = folded;
        unshownLive.current = true;
        return;
      }
      const previous = liveTurn.current;
      const next = applyLiveEvent(previous, event);
      if (next === previous) return;
      liveTurn.current = next;
      if (next.held && next.parts === previous.parts) return;  // held back: nothing new to show
      showLiveTurn(next);
    };
    return () => { liveEvents.current = null; clearTimeout(settleTimer.current); };
  }, [runtime, liveEvents, initialState.conversationUid]);

  // Where saved history holds each send this page has seen saved (see SavedSends).
  const savedSends = useRef(new Map<string, number>());

  /**
   * The thread after a history sync added `rows`: the live turn gives up the
   * parts those rows now show (a tool call drawn twice otherwise), and its row
   * follows (#1359).
   */
  const withLiveSynced = useCallback((messages: ThreadMessageLike[], rows: readonly ThreadMessageLike[]) => {
    // Only the rows saved after the message that started the turn are its
    // own: the reply of the turn before can reach the page late, after this
    // one's first words, and contain them. A turn whose message the page has
    // not seen saved is a long one, and the rows a sync adds are its own.
    const startedAt = liveTurn.current.requestId ? savedSends.current.get(liveTurn.current.requestId) : undefined;
    const own = startedAt === undefined ? rows
      : rows.filter((row) => (historyMessageIndex(row) ?? Number.POSITIVE_INFINITY) > startedAt);
    const synced = withoutSavedParts(liveTurn.current, own);
    if (synced === liveTurn.current) return messages;
    liveTurn.current = synced;
    const live = liveMessage(synced);
    const at = messages.findIndex(isLiveMessage);
    if (at >= 0 && runtime.thread.getState().isRunning) {
      // The run's next state brings the row its request sent: keep this one.
      midTurnLive.current = { id: String(messages[at].id), message: live };
      unshownLive.current = true;
    }
    const kept = messages.filter((message) => !isLiveMessage(message));
    return !live ? kept : at < 0 ? [...kept, live] : [...kept.slice(0, at), live, ...kept.slice(at)];
  }, [runtime]);

  // Output folded in while this page's own run was pending shows once it ends.
  useEffect(() => runtime.thread.subscribe(() => {
    if (!unshownLive.current || runtime.thread.getState().isRunning) return;
    showPendingLive.current();
  }), [runtime]);

  // Turns this page did not run (a hub message that woke the agent, a turn
  // typed in the terminal) reach the open thread when App reloads the history
  // after them. Only the history past what the thread shows is appended:
  // assistant-ui keeps every message it was given, so swapping in reloaded
  // copies of turns already on screen would show those turns twice.
  const reloaded = useRef(initialState.messages);
  const reloadedWindowStart = useRef(initialState.synced?.start ?? 0);
  const latestInitial = useRef(initialState);
  latestInitial.current = initialState;
  const [historySyncRevision, setHistorySyncRevision] = useState(0);
  const deferredHistorySync = useRef(false);
  const deferredOlderWindow = useRef<EngineState | null>(null);
  useEffect(() => runtime.thread.subscribe(() => {
    if (ownsPendingInput(runtime.thread.getState())) return;
    // The run let go: its states no longer bring the sent live row back.
    midTurnLive.current = null;
    if (midTurnRows.current.length) {
      const midTurn = { rows: midTurnRows.current };
      midTurnRows.current = [];
      const current = (runtime.thread.getState().extras as { state?: EngineState } | undefined)?.state;
      if (current) {
        // The run's last state carries the live row its request sent: show the
        // watched turn as it stands now.
        const live = liveMessage(liveTurn.current);
        const messages = withMidTurnRows(current.messages, midTurn)
          .flatMap((row) => !isLiveMessage(row) ? [row] : live && live.id === row.id ? [live] : []);
        runtime.thread.importExternalState({
          ...current,
          messages,
          synced: current.synced ? { ...current.synced, messages: messages.length } : current.synced,
        });
      }
    }
    if (!deferredHistorySync.current) return;
    deferredHistorySync.current = false;
    setHistorySyncRevision((revision) => revision + 1);
  }), [runtime]);
  useEffect(() => {
    const thread = runtime.thread.getState();
    // A compaction replaces the daemon's history indexes. Treat its marker as
    // an in-place transcript rebase before stale-row checks can remount the
    // provider. Do not consume it while a local input owns the thread: its
    // compacted snapshot must be retried after that run settles.
    const currentShown = (thread.extras as { state?: EngineState } | undefined)?.state;
    const compacted = currentShown && initialState.messages !== reloaded.current &&
      initialState.synced && initialState.synced.authoritative !== false
      ? mergeCompactionHistorySnapshot(currentShown.messages, initialState.messages)
      : null;
    // A compaction renumbers the rows: positions from before it mean nothing.
    if (compacted) savedSends.current.clear();
    rememberSavedSends(savedSends.current, initialState.messages);
    if (compacted) {
      if (ownsPendingInput(thread)) {
        deferredHistorySync.current = true;
        return;
      }
      const snapshot = initialState;
      const timer = window.setTimeout(() => {
        if (!mounted.current || latestInitial.current.messages !== snapshot.messages) return;
        const currentThread = runtime.thread.getState();
        if (ownsPendingInput(currentThread)) {
          deferredHistorySync.current = true;
          return;
        }
        const current = (currentThread.extras as { state?: EngineState } | undefined)?.state;
        if (!current || current.sessionId !== snapshot.sessionId ||
          (current.conversationUid && snapshot.conversationUid &&
            current.conversationUid !== snapshot.conversationUid)) return;

        const rebased = mergeCompactionHistorySnapshot(current.messages, snapshot.messages, isLiveMessage);
        if (!rebased) {
          // Another sync already imported this marker; it is now our baseline.
          reloaded.current = snapshot.messages;
          deferredOlderWindow.current = null;
          return;
        }
        const liveBefore = liveTurn.current;
        const exactLiveReply = liveBefore.parts.length > 0 &&
          historyHasAssistantRequestId(rebased.history, liveBefore.requestId);
        const nextLive = exactLiveReply ? replacedLiveTurn(liveBefore) : liveBefore;
        const held = exactLiveReply ? liveMessage(nextLive) : null;
        const messages = exactLiveReply
          ? [...rebased.messages.filter((message) => !isLiveMessage(message)), ...(held ? [held] : [])]
          : rebased.messages;
        const replacement: EngineState = {
          ...current,
          ...snapshot,
          messages,
          synced: { ...snapshot.synced!, messages: messages.length },
        };
        const priorReloaded = reloaded.current;
        const runAtRebase = runVersion.current;
        reloaded.current = snapshot.messages;
        // Keep existing display IDs and publish once so the viewport anchor stays mounted.
        flushSync(() => {
          if (!mounted.current) return;
          const busy = ownsPendingInput(runtime.thread.getState());
          const stillCurrent = latestInitial.current.messages === snapshot.messages &&
            runVersion.current === runAtRebase && liveTurn.current === liveBefore && !busy;
          if (!stillCurrent) {
            reloaded.current = priorReloaded;
            deferredHistorySync.current = busy;
            if (!busy) window.setTimeout(() => {
              if (mounted.current) setHistorySyncRevision((revision) => revision + 1);
            }, 0);
          } else {
            if (exactLiveReply) liveTurn.current = nextLive;
            deferredOlderWindow.current = null;
          }
          runtime.thread.importExternalState(stillCurrent ? replacement : current);
        });
      }, 0);
      return () => window.clearTimeout(timer);
    }


    // Never remap the thread while a locally queued input still owns it.
    if (ownsPendingInput(thread)) {
      if (initialState.messages === reloaded.current) return;
      if (initialState.synced?.olderWindow) deferredOlderWindow.current = initialState;
      // A reconnect can recover a followed reply from canonical history even
      // when its token events were missed. Do not publish that row beside the
      // ephemeral live reply; reconcile it after this page's run releases the
      // thread.
      if (historyHasAssistantRequestId(initialState.messages, liveTurn.current.requestId)) {
        deferredHistorySync.current = true;
        return;
      }
      const shown = (thread.extras as { state?: EngineState } | undefined)?.state;
      const from = shown?.synced;
      if (shown && from && initialState.synced &&
        initialState.synced.authoritative !== false && !initialState.synced.olderWindow &&
        shown.sessionId === initialState.sessionId &&
        (!shown.conversationUid || !initialState.conversationUid ||
          shown.conversationUid === initialState.conversationUid)) {
        // Publish only newly arrived canonical history rows while this page's
        // turn is active. Preserve its in-flight messages and defer any full
        // reconciliation until settlement, when the engine mirror has caught up.
        // The saved copy of this page's own send stays out: its in-flight row
        // already shows it (#1356).
        const newRows = initialState.messages.filter((message) => {
          const index = historyMessageIndex(message);
          return index !== null && index >= from.history;
        });
        // Nor do the rows of the turn its stream is drawing, and the saved
        // copies of sends an earlier run left on screen (#1359).
        const tail = withoutOwnTurnRows(thread.messages, initialState.messages,
          withoutInFlightSends(thread.messages, newRows, inFlightSendIds.current), savedSends.current);
        const merged = { added: [] as ThreadMessageLike[], messages: [] as ThreadMessageLike[] };
        merged.messages = aroundLive(shown.messages, (rows) => {
          const result = mergeMissingHistoryMessages(rows, tail);
          merged.added = result.added;
          return result.messages;
        });
        if (merged.added.length) {
          midTurnRows.current = mergeMissingHistoryMessages(midTurnRows.current, merged.added).messages;
          const messages = withLiveSynced(merged.messages, merged.added);
          runtime.thread.importExternalState({
            ...shown,
            messages,
            synced: {
              ...from,
              history: Math.max(from.history, initialState.synced.history),
              messages: messages.length,
            },
          });
        }
      }
      // Reconcile the newest snapshot after settlement, when the engine
      // mirror has caught up and this thread no longer has an active owner.
      deferredHistorySync.current = true;
      return;
    }
    const pendingOlder = deferredOlderWindow.current;
    const deferred = !!pendingOlder && pendingOlder.sessionId === initialState.sessionId &&
      (!pendingOlder.conversationUid || !initialState.conversationUid ||
        pendingOlder.conversationUid === initialState.conversationUid);
    if (pendingOlder && !deferred) deferredOlderWindow.current = null;
    if (!deferred && initialState.messages === reloaded.current) return;
    // A permission prompt opened or closed in another window (the agent's
    // terminal): a run that ends at a prompt leaves no sync point to append
    // from, so when the thread and the reloaded history disagree on whether a
    // prompt is waiting, reload the thread whole.
    const waiting = (message?: { status?: { type?: string } }) => message?.status?.type === "requires-action";
    if (!deferred && waiting(thread.messages.at(-1)) !== initialState.messages.some(waiting)) {
      reloaded.current = initialState.messages;
      onStale?.();
      return;
    }
    // The turn behind the shown prompt ended and left it open: reload whole too.
    if (!deferred && promptParkedSinceShown(thread.messages, initialState.messages)) {
      reloaded.current = initialState.messages;
      onStale?.();
      return;
    }
    const shown = (thread.extras as { state?: EngineState } | undefined)?.state;
    const from = shown?.synced;
    if (from?.start !== undefined) reloadedWindowStart.current = from.start;
    /**
     * Rebuilds the thread on the loaded history where it stands (see
     * rebaseOntoHistory); false when this history cannot prove what is gone.
     */
    const rebase = (): boolean => {
      const loaded = initialState.synced;
      if (!shown || !loaded || loaded.authoritative === false || loaded.olderWindow || deferred ||
          shown.sessionId !== initialState.sessionId ||
          (shown.conversationUid && initialState.conversationUid &&
            shown.conversationUid !== initialState.conversationUid)) return false;
      const known = new Set(shown.messages);
      let added: ThreadMessageLike[] = [];
      const rebuilt = aroundLive(shown.messages, (rows) => {
        const next = rebaseOntoHistory(rows, initialState.messages, loaded.history, savedSends.current);
        added = next.filter((row) => !known.has(row) && historyMessageIndex(row) !== null);
        return next;
      });
      // A live turn this page is watching gives way to its saved reply.
      const replaced = historyReplacesLiveTurn(liveTurn.current, added);
      if (replaced) liveTurn.current = replacedLiveTurn(liveTurn.current);
      const held = replaced ? liveMessage(liveTurn.current) : null;
      const messages = replaced
        ? [...rebuilt.filter((row) => !isLiveMessage(row)), ...(held ? [held] : [])]
        : withLiveSynced(rebuilt, added);
      deferredOlderWindow.current = null;
      // Rows history has not taken yet keep the thread off its sync point, so
      // the next load rebuilds it again.
      const settledNow = !hasUnsettledRows(messages);
      const unchanged = messages.length === shown.messages.length &&
        messages.every((row, index) => row === shown.messages[index]);
      if (unchanged && (from ? from.messages === messages.length : !settledNow)) return true;
      const shownStart = messages.reduce((first, row) => Math.min(first, historyMessageIndex(row) ?? first), Infinity);
      runtime.thread.importExternalState({
        ...shown,
        // The error a dropped stream left belongs to rows the history replaced.
        ...(settledNow && hasUnsettledRows(shown.messages) ? { error: undefined } : {}),
        messages,
        synced: settledNow ? {
          history: Math.max(from?.history ?? 0, loaded.history),
          start: Math.min(from?.start ?? (Number.isFinite(shownStart) ? shownStart : loaded.start ?? 0), loaded.start ?? 0),
          messages: messages.length,
        } : undefined,
      });
      return true;
    };
    // A failed alignment leaves the local tail unproven. A cursor-validated
    // older page can still extend the prefix strictly before the known window.
    // Preserve every local row and leave the sync watermark unclaimed.
    if (shown && !from && (deferred || initialState.synced?.olderWindow)) {
      const page = deferred && pendingOlder ? pendingOlder : initialState;
      if (!page.synced || shown.sessionId !== page.sessionId ||
          (shown.conversationUid && page.conversationUid && shown.conversationUid !== page.conversationUid)) return;
      const firstShown = shown.messages.reduce((first, row) =>
        Math.min(first, historyMessageIndex(row) ?? Infinity), Infinity);
      const end = Number.isFinite(firstShown) ? firstShown : reloadedWindowStart.current;
      const prefix = page.messages.filter((row) => {
        const index = historyMessageIndex(row);
        return index !== null && index < end;
      });
      const { messages, added } = mergeMissingHistoryMessages(shown.messages, prefix);
      deferredOlderWindow.current = null;
      if (page.messages === initialState.messages) reloaded.current = page.messages;
      if (added.length) runtime.thread.importExternalState({ ...shown, messages });
      return;
    }
    if (!shown || !from || shown.messages.length !== from.messages || !initialState.synced) {
      deferredOlderWindow.current = null;
      reloaded.current = initialState.messages;
      // A run that ended without the engine matching its rows (its stream
      // dropped, or its final state claimed no sync) leaves the thread off its
      // sync point: take the saved history in place (#1359). A turn parked
      // at a permission prompt is off it too, and stays as it is.
      if (!waiting(thread.messages.at(-1))) rebase();
      return;
    }
    if ((initialState.synced.olderWindow || deferred) && reconcileBoundaryToolResults(
      shown.messages, initialState.messages, initialState.synced.start ?? 0, initialState.synced.history,
    )) {
      // Boundary correction retains the shown fallback id. Publish the older
      // rows and corrected content together so assistant-ui can keep its
      // existing viewport anchor and message instances.
      const timer = window.setTimeout(() => {
        if (!mounted.current || latestInitial.current.messages !== initialState.messages) return;
        const currentThread = runtime.thread.getState();
        if (ownsPendingInput(currentThread)) {
          deferredHistorySync.current = true;
          return;
        }
        const current = (currentThread.extras as { state?: EngineState } | undefined)?.state;
        const currentSync = current?.synced;
        const incoming = initialState.synced;
        if (!current || !currentSync || current.messages.length !== currentSync.messages || !incoming ||
          current.sessionId !== initialState.sessionId ||
          (current.conversationUid && initialState.conversationUid && current.conversationUid !== initialState.conversationUid)) {
          deferredOlderWindow.current = null;
          reloaded.current = initialState.messages;
          return;
        }
        const corrected = reconcileBoundaryToolResults(current.messages, initialState.messages,
          incoming.start ?? 0, incoming.history);
        if (!corrected) {
          setHistorySyncRevision((revision) => revision + 1);
          return;
        }
        const shownIds = new Set(current.messages.map((message) => message.id));
        const tail = initialState.messages.filter((message) => {
          const index = historyMessageIndex(message);
          return index !== null && index >= currentSync.history && !shownIds.has(message.id);
        });
        const liveBefore = liveTurn.current;
        const replaced = historyReplacesLiveTurn(liveBefore, tail);
        const nextLive = replaced ? replacedLiveTurn(liveBefore) : liveBefore;
        const held = replaced ? liveMessage(nextLive) : null;
        const messages = replaced
          ? [...corrected.filter((message) => !isLiveMessage(message)), ...(held ? [held] : [])]
          : corrected;
        const replacement = {
          ...current,
          messages,
          synced: {
            history: Math.max(currentSync.history, incoming.history),
            start: Math.min(currentSync.start ?? 0, incoming.start ?? 0),
            messages: messages.length,
          },
        };
        const previousReloaded = reloaded.current;
        const version = runVersion.current;
        const busy = ownsPendingInput(runtime.thread.getState());
        const stillCurrent = latestInitial.current.messages === initialState.messages &&
          runVersion.current === version && liveTurn.current === liveBefore && !busy;
        if (!stillCurrent) {
          reloaded.current = previousReloaded;
          deferredHistorySync.current = busy;
          if (!busy) window.setTimeout(() => {
            if (mounted.current) setHistorySyncRevision((revision) => revision + 1);
          }, 0);
        } else {
          reloaded.current = initialState.messages;
          if (replaced) liveTurn.current = nextLive;
          deferredOlderWindow.current = null;
        }
        flushSync(() => runtime.thread.importExternalState(stillCurrent ? replacement : current));
      }, 0);
      return () => window.clearTimeout(timer);
    }
    // The run's own rows take their saved copies in place (#1358).
    const settled = withSavedCopies(shown.messages, initialState.messages);
    // A non-authoritative window (older-focused merge, or a cache fallback
    // while a fresh sync was unavailable) cannot prove that rows missing
    // from it were deleted: merging keeps the shown rows instead of
    // resetting the thread on every such publish. Only a fresh
    // latest-window claim may declare shown rows gone.
    if (
      shownMessagesChanged(settled, initialState.messages, initialState.synced.history) &&
      initialState.synced.authoritative !== false && !deferred
    ) {
      reloaded.current = initialState.messages;
      // In place: a whole reload blanks the chat and moves the reader (#1358).
      if (!rebase()) onStale?.();
      return;
    }
    deferredOlderWindow.current = null;
    reloaded.current = initialState.messages;
    const currentStart = from.start ?? 0;
    const nextStart = initialState.synced.start ?? currentStart;
    const kept = settled.filter((message) => !isLiveMessage(message));
    const merged = mergeMissingHistoryMessages(kept, initialState.messages);
    const tail = merged.added.filter((message) => {
      const index = historyMessageIndex(message);
      return index !== null && index >= from.history;
    });
    if (!merged.added.length && nextStart >= currentStart && settled === shown.messages) return;
    // A live turn this page is only watching rides at the end, outside the
    // synced bookkeeping, until the history carries its reply (replaced above).
    const replaced = historyReplacesLiveTurn(liveTurn.current, tail);
    if (replaced) liveTurn.current = replacedLiveTurn(liveTurn.current);
    const held = replaced ? liveMessage(liveTurn.current) : null;  // the queued turn it held back
    const liveShown = replaced ? (held ? [held] : []) : shown.messages.filter(isLiveMessage);
    // A stale sync point replays the run's own rows next to the transient
    // ones it already drew (#666); drop the transient block when the tail
    // carries the same turn, proven row by row before anything is hidden.
    const deduped = dropTransientTurnDuplicates(merged.messages, tail);
    // A live row that history did not replace keeps its place (#1364), without
    // the parts the new rows show.
    const messages = replaced ? [...(deduped ?? merged.messages), ...liveShown]
      : withLiveSynced(aroundLive(settled, () => [...(deduped ?? merged.messages)]), tail);
    runtime.thread.importExternalState({
      ...shown,
      messages,
      synced: {
        history: Math.max(from.history, initialState.synced.history),
        start: Math.min(currentStart, nextStart),
        messages: messages.length,
      },
    });
  }, [initialState, runtime, onStale, historySyncRevision]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <PermissionCancellationContext.Provider
        value={{ cancel: cancelPermission, cancelling: permissionCancellationPending }}
      >
        <InitialMessagesGate
          runtime={runtime}
          messages={initialState.messages}
        >
          <PermissionToolUI />
          <VoiceModeProvider api={api} sessionId={sessionId}>
            {children}
          </VoiceModeProvider>
        </InitialMessagesGate>
      </PermissionCancellationContext.Provider>
    </AssistantRuntimeProvider>
  );
}

// Stable identity for the "no extras yet" case. `useAuiState` compares selector
// results by reference, so returning a fresh `{}` from the selector below would
// register as a state change on every render and loop until React throws
// "Maximum update depth exceeded" (error #185). It MUST be a module constant.
const EMPTY_EXTRAS: { state?: EngineState } = {};

export function useEngineRuntimeState(): { state?: EngineState } {
  // `thread.extras` is `unknown` and genuinely absent on the first render of
  // any component mounted under EngineRuntimeProvider — AssistantRuntimeProvider
  // wires the assistant-transport runtime into the aui store after that first
  // pass. Without this guard, `useEngineRuntimeState().state` throws on mount
  // and white-screens the app. Never let this return undefined.
  return useAuiState(
    (state) =>
      (state.thread.extras ?? EMPTY_EXTRAS) as unknown as {
        state?: EngineState;
      },
  );
}

export function buildInitialState(
  sessionId: string,
  sessions: Session[],
  history: HistoryMessage[],
  pendingPermissions: PermissionPrompt[] = [],
  lastTurnError: TurnError | null = null,
  historyStartIndex = 0,
  conversationUid: string | null = null,
  // A window built from an older-focused merge or a cache fallback is not a
  // fresh "this is the latest history" claim: rows beyond its end may still
  // exist upstream. Publishers pass false for those so the reload-sync
  // effect merges instead of hard-resetting the thread.
  authoritative = true,
  olderWindow = false,
): EngineState {
  const messages = historyToMessages(
    history,
    pendingPermissions,
    lastTurnError,
    historyStartIndex,
    sessionId,
  );
  return {
    sessionId,
    conversationUid,
    sessions,
    messages,
    synced: {
      history: historyStartIndex + history.length,
      start: historyStartIndex,
      messages: messages.length,
      ...(authoritative ? {} : { authoritative: false }),
      ...(olderWindow ? { olderWindow: true } : {}),
    },
  };
}
