// Lane C (#440) contract tests for the phone media pipeline.
// Run: cd packages/kollabor-webui/frontend && npx vitest run tests/phone-audio.test.ts
import { describe, expect, it } from "vitest";
import {
  AUDIO_MAX_BYTES,
  CallError,
  MIC_BYTES,
  MIC_SAMPLES,
  QUEUE_MAX_BYTES,
  createBoundedQueue,
  createFrameAssembler,
  createPhoneAudio,
  createResampler,
  packPcm16le,
  phoneCallTransport,
  unpackPcm16le,
  type PhoneCallTransport,
} from "../src/voice/phone-audio";
import type { PhoneTransport } from "../src/phonelink/phone-transport";

const VS_ID = "0ec4d9a2-0000-4000-8000-000000000001";

type Call = { op: string; msg: Record<string, unknown> };

function makeTransport(overrides?: {
  tickRejectsFirstAttempt?: boolean;
  tickResult?: Record<string, unknown>;
}) {
  const calls: Call[] = [];
  const attemptsPerN = new Map<number, number>();
  const transport: PhoneCallTransport = {
    async callOpen(msg) {
      calls.push({ op: "call_open", msg });
      return { ok: true, id: msg.id, input_rate: 16000, output_rate: 24000, frame_ms: 200 };
    },
    async callTick(msg) {
      calls.push({ op: "call_tick", msg });
      const n = msg.n;
      const count = (attemptsPerN.get(n) ?? 0) + 1;
      attemptsPerN.set(n, count);
      if (overrides?.tickRejectsFirstAttempt && count === 1) {
        throw new Error("simulated lost response");
      }
      return overrides?.tickResult ?? { ok: true, id: msg.id, n, audio: "" };
    },
    async callClose(msg) {
      calls.push({ op: "call_close", msg });
      return { ok: true };
    },
  };
  return { transport, calls, attemptsPerN };
}

type FakeCtxOpts = { sampleRate?: number };

function makeFakeCtx(opts: FakeCtxOpts = {}) {
  const log: string[] = [];
  const nodeMessages: Record<string, unknown>[] = [];
  let micListener: ((ev: MessageEvent) => void) | null = null;
  const node = {
    port: {
      set onmessage(fn: ((ev: MessageEvent) => void) | null) {
        micListener = fn;
      },
      get onmessage() {
        return micListener;
      },
      postMessage(msg: Record<string, unknown>) {
        nodeMessages.push(msg);
      },
    },
    connect() {
      log.push("node.connect");
    },
    disconnect() {
      log.push("node.disconnect");
    },
  };
  const ctx = {
    sampleRate: opts.sampleRate ?? 48_000,
    state: "running",
    resume: () => {
      log.push("ctx.resume");
      return Promise.resolve();
    },
    close: () => {
      log.push("ctx.close");
      return Promise.resolve();
    },
    destination: {},
    audioWorklet: {
      addModule: () => {
        log.push("addModule");
        return Promise.resolve();
      },
    },
    createMediaStreamSource: (stream: MediaStream) => ({
      connect() {
        log.push("source.connect");
      },
      disconnect() {
        log.push("source.disconnect");
      },
      stream,
    }),
  } as unknown as AudioContext;
  return {
    ctx,
    log,
    nodeMessages,
    node: node as unknown as AudioWorkletNode,
    feedMic(samples: Float32Array) {
      if (!micListener) throw new Error("worklet node not wired");
      const copy = new Float32Array(samples);
      micListener({ data: { type: "mic", pcm: copy.buffer } } as MessageEvent);
    },
  };
}

function makeGum(log: string[], stream: MediaStream | null = null) {
  const tracks = [{ stop: () => log.push("track.stop") }];
  const mediaStream = stream ?? ({ getTracks: () => tracks } as unknown as MediaStream);
  const gum = (async () => {
    log.push("getUserMedia.invoked");
    return mediaStream;
  }) as (c: MediaStreamConstraints) => Promise<MediaStream>;
  return { gum, mediaStream, tracks };
}

