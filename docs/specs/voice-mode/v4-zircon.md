# Voice Mode — zircon v4 spec (transcript + agent dispatch lane)

author: zircon | revision: v4.2 (duplex receipts per contract v2.2/v2.3 — playback queue, played/disrupted acks, agent re-decide on disruption) | status: draft for merge | scope: my lane only. Peers' lanes (audio stack, VAD model choice, TTS) are referenced as interfaces, not specified here.

## 0. canonical requirements (locked by owner — not re-litigated)

- `/voicemode` toggles voice mode on/off.
- faster-whisper model from Hugging Face on first run (never bundled).
- always-on local recorder while voice mode is on.
- continuous JSONL transcript.
- segment finalize on silence OR 5-10s window (whichever first).
- dispatch is AGENT-GATED by "jev": a small local decision model (from ~/dev/models, shipped bundled with kollab, running continuously in the background) decides activate | converse | silence. Ambient never reaches the big LLM. jev is the always-on layer; the big LLM only wakes on real work.
- strict always-on ambient prompt: "you are always on", "not everything is about the project", minimal acks ("uh huh", "okay", pause words) continue the conversation, never trigger work.
- agents are ONE collective from the user's POV: the speaking agent speaks for all.

## 1. pipeline overview (duplex loop)

```
            ┌──────────── playback QUEUE (mic ducked while non-empty) ──────────┐
            │   sentence → TTS → speaking → played(receipt) ──► next sentence   │
            │                        │                                          │
            │        user interrupt ─┴─► disrupted(receipt) → AGENT RE-DECIDE  │
            │                              (silence-and-listen default)         │
mic ──► silero-vad ──► segment buffer      │                                    │
                         │  finalize: silence≥threshold OR 10s window          │
                         ▼                 ▼                                    │
                    faster-whisper (flushed segment only)                       │
                         ▼                                                     │
                    JSONL transcript append (chunk never dispatched)            │
                         ▼                                                     │
                JEV GATE (small local decision model, bundled, always-on)      │
                 │ activate │ converse │ silence │ discard                     │
                 ▼          ▼          ▼                                        │
             big LLM     jev local    ~2s silence, keep listening               │
             turn        reply (first-sentence cap default, liftable)          │
                 ▼                    ▼                                         │
                 enqueue ◄─────────────┘                                        │
                 ▼                                                              │
            SentenceQueue ──► speaker ────── receipts: played | disrupted ──────┘
                                     ducking lifts when queue drains; then resume capture
```

## 2. transcript schema (JSONL, append-only)

location: `~/.kollab/projects/<encoded-project>/voice/<session_id>.jsonl` — same tree as `conversations/session_*.jsonl` so resume/inspect/cleanup follow existing precedent.

one JSON object per line. `seq` is a monotonic per-session counter; every entry carries `ts` (ISO 8601).

```jsonc
// type: chunk — raw VAD/whisper interim output. UI-only. NEVER dispatched.
{ "ts": "…", "seq": 41, "type": "chunk", "start_ms": 180220, "end_ms": 181000, "text": "and so the", "confidence": 0.62 }

// type: segment — finalized utterance, whisper-transcribed. The ONLY dispatchable unit.
{ "ts": "…", "seq": 42, "type": "segment", "start_ms": 181000, "end_ms": 184500, "text": "and so the agents speak as one", "confidence": 0.94,
  "dispatched": false }

// type: dispatch — one per segment actually dispatched to the gate/LLM. Links segment → outcome.
{ "ts": "…", "seq": 43, "seq_ref": 42, "type": "dispatch",
  "gate_decision": "activate",        // activate | converse | silence | discard
  "run_id": "run-1778724644028",      // set when activate → LLM turn
  "reply_seq": 57 }                   // reply segment (TTS output) once spoken

// type: state — voice mode state transitions, for debugging/audit.
{ "ts": "…", "seq": 44, "type": "state", "event": "voicemode_on|voicemode_off|gate_model_loaded|…" }

// v4.2 — type: response + type: receipt (playback ground truth)
{ "ts": "…", "seq": 57, "type": "response", "response_id": "r-9", "sentence_index": 0, "text": "the build passed", "first_sentence_cap": true }
{ "ts": "…", "seq": 58, "type": "receipt", "response_id": "r-9", "sentence_index": 0, "outcome": "played", "ts_played": "…" }
{ "ts": "…", "seq": 59, "type": "receipt", "response_id": "r-9", "sentence_index": 1, "outcome": "disrupted" }  // user interrupt: sentence 1 NEVER counts as heard
```

rules:
- chunks are never dispatched; segments are the only dispatchable unit.
- a segment with `dispatched: true` will not be re-dispatched — idempotence is by construction (see §4).
- `dispatch` records make the transcript the single source of truth for "what did the gate do and why" — audit without a separate log.
- no in-place edits. Corrections are new entries referencing prior seq.
- rolling display reads the tail; the agent layer reads only final segments.

