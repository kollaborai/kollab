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
    /** How many times the page asked the engine to stop the agent's turn. */
    cancels: number;
  }
}
type Transport = { request: { state?: EngineState }; push: (frame: string) => void; close: () => void; aborted: boolean };
window.transports = [];
window.cancels = 0;

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
    push: (frame) => stream.enqueue(encoder.encode(frame)),
    close: () => stream.close(),
    aborted: false,
  };
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
  cancel: async () => { window.cancels += 1; },
  cancelOwned: async () => { window.cancels += 1; },
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

function Probe() {
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
      <button id="send-more" onClick={() => aui.thread().append("again")}>send again</button>
      <button id="stop" onClick={() => aui.thread().cancelRun()}>stop</button>
      <div id="running">{running ? "running" : "idle"}</div>
      <div id="assistant">{assistant}</div>
      <div id="users">{users}</div>
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
        <Probe />
        {realChat ? <Thread commands={commands} attachmentsEnabled={false} /> : null}
      </EngineRuntimeProvider>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
