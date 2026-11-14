import { useAui, useAuiState } from "@assistant-ui/react";
import { createRoot } from "react-dom/client";
import type { EngineApi, SessionEvent } from "../../src/api";
import { EngineRuntimeProvider, type EngineState } from "../../src/runtime";

declare global {
  interface Window {
    liveEvent: (event: SessionEvent) => void;
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

const initialState: EngineState = { sessionId: "s1", sessions: [], messages: [] };
const liveEvents: { current: ((event: SessionEvent) => void) | null } = { current: null };
window.liveEvent = (event) => liveEvents.current?.(event);

function Probe() {
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
      <div id="running">{running ? "running" : "idle"}</div>
      <div id="assistant">{text}</div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <EngineRuntimeProvider api={api} sessionId="s1" initialState={initialState} liveEvents={liveEvents}>
    <Probe />
  </EngineRuntimeProvider>,
);
