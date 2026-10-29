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
/** States that mean the runtime is up; the worker can report one before "ready" is seen. */
const LIVE = new Set(["ready", "listening", "hearing", "thinking", "speaking"]);

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
  /** The create in flight: a stop before it answers ends the session it makes. */
  private creating: Promise<VoiceSessionView> | null = null;

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
    if (this.view || this.state.state === "starting" || this.state.state === "stopping") return;
    const epoch = ++this.epoch;
    this.set({ ...initialRoomState, state: "starting" });
    try {
      const creating = this.api.create(sessionId, threadId);
      this.creating = creating;
      const view = await creating.finally(() => (this.creating = null));
      if (epoch !== this.epoch) return; // stop() ends the session it made
      this.view = view;
      this.set({ view, state: view.state, detail: view.detail });
      this.follow(view, epoch);
      // The engine refuses a join until its runtime is ready (models may still be
      // loading or downloading); the event stream says when it is.
      await this.untilReady(epoch);
      if (epoch !== this.epoch) return;
      const ticket = await this.api.join(view.voice_session_id, view.generation);
      if (epoch !== this.epoch) return;
      this.set({ state: "requesting_permission" });
      const room = await this.createRoom();
      if (epoch !== this.epoch) return; // stopped while it was built: never connect it
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

  /** Resolve once the runtime is ready (or the start was superseded); reject if it fails first. */
  private untilReady(epoch: number) {
    return new Promise<void>((resolve, reject) => {
      const check = () => {
        const { state, error, detail } = this.state;
        if (epoch !== this.epoch || LIVE.has(state)) {
          unsubscribe();
          resolve();
        } else if (state === "error" || state === "stopped" || state === "disconnected") {
          unsubscribe();
          reject(new Error(error || detail || "Voice could not start."));
        }
      };
      const unsubscribe = this.subscribe(check);
      check();
    });
  }

  async stop(reason: VoiceStopReason = "user") {
    const creating = this.view ? null : this.creating;
    if (creating) {
      // Still creating: end that session once create answers. Until then a
      // start waits, or it would get the same session back and lose it.
      this.creating = null;
      this.epoch++;
      this.set({ state: "stopping" });
      const view = await creating.catch(() => null);
      if (view) await this.api.stop(view.voice_session_id, view.generation, reason).catch(() => undefined);
      if (this.state.state === "stopping") this.set({ state: "stopped" });
      return;
    }
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
              if (epoch !== this.epoch) return;
              this.set(reduceVoiceEvent(this.state, e));
              // The engine ended the session (its worker stopped): leave the room and free the mic.
              const ended = ["error", "resource_shortage", "stopped"].includes(this.state.state);
              if (ended) void this.teardown("error");
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
