import { useEffect, useLayoutEffect, useRef } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";

// What each chat's message box holds, kept past a rebuild of its thread. The runtime reads its initial state only on
// mount, so a thread rebuilt for the same chat (the agent's history replacing the engine's saved copy, a reload after a
// stale refresh, cleared history) or reopened after another chat is a new composer that starts empty. In memory only:
// reloading the page starts clean.
const kept = new Map<string, string>();

/**
 * Renders nothing. Inside the runtime it copies the message box's text into `kept` as it changes, and puts it back when
 * a runtime for the same chat mounts with an empty box. Text the person deleted or sent is forgotten, so it never
 * comes back.
 */
export function ComposerKeeper({ sessionId }: { sessionId: string }) {
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);
  // Set when the kept text was put back on mount; the render before the store shows it still reads an empty box,
  // and writing that down would erase what was just restored.
  const restoring = useRef(false);

  useLayoutEffect(() => {
    const saved = kept.get(sessionId);
    if (!saved || aui.composer.getState().text !== "") return;
    restoring.current = true;
    aui.composer.setText(saved);
  }, [aui, sessionId]);

  useEffect(() => {
    if (restoring.current) {
      if (text === "") return;
      restoring.current = false;
    }
    if (text) kept.set(sessionId, text);
    else kept.delete(sessionId);
  }, [text, sessionId]);

  return null;
}
