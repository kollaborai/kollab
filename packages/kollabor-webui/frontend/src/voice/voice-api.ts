import type { EngineApi } from "../api";
import type { VoiceEventEnvelope, VoiceSessionTicket, VoiceSessionView, VoiceStopReason } from "./types";

/** The engine calls the room controller makes (web-control.md). */
export type VoiceApi = {
  create(sessionId: string, threadId: string | null): Promise<VoiceSessionView>;
  join(voiceSessionId: string, generation: number): Promise<VoiceSessionTicket>;
  stop(voiceSessionId: string, generation: number, reason: VoiceStopReason): Promise<VoiceSessionView>;
  /** Long-lived SSE; resolves when the stream ends, rejects on abort or error. */
  events(
    voiceSessionId: string,
    cursor: string | null,
    signal: AbortSignal,
    onEvent: (e: VoiceEventEnvelope) => void,
  ): Promise<void>;
};

export const encodeCursor = (voiceSessionId: string, generation: number, sequence: number) =>
  `${voiceSessionId}:${generation}:${sequence}`;

export function voiceApi(api: EngineApi): VoiceApi {
  const id = encodeURIComponent;
  return {
    create: (sessionId, threadId) =>
      api.json(`/sessions/${id(sessionId)}/voice`, {
        method: "POST",
        body: JSON.stringify({ selected_thread_id: threadId }),
      }),
    join: (vid, generation) =>
      api.json(`/voice/sessions/${id(vid)}/join`, { method: "POST", body: JSON.stringify({ generation }) }),
    stop: (vid, generation, reason) =>
      api.json(`/voice/sessions/${id(vid)}`, { method: "DELETE", body: JSON.stringify({ generation, reason }) }),
    async events(vid, cursor, signal, onEvent) {
      const query = cursor ? `?cursor=${id(cursor)}` : "";
      const response = await api.request(`/voice/sessions/${id(vid)}/events${query}`, {
        method: "GET",
        headers: cursor ? { "Last-Event-ID": cursor } : {},
        signal,
      });
      if (!response.body) throw new Error("voice event stream has no body");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const flush = (frame: string) => {
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!data) return;
        try {
          onEvent(JSON.parse(data) as VoiceEventEnvelope);
        } catch {
          // keepalive or partial frame
        }
      };
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          frames.forEach(flush);
        }
        if (buffer.trim()) flush(buffer);
      } finally {
        reader.releaseLock();
      }
    },
  };
}