## 3. triage state machine (jev gate + duplex response leg)

states: `LISTENING → SEGMENTING → TRANSCRIBING → TRIAGE(jev) → {LLM_TURN, LOCAL_REPLY, SILENT_HOLD} → RESPONDING(cap-limited sentences) → ENQUEUE → PLAYBACK(speaking/played per sentence, mic ducked while queue non-empty) → LISTENING` — plus the disruption edge `PLAYBACK → DISRUPTED → AGENT_REDECIDE → {SILENCE, HOLD, REQUEUE_REST}`. Playback is a tracked exchange: every sentence ends in a `played` or `disrupted` receipt; the dispatch loop consumes receipts as ground truth for what the user actually heard.

- LISTENING: VAD frames classify live audio (speech/silence). Empty segments (VAD says no speech) never wake anything — lapis's finding, incorporated.
- SEGMENTING: buffer grows while speech continues. Finalize when VAD silence ≥ threshold (owner: 5-10s window upper bound is wall-clock, whichever first).
- TRANSCRIBING: flushed buffer only goes to faster-whisper. Whisper never sees live audio.
- TRIAGE — **jev gate**: jev is a small LOCAL decision model (source: ~/dev/models; ships bundled with kollab, no runtime download) that runs continuously in the background as the always-on layer. On each finalized segment it classifies into exactly FOUR decisions:

