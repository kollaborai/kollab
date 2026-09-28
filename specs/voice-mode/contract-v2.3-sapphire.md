# Voice Engine Contract — v2.3 amendment (sapphire)

Amends contract-v2.2. §§1-14 remain in force except as marked. malmazan's echo ruling (2026-09-25 stream): mic stays OPEN during playback; the agent must never hear itself speak. Echo defense = three layers: acoustic cancellation, event flag, prompt defense.

## 15. Playback reference & AEC (normative) [AMENDS v2.2 §10 implicitly]

  The engine records the ACTUAL playing audio track (post-mix, what leaves the speakers) as the AEC reference signal and subtracts it from mic input. Mic stays OPEN during playback — talk-over is the point (v2.2 §12 receipts make interruption safe). HARD DUCK (v2 §9, agenda C5) is demoted to FALLBACK state, entered only when B13 or B14 budgets are exceeded — not the default.

## 16. Segment echo flag (normative) [EXTENDS §3 "segment" event]

  type "segment"  gains: echo_suspect:bool
  Set when post-cancellation residual CORRELATES with the playback reference window (probable self-echo leak — cancellation failed on this segment). echo_suspect segments still flow to the gate; the gate decides. They are marked in transcript JSONL identically (envelope carries the flag).

## 17. Gate prompt clause (normative text)

  The ambient preamble (voice.ambient_prompt) MUST include verbatim:
  "What you just heard may be yourself hearing yourself speak. When in doubt, wait rather than respond."
  Prompt defense is the LAST layer and must hold even when cancellation fails — conformance §19 proves it does.

## 18. Budgets (numbering continues; final)

  B13 AEC cancellation overhead   <= 3 ms per frame (30ms frames), inclusive of reference-buffer correlation
  B14 echo_suspect detection      <= 50 ms post-finalize, before gate dispatch
  B13/B14 breach -> FALLBACK state per §15 (hard duck + hard-duck budgets from v2 §9/§11 resume; engine emits state "degraded", reason "aec").

## 19. Conformance corpus additions (normative fixtures)

  E1  self-echo, no user speech: agent TTS replayed through mic with no user speech. Expect: cancellation removes it OR gate returns silence verdict. ACCEPT = zero escalated events. The prompt defense must hold even when cancellation is forced off (fixture variant E1b runs AEC disabled).
  E2  talk-over: user speech overlapping active TTS playback (reference present). Expect: user segment extracted cleanly — no echo content in text, no echo_suspect flag when cancellation succeeds (E2b, AEC disabled: echo_suspect may be true; escalation permitted only if gate still extracts the user's words — text must match the user-utterance regex, not TTS content).
  E3  residual echo + real speech mix: cancellation partially fails mid-sentence. Expect: echo_suspect:true AND either silence verdict or clean user text — never TTS-derived words in an escalated segment.

— sapphire, 2026-09-25, repo @9725d35. v2.1/v2.2 text unchanged; separate amendment file, history preserved.
