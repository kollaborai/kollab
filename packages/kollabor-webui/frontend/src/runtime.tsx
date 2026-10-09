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
import { useEffect, useRef, useState, type ReactNode } from "react";
import type {
  EngineApi,
  HistoryMessage,
  PermissionPrompt,
  Session,
  TurnError,
} from "./api";
import { isToolOutputBatch } from "./api";
import { splitAgentHud, type HubNote } from "./hub-notes";
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
  sessions: Session[];
  messages: ThreadMessageLike[];
  /**
   * The first `messages` entries show the daemon history's first `history`
   * messages. Set when the history loads, and by the engine when a turn this
   * page ran ends; a later reload appends only what lies past it.
   */
  synced?: { history: number; messages: number };
  error?: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
    toolCalls: number;
    stopReason?: string;
  };
};

const messageConverter = createMessageConverter<ThreadMessageLike>((message) => message);
const imageAttachmentAdapter = new SimpleImageAttachmentAdapter();

function historyContentToThreadContent(content: unknown): ThreadMessageLike["content"] {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";

  let imageIndex = 0;
  const parts: Array<TextMessagePart | ImageMessagePart> = [];
  for (const rawPart of content) {
    if (!rawPart || typeof rawPart !== "object") continue;
    const part = rawPart as Record<string, unknown>;
    if (part.type === "text" && typeof part.text === "string") {
      parts.push({ type: "text", text: part.text });
      continue;
    }
    if (part.type !== "image") continue;
    imageIndex += 1;

    const source =
      part.source && typeof part.source === "object"
        ? (part.source as Record<string, unknown>)
        : undefined;
    const image =
      typeof part.image === "string"
        ? part.image
        : source?.kind === "url" && typeof source.url === "string"
          ? source.url
          : undefined;
    if (image) {
      parts.push({ type: "image", image });
    } else {
      parts.push({ type: "text", text: `[image${imageIndex}]` });
    }
  }
  return parts.length ? parts : "";
}

/**
 * A user turn's hub messages (the agent HUD the daemon puts on top of it, see
 * hub-notes.ts) and what is left: what the user typed. The HUD rides on the
 * first text part.
 */
