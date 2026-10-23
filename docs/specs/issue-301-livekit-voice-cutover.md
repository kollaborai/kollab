# Issue #301: classifier-free LiveKit voice cutover

Status: implementation checklist; not evidence of acceptance or a live rollout.
Source baseline: `release/0.18.0` at `b1c8c514`.

## Fixed integration contract

- Use one Engine-managed LiveKit room path and the owner-sealed #299 contract:
  one `VoiceService` / `SpeechAdapter` / KVA1 streaming-PCM owner.
- Room audio goes through Parakeet-MLX STT, then into the selected session and
  thread through the existing RuntimeHost/session history, admission, and
  `PermissionManager` path. The configured LLM provider handles the turn with
  thinking low/off. VibeVoice Carter at five steps streams the reply back to the
  same room.
- WebUI and PhoneLink use this same path. Do not add a second LiveKit
  integration, model manager, worker-side LLM/tool path, or permission bypass.
  Keep the existing dangerous-command blocklist in the normal guarded turn.
- Keep each user's existing provider, API-key, and model settings intact.
  Live E2E, Engine/WebUI restarts, and audio demos are gated on explicit
  approval; #299 currently forbids them.

## Removal and refactor inventory

Classifier-only code and model assets to remove after replacement coverage is
in place:

- `packages/kollabor-voice/src/kollabor_voice/classifiers.py`, `laya.py`,
  `qwen.py`, `laya_voice_model.py`, `laya_worker.py`, and `qwen_worker.py`.
- Tracked `voice-intent-v1.safetensors` and `spoken-style-v1.safetensors`; the
  classifier/Laya/Qwen model descriptors, pins, and assets in `manifest.json`.
  Retain the Parakeet and VibeVoice manifest configuration.
- Classifier-specific review/ack/observer paths in `speech_review.py`,
  `acks.py`, and `observer.py`; remove the `voice-classifier` speech-cache
  producer, not unrelated progress/audio caching.

Refactor callers; do not remove their non-classifier lifecycle blindly:

- `packages/kollabor-voice/src/kollabor_voice/service.py`: classifier and
  speech-review state/jobs, RPC ops, preparation/submission/result handling,
  and owned worker lifecycle. `client.py`: `voice_out` and `review_speech`
  review polling.
- `plugins/voice_plugin.py`: classifier selection/preparation, decision and
  observer/log state, classifier acknowledgements, and speech-review path.
  Preserve or replace capture/session lifecycle as one coherent path rather
  than deleting it without an integration owner.
- `kollabor/panels/voice.py`: classifier-only settings/status. Preserve other
  voice settings only where still supported by the target design.
- `kollabor/commands/system_commands/handlers/voicemode.py`: classifier
  command/status/log surfaces; `plugins/altview/voice_classifier_altview.py`:
  classifier decision UI. Update the classifier-specific `said` activity field
  in `kollabor/state/snapshots.py` and WebUI `voice-mode.tsx`.
- Audit classifier-only tests and `packages/kollabor-voice/bench/` training,
  data, and result artifacts. Keep unrelated STT, echo, and speaker-gate
  coverage/assets.

Preference boundary is a prerequisite, not a cleanup detail:

- `classifiers.py` currently provides `load_preferences` used by `speech.py`,
  `transcription.py`, and `speech_cache.py` for general speech settings and TTS
  cache identity. Move that persistence seam to a classifier-independent
  settings module first; preserve stored STT/TTS/VibeVoice values and cache
  behavior.
- Keep and extend `speech_settings.py` for supported speech-engine settings.
  Do not move or overwrite LLM credentials: direct turns must keep resolving the
  user's existing provider/API-key/model profile through the Engine.
- Remove only tracked classifier source/model assets in this task branch. Do
  not purge user model caches, delete external model/runtime artifacts, or
  terminate a live external process.

## Commit sequence

1. Freeze the source inventory/contract and establish a reproducible baseline.
2. Integrate the LiveKit room/audio adapter with the #299 owner path; route
   Parakeet transcripts through the selected session/thread.
3. Connect direct LLM turns and streaming VibeVoice; preserve user settings,
   low/off thinking, blocklist, and permission fencing.
4. Remove classifier workers/models/preferences/review/ack/observer callers and
   dead UI paths only after replacement tests cover them.
5. Finish regression, lifecycle, import-residue, latency, and product-readiness
   gates; update final user-facing documentation.

## Validation and latency plan

- Unit/integration coverage: room lifecycle and failure handling; Parakeet
  transcripts; selected session/thread; provider/key/model preservation;
  dangerous-command blocklist and `PermissionManager`; VibeVoice streaming and
  errors; repeated owned-service shutdown with no orphan workers.
- Add a negative import/reference check proving classifier modules, operations,
  dependencies, and model assets are absent. Run voice, engine-safety, WebUI,
  and PhoneLink suites.
- Before/after latency: same approved host, fixed fixture-turn corpus,
  provider/model/settings, and documented warm/cold conditions. Use three
  unmeasured warmups and at least 30 measured turns per condition. Record
  environment and sample counts plus p50/p95 for transport/capture, STT, LLM
  first-token, TTS first-chunk, and end-to-end response. Do not capture or upload
  live audio for this benchmark.
- Live WebUI + PhoneLink E2E remains pending explicit approval while
  #299's no-live-audio/demo/restart restrictions apply. Until then, use fixtures
  and mocked rooms and do not claim live acceptance.

## Product-ready checklist

- [ ] Both WebUI and PhoneLink use the same Engine-owned LiveKit flow.
- [ ] Transcript and response stay on the selected session/thread and normal
      history/admission/permission path.
- [ ] Dangerous-command blocklist and per-user provider/API-key/model settings
      are preserved; thinking stays low/off by default.
- [ ] No classifier imports, settings, workers, model assets, review/ack routes,
      or stale UI commands remain.
- [ ] Service lifecycle tests show no task or owned worker leaks after shutdown.
- [ ] Before/after p50/p95 latency results and test environment are recorded.
- [ ] Live E2E is run only after approval; otherwise its pending status is clear.
- [ ] User docs describe setup, settings, errors, and the supported voice flow.
