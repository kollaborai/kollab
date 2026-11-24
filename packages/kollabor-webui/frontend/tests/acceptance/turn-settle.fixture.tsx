import { ThreadPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { EngineApi, SessionEvent } from "../../src/api";
import { Thread } from "../../src/components/assistant-ui/thread";
import { EngineRuntimeProvider, type EngineState } from "../../src/runtime";

declare global {
  interface Window {
    setHistory: (messages: EngineState["messages"], window?: { start: number; total: number }) => void;
    liveEvent: (event: SessionEvent) => void;
    transport?: Transport;
    /** Every request the page sent, oldest first. */
    transports: Transport[];
    /** Busy-composer messages admitted to the synthetic daemon queue. */
    queueAdmissions: Array<{ text: string; sendNow: boolean }>;
    /** How many times the page asked the engine to stop the agent's turn. */
    cancels: number;
    /** Wire-level request and turn ordering for Send Now acceptance. */
    order: string[];
  }
}
type Transport = { request: { state?: EngineState; commands?: Array<{ message?: unknown }> }; push: (frame: string) => void; close: () => void; aborted: boolean };
window.transports = [];
window.queueAdmissions = [];
window.cancels = 0;
window.order = [];

// The page's send streams from here: the test reads what the request sent,
// pushes the engine's frames one at a time and ends the stream (an engine
// holds it open between its final state and the finish while it refreshes
// history).
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  if (!String(input instanceof Request ? input.url : input).endsWith("/sessions/s1/assistant")) {
    return realFetch(input, init);
  }
  const encoder = new TextEncoder();
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start: (controller) => { stream = controller; },
    cancel: () => { transport.aborted = true; },
  });
  const transport: Transport = {
    request: JSON.parse(String(init?.body ?? "{}")),
    push: (frame) => {
      for (const line of frame.split(/\r?\n/)) {
        if (!line.startsWith("data:")) continue;
        try {
          const event = JSON.parse(line.slice(5).trim());
          if (event?.type === "turn_complete" || event?.type === "turn_cancelled") {
            window.order.push(`terminal:${event.request_id ?? event.request_ids?.[0] ?? "unnamed"}`);
          }
        } catch { /* non-JSON assistant-transport frames are not turn terminals */ }
      }
      stream.enqueue(encoder.encode(frame));
    },
    close: () => stream.close(),
    aborted: false,
  };
  const commandText = (command: { message?: unknown }) => {
    const message = command.message;
    if (typeof message === "string") return message;
    if (!message || typeof message !== "object") return "";
    const parts = (message as { parts?: unknown }).parts;
    return Array.isArray(parts)
      ? parts.filter((part): part is { text: string } =>
        !!part && typeof part === "object" && (part as { type?: unknown }).type === "text" &&
        typeof (part as { text?: unknown }).text === "string")
        .map((part) => part.text).join("")
      : "";
  };
  window.order.push(`post:${(transport.request.commands ?? []).map(commandText).join("|")}`);
  // As a real fetch does: a request the page lets go ends its stream, and a
  // stream the page stops reading lets its request go.
  init?.signal?.addEventListener("abort", () => {
    transport.aborted = true;
    try { stream.error(init.signal?.reason); } catch { /* already closed */ }
  });
  window.transport = transport;
  window.transports.push(transport);
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
};

// Synthetic: a fake API, no engine and no provider (#1358). The browser test
// answers the page's send with the frames the engine streams, and plays App's
// history reloads through setHistory.
const api = {
  assistantUrl: (sessionId: string) => `/sessions/${sessionId}/assistant`,
  refreshToken: async () => false,
  getToken: () => null,
  getSessionState: async () => ({}),
  cancel: async () => { window.cancels += 1; window.order.push("cancel-ack"); },
  cancelOwned: async () => { window.cancels += 1; window.order.push("cancel-ack"); },
} as unknown as EngineApi;

const initialState: EngineState = {
  sessionId: "s1", sessions: [], messages: [], synced: { history: 0, messages: 0 },
};

// Another client's turn this page watches streams through App's events.
const liveEvents: { current: ((event: SessionEvent) => void) | null } = { current: null };
window.liveEvent = (event) => liveEvents.current?.(event);

