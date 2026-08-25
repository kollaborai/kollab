import {
  AssistantRuntimeProvider,
  type AssistantTransportConnectionMetadata,
  type ThreadMessageLike,
  unstable_createMessageConverter as createMessageConverter,
  useAssistantTransportRuntime,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import type { ReactNode } from "react";
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
  const messages: ThreadMessageLike[] = history
    .filter(
      (message) =>
        (message.role === "user" || message.role === "assistant") &&
        !isToolOutputBatch(message),
    )
    .map((message, index): ThreadMessageLike => ({
      id: `history-${index}`,
      role: message.role as "user" | "assistant",
      content: message.content || "",
      ...(message.role === "assistant"
        ? { status: { type: "complete", reason: "stop" } }
        : {}),
    }));

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

  const aui = useAui();
  return (
    <AssistantRuntimeProvider aui={aui} runtime={runtime}>
      <PermissionToolUI />
      {children}
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
