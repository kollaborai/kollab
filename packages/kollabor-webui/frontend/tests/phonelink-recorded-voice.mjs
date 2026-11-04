import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const ARTIFACT_ROOT = "/private/tmp/k440.7vZ8mP";
const TEST_SESSION = "phone-voice-test-440-solo";
const MAX_RUN_MS = 120_000;

/**
 * Replace audio getUserMedia only in this page. The AudioContext and its resume
 * call are created synchronously from the real voice-button tap; the WAV is
 * played only after runRecordedVoiceCheck observes the real call_open success.
 */
export async function installRecordedVoiceHooks(page, wavBase64) {
  assert.equal(typeof wavBase64, "string");
  const wav = Buffer.from(wavBase64, "base64");
  assert.ok(wav.length > 44, "recorded input must be a non-empty WAV file");
  assert.equal(wav.toString("ascii", 0, 4), "RIFF", "recorded input must have a RIFF header");
  assert.equal(wav.toString("ascii", 8, 12), "WAVE", "recorded input must have a WAVE header");

  await page.addInitScript((encodedWav) => {
    const metrics = {
      microphoneRequests: 0,
      callOpen: null,
      openFailure: false,
      ticks: 0,
      responseAudioBytes: 0,
      responseNonzeroSamples: 0,
      callCloses: 0,
      microphoneBytes: 0,
      microphoneNonzeroSamples: 0,
      maxTickGapMs: 0,
      lastTickAt: 0,
      inputStarted: false,
      inputEnded: false,
      tickRoundTripsMs: [],
      tickErrors: [],
      workletMessages: 0,
      maxWorkletGapMs: 0,
      playbackQueuedSamples: null,
      playbackZeroSince: null,
      lastPlaybackStateAt: 0,
      lastOutputNonzeroAt: 0,
      audibleWindows: 0,
      outputQuietSince: null,
      lastOutputProbeAt: 0,
      renderedPeak: 0,
    };
    let audioContext = null;
    let destination = null;
    let captureDestination = null;
    let resumePromise = null;
    let recordedSource = null;
    let recorder = null;
    let recorderChunks = [];
    let finishPromise = null;
    let targetWorklet = null;
    let outputProbe = null;

    const decodeWav = () => {
      const binary = atob(encodedWav);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return audioContext.decodeAudioData(bytes.buffer);
    };

    const beginCapture = () => {
      if (recorder) return;
      if (!captureDestination || typeof MediaRecorder === "undefined") {
        throw new Error("This browser cannot record the phone voice output.");
      }
      const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
      const mimeType = types.find((type) => MediaRecorder.isTypeSupported(type));
      recorder = mimeType
        ? new MediaRecorder(captureDestination.stream, { mimeType })
        : new MediaRecorder(captureDestination.stream);
      recorderChunks = [];
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data?.size) recorderChunks.push(event.data);
      });
      recorder.start(250);
    };

    const stopCapture = async (stopInput = true) => {
      if (stopInput && outputProbe) { clearInterval(outputProbe); outputProbe = null; }
      if (stopInput && recordedSource) {
        try { recordedSource.stop(); } catch { /* it may have ended naturally */ }
        recordedSource.disconnect();
        recordedSource = null;
      }
      if (recorder && recorder.state !== "inactive") {
        await new Promise((resolve, reject) => {
          recorder.addEventListener("stop", resolve, { once: true });
          recorder.addEventListener("error", (event) => reject(event.error ?? new Error("Phone audio recording failed.")), { once: true });
          recorder.stop();
        });
      }
    };

    const hooks = {
      metrics,
      async startRecordedInput() {
        if (!audioContext || !destination) throw new Error("The voice button did not request audio capture.");
        if (resumePromise) await resumePromise;
        if (audioContext.state !== "running") await audioContext.resume();
        if (audioContext.state !== "running") throw new Error("The phone AudioContext did not resume from the voice-button tap.");
        if (recordedSource) throw new Error("Recorded input has already started.");
        const buffer = await decodeWav();
        recordedSource = audioContext.createBufferSource();
        recordedSource.buffer = buffer;
        recordedSource.connect(destination);
        recordedSource.onended = () => { metrics.inputEnded = true; };
        recordedSource.start();
        metrics.inputStarted = true;
        return { durationSeconds: buffer.duration, sampleRate: buffer.sampleRate };
      },
      async discardCapture() {
        await stopCapture();
      },
      async captureReply() {
        await stopCapture(false);
        recorder = null;
        recorderChunks = [];
        finishPromise = null;
        beginCapture();
        metrics.replyCaptureStarted = true;
      },
      async finishCapture() {
        if (finishPromise) return finishPromise;
        finishPromise = (async () => {
          await stopCapture();
          if (!recorder) throw new Error("The phone AudioWorklet output was not connected to the recorder.");
          const mimeType = recorder.mimeType || recorderChunks.find((chunk) => chunk.type)?.type || "";
          const blob = new Blob(recorderChunks, { type: mimeType });
          if (!blob.size || !mimeType.startsWith("audio/")) throw new Error("The phone voice output recording is empty or has no audio MIME type.");
          const decoded = await audioContext.decodeAudioData(await blob.arrayBuffer());
          let peak = 0;
          let squareSum = 0;
          let sampleCount = 0;
          for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
            const samples = decoded.getChannelData(channel);
            sampleCount += samples.length;
            for (const sample of samples) {
              const magnitude = Math.abs(sample);
              if (magnitude > peak) peak = magnitude;
              squareSum += sample * sample;
            }
          }
          const bytes = new Uint8Array(await blob.arrayBuffer());
          let binary = "";
          for (let offset = 0; offset < bytes.length; offset += 0x8000) {
            binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
          }
          const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16_000), 16_000);
          const renderedSource = offline.createBufferSource();
          renderedSource.buffer = decoded;
          renderedSource.connect(offline.destination);
          renderedSource.start();
          const mono = (await offline.startRendering()).getChannelData(0);
          const wave = new Uint8Array(44 + mono.length * 2);
          const view = new DataView(wave.buffer);
          const tag = (offset, text) => Array.from(text).forEach((c, i) => { wave[offset + i] = c.charCodeAt(0); });
          tag(0, "RIFF"); view.setUint32(4, wave.length - 8, true); tag(8, "WAVE");
          tag(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
          view.setUint16(22, 1, true); view.setUint32(24, 16_000, true);
          view.setUint32(28, 32_000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
          tag(36, "data"); view.setUint32(40, wave.length - 44, true);
          mono.forEach((sample, i) => view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true));
          let waveBinary = "";
          for (let offset = 0; offset < wave.length; offset += 0x8000)
            waveBinary += String.fromCharCode(...wave.subarray(offset, offset + 0x8000));
          return {
            mimeType,
            base64: btoa(binary),
            bytes: blob.size,
            durationSeconds: decoded.duration,
            peak,
            rms: sampleCount ? Math.sqrt(squareSum / sampleCount) : 0,
            waveBase64: btoa(waveBinary),
          };
        })();
        return finishPromise;
      },
    };
    Object.defineProperty(window, "__K440_RECORDED_VOICE__", {
      configurable: false,
      enumerable: false,
      value: hooks,
    });

    const mediaDevices = navigator.mediaDevices;
    const nativeGetUserMedia = mediaDevices.getUserMedia.bind(mediaDevices);
    Object.defineProperty(mediaDevices, "getUserMedia", {
      configurable: true,
      value(constraints) {
        if (!constraints?.audio) return nativeGetUserMedia(constraints);
        metrics.microphoneRequests++;
        if (!audioContext) {
          audioContext = new AudioContext();
          resumePromise = audioContext.resume();
          resumePromise.catch(() => undefined);
          destination = audioContext.createMediaStreamDestination();
        }
        return Promise.resolve(destination.stream);
      },
    });

    const NativeAudioWorkletNode = window.AudioWorkletNode;
    window.AudioWorkletNode = class extends NativeAudioWorkletNode {
      constructor(context, name, options) {
        super(context, name, options);
        if (name === "phone-audio") {
          targetWorklet = this;
          let previous = performance.now();
          this.port.addEventListener("message", (event) => {
            if (!["mic", "play_state", "reserve_ack", "play_ack"].includes(event.data?.type)) return;
            const now = performance.now();
            metrics.workletMessages++;
            metrics.maxWorkletGapMs = Math.max(metrics.maxWorkletGapMs, now - previous);
            previous = now;
            const queued = event.data?.queuedSamples;
            if (Number.isSafeInteger(queued) && queued >= 0) {
              if (queued > 0) metrics.playbackZeroSince = null;
              else if (metrics.playbackQueuedSamples !== 0) metrics.playbackZeroSince = now;
              metrics.playbackQueuedSamples = queued;
              metrics.lastPlaybackStateAt = now;
            }
          });
        }
      }
    };
    const nativeConnect = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function (target, ...rest) {
      const result = nativeConnect.call(this, target, ...rest);
      if (this === targetWorklet && target === this.context.destination) {
        // Input and product playback have separate contexts and destinations.
        // Feeding playback into the recorded input would create an echo loop.
        captureDestination = this.context.createMediaStreamDestination();
        const analyser = this.context.createAnalyser();
        analyser.fftSize = 2048;
        nativeConnect.call(this, analyser);
        nativeConnect.call(analyser, captureDestination);
        const samples = new Float32Array(analyser.fftSize);
        outputProbe = setInterval(() => {
          const now = performance.now();
          analyser.getFloatTimeDomainData(samples);
          let peak = 0; let energy = 0;
          for (const sample of samples) { peak = Math.max(peak, Math.abs(sample)); energy += sample * sample; }
          const audible = peak > 0.001 || Math.sqrt(energy / samples.length) > 0.0001;
          if (audible) metrics.audibleWindows++;
          metrics.renderedPeak = Math.max(metrics.renderedPeak, peak);
          if (audible || this.context.state !== "running") metrics.outputQuietSince = null;
          else if (metrics.outputQuietSince === null || now - metrics.lastOutputProbeAt > 250)
            metrics.outputQuietSince = now;
          metrics.lastOutputProbeAt = now;
        }, 25);
        beginCapture();
      }
      return result;
    };

    let realCallPacket = null;
    Object.defineProperty(window, "__KOLLAB_PHONE_CALL__", {
      configurable: true,
      enumerable: true,
      get() { return realCallPacket; },
      set(candidate) {
        if (typeof candidate !== "function") {
          realCallPacket = candidate;
          return;
        }
        realCallPacket = async (packet) => {
          const started = performance.now();
          const response = await candidate(packet);
          if (packet?.op === "call_open") {
            if (response?.ok === true && typeof packet.voice_session_id === "string") {
              metrics.callOpen = {
                ok: true,
                voiceSessionId: packet.voice_session_id,
                generation: packet.generation,
              };
            } else {
              metrics.openFailure = true;
            }
          } else if (packet?.op === "call_tick") {
            if (metrics.tickRoundTripsMs.length < 32) metrics.tickRoundTripsMs.push(Math.round(performance.now() - started));
            if (typeof response?.error === "string") metrics.tickErrors.push(response.error);
            metrics.ticks++;
            const now = performance.now();
            if (metrics.lastTickAt) metrics.maxTickGapMs = Math.max(metrics.maxTickGapMs, now - metrics.lastTickAt);
            metrics.lastTickAt = now;
            if (typeof packet.mic === "string") {
              const mic = atob(packet.mic);
              metrics.microphoneBytes += mic.length;
              for (let i = 0; i + 1 < mic.length; i += 2)
                if (mic.charCodeAt(i) || mic.charCodeAt(i + 1)) metrics.microphoneNonzeroSamples++;
            }
            const audio = typeof response?.audio === "string" ? response.audio : "";
            if (audio) {
              const binary = atob(audio);
              metrics.responseAudioBytes += binary.length;
              const before = metrics.responseNonzeroSamples;
              for (let i = 0; i + 1 < binary.length; i += 2) {
                if (binary.charCodeAt(i) !== 0 || binary.charCodeAt(i + 1) !== 0) metrics.responseNonzeroSamples++;
              }
              if (metrics.responseNonzeroSamples > before) metrics.lastOutputNonzeroAt = performance.now();
            }
          } else if (packet?.op === "call_close") {
            metrics.callCloses++;
          }
          return response;
        };
      },
    });
  }, wavBase64);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const contentText = (content) => {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((part) => typeof part === "string" ? part : part?.text ?? "").join(" ");
  return "";
};

