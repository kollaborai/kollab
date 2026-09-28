# Voice service implementation evidence

September 26, 2026. Current source is tracked against the
[device service](device-service-redesign.md), [classifier selection](classifier-providers.md)
and [spoken response](spoken-response-review.md) contracts. This ledger separates
source/tests, measured classifier behavior, actual audio, installed-package and
active-process evidence. Changes remain local and uncommitted; no release is claimed.

## Implemented surfaces

- `/voicemode` starts setup in the background. `/voicemode status` exposes state;
  the reserved TUI rows were removed by separate scrollback work and are currently
  an acceptance gap, with three status-layout tests failing.
  Automatic pinned/hash-verified Whisper base, Kokoro `af_heart` and Laya assets,
  isolated dependency installation, resumable downloads and runtime repair.
- One detached device service per OS user, protected by an OS lock. Expiring
  microphone leases, transfer fencing and cancelled-activation withdrawal.
  Startup alone never records. Models warm before a microphone claim succeeds;
  stopping voice releases capture while retaining ready models.
- Separate capture, VAD, transcription and sentence playback; bounded queues;
  fsynced timestamped JSONL before consumption, rotation, retention, explicit gaps
  and recoverable torn tails. No raw audio archive.
- Laya is the default; the original active-provider classifier remains selectable.
  One persistent Python worker shares one encoder across incoming intent and
  outgoing style heads. Incoming uncertainty, unsupported language and oversized
  utterances go to the main agent with full text and a note. Errors stay visible.
- Laya classifies playback echo with playback context; no text-match prefilter.
  Connected utterances preserve an initially ignored lead-in. Observer context
  uses original speech instead of injected voice instructions or agent HUD text.
- Incoming decisions include the previous 10 complete transcript lines by default,
  configurable with `/voicemode context N` (1–50). Ignored and admitted lines are
  context only, scoped to the current capture session. Laya, provider observation
  and main-agent fallback receive history; ignored earlier speech in the same
  batch is retained too. Conflicting local decisions with/without history defer
  to the main agent. Oversized windows defer intact; old workers must acknowledge
  history or report a compatibility error.
- Durable normal-host admission, original permission flow and attached routing.
  Tool-free observation/review requests cannot inject a hosted image tool.
  Failed image attempts do not invalidate a later completed image artifact.
- `display_text` stays on screen. Only `spoken_text` enters speech review and the
  device speech API. Example tool calls inside display fields remain data.
  Actual tool calls, results and private reasoning are excluded from narration.
  Streaming and saved-chat replay hide delivery tags while retaining raw history.
- Spoken output is one or two brief conversational sentences. Format limits reject
  code, links, lists, paths and long replies; Laya checks style. Low-confidence
  style decisions receive a tool-free provider check, without relaxing limits.
  Rejected automatic narration gets one rewrite and recheck. Missing narration
  is created on the responding host; display text is never directly read aloud.
  A confident Laya rejection of a format-valid rewrite gets one final provider
  style decision. The rewrite prompt asks for everyday wording; technical subject
  matter alone does not make a concise update unsuitable. Rejected replies are
  skipped individually and the next reply clears the old error at review start.
- The 30-second transcript deadline applies at admission only. Accepted progress
  and final replies may arrive later while current ownership and leases remain
  valid. Expired unadmitted inputs, cancelled work and old owners remain fenced.
- A lone `.` or explicit empty spoken field is silent before synthesis/receipts.
  Pre-tool updates, final narration and explicit `voice_out` share one ordered
  sentence queue. Idempotency, one rewrite budget and generation/lease fencing
  prevent duplicate or late playback. Failures are visible and do not halt capture.
- Attached speech events enter a bounded, ordered background worker. The socket
  reader returns immediately so review/rewrite RPC responses can arrive on the
  same connection. Off, cancellation and shutdown clear current/waiting speech;
  ownership and classifier changes invalidate stale queued replies.
- Snapshot restoration preserves structured content, tool/voice metadata and
  thinking. The streaming JSONL resume path is incomplete; the latest restart
  required an exact live snapshot to retain tool messages. Status data includes
  capture, review and playback state; rendering the reserved rows is unresolved.

## Source and package checks

