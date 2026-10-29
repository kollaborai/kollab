import type { VoiceEventEnvelope, VoiceLatency, VoiceSessionState, VoiceSessionView, VoiceTranscriptChunk } from "./types";

export type VoiceLine = {
  turnId: string;
  speaker: "user" | "assistant";
  revision: number;
  final: boolean;
  text: string;
};

export type VoiceDownload = { model: string; bytesDone: number; bytesTotal: number };

export type VoiceRoomState = {
  /** idle until the user clicks the mic; then follows the engine's state. */
  state: VoiceSessionState;
  detail: string | null;
  retryable: boolean;
  view: VoiceSessionView | null;
  lines: VoiceLine[];
  download: VoiceDownload | null;
  latency: VoiceLatency | null;
  /** The user talked over the assistant; the worker cut playback. */
  bargedIn: boolean;
  /** The user is speaking right now (LiveKit active speaker). */
  userSpeaking: boolean;
  error: string | null;
  /** Last applied (generation, sequence), for resuming the event stream. */
  cursor: { generation: number; sequence: number } | null;
};

export const initialRoomState: VoiceRoomState = {
  state: "idle",
  detail: null,
  retryable: false,
  view: null,
  lines: [],
  download: null,
  latency: null,
  bargedIn: false,
  userSpeaking: false,
  error: null,
  cursor: null,
};

/** The voice is live: the mic is (or is about to be) open. */
export const isActive = (s: VoiceRoomState) =>
  !["idle", "stopped", "error", "resource_shortage", "disconnected"].includes(s.state);

/**
 * livekit-server listens on this computer's loopback only (voice_livekit.py), so
 * only a page served from this computer can join a room. Elsewhere (PhoneLink,
 * a phone on the LAN) the mic stays the /voice toggle.
 */
export const roomReachable = (hostname: string) => ["localhost", "127.0.0.1", "[::1]"].includes(hostname);

/** Talking while the assistant speaks: the "interrupt by talking" indicator. */
export const isInterrupting = (s: VoiceRoomState) => s.userSpeaking && s.state === "speaking";

/** The assistant's reply goes to a remote LLM URL: show the notice. */
export const remoteNotice = (s: VoiceRoomState): string | null =>
  s.view?.remote_llm
    ? `Remote LLM is listening${s.view.remote_llm_host ? ` (${s.view.remote_llm_host})` : ""}. Your transcript and session context are sent to it. Audio stays on this machine.`
    : null;

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

// Chunks of one revision wait here until every piece has arrived.
const pending = new Map<string, Map<number, string>>();

function applyTranscript(lines: VoiceLine[], turnId: string | null, p: Record<string, unknown>): VoiceLine[] {
  const c = p as unknown as VoiceTranscriptChunk;
  const turn = c.turn_id ?? turnId;
  if (!turn || (c.speaker !== "user" && c.speaker !== "assistant")) return lines;
  const count = Math.max(1, c.chunk_count ?? 1);
  const key = `${turn}:${c.speaker}:${c.revision}`;
  const parts = pending.get(key) ?? new Map<number, string>();
  parts.set(c.chunk_index ?? 0, c.display_text_chunk ?? "");
  pending.set(key, parts);
  if (parts.size < count) return lines;
  pending.delete(key);
  const text = Array.from({ length: count }, (_, i) => parts.get(i) ?? "").join("");
  const at = lines.findIndex((l) => l.turnId === turn && l.speaker === c.speaker);
  const line: VoiceLine = { turnId: turn, speaker: c.speaker, revision: c.revision, final: Boolean(c.final), text };
  if (at < 0) return [...lines, line];
  const old = lines[at];
  // A final revision is never replaced; an older revision never wins.
  if (old.final || c.revision < old.revision) return lines;
  return lines.map((l, i) => (i === at ? line : l));
}

/** Fold one voice-session SSE event into the room state. */
export function reduceVoiceEvent(s: VoiceRoomState, e: VoiceEventEnvelope): VoiceRoomState {
  const cursor = { generation: e.generation, sequence: e.sequence };
  const p = e.payload ?? {};
  switch (e.event) {
    case "voice.state": {
      const said = (str(p.state) as VoiceSessionState | null) ?? s.state;
      // The browser's own join steps outrank a late "ready" from the runtime.
      const joining = s.state === "requesting_permission" || s.state === "connecting";
      const next = said === "ready" && joining ? s.state : said;
      const dl = p.download as Record<string, unknown> | undefined;
      const download =
        next === "downloading_models" && dl
          ? { model: str(dl.model) ?? "model", bytesDone: num(dl.bytes_done) ?? 0, bytesTotal: num(dl.bytes_total) ?? 0 }
          : next === "downloading_models"
            ? s.download
            : null;
      return {
        ...s,
        cursor,
        state: next,
        detail: str(p.detail),
        retryable: p.retryable === true,
        download,
        bargedIn: p.barge_in === true ? true : next === "speaking" ? false : s.bargedIn,
        // A model out of memory ends the session too: its reason shows like an error's.
        error: next === "error" || next === "resource_shortage" ? (str(p.detail) ?? "Voice failed.") : s.error,
      };
    }
    case "voice.transcript":
      return { ...s, cursor, lines: applyTranscript(s.lines, e.turn_id, p) };
    case "voice.metrics":
      return {
        ...s,
        cursor,
        latency: {
          stt_ms: num(p.stt_ms),
          llm_first_token_ms: num(p.llm_first_token_ms),
          first_audio_ms: num(p.first_audio_ms),
          total_turn_ms: num(p.total_turn_ms),
        },
      };
    case "voice.error":
      return { ...s, cursor, error: str(p.message) ?? "Voice failed.", retryable: p.retryable === true };
    case "voice.cursor_reset": {
      const snap = p.snapshot as VoiceSessionView | undefined;
      const fresh = p.reason === "generation_changed";
      return {
        ...s,
        cursor: { generation: num(p.current_generation) ?? e.generation, sequence: (num(p.next_sequence) ?? 1) - 1 },
        view: snap ?? s.view,
        state: snap?.state ?? s.state,
        lines: fresh ? [] : s.lines,
      };
    }
    default:
      return { ...s, cursor };
  }
}

/** The engine's "no speech model yet" state (speech_adapter.map_readiness): a setup step, not a failure. */
export const needsSpeechModel = (error: string | null) => !!error?.includes("needs a speech model on this computer");
