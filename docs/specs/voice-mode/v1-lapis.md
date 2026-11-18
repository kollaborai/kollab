# Voice Mode Specification — v1 (lapis)

Author: lapis | Status: draft v1 | Scope: model pipeline lane
Sibling specs: v1-koordinator.md, v1-sapphire.md, v1-aquamarine.md, v1-zircon.md, v1-bismuth.md
(read-only constraint: written independently, peer specs not consulted)

## 1. Overview

`/voicemode` turns kollab into an always-listening conversational assistant.
Speech in, transcribed locally by Whisper, gated by a tiny local model that
decides whether the utterance deserves a real LLM response. Responses are
spoken back through TTS. One assistant voice from the user's point of view —
the agent collective acts as one participant in the conversation.

```
mic ──> VAD ──> faster-whisper ──> transcript (JSONL)
                                        │
                              segment finalize (silance | 5-10s)
                                        ▼
                              LOCAL GATEKEEPER (~2s budget)
                              verdict: act | converse | silence
                                        │
                    ┌───────────────────┼──────────────────┐
                  act               converse            silence
                    │                   │                   │
              full LLM             full LLM            (nothing;
              dispatch             short reply,         keep listening)
              (work agent)         keep listening
                                        │
                                     TTS out (one-sentence units)
```

## 2. Canonical requirements (fixed, from owner via koordinator)

- `/voicemode` slash command toggles voice mode on/off.
- Whisper runs locally via faster-whisper; weights downloaded from Hugging
  Face on first run, never bundled with kollab.
- Recorder is always-on while voice mode is active.
- Transcript is a continuous JSONL stream.
- Segment finalize on silence detection OR a 5-10s window (variable).
- Dispatch is AGENT-GATED: a triage step decides `act | converse | silence`
  within a ~2s budget, and the mic keeps listening during triage.
- Ambient prompt is strict: "you are always on", "not everything is about
  the project", small acknowledgments ("uh huh", "okay") are valid
  continuations — they are conversations, not tasks.
- Agents present as ONE collective from the user's POV.

## 3. Model pipeline (lapis lane — the emphasis)

### 3.1 STT: primary = whisper-large-v3-turbo (license-cleaned 2026-09-25)

License sweep verdict (owner: engine software, MIT/Apache-2.0 only):
parakeet v3 DISQUALIFIED — cc-by-4.0 weights (verified HF API tags),
not distributable in a package. Fastest license-clean pick is turbo.

| model                | EN WER | RTFx  | realtime (M4 Pro) | license     |
|----------------------|--------|-------|-------------------|-------------|
| whisper large-v3-turbo| 7.83% | 350   | ~14x              | MIT (pick)  |
| moonshine-base       | ~5-6%  | —     | streaming-first   | MIT (r-up)  |
| canary 1B v2         | 7.15%  | 749   | —                 | closed (out)|
| parakeet TDT 0.6B v3 | 6.32%  | 3333  | ~60-103x          | cc-by (OUT) |

Sources: HF Open-ASR Leaderboard, whispernotes.app M4 Pro/M5 measurements
(parakeet 2.91s vs turbo 20.92s on a 5-min podcast = 103x vs 14.3x),
moonshine-voice docs.

- DEFAULT: whisper-large-v3-turbo (1.6GB, ~2.3GB RAM, 7.83% leaderboard
  WER, RTFx 350, ~14x RT on M4 Pro) via faster-whisper — the fastest
  license-clean (MIT) STT available. vad_filter=True MANDATORY (44%
  silence hallucination unmitigated).
- FALLBACK/compat: faster-whisper small (466MB, ~4-5x RT, 3.4% EN WER) —
  weak machines, 8GB Macs.
- MULTILINGUAL: whisper family covers 100+ languages (same turbo weights).
- RUNNER-UP: moonshine-base (MIT, 2024-10, streaming-first, ~5-6% EN WER
  class) — swap-in if whisper's chunked latency ever bothers us; its
  streaming decode is the better fit for live-mode, turbo's accuracy+tool
  ecosystem wins the default.

Config surface becomes:

    voice.stt_engine = "whisper-turbo"  # default (large-v3-turbo)
    # "whisper-small"  → fallback (weak machines)
    # "moonshine"      → streaming runner-up

### 3.1b STT (legacy whisper table, kept for reference)

Whisper tier (faster-whisper) — still the fallback engine:

