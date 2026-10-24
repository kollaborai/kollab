# Voice benchmarks

Evidence for the speaker gate, echo cancellation and speech-to-text engines. The
intent and spoken-style classifiers it once covered were removed (issue 301).

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

