import { initialRoomState, reduceVoiceEvent, type VoiceRoomState } from "./room-state";
import type { VoiceApi } from "./voice-api";
import { encodeCursor } from "./voice-api";
import type { VoiceSessionView, VoiceStopReason } from "./types";

/** The slice of livekit-client's Room the controller uses (mocked in tests). */
export type RoomLike = {
  connect(url: string, token: string): Promise<unknown>;
  /** Asks the browser for the mic; only ever called from a user click. */
  setMicrophoneEnabled(on: boolean): Promise<unknown>;
  disconnect(): unknown;
  /** Fires with whether the local user is an active speaker. */
  onLocalSpeaking(cb: (speaking: boolean) => void): void;
  onDisconnected(cb: () => void): void;
};

const MAX_STREAM_RETRIES = 3;

const errorText = (reason: unknown) =>
  (reason instanceof Error ? reason.message : String(reason)).replace(/^\d{3}: /, "");

/**
 * One voice room: create -> event stream -> join -> mic. State lives in
 * VoiceRoomState; the engine's SSE is the source of truth for everything but
 * the local mic and speaking flags.
 */
export class VoiceRoomController {
  private state: VoiceRoomState = initialRoomState;
  private listeners = new Set<() => void>();
  private abort: AbortController | null = null;
  private room: RoomLike | null = null;
  private view: VoiceSessionView | null = null;
  /** Bumps on every start/stop so a late callback from an old room is ignored. */
  private epoch = 0;

  private api: VoiceApi;
  private createRoom: () => Promise<RoomLike>;

  constructor(api: VoiceApi, createRoom: () => Promise<RoomLike>) {
    this.api = api;
    this.createRoom = createRoom;
  }

  getState = () => this.state;

  subscribe = (cb: () => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(patch: Partial<VoiceRoomState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((cb) => cb());
  }

  async start(sessionId: string, threadId: string | null) {
    if (this.view || this.state.state === "starting") return;
    const epoch = ++this.epoch;
    this.set({ ...initialRoomState, state: "starting" });
    try {
      const view = await this.api.create(sessionId, threadId);
      if (epoch !== this.epoch) return;
      this.view = view;
      this.set({ view, state: view.state, detail: view.detail });
      this.follow(view, epoch);
      const ticket = await this.api.join(view.voice_session_id, view.generation);
      if (epoch !== this.epoch) return;
      this.set({ state: "requesting_permission" });
      const room = await this.createRoom();
      this.room = room;
      room.onLocalSpeaking((userSpeaking) => epoch === this.epoch && this.set({ userSpeaking }));
      room.onDisconnected(() => epoch === this.epoch && void this.stop("disconnect"));
      this.set({ state: "connecting" });
      await room.connect(ticket.livekit_url, ticket.participant_token);
      if (epoch !== this.epoch) return room.disconnect();
      await room.setMicrophoneEnabled(true);
    } catch (reason) {
      if (epoch !== this.epoch) return;
      this.set({ state: "error", error: errorText(reason), retryable: true });
      await this.teardown("error");
    }
  }

  async stop(reason: VoiceStopReason = "user") {
    if (!this.view) return;
    this.set({ state: "stopping" });
    await this.teardown(reason);
    if (this.state.state === "stopping") this.set({ state: "stopped" });
  }

  private async teardown(reason: VoiceStopReason) {
    const view = this.view;
    this.view = null;
    this.epoch++;
    this.abort?.abort();
    this.abort = null;
    const room = this.room;
    this.room = null;
    try {
      await room?.setMicrophoneEnabled(false);
    } catch {
      // the room may already be gone
    }
    room?.disconnect();
    if (view) {
      try {
        await this.api.stop(view.voice_session_id, view.generation, reason);
      } catch {
        // already stopped on the engine
      }
    }
    this.set({ userSpeaking: false });
  }

  /** Follow the voice-session SSE, resuming from the last cursor if it drops. */
  private follow(view: VoiceSessionView, epoch: number) {
    const abort = new AbortController();
    this.abort = abort;
    void (async () => {
      for (let attempt = 0; attempt <= MAX_STREAM_RETRIES; attempt++) {
        const c = this.state.cursor;
        try {
          await this.api.events(
            view.voice_session_id,
            c ? encodeCursor(view.voice_session_id, c.generation, c.sequence) : null,
            abort.signal,
            (e) => {
              if (epoch === this.epoch) this.set(reduceVoiceEvent(this.state, e));
            },
          );
        } catch {
          // fall through to retry
        }
        if (abort.signal.aborted || epoch !== this.epoch) return;
        await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
      }
      if (epoch === this.epoch) {
        this.set({ error: "Lost the voice event stream.", state: "disconnected" });
        void this.teardown("disconnect");
      }
    })();
  }
}