| variant       | params | disk  | RAM   | EN WER | speed (M1)  |
|---------------|--------|-------|-------|--------|-------------|
| tiny          | 39M    | 75MB  | 273MB | 7.6%   | ~10x RT     |
| base          | 74M    | 142MB | 388MB | 5.0%   | ~7x RT      |
| small (DEF)   | 244M   | 466MB | 852MB | 3.4%   | ~4-5x RT    |
| large-v3-turbo| 809M   | 1.6GB | 2.3GB | 2.5%   | ~8x RT*     |
| large-v3      | 1.55B  | 2.9GB | 3.9GB | 2.4%   | ~1x RT      |

*turbo distilled: 4 decoder layers; RTFx ~350 per HF leaderboard.

### 3.2 HF download flow (first run, never bundled)

- whisper path: `WhisperModel("small")` auto-downloads from Systran/
  faster-whisper-* repos. moonshine path: onnx weights ship in the
  UsefulSensors repo itself. Same hub mechanics, same cache, same
  offline-after-first behavior.
- vad_filter=True MANDATORY on whisper path (turbo: 44% silence
  hallucination). Light VAD always on for segment-finalize boundaries.
- compute_type: int8 on arm64 CPU for the whisper fallback (no Metal in
  faster-whisper; int8 keeps small/turbo comfortably >realtime).

- `WhisperModel("small")` triggers huggingface_hub.hf_hub_download from
  Systran/faster-whisper-* repos: version-aware, resumable, atomic.
- Cache: `~/.cache/huggingface/hub` by default; honor `HF_HUB_CACHE`.
- First run UX: `/voicemode` with no cached weights shows a one-line
  "downloading whisper small (466MB)…" progress state, then starts.
- Offline-after-first: after cache hit, load with `local_files_only=True`
  and set HF_HUB_OFFLINE behavior; no network needed ever again.
- Failure handling: no network on first run → clear error, voice mode
  refuses to start (do not half-start). Corrupted cache → nuke the model
  dir and re-download.

### 3.3 The gatekeeper: tiny LOCAL decision model

owner's delta: the gate runs on a tiny local model, so ambient/no-op
turns cost one cheap inference, and the big LLM is never woken for "uh
huh"-tier utterances.

Gatekeeper duties (fast, structured):
  input : last transcript segment + rolling conversation context (compact)
  output: verdict ∈ {act, converse, silence} + optional one-line reason
  budget: target ≤2s wall-clock on M1, hard cap 3s
  default-on-timeout: `converse` (fail soft, never drop the user)

Candidate runtimes (all on-device, no server), license-cleaned:
  A. DEFAULT: Qwen3-0.6B (apache-2.0, 2025-04) via onnx-community/Qwen3-
     0.6B-ONNX — newest license-clean small instruct with a live ONNX
     conversion, conversational, 40K ctx, JSON-verdict-capable. Replaces
     the earlier Qwen2.5-0.5B pick (older, same family, apache-2.0).
  B. (retired) jev — owner's local decision model; declined before
     ever being located. voice.gate_model stays as a generic user-override
     hook for ANY local GGUF/ONNX.
  C. classifier head / rule+embedding hybrid — cheapest, least flexible;
     not recommended as primary. SmolLM3 considered and DISQUALIFIED
     (cc-by-nc-4.0, non-commercial).

Recommendation: ship A as the gatekeeper, downloaded from HF on first
voice-mode start — same download-once/offline-forever flow as STT/TTS,
keeps the kollab bundle light (nothing bundled). Gatekeeper must support
structured output (JSON verdict) and the strict system prompt (see 3.4).
Runs in BACKGROUND as a persistent process (preloaded, warm inference) so
gate latency is model compute only, no cold start per segment.

### 3.4 Prompting the two tiers