function extensionForMime(mimeType) {
  const mime = mimeType.split(";", 1)[0].trim().toLowerCase();
  if (mime === "audio/webm") return ".webm";
  if (mime === "audio/mp4") return ".m4a";
  if (mime === "audio/ogg") return ".ogg";
  if (mime === "audio/wav" || mime === "audio/wave" || mime === "audio/x-wav") return ".wav";
  throw new Error(`Unsupported phone output recording MIME type: ${mime || "missing"}`);
}

/** Drive one real phone voice turn and save the phone AudioWorklet output. */
export async function runRecordedVoiceCheck(page, control, { outputPrefix, expectedPhrase, watchVoice }) {
  assert.equal(typeof control, "function");
  assert.equal(typeof watchVoice, "function");
  assert.equal(typeof expectedPhrase, "string");
  assert.ok(expectedPhrase.trim(), "expectedPhrase must not be empty");
  const prefix = path.resolve(outputPrefix);
  const relative = path.relative(ARTIFACT_ROOT, prefix);
  assert.ok(relative && !relative.startsWith("..") && !path.isAbsolute(relative),
    `outputPrefix must be inside ${ARTIFACT_ROOT}`);

  const hardDeadline = Date.now() + MAX_RUN_MS;
  const deadline = hardDeadline - 10_000; // reserve bounded cleanup time
  const remaining = () => Math.max(1, deadline - Date.now());
  const cleanupRemaining = () => Math.max(1, Math.min(5_000, hardDeadline - Date.now()));
  let attemptedStart = false;
  let opened = null;
  let observer = null;
  let observedTurnId = null;
  let observedUserText = "";
  let matchedReplyText = "";
  let expectedUiCounts = null;
  let outputBeforeInput = 0;
  const observedEvents = [];
  let replyGenerated = false;
  let latestVoiceState = null;
  let startupGenerated = false;
  let startupTurnId = null;
  let startupState = null;
  let captureReply = null;
  const transcripts = new Map();
  let captured = null;
  let result = null;
  let failure = null;
  const start = page.getByRole("button", { name: "Start voice mode", exact: true });
  const expected = expectedPhrase.toLocaleLowerCase();
  const visiblePair = () => page.evaluate(({ phrase, userText, assistantText }) => {
    const viewport = document.querySelector('[data-slot="aui_thread-viewport"]');
    const rows = (role) => Array.from(viewport?.querySelectorAll(`[data-role="${role}"]`) ?? []);
    const normalize = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const users = rows("user"); const assistants = rows("assistant");
    const count = (nodes) => nodes.filter((node) => node.textContent.toLowerCase().includes(phrase)).length;
    return { user: count(users), assistant: count(assistants),
      latestUser: !!userText && normalize(users.at(-1)?.textContent ?? "").includes(normalize(userText)),
      latestAssistant: !!assistantText && normalize(assistants.at(-1)?.textContent ?? "").includes(normalize(assistantText)) };
  }, { phrase: expected, userText: observedUserText, assistantText: matchedReplyText });
  const correctPair = (pair) => expectedUiCounts && pair.user === expectedUiCounts.user &&
    pair.assistant === expectedUiCounts.assistant && pair.latestUser && pair.latestAssistant;
  const waitForPlaybackDrain = (afterAudibleWindows) => page.waitForFunction((minimum) => {
    const m = window.__K440_RECORDED_VOICE__?.metrics;
    const now = performance.now();
    // RTP keeps sending silence; queued PCM need not be empty while output is
    // quiet. Observe the actual rendered signal, with fresh continuous probes.
    return m && m.audibleWindows > minimum && typeof m.outputQuietSince === "number" &&
      now - m.outputQuietSince >= 1_000 && now - m.lastOutputProbeAt < 250 &&
      now - m.lastPlaybackStateAt < 250;
  }, afterAudibleWindows, { timeout: remaining() });
  const baseline = await withTimeout(control("GET", `/sessions/${TEST_SESSION}/history?limit=50`), remaining());
  const priorVoiceTurns = new Set((baseline.history ?? []).map((row) => row.metadata?.voice_turn_id).filter(Boolean));
  const previousRecognitions = (baseline.history ?? []).filter((row) => row?.role === "user" &&
    contentText(row.content).toLocaleLowerCase().includes(expected)).length;

  try {
    await start.waitFor({ state: "visible", timeout: remaining() });
    attemptedStart = true;
    await start.click({ timeout: remaining() });

    await page.waitForFunction(() => {
      const state = window.__K440_RECORDED_VOICE__?.metrics;
      return state?.callOpen?.ok === true || state?.openFailure === true;
    }, undefined, { timeout: remaining() });
    opened = await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.metrics.callOpen), remaining());
    assert.ok(opened?.ok, "the actual PhoneLink call_open did not succeed");
    assert.equal(typeof opened.voiceSessionId, "string");

    observer = await withTimeout(watchVoice(opened.voiceSessionId, (event) => {
      const p = event.payload;
      if (event.voice_session_id !== opened.voiceSessionId || event.generation !== opened.generation) return;
      if (["voice.state", "voice.metrics"].includes(event.event)) {
        observedEvents.push({ event: event.event, state: p?.state, turnId: event.turn_id,
          firstAudio: p?.first_audio_ms, totalTurn: p?.total_turn_ms });
        if (observedEvents.length > 12) observedEvents.shift();
      }
      if (event.event === "voice.state") {
        startupState = p?.state;
        if (observedTurnId) latestVoiceState = p?.state;
        return;
      }
      if (event.event === "voice.metrics" && !observedTurnId && typeof p?.total_turn_ms === "number") {
        startupGenerated = true;
        startupTurnId = event.turn_id;
      }
      if (event.event === "voice.metrics" && observedTurnId && event.turn_id === observedTurnId) {
        // Deferred replies use session.say and intentionally omit turn metrics.
        // Actual rendered output, rather than this optional telemetry, is the oracle.
        if (typeof p?.total_turn_ms === "number") replyGenerated = true;
        return;
      }
      if (event.event !== "voice.transcript" ||
          p?.voice_session_id !== opened.voiceSessionId || !p.final || p.speaker !== "user") return;
      const key = `${p.turn_id}:${p.revision}`;
      const chunks = transcripts.get(key) ?? new Map();
      chunks.set(p.chunk_index, p.display_text_chunk);
      transcripts.set(key, chunks);
      if (chunks.size !== p.chunk_count || observedTurnId) return;
      const text = Array.from({ length: p.chunk_count }, (_, i) => chunks.get(i) ?? "").join("");
      if (!text.toLowerCase().includes(expected)) return;
      observedTurnId = p.turn_id;
      observedUserText = text;
    }, opened.generation), remaining());

    console.log("voice call opened", JSON.stringify(opened));
    // Speak after the normal welcome has finished and the real mic pipeline
    // has acknowledged a packet, as a person waiting for Listening would do.
    let greeting = null;
    while (Date.now() < deadline) {
      const welcome = await withTimeout(control("GET", `/sessions/${TEST_SESSION}/history?limit=50`), remaining());
      greeting = (welcome.history ?? []).findLast((row) => row.role === "assistant" &&
        row.metadata?.voice_turn_id && !priorVoiceTurns.has(row.metadata.voice_turn_id) && contentText(row.content).trim());
      if (greeting && startupState === "listening") break;
      await sleep(Math.min(200, remaining()));
    }
    assert.ok(greeting && startupState === "listening", "the initial voice greeting did not finish");
    startupTurnId = greeting.metadata.voice_turn_id;
    await page.waitForFunction(() => window.__K440_RECORDED_VOICE__?.metrics.ticks > 0,
      undefined, { timeout: remaining() });
    await waitForPlaybackDrain(0);
    assert.ok(greeting && !contentText(greeting.content).toLowerCase().includes(expected),
      "the startup greeting must be distinct from the expected reply");
    outputBeforeInput = await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.metrics.audibleWindows), remaining());
    // Start a fresh output window after measured welcome quiet, before input.
    // A delayed welcome tail cannot satisfy the distinct canonical reply check.
    captureReply = withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.captureReply()), remaining());
    await captureReply;
    const previousUi = await withTimeout(visiblePair(), remaining());
    expectedUiCounts = { user: previousUi.user + 1, assistant: previousUi.assistant + 1 };
    await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.startRecordedInput()), remaining());

    let matchedHistory = null;
    while (Date.now() < deadline) {
      const response = await withTimeout(
        control("GET", `/sessions/${TEST_SESSION}/history?limit=50`), remaining(),
      );
      const history = Array.isArray(response?.history) ? response.history : [];
      // LiveKit stores trusted voice origin separately; its user row has no
      // legacy microphone metadata. The observed final transcript and following
      // assistant's exact voice_turn_id establish this run's correlation.
      const userIndex = history.findLastIndex((row) => row?.role === "user" &&
        contentText(row.content).toLocaleLowerCase().includes(expected));
      const recognitions = history.filter((row) => row?.role === "user" &&
        contentText(row.content).toLocaleLowerCase().includes(expected)).length;
      if (userIndex >= 0 && recognitions > previousRecognitions) {
        const following = history.slice(userIndex + 1);
        const nextUser = following.findIndex((row) => row?.role === "user");
        const assistant = following.slice(0, nextUser < 0 ? undefined : nextUser).find((row) =>
          row?.role === "assistant" && observedTurnId && row.metadata?.voice_turn_id === observedTurnId &&
          contentText(row.content).toLocaleLowerCase().includes(expected));
        if (assistant) {
          matchedHistory = { userIndex, voiceTurnId: observedTurnId };
          matchedReplyText = contentText(assistant.content);
          break;
        }
      }
      await sleep(Math.min(750, remaining()));
    }
    assert.ok(matchedHistory, "the exact test session history did not contain the recognized phrase and matching assistant reply");

    while (Date.now() < deadline) {
      const pair = await withTimeout(visiblePair(), remaining());
      if (correctPair(pair)) break;
      await sleep(Math.min(250, remaining()));
    }
    assert.ok(correctPair(await withTimeout(visiblePair(), remaining())),
      "the phone UI must render both the voice transcript and its reply once");
    assert.ok(deadline - Date.now() >= 6_500, "the full persistence interval must fit before the acceptance deadline");
    await sleep(6_500);
    assert.ok(correctPair(await withTimeout(visiblePair(), remaining())),
      "the voice transcript and reply must survive the former five-second cleanup");
    await page.screenshot({ path: prefix + "-ui.png", timeout: remaining() });

    // GET session status describes runtime readiness. Live speaking/listening
    // transitions come from the scoped worker SSE stream instead.
    while (latestVoiceState !== "listening" && Date.now() < deadline) {
      await sleep(Math.min(250, remaining()));
    }
    assert.ok(latestVoiceState === "listening" && captureReply,
      "the matched reply did not return to listening");
    await captureReply;
    await waitForPlaybackDrain(outputBeforeInput);

    await page.waitForFunction(() => {
      const state = window.__K440_RECORDED_VOICE__?.metrics;
      return state?.ticks > 0 && state?.responseAudioBytes > 0 && state?.responseNonzeroSamples > 0;
    }, undefined, { timeout: remaining() });
    // Let the real phone AudioWorklet render the returned samples into the
    // parallel MediaStreamDestination before finalizing the captured clip.
    await sleep(Math.min(900, remaining()));
    captured = await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.finishCapture()), remaining());
    assert.ok(captured.bytes > 0 && captured.durationSeconds > 0.1, "the rendered phone audio clip is empty");
    assert.ok(captured.peak > 0.001 && captured.rms > 0.0001,
      "the recorded AudioWorklet output contains no audible samples");

    const extension = extensionForMime(captured.mimeType);
    const artifactPath = prefix + extension;
    await mkdir(path.dirname(artifactPath), { recursive: true });
    const clip = Buffer.from(captured.base64, "base64");
    assert.equal(clip.length, captured.bytes);
    await writeFile(artifactPath, clip, { flag: "wx", mode: 0o600 });
    const wavePath = prefix + "-rendered.wav";
    await writeFile(wavePath, Buffer.from(captured.waveBase64, "base64"), { flag: "wx", mode: 0o600 });

    const metrics = await withTimeout(page.evaluate(() => {
      const { microphoneRequests, callOpen, ticks, responseAudioBytes, responseNonzeroSamples } =
        window.__K440_RECORDED_VOICE__.metrics;
      return { microphoneRequests, callOpen, ticks, responseAudioBytes, responseNonzeroSamples };
    }), remaining());
    result = {
      voiceSessionId: opened.voiceSessionId,
      generation: opened.generation,
      voiceTurnId: matchedHistory.voiceTurnId,
      visibleAfterPlayback: true,
      ...metrics,
      clip: { path: artifactPath, wavePath, bytes: captured.bytes, mimeType: captured.mimeType,
        durationSeconds: captured.durationSeconds, peak: captured.peak, rms: captured.rms },
    };
  } catch (error) {
    failure = error;
    console.error("recorded voice diagnostics", JSON.stringify(await withTimeout(page.evaluate(() => ({
      ...window.__K440_RECORDED_VOICE__?.metrics,
    })), cleanupRemaining()).catch(() => ({ unavailable: true }))));
    console.error("voice observer diagnostics", JSON.stringify({ startupGenerated, startupState,
      observedTurnId, replyGenerated, latestVoiceState, events: observedEvents }));
  } finally {
    if (attemptedStart) {
      if (!captured) {
        try { await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__.discardCapture()), cleanupRemaining()); }
        catch { /* failure path: best-effort stop of the synthetic input and recorder */ }
      }
      try {
        const stop = page.getByRole("button", { name: "Stop voice room", exact: true });
        if (await withTimeout(stop.count(), cleanupRemaining())) {
          await stop.click({ timeout: cleanupRemaining() });
          await page.waitForFunction(() =>
            window.__K440_RECORDED_VOICE__?.metrics.callCloses > 0,
            undefined, { timeout: cleanupRemaining() });
        } else if (await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__?.metrics.callOpen?.ok), cleanupRemaining())) {
          throw new Error("The active phone voice room has no real Stop voice room button.");
        }
        if (opened) {
          let view;
          const stopDeadline = Date.now() + cleanupRemaining();
          do {
            view = await withTimeout(control("GET", `/voice/sessions/${opened.voiceSessionId}`), cleanupRemaining());
            if (view.state === "stopped") break;
            await sleep(Math.min(150, cleanupRemaining()));
          } while (Date.now() < stopDeadline);
          assert.equal(view.state, "stopped", "the real Stop button must stop its owned voice session");
          const pair = await withTimeout(visiblePair(), cleanupRemaining());
          if (result) assert.ok(correctPair(pair), "stopping voice must preserve both chat messages");
          if (result) result.stoppedCleanly = true;
        }
      } catch (error) {
        if (!failure) failure = error;
        // A failed UI stop is still a failed acceptance check. Reap only the
        // exact call opened by this run so a failed test cannot retain ownership.
        if (!opened) opened = await withTimeout(page.evaluate(() => window.__K440_RECORDED_VOICE__?.metrics.callOpen), cleanupRemaining()).catch(() => null);
        if (opened) await withTimeout(control("DELETE", `/voice/sessions/${opened.voiceSessionId}`,
          { generation: opened.generation, reason: "user" }), cleanupRemaining()).catch(() => undefined);
      }
    }
    if (observer) await withTimeout(observer.close(), cleanupRemaining());
  }

  if (failure) throw failure;
  // A greeting cannot satisfy reply-audio acceptance: independently transcribe
  // the rendered output clip using the already installed local Whisper model.
  const python = "/private/tmp/k440.7vZ8mP/voice/runtimes/ef1867859dbaa365/bin/python";
  const verify = `import json,sys,wave,numpy as np\nfrom faster_whisper import WhisperModel\nwith wave.open(sys.argv[1],'rb') as w:\n assert w.getnchannels()==1 and w.getframerate()==16000 and w.getsampwidth()==2\n audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768\nmodel=WhisperModel('/private/tmp/k440.7vZ8mP/voice/models/whisper-base/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66',device='cpu',compute_type='int8',local_files_only=True,cpu_threads=2)\nsegments,_=model.transcribe(audio,language='en',beam_size=5,vad_filter=True)\nprint(json.dumps({'text':' '.join(s.text for s in segments)}))`;
  const { stdout } = await promisify(execFile)(python, ["-c", verify, result.clip.wavePath], {
    timeout: Math.min(30_000, remaining()), maxBuffer: 100_000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
  const transcript = JSON.parse(stdout).text;
  const normalized = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  assert.ok(normalized(transcript).includes(normalized(expectedPhrase)),
    "the rendered phone clip does not contain the requested spoken reply");
  assert.ok(normalized(transcript).includes(normalized(matchedReplyText)),
    "the rendered phone clip does not contain the full matched assistant reply");
  result.clip.replyRecognized = true;
  result.clip.recognizedText = transcript.trim();
  return result;
}

function withTimeout(promise, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("The recorded voice acceptance deadline expired.")), timeoutMs);
    promise.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