function withHubNotes(content: ThreadMessageLike["content"]): {
  notes: HubNote[];
  content: ThreadMessageLike["content"];
} {
  if (typeof content === "string") {
    const { notes, rest } = splitAgentHud(content);
    return { notes, content: rest };
  }
  const first = content.findIndex((part) => part.type === "text");
  if (first < 0) return { notes: [], content };
  const { notes, rest } = splitAgentHud((content[first] as TextMessagePart).text);
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
): ThreadMessageLike[] {
  type RestoredToolCall = {
    type: "tool-call";
    toolCallId: string;
    toolName: string;
    args: ReadonlyJSONObject;
    argsText: string;
    result?: unknown;
    isError?: boolean;
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

    return rawCalls.flatMap((rawCall, callIndex) => {
      const call = asRecord(rawCall);
      if (!call) return [];
      const functionValue = asRecord(call.function);
      const toolCallId =
        asString(call.id) ||
        asString(call.tool_call_id) ||
        `history-${sourceIndex}-tool-${callIndex}`;
      const toolName =
        asString(functionValue?.name) ||
        asString(call.name) ||
        asString(call.tool_name) ||
        "tool";
      const rawArguments =
        functionValue?.arguments ?? call.arguments ?? call.input ?? call.args;
      const { args, argsText } = parseArguments(rawArguments ?? {});
      return [{
        type: "tool-call",
        toolCallId,
        toolName,
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
    const status = { type: "incomplete", reason: "error", error: turnError.message } as const;
    const reply = firstReply === undefined ? undefined : messages[firstReply];
    if (firstReply !== undefined && reply) messages[firstReply] = { ...reply, status };
    else {
      messages.push({
        id: `history-${turnError.history_length}-error`,
        role: "assistant",
        content: "",
        status,
      });
    }
  };

  history.forEach((message, sourceIndex) => {
    if (sourceIndex === turnError?.history_length) showTurnError();
    const metadata = asRecord(message.metadata);
    if (
      metadata?.context_compaction === true ||
      metadata?.context_compaction === "true"
    ) {
      messages.push({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content: compactionMessage(message),
        status: { type: "complete", reason: "stop" },
      });
      return;
    }

    if (isToolOutputBatch(message)) return;

    if (message.role === "user") {
      // Before the HUD check: a status-only turn still ends the one before.
      closeTurn();
      turnStartedAt = message.timestamp;
      const { notes, content } = withHubNotes(
        historyContentToThreadContent(message.content),
      );
      // What other agents sent this one, as messages from their gems.
      notes.forEach((note, noteIndex) => {
        messages.push({
          id: `history-${sourceIndex}-hub-${noteIndex}`,
          role: "system",
          content: note.text,
          metadata: { custom: { hub: note } },
        });
      });
      // A turn that was only agent status stays in the Trajectory tab.
      if (!content.length) return;
      messages.push({
        id: `history-${sourceIndex}`,
        role: "user",
        content,
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
        messages.push({
          id: `history-${sourceIndex}`,
          role: "assistant",
          content,
          status: { type: "complete", reason: "stop" },
        });
        firstReply ??= messages.length - 1;
        finalReplyAt = message.timestamp;
        return;
      }
      // A step with tool calls; the turn's final reply comes after it.
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
      messages.push({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content,
        status: { type: "complete", reason: "stop" },
      });
      firstReply ??= messages.length - 1;
      return;
    }

    if (message.role === "tool") {
      const callId = toolResultId(message);
      const existingCall = callId ? callsById.get(callId) : undefined;
      if (existingCall) {
        existingCall.result = historyContentToThreadContent(message.content);
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
        result: historyContentToThreadContent(message.content),
        isError: toolResultIsError(message),
      };
      messages.push({
        id: `history-${sourceIndex}`,
        role: "assistant",
        content: [fallbackCall],
        status: { type: "complete", reason: "stop" },
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
          })),
          argsText: JSON.stringify(prompt),
        },
      ],
      status: { type: "requires-action", reason: "tool-calls" },
    } satisfies ThreadMessageLike);
  }
  return messages;
}

const converter = (
  state: EngineState,
  metadata: AssistantTransportConnectionMetadata,
) => {
  // Stream timing rides on the assistant message's metadata, where
  // `useMessageTiming` reads it (see turn-timing.ts).
  const stored = state.messages.map((message) => {
    const timing =
      message.role === "assistant" && message.id
        ? turnTimings.get(message.id)
        : undefined;
    return timing
      ? { ...message, metadata: { ...message.metadata, timing } }
      : message;
  });
  const optimistic = metadata.pendingCommands.flatMap((command) => {
    if (command.type !== "add-message") return [];
    const parts: Array<TextMessagePart | ImageMessagePart> = command.message.parts.map(
      (part) =>
        part.type === "text"
          ? { type: "text" as const, text: part.text }
          : { type: "image" as const, image: part.image },
    );
    return [
      {
        role: "user" as const,
        content: parts,
        metadata: { isOptimistic: true },
      } satisfies ThreadMessageLike,
    ];
  });

  return {
    // assistant-stream requires JSON state; EngineState is JSON-compatible at
    // runtime, while ThreadMessageLike's rich union is intentionally narrower
    // than the transport's structural JSON type.
    state: JSON.parse(JSON.stringify(state)),
    messages: messageConverter.toThreadMessages(
      [...stored, ...optimistic],
      metadata.isSending,
      { error: state.error },
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
  initialState,
  onStale,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  initialState: EngineState;
  /** Reload the thread whole: the prompt it waits on closed in another window. */
  onStale?: () => void;
  children: ReactNode;
}) {
  const turnRef = useRef<TurnClock>(newTurnClock());
  const runStartRef = useRef(0);

  const runtime = useAssistantTransportRuntime({
    initialState,
    api: api.assistantUrl(sessionId),
    protocol: "assistant-transport",
    converter,
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
      // A new message starts a new turn; a tool answer continues the last one.
      if (body.commands.some((command) => command.type === "add-message")) {
        turnRef.current = newTurnClock();
      }
      runStartRef.current = performance.now();
      return { ...body, sessionId };
    },
    onResponse: (response) => {
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
      updateState((state) => ({ ...state, error: error.message }));
    },
    onCancel: ({ updateState, error }) => {
      // A failed request lands here too, after onError: keep the error it
      // set, and leave the daemon's turn alone (a dropped phone connection
      // must not stop the agent's work).
      if (error) return;
      // The transport aborts its request, but the daemon is independent of
      // that HTTP stream. Explicitly cancel its active turn as well.
      void api.cancel(sessionId).catch(() => undefined);
      updateState((state) => ({ ...state, error: "Run cancelled" }));
    },
  });

  // Turns this page did not run (a hub message that woke the agent, a turn
  // typed in the terminal) reach the open thread when App reloads the history
  // after them. Only the history past what the thread shows is appended:
  // assistant-ui keeps every message it was given, so swapping in reloaded
  // copies of turns already on screen would show those turns twice.
  const reloaded = useRef(initialState.messages);
  useEffect(() => {
    if (initialState.messages === reloaded.current) return;
    reloaded.current = initialState.messages;
    const thread = runtime.thread.getState();
    // A permission prompt opened or closed in another window (the agent's
    // terminal): a run that ends at a prompt leaves no sync point to append
    // from, so when the thread and the reloaded history disagree on whether a
    // prompt is waiting, reload the thread whole.
    const waiting = (message?: { status?: { type?: string } }) => message?.status?.type === "requires-action";
    if (!thread.isRunning && waiting(thread.messages.at(-1)) !== initialState.messages.some(waiting)) {
      onStale?.();
      return;
    }
    const shown = (thread.extras as { state?: EngineState } | undefined)?.state;
    const from = shown?.synced;
    // A run that ended without a sync point (an older engine, a dropped
    // stream) leaves the thread to its next full load.
    if (thread.isRunning || !shown || !from || shown.messages.length !== from.messages) return;
    const tail = initialState.messages.filter((message) => {
      const index = /^history-(\d+)/.exec(String(message.id ?? ""))?.[1];
      return index !== undefined && Number(index) >= from.history;
    });
    if (!tail.length || !initialState.synced) return;
    const messages = [...shown.messages, ...tail];
    runtime.thread.importExternalState({
      ...shown,
      messages,
      synced: { history: initialState.synced.history, messages: messages.length },
    });
  }, [initialState, runtime, onStale]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <InitialMessagesGate
        runtime={runtime}
        messages={initialState.messages}
      >
        <PermissionToolUI />
        <VoiceModeProvider api={api} sessionId={sessionId}>
          {children}
        </VoiceModeProvider>
      </InitialMessagesGate>
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
): EngineState {
  const messages = historyToMessages(history, pendingPermissions, lastTurnError);
  return {
    sessionId,
    sessions,
    messages,
    synced: { history: history.length, messages: messages.length },
  };
}
