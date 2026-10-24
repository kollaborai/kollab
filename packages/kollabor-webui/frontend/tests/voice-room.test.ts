// Run: node --test tests/voice-room.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

registerHooks({
  resolve: (specifier, context, next) => {
    try {
      return next(specifier, context);
    } catch (error) {
      if (!specifier.startsWith(".")) throw error;
      return next(`${specifier}.ts`, context);
    }
  },
});
const { initialRoomState, reduceVoiceEvent, isInterrupting, remoteNotice } = await import("../src/voice/room-state.ts");
const { VoiceRoomController } = await import("../src/voice/room-controller.ts");

let seq = 0;
const ev = (event: string, payload: Record<string, unknown>, turn: string | null = null) =>
  ({
    schema_version: 1,
    event_id: String(seq),
    event,
    voice_session_id: "v1",
    turn_id: turn,
    generation: 1,
    sequence: ++seq,
    occurred_at_utc_ms: 0,
    payload,
  }) as never;

const view = (over: Record<string, unknown> = {}) =>
  ({
    voice_session_id: "v1",
    control_session_id: "s1",
    conversation_session_id: "s1",
    agent_id: "a",
    thread_id: null,
    voice_provider_profile_id: "p",
    voice_prompt_revision: "r",
    state: "starting",
    generation: 1,
    detail: null,
    retryable: false,
    ...over,
  }) as never;

test("model download progress shows while downloading and clears after", () => {
  let s = reduceVoiceEvent(
    initialRoomState,
    ev("voice.state", { state: "downloading_models", download: { model: "parakeet", bytes_done: 5, bytes_total: 10 } }),
  );
  assert.deepEqual(s.download, { model: "parakeet", bytesDone: 5, bytesTotal: 10 });
  s = reduceVoiceEvent(s, ev("voice.state", { state: "listening" }));
  assert.equal(s.download, null);
});

test("transcript chunks reassemble and a final is never replaced", () => {
  let s = reduceVoiceEvent(
    initialRoomState,
    ev("voice.transcript", { turn_id: "t", speaker: "user", revision: 1, final: true, chunk_index: 1, chunk_count: 2, display_text_chunk: "world" }),
  );
  assert.equal(s.lines.length, 0);
  s = reduceVoiceEvent(
    s,
    ev("voice.transcript", { turn_id: "t", speaker: "user", revision: 1, final: true, chunk_index: 0, chunk_count: 2, display_text_chunk: "hello " }),
  );
  assert.equal(s.lines[0].text, "hello world");
  s = reduceVoiceEvent(
    s,
    ev("voice.transcript", { turn_id: "t", speaker: "user", revision: 2, final: false, chunk_index: 0, chunk_count: 1, display_text_chunk: "x" }),
  );
  assert.equal(s.lines[0].text, "hello world");
});

test("talking over the assistant shows interrupting; remote LLM shows the notice", () => {
  const speaking = reduceVoiceEvent(initialRoomState, ev("voice.state", { state: "speaking" }));
  assert.equal(isInterrupting(speaking), false);
  assert.equal(isInterrupting({ ...speaking, userSpeaking: true }), true);
  assert.equal(remoteNotice(speaking), null);
  const remote = { ...speaking, view: view({ remote_llm: true, remote_llm_host: "api.example.com" }) };
  assert.match(remoteNotice(remote) ?? "", /Remote LLM is listening \(api\.example\.com\)/);
});

test("barge_in and latency events land in state", () => {
  let s = reduceVoiceEvent(initialRoomState, ev("voice.state", { state: "listening", barge_in: true }));
  assert.equal(s.bargedIn, true);
  s = reduceVoiceEvent(s, ev("voice.metrics", { stt_ms: 120, llm_first_token_ms: null, first_audio_ms: 900, total_turn_ms: 2000 }));
  assert.equal(s.latency?.first_audio_ms, 900);
  assert.equal(s.latency?.llm_first_token_ms, null);
});

type Script = Array<Record<string, unknown>>;

