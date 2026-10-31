// Phone call media pipeline (lane C, #440), implementing the frozen wire
// contract in plan-440call-gyrolite.md section 2:
// - mic frames: exactly 6400 bytes PCM16LE mono 16 kHz (3200 samples = 200 ms)
// - response audio: 0..9600 bytes PCM16LE mono 24 kHz, even length, no header
// - both directions bounded at 600 ms; overflow drops oldest, drops reported
// - at most one in-flight call_tick; a lost response retries the SAME n once
//   (the engine serves a repeated n from cache); if the engine still never
//   acknowledges, the call fails closed rather than risking a sequence gap
// - tap unlock: AudioContext creation/resume + getUserMedia are invoked
//   synchronously inside start() before any network await

import type { CallPacket, PhoneTransport } from "../phonelink/phone-transport";

export const MIC_RATE = 16_000;
export const OUT_RATE = 24_000;
export const FRAME_MS = 200;
export const MIC_SAMPLES = (MIC_RATE * FRAME_MS) / 1000; // 3200
export const MIC_BYTES = MIC_SAMPLES * 2; // 6400
export const AUDIO_MAX_BYTES = 9600;
// 600 ms of PCM16 at 24 kHz = 28800 bytes (the contract's bounded queue).
export const QUEUE_MAX_BYTES = Math.round((OUT_RATE * 600) / 1000) * 2;

export type CallErrorCode =
  | "call_invalid"
  | "call_forbidden"
  | "call_not_ready"
  | "call_busy"
  | "call_stale"
  | "call_sequence"
  | "call_overloaded"
  | "call_unavailable";

export class CallError extends Error {
  readonly code: CallErrorCode;
  constructor(code: CallErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.code = code;
  }
}

export type CallOpenMsg = {
  op: "call_open";
  id: string;
  voice_session_id: string;
  generation: number;
};
export type CallTickMsg = {
  op: "call_tick";
  id: string;
  n: number;
  mic: string;
};
export type CallCloseMsg = { op: "call_close"; id: string };

/** Structural transport; lane D injects the real encrypted path. */
export interface PhoneCallTransport {
  callOpen(msg: CallOpenMsg): Promise<Record<string, unknown>>;
  callTick(msg: CallTickMsg): Promise<Record<string, unknown>>;
  callClose(msg: CallCloseMsg): Promise<Record<string, unknown>>;
}

/** Adapter over lane A's committed PhoneTransport.callPacket wire path. */
export function phoneCallTransport(t: PhoneTransport): PhoneCallTransport {
  const checked = (r: Record<string, unknown>) => {
    if (r && typeof r.error === "string") {
      throw new CallError(r.error as CallErrorCode, "engine rejected call op");
    }
    return r;
  };
  // CallPacket adds Record<string, Json>; every field we send is a JSON
  // primitive by construction, so the boundary cast is safe and checked by
  // the msg types themselves.
  const packet = (msg: CallOpenMsg | CallTickMsg | CallCloseMsg): CallPacket =>
    msg as unknown as CallPacket;
  return {
    callOpen: (msg) => t.callPacket(packet(msg)).then(checked),
    callTick: (msg) => t.callPacket(packet(msg)).then(checked),
    callClose: (msg) => t.callPacket(packet(msg)).then(checked),
  };
}

/**
 * Linear-interpolation resampler with continuous phase across pushes; the
 * fractional read position carries over so no drift accumulates.
 */
export function createResampler(fromRate: number, toRate: number) {
  if (!(fromRate > 0 && toRate > 0)) throw new Error("rates must be positive");
  let buf = new Float32Array(0);
  let pos = 0; // fractional sample position
  const step = fromRate / toRate;
  return {
    push(input: Float32Array): Float32Array {
      const merged = new Float32Array(buf.length + input.length);
      merged.set(buf);
      merged.set(input, buf.length);
      buf = merged;
      const out: number[] = [];
      // Interpolate while both pos and pos+1 are valid indices.
      while (Math.floor(pos) + 1 < buf.length) {
        const i = Math.floor(pos);
        const frac = pos - i;
        out.push(buf[i] + (buf[i + 1] - buf[i]) * frac);
        pos += step;
      }
      // Keep exactly the one sample pos points at (if any) for next push.
      const keepFrom = Math.min(Math.floor(pos), buf.length);
      buf = buf.slice(keepFrom);
      pos -= keepFrom;
      return Float32Array.from(out);
    },
    get pending(): number {
      return buf.length;
    },
  };
}

