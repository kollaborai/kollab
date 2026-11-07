// Phone call media pipeline (lane C, #440), implementing the frozen wire
// contract in plan-440call-gyrolite.md section 2:
// - mic frames: exactly 6400 bytes PCM16LE mono 16 kHz (3200 samples = 200 ms)
// - response audio: 0..9600 bytes PCM16LE mono 24 kHz, even length, no header
// - capture/playback FIFOs each hold at most 600 ms; overflow drops oldest
// - at most three 200 ms packets of additional transport/response staging
// - at most three in-flight call_ticks, with credit bounded by the oldest
//   sequence not yet committed through ordered playback; lost responses retry
//   the same n once (the engine serves a repeated n from cache)
// - tap unlock: AudioContext creation/resume + getUserMedia are invoked
//   synchronously inside start() before any network await

import type { CallPacket, PhoneTransport } from "../phonelink/phone-transport";
import workletSource from "./phone-audio-worklet.js?raw";

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
export function phoneCallTransport(t: Pick<PhoneTransport, "callPacket">): PhoneCallTransport {
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
  /** Reject before requesting the microphone when the phone loader lacks call support. */
  checkAvailable?: () => void;
  /** Defaults to the Vite-emitted worklet URL; tests inject a stub module. */
  workletUrl?: string;
  audioContextFactory?: () => AudioContext;
  workletNodeFactory?: (ctx: AudioContext) => AudioWorkletNode;
  getUserMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>;
}