function harness(opts: { joinFails?: boolean; startsAs?: string; events?: Script } = {}) {
  const calls: string[] = [];
  let speaking: (b: boolean) => void = () => undefined;
  const room = {
    connect: async () => void calls.push("connect"),
    setMicrophoneEnabled: async (on: boolean) => void calls.push(`mic:${on}`),
    disconnect: () => void calls.push("disconnect"),
    onLocalSpeaking: (cb: (b: boolean) => void) => void (speaking = cb),
    onDisconnected: () => undefined,
  };
  const api = {
    create: async () => (calls.push("create"), view({ state: opts.startsAs ?? "ready" })),
    join: async () => {
      calls.push("join");
      if (opts.joinFails) throw new Error("409: models not ready");
      return { voice_session_id: "v1", generation: 1, room_name: "r", livekit_url: "ws://x", participant_token: "t", expires_at_utc_ms: 0 };
    },
    stop: async (_id: string, _g: number, reason: string) => (calls.push(`stop:${reason}`), view()),
    events: (_id: string, _c: string | null, signal: AbortSignal, onEvent: (e: never) => void) =>
      new Promise<void>((_, reject) => {
        signal.addEventListener("abort", () => reject(new Error("abort")));
        // the engine's later states, one per tick, as the SSE stream delivers them
        (opts.events ?? []).forEach((payload, i) =>
          setTimeout(() => (calls.push(`event:${payload.state}`), onEvent(ev("voice.state", payload))), 5 * (i + 1)),
        );
      }),
  };
  return { calls, api, room, speaking: (b: boolean) => speaking(b), controller: new VoiceRoomController(api, async () => room) };
}

test("the mic opens only inside start, after join and connect; stop releases it", async () => {
  const h = harness();
  await h.controller.start("s1", null);
  assert.deepEqual(h.calls, ["create", "join", "connect", "mic:true"]);
  h.speaking(true);
  assert.equal(h.controller.getState().userSpeaking, true);
  await h.controller.stop();
  assert.deepEqual(h.calls.slice(4), ["mic:false", "disconnect", "stop:user"]);
  assert.equal(h.controller.getState().state, "stopped");
});

test("a refused join surfaces the error and never asks for the mic", async () => {
  const h = harness({ joinFails: true });
  await h.controller.start("s1", null);
  const s = h.controller.getState();
  assert.equal(s.state, "error");
  assert.match(s.error ?? "", /models not ready/);
  assert.ok(!h.calls.includes("mic:true"));
  assert.ok(h.calls.includes("stop:error"));
});

test("a cold start joins only once the engine says ready", async () => {
  const h = harness({
    startsAs: "starting",
    events: [{ state: "downloading_models", download: { model: "parakeet", bytes_done: 1, bytes_total: 2 } }, { state: "ready" }],
  });
  await h.controller.start("s1", null);
  assert.deepEqual(h.calls, ["create", "event:downloading_models", "event:ready", "join", "connect", "mic:true"]);
  await h.controller.stop();
});

test("a worker state that lands before ready still lets the room join", async () => {
  const h = harness({ startsAs: "starting", events: [{ state: "listening" }] });
  await h.controller.start("s1", null);
  assert.deepEqual(h.calls, ["create", "event:listening", "join", "connect", "mic:true"]);
  await h.controller.stop();
});

test("a runtime that fails before ready never joins or asks for the mic", async () => {
  const h = harness({ startsAs: "starting", events: [{ state: "error" }] });
  await h.controller.start("s1", null);
  assert.equal(h.controller.getState().state, "error");
  assert.ok(!h.calls.includes("join") && !h.calls.includes("mic:true"));
  assert.ok(h.calls.includes("stop:error"));
});

test("only a page served from this computer can reach the voice room", async () => {
  const { roomReachable } = await import("../src/voice/room-state.ts");
  assert.ok(["localhost", "127.0.0.1", "[::1]"].every(roomReachable));
  assert.ok(!["10.0.0.7", "kollabor.ai", "192.168.1.20"].some(roomReachable));
});
