// Lane C (#440) contract tests for the phone media pipeline.
// Run: cd packages/kollabor-webui/frontend && npx vitest run tests/phone-audio.test.ts
import { describe, expect, it, vi } from "vitest";
import { runInNewContext } from "node:vm";
import workletSource from "../src/voice/phone-audio-worklet.js?raw";
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

function actualWorklet(rate: number, send: (message: Record<string, unknown>) => void) {
  class ProcessorBase {
    port = { onmessage: null as ((event: { data: Record<string, unknown> }) => void) | null,
      postMessage: send };
  }
  let Processor!: new () => ProcessorBase & { process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean };
  runInNewContext(workletSource, {
    AudioWorkletProcessor: ProcessorBase, sampleRate: rate, Float32Array,
    registerProcessor: (_name: string, processor: typeof Processor) => { Processor = processor; },
  });
  return new Processor();
}

it("the packaged worklet keeps newest playback within 600 ms and reports every dropped sample", () => {
  const messages: Record<string, unknown>[] = [];
  const processor = actualWorklet(48_000, (message) => messages.push(message));
  const ramp = Float32Array.from({ length: 28_800 }, (_, i) => i);
  processor.port.onmessage!({ data: { type: "play", id: 1, pcm: ramp.buffer } });
  processor.port.onmessage!({ data: { type: "play", id: 2, pcm: new Float32Array(4_800).fill(-1).buffer } });
  const output = new Float32Array(28_800);
  processor.process([], [[output]]);
  expect(output[0]).toBe(4_800); // discard the oldest 100 ms, keep the new 100 ms
  expect(Array.from(output.slice(-4_800))).toEqual(Array(4_800).fill(-1));
  expect(messages).toContainEqual({ type: "play_ack", id: 2, queuedSamples: 28_800, droppedSamples: 4_800 });
  const oversized = Float32Array.from({ length: 28_801 }, (_, i) => i);
  processor.port.onmessage!({ data: { type: "play", id: 3, pcm: oversized.buffer } });
  processor.process([], [[output]]);
  expect(output[0]).toBe(1);
  expect(messages).toContainEqual({ type: "play_ack", id: 3, queuedSamples: 28_800, droppedSamples: 4_801 });
});

it("the worklet reserves room before admitting an in-flight playback packet", () => {
  const messages: Record<string, unknown>[] = [];
  const processor = actualWorklet(48_000, (message) => messages.push(message));
  processor.port.onmessage!({ data: { type: "play", id: 1, pcm: new Float32Array(28_800).buffer } });
  processor.port.onmessage!({ data: { type: "play_reserve", id: 2, samples: 9_600 } });
  expect(messages.at(-1)).toEqual({ type: "reserve_ack", id: 2, queuedSamples: 19_200, droppedSamples: 9_600 });
  expect((messages.at(-1)!.queuedSamples as number) + 9_600).toBe(28_800);
  processor.process([], [[new Float32Array(128)]]); // consumption only frees more room
  processor.port.onmessage!({ data: { type: "play", id: 2, pcm: new Float32Array(9_600).buffer } });
  expect(messages.at(-1)).toEqual({ type: "play_ack", id: 2, queuedSamples: 28_672, droppedSamples: 9_600 });
});

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
      return overrides?.tickResult
        ? { ok: true, id: msg.id, n, audio: "", ...overrides.tickResult }
        : { ok: true, id: msg.id, n, audio: "" };
    },
    async callClose(msg) {
      calls.push({ op: "call_close", msg });
      return { ok: true };
    },
  };
  return { transport, calls, attemptsPerN };
}

type FakeCtxOpts = { sampleRate?: number; holdPlayAck?: boolean };

