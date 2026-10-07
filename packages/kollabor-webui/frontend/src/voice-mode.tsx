import { useAui, useAuiState } from "@assistant-ui/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { EngineApi } from "./api";

export type VoiceMode = {
  /** A /voicemode command is in flight; the mic holds still until it settles. */
  pending: boolean;
  /** The last /voicemode on reported success and no later off did. */
  on: boolean;
  toggle: () => void;
};

const IDLE: VoiceMode = { pending: false, on: false, toggle: () => undefined };

const VoiceModeContext = createContext<VoiceMode>(IDLE);

export const useVoiceMode = () => useContext(VoiceModeContext);

/**
 * The composer mic is a /voicemode toggle. The command goes out as a normal
 * chat turn, the same path a typed slash command takes, and the daemon runs it
 * (LocalStateService._run_web_slash_command). Success is read from the saved
 * reply's `command_success` in history. That flag means the plugin accepted the
 * request: start_voice() answers "Voice Starting" and finishes setup in the
 * background, and no engine route exposes VoicePlugin.status(), so the web
 * cannot see later transitions (a service that fails after starting). Toggling
 * again is self-correcting: "on" while already on answers success, as does "off"
 * while already off.
 */
export function VoiceModeProvider({
  api,
  sessionId,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  children: ReactNode;
}) {
  const aui = useAui();
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const [on, setOn] = useState(false);
  const [pending, setPending] = useState<"on" | "off" | null>(null);
  const sawRun = useRef(false);

  const toggle = useCallback(() => {
    if (pending || isRunning) return;
    const action = on ? "off" : "on";
    sawRun.current = false;
    setPending(action);
    aui.thread.append(`/voicemode ${action}`);
  }, [aui, isRunning, on, pending]);

  // The command is a chat turn: once its run has started and ended, its reply
  // is in history.
  useEffect(() => {
    if (!pending) return;
    if (isRunning) {
      sawRun.current = true;
      return;
    }
    if (!sawRun.current) return;
    let cancelled = false;
    void (async () => {
      let ok = false;
      try {
        const { history } = await api.getHistory(sessionId);
        const reply = [...(history ?? [])].reverse().find((message) => {
          const meta = (message.metadata ?? {}) as Record<string, unknown>;
          return (
            meta.slash_command === `/voicemode ${pending}` &&
            typeof meta.command_success === "boolean"
          );
        });
        ok =
          (reply?.metadata as Record<string, unknown> | undefined)
            ?.command_success === true;
      } catch {
        // Unreadable history counts as unconfirmed: the mic stays where it was.
      }
      if (cancelled) return;
      if (ok) setOn(pending === "on");
      setPending(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [api, isRunning, pending, sessionId]);

  // A command whose run never started must not leave the mic stuck.
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setPending(null), 15_000);
    return () => window.clearTimeout(timer);
  }, [pending]);

  const value = useMemo(
    () => ({ pending: pending !== null, on, toggle }),
    [on, pending, toggle],
  );
  return (
    <VoiceModeContext.Provider value={value}>
      {children}
    </VoiceModeContext.Provider>
  );
}