Gatekeeper system prompt (strict, short):
  "You are an always-on ambient assistant gate. The user speaks freely;
  most speech is conversation, not commands. Classify the utterance:
  act = user wants work done (task, research, code, reminder);
  converse = user is chatting, asks a quick question, or expects a brief
  reply; silence = nothing worth responding to. Reply JSON only:
  {\"verdict\": \"act|converse|silence\", \"reason\": \"<10 words>\"}.
  When unsure, prefer converse."

LLM tiers after the gate:
  act      → dispatch to work-agent lane (existing hub/agent machinery),
             inject full rolling transcript context.
  converse → short-reply lane: strict "one sentence, conversational,
             no markdown" instruction; goes straight to TTS.
  silence  → no LLM call at all. This is the cost win.

### 3.5 TTS (outbound) — VibeVoice-Realtime-0.5B (license-cleaned pick)

- One-sentence response cap per turn (canonical: "construct one sentence,
  then send to TTS").
- ENGINE: microsoft/VibeVoice-Realtime-0.5B (MIT, 2025-12) — newest
  license-clean realtime TTS; streaming text input matches our sentence-
  unit atomic flow natively; robust long-form generation for act-reports;
  already present in owner's HF cache; onnx-community ONNX conversion
  published 2026-09. One integration check owed: diff the conversion
  repo's VIBEVOICE_LICENSE file vs MIT before shipping.
- RUNNER-UP: Kokoro-82M (apache-2.0, 2025-04; 82M, ~330MB RAM, sub-second
  first audio on M-series CPU, fixed voice set, 11.7M downloads). If
  VibeVoice integration stumbles or the license file check fails, Kokoro
  takes the slot — it is proven, tiny, and apache-clean.
- Rejected: f5-tts-mlx (cloning-oriented, mlx dependency), XTTS v2
  (cloning, heavier), Piper (robotic), csm-1b (cc-by-4.0 — OUT), cloud
  TTS (voice mode stays local after first-run downloads).
- voice.tts_engine = "vibevoice" | "kokoro" | "piper" (escape hatch),
  voice.tts_voice for chosen voice id.
- Streaming-friendly: sentence is the atomic TTS unit; a multi-sentence
  reply plays as a queue of sentence-chunks, and any new user speech
  triggers a soft cancel of pending TTS (barge-in), see 4.2.

## 4. Explicit recommendations (requested deliverables)

  [R1] Dispatch target      : act → existing agent dispatch (koordinator
                              queue / active work agent); converse →
                              lightweight session with the SAME provider
                              profile as the chat, one-shot, no history
                              beyond a compact rolling summary.
  [R2] Queue vs interrupt   : BARGE-IN model. User speech always wins:
                              new finalized segment during TTS playback
                              cancels queued TTS chunks immediately
                              (finish the currently playing one at most);
                              during LLM generation, let the in-flight
                              call finish but suppress TTS of a superseded
                              reply. Reason: always-on mics with queues
                              feel broken within minutes of use.
  [R3] Terminal vs web-ui   : terminal-only for v1. Reasons: [a] mic
                              capture, VAD, and audio device handling are
                              hard enough without browser permission
                              model; [b] repo precedent (crys-017): ship
                              terminal-first, add web-ui explicitly later;
                              [c] the recorder is a local daemon pattern
                              that maps to the terminal plugin model.
  [R4] Transcript retention : JSONL session file under
                              ~/.kollab/projects/<proj>/voice/<ts>.jsonl,
                              capped rolling retention (default: keep last
                              30 days), each line = {ts, audio_ms, text,
                              verdict, acted}. Transcripts are user data —
                              never sent anywhere except to local whisper
                              and the gated LLM context; not included in
                              goal/insight pipelines by default.
  [R5] Gatekeeper model     : Qwen3-0.6B (apache-2.0, ONNX), local,
                              background-resident, HF-download on first
                              run. jev retired (owner opted out);
                              voice.gate_model = override hook. Never a
                              cloud call; never the big LLM.

## 5. Non-goals / open items

- Speaker diarization (single-user assumption for v1).
- Wake-word ("hey kollab") — optional future; VAD+gate makes it unnecessary.
- Translation, multi-language UX polish — whisper is multilingual but
  prompts/UI are English-first v1.
- RESOLVED: gatekeeper identity — owner opted out of jev entirely;
  new local default picked (Qwen2.5-0.5B-Instruct int4), see 3.3.
- RESOLVED (was open): TTS engine — picked Kokoro 82M, see 3.5.
- OPEN: whether `act` verdicts should carry an audio ack ("on it") via
  converse-lane TTS before dispatch completes. My vote: yes, cheap and
  makes the assistant feel alive.

## 6. Sources

- openwhispr.com/blog/whisper-model-sizes-explained (dual-source table)
- lexawrite.ai/blog/whisper-model-comparison (M1/Metal benchmarks)
- huggingface.co/openai/whisper-large-v3-turbo (leaderboard evals)
- inferencebench.io turbo H100 benchmark (silence hallucination, 44%)
- codersera.com faster-whisper-vs-whisper-cpp-2026 (runtime matrix)
- macgpu.com 2026 STT runbook (chunking/latency thresholds)
- pyproject.toml (dep audit: zero ML deps today, no conflicts)
- whispernotes.app/blog/parakeet-v3-default-mac-model (parakeet v3 vs
  turbo on M4 Pro/M5: 6.32% vs 7.83% WER, 103x vs 14.3x RT)
- huggingface.co/spaces/hf-audio/open_asr_leaderboard (parakeet 3333 RTFx,
  canary 749, turbo 350)
- moonshine-voice.readthedocs.io (moonshine benchmark methodology)
- freevoicereader.com / tts.swaroop.se (Kokoro vs F5-TTS Mac comparison)
