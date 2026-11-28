# Voice Mode — Cross-Spec Merge Agenda (aquamarine, merge-prep lane)

Compared: v1-lapis.md (14197B, model pipeline) · v3-aquamarine.md (10580B, audio) ·
v4-zircon.md (12021B, transcript + dispatch). Written 2026-09-25. One page, for owner.

## A. AGREEMENTS — bless and lock (no discussion needed)

| # | Decision | Status |
|---|----------|--------|
| A1 | sounddevice capture, 16kHz mono, 30ms frames, callback→queue | v3 only lane; unopposed → bless |
| A2 | silero-vad via ONNX (no torch), webrtcvad-wheels fallback | v3 specifies; v4 diagram concurs |
| A3 | VAD-segmented finalize + window cap (silence≥~700ms OR 5-10s, whichever first) | all three |
| A4 | Fixed-window chunking REJECTED (cuts words, transcribes dead air) | v3; unopposed |
| A5 | Gate = TEXT gate, after whisper, before big LLM | v3 recommended; v1+v4 flows both place it there |
| A6 | jev ships BUNDLED (unlike STT), persistent background process, warm/preloaded | v1 §3.3 + v4 §0 agree; v3 silent → adopt |
| A7 | Whisper never sees live audio; flushed segments only | v3 + v4 explicit |
| A8 | Transcript: JSONL append-only at ~/.kollab/projects/<proj>/voice/<session>.jsonl | all three independently chose same path |
| A9 | v4's 4-type schema (chunk/segment/dispatch/state, seq + idempotence, 500ms dispatch lock) | most complete; adopt as base |
| A10 | TTS = Kokoro 82M | v1 picked with sources; v4 deferred; unopposed |
| A11 | One-sentence response cap; enforced by voice layer truncation, not prompt | v1 + v4 (v4's enforcement is the stronger form) |
| A12 | Terminal-first v1 (TUI+CLI before web-ui) | all three |
| A13 | Mac TCC dead-stream watchdog + actionable error | v3 only; unopposed → bless |
| A14 | First-run HF download UX: progress line, offline-after-first, refuse-to-half-start | v1 only; unopposed → bless |
| A15 | Ambient prompt as trender include so every bundle inherits it | v4 mechanism; consistent with v1 prompts |
| A16 | VAD-empty segments never wake the gate at all | v1 finding, incorporated by v4 |
| A17 | jev artifact NOT YET FOUND in ~/dev/models (only MiniLM onnx cache there per v4; v1 search also failed) | factual consensus — lapis's JEV HUNT lane is the unblock |

## B. CONFLICTS — owner must pick (each side stated)

| # | Issue | v1-lapis | v3-aquamarine | v4-zircon |
|---|-------|----------|---------------|-----------|
| C1 | STT engine | parakeet TDT 0.6B v3 DEFAULT (6.32% WER, 103x RT on M4 Pro; faster-whisper small = fallback, turbo = opt-in) — post-canonical update | (out of lane) | faster-whisper per canonical letter |
| C2 | Who replies on CONVERSE | full LLM, one-shot lightweight session | one handler (implied LLM) | jev composes reply LOCALLY, no big LLM |
| C3 | Verdict set | act \| converse \| silence | act \| converse \| silence | + DISCARD (4th, noise/VAD-leak) |
| C4 | Interrupt model | BARGE-IN: user speech during TTS cancels playback | QUEUE always; interrupt human-only, phase 2 | QUEUE; interrupt only via explicit phrase ("stop","wait") matched by gate |
| C5 | Mic during TTS playback | barge-in requires hearing user DURING playback | duck VAD→silence, keep frames in ring, 300ms tail | hard duck (capture suppressed), engine-level |
| C6 | ACT dispatch target | koordinator queue / work agent | task/chain via existing orchestration | in-session active agent (pre_user_input), NO hub routing |
| C7 | Retention default | rolling 30-day cap | keep forever (retention_days=0) | retain like conversations, cleanup later |

CRITICAL INTERLOCK — C4×C5: lapis's barge-in and zircon's/v3's mic-ducking are
mutually exclusive in their stated forms. If the mic is ducked during TTS
playback, no segment can finalize during playback, so barge-in cannot trigger.
Merge must pick: [a] hard duck v1 (simple, no self-hearing, no interruption
until playback ends — zircon/v3 default) or [b] half-duck (capture continues,
VAD still runs, echo rejected by energy/keyword gate — enables lapis barge-in,
harder). Recommendation (mine): [a] for v1, barge-in as phase 2.

## C. GAPS — nobody covered

| # | Gap | Note |
|---|-----|------|
| G1 | Barge-in vs duck resolution | see interlock above — decision + design needed |
| G2 | Audio ack on ACT verdict ("on it") | v1 voted yes (§5); v3/v4 silent. Cheap, makes it feel alive — needs a call |
| G3 | v4 internal tension: schema has "chunk = raw whisper interim output" but pipeline rule A7 says whisper only sees flushed segments — interim chunks can't exist as specified | reconcile: chunk = UI-side VAD/partial text only, or drop chunk type |
| G4 | Gate confidence fallback policy | v3: verdict-confidence logged, <0.5 → escalate to LLM; v1: timeout → converse. Compose both? unstated elsewhere |
| G5 | Multilingual UX | v1 notes parakeet=EN-25lang, whisper=100+; others silent. v1 is non-EN answer by fallback — fold into C1 decision |
| G6 | Input device selection (--input-device) for multi-mic setups | v3 §9 only |
| G7 | jev fallback when artifact absent | v1: generic 0.5B-1B GGUF int4 default; v4 flags to bismuth. Align on v1's fallback |
| G8 | Config namespace unification | v1 voice.stt_engine / v3 VoiceModeConfig dataclass — compose into one surface |
| G9 | Streaming/interim transcript UX in TUI | v4 chunk type implies partial display; no spec for TUI rendering of live transcript |

## D. Suggested merge order

1. Resolve C1 (STT: parakeet vs faster-whisper default) — biggest user-facing lever.
2. Resolve interlock C4×C5 (duck vs barge-in) — changes state machine shape.
3. Resolve C2 (converse responder: jev-local vs LLM one-shot) — cost vs quality.
4. C3 (discard verdict) and C6 (act target) — small, pick and move on.
5. C7 retention, G2 ack, G4 fallback — quick calls.
6. Assembly: v4 state machine + schema as skeleton; graft v3 audio layer (§2-4, §5
   duck hook), v1 model/download/TTS sections, v1+v4 prompts.

---

# DELTA — v2-sapphire + v5-bismuth folded in (aquamarine, 22:45)

Landed after the 22:33 cut. Delta-format: new conflicts, updated rows, refined
blessings. The 17 blessed items stand; one of them is now CHALLENGED (A12).

## Δ Agreements — two more blessings

| # | Decision | Status |
|---|----------|--------|
| A18 | packages/kollabor-voice surface-agnostic core (no kollabor-tui imports); frontends = thin adapters | sapphire §2 + zircon §3.1 independently converge → bless |
| A19 | One-sentence rule = DUAL-LAYER: prompt asks, first-sentence post-processor enforces before TTS | sapphire §6 refines A11; v1+v4 consistent → bless (replaces A11's weaker form) |
| A20 | kollab[voice] optional extra, lazy imports, friendly error if absent, ~120MB wheels all-arm64 (verified live by bismuth) | v5 packaging canon; unopposed → bless |

## Δ Conflicts — updated + new

| # | Issue | Positions now on the table |
|---|-------|---------------------------|
| C1 | STT engine | [a] lapis: parakeet v3 default (faster+more accurate) [b] canonical+zircon: faster-whisper [c] bismuth: faster-whisper, REJECTS parakeet — cc-by-4.0 license, no ONNX in repo, nemo-toolkit heavy. NOTE: sapphire's Transcriber interface LOWERS the stakes — engine becomes a config swap. owner arbitrates with license evidence now on the table |
| C2 | Converse responder | [a] jev-local (zircon; bismuth: "gate model drafts the reply, LLM only if low confidence") [b] LLM one-shot (lapis; sapphire — since jev artifact missing). 2-2 split |
| C4 | Interrupt | NEW third position — bismuth: queue normally, barge-in ONLY during TTS. lapis: full barge-in. v3/sapphire: queue + phase-2 barge-in. zircon: queue + explicit phrase. All barge-in variants still hit the C4×C5 interlock — capture must run during playback. Hard-duck v1 recommendation unchanged |
| C7 | Retention | THREE positions now: bismuth 14d (conversations-dir convention) · lapis+sapphire 30d · v3 forever. needs owner |
| C8 | NEW — whisper size default | bismuth: BASE (145MB first download, 3.3x smaller, CPU-friendly; offers accommodation: base default on CPU/slow-link heuristic) vs lapis: SMALL (3.4% WER, quality-first). gate-is-a-classifier argument (transcript noise tolerable) is bismuth's strongest card |
| C9 | NEW — first/demo surface | sapphire: WEB UI (dsk/Mentiko) demo first, TUI adapter same PR · v1/v3/v4/v5: terminal-first. CHALLENGES blessed A12. note: sapphire's core-package re-arch makes both cheap — the fight is over demo optics, not architecture |

## Δ Gaps — updated

| # | Gap | Update |
|---|-----|--------|
| G2 | ACT audio ack | sapphire votes yes with 2-WORD cap ("on it" tier). cheap — recommend adopt |
| G7 | jev absent fallback | CONVERGING: bismuth Qwen2.5-0.5B-Instruct ONNX (apache-2.0, ungated, q4 ~350-400MB) interim default; lapis generic 0.5-1B GGUF. ONNX wins the tiebreak: bismuth proved onnxruntime is already a faster-whisper hard dep = zero added install weight. bless candidate |
| G8 | Config namespace | now FOUR surfaces (voice.stt_engine / VoiceModeConfig / voice.* tree / kollabor.voice.*). sapphire votes unification = merge STEP 1. agree — propose kollabor.voice.* (bismuth's) as the winner: it matches existing config conventions |
| G10 | NEW — jev artifact constraint | bismuth: must be ONNX + redistributable license to bundle. GGUF/llamafile runtime rejected (sdist wheel pain). hardens the jev hunt criteria |

## Δ Merge order update (replaces §D)

1. C9 (demo surface) — decides which adapter ships in the same PR
2. C1+C8 together (engine + size — one config family, Transcriber interface)
3. C4×C5 interlock (duck vs barge-in) — state machine shape
4. C2 (converse responder — 2-2 split, owner breaks it)
5. C7, G2 (quick calls)
6. G8 config unification into kollabor.voice.* (sapphire's step-1 vote honored here)
7. Assembly: zircon state machine + schema skeleton; sapphire core-package layout (A18); v3 audio layer; v1 model/download sections; v5 packaging extra (A20); v1+v4 prompts + sapphire dual-layer enforcement (A19)

---

# DELTA 2 — peridot v2-review findings + jev ruling (aquamarine, 22:52)

## Δ Errata (carry into assembly; corrected cites)
- v2-sapphire context injection cite: message_handler.py:74 (NOT :444)
- v2-sapphire startup gate cite: message_handler.py:100-117 (NOT :81)

## Δ New gap / pre-req fix
- G11: startup gate DROPS input on 30s boot timeout (message_handler.py:113-116)
  → voice segments would VANISH, not queue. peridot speccing re-enqueue fix.
  TAG AS PRE-REQ: must land before voice dispatch wiring. Assembly note: gate
  behavior is load-bearing for C6/act-path, not just UX.

## Δ TTS quick call (under A10)
- kokoro-onnx 0.6.1 = bismuth's VERIFIED pick (evidence in vault).
- v2's "local-piper" default has NO verification behind it — demote to
  escape-hatch only. say-fallback remains bismuth's Darwin zero-dep floor.

## Δ Build-not-wire caveat (C9 / assembly)
- web-ui server.py is a 92-line thin FastAPI today. v2's "web session input
  path" seam is PARTIALLY FUTURE WORK — mark as build-not-wire in assembly;
  do not present as existing plumbing.

## Δ G7/G10 update — jev RULED OUT by owner (22:49): "don't want to wait
for jev. would rather use a new local model."
- jev hunt = CLOSED, artifact search moot, G10 ONNX+redistributable
  constraint transfers to the replacement model.
- G7 default IS the answer now: Qwen2.5-0.5B-Instruct ONNX (apache-2.0,
  ungated, q4 ~350-400MB, onnxruntime already a faster-whisper dep). Any
  newer local decision-model candidate must meet: ONNX, permissive license,
  ≤2s verdict budget on M-series CPU.
- Bundling decision unchanged: gate model may still bundle OR first-run
  download (bismuth's flow handles either; owner's "new local model"
  phrasing doesn't override bundling-vs-download mechanics).

---

# SEAL — owner rulings applied (aquamarine, 22:58). Agenda is now CLOSED.

- JEV: DROPPED ENTIRELY. No bundle-exception exists anymore. A17 and every
  jev reference above are HISTORICAL — do not carry into the merged doc.
- C2 RESOLVED → LLM one-shot (bismuth/sapphire position). jev-local
  converse replies died with jev. Not open for arbitration anymore.
- C7 remains open (14d / 30d / forever — three-way, owner to pick).
- G7 RESOLVED → local gate model, primary candidate Qwen2.5-0.5B-Instruct
  ONNX, PENDING bismuth's packaging confirm. Criteria locked: ONNX,
  permissive license, ≤2s verdict on M-series CPU.
- ASSEMBLY ERRATA (add to the v2-cite errata above):
    [e3] rename "jev gatekeeper" → "local gate model" throughout the merged
         doc (v2-sapphire gate.py naming, v1/v4 gate sections).
    [e4] sapphire's W2 (bundled-jev unverified) is MOOT — no jev to bundle.
- Peridot findings already folded (Δ2): cite errata e1/e2, G11 pre-req
  (startup-drop re-enqueue must land before dispatch wiring), TTS sub-call
  (kokoro-onnx verified; piper escape-hatch only).

OPEN FOR OWNER AT SEAL TIME: C1 (STT engine), C3 (discard verdict),
C4×C5 (duck vs barge-in), C6 (act target), C7 (retention), C8 (whisper
size), C9 (demo surface). Everything else is decided. Merge order stands:
C9 → C1+C8 → C4×C5 → C6 → C7 → assembly per §D-Δ.
