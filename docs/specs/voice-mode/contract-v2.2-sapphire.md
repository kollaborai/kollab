# Voice Engine Contract — v2.2 amendment (sapphire)

Amends contract-v2.1. v2.1 §§1-8 remain in force except where amended below (marked [AMENDS §x]). New sections §9-14 are normative. owner's dictated speech-output subsystem (2026-09-25 stream): sentence-atomic playback queue, first-sentence cap, output-layer interrupt, receipts, disruption. C4 interlock RESOLVED: duck on input, interrupt on output — no conflict.

## 9. Output state machine (normative, parallel to input §2)

States: "idle" | "inferring" | "speaking" | "done" | "interrupted"
  idle       -> inferring   on reply accepted (first or next sentence)
  inferring  -> speaking    on first audio frame
  speaking   -> done        on sentence audio fully played
  speaking   -> interrupted on user interrupt (B11) — message ENDS here
  done       -> idle        | interrupted -> idle   (queue advances)
Per-reply, per-sentence. Sentence is the ATOMIC UNIT everywhere: of capping, of playback, of receipts, of interruption. A reply's sentence N+1 infers while N plays.

## 10. Playback queue (normative)

Agent speech -> queue -> TTS inference (VibeVoice-class) -> plays on the user's machine continuously, sentence-by-sentence. FIFO across replies unless a reply is DISRUPTED (§12), which clears only that reply's remaining sentences — later queued replies still play. Queue is engine-owned; agents may only enqueue, never play directly.

## 11. Sentence cap (normative) [AMENDS §4 config]

  voice.reply.sentence_cap   int default 1        # FIRST SENTENCE ONLY by default
  voice.reply.extendable     bool default true    # user may raise at runtime
Playing beyond the cap requires explicit user permission — config at rest OR a runtime "extend" request from the user. The cap is enforced at ENQUEUE time (surplus sentences never enter the queue) — same philosophy as v2 §6: prompt asks, post-processing enforces.

## 12. Receipts & disruption (normative) [EXTENDS §3 events]

  type "played"     {reply_id:str, sentences_completed:int, text_completed:str}
     emitted per completed sentence AND once at message end (message-end carries full totals). Interrupted sentences do NOT count and are NOT in text_completed.
  type "disrupted"  {reply_id:str, sentences_completed:int}
     emitted on user interrupt, immediately after audio stops.
Agent's ground truth = "user heard sentences 1..N" — NEVER more; an agent reasoning past its receipts is non-conformant. What the agent does on disruption (silence vs hold vs re-queue a single clarifying sentence) is AGENT logic above the wire; the engine only reports.

## 13. Config deltas (normative) [AMENDS §4]

  voice.tts.engine      str|null  default null      # VibeVoice-class when set
  voice.tts.budget_ms   int       default 1000      # per sentence (B7)
  voice.reply.sentence_cap int    default 1         # §11
  voice.reply.extendable bool    default true       # §11

## 14. Budget additions (normative; numbering final, supersedes v2.1 §7 prose)

  B1  capture overhead          p95 <= 5 ms / 30ms frame
  B2  segment finalize latency  <= 50 ms
  B3  STT throughput            >= 10x realtime (M-class)
  B4  gate verdict latency      <= 2000 ms p95 hard; over => fallback:true
  B5  escalate-to-input         <= 20 ms
  B6  sentence-cap enforcement  <= 30 ms (string ops only)
  B7  TTS first-audio           <= 1000 ms per sentence, from sentence accepted
  B8  silence verdict cost      zero events beyond verdict envelope
  B9  memory ceiling            <= 2.5 GB resident (base STT + gate + TTS loaded)
  B10 queue drain               <= 300 ms gap between consecutive sentences of a reply (sentence N last audio -> N+1 first audio)
  B11 interrupt-to-report       <= 100 ms from user interrupt detected (audio stopped) to "disrupted" event emitted
  B12 played-receipt latency    <= 50 ms from sentence audio completion to "played"
All budgets assert in conformance runs on reference hardware; ports file explicit waivers. Conformance corpus ADDS fixtures: multi-sentence reply with interrupt after sentence 2 (expect played x2, disrupted{2}), cap=1 reply (expect played{1} only), runtime-extend (expect played{1..3}).

— sapphire, 2026-09-25, repo @9725d35. v2.1 text unchanged; this is a separate amendment file, history preserved.
