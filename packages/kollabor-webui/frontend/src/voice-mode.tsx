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

/** VoicePlugin._activity, polled while voice is on. */
export type VoiceActivity = {
  /** listening, hearing, transcribing, speaking, or the service state. */
  stage?: string;
  /** Microphone loudness, 0..1. */
  level?: number;
  /** The last line the microphone heard. */
  heard?: string;
};

export type VoiceMode = {
  /** A /voice command is in flight; the mic holds still until it settles. */
  pending: boolean;
  /** The last /voice on reported success and no later off did. */
  on: boolean;
  toggle: () => void;
  activity: VoiceActivity | null;
};

const IDLE: VoiceMode = {
  pending: false,
  on: false,
  toggle: () => undefined,
  activity: null,
};

/** How often the composer reads the voice stage while voice is on. */
const ACTIVITY_POLL_MS = 600;

const VoiceModeContext = createContext<VoiceMode>(IDLE);

export const useVoiceMode = () => useContext(VoiceModeContext);

/**
 * The composer mic is a /voice toggle. The command goes out as a normal
 * chat turn, the same path a typed slash command takes, and the daemon runs it
 * (LocalStateService._run_web_slash_command).
 *
 * The mic's state is the daemon's own: `voice_requested` from
 * VoicePlugin.status(), carried in the session state's system snapshot. It is
 * read on mount (so a reload, or voice mode started from the terminal, shows
 * the real state), again once each toggle's run ends, and every
 * ACTIVITY_POLL_MS while voice is on, with `voice_activity`, so the composer
 * shows that the microphone hears and what voice is doing. The reply's
 * `command_success` in history is the fallback for a daemon that predates the
 * field. ponytail: voice started from the terminal shows at the next mount or
 * toggle, since nothing polls while the mic is off.
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
  const [activity, setActivity] = useState<VoiceActivity | null>(null);
  const sawRun = useRef(false);

  /** The daemon's voice_requested, or undefined when it cannot say. */
  const readRequested = useCallback(async () => {
    try {
      const requested = (await api.getSessionState(sessionId)).system
        ?.voice_requested;
      return typeof requested === "boolean" ? requested : undefined;
    } catch {
      return undefined;
    }
  }, [api, sessionId]);

  useEffect(() => {
    let cancelled = false;
    void readRequested().then((requested) => {
      if (!cancelled && requested !== undefined) setOn(requested);
    });
    return () => {
      cancelled = true;
    };
  }, [readRequested]);

  const toggle = useCallback(() => {
    if (pending || isRunning) return;
    const action = on ? "off" : "on";
    sawRun.current = false;
    setPending(action);
    aui.thread.append(`/voice ${action}`);
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
            meta.slash_command === `/voice ${pending}` &&
            typeof meta.command_success === "boolean"
          );
        });
        ok =
          (reply?.metadata as Record<string, unknown> | undefined)
            ?.command_success === true;
      } catch {
        // Unreadable history counts as unconfirmed: the mic stays where it was.
      }
      const requested = await readRequested();
      if (cancelled) return;
      if (requested !== undefined) setOn(requested);
      else if (ok) setOn(pending === "on");
      setPending(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [api, isRunning, pending, readRequested, sessionId]);

  // While voice is on, follow the service: a stopped service or /voice off from
  // the terminal turns the mic off here too.
  useEffect(() => {
    if (!on) {
      setActivity(null);
      return;
    }
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      let system: Record<string, unknown> | null | undefined;
      try {
        system = (await api.getSessionState(sessionId)).system;
      } catch {
        system = undefined;
      }
      if (cancelled) return;
      setActivity((system?.voice_activity as VoiceActivity | undefined) ?? null);
      if (system?.voice_requested === false && !pending) setOn(false);
      timer = window.setTimeout(poll, ACTIVITY_POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, on, pending, sessionId]);

  // A command whose run never started must not leave the mic stuck.
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setPending(null), 15_000);
    return () => window.clearTimeout(timer);
  }, [pending]);

  const value = useMemo(
    () => ({ pending: pending !== null, on, toggle, activity }),
    [activity, on, pending, toggle],
  );
  return (
    <VoiceModeContext.Provider value={value}>
      {children}
    </VoiceModeContext.Provider>
  );
}
