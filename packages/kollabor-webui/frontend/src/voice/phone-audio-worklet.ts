// Phone call audio worklet (lane C, #440). Runs inside the phone page's
// AudioContext. Capture: posts raw Float32 mic chunks to the main thread.
// Playback: consumes {type:'play'} Float32 chunks into a bounded buffer and
// outputs silence (never stale audio) when starved. No resampling here; the
// main thread resamples with continuous phase (see phone-audio.ts).

declare function registerProcessor(
  name: string,
  processor: typeof AudioWorkletProcessor,
): void;
declare const sampleRate: number;
// AudioWorklet realm global; the frontend tsconfig lib does not ship it.
declare const AudioWorkletProcessor: {
  new (): { port: MessagePort };
};

const PLAY_MAX_SAMPLES = Math.ceil(sampleRate * 0.6); // 600 ms bounded buffer

class PhoneAudioProcessor extends AudioWorkletProcessor {
  private play: Float32Array = new Float32Array(0);
  private playRead = 0;

  constructor() {
    super();
    this.port.onmessage = (ev: MessageEvent) => {
      const msg = ev.data as { type: string; pcm?: ArrayBuffer };
      if (msg.type === "play" && msg.pcm) {
        this.appendPlay(new Float32Array(msg.pcm));
      } else if (msg.type === "flush") {
        this.play = new Float32Array(0);
        this.playRead = 0;
      }
    };
  }

  private appendPlay(chunk: Float32Array): void {
    const buffered = this.play.length - this.playRead;
    const room = PLAY_MAX_SAMPLES - buffered;
    if (room <= 0) return; // bounded: full means new audio is dropped, never grows
    const take = Math.min(room, chunk.length);
    const merged = new Float32Array(buffered + take);
    merged.set(this.play.subarray(this.playRead));
    merged.set(chunk.subarray(0, take), buffered);
    this.play = merged;
    this.playRead = 0;
  }

  process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
  ): boolean {
    const input = inputs[0] && inputs[0][0];
    if (input && input.length > 0) {
      const copy = new Float32Array(input.length);
      copy.set(input);
      this.port.postMessage({ type: "mic", pcm: copy.buffer }, [copy.buffer]);
    }
    const output = outputs[0] && outputs[0][0];
    if (output) {
      let written = 0;
      while (written < output.length && this.playRead < this.play.length) {
        const n = Math.min(
          output.length - written,
          this.play.length - this.playRead,
        );
        output.set(this.play.subarray(this.playRead, this.playRead + n), written);
        this.playRead += n;
        written += n;
      }
      if (this.playRead > 0 && this.playRead === this.play.length) {
        this.play = new Float32Array(0);
        this.playRead = 0;
      }
      // Remaining samples stay zero: silence when starved, never stale audio.
    }
    return true;
  }
}

registerProcessor("phone-audio", PhoneAudioProcessor);