export interface PhoneAudio {
  prepare(): void;
  start(voiceSessionId: string, generation: number): Promise<void>;
  stop(): Promise<void>;
  onError(cb: (error: Error) => void): void;
  /** Cumulative response-audio bytes the bounded play queue dropped. */
  readonly droppedBytes: number;
  /** Response-audio bytes currently buffered for playback. */
  readonly queuedBytes: number;
  /** Captured microphone frames dropped after the 600 ms queue filled. */
  readonly droppedMicFrames: number;
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

const PLAY_ACK_TIMEOUT_MS = 1_000;
const MAX_MEDIA_IN_FLIGHT = 3;

export function createPhoneAudio(deps: PhoneAudioDeps): PhoneAudio {
  const transport = deps.transport;
  let workletBlobUrl: string | null = null;
  const errorCbs: ((e: Error) => void)[] = [];
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let micPromise: Promise<MediaStream> | null = null;
  let epoch = 0;
  let source: MediaStreamAudioSourceNode | null = null;
  let node: AudioWorkletNode | null = null;
  let callId: string | null = null;
  let nextN = 0;
  // Sent frames retain window credit through their ordered playback ACK.
  const mediaWindow = new Map<number, { id: string; n: number; mic: string; epoch: number; retries: number }>();
  const readyResponses = new Map<number, Record<string, unknown>>();
  let nextCommitN = 0;
  let committingEpoch: number | null = null;
  let pendingMic: Uint8Array[] = [];
  let stopping = false;
  let outResampler = createResampler(OUT_RATE, MIC_RATE);
  let micResampler = createResampler(MIC_RATE, MIC_RATE);
  let assembler = createFrameAssembler();
  let dropped = 0;
  let remoteDropped = 0;
  let queuedSamples = 0;
  let pendingSamples = 0;
  let deviceDrops = 0;
  let playSerial = 0;
  let playPending: { id: number; phase: string; resolve: () => void; reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout> } | null = null;
  let micFramesDropped = 0;

  const fail = (e: Error) => {
    for (const cb of errorCbs) {
      try {
        cb(e);
      } catch {
        // listener errors never break the pipeline
      }
    }
  };

  const onTickResult = async (r: Record<string, unknown>): Promise<void> => {
    if (typeof r.dropped === "number" && Number.isSafeInteger(r.dropped) && r.dropped >= 0)
      remoteDropped = r.dropped * 2;
    const audioB64 = typeof r.audio === "string" ? r.audio : "";
    if (audioB64 === "") return;
    const bytes = fromBase64(audioB64);
    if (bytes.length > AUDIO_MAX_BYTES || bytes.length % 2 !== 0) {
      throw new CallError("call_invalid", "audio frame outside contract bounds");
    }
    if (!node || !ctx) throw new CallError("call_unavailable", "playback is unavailable");
    const up = outResampler.push(unpackPcm16le(bytes));
    if (!up.length) return;
    const target = node;
    const playbackEpoch = epoch;
    const id = ++playSerial;
    // The worklet owns the sole 600 ms playback FIFO. Ordered response commits
    // admit one transferred packet at a time, so its message port stays bounded.
    const acknowledged = (message: Record<string, unknown>, phase: string) => new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        playPending = null;
        reject(new CallError("call_unavailable", "playback stopped responding"));
      }, PLAY_ACK_TIMEOUT_MS);
      playPending = { id, phase, resolve, reject, timer };
      try {
        target.port.postMessage(message, phase === "play_ack" ? [up.buffer] : []);
      } catch (error) {
        clearTimeout(timer);
        playPending = null;
        reject(error);
      }
    });
    // Reserve room before admitting the transferred packet. Consumption can
    // only decrease the FIFO between this ACK and append, so queued + in-flight
    // playback remains within the same 600 ms budget.
    await acknowledged({ type: "play_reserve", id, samples: up.length }, "reserve_ack");
    if (playbackEpoch !== epoch || target !== node) throw new Error("Voice stopped.");
    pendingSamples = up.length;
    await acknowledged({ type: "play", id, pcm: up.buffer }, "play_ack");
  };

  const failCall = (tickEpoch: number, error: unknown): void => {
    if (tickEpoch !== epoch) return;
    fail(error instanceof Error ? error : new Error(String(error)));
    void audio.stop();
  };

  const commitResponses = async (commitEpoch: number): Promise<void> => {
    if (committingEpoch === commitEpoch) return;
    committingEpoch = commitEpoch;
    try {
      while (commitEpoch === epoch && readyResponses.has(nextCommitN)) {
        const n = nextCommitN;
        const response = readyResponses.get(n)!;
        readyResponses.delete(n);
        await onTickResult(response);
        if (commitEpoch !== epoch) return;
        mediaWindow.delete(n); // playback ACK is part of ordered commit credit
        nextCommitN = n + 1;
        pumpMic();
      }
    } catch (error) {
      failCall(commitEpoch, error);
    } finally {
      if (committingEpoch === commitEpoch) committingEpoch = null;
      if (commitEpoch === epoch && readyResponses.has(nextCommitN)) void commitResponses(commitEpoch);
    }
  };

  const acceptTickResponse = (request: { id: string; n: number; mic: string; epoch: number; retries: number },
    response: Record<string, unknown>): void => {
    if (request.epoch !== epoch || request.id !== callId) return;
    if (response.id !== request.id || response.n !== request.n || response.ok !== true) {
      failCall(request.epoch, new CallError("call_invalid", `tick ${request.n} returned a mismatched acknowledgement`));
      return;
    }
    readyResponses.set(request.n, response);
    void commitResponses(request.epoch);
  };

  const sendTick = (request: { id: string; n: number; mic: string; epoch: number; retries: number }): void => {
    if (request.epoch !== epoch || request.id !== callId) return;
    Promise.resolve().then(() => {
      if (request.epoch !== epoch || request.id !== callId) throw new Error("Voice stopped.");
      return transport.callTick({ op: "call_tick", id: request.id, n: request.n, mic: request.mic });
    })
      .then((response) => acceptTickResponse(request, response))
      .catch((error: unknown) => {
        if (request.epoch !== epoch || request.id !== callId) return;
        if (request.retries === 0 && !(error instanceof CallError)) {
          request.retries = 1;
          sendTick(request); // exact same n and mic bytes
          return;
        }
        failCall(request.epoch, error);
      });
  };

  /** Fills the bounded sequence window without passing the oldest uncommitted n. */
  function pumpMic(): void {
    if (stopping || !callId) return;
    while (pendingMic.length && mediaWindow.size < MAX_MEDIA_IN_FLIGHT) {
      const oldestUncommitted = mediaWindow.size ? Math.min(...mediaWindow.keys()) : nextN;
      if (nextN > oldestUncommitted + MAX_MEDIA_IN_FLIGHT - 1) return;
      if (!Number.isSafeInteger(nextN) || nextN < 0 || nextN === Number.MAX_SAFE_INTEGER) {
        failCall(epoch, new CallError("call_sequence", "media sequence exhausted"));
        return;
      }
      const n = nextN++;
      const request = { id: callId, n, mic: toBase64(pendingMic.shift()!.slice()), epoch, retries: 0 };
      mediaWindow.set(n, request);
      sendTick(request);
    }
  }

  const onWorkletMessage = (ev: MessageEvent): void => {
    const msg = ev.data as { type: string; pcm?: ArrayBuffer; id?: number;
      queuedSamples?: number; droppedSamples?: number };
    if (!ctx) return;
    const pending = playPending;
    const acknowledged = pending && pending.id === msg.id && pending.phase === msg.type;
    if (acknowledged && msg.type === "play_ack") pendingSamples = 0;
    if (typeof msg.queuedSamples === "number" && Number.isSafeInteger(msg.queuedSamples) &&
        msg.queuedSamples >= 0 && msg.queuedSamples <= Math.ceil(ctx.sampleRate * 0.6))
      queuedSamples = msg.queuedSamples;
    if (typeof msg.droppedSamples === "number" && Number.isSafeInteger(msg.droppedSamples) &&
        msg.droppedSamples >= deviceDrops) {
      deviceDrops = msg.droppedSamples;
      // Convert the cumulative duration, preserving fractions between reports.
      dropped = Math.floor(deviceDrops * OUT_RATE / ctx.sampleRate) * 2;
    }
    if (pending && acknowledged) {
      playPending = null;
      clearTimeout(pending.timer);
      pending.resolve();
    }
    if (msg.type !== "mic" || !msg.pcm) return;
    const chunk = new Float32Array(msg.pcm);
    const resampled =
      ctx.sampleRate === MIC_RATE ? chunk : micResampler.push(chunk);
    for (const frame of assembler.push(resampled)) {
      if (pendingMic.length === 3) {
        pendingMic.shift(); // 600 ms queued: discard the oldest only on overflow
        micFramesDropped += 1;
      }
      pendingMic.push(frame);
    }
    pumpMic();
  };

  const unwindLocal = (): void => {
    pendingMic = [];
    mediaWindow.clear();
    readyResponses.clear();
    nextCommitN = 0;
    assembler = createFrameAssembler();
    if (playPending) {
      clearTimeout(playPending.timer);
      playPending.reject(new Error("Voice stopped."));
      playPending = null;
    }
    queuedSamples = 0;
    pendingSamples = 0;
    if (node) {
      node.port.onmessage = null;
      node.onprocessorerror = null;
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
    prepare() {
      if (ctx) return;
      deps.checkAvailable?.();
      ctx = deps.audioContextFactory ? deps.audioContextFactory() : new AudioContext();
      void ctx.resume().catch(() => undefined);
      const gUM =
        deps.getUserMedia ??
        navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      const preparingEpoch = epoch;
      micPromise = gUM({
        audio: { echoCancellation: true, noiseSuppression: true },
      }).then((media) => {
        if (preparingEpoch !== epoch) {
          for (const track of media.getTracks()) track.stop();
          throw new Error("Voice start was cancelled.");
        }
        stream = media;
        return media;
      });
      // Permission may reject while the voice runtime is still starting.
      void micPromise.catch(() => undefined);
    },

    async start(voiceSessionId, generation) {
      if (!ctx) audio.prepare();
      dropped = remoteDropped = queuedSamples = deviceDrops = 0;
      micFramesDropped = 0;
      const startingEpoch = epoch;
      const id = crypto.randomUUID().replaceAll("-", "");
      let opened = false;
      try {
        const activeCtx = ctx!;
        const url = deps.workletUrl ?? (workletBlobUrl = URL.createObjectURL(new Blob([workletSource], { type: "text/javascript" })));
        await activeCtx.audioWorklet.addModule(url);
        if (workletBlobUrl) { URL.revokeObjectURL(workletBlobUrl); workletBlobUrl = null; }
        if (startingEpoch !== epoch) throw new Error("Voice start was cancelled.");
        node = deps.workletNodeFactory
          ? deps.workletNodeFactory(activeCtx)
          : new AudioWorkletNode(activeCtx, "phone-audio");
        node.port.onmessage = (event) => {
          if (startingEpoch === epoch) onWorkletMessage(event);
        };
        node.onprocessorerror = () => {
          if (startingEpoch !== epoch) return;
          fail(new CallError("call_unavailable", "playback processor failed"));
          void audio.stop();
        };
        node.connect(activeCtx.destination);
        micResampler = createResampler(activeCtx.sampleRate, MIC_RATE);
        outResampler = createResampler(OUT_RATE, activeCtx.sampleRate);
        stream = await micPromise!;
        if (startingEpoch !== epoch) throw new Error("Voice start was cancelled.");
        source = activeCtx.createMediaStreamSource(stream);
        source.connect(node);
        const res = await transport.callOpen({
          op: "call_open",
          id,
          voice_session_id: voiceSessionId,
          generation,
        });
        opened = res?.ok === true;
        if (startingEpoch !== epoch) {
          throw new Error("Voice start was cancelled.");
        }
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
        nextN = 0;
        nextCommitN = 0;
        mediaWindow.clear();
        readyResponses.clear();
        pumpMic(); // frames captured while call_open was pending already have FIFO bounds
      } catch (e) {
        // A confirmed call must close even if its rates or local start fail.
        if (startingEpoch !== epoch) {
          if (opened) await transport.callClose({ op: "call_close", id }).catch(() => undefined);
          throw e;
        }
        epoch++;
        callId = null;
        unwindLocal();
        if (workletBlobUrl) { URL.revokeObjectURL(workletBlobUrl); workletBlobUrl = null; }
        micPromise = null;
        if (opened) await transport.callClose({ op: "call_close", id }).catch(() => undefined);
        const err = e instanceof Error ? e : new Error(String(e));
        fail(err);
        throw err;
      }
    },

    async stop() {
      epoch++;
      stopping = true;
      pendingMic = [];
      mediaWindow.clear();
      readyResponses.clear();
      nextN = 0;
      nextCommitN = 0;
      assembler = createFrameAssembler();
      // Flush queued audio: on stop we close rather than play stale speech.
      if (node) node.port.postMessage({ type: "flush" });
      unwindLocal();
      if (workletBlobUrl) { URL.revokeObjectURL(workletBlobUrl); workletBlobUrl = null; }
      micPromise = null;
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
      return dropped + remoteDropped;
    },

    get queuedBytes() {
      return ctx ? Math.ceil((queuedSamples + pendingSamples) * OUT_RATE / ctx.sampleRate) * 2 : 0;
    },
    get droppedMicFrames() {
      return micFramesDropped;
    },
  };
  return audio;
}
