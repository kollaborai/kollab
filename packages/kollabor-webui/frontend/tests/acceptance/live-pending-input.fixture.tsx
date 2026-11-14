import { useAui, useAuiState } from "@assistant-ui/react";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { EngineApi, SessionEvent } from "../../src/api";
import { EngineRuntimeProvider, type EngineState } from "../../src/runtime";

declare global {
  interface Window {
    liveEvent: (event: SessionEvent) => void;
    refreshHistory: () => void;
  }
}

// Synthetic: a fake API, no engine and no provider. The page watches a turn it
// did not start; the browser's own submission goes to /sessions/.../assistant,
// which the harness holds open (a steering admission still pending).
const api = {
  assistantUrl: (sessionId: string) => `/sessions/${sessionId}/assistant`,
  refreshToken: async () => false,
  getToken: () => null,
  getSessionState: async () => ({}),
  cancel: async () => undefined,
  cancelOwned: async () => undefined,
} as unknown as EngineApi;

const initialState: EngineState = {
  sessionId: "s1", sessions: [], messages: [], synced: { history: 0, messages: 0 },
};
const liveEvents: { current: ((event: SessionEvent) => void) | null } = { current: null };
window.liveEvent = (event) => liveEvents.current?.(event);

function Probe({ onRefresh }: { onRefresh: () => void }) {
  const aui = useAui();
  const running = useAuiState((s) => s.thread.isRunning);
  const text = useAuiState((s) => s.thread.messages
    .filter((message) => message.role === "assistant")
    .map((message) => message.content
      .map((part) => (part.type === "text" ? part.text : ""))
      .join(""))
    .join("|"));
  return (
    <div>
      <button id="send" onClick={() => aui.thread().append("hello")}>send</button>
      <button id="refresh-history" onClick={onRefresh}>refresh history</button>
      <div id="running">{running ? "running" : "idle"}</div>
      <div id="assistant">{text}</div>
    </div>
  );
}

function Fixture() {
  const [state, setState] = useState(initialState);
  const refreshHistory = () => setState({
    ...initialState,
    messages: [{
      id: "history-r1",
      role: "assistant",
      content: [{ type: "text", text: "AlphaBeta" }],
      metadata: { custom: { requestId: "r1" } },
    }],
    synced: { history: 1, messages: 1 },
  });
  window.refreshHistory = refreshHistory;
  return (
    <EngineRuntimeProvider api={api} sessionId="s1" initialState={state} liveEvents={liveEvents}>
      <Probe onRefresh={refreshHistory} />
    </EngineRuntimeProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
