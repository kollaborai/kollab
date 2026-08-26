import {
  AssistantRuntimeProvider,
  type AssistantTransportConnectionMetadata,
  type ThreadMessageLike,
  unstable_createMessageConverter as createMessageConverter,
  useAssistantTransportRuntime,
  useAuiState,
} from "@assistant-ui/react";
import type { ReadonlyJSONObject } from "assistant-stream/utils";
import { useEffect, useState, type ReactNode } from "react";
import type {
  EngineApi,
  HistoryMessage,
  PermissionPrompt,
  Session,
} from "./api";
import { isToolOutputBatch } from "./api";
import { PermissionToolUI } from "./components/PermissionTool";

export type EngineState = {
  sessionId: string;
  sessions: Session[];
  messages: ThreadMessageLike[];
  error?: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
    toolCalls: number;
    stopReason?: string;
  };
};

const messageConverter = createMessageConverter<ThreadMessageLike>((message) => message);

function historyToMessages(
  history: HistoryMessage[],
  pendingPermissions: PermissionPrompt[],
): ThreadMessageLike[] {
  type RestoredToolCall = {
    type: "tool-call";
    toolCallId: string;
    toolName: string;
    args: ReadonlyJSONObject;
    argsText: string;
    result?: unknown;
    isError?: boolean;
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

  const messages: ThreadMessageLike[] = [];
  const callsById = new Map<string, RestoredToolCall>();

  history.forEach((message, sourceIndex) => {
    if (isToolOutputBatch(message)) return;

    if (message.role === "user") {
      messages.push({
        id: `history-${sourceIndex}`,
        role: "user",
        content: message.content || "",
      });
      return;
    }

    if (message.role === "assistant") {
      const calls = restoredCalls(message, sourceIndex);
      if (!calls.length) {
        messages.push({
          id: `history-${sourceIndex}`,
          role: "assistant",
          content: message.content || "",
          status: { type: "complete", reason: "stop" },
        });
        return;
      }

      const content: Array<
        | { type: "text"; text: string }
        | RestoredToolCall
      > = [];
      if (message.content) content.push({ type: "text", text: message.content });
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
      return;
    }

    if (message.role === "tool") {
      const callId = toolResultId(message);
      const existingCall = callId ? callsById.get(callId) : undefined;
      if (existingCall) {
        existingCall.result = message.content || "";
        existingCall.isError = toolResultIsError(message);
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
        result: message.content || "",
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
  const optimistic = metadata.pendingCommands.flatMap((command) => {
    if (command.type !== "add-message") return [];
    const text = command.message.parts
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("\n");
    return [
      {
        role: "user" as const,
        content: text,
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
      [...state.messages, ...optimistic],
      metadata.isSending,
      { error: state.error },
    ),
    isRunning: metadata.isSending,
  };
};

type ThreadReadyRuntime = {
  thread: {
    getState: () => { messages: readonly ThreadMessageLike[] };
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
    let attempts = 0;
    let retry: number | undefined;
    const hydrate = () => {
      if (
        runtime.thread.getState().messages.length >= messages.length ||
        attempts >= 20
      ) {
        // Give the provider's assistant-ui adapter one commit to publish the
        // bound thread state before Thread reads its empty-state selector.
        retry = window.setTimeout(() => setReady(true), 50);
        return;
      }
      attempts += 1;
      retry = window.setTimeout(hydrate, 10);
    };

    // The remote thread runtime is bound by AssistantRuntimeProvider after
    // this component mounts. Wait for that binding before mounting Thread so
    // its initial empty-state selector cannot stick after a full reload.
    hydrate();
    return () => {
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [messages.length, runtime]);

  return ready ? children : null;
}

export function EngineRuntimeProvider({
  api,
  sessionId,
  initialState,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  initialState: EngineState;
  children: ReactNode;
}) {
  const runtime = useAssistantTransportRuntime({
    initialState,
    api: api.assistantUrl(sessionId),
    protocol: "assistant-transport",
    converter,
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
    prepareSendCommandsRequest: (body) => ({
      ...body,
      sessionId,
    }),
    onResponse: (response) => {
      if (response.status === 401) void api.refreshToken();
    },
    onError: async (error, { updateState }) => {
      updateState((state) => ({ ...state, error: error.message }));
    },
    onCancel: ({ updateState }) => {
      // The transport aborts its request, but the daemon is independent of
      // that HTTP stream. Explicitly cancel its active turn as well.
      void api.cancel(sessionId).catch(() => undefined);
      updateState((state) => ({ ...state, error: "Run cancelled" }));
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <InitialMessagesGate
        runtime={runtime}
        messages={initialState.messages}
      >
        <PermissionToolUI />
        {children}
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
): EngineState {
  return {
    sessionId,
    sessions,
    messages: historyToMessages(history, pendingPermissions),
  };
}