/** Float32 [-1,1] -> PCM16 little-endian bytes, clamped and rounded. */
export function packPcm16le(samples: Float32Array): Uint8Array {
  const out = new Uint8Array(samples.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < samples.length; i++) {
    let s = samples[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    view.setInt16(i * 2, Math.round(s * 32767), true);
  }
  return out;
}

/**
 * PCM16 little-endian bytes -> Float32 [-1,1]. Empty input is valid; input
 * above the contract's audio frame bound fails closed.
 */
export function unpackPcm16le(bytes: Uint8Array): Float32Array {
  if (bytes.length === 0) return new Float32Array(0);
  if (bytes.length % 2 !== 0) throw new Error("odd PCM16 byte count");
  if (bytes.length > AUDIO_MAX_BYTES) {
    throw new Error("PCM16 input exceeds the audio frame bound");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(bytes.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = view.getInt16(i * 2, true) / 32767;
  }
  return out;
}

/** Emits exact 3200-sample mic frames, carrying the remainder across pushes. */
export function createFrameAssembler() {
  let rest = new Float32Array(0);
  return {
    push(samples: Float32Array): Uint8Array[] {
      const all = new Float32Array(rest.length + samples.length);
      all.set(rest);
      all.set(samples, rest.length);
      const frames: Uint8Array[] = [];
      let off = 0;
      for (; off + MIC_SAMPLES <= all.length; off += MIC_SAMPLES) {
        frames.push(packPcm16le(all.subarray(off, off + MIC_SAMPLES)));
      }
      rest = all.slice(off);
      return frames;
    },
    get remainder(): number {
      return rest.length;
    },
  };
}

/** Bounded FIFO in PCM16 bytes; overflow drops the oldest bytes, counted. */
export function createBoundedQueue(maxBytes: number) {
  let chunks: Uint8Array[] = [];
  let buffered = 0;
  let dropped = 0;
  return {
    push(bytes: Uint8Array): number {
      if (bytes.length > maxBytes) {
        const excess = bytes.length - maxBytes;
        dropped += excess;
        bytes = bytes.slice(excess); // keep only the newest maxBytes
      }
      if (bytes.length > 0) {
        chunks.push(bytes);
        buffered += bytes.length;
      }
      while (buffered > maxBytes && chunks.length > 0) {
        const head = chunks[0];
        const over = buffered - maxBytes;
        if (over >= head.length) {
          chunks.shift();
          buffered -= head.length;
          dropped += head.length;
        } else {
          chunks[0] = head.slice(over);
          buffered -= over;
          dropped += over;
        }
      }
      return dropped;
    },
    pop(maxOutBytes: number): Uint8Array {
      if (chunks.length === 0 || maxOutBytes <= 0) return new Uint8Array(0);
      const head = chunks[0];
      if (head.length <= maxOutBytes) {
        chunks.shift();
        buffered -= head.length;
        return head;
      }
      chunks[0] = head.slice(maxOutBytes);
      buffered -= maxOutBytes;
      return head.slice(0, maxOutBytes);
    },
    get bufferedBytes(): number {
      return buffered;
    },
    get droppedBytes(): number {
      return dropped;
    },
  };
}

export interface PhoneAudioDeps {
  transport: PhoneCallTransport;
  /** Defaults to the Vite-emitted worklet URL; tests inject a stub module. */
  workletUrl?: string;
  audioContextFactory?: () => AudioContext;
  workletNodeFactory?: (ctx: AudioContext) => AudioWorkletNode;
  getUserMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>;
}

export interface PhoneAudio {
  start(voiceSessionId: string, generation: number): Promise<void>;
  stop(): Promise<void>;
  onError(cb: (error: Error) => void): void;
  /** Cumulative response-audio bytes the bounded play queue dropped. */
  readonly droppedBytes: number;
  /** Response-audio bytes currently buffered for playback. */
  readonly queuedBytes: number;
}

function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

function fromBase64(b64: string): Uint8Array {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

const PLAY_TOP_UP_MS = 80;
const PLAY_POP_BYTES = Math.round((OUT_RATE * 100) / 1000) * 2; // 100 ms @24k

export function createPhoneAudio(deps: PhoneAudioDeps): PhoneAudio {
  const transport = deps.transport;
  const url =
    deps.workletUrl ??
    new URL("./phone-audio-worklet.ts", import.meta.url).href;
  const errorCbs: ((e: Error) => void)[] = [];
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let node: AudioWorkletNode | null = null;
  let callId: string | null = null;
  let nextN = 0;
  let inFlight = false;
  let pendingMic: Uint8Array | null = null;
  let stopping = false;
  let outResampler = createResampler(OUT_RATE, MIC_RATE);
  let micResampler = createResampler(MIC_RATE, MIC_RATE);
  const assembler = createFrameAssembler();
  let playQueue = createBoundedQueue(QUEUE_MAX_BYTES);
  let dropped = 0;
  let micFramesDropped = 0;
  let playTimer: ReturnType<typeof setInterval> | null = null;

  const fail = (e: Error) => {
    for (const cb of errorCbs) {
      try {
        cb(e);
      } catch {
        // listener errors never break the pipeline
      }
    }
  };

  /** Moves queued response audio into the worklet's bounded play buffer. */
  const pumpPlay = (): void => {
    if (!node || !ctx) return;
    for (;;) {
      const bytes = playQueue.pop(PLAY_POP_BYTES);
      if (bytes.length === 0) return;
      const up = outResampler.push(unpackPcm16le(bytes));
      if (up.length === 0) return;
      const copy = new Float32Array(up); // copy so the buffer is transferable
      node.port.postMessage({ type: "play", pcm: copy.buffer }, [copy.buffer]);
      if (playQueue.bufferedBytes === 0) return;
    }
  };

  const onTickResult = (r: Record<string, unknown>): void => {
    const audioB64 = typeof r.audio === "string" ? r.audio : "";
    if (audioB64 === "") return;
    const bytes = fromBase64(audioB64);
    if (bytes.length > AUDIO_MAX_BYTES || bytes.length % 2 !== 0) {
      throw new CallError("call_invalid", "audio frame outside contract bounds");
    }
    dropped = playQueue.push(bytes);
  };

  /** Sends the next mic frame; at most one in-flight tick per contract. */
  const pumpMic = (): void => {
    if (stopping || !callId || inFlight || !pendingMic) return;
    const mic = pendingMic;
    pendingMic = null;
    const n = nextN;
    inFlight = true;
    const attempt = () =>
      transport.callTick({
        op: "call_tick",
        id: callId as string,
        n,
        mic: toBase64(mic),
      });
    attempt()
      .catch(() =>
        // Lost response: the engine serves a repeated n from its cache, so a
        // single retry of the SAME n resolves the loss without a gap.
        attempt(),
      )
      .then((r) => {
        onTickResult(r);
        nextN = n + 1; // accepted: sequence advances exactly once
      })
      .catch((e: unknown) => {
        // Engine never acknowledged n even once. Sequence state is
        // unknowable: fail closed and end the call (contract).
        fail(e instanceof Error ? e : new Error(String(e)));
        void audio.stop();
      })
      .finally(() => {
        inFlight = false;
        pumpMic();
      });
  };

  const onWorkletMessage = (ev: MessageEvent): void => {
    const msg = ev.data as { type: string; pcm?: ArrayBuffer };
    if (msg.type !== "mic" || !msg.pcm || !ctx) return;
    const chunk = new Float32Array(msg.pcm);
    const resampled =
      ctx.sampleRate === MIC_RATE ? chunk : micResampler.push(chunk);
    for (const frame of assembler.push(resampled)) {
      if (pendingMic) micFramesDropped += 1; // bounded: keep the newest frame
      pendingMic = frame;
    }
    pumpMic();
  };

  const unwindLocal = (): void => {
    if (playTimer) {
      clearInterval(playTimer);
      playTimer = null;
    }
    if (node) {
      node.port.onmessage = null;
      node.disconnect();
      node = null;
    }
    if (source) {
      source.disconnect();
      source = null;
    }
    if (stream) {
      for (const t of stream.getTracks()) t.stop();
      stream = null;
    }
    if (ctx && ctx.state !== "closed") void ctx.close().catch(() => undefined);
    ctx = null;
  };

  const audio: PhoneAudio = {
    async start(voiceSessionId, generation) {
      // Tap-critical section: the AudioContext is created and resumed and
      // getUserMedia is INVOKED synchronously before any network await.
      const id = crypto.randomUUID().replaceAll("-", "");
      ctx = deps.audioContextFactory ? deps.audioContextFactory() : new AudioContext();
      void ctx.resume().catch(() => undefined);
      const gUM =
        deps.getUserMedia ??
        navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      const micPromise = gUM({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      try {
        await ctx.audioWorklet.addModule(url);
        node = deps.workletNodeFactory
          ? deps.workletNodeFactory(ctx)
          : new AudioWorkletNode(ctx, "phone-audio");
        node.port.onmessage = onWorkletMessage as EventListener;
        node.connect(ctx.destination);
        micResampler = createResampler(ctx.sampleRate, MIC_RATE);
        outResampler = createResampler(OUT_RATE, ctx.sampleRate);
        stream = await micPromise;
        source = ctx.createMediaStreamSource(stream);
        source.connect(node);
        const res = await transport.callOpen({
          op: "call_open",
          id,
          voice_session_id: voiceSessionId,
          generation,
        });
        if (!res || res.ok !== true) {
          throw new CallError("call_unavailable", "call_open did not confirm");
        }
        if (
          res.input_rate !== MIC_RATE ||
          res.output_rate !== OUT_RATE ||
          res.frame_ms !== FRAME_MS
        ) {
          throw new CallError("call_unavailable", "engine offered unexpected rates");
        }
        callId = id;
        playTimer = setInterval(pumpPlay, PLAY_TOP_UP_MS);
      } catch (e) {
        // Denial or any startup failure: unwind local resources. No close op:
        // the engine never confirmed this call id.
        callId = null;
        unwindLocal();
        const err = e instanceof Error ? e : new Error(String(e));
        fail(err);
        throw err;
      }
    },

    async stop() {
      stopping = true;
      // Flush queued audio: on stop we close rather than play stale speech.
      playQueue = createBoundedQueue(QUEUE_MAX_BYTES);
      if (node) node.port.postMessage({ type: "flush" });
      unwindLocal();
      const id = callId;
      callId = null;
      if (id) {
        try {
          await transport.callClose({ op: "call_close", id });
        } catch {
          // best effort; the engine times calls out on its own
        }
      }
      stopping = false;
    },

    onError(cb) {
      errorCbs.push(cb);
    },

    get droppedBytes() {
      return dropped;
    },

    get queuedBytes() {
      return playQueue.bufferedBytes;
    },
  };
  void micFramesDropped;
  return audio;
}