- Latest broad run: 450 passed and 3 failed in 4.49 seconds. All three failures
  are `test_reserved_voice_status_with_empty_old_layout` at widths 40/80/120,
  reproduced before the speech fix after the independent row removal.
  Log: `/tmp/kollab-voice-recovery-tests.log`.
  Recovery regressions cover local/attached input deadlines, preserved owner
  checks, bounded provider adjudication, failures followed by successful speech,
  stale-error clearing, cancellation and provider switches during adjudication.
  Final focused check after the prompt edits: 73 passed in 1.01 seconds, with
  those three known layout cases excluded. Scoped Ruff and diff whitespace checks
  passed. The physical probe below verifies the final provider prompts.
- Previously, 441 focused and adjacent tests passed in 4.76 seconds. Coverage includes the
  voice package, plugin lifecycle/delivery, parsing/streaming/replay, host queue,
  message handling, tool registry/parity/integration, attach startup/permissions,
  status layouts/widgets, Responses provider/transformer, image artifacts,
  conversation restoration and bounded transcript context.
  Log: `/tmp/kollab-voice-rpc-tests.log`.
- Scoped Ruff and `git diff --check` pass. Tests cover explicit display/spoken
  separation, code/tool examples, silence, uncertain/failed style review, bounded
  rewrite, cancellation, provider switches and local/attached/explicit delivery.
  Retired imports, command/UI siblings, documentation links and asset hashes were checked.
- Context regressions cover ignored/admitted history, configured bounds, ordering,
  no future/other-owner data, no replay, capture reset, provider switching, local
  and attached transport acknowledgments, oversized deferral, contradictory model
  decisions and same-batch history through the normal admission loop.
- The attached-reader regression failed before the fix for both review and rewrite:
  awaiting speech held the only reader that could receive its RPC reply. Both
  pass with the ordered worker. Additional checks cover ordering, queue overflow,
  cancel/off/shutdown, subsequent replies after cancellation and stale generations.
- Built `/tmp/kollab-voice-context-wheel/kollabor_voice-0.1.0-py3-none-any.whl`
  and installed it without audio extras in `/tmp/kv-classifier-wheel-consumer-0926`.
  Client imports load no Torch, Laya, numpy, Whisper, Kokoro or sounddevice.
- Fifty installed clients concurrently launched one service (PID 68213) and one
  Laya worker (PID 68620) in isolated root `/tmp/kv-wheel-_8yu3mdk`. Verified cached
  assets loaded with an unreachable HTTP/HTTPS proxy and no microphone claim.
  Both test processes stopped afterward. Evidence:
  `/tmp/kollab-voice-classifier-wheel-proof-0926.json`.

## Live evidence

- Session `2609262000-vector-node` produced the reported style rejection at
  20:03:07, followed by input-deadline rejections at 20:03:37 and 20:06:13.
  The original spoken candidate is present in the raw API log; its exact first
  rewrite was not persisted. A fresh reproduction showed near-identical rewrites
  and conflicting style judgments. Final prompt/review policy accepted three of
  three repeated incident examples; this small development check is not a general
  classifier-quality guarantee. Evidence:
  `/tmp/kollab-voice-style-recovery-proof.json`.
- The final physical-audio probe used restarted `koordinator` 28160 through its
  actual speech RPCs, existing device service 80333 and warm Laya worker 80451.
  The incident was rewritten and approved, then two sentences played. A deliberate
  three-sentence rejection produced no receipts; the next valid reply cleared the
  error and played, including receipt `897dd1308b410b09d4fbc528f24e38c8`.
  All candidates carried input deadlines five minutes in the past. Display text
  never entered review or playback. No new user turn or ambient observer ran;
  conversation content was unchanged during this probe. The test released its
  microphone lease and retained the same warm service/worker. Evidence:
  `/tmp/kollab-voice-recovery-audio-proof.json`.
- Restart initially exposed CLI option ambiguity: combining `--permissions` and
  `--resume` submitted the session ID as user input. That turn was cancelled.
  Normal streaming-log resume loaded 40 of 95 live messages, omitting tool records.
  Restoring a separate full snapshot recovered all 95 messages with exact role,
  content, metadata and thinking equality. Active session after recovery:
  `2609262021-delta-stream`. Evidence:
  `/tmp/kollab-voice-recovery-reload-proof.json`; original backup:
  `/tmp/kollab-voice-recovery-runtime-backup.json`. These resume issues are not fixed
  by the speech change.