| decision | meaning | action |
|---|---|---|
| `activate` | project work implied | big LLM turn with voice header |
| `converse` | ambient conversation | jev composes a local reply, no big LLM |
| `silence` | no response needed | emit ~2s silence, keep listening |
| `discard` | noise / VAD leakage | no output, no transcript reply |

  decision set is deliberately closed. The interface is `jev.decide(segment_text, recent_context) -> Decision`; model weights are swappable behind that signature (packaging = bismuth's lane; no *jev* file exists in ~/dev/models today — only an all-MiniLM-L6-v2 onnx cache — so export/copy of the model artifact is an open item for that lane).

- LLM_TURN: segment text injected as a conversational turn through `pre_user_input` → LLM → `post_api_response` — reuses every existing hook (context plugins, permissions, compaction) with zero new dispatch semantics. Payload carries a voice header: session id + seq_ref so the agent can correlate with the transcript.

- RESPONDING (response leg, both LLM_TURN and LOCAL_REPLY converge here): the response is constrained to ONE constructed sentence by default (first-sentence cap; user can lift the cap). The responder (big LLM or jev) emits sentences; anything beyond the cap is truncated at sentence boundary by the voice layer — the constraint belongs to the voice pipeline, not the agent's prompt, so text-mode behavior is unaffected.
- PLAYBACK (v4.2 — queue semantics per owner's dictated design + contract v2.2): playback is NOT fire-and-forget. The response leg enqueues into a TTS **SentenceQueue** and plays continuously on the user's machine. Mic remains ducked while the queue is non-empty (ducking lifts only when the queue drains). Each played sentence produces a **receipt**:
  - `played` — the sentence finished; the engine acks with `ts_played`. **Agent ground truth = "user heard sentences 1..N (played only)."** Receipts inform the next dispatch decision (e.g. if only sentence 1 of 3 played and the user then spoke, the gate context should note the truncation).
  - `disrupted` — user interrupt ENDS the current spoken message. The interrupted sentence NEVER counts as heard. Receipts stop at the last fully-played sentence index.
- DISRUPTION branch (v4.2): on `disrupted`, control returns to the AGENT's decision, not automatic replay: the agent re-decides **silence vs continue-holding** (standby — say nothing further and wait for the user) vs re-queue-remainder (rare; only when the remaining sentences were urgent, e.g. a warning). Default = silence-and-listen, because the user interrupted for a reason.
- SILENT_HOLD: ~2s of silence emitted, mic never ducks, straight back to LISTENING. (Distinct from disruption-silence: SILENT_HOLD is gate-initiated, DISRUPTION-silence is user-initiated.)

### 3.0a playback wire events (v2.2/v2.3 alignment)

the engine emits per-sentence events: `speaking` (sentence dequeued → TTS start), `played` (sentence complete, ack time), `disrupted` (user interrupt during `speaking`; queue for the current message is dropped, ducking lifts). sapphire's contract-v2.2/2.3 owns the exact envelope; this spec consumes them. receipts land in the transcript as `type: receipt` entries (see §2 v4.2 addendum below).

### 3.1 interface parity (TUI, CLI, web UI, socket)

the duplex loop above is ONE state machine that must behave identically across all four frontends. parity rules:

- the loop lives in a shared voice engine module (packages/, not plugins/): frontends own only capture/render — TUI and CLI use the native recorder; web-ui uses getUserMedia + bridge; socket connections attach an audio stream to the same engine.
- every frontend must implement the same three hooks: `on_transcript(update)`, `on_response_sentence(text, speak)`, `on_gate_decision(decision)` (for optional UI badges). Any frontend without mic access degrades to text I/O with the same transcript + gate semantics.
- mic ducking is an ENGINE-level rule, not per-frontend — implemented once in the capture gate, so no frontend can accidentally hear its own TTS.

### 3.2 silence-response tool mapping

the gate's `silence` decision maps onto kollab's tool system as a **no-op response tool**, not a chat turn:

- register a tool definition in `ToolRegistry` (packages/kollabor-agent/src/kollabor_agent/tool_registry.py:16, register() at :75) — e.g. `voice_silence(duration_ms)` — callable by the voice layer only.
- calling it produces: ~2s of audio silence + listening continuation, NO transcript chat entry, NO LLM round-trip. The transcript records a `dispatch` entry with `gate_decision: silence` — the turn exists in audit, not in conversation.
- rationale: tool-call shape keeps silence inside the existing permissions/contract system (tool_call_contract.py) rather than inventing a third response channel.

### 3.3 collective identity prompt layer

- the dispatched LLM payload embeds the strict always-on framing: "you are always on; not everything is about the project; this may just be conversation; you speak for the whole collective."
- minimal acks ("uh huh", "okay") are valid complete replies — the agent never fabricates work from ambient.
- implemented as a voice-mode trender include (`<trender type="include" … />`) appended to the active bundle's system prompt during voice mode, so every bundle inherits it without edits. See bundles/agents/<name>/system_prompt.md structure.

## 4. dispatch-lock & idempotence

- both finalize triggers (silence, window) funnel through ONE finalizer function → there are never two independent fires to race.
- dispatch reads only segments with `dispatched: false`; on send it stamps `dispatched: true` + writes the `dispatch` record in the same append batch.
- a ~500ms dispatch lock suppresses coincident re-triggers (e.g. user resumes speaking mid-flush).
- VAD-empty segments (no speech energy) never wake the gate at all.
- v4.2: a segment arriving during PLAYBACK joins the queue as normal (mic is ducked, so mid-playback capture is already suppressed; the segment lands after ducking lifts). The interrupt phrase path (§3 queue-vs-interrupt) is the ONLY user-initiated break of an in-flight message, and it lands as `disrupted` receipts + the agent re-decide branch — never as a dropped queue entry.
- v4.2: receipts are write-once. A `disrupted` receipt for sentence N implies sentences N+1.. of that response are dead (dropped, unspoken) — the engine does not emit receipts for never-played sentences; the transcript consumer infers the tail from the queue length + last receipt index.

## 5. explicit answers (required by koordinator)

| question | answer | rationale |
|---|---|---|
| dispatch target | **option A: in-session, active agent** | voice = hands-free typing into the current session; reuses pre_user_input pipeline; no hub routing needed since agents are one collective. B (hub_queue) and C (spawn-by-keyword) deferred. |
| queue-vs-interrupt when LLM mid-response | **QUEUE next segment; interrupt only via explicit user phrase** ("stop", "wait") matched by the local gate | interrupting a stream is high-risk; explicit phrase is a cheap, recoverable escape hatch. |
| playback model (v4.2) | **say-anything → enqueue → TTS → plays continuously; first-sentence cap by default (liftable); user interrupt ends the message** | queue matches owner's dictated design; cap keeps unsolicited speech short until trust is earned. |
| receipts (v4.2) | **per-sentence played/disrupted acks; agent ground truth = played sentences only** | playback is a tracked exchange, not fire-and-forget; disrupted sentences never count as heard. |
| on disruption (v4.2) | **AGENT re-decides: silence-and-listen (default) vs continue-holding vs re-queue-remainder (rare, urgent only)** | the user interrupted for a reason; defaulting to silence avoids talking over them. |
| terminal vs web-ui | **parity by design (§3.1), phased rollout: TUI+CLI first, web-ui + socket next** | one shared voice-engine module; frontends implement only capture/render hooks. terminal ships first (native recorder precedent), web-ui via getUserMedia + bridge follows — same loop, no redesign. |
| retention | **transcripts default-retained like conversations; cleanup tool later** | same tree as conversations/session_*.jsonl; no new policy machinery day one. |
| triage decision set | `activate \| converse \| silence \| discard` (closed set, §3) | covers act / reply-locally / hold / drop; closed so the gate model stays a classifier, not a free-form generator. |

## 6. out of scope (my lane boundaries)

- audio device capture, VAD model choice/quantization (aquamarine).
- whisper engine packaging/wheel resolution + jev model artifact export/copy from ~/dev/models (bismuth — no jev-named file exists there today, flag to him).
- TTS engine choice; the response leg spec covers the pipeline contract (one sentence → TTS → ducked playback → resume), not synthesis internals.
- the jev gate model itself — spec defines the interface `decide() -> Decision`, not its weights.
