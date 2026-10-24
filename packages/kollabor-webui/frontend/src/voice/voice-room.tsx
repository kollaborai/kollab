import { SquareIcon } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { EngineApi } from "../api";
import type { VoiceMode } from "../voice-mode";
import { createLiveKitRoom } from "./livekit-room";
import { VoiceRoomController } from "./room-controller";
import { isActive, isInterrupting, remoteNotice, roomReachable, type VoiceRoomState } from "./room-state";
import { voiceApi } from "./voice-api";

type Ctx = { controller: VoiceRoomController; sessionId: string };
const VoiceRoomContext = createContext<Ctx | null>(null);

/** One LiveKit voice room for the open chat; leaving the chat stops it. */
export function VoiceRoomProvider({
  api,
  sessionId,
  children,
}: {
  api: EngineApi;
  sessionId: string;
  children: ReactNode;
}) {
  const value = useMemo(
    () => ({ controller: new VoiceRoomController(voiceApi(api), createLiveKitRoom), sessionId }),
    [api, sessionId],
  );
  useEffect(() => () => void value.controller.stop("session_switch"), [value]);
  return <VoiceRoomContext.Provider value={value}>{children}</VoiceRoomContext.Provider>;
}

const NO_SUBSCRIBE = () => () => undefined;

const REACHABLE = typeof window !== "undefined" && roomReachable(window.location.hostname);

export function useVoiceRoom() {
  const ctx = useContext(VoiceRoomContext);
  const room = useSyncExternalStore(
    ctx?.controller.subscribe ?? NO_SUBSCRIBE,
    () => ctx?.controller.getState() ?? null,
  );
  return { ctx, room };
}

const LABEL: Record<string, string> = {
  starting: "Starting",
  ready: "Ready",
  requesting_permission: "Waiting For Mic",
  connecting: "Connecting",
  listening: "Listening",
  hearing: "Hearing",
  thinking: "Thinking",
  speaking: "Speaking",
  stopping: "Stopping",
  disconnected: "Disconnected",
  downloading_models: "Downloading Models",
  resource_shortage: "Low On Resources",
  degraded: "Degraded",
  error: "Error",
};

const seconds = (ms: number | null) => (ms == null ? null : `${(ms / 1000).toFixed(1)}s`);

export const mb = (bytes: number) => `${(bytes / 1_048_576).toFixed(0)} MB`;

/** Latency readout: only the stages the engine measured. */
export function latencyText(room: VoiceRoomState): string {
  const l = room.latency;
  if (!l) return "";
  const stages: [string, number | null][] = [
    ["Hear", l.stt_ms],
    ["Think", l.llm_first_token_ms],
    ["Speak", l.first_audio_ms],
    ["Total", l.total_turn_ms],
  ];
  return stages.flatMap(([name, ms]) => (ms == null ? [] : [`${name} ${seconds(ms)}`])).join(" · ");
}

/**
 * The room as the composer mic sees it: the mic starts the room in this browser
 * (the phone's mic on a phone), the listening mic stops it.
 */
export function useRoomVoiceMode(): VoiceMode | null {
  const { ctx, room } = useVoiceRoom();
  return useMemo(() => {
    if (!ctx || !room || !REACHABLE) return null;
    const on = isActive(room);
    return {
      pending: room.state === "starting" || room.state === "stopping",
      on,
      toggle: () => void (on ? ctx.controller.stop() : ctx.controller.start(ctx.sessionId, null)),
      activity: null,
    };
  }, [ctx, room]);
}

/**
 * The voice room strip above the composer while a room is up: state, talk-over
 * indicator, model download, latency, the remote-LLM notice and a stop button.
 * The composer mic starts it.
 */
export function VoiceRoomBar() {
  const { ctx, room } = useVoiceRoom();
  if (!ctx || !room || !REACHABLE) return null;
  const active = isActive(room);
  if (!active && !room.error) return null;
  const notice = remoteNotice(room);
  const interrupting = isInterrupting(room);
  const dl = room.download;
  const busy = room.state === "starting" || room.state === "stopping";
  const latency = latencyText(room);
  return (
    <div data-slot="aui_voice-room" className="flex min-w-0 flex-col gap-1 px-1 text-xs text-muted-foreground">
      <div className="flex min-w-0 items-center gap-2">
        {active ? (
          <button
            type="button"
            disabled={busy}
            aria-label="Stop voice room"
            title="Stop Voice"
            onClick={() => void ctx.controller.stop()}
            className="flex size-7 shrink-0 items-center justify-center rounded-full text-destructive hover:bg-muted disabled:opacity-50"
          >
            <SquareIcon className="size-3.5 fill-current" />
          </button>
        ) : null}
        {active || room.error ? (
          <>
            <span className="shrink-0 font-medium text-foreground" data-slot="aui_voice-room-state">
              {interrupting ? "Interrupting" : (LABEL[room.state] ?? room.state)}
            </span>
            {room.bargedIn && !interrupting ? <span className="shrink-0">Interrupted</span> : null}
            {dl ? (
              <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" data-slot="aui_voice-room-download">
                {dl.model} {mb(dl.bytesDone)}
                {dl.bytesTotal ? ` of ${mb(dl.bytesTotal)}` : ""}
              </span>
            ) : null}
            {latency ? (
              <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" data-slot="aui_voice-room-latency">
                {latency}
              </span>
            ) : null}
          </>
        ) : null}
      </div>
      {room.error ? (
        <div role="alert" className="text-destructive">
          {room.error}
        </div>
      ) : null}
      {active && notice ? (
        <div role="note" data-slot="aui_voice-room-remote">
          {notice}
        </div>
      ) : null}
    </div>
  );
}