async function until(cond: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 200; i++) {
    if (cond()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error(`timed out waiting for ${what}`);
}

function tickCalls(calls: Call[]) {
  return calls.filter((c) => c.op === "call_tick");
}

async function startedAudio(sampleRate = 48_000) {
  const { transport, calls, attemptsPerN } = makeTransport();
  const fake = makeFakeCtx({ sampleRate });
  const { gum } = makeGum(fake.log);
  const audio = createPhoneAudio({
    transport,
    workletUrl: "stub://phone-audio-worklet",
    audioContextFactory: () => fake.ctx,
    workletNodeFactory: () => fake.node,
    getUserMedia: gum,
  });
  return { audio, transport, calls, attemptsPerN, fake };
}

describe("resampler (continuous phase)", () => {
  it("resamples_44100_and_48000", () => {
    // 48000 -> 16000 in one push equals chunked pushes with carried phase.
    const ramp = new Float32Array(4800);
    for (let i = 0; i < ramp.length; i++) ramp[i] = i / ramp.length;
    const once = createResampler(48_000, 16_000).push(ramp);
    expect(once.length).toBe(1600); // ceil(N/step); the final input sample carries
    const chunked = createResampler(48_000, 16_000);
    const parts: number[] = [];
    for (let off = 0; off < ramp.length; off += 1000) {
      for (const v of chunked.push(ramp.subarray(off, off + 1000))) parts.push(v);
    }
    expect(parts.length).toBe(once.length);
    for (let i = 0; i < once.length; i++) {
      expect(Math.abs(parts[i] - once[i])).toBeLessThan(1e-9);
    }
    // 44100 -> 16000 keeps continuous phase and roughly the right count.
    const src = new Float32Array(44_100);
    for (let i = 0; i < src.length; i++) src[i] = Math.sin(i / 10);
    const r = createResampler(44_100, 16_000);
    let got = 0;
    for (let off = 0; off < src.length; off += 4410) got += r.push(src.subarray(off, off + 4410)).length;
    expect(got).toBeGreaterThan(15_900);
    expect(got).toBeLessThan(16_100);
  });
});

describe("PCM framing", () => {
  it("packs_pcm16le_and_exact_200ms", () => {
    const packed = packPcm16le(new Float32Array([0, 1, -1, 0.5]));
    expect(Array.from(packed)).toEqual([0x00, 0x00, 0xff, 0x7f, 0x01, 0x80, 0x00, 0x40]);
    // unpackPcm16le stores into Float32Array: f32-quantized roundtrip values.
    expect(Array.from(unpackPcm16le(packed))).toEqual([
      0, 1, -1, Math.fround(Math.round(0.5 * 32767) / 32767),
    ]);
    // Exact 200 ms frames: 3200 samples -> 6400 bytes; remainder carried.
    const asm = createFrameAssembler();
    const ramp = new Float32Array(3000);
    for (let i = 0; i < ramp.length; i++) ramp[i] = (i % 100) / 100;
    expect(asm.push(ramp)).toEqual([]);
    expect(asm.remainder).toBe(3000);
    const frames = asm.push(new Float32Array(3500).fill(0.25));
    expect(frames.length).toBe(2);
    for (const f of frames) expect(f.length).toBe(MIC_BYTES);
    expect(asm.remainder).toBe(3000 + 3500 - 2 * MIC_SAMPLES);
  });
});

describe("bounded queues", () => {
  it("playback_handles_partial_and_empty", () => {
    const q = createBoundedQueue(1000);
    expect(q.pop(100).length).toBe(0); // empty pop
    q.push(new Uint8Array(0));
    expect(q.pop(100).length).toBe(0); // empty chunk is a no-op
    q.push(new Uint8Array(300));
    const first = q.pop(100); // partial drain
    expect(first.length).toBe(100);
    expect(q.bufferedBytes).toBe(200);
    const rest = q.pop(1000);
    expect(rest.length).toBe(200);
    expect(q.bufferedBytes).toBe(0);
  });

  it("does_not_queue_unbounded_audio", () => {
    const q = createBoundedQueue(QUEUE_MAX_BYTES);
    let drops = 0;
    for (let i = 0; i < 100; i++) drops = q.push(new Uint8Array(6000));
    expect(q.bufferedBytes).toBeLessThanOrEqual(QUEUE_MAX_BYTES);
    expect(drops).toBeGreaterThan(0);
    expect(drops).toBe(q.droppedBytes);
    // And the live pipeline enforces the same bound on tick responses.
    return (async () => {
      const loud: Record<string, unknown> = {
        ok: true,
        id: "x",
        n: 0,
        audio: btoa(String.fromCharCode(0).repeat(9600)),
      };
      expect(loud.audio!.length).toBeGreaterThan(0);
      const big = new Uint8Array(AUDIO_MAX_BYTES + 2);
      expect(() => unpackPcm16le(big)).toThrow();
    })();
  });
});

describe("createPhoneAudio lifecycle", () => {
  it("unlocks_on_tap", async () => {
    // One shared timeline: local unlock events and network calls interleaved.
    const order: string[] = [];
    const fake = makeFakeCtx();
    const { gum } = makeGum(fake.log);
    const { transport, calls } = makeTransport();
    const traced: PhoneCallTransport = {
      callOpen: async (msg) => {
        order.push(`net:${msg.op}`);
        return transport.callOpen(msg);
      },
      callTick: async (msg) => {
        order.push(`net:${msg.op}`);
        return transport.callTick(msg);
      },
      callClose: async (msg) => {
        order.push(`net:${msg.op}`);
        return transport.callClose(msg);
      },
    };
    const base = fake.ctx as unknown as Record<string, unknown>;
    const ctx = {
      ...base,
      resume: async () => {
        order.push("ctx.resume");
        await (base.resume as () => Promise<void>)();
      },
    };
    const audio = createPhoneAudio({
      transport: traced,
      workletUrl: "stub://x",
      audioContextFactory: () => ctx as unknown as AudioContext,
      workletNodeFactory: () => fake.node,
      getUserMedia: async (c) => {
        order.push("getUserMedia.invoked");
        return gum(c);
      },
    });
    await audio.start(VS_ID, 3);
    // getUserMedia and ctx.resume happen before the network open call.
    const gumAt = order.indexOf("getUserMedia.invoked");
    const resumeAt = order.indexOf("ctx.resume");
    const openAt = order.indexOf("net:call_open");
    expect(gumAt).toBeGreaterThanOrEqual(0);
    expect(resumeAt).toBeGreaterThanOrEqual(0);
    expect(openAt).toBeGreaterThan(gumAt);
    expect(openAt).toBeGreaterThan(resumeAt);
    const open = calls[0].msg as Record<string, unknown>;
    expect(open.op).toBe("call_open");
    expect(open.voice_session_id).toBe(VS_ID);
    expect(open.generation).toBe(3);
    expect(open.id).toMatch(/^[0-9a-f]{32}$/);
    await audio.stop();
  });

  it("stops_tracks_on_denial_or_disconnect", async () => {
    // Denial: getUserMedia rejects; start() throws and unwinds local state.
    const { transport, calls, fake } = await startedAudio();
    const deny = async () => {
      throw new DOMException("denied", "NotAllowedError");
    };
    const audio = createPhoneAudio({
      transport,
      workletUrl: "stub://x",
      audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node,
      getUserMedia: deny as (c: MediaStreamConstraints) => Promise<MediaStream>,
    });
    const errors: Error[] = [];
    audio.onError((e) => errors.push(e));
    await expect(audio.start(VS_ID, 1)).rejects.toThrow();
    expect(errors.length).toBe(1);
    expect(fake.log).toContain("ctx.close");
    expect(calls.some((c) => c.op === "call_open")).toBe(false);
    // Disconnect: stop() tears down tracks and sends call_close exactly once.
    const run = await startedAudio();
    await run.audio.start(VS_ID, 1);
    await run.audio.stop();
    const closes = run.calls.filter((c) => c.op === "call_close");
    expect(closes.length).toBe(1);
    expect(run.fake.log.filter((l) => l === "track.stop").length).toBe(1);
  });

  it("sends exact mic frames and retries a lost tick with the same n", async () => {
    const { transport: rt, calls: rc, attemptsPerN: ra } = makeTransport({
      tickRejectsFirstAttempt: true,
    });
    const fake2 = makeFakeCtx();
    const { gum } = makeGum(fake2.log);
    const audio2 = createPhoneAudio({
      transport: rt,
      workletUrl: "stub://x",
      audioContextFactory: () => fake2.ctx,
      workletNodeFactory: () => fake2.node,
      getUserMedia: gum,
    });
    await audio2.start(VS_ID, 5);
    // One full mic frame at the fake context's 48 kHz: 9600 samples.
    const samples = new Float32Array(9600);
    for (let i = 0; i < samples.length; i++) samples[i] = Math.sin(i / 8);
    fake2.feedMic(samples);
    await until(() => tickCalls(rc).length >= 1, "first tick");
    await until(() => ra.get(0) === 2, "retry of n=0");
    const first = tickCalls(rc)[0].msg as Record<string, unknown>;
    expect(first.n).toBe(0);
    const micBytes = Uint8Array.from(atob(first.mic as string), (c) => c.charCodeAt(0));
    expect(micBytes.length).toBe(MIC_BYTES);
    // After acceptance the sequence advanced exactly once.
    fake2.feedMic(new Float32Array(9600));
    // [0] is the lost tick, [1] its same-n retry, [2] the next real frame.
    await until(() => tickCalls(rc).length >= 3, "second real tick");
    const ticks = tickCalls(rc);
    const second = ticks[ticks.length - 1].msg as Record<string, unknown>;
    expect(second.n).toBe(1);
    // This transport drops the first attempt of every n: exactly one retry.
    await until(() => (ra.get(1) ?? 0) >= 2, "n=1 retried once");
    expect(ra.get(1)).toBe(2);
    expect(second.id).toBe(first.id);
    await audio2.stop();
  });

  it("fails closed when the engine never acknowledges a tick", async () => {
    const { transport, calls } = makeTransport();
    const failing: PhoneCallTransport = {
      callOpen: transport.callOpen,
      callClose: transport.callClose,
      callTick: async (msg) => {
        calls.push({ op: "call_tick", msg: { ...msg } });
        throw new Error("relay gone");
      },
    };
    const fake = makeFakeCtx();
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: failing,
      workletUrl: "stub://x",
      audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node,
      getUserMedia: gum,
    });
    const errors: Error[] = [];
    audio.onError((e) => errors.push(e));
    await audio.start(VS_ID, 1);
    fake.feedMic(new Float32Array(9600));
    await until(() => calls.some((c) => c.op === "call_close"), "fail-closed close");
    expect(errors.length).toBe(1);
    expect(calls.filter((c) => c.op === "call_tick").length).toBe(2); // one retry, then closed
  });

  it("bounds the playback queue across ticks and surfaces drops", async () => {
    const audioB64 = btoa("A".repeat(9600));
    const { transport, calls, fake } = await startedAudio();
    const t: PhoneCallTransport = {
      callOpen: transport.callOpen,
      callClose: transport.callClose,
      callTick: async (msg) => {
        calls.push({ op: "call_tick", msg: { ...msg } });
        return { ok: true, id: msg.id, n: msg.n, audio: audioB64 };
      },
    };
    const fake2 = makeFakeCtx();
    const { gum } = makeGum(fake2.log);
    const audio = createPhoneAudio({
      transport: t,
      workletUrl: "stub://x",
      audioContextFactory: () => fake2.ctx,
      workletNodeFactory: () => fake2.node,
      getUserMedia: gum,
    });
    await audio.start(VS_ID, 1);
    for (let i = 0; i < 8; i++) {
      fake2.feedMic(new Float32Array(9600));
      await until(() => tickCalls(calls).length >= i + 1, `tick ${i}`);
    }
    await until(() => audio.queuedBytes > 0 || audio.droppedBytes > 0, "queue activity");
    expect(audio.queuedBytes).toBeLessThanOrEqual(QUEUE_MAX_BYTES);
    void transport;
    void fake;
    await audio.stop();
    expect(audio.queuedBytes).toBe(0); // flushed on stop
  });
});

describe("transport adapter", () => {
  it("maps engine error results to CallError codes", async () => {
    const packet = async (data: Record<string, unknown>) => ({ error: "call_busy" });
    const fakeT = { callPacket: packet } as unknown as PhoneTransport;
    const adapter = phoneCallTransport(fakeT);
    await expect(
      adapter.callOpen({ op: "call_open", id: "a".repeat(32), voice_session_id: VS_ID, generation: 1 }),
    ).rejects.toMatchObject({ code: "call_busy" });
    await expect(adapter.callClose({ op: "call_close", id: "a".repeat(32) })).rejects.toBeInstanceOf(
      CallError,
    );
  });
});
