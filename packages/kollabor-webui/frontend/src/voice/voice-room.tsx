import { AudioLinesIcon, SquareIcon } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { EngineApi } from "../api";
import { createLiveKitRoom } from "./livekit-room";
import { VoiceRoomController } from "./room-controller";
import { isActive, isInterrupting, remoteNotice, type VoiceRoomState } from "./room-state";
import { voiceApi } from "./voice-api";
import { cn } from "@/lib/utils";

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
 * The voice room strip above the composer: a mic icon to start, then state,
 * talk-over indicator, model download, latency and the remote-LLM notice.
 * The /voice toggle (voice-mode.tsx) is separate and unchanged.
 */
export function VoiceRoomBar() {
  const { ctx, room } = useVoiceRoom();
  if (!ctx || !room) return null;
  const { controller, sessionId } = ctx;
  const active = isActive(room);
  const notice = remoteNotice(room);
  const interrupting = isInterrupting(room);
  const dl = room.download;
  const busy = room.state === "starting" || room.state === "stopping";
  const latency = latencyText(room);
  return (
    <div data-slot="aui_voice-room" className="flex min-w-0 flex-col gap-1 px-1 text-xs text-muted-foreground">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          disabled={busy}
          aria-label={active ? "Stop voice room" : "Start voice room"}
          title={active ? "Stop Voice Room" : "Start Voice Room"}
          onClick={() => (active ? void controller.stop() : void controller.start(sessionId, null))}
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full text-foreground hover:bg-muted disabled:opacity-50",
            active && "text-destructive",
          )}
        >
          {active ? <SquareIcon className="size-3.5 fill-current" /> : <AudioLinesIcon className="size-4" />}
        </button>
        {active || room.state === "error" ? (
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
