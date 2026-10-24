// Wire shapes from specs/voice-mode/livekit-web/contracts/shared-types.md.

export type VoiceSessionState =
  | "idle"
  | "starting"
  | "ready"
  | "requesting_permission"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "stopping"
  | "stopped"
  | "disconnected"
  | "downloading_models"
  | "resource_shortage"
  | "degraded"
  | "error";

export type VoiceSessionView = {
  voice_session_id: string;
  control_session_id: string;
  conversation_session_id: string;
  agent_id: string;
  thread_id: string | null;
  voice_provider_profile_id: string;
  voice_prompt_revision: string;
  state: VoiceSessionState;
  generation: number;
  detail: string | null;
  retryable: boolean;
  /** The selected LLM is a remote URL: transcript and context leave this machine. */
  remote_llm?: boolean;
  remote_llm_host?: string | null;
};

export type VoiceSessionTicket = {
  voice_session_id: string;
  generation: number;
  room_name: string;
  livekit_url: string;
  participant_token: string;
  expires_at_utc_ms: number;
};

export type VoiceStopReason = "user" | "session_switch" | "disconnect" | "shutdown" | "error";

export type VoiceEventName =
  | "voice.state"
  | "voice.transcript"
  | "voice.tool"
  | "voice.permission"
  | "voice.metrics"
  | "voice.error"
  | "voice.cursor_reset";

export type VoiceEventEnvelope = {
  schema_version: 1;
  event_id: string;
  event: VoiceEventName;
  voice_session_id: string;
  turn_id: string | null;
  generation: number;
  sequence: number;
  occurred_at_utc_ms: number;
  payload: Record<string, unknown>;
};

export type VoiceTranscriptChunk = {
  turn_id: string;
  speaker: "user" | "assistant";
  revision: number;
  final: boolean;
  chunk_index: number;
  chunk_count: number;
  display_text_chunk: string;
};

export type VoiceLatency = {
  stt_ms: number | null;
  llm_first_token_ms: number | null;
  first_audio_ms: number | null;
  total_turn_ms: number | null;
};