const texts = (role: string) => (s: { thread: { messages: readonly { role: string; content: readonly { type: string; text?: string }[] }[] } }) =>
  s.thread.messages
    .filter((message) => message.role === role)
    .map((message) => message.content.map((part) => (part.type === "text" ? part.text : "")).join(""))
    .join("|");

// `?chat` draws the real chat under the probe, for a scenario that types
// into the real message box. `/config` gives the slash menu a row to pick.
const realChat = new URLSearchParams(window.location.search).has("chat");
// `?harness`: the session runs on a harness (Codex CLI, Claude Code).
const sessionKind: Record<string, unknown> = new URLSearchParams(window.location.search).has("harness")
  ? { harness: true } : {};
const commands = [{ name: "config", description: "Settings" }];

// A message with a sibling branch: the "2 / 2" picker the chat would draw.
function Branched() {
  const branches = useAuiState((s) => s.message.branchCount);
  return branches > 1 ? <span className="branched">{branches}</span> : null;
}

function Probe({ queued }: { queued: readonly string[] }) {
  const aui = useAui();
  const running = useAuiState((s) => s.thread.isRunning);
  const assistant = useAuiState(texts("assistant"));
  const users = useAuiState(texts("user"));
  // Every tool call the chat draws, by id: a call drawn twice shows twice.
  const tools = useAuiState((s) => s.thread.messages.flatMap((message) => message.role !== "assistant" ? []
    : message.content.flatMap((part) => (part.type === "tool-call" ? [part.toolCallId] : []))).join("|"));
  // Replies the chat would draw an error box under.
  const errors = useAuiState((s) => s.thread.messages.filter((message) =>
    message.role === "assistant" && message.status?.type === "incomplete" && message.status.reason === "error").length);
  // Every row the chat draws, by id: the chat takes down a row whose id changes and builds it again.
  const ids = useAuiState((s) => s.thread.messages.map((message) => message.id).join("|"));
  return (
    <div>
      <button id="send" onClick={() => aui.thread().append("hello")}>send</button>
      <button id="send-what" onClick={() => aui.thread().append("what")}>send what</button>
      <button id="send-later" onClick={() => aui.thread().append("later")}>send later</button>
      <button id="stop" onClick={() => aui.thread().cancelRun()}>stop</button>
      <div id="running">{running ? "running" : "idle"}</div>
      <div id="assistant">{assistant}</div>
      <div id="users">{users}</div>
      <div id="queued">{queued.length ? `Queued ${queued.join("|")}` : ""}</div>
      <div id="errors">{errors}</div>
      <div id="tools">{tools}</div>
      <div id="ids">{ids}</div>
      <div id="branches"><ThreadPrimitive.Messages>{() => <Branched />}</ThreadPrimitive.Messages></div>
    </div>
  );
}

function Fixture() {
  const [state, setState] = useState(initialState);
  const [remounts, setRemounts] = useState(0);
  const [queued, setQueued] = useState<string[]>([]);
  // `window` says the rows are only the newest of a longer history: its first
  // row's position and the history's row count.
  window.setHistory = (messages, window) => setState({
    ...initialState, messages,
    synced: window ? { history: window.total, start: window.start, messages: messages.length }
      : { history: messages.length, messages: messages.length },
  });
  return (
    <>
      <div id="remounts">{remounts}</div>
      <EngineRuntimeProvider
        key={remounts}
        api={api}
        sessionId="s1"
        initialState={state}
        onStale={() => setRemounts((count) => count + 1)}
        liveEvents={liveEvents}
        {...sessionKind}
      >
        <Probe queued={queued} />
        {realChat ? <Thread
          commands={commands}
          attachmentsEnabled={false}
          onQueueBusyMessage={async ({ text, sendNow }) => {
            window.queueAdmissions.push({ text, sendNow });
            setQueued((current) => [...current, text]);
            return {
              admitted: true,
              batchId: "fixture-batch",
              requestId: `fixture-${window.queueAdmissions.length}`,
              operationId: `operation-${window.queueAdmissions.length}`,
              scopeId: "fixture-scope",
              incarnation: "fixture-incarnation",
              planKey: `fixture-plan-${window.queueAdmissions.length}`,
              sendNowStatus: sendNow ? "not-accepted" : "not-requested",
            };
          }}
        /> : null}
      </EngineRuntimeProvider>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
