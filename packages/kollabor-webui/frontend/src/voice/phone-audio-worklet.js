// Phone call audio worklet (lane C, #440). Runs inside the phone page's
// AudioContext. Capture: posts raw Float32 mic chunks to the main thread.
// Playback: consumes {type:'play'} Float32 chunks into a bounded buffer and
// outputs silence (never stale audio) when starved. No resampling here; the
// main thread resamples with continuous phase (see phone-audio.ts).






// AudioWorklet realm global; the frontend tsconfig lib does not ship it.




const PLAY_MAX_SAMPLES = Math.ceil(sampleRate * 0.6); // 600 ms bounded buffer

class PhoneAudioProcessor extends AudioWorkletProcessor {
          play               = new Float32Array(0);
          playRead = 0;
          droppedSamples = 0;

  constructor() {
    super();
    this.port.onmessage = (ev              ) => {
      const msg = ev.data                                       ;
      if (msg.type === "play_reserve" && Number.isSafeInteger(msg.samples) && msg.samples >= 0 && msg.samples <= PLAY_MAX_SAMPLES) {
        const drop = Math.max(0, this.play.length - this.playRead + msg.samples - PLAY_MAX_SAMPLES);
        this.playRead += drop;
        this.droppedSamples += drop;
        this.port.postMessage({ type: "reserve_ack", id: msg.id,
          queuedSamples: this.play.length - this.playRead, droppedSamples: this.droppedSamples });
      } else if (msg.type === "play" && msg.pcm) {
        this.appendPlay(new Float32Array(msg.pcm));
        this.port.postMessage({ type: "play_ack", id: msg.id,
          queuedSamples: this.play.length - this.playRead, droppedSamples: this.droppedSamples });
      } else if (msg.type === "flush") {
        this.play = new Float32Array(0);
        this.playRead = 0;
      }
    };
  }

          appendPlay(chunk              )       {
    const buffered = this.play.length - this.playRead;
    const overflow = Math.max(0, buffered + chunk.length - PLAY_MAX_SAMPLES);
    const skipOld = Math.min(buffered, overflow);
    const old = this.play.subarray(this.playRead + skipOld);
    const newest = chunk.subarray(overflow - skipOld);
    const merged = new Float32Array(old.length + newest.length);
    merged.set(old);
    merged.set(newest, old.length);
    this.droppedSamples += overflow;
    this.play = merged;
    this.playRead = 0;
  }

  process(
    inputs                  ,
    outputs                  ,
  )          {
    const input = inputs[0] && inputs[0][0];
    const output = outputs[0] && outputs[0][0];
    if (output) {
      output.fill(0);
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
    const state = { queuedSamples: this.play.length - this.playRead, droppedSamples: this.droppedSamples };
    if (input && input.length > 0) {
      const copy = new Float32Array(input);
      this.port.postMessage({ type: "mic", pcm: copy.buffer, ...state }, [copy.buffer]);
    } else if (output) {
      this.port.postMessage({ type: "play_state", ...state });
    }
    return true;
  }
}

registerProcessor("phone-audio", PhoneAudioProcessor);
