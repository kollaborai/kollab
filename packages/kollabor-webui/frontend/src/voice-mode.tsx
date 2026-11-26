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
import { newInputId, type InputInfo } from "./chat-stream";
import { WEB_INPUT_PREFIX, type LiveStore } from "./chat-store";

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
  /** A rejected /voice command, shown beside the composer for retry. */
  error: string | null;
  /** The last /voice on reported success and no later off did. */
  on: boolean;
  toggle: () => void;
  activity: VoiceActivity | null;
};

const IDLE: VoiceMode = {
  pending: false,
  error: null,
  on: false,
  toggle: () => undefined,
  activity: null,
};

/** How often the composer reads the voice stage while voice is on. */
const ACTIVITY_POLL_MS = 600;

const VoiceModeContext = createContext<VoiceMode>(IDLE);

type VoiceInputStore = Pick<LiveStore, "inputs" | "subscribe">;

export const useVoiceMode = () => useContext(VoiceModeContext);

/**
 * The composer mic is a /voice toggle. The command goes out through
 * `sendCommand` as a normal chat turn, the same path a typed slash command
 * takes, and the daemon runs it (LocalStateService._run_web_slash_command).
 *
 * The mic's state is the daemon's own: `voice_requested` from
 * VoicePlugin.status(), carried in the session state's system snapshot. It is
 * read on mount (so a reload, or voice mode started from the terminal, shows
 * the real state), again once each toggle's run ends, and every
 * ACTIVITY_POLL_MS while voice is on, with `voice_activity`, so the composer
 * shows that the microphone hears and what voice is doing. A daemon that omits
 * `voice_requested` leaves the mic where it was. ponytail: voice started from
 * the terminal shows at the next mount or toggle, since nothing polls while the
 * mic is off.
 */
export function VoiceModeProvider({
  api,
  sessionId,
  running,
  store,
  sendCommand,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  /** A chat turn is in progress; the toggle waits for it to end. */
  running: boolean;
  /** The session store reports this command's queued, running and ended states. */
  store: VoiceInputStore;
  /** Sends one chat turn under the input ID supplied by the toggle. */
  sendCommand: (text: string, inputId: string) => void | Promise<void>;
  children: ReactNode;
}) {
  const [on, setOn] = useState(false);
  const [pending, setPending] = useState<"on" | "off" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<VoiceActivity | null>(null);
  const activeInput = useRef<{ id: string; started: boolean } | null>(null);

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
    if (pending || running) return;
    const action = on ? "off" : "on";
    const inputId = newInputId(WEB_INPUT_PREFIX);
    activeInput.current = { id: inputId, started: false };
    setError(null);
    setPending(action);
    void Promise.resolve()
      .then(() => sendCommand(`/voice ${action}`, inputId))
      .catch(() => {
        // A stream completion wins over a late ambiguous POST failure.
        if (activeInput.current?.id !== inputId) return;
        activeInput.current = null;
        setPending((current) => current === action ? null : current);
        setError("Could not change voice mode. Try again.");
      });
  }, [on, pending, running, sendCommand]);

  // Follow this exact input in the store. The store publishes every event
  // synchronously, so a started and ended pair in one SSE read is not lost to
  // React batching or a transient `running` render.
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    const reconcile = () => {
      const active = activeInput.current;
      if (!active) return;
      const input: InputInfo | undefined = store.inputs[active.id];
      if (!input) return;
      if (input.state === "started" || input.state === "joined") active.started = true;
      const endedAfterRun = input.state === "ended" && (active.started || input.turn_id !== null);
      if (!endedAfterRun) return;
      activeInput.current = null;
      void readRequested().then((requested) => {
        if (cancelled) return;
        if (requested !== undefined) setOn(requested);
        setPending(null);
      });
    };
    const unsubscribe = store.subscribe(reconcile);
    reconcile();
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [pending, readRequested, store]);

  // A command whose input never starts must not leave the mic stuck.
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => {
      activeInput.current = null;
      setPending(null);
    }, 15_000);
    return () => window.clearTimeout(timer);
  }, [pending]);

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

  const value = useMemo(
    () => ({ pending: pending !== null, error, on, toggle, activity }),
    [activity, error, on, pending, toggle],
  );
  return (
    <VoiceModeContext.Provider value={value}>
      {children}
    </VoiceModeContext.Provider>
  );
}
