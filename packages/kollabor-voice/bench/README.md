# Local voice classifier evidence

Laya is the default. `provider` remains available through `/voice classifier`.
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

## Speaker gate

`speaker-gate-results.json` comes from `speaker_bench.py`. Every source is cut by
the production segmenter. No recordings of the real user exist yet, so the 40
LibriSpeech dev-clean readers stand in: each enrolls with its first 30 seconds
(with the same consistency pass as `/voice enroll`) and is scored against its
remaining lines (false rejects) and against everything else (false accepts):
the other 39 readers, 40 minutes of a 1940 film soundtrack (*His Girl Friday*,
public domain), 50 Kokoro playback lines and 50 macOS `say` lines.

The shipped model is 3D-Speaker ERes2Net trained on VoxCeleb (Apache-2.0, a
26.5 MB ONNX file, pinned by revision and SHA-256). Its equal error rate here
is 1.21% at a threshold of 0.407. SpeechBrain ECAPA-TDNN scored the same (1.2%)
but needs torch in the voice runtime. WeSpeaker ResNet34-LM scored 9.0%: its ONNX
export carries no feature normalization metadata, and fed un-normalized features
it does not separate these speakers, so it is not used.

At the default threshold of 0.45 (3,410 genuine and 163,502 impostor trials):

