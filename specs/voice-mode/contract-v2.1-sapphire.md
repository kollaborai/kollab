# Voice Engine Contract — v2.1 addendum to v2-sapphire (sapphire)

malmazan directive: the voice engine powers MULTIPLE PLATFORMS (CLI, native app, web,
anything) and its logic must be solid, efficient, and DUPLICATABLE in any programming
language. This document is the normative contract. The Python package
(packages/kollabor-voice/) is the REFERENCE IMPLEMENTATION. Any team (rust, swift, go)
can reimplement from this file alone and be provably compatible via the shared
conformance fixtures.

## 1. Scope rule

NORMATIVE: everything in §§2-7 — state machines, event schemas, config schema, wire
format, conformance corpus, budgets. Reimplementations MUST match these exactly.
INFORMATIVE: v2-sapphire §2 file layout, python idioms, asyncio internals. Free to vary.

## 2. Engine state machine (normative)

States (on-wire strings): "off" | "downloading" | "loading" | "listening" |
"transcribing" | "gate" | "escalated" | "error" | "degraded"

Transitions:
  off          -> downloading   on enable, when STT weights absent
  off          -> loading       on enable, when weights present
  downloading  -> loading       on download complete | -> error on failure (stays off)
  loading      -> listening     on models ready | -> error on failure
  listening    -> transcribing  on segment finalize (VAD silence OR window cap)
  transcribing -> gate          on transcript text produced
  gate         -> listening     on verdict silence (no bus traffic, zero fan-out)
  gate         -> escalated     on verdict converse/act; escalated -> listening
                 immediately after the input event is emitted
  any         <-> degraded      on gate-miss/OOM; degraded continues with rule
                 fallback; entering error requires operator action
  listening    -> off           on disable (drain, flush partial, close stream)

Verdict enum (wire): "silence" | "converse" | "act". NOTE: malmazan's latest direction
(2026-09-25 stream) treats converse+act as one combined flow — "first converse, then act
immediately, or silence." Pending merge confirmation the enum stays 3-valued but the
escalation path is unified: every escalated segment ALWAYS returns one sentence to the
user; "act" additionally spawns work. This contract fixes the WIRE format at 3 values so
reimplementations don't churn; semantic combination lives above the wire.

## 3. Event schemas (normative, JSON, UTF-8)

All events share an envelope:
  {"v":1, "seq":<int, monotonic per engine run>, "ts":<float, unix epoch seconds>,
   "type":"<one of below>"}

  type "segment"     {id:str uuid4, text:str, dur_ms:int, lang:str|null,
                      final:bool}
  type "verdict"     {segment_id:str, verdict:"silence"|"converse"|"act",
                      confidence:float 0..1, fallback:bool, latency_ms:int}
  type "input"       {segment_id:str, source:"voice", text:str,
                      escalated:bool}   // emitted ONLY for non-silence
  type "state"       {state:<state above>, reason:str|null}
  type "error"       {code:str, message:str, recoverable:bool}
  type "ack"         {segment_id:str}    // optional audio-ack pairing (G2)

Wire discipline: field order irrelevant; unknown fields MUST be ignored (forward
compat); seq gaps indicate a dropped event, never resequenced; ts is advisory only,
ordering authority is seq.

Transcript JSONL (normative location per merge agenda A8:
~/.kollab/projects/<encoded-project>/voice/<session_id>.jsonl): one "segment" event per
line, same envelope, append-only, never rewritten.

## 4. Config schema (normative)

Single namespaced tree (unifies G8 divergence across v1/v2/v3/v4):

  voice.model            str  default "base"          # STT engine+size token
  voice.model_path       str|null                     # local weights, skip download
  voice.stt.fallback     str|null                     # e.g. faster-whisper-small
  voice.vad.silence_ms   int  default 700             # finalize threshold
  voice.vad.max_ms       int  default 8000            # window cap (5000..10000)
  voice.vad.min_ms       int  default 300             # discard shorter
  voice.gate.verdicts    ["silence","converse","act"] # fixed v1
  voice.gate.budget_ms   int  default 2000
  voice.gate.model       str|null  default null       # null => rule fallback
  voice.transcript.dir   str  default <A8 path>
  voice.transcript.retention_days int default 30      # 0 = keep forever
  voice.reply.sentence_cap int default 1              # one-sentence invariant
  voice.tts.engine       str|null default null
  voice.tts.budget_ms    int  default 1000
  voice.ambient_prompt   str  path to user-editable preamble

Reimplementations must accept this tree verbatim (JSON) and apply identical defaults.

## 5. Engine-as-service mode (normative)

A conforming engine MUST run standalone behind language-neutral IPC:

  TRANSPORT  JSON lines over (a) stdio — one JSON event per line, requests in the
             same format: {"type":"enable"} {"type":"disable"} {"type":"config",
             "config":{...}} {"type":"shutdown"}; (b) websocket, same frames, plus
             {"type":"subscribe"} handshake. Non-python hosts embed WITHOUT porting.
  VERSIONING handshake: on start the engine emits
             {"v":1,"type":"state","state":"off","proto":1}. Hosts reject proto!=1.
  EXIT       {"type":"shutdown"} -> full teardown (drain, flush partial {final:false},
             close audio), then process exit 0. Watchdog rule from v2 §9 applies.

## 6. Conformance test suite (normative corpus)

fixtures/ directory ships with the reference implementation:
  - N>=12 named WAV fixtures (16kHz mono): clean speech, two-speaker overlap, ambient
    noise, silence-only, trailing-partial (no final silence), window-cap overflow,
    multilingual sample, ultrashort (<min_ms), mic-drop mid-segment, clipped-hot input,
    whisper-of-speech (VAD-leak), and each gate-verdict case.
  - For every fixture, an EXPECTED event-sequence file: ordered list of envelopes
    (segments/verdicts/states), with text fields matched by exact string for
    deterministic fixtures and by regex/nonempty predicate for STT-variable ones.
  - Conformance = replay fixtures through the engine (fake clock, real VAD+STT) and
    diff emitted envelopes against expected, ignoring ts, engine-instance ids, and
    seq absolute values (gaps still checked).
  - SAME fixtures for every language. A rust/swift/go port passes the identical corpus;
    its CI entry proves byte-compat semantics. The corpus is append-only; a fixture may
    never be edited after landing (only deprecated).

## 7. Performance budgets (normative, testable in conformance)

  capture overhead          callback-to-queue p95 <= 5 ms per 30ms frame
  segment finalize latency  VAD trigger -> transcribing state <= 50 ms
  STT throughput            >= 10x realtime on M-class silicon (fixture-timed)
  gate verdict latency      <= 2000 ms p95, hard budget; over-budget => fallback
                            verdict path, event carries fallback:true
  escalate-to-input latency verdict -> input event emitted <= 20 ms
  sentence-cap enforcement  reply pipeline adds <= 30 ms (string ops only)
  TTS first-audio           <= 1000 ms from reply text accepted
  silence verdict cost      ZERO events beyond the verdict envelope itself —
                            no bus fan-out, no transcript write beyond segment line
  memory ceiling            resident <= 2.5 GB with "base" STT + gate loaded

Budgets are asserted in conformance runs on reference hardware (M4-class, mains power);
ports re-assert with the same numbers or file explicit waivers per platform.

## 8. Placement

This addendum binds the merged spec: where v2-sapphire or any merged section conflicts
with §§2-7, THIS document wins. Assembly note: graft the reference implementation
mapping (v2 §2 file tree) as an appendix, not as contract text.

— sapphire, 2026-09-25, repo @9725d35