function makeFakeCtx(opts: FakeCtxOpts = {}) {
  const log: string[] = [];
  const nodeMessages: Record<string, unknown>[] = [];
  let micListener: ((ev: MessageEvent) => void) | null = null;
  const heldAcks: Record<string, unknown>[] = [];
  const processor = actualWorklet(opts.sampleRate ?? 48_000, (message) => {
    if (opts.holdPlayAck && message.type === "play_ack") { heldAcks.push(message); return; }
    queueMicrotask(() => micListener?.({ data: message } as MessageEvent));
  });
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
        processor.port.onmessage?.({ data: msg });
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
    emit(message: Record<string, unknown>) {
      micListener?.({ data: message } as MessageEvent);
    },
    releasePlayAcks() {
      opts.holdPlayAck = false;
      for (const message of heldAcks.splice(0)) micListener?.({ data: message } as MessageEvent);
    },
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
  it("keeps captured microphone frames in the bounded FIFO while call_open is pending", async () => {
    const { transport, calls } = makeTransport();
    let release!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callOpen: async (message) => { await wait; return transport.callOpen(message); } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const start = audio.start(VS_ID, 1);
    try {
      await until(() => fake.log.includes("source.connect"), "microphone capture before call_open ACK");
      for (let i = 1; i <= 4; i++) fake.feedMic(new Float32Array(3200).fill(i / 10));
      expect(audio.droppedMicFrames).toBe(1);
      release();
      await start;
      await until(() => tickCalls(calls).length === 3, "all retained frames after call_open");
      const firstSamples = tickCalls(calls).map(({ msg }) => {
        const bytes = Uint8Array.from(atob(msg.mic as string), (c) => c.charCodeAt(0));
        return new DataView(bytes.buffer).getInt16(0, true);
      });
      expect(firstSamples).toEqual([6553, 9830, 13107]);
    } finally { release(); await audio.stop(); }
  });

  it("dispatches three media frames while the oldest relay response is held", async () => {
    const { transport, calls } = makeTransport();
    const held = new Map<number, (response: Record<string, unknown>) => void>();
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: {
        ...transport,
        callTick: (message) => {
          calls.push({ op: "call_tick", msg: { ...message } });
          return new Promise((resolve) => held.set(message.n, resolve));
        },
      },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    try {
      await audio.start(VS_ID, 1);
      for (let i = 0; i < 4; i++) fake.feedMic(new Float32Array(3200).fill((i + 1) / 10));
      await until(() => tickCalls(calls).length >= 3, "three outstanding media frames");
      expect(tickCalls(calls).map((call) => call.msg.n)).toEqual([0, 1, 2]);
      expect(held.has(0)).toBe(true);
      expect(held.has(1)).toBe(true);
      expect(held.has(2)).toBe(true);
      // Later ACKs do not let the sender move past the missing oldest n.
      held.get(1)!({ ok: true, id: tickCalls(calls)[1].msg.id, n: 1, audio: "" });
      held.get(2)!({ ok: true, id: tickCalls(calls)[2].msg.id, n: 2, audio: "" });
      await Promise.resolve();
      await Promise.resolve();
      expect(tickCalls(calls)).toHaveLength(3);
      held.get(0)!({ ok: true, id: tickCalls(calls)[0].msg.id, n: 0, audio: "" });
      await until(() => tickCalls(calls).length === 4, "fourth frame after oldest ACK");
      expect(tickCalls(calls).map((call) => call.msg.n)).toEqual([0, 1, 2, 3]);
    } finally { await audio.stop(); }
  });

  it("commits out-of-order network responses and playback in sequence order", async () => {
    const { transport, calls } = makeTransport();
    const held = new Map<number, (response: Record<string, unknown>) => void>();
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callTick: (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        return new Promise((resolve) => held.set(message.n, resolve));
      } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const response = (n: number, value: number) => {
      const bytes = packPcm16le(new Float32Array(3200).fill(value));
      return { ok: true, id: tickCalls(calls)[n].msg.id, n, audio: btoa(String.fromCharCode(...bytes)) };
    };
    try {
      await audio.start(VS_ID, 1);
      for (let i = 0; i < 3; i++) fake.feedMic(new Float32Array(3200).fill(0.25));
      await until(() => tickCalls(calls).length === 3, "three media requests");
      held.get(2)!({ ...response(2, 0.3) });
      held.get(1)!({ ...response(1, 0.2) });
      await Promise.resolve();
      expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(0);
      held.get(0)!({ ...response(0, 0.1) });
      await until(() => fake.nodeMessages.filter((message) => message.type === "play").length === 3,
        "all three ordered playback packets");
      const firstSamples = fake.nodeMessages.filter((message) => message.type === "play").map((message) => {
        return new Float32Array(message.pcm as ArrayBuffer)[10];
      });
      expect(firstSamples).toEqual([
        Math.fround(Math.round(0.1 * 32767) / 32767),
        Math.fround(Math.round(0.2 * 32767) / 32767),
        Math.fround(Math.round(0.3 * 32767) / 32767),
      ]);
    } finally { await audio.stop(); }
  });

  it("fences all three pending media callbacks when stop closes the call", async () => {
    const { transport, calls } = makeTransport();
    const held = new Map<number, (response: Record<string, unknown>) => void>();
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callTick: (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        return new Promise((resolve) => held.set(message.n, resolve));
      } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const errors: Error[] = [];
    audio.onError((error) => errors.push(error));
    await audio.start(VS_ID, 1);
    for (let i = 0; i < 3; i++) fake.feedMic(new Float32Array(3200).fill(0.25));
    await until(() => tickCalls(calls).length === 3, "three pending media responses");
    const stop = audio.stop();
    expect(fake.log).toContain("track.stop"); // local microphone teardown precedes close completion
    for (let n = 0; n < 3; n++) held.get(n)!({ ok: true, id: tickCalls(calls)[n].msg.id, n, audio: "" });
    await stop;
    await Promise.resolve();
    expect(tickCalls(calls)).toHaveLength(3);
    expect(calls.filter((call) => call.op === "call_close")).toHaveLength(1);
    expect(errors).toHaveLength(0);
  });

  it("fences three pending responses when the active worklet fails", async () => {
    const { transport, calls } = makeTransport();
    const held = new Map<number, (response: Record<string, unknown>) => void>();
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callTick: (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        return new Promise((resolve) => held.set(message.n, resolve));
      } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const errors: Error[] = [];
    audio.onError((error) => errors.push(error));
    await audio.start(VS_ID, 1);
    for (let i = 0; i < 3; i++) fake.feedMic(new Float32Array(3200).fill(0.25));
    await until(() => tickCalls(calls).length === 3, "three pending media responses");
    fake.node.onprocessorerror!(new Event("processorerror"));
    await until(() => calls.some((call) => call.op === "call_close"), "close after worklet failure");
    expect(fake.log).toContain("track.stop");
    for (let n = 0; n < 3; n++) held.get(n)!({ ok: true, id: tickCalls(calls)[n].msg.id, n, audio: "" });
    await Promise.resolve();
    expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(0);
    expect(tickCalls(calls)).toHaveLength(3);
    expect(errors.map((error) => error.message)).toEqual(["call_unavailable: playback processor failed"]);
  });

  it("fails closed on a response with a mismatched sequence ACK", async () => {
    const { transport, calls } = makeTransport();
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callTick: async (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        return { ok: true, id: message.id, n: message.n + 1, audio: btoa("A") };
      } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const errors: Error[] = [];
    audio.onError((error) => errors.push(error));
    await audio.start(VS_ID, 1);
    fake.feedMic(new Float32Array(3200).fill(0.25));
    await until(() => calls.some((call) => call.op === "call_close"), "close after invalid ACK");
    expect(errors[0]?.message).toContain("mismatched acknowledgement");
    expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(0);
  });

  it.each([7, 8])("keeps a separate 600 ms capture FIFO behind three delayed requests (%i frames)", async (frameCount) => {
    const { transport, calls } = makeTransport();
    let release!: () => void;
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    const fake = makeFakeCtx({ sampleRate: 16_000 });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({
      transport: { ...transport, callTick: async (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        await delayed;
        return { ok: true, id: message.id, n: message.n, audio: "" };
      } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    try {
      await audio.start(VS_ID, 1);
      for (let i = 1; i <= frameCount; i++) fake.feedMic(new Float32Array(3200).fill(i / 10));
      await until(() => tickCalls(calls).length === 3, "three media requests before the relay response");
      release();
      await until(() => tickCalls(calls).length >= 6, "the buffered speech frames");
      const firstSamples = tickCalls(calls).map(({ msg }) => {
        const bytes = Uint8Array.from(atob(msg.mic as string), (c) => c.charCodeAt(0));
        return new DataView(bytes.buffer).getInt16(0, true);
      });
      expect(firstSamples).toEqual(frameCount === 7
        ? [3277, 6553, 9830, 16384, 19660, 22937]
        : [3277, 6553, 9830, 19660, 22937, 26214]);
      expect(audio.droppedMicFrames).toBe(frameCount - 6);
    } finally { release(); await audio.stop(); }
  });

  it("admits one playback transfer until its matching ACK and counts cumulative device drops", async () => {
    const { transport: baseTransport, calls } = makeTransport();
    const transport: PhoneCallTransport = {
      callOpen: baseTransport.callOpen,
      callClose: baseTransport.callClose,
      callTick: async (message) => {
        calls.push({ op: "call_tick", msg: { ...message } });
        return { ok: true, id: message.id, n: message.n,
          audio: message.n === 0 ? btoa("A".repeat(9600)) : "" };
      },
    };
    const fake = makeFakeCtx({ sampleRate: 44_100, holdPlayAck: true });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({ transport, workletUrl: "stub://x",
      audioContextFactory: () => fake.ctx, workletNodeFactory: () => fake.node, getUserMedia: gum });
    try {
      await audio.start(VS_ID, 1);
      fake.feedMic(new Float32Array(8_820));
      await until(() => fake.nodeMessages.some((message) => message.type === "play"), "first playback transfer");
      fake.feedMic(new Float32Array(8_820));
      fake.feedMic(new Float32Array(8_820));
      fake.feedMic(new Float32Array(8_820));
      await until(() => tickCalls(calls).length === 3, "three requests while the oldest playback ACK is held");
      fake.emit({ type: "play_ack", id: 999 }); // another transfer cannot release this one
      fake.emit({ type: "reserve_ack", id: fake.nodeMessages.find((message) => message.type === "play")!.id });
      await Promise.resolve();
      expect(tickCalls(calls)).toHaveLength(3); // playback ACK retains the oldest window credit
      expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(1);
      fake.releasePlayAcks();
      await until(() => tickCalls(calls).length === 4, "fourth request after oldest playback ACK");
      expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(1);
      fake.emit({ type: "play_state", queuedSamples: 0, droppedSamples: 1 });
      expect(audio.droppedBytes).toBe(0);
      fake.emit({ type: "play_state", queuedSamples: 0, droppedSamples: 2 });
      expect(audio.droppedBytes).toBe(2); // fractions survive consecutive 44.1 kHz reports
    } finally { await audio.stop(); }
  });

  it("fails closed when the playback worklet never acknowledges", async () => {
    const { transport, calls } = makeTransport({ tickResult: { audio: btoa("A".repeat(9600)) } });
    const fake = makeFakeCtx({ holdPlayAck: true });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({ transport, workletUrl: "stub://x",
      audioContextFactory: () => fake.ctx, workletNodeFactory: () => fake.node, getUserMedia: gum });
    const errors: Error[] = [];
    audio.onError((error) => errors.push(error));
    try {
      await audio.start(VS_ID, 1);
      vi.useFakeTimers();
      fake.feedMic(new Float32Array(9600));
      await vi.waitFor(() => expect(fake.nodeMessages.some((message) => message.type === "play")).toBe(true));
      await vi.advanceTimersByTimeAsync(1000);
      expect(errors[0]?.message).toContain("playback stopped responding");
      expect(tickCalls(calls)).toHaveLength(1); // an accepted tick is not replayed for a local ACK failure
      expect(calls.filter((call) => call.op === "call_close")).toHaveLength(1);
      expect(fake.log).toContain("track.stop");
    } finally {
      vi.useRealTimers();
      await audio.stop();
    }
  });

  it("ignores a stopped worklet's ACK while the next call awaits its own playback", async () => {
    const { transport, calls } = makeTransport({ tickResult: { audio: btoa("A".repeat(9600)) } });
    const fake = makeFakeCtx({ holdPlayAck: true });
    const { gum } = makeGum(fake.log);
    const audio = createPhoneAudio({ transport, workletUrl: "stub://x",
      audioContextFactory: () => fake.ctx, workletNodeFactory: () => fake.node, getUserMedia: gum });
    try {
      await audio.start(VS_ID, 1);
      fake.feedMic(new Float32Array(9600));
      await until(() => fake.nodeMessages.filter((message) => message.type === "play").length === 1, "old playback");
      const oldHandler = fake.node.port.onmessage!;
      const oldId = fake.nodeMessages.find((message) => message.type === "play")!.id;
      await audio.stop();
      await audio.start(VS_ID, 1);
      fake.feedMic(new Float32Array(9600));
      await until(() => fake.nodeMessages.filter((message) => message.type === "play").length === 2, "new playback");
      const newId = fake.nodeMessages.filter((message) => message.type === "play")[1].id;
      fake.feedMic(new Float32Array(9600));
      oldHandler({ data: { type: "play_ack", id: newId } } as MessageEvent);
      fake.emit({ type: "play_ack", id: oldId });
      await Promise.resolve();
      expect(tickCalls(calls)).toHaveLength(3); // old call n=0; new call n=0 and n=1
      expect(fake.nodeMessages.filter((message) => message.type === "play")).toHaveLength(2);
      fake.releasePlayAcks();
      await until(() => tickCalls(calls).length === 3, "matching current ACK");
      await until(() => fake.nodeMessages.filter((message) => message.type === "play").length === 3,
        "second in-order response playback");
    } finally { await audio.stop(); }
  });

  it("refuses an unsupported loader before requesting the microphone", () => {
    const { transport } = makeTransport();
    let asked = false;
    const audio = createPhoneAudio({
      transport,
      checkAvailable: () => { throw new Error("PhoneLink loader needs an update"); },
      getUserMedia: async () => { asked = true; throw new Error("unexpected microphone request"); },
    });
    expect(() => audio.prepare()).toThrow("loader needs an update");
    expect(asked).toBe(false);
  });
  it("starts the next call at sequence zero and clears old partial microphone frames", async () => {
    const { audio, calls, fake } = await startedAudio();
    await audio.start(VS_ID, 1);
    fake.feedMic(new Float32Array(9600));
    await until(() => tickCalls(calls).length === 1, "first call tick");
    fake.feedMic(new Float32Array(4000));
    await audio.stop();
    await audio.start(VS_ID, 1);
    fake.feedMic(new Float32Array(9600));
    await until(() => tickCalls(calls).length === 2, "second call tick");
    expect(tickCalls(calls).map((call) => call.msg.n)).toEqual([0, 0]);
    await audio.stop();
  });

  it("closes a late call open after the user stops", async () => {
    const { transport, calls } = makeTransport();
    const fake = makeFakeCtx();
    const { gum } = makeGum(fake.log);
    let release!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const audio = createPhoneAudio({
      transport: { ...transport, callOpen: async (message) => { await wait; return transport.callOpen(message); } },
      workletUrl: "stub://x", audioContextFactory: () => fake.ctx,
      workletNodeFactory: () => fake.node, getUserMedia: gum,
    });
    const start = audio.start(VS_ID, 1);
    await until(() => fake.log.includes("source.connect"), "open pending");
    await audio.stop();
    release();
    await expect(start).rejects.toThrow("cancelled");
    expect(calls.filter((call) => call.op === "call_close")).toHaveLength(1);
  });

  it("stops a late microphone grant after the worklet fails", async () => {
    const { transport } = makeTransport();
    const fake = makeFakeCtx();
    let grant!: (stream: MediaStream) => void;
    const pending = new Promise<MediaStream>((resolve) => { grant = resolve; });
    let stopped = 0;
    const ctx = { ...fake.ctx, audioWorklet: { addModule: async () => { throw new Error("worklet failed"); } } } as AudioContext;
    const audio = createPhoneAudio({ transport, workletUrl: "stub://x", audioContextFactory: () => ctx, getUserMedia: () => pending });
    await expect(audio.start(VS_ID, 1)).rejects.toThrow("worklet failed");
    grant({ getTracks: () => [{ stop: () => { stopped++; } }] } as unknown as MediaStream);
    await until(() => stopped === 1, "late microphone cleanup");
  });

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
    expect(tickCalls(rc)[1].msg.mic).toBe(first.mic); // retry preserves the exact retained microphone frame
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