| Measure | Rate |
| --- | --- |
| False rejects (the user's own lines) | 1.6%: 6.2% for 1–2 s lines, 1.2% for longer ones |
| False accepts: other people | 0.49% |
| False accepts: TV film | 0.01% |
| False accepts: voice's Kokoro playback | 0.05% |
| False accepts: macOS `say` voices | 0.05% |

Lines under one second are not judged (`unverified`) and pass: 3.4% of reader
lines and 2.3% of film lines. Embedding costs about 70 ms per second of audio
on one M2 Max core.

A service run used the real service, Whisper and the pinned model downloaded
through the manifest, with recorded audio in place of the microphone: reader
422 enrolled from five lines (33.9 s); the reader's seven later lines scored
0.65–0.90 (`user`), three film lines 0.02–0.10 and two lines from another reader
0.10–0.14 (`other_speaker`).

Not measured yet: the real user's voice through a laptop microphone, a TV heard
across a room, distance and room noise. Those numbers need `/voice enroll` on
the user's own machine.

```sh
python bench/speaker_bench.py prep DATA
python bench/speaker_bench.py embed DATA DATA/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx
python bench/speaker_bench.py score DATA DATA/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx 0.45
```

## Echo cancellation

`echo-cancellation-results.json` comes from `echo_bench.py`. WebRTC AEC3
(pywebrtc-audio) gets the playback reference exactly as the service feeds it:
512-sample chunks with their predicted play times, each microphone frame paired
with playback 60 ms ahead. A simulated room plays voice's Kokoro reply back into
the microphone (a direct path plus a decaying 120 ms tail) between 40 ms early
and 300 ms late. `say` talks over the reply and again after it ends, at
speech-to-echo ratios of −7 and −1 dB. Whisper base transcribes both signals;
each figure is the share of a line's distinct words found in the transcript.

- Echo drops 31–35 dB when it lands between 40 ms early and 120 ms late, 12–13 dB
  at 300 ms late.
- Voice talking alone: Whisper heard 20–23% of voice's words without
  cancellation and none with it, at every delay.
- The person talking after voice stops: every word heard, with and without.
- Talking over voice: cancellation costs the person's words. Without it Whisper
  heard 27–91% of them, with it 9–64%; AEC3's echo suppressor also suppresses
  the person during double talk, so interrupting voice is less reliable with
  Echo Cancellation on.
- 0.2–0.3 ms per 30 ms frame.

Not proven yet on real speakers and a real microphone.

```sh
python bench/echo_bench.py DATA ~/.kollab/voice/models/whisper-base/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66
```

## Speech-to-text: Whisper Base vs Parakeet v3

`stt-results.json` comes from `stt_bench.py`. Twenty requests, most of them
addressed to an agent by name, are spoken by four macOS `say` voices (Samantha,
Daniel, Karen, Rishi) at 16 kHz: 80 clean utterances, and the same 80 with
another voice saying another sentence 10 dB below (babble). Each engine runs the
service's own code on every utterance, one engine after the other: Whisper Base
through `LocalModels.transcribe` (CPU int8, primed with the agent names as the
service primes it), Parakeet v3 through `ParakeetWorker` (MLX on the GPU in its
own process; it takes no priming). M2 Max, macOS 27.2, 30 minutes after a reboot
(load average 4–7).

| Measure | Whisper Base | Parakeet v3 |
| --- | --- | --- |
| Latency p50 / p95, clean | 562 / 611 ms | 69 / 92 ms |
| Latency p50 / p95, babble | 558 / 592 ms | 73 / 84 ms |
| Word error rate, clean | 2.8% | 1.4% |
| Word error rate, babble | 6.1% | 6.3% |
| Agent names right, clean | 54/60 | 51/60 |
| Agent names right, babble | 56/60 | 49/60 |
| First utterance after 30 / 120 / 300 s of quiet | 587 / 570 / 569 ms | 129 / 128 / 110 ms |

Both engines wrote aquamarine, lapis and zircon right in clean speech. Neither
knows "Kollab": Whisper got it 6 of 16 times with priming, Parakeet never
("Column", "Colib", "Colab", "Coleb", "Colub", "Colin"). Parakeet's other misses
were "Safaya" and "Safi up" for sapphire (Rishi's voice), "Zirkon" and
"Aquamarines". Its worker was ready 2.9 s after start with the files on disk and
holds about 1.6 GB.

A live run used the real service with `/voice on` and Parakeet selected, `say`
playing through the MacBook Pro speakers into its microphone: 7 of 9 lines came
back exact, each in 0.12–0.55 s (the first after a pause the slowest). It wrote
"Zirkon" and "Column", and once heard "Aquamarine, what time is it in Tokyo?" as
"Yeah."; the replay was exact.

Not settled: an earlier run on this Mac under heavy memory pressure (36–67 GB of
swap in use, a training job on the GPU) saw Parakeet's first utterance after
30–180 s of quiet take 14–113 s against Whisper's 1.6–3.5 s, most likely macOS
compressing the idle worker's memory. That run's file was lost to a reboot and
the calm run above does not show it. Not measured: the real user's voice,
accents beyond these four voices, languages other than English.

```sh
PYTHONPATH=src ~/.kollab/voice/runtimes/<voice runtime>/bin/python bench/stt_bench.py --clips /tmp/stt-clips --idle 30,120,300
```

## Fine-tuned Qwen3-0.6B

`qwen-voice-results.json` comes from `qwen_voice.py`. One LoRA adapter on
Qwen3-0.6B (Apache-2.0, pinned revision) makes all three voice decisions, each
as a single constrained token: intent (`respond`/`ignore`), the reply to "was
that for me?" (`yes`/`no`/`other`) and spoken style (`say`/`rewrite`). It is
trained on this folder's synthetic fixtures plus generated echo and talk-over
cases, with no household speech. The real test is private household microphone
speech kept outside the repository (101 intent groups from one day, 12 replies
to the question, 119 spoken updates); only aggregates are recorded.

On the real day, under the runtime policy (0.8 threshold), MLX 8-bit against
the shipped Laya policy:

| Measure | Qwen | Laya |
| --- | --- | --- |
| Intent, argmax correct | 79/101 | 47/101 |
| Intent, right / wrong / deferred | 70 / 18 / 13 | 45 / 20 / 36 |
| Answers, synthetic heldout | 59/60 | 44/60 (regex) |
| Style, right / wrong / deferred | 71 / 22 / 26 | 65 / 21 / 33 |
| Intent latency p50 / p95 | 96 / 118 ms | 102 / 130 ms |
| Peak memory footprint | 1.75 GB | 3.87 GB |
| Download | 645 MB | 846 MB |

Synthetic intent: 40/40 acceptance and all eight echo regressions decided
automatically and correctly (Laya 37/40; 3 automatic, 5 deferred). Qwen's 18
real errors are 12 false wakes (mostly room or TV fragments) and 6 misses (3
"can you hear me?" checks, 3 questions mixed into echo of its own reply);
Laya's are mostly misses (15). Style has no clear winner: both over-reject plain
spoken updates. 8-bit matches fp16; 4-bit loses 5 correct real intent decisions
and 2 real answers. Also fitting on the private real days gave 79→82 argmax and
18→13 errors: a lever for a local per-user fine-tune, never a published model.

Recommendation: ship it on Apple Silicon for all three decisions, as MLX 8-bit
through `mlx==0.32.4` (already pinned for Parakeet), `mlx-metal==0.32.4`,
`mlx-lm==0.32.2`, `transformers==5.20.1`, `tokenizers==0.23.3` and
`numpy==2.5.4`, with the MLX buffer cache capped (uncapped it reached 15 GB).
Keep Laya on other platforms until the same weights are measured through the
existing torch runtime on Linux CPU (on MPS: p50 104 ms, 2.7 GB). GGUF Q8_0
through llama.cpp was fastest (p50 87 ms, 0.59 GB), but llama-cpp-python 0.3.36
has no wheel for this Mac and compiled from source.

Distribution: one Hugging Face model repo holds `mlx-8bit/`, the adapter and a
model card with the exact prompts; `manifest.json` pins its revision and every
file's size and SHA-256 like the Laya bundle. A GitHub Release on the kollab
repo carries the same files as a mirror; the downloader tries Hugging Face first
and accepts mirror bytes only when size and SHA-256 match. Nothing is uploaded
yet: `manifest.json` pins every file with an empty `base_url`, so the weights
are placed by hand.

Wired as the opt-in `qwen` classifier (`kollabor_voice/qwen_worker.py`; Laya
stays the default). `score runtime` runs these weights through that worker's
own `predict`, `predict_answer` and `predict_speech`, so utterance grouping, the
0.8 policy and the prompts are the shipped ones. Every split matches the MLX
8-bit run above exactly (largest probability difference 0.0005, trace
rounding); real intent ran at p50 80 ms, p95 103 ms. The worker refuses
prompts whose SHA-256 differs from the manifest's `prompt_sha256`, since the
weights were trained on them.

Not proven: one test day, and 92 of the 101 real intent labels are the author's
annotations (9 are the user's own answers to "was that for me?"); recent chat
for real cases is reconstructed; latency comes from a machine shared with live
agents and a VM.

```sh
python bench/qwen_voice.py train --playback --out ADAPTER
python -m mlx_lm fuse --model ~/.cache/huggingface/hub/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca --adapter-path ADAPTER --save-path FUSED
python -m mlx_lm convert --hf-path FUSED --mlx-path FUSED-8bit -q --q-bits 8 --q-group-size 64
python bench/qwen_voice.py score mlx --model FUSED-8bit --out /tmp/qwen.json
python bench/qwen_voice.py score runtime --model FUSED-8bit --out /tmp/qwen-runtime.json
python bench/qwen_voice.py score laya --model ~/.kollab/voice/models/laya/1c5edc17a7acd8701df6fc341c0d179f1c62c982 --out /tmp/laya.json
python bench/qwen_voice.py report /tmp/qwen.json /tmp/laya.json
```
