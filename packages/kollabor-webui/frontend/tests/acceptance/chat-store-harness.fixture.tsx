import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import type { ChatRow, ChatStreamEvent, InputInfo, TurnInfo } from "../../src/chat-stream";
import { ChatStoreProvider, LiveChatStore, type ChatTransport, useChatStore } from "../../src/chat-store";
import { useChatRuntime } from "../../src/chat-runtime";
import { PermissionResponseContext, type PermissionArgs, type PermissionResult } from "../../src/components/PermissionTool";
import { Thread } from "../../src/components/Thread";
import "../../src/styles.css";

type RequestRecord = { path: string; method: string; body: unknown };
type PermissionResponseRecord = { toolCallId: string; args: PermissionArgs; result: PermissionResult };

declare global {
  interface Window {
    chatHarness: {
      requests: RequestRecord[];
      streamConnects: number;
      threadMounts: number;
      permissionResponses: PermissionResponseRecord[];
      publish: (type: string, data: unknown) => void;
    };
  }
}

const CONVERSATION_UID = "acceptance-conversation";
const CREATED_AT = "2026-11-25T00:00:00.000Z";

class ChatStoreTransport implements ChatTransport {
  readonly requests: RequestRecord[] = [];
  streamConnects = 0;
  private sequence = 0;
  private rows: ChatRow[] = [];
  private readonly inputs = new Map<string, InputInfo>();
  private readonly turns = new Map<string, TurnInfo>();
  private readonly listeners = new Set<(event: unknown) => void>();

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) as unknown : null;
    this.requests.push({ path, method, body });
    if (path.includes("/rows?")) {
      return new Response(JSON.stringify({ rows: [], older: null }), { status: 200 });
    }
    if (path.endsWith("/permissions")) {
      return new Response(JSON.stringify({ pending_prompts: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({ id: (body as { id?: string } | null)?.id ?? "", state: "queued" }), {
      status: 202,
    });
  }

  async streamSse<T = unknown>(
    _path: string,
    signal?: AbortSignal,
    onEvent?: (event: T) => void,
    onOpen?: () => void,
  ): Promise<void> {
    if (!onEvent) throw new Error("chat stream callback is required");
    this.streamConnects += 1;
    const receive = onEvent as (event: unknown) => void;
    this.listeners.add(receive);
    onOpen?.();
    receive({
      seq: this.sequence,
      conversation_uid: CONVERSATION_UID,
      type: "snapshot",
      data: {
        rows: [...this.rows],
        next_seq: this.sequence + 1,
        older: null,
        turns: [...this.turns.values()],
        inputs: [...this.inputs.values()],
      },
    } as T);
    if (new URLSearchParams(window.location.search).has("fail-stream")) return Promise.resolve();
    return new Promise((resolve) => {
      const close = () => {
        this.listeners.delete(receive);
        resolve();
      };
      if (signal?.aborted) close();
      else signal?.addEventListener("abort", close, { once: true });
    });
  }

  publish(type: string, data: unknown): void {
    this.sequence += 1;
    let eventData = data;
    if (type === "row.upsert") {
      const row = { ...(data as ChatRow), seq: this.sequence };
      this.rows = this.rows.some((candidate) => candidate.id === row.id)
        ? this.rows.map((candidate) => candidate.id === row.id ? row : candidate)
        : [...this.rows, row];
      eventData = row;
    } else if (type === "row.delete") {
      const id = (data as { id: string }).id;
      this.rows = this.rows.filter((row) => row.id !== id);
    } else if (type === "replace") {
      this.rows = [...(data as { rows: ChatRow[] }).rows];
    } else if (type === "input.state") {
      const input = data as InputInfo;
      this.inputs.set(input.id, input);
    } else if (type === "turn.state") {
      const turn = data as TurnInfo;
      this.turns.set(turn.turn_id, turn);
    }

    const event = {
      seq: this.sequence,
      conversation_uid: CONVERSATION_UID,
      type,
      data: eventData,
    } as ChatStreamEvent;
    for (const listener of [...this.listeners]) listener(event);
  }
}

const transport = new ChatStoreTransport();
const store = new LiveChatStore(transport, "s1", {
  retryBaseMs: 0,
  maxAttempts: new URLSearchParams(window.location.search).has("fail-stream") ? 1 : 20,
});
const permissionResponses: PermissionResponseRecord[] = [];
window.chatHarness = {
  requests: transport.requests,
  streamConnects: 0,
  threadMounts: 0,
  permissionResponses,
  publish: (type, data) => transport.publish(type, data),
};

Object.defineProperty(window.chatHarness, "streamConnects", {
  get: () => transport.streamConnects,
});

function StoreProbe() {
  const chat = useChatStore();
  const text = (row: ChatRow) => row.parts
    .filter((part): part is Extract<ChatRow["parts"][number], { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("");
  const users = chat.rows.filter((row) => row.role === "user").map(text).join("|");
  const assistant = chat.rows.filter((row) => row.role === "assistant").map(text).join("|");
  return (
    <div aria-hidden="true" className="hidden">
      <div id="connection">{chat.connection}</div>
      <div id="running">{chat.running ? "running" : "idle"}</div>
      <div id="users">{users}</div>
      <div id="assistant">{assistant}</div>
      <div id="ids">{chat.rows.map((row) => row.id).join("|")}</div>
      <div id="input-states">{JSON.stringify(chat.inputs)}</div>
      <div id="created-at">{CREATED_AT}</div>
    </div>
  );
}

function RuntimeRoot() {
  const runtime = useChatRuntime(store);
  const respond = async (toolCallId: string, args: PermissionArgs, result: PermissionResult) => {
    permissionResponses.push({ toolCallId, args, result });
    return "answered" as const;
  };
  useEffect(() => {
    window.chatHarness.threadMounts += 1;
  }, []);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <PermissionResponseContext.Provider value={respond}>
        <Thread sessionId="s1" identity="acceptance" />
      </PermissionResponseContext.Provider>
    </AssistantRuntimeProvider>
  );
}

function Fixture() {
  useEffect(() => {
    store.start();
    return () => store.dispose();
  }, []);
  return (
    <ChatStoreProvider store={store}>
      <RuntimeRoot />
      <StoreProbe />
    </ChatStoreProvider>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("acceptance fixture root is missing");
createRoot(root).render(<Fixture />);
