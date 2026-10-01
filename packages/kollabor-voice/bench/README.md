# Local voice classifier evidence

Laya is the default. `provider` remains available through `/voicemode classifier`.
The bundled `voice-intent-v1.safetensors` is a small radial-basis decision head
trained on frozen representations from Laya's pinned `typed-decisions` encoder.
It uses the CLS vector, the difference between option-marker vectors, and the
mean state vector. It does not generate text or call a hosted service.

The generic checkpoint's voice intent accuracy was inadequate. Experiments with
question wording, a linear scorer and low-rank fine-tuning did not establish a
reliable general voice gate; those experimental weights are not shipped. The
selected head uses 236 synthetic/regression training utterances and 40 separate
calibration examples. Hyperparameters minimize calibration log loss. The first
48 evaluation examples became development data after error analysis; this is
explicit in `voice-intent.json`. A second 48-example evaluation split and the
40 examples in `voice-acceptance.json` were not used to fit this head.

`voice-acceptance-results.json` records the actual runtime policy on those 40
examples: 34 correct automatic decisions, 2 incorrect automatic decisions and
4 decisions deferred to the main agent. Underlying argmax accuracy was 37/40, which must not be confused
with successful automatic handling of all 40. The 0.8 uncertainty threshold is a
product guard, not a guarantee that high-confidence predictions are correct.
This is not production acceptance of background-speech discrimination. The
remaining errors and real recorded conversation coverage need further work.

Uncertainty now routes the full utterance and playback context to the main agent,
which can return a single period to remain silent. Echo is classified by Laya;
it is not removed by a text-match prefilter. A deferral is not evidence that the
main agent chose correctly. Integration tests cover the silence sentinel before
synthesis, while physical playback and main-agent behavior need separate evidence.

Recent transcript history defaults to 10 lines, including ignored speech. The
earlier 40-example report does not evaluate that extension. Development probes
found that a TV notice could dominate the small head even for a clear new request.
The runtime now compares the history-aware result with the current utterance
alone; disagreement or uncertainty defers with the full window. It does not
discard history or force either model choice. The unchanged model weights and
these inspected probes are not production acceptance of contextual intent.

Seven real-model context probes are recorded in
`/tmp/kollab-voice-context-model-proof.json`. Warm checks requiring two intent
passes measured about 68–82 ms on this Mac. An installed-client RPC probe confirmed
all 10 history IDs reached the persistent worker; separate real-provider checks
kept a TV-context greeting silent and answered an explicit request to Kollab.
See the [implementation evidence](../../../specs/voice-mode/implementation-status.md)
for the exact boundaries and artifacts. Recognition mistakes in Whisper's input
remain a separate limitation.

`voice-playback-regressions.json` adds eight inspected echo/interruption examples.
The real model produced three correct automatic decisions and five deferrals;
see `voice-playback-results.json`. This is a development regression set, not blind
acceptance. Separately, a synthetic uncertain echo admitted through the live
attached agent's normal queue produced exactly `.`, no tool calls, and no speech
receipts. That proves the main-agent silence path for that example, not all five
deferred cases or physical talk-over recognition.

Local measurements separate startup from inference: see the report for cold
import/model load, first inference and warm mean. These cannot be compared to a
hosted provider's warm latency as one combined number. The reference
integration uses the same persistent-worker principle; Kollab owns its worker,
isolated dependencies, verified cache, transcript admission and selection state.

Reproduce with the pinned Laya environment and this package installed/on
`PYTHONPATH`:

```sh
python bench/evaluate_voice.py --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --out /tmp/voice-evaluation.json
python bench/evaluate_voice.py --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --fixtures bench/voice-playback-regressions.json --out /tmp/voice-playback-evaluation.json
python bench/train_voice_head.py --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --out /tmp/voice-head.safetensors
```

Training output does not install itself. Updating the shipped head also requires
its size/checksum and question checksum in `manifest.json`, an evaluation report,
and installed-worker verification. No personal microphone recordings are used
by these scripts; benchmark evaluation is local.

## Spoken response review

Incoming recipient intent and outgoing speech style are separate decisions. The
base checkpoint approved all eight initial style probes, including code and long
prose, so it is not used zero-shot for this gate. `spoken-style-v1.safetensors`
is a separate small head on the same encoder already loaded for incoming speech.
The worker warms both heads before reporting ready; it does not load a second
encoder or create another process.

`spoken-style.json` contains 103 training, 26 calibration and 26 heldout synthetic
examples. A multiline fixture encoding defect was corrected after the initial
fit, so this reused split is not claimed as blind acceptance. The raw head has
24/26 correct argmax decisions, including one incorrect automatic decision and
one uncertain result. `spoken-style-policy-results.json` evaluates the actual
format-plus-classifier policy: all 12 suitable replies were allowed and all 14
unsuitable replies were held. This is not human-rated naturalness or general
production acceptance.

That report measures about 33 ms per warm model inference on the test Mac. Format
rejections are reported separately from model decisions. A rejection triggers
one tool-free agent rewrite and another check; a second rejection stays silent.
The synthetic set missed a valid live rewrite that received only 0.56 confidence.
An uncertain style decision now defers to the responding provider for a tool-free
check instead of being treated as a rejection. That additional provider latency
is separate from the local inference measurement. The policy report records
deferrals explicitly; none of its 26 examples exercises this path. Regression
tests and a separate live probe exercise it without fitting new model weights.
Only `spoken_text` enters this policy. `display_text`, tool calls, tool results
and private reasoning never reach the speech classifier or synthesis.

```sh
python bench/train_voice_head.py --task speech --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --fixtures bench/spoken-style.json --out /tmp/spoken-style.safetensors
python bench/evaluate_spoken.py --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --out /tmp/spoken-style-policy.json
```