- The user's `2609261913-hydra-blade` session admitted the request about recent
  changes, but its attached UI reported `voice.rewrite_speech` timing out after
  two seconds. Source tracing and the failing regression locate the deadlock in
  the awaited voice-event handler. The live connection subsequently reported
  `Remote voice lease expired`, independently confirmed by a read-only RPC probe.
  The later `vector-node` attached UI played pre-tool speech before encountering
  the distinct style/deadline failure above, confirming that this earlier socket
  deadlock fix loaded. The most recent recovery was verified through a fresh device
  plugin and real speaker; the user's attached terminal still needs reopening.
- An isolated real socket probe then exercised the actual RPC client/server and
  host/device plugin handlers with the configured provider. Review, rewrite and
  re-review completed in order in 4.91 seconds total while 14 heartbeats succeeded.
  The rewrite job acknowledgment took 33 ms; this is not model generation latency.
  Only approved spoken text reached the output sink; the display sentinel did not.
  No microphone, physical playback or user conversation was involved. Evidence:
  `/tmp/kollab-voice-rpc-socket-proof.json`.
- Latest context probe used that installed client against device service 80333 and
  persistent Laya worker 80451. All 10 prior transcript IDs were acknowledged.
  With a TV/background notice in that window, `Hello.` was ignored and a clear
  request to Kollab deferred with only its new ID selected. The same worker served
  both requests. The brief microphone lease was released; no ambient observer,
  main-agent turn or playback ran in this transport probe. Evidence:
  `/tmp/kollab-voice-context-installed-proof.json`.
- A separate real configured-provider probe used synthetic TV context with tools
  disabled. Ambiguous `Hello.` returned exactly `.`; `Kollab, are you listening?`
  returned separate display/spoken fields. No chat or audio was changed. This
  checks the main-agent instruction path, not physical room-audio acceptance.
  Evidence: `/tmp/kollab-voice-context-provider-proof.json`.
- The following playback and conversation-reload checks predate the context
  extension; they are historical evidence for unchanged delivery surfaces.
- Real cold audio setup, physical speaker-to-MacBook-microphone capture, timestamped
  transcripts and ordered three-sentence playback were verified earlier. A real
  attached request displayed once, answered `voice test successful.`, and played;
  receipt `c2d794443227983bd181f54b6b1a3765` is in `~/.kollab/voice/speech.jsonl`.
- User transcript records 6 and 7 from stream
  `f2af9b7e-28b0-411d-9399-82e5687e82ba` exposed the missing lead-in. A regression
  and a real tool-free provider probe admitted both records together. The reply
  was normal speech with zero generated images.
- A pre-tool update physically played after restart: “I'll verify the live
  observer implementation and model-resolution path now, then I'll tell you
  exactly what is and isn't working.” Receipt `9d5c2325bc7ad2a36f61230818e8279d`.
- An uncertain synthetic echo admitted through the normal live main-agent queue
  produced exactly `.`, zero tool calls and zero speech receipts. Evidence:
  `/tmp/kollab-voice-silence-live-proof-0926.json`. This is a controlled example,
  not acceptance of all ambient-speech or talk-over cases.
- Final dual-output probe used the live main-agent queue and attached TUI, then
  controlled real-device playback without an ambient observer. The model displayed
  a Python example containing `DISPLAY_ONLY_SENTINEL_0926` and returned separate
  narration, “The code example is on your screen.” Only that narration played:
  receipt `8171cfd6c3f56b3c6de35a3ca826837d`.
- A three-sentence spoken candidate was rejected, rewritten once through the real
  configured provider and approved after Laya deferred its uncertain style result.
  Only the two rewritten sentences played, receipts
  `033e9e98fefaa973ca02f7276e311e0d` and `5f1f49ad975f0076a53d3a979d061811`.
  A subsequent `.` created zero receipts. Display code never appeared in reviewed
  speech or playback. Evidence: `/tmp/kollab-voice-final-policy-live-proof-0926.json`.
- All 43 messages were preserved across the final agent reload, with exact role,
  content, metadata and thinking comparisons. Resumed display output omitted
  protocol tags. Session `2609261727-pixel-node`; backup
  `/tmp/kollab-voice-final-replay-backup-0926.json`, recovery session
  `2609261727-voice-replay-e8590a`. Runtime proof:
  `/tmp/kollab-voice-final-replay-runtime-0926.json`.

## Classifier measurements and limitations

- Incoming acceptance without the new transcript-history window: 34 correct
  automatic decisions, 2 incorrect automatic decisions, 4 deferrals across 40
  examples. Eight inspected playback regressions
  produced 3 correct automatic decisions and 5 deferrals. These synthetic and
  development sets do not establish production accuracy or human talk-over quality.
