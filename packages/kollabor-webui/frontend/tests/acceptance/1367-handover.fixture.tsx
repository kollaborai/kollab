import { ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import { EngineApi, type PermissionPrompt, type SessionEvent } from "../../src/api";
import { Thread } from "../../src/components/assistant-ui/thread";
import { buildInitialState, EngineRuntimeProvider, type EngineState } from "../../src/runtime";

type AssistantTransport = {
  request: { commands?: Array<{ type?: string; message?: { id?: string; parts?: Array<{ text?: string }> } }>; state?: EngineState };
  push: (frame: string) => void;
  close: () => void;
  aborted: boolean;
  closed: boolean;
  pushCount: number;
};

type SessionEventTransport = {
  push: (event: SessionEvent) => void;
  close: () => void;
  closed: boolean;
  pushCount: number;
};

declare global {
  interface Window {
    transports: AssistantTransport[];
    transport?: AssistantTransport;
    sessionEventTransport?: SessionEventTransport;
    sessionEventOpen: boolean;
    sessionEventsDelivered: SessionEvent[];
    mockEngineRequests: string[];
    refreshPermissions: (prompts: PermissionPrompt[]) => void;
    switchSession: (sessionId: string) => void;
  }
}

window.transports = [];
window.sessionEventOpen = false;
window.sessionEventsDelivered = [];
window.mockEngineRequests = [];

// The fixture exposes two distinct HTTP response bodies: the assistant POST
// transport and the session GET /events stream. The latter is consumed by the
// real EngineApi.streamEvents parser below; it never calls runtime callbacks
// directly from the browser test.
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = input instanceof Request ? input.url : String(input);
  const pathname = new URL(url, window.location.origin).pathname;
  window.mockEngineRequests.push(pathname);
  const encoder = new TextEncoder();

  const eventsSession = /^\/sessions\/([^/]+)\/events$/.exec(pathname)?.[1];
  if (eventsSession) {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => { stream = controller; },
      cancel: () => { if (window.sessionEventTransport) window.sessionEventTransport.closed = true; },
    });
    window.sessionEventTransport = {
      push: (event) => {
        const payload = { ...event, session_id: eventsSession };
        stream.enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`));
        window.sessionEventTransport!.pushCount += 1;
      },
      close: () => {
        if (window.sessionEventTransport?.closed) return;
        window.sessionEventTransport!.closed = true;
        stream.close();
      },
      closed: false,
      pushCount: 0,
    };
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  }

  if (!/^\/sessions\/[^/]+\/assistant$/.test(pathname)) return realFetch(input, init);

  let stream!: ReadableStreamDefaultController<Uint8Array>;
  let transport!: AssistantTransport;
  const body = new ReadableStream<Uint8Array>({
    start: (controller) => { stream = controller; },
    cancel: () => { transport.aborted = true; },
  });
  transport = {
    request: JSON.parse(String(init?.body ?? "{}")),
    push: (frame) => {
      transport.pushCount += 1;
      stream.enqueue(encoder.encode(frame));
    },
    close: () => {
      if (transport.closed) return;
      transport.closed = true;
      stream.close();
    },
    aborted: false,
    closed: false,
    pushCount: 0,
  };
  init?.signal?.addEventListener("abort", () => {
    transport.aborted = true;
    try { stream.error(init.signal?.reason); } catch { /* already closed */ }
  });
  window.transport = transport;
  window.transports.push(transport);
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
};

const api = new EngineApi(window.location.origin);
// Pin the EngineApi to Vite's local origin without requesting a host engine's
// /api/config or credentials. streamEvents and its SSE parser remain real.
api.loadConfig = async () => null;
api.refreshToken = async () => false;
const initialState: EngineState = {
  sessionId: "s1", sessions: [], messages: [], synced: { history: 0, messages: 0 },
};
const liveEvents: { current: ((event: SessionEvent) => void) | null } = { current: null };

function Probe() {
  const running = useAuiState((state) => state.thread.isRunning);
  const assistant = useAuiState((state) => state.thread.messages
    .filter((message) => message.role === "assistant")
    .map((message) => message.content
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("")
    ).join("|"));
  const tools = useAuiState((state) => state.thread.messages.flatMap((message) =>
    message.role !== "assistant" ? [] : message.content.flatMap((part) =>
      part.type === "tool-call" ? [part.toolCallId] : []
    )
  ).join("|"));
  const ids = useAuiState((state) => state.thread.messages.map((message) => message.id).join("|"));

  return (
    <>
      <div id="running">{running ? "running" : "idle"}</div>
      <div id="assistant">{assistant}</div>
      <div id="tools">{tools}</div>
      <div id="ids">{ids}</div>
      <div id="branches"><ThreadPrimitive.Messages>{() => <BranchProbe />}</ThreadPrimitive.Messages></div>
      <Thread commands={[]} attachmentsEnabled={false} />
    </>
  );
}

function BranchProbe() {
  const branches = useAuiState((state) => state.message.branchCount);
  return branches > 1 ? <span className="branched">{branches}</span> : null;
}

function Fixture() {
  const [sessionId, setSessionId] = useState("s1");
  const [state, setState] = useState(initialState);
  window.refreshPermissions = (prompts) => setState(buildInitialState(
    sessionId, initialState.sessions, [], prompts,
  ));
  window.switchSession = (nextSessionId) => {
    window.sessionEventOpen = false;
    window.sessionEventTransport = undefined;
    setSessionId(nextSessionId);
    setState(buildInitialState(nextSessionId, [], [], []));
  };
  useEffect(() => {
    const controller = new AbortController();
    void api.streamEvents(sessionId, controller.signal, (event) => {
      window.sessionEventsDelivered.push(event);
      liveEvents.current?.(event);
    }, () => {
      window.sessionEventOpen = true;
    }).catch(() => undefined);
    return () => controller.abort();
  }, [sessionId]);

  return (
    <EngineRuntimeProvider
      key={sessionId}
      api={api}
      sessionId={sessionId}
      initialState={state}
      liveEvents={liveEvents}
    >
      <Probe />
    </EngineRuntimeProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