- Real history probes exposed over-suppression: the small Laya head could classify
  a clear request as background when a prior TV notice was present. Requiring
  agreement with the current-utterance-only decision now defers that conflict to
  the main agent, preserving the full window. Seven development probes include
  TV greetings, direct requests and the user's actual mistranscribed notice.
  They do not establish general history-aware accuracy. Evidence:
  `/tmp/kollab-voice-context-model-proof.json`.
- In the recorded incident, Whisper produced “I'm marching to be in that background
  noise.” for the TV notice. This update cannot restore words absent from the
  transcript. Transcription quality remains separate from the context fix.
- Outgoing style: separate head on the same encoder. The 26-example synthetic
  policy set allowed 12 suitable replies and held 14 unsuitable ones; no deferrals
  occurred in that set. Encoding repair reused the split, so it is not blind
  acceptance. See `packages/kollabor-voice/bench/*results.json` and the bench README.
- A live valid two-sentence rewrite scored only 0.56 confidence, exposing a gap in
  that set. The explicit uncertainty-to-provider path was then verified with the
  real sentence and normal configured provider, without retraining weights or
  lowering the threshold. Provider review latency is additional to local inference.
- Local warm style inference mean was about 33 ms in the earlier style benchmark.
  History checks using two local intent passes measured roughly 68–82 ms warm in
  this development probe. The active worker's cold load/warmup took 42.03 seconds;
  audio warmup took 2.28 seconds. These are separate measured stages, not a promise
  of universal latency or a hosted-provider comparison.

## Acceptance boundaries

- V01–V04: cold installation/model loading, cached offline startup and warmup are
  verified; responsive command/status, interrupted setup, retry and cancellation
  are tested. Reserved status-row rendering is currently failing after separate
  scrollback changes. Every cold TUI/network interruption combination is not live-proven.
- V05–V07: fifty-client singleton launch, controlled ownership/expiry/transfer,
  cancelled claims, stale receipts and late results are covered. Fifty physical
  microphone claims and all multi-interface crash permutations were not exercised.
- V08–V10: real frames/transcripts/files/preview, ordered audio, 17-second sample
  preservation, queue bounds and storage recovery are covered. Physical permission
  denial, hardware unplug, prolonged overload and quiet-speech quality remain open.
- V11–V13: lead-ins, retained ignored speech, uncertain admission/silence, durable
  cursors/acknowledgments and normal queue/permission paths are covered. Ambient
  intent accuracy is partial, and exhaustive startup/cancellation races remain open.
- V14–V16: real pre-tool/final speech, dual-output playback and one rewrite verified;
  cancellation and producer scope tested. Real simultaneous multi-agent speech and
  physical stop latency remain unmeasured.
- V17: echo context, classifier decisions and main-agent silence are covered by
  controlled tests. No acoustic echo cancellation or general talk-over claim.
- V18: installed wheel/client/assets/offline singleton verified on macOS arm64 and
  Python 3.12. Linux is not live-accepted; Windows remains unsupported.

## Runtime and scope

The latest recovery check left device service 80333 and Laya worker 80451 warm
with the microphone off. `koordinator` 28160 has the new host code and the restored
conversation. The old attached UI exited during restart; reopen it with
`kollab --attach koordinator`, then `/voicemode on` to resume microphone ownership.
The separate TUI row work remains unresolved by this speech fix. Process IDs
describe these checks, not configuration.

This recovery change touches `plugins/voice_plugin.py`,
`packages/kollabor-voice/src/kollabor_voice/speech_review.py`,
`tests/unit/test_voice_speech_delivery.py`, `tests/unit/test_voice_plugin_lifecycle.py`,
`docs/features/voice-mode.md`, and the spoken-response, device-service and evidence
documents in this directory. It does not edit the TUI renderer or widgets.

The retired per-process assembly/gate, model picker, broken TTS adapters, AEC
placeholder and synthetic conformance implementation (64 files) remain recoverable
at `/var/folders/m2/9jqrk_yj05nc0tw10f9nv0t80000gn/T/kollab-retired-voice-26vqa87d`,
including an exact `retired-files.json`. Original baseline:
`/var/folders/m2/9jqrk_yj05nc0tw10f9nv0t80000gn/T/kollab-voice-debug-039maw8k/baseline`.
Legacy models/transcripts, unrelated agent-discovery edits and
`bundles/skills/contract-first-build/` were preserved. Nothing was committed or pushed.
