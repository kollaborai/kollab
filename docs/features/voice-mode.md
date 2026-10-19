# Voice mode

Run `/voice on` in the chat that should respond to you. Kollab prepares local
voice support in the background, downloads Whisper base and the default Laya classifier, and prepares the selected
speech engine (Kokoro by default), warms them, then starts the microphone. Engine setup is automatic. The input box stays usable during setup.

Use `/voice status` to inspect setup/download progress, microphone activity,
model warmup, transcription, speech playback and errors. The transcript file uses
UTC timestamps. The compact status row shows a three-bar input level and what voice
is doing (`starting`, `listening`, `hearing`, `transcribing`, `deciding`, `asking`,
`speaking`), what the classifier did with your last words (`laya sent`, `ignored`,
`unsure`, `deciding` or `stopped`, then how many lines wait), voice's own last line
for ten seconds (`said: Let me check on that.`) and, when space permits, the latest
transcript. The web UI shows the same line above the message box.

- `/voice` toggles voice for this chat.
- `/voice on` makes this chat the responding conversation.
- `/voice off` releases its microphone lease and cancels unplayed speech.
- `/voice status` shows the service, owner, devices, transcript and queues.
- `/voice retry` retries now. Voice also retries on its own (see Recovery).
- `/voice classifier` opens the decision log: each classifier call on the left;
  on the right what was heard, what the classifier read (transcript history, recent
  conversation, the agent's own speech), each pass's answer, the decision, and what
  reached the agent. Lines dropped as the agent's own voice show as `echo`. It
  updates while open. Without a terminal it names the classifier and the choices.
- `/voice classifier laya` selects the default local classifier.
- `/voice classifier qwen` selects Fine-Tuned Qwen3 0.6B, also local, on an
  Apple silicon Mac (see [Select a classifier](#select-a-classifier)).
- `/voice classifier provider` selects the active AI provider instead.
- `/voice context` shows the recent transcript window (default 10 lines).
- `/voice context 20` changes it to 20 lines; the allowed range is 1–50.
- `/voice enroll` saves your voiceprint from your next 30 seconds of speech; `/voice
  enroll forget` deletes it. See [Echo cancellation and the speaker gate](#echo-cancellation-and-the-speaker-gate).
- `/voice config` opens Voice Configuration in the TUI or Settings → Voice in the web UI.
  Change the classifier and transcript context there, then save for this device.
  Select Whisper Base or Parakeet v3 under Transcription Engine (see
  [Select a transcription engine](#select-a-transcription-engine)), and Kokoro or
  VibeVoice and their voices under Speech Engine. The panel also shows microphone,
  operating state, classifier readiness, transcription readiness and speech readiness. Saving does not enable the microphone.
  Preferences are shared across chats on the device running Kollab; an attached
  remote host's web settings apply to that host.
  `/voicemode`, `/vm`, `/voicemodels` and `/vmodels` are no longer registered.

## Flow at a glance

```mermaid
flowchart TD
    Start["/voice on"] --> Plugin["VoicePlugin connects to device voice service"]
    Plugin --> Prepare["Prepare local Whisper and Kokoro<br/>Prepare Laya or Qwen if selected"]
    Prepare --> Ready{"Service and selected classifier ready?"}
    Ready -->|waiting| Poll["Poll readiness"]
    Poll --> Ready
    Ready -->|error| Failed["Show error; do not claim microphone<br/>start again on its own"]
    Ready -->|ready| Lease["Claim device microphone lease"]

    Lease --> Capture["Recorder captures audio frames"]
    Capture --> Segment["Segmenter groups speech into utterances"]
    Segment --> STT["Local Whisper or Parakeet transcribes"]
    STT --> Log["Append timestamped transcript"]
    Log -.-> Status["Status row: mic state + latest transcript"]
    Log --> Observe["VoicePlugin reads final transcript<br/>waits for speech to settle"]

    Observe --> Classifier{"Selected classifier"}
    Classifier -->|Laya default| Laya["Local Laya worker<br/>transcript + conversation context"]
    Classifier -->|Qwen option, Apple silicon| Qwen["Local Qwen worker<br/>same context"]
    Classifier -->|Provider option| Provider["Configured provider classifier"]
    Laya --> Intent{"Decision"}
    Qwen --> Intent
    Provider --> Intent
    Intent -->|Ignore| NoTurn["No chat turn; transcript stays logged"]
    Intent -->|Unsure| Ask["Voice asks: Sorry, was that for me?"]
    Ask -->|yes| Line
    Intent -->|Respond, or called by name| Line["Voice says its own line at once<br/>Let me check on that. / On it."]
    Line --> Admit["Admit selected IDs<br/>with the line voice already said"]
    Admit --> Agent["Normal Kollab conversation and permission flow<br/>local or attached/Hub agent"]
    Agent -.->|no answer yet| Progress["Still-working lines"]
    Agent --> Reply["Voice-tagged reply<br/>display_text + spoken_text"]
    Reply --> Review["Review spoken text<br/>one rewrite; provider final check if the local classifier rejects"]
    Review -->|approved| Synthesize["Local speech queue + Kokoro synthesis"]
    Review -->|still rejected| Strip["Speak it with code, links<br/>and paths taken out"]
    Strip --> Synthesize
    Synthesize --> Play["Play on the microphone-owning device"]
```

## Intent decision detail

The classifier selects which transcript IDs should wake the responding agent; it
does not draft the answer. Provider classification is respond/ignore. Laya can
also defer uncertain or unsupported speech to the main agent.

```mermaid
flowchart TD
    Batch["Settled transcript batch<br/>up to 8 pending records + recent context"] --> Fresh{"Within 30 seconds?"}
    Fresh -->|No| Context["Keep as context; never sent"]
    Fresh -->|Yes| Route{"Selected classifier"}

    Route -->|Laya default| Laya["Local Laya worker<br/>on the microphone-owning device"]
    Route -->|Provider| Provider["Tool-free provider observer<br/>configured provider or attached agent"]

    Laya --> Assess["Group connected utterances; consider<br/>conversation, transcript history,<br/>and assistant playback"]
    Assess -->|Unsupported language, oversized,<br/>low confidence, or history conflict| Defer["Mark those IDs unsure"]
    Assess -->|Confident| LIntent{"Does the speaker address<br/>or continue with Kollab?"}
    LIntent -->|Yes| LSelect["Select response IDs"]
    LIntent -->|No| LIgnore["Ignore; keep as context"]
    Defer --> LResult["Return response IDs plus unsure IDs"]
    LSelect --> LResult
    LIgnore --> LResult

    Provider --> PContext["Review transcript and limited context;<br/>do not run tools"]
    PContext --> PResult["Return respond/ignore<br/>with transcript IDs"]
    LResult --> Validate["Validate classifier result and IDs"]
    PResult --> Validate

    Validate -->|Error, timeout, invalid IDs| Retry["Keep speech pending; show error;<br/>retry on its own, waiting longer each time"]
    Retry --> Batch
    Validate -->|Valid| Current{"Classifier and lease<br/>still current?"}
    Current -->|No| Recheck["Do not admit a stale result"]
    Current -->|Yes| Any{"Any response or unsure IDs?"}
    Any -->|No: ignored only| NoTurn["No chat turn;<br/>retain transcript for context"]
    Any -->|Unsure only| Ask["Voice asks: Sorry, was that for me?<br/>a yes sends them"]
    Any -->|Yes| Expand["Include the full connected utterance<br/>when any segment is selected"]
    Ask -->|yes| Expand
    Expand --> Guard["Recheck device ownership<br/>and duplicate-admission state"]
    Guard --> Admit["Voice says its own line, then dispatches<br/>the selected IDs with that line"]
    Admit --> Main["Main agent answers, adds to voice's line or holds off;<br/>then normal permission/tool flow"]
```

Voice activates only through an explicit command. Starting another agent or
inheriting an old enabled setting never starts its microphone. The latest
explicit activation owns responses. An older chat shows Active Elsewhere.
Closing the owning chat stops capture when its three-second lease expires.
The shared service can remain idle with its models loaded. Classifier selection is
saved for this device; changing it preserves pending speech and does not enable
the microphone by itself.

## What is local

One process per OS user owns the microphone, speech models and speaker. It uses
Whisper base on CPU (or Parakeet v3 on an Apple silicon GPU, when selected) and
Kokoro ONNX with `af_heart`. The lightweight agent client
has no audio dependencies. First use installs the pinned audio dependencies into
`~/.kollab/voice/runtimes/`; it does not install them into your agent environment.
Downloads resume and must match the sizes and SHA256 hashes in the bundled
manifest before loading. Verified models work offline. Laya runs in one persistent
Python worker owned by the device service, with its own pinned environment and
verified `typed-decisions` checkpoint. Fine-Tuned Qwen3 0.6B, when selected on an
Apple silicon Mac, runs the same way in its own worker and pinned MLX runtime.
Multiple agents share the same worker;
changing the responding conversation does not reload idle model weights. Laya
uses a bundled voice intent head over its frozen checkpoint. The current
[local evaluation](../../packages/kollabor-voice/bench/README.md) records its
remaining classification errors and the decisions it is unsure about; it is not a guarantee that all
background speech will be recognized correctly.

Data lives in `~/.kollab/voice/`:

- `transcripts/YYYY-MM-DD.jsonl`: timestamped recognized speech, including speech
  the AI ignores. Partial audio and explicit gaps are marked and never dispatched.
- `models/`: verified models.
- `cache/speech/`: audio of voice's own lines (acknowledgements, "was that for
  me?", still-working lines) so they play without synthesis, one file per line
  and voice, deleted three days after it last played. Agent replies are never
  kept.
- `speech.jsonl`: queued, synthesizing, playing, played, failed or cancelled
  sentence receipts. Played means the audio device completed playback.
- `consumers/`: per-chat cursors and pending records for interrupted delivery.
- `admissions.sqlite3`: durable request identities used to avoid duplicate turns.
- `preferences.json`: selected classifier; never microphone activation.
- `voiceprint.json`: your voiceprint from `/voice enroll`, 192 numbers made by the
  speaker model, never audio. Readable only by you; never synced or sent.
- `logs/service.log`, `logs/laya.log` and `logs/qwen.log`: setup and worker
  diagnostics.

Raw microphone audio is not saved. Owned, closed daily transcript files expire
after 30 days. Active files and legacy project transcripts are preserved. The
socket and transcript files are restricted to the current OS user.

## When the AI responds

After speech is written to the transcript, the local classifier (Laya, or Qwen
when selected) decides whether it is
addressed to the assistant. A voice-specific decision contract includes the full
utterance, the previous 10 complete transcript lines by default, the last six
visible conversation messages (three exchanges, 200 characters each) and any
overlapping assistant playback. When that runs over Laya's context, the oldest
conversation text is dropped first. Prior
transcript lines include ignored speech and already admitted requests. They are
context only and cannot be submitted again. The window clears when capture is
restarted and never includes a different microphone owner's session.

Laya judges the utterance twice when history is present: once with the history
and once on its own. A pass at or above the confidence threshold (0.8) decides,
so history alone cannot push a clear request into doubt. When neither pass is
sure, or the sure passes disagree, voice asks "was that for me?" (see What voice
says itself). This prevents a background-TV notice from acting as a blanket mute
on a clear new request. Confident background speech is ignored. Uncertain,
unsupported-language or oversized utterances are asked about the same way. They
do not block later speech. The main agent responds normally when addressed, or
returns exactly `.` to stay silent. A response containing only that period never
reaches speech synthesis.

Before classification, a line that repeats what the assistant itself just spoke
(the microphone hears its own playback) is matched against the overlapping
playback text. Echo stays in the transcript but is never classified or
delivered. A reply under three words counts as echo only when it equals a whole
spoken sentence, so "stop" over a reply that says "stop" still arrives. Talking
over voice merges both into one line ("Bear with me still looking into- Hello,
can you hear me?"): runs of three or more words matching the playback are cut,
and only the human's words are judged and sent.

The first voice turn of a session carries the full voice rules (the
`<display_text>`/`<spoken_text>` fields and the silence rules). Later turns
carry only `[voice mode on: the user said this aloud]`, plus `[you already
said: ...]` with voice's own line. A new voice session, or a history that
compaction emptied of the rules, sends them again.
Invalid results or provider failures pause admission with a visible error and
retry on their own; there is no automatic switch to another provider.
`/voice status` names the selected classifier and reports actual setup/readiness.

Choosing `provider` uses a separate tool-free request through the active chat's
AI profile. That option sends transcript text and limited recent conversation
context, including the recent transcript window, to the configured provider and
can incur inference charges. Accepted
speech, and speech confirmed after "was that for me?", still goes to the main
agent under either classifier option. Local classification does not make a
hosted main agent local. Once listening, an
unavailable classifier or agent does not stop durable transcription.

Connected speech segments are kept together, including a lead-in initially
classified as background speech. The observer waits for a pause and pending
transcription before admitting a request; selecting its question includes the
rest of that connected utterance.

Accepted requests enter the normal conversation queue and permission flow.
Assistant responses have separate `display_text` and `spoken_text` fields. The
full answer and code examples stay on screen. Only the spoken field reaches the
speech classifier and device queue; there is no fallback that reads display text.
Tool calls, tool results and private reasoning never become spoken content.

Before playback, the selected classifier checks that narration reads as natural
speech. One or two short sentences are preferred, but length alone is never a
reason to rewrite: a longer reply is still spoken. Laya uses a separate style
head over the same loaded encoder; Qwen uses the same model. If the local
classifier is unsure, the agent's configured
provider performs a tool-free style check of only the spoken text. Format limits
still reject code, lists, links and paths. A rejected spoken reply gets one
tool-free nudge to the responding agent to rewrite it, then is checked again. If
the local classifier rejects that rewrite despite valid formatting, the configured provider
makes one final style decision. A missing spoken field is created on that host
first, and that counts as the one rewrite. A reply still rejected after its one
rewrite is spoken with code, links, paths and markup taken out; it is skipped
only when nothing speakable is left, and status shows the reason. The written
answer stays on screen. The next reply is processed automatically and clears the
earlier error when its review starts. An empty spoken field or `.` means
silence.

Brief updates before tools and final narration use the same sentence queue.
The `voice_out` tool uses that adapter for intentional spoken updates and returns
a nudge when its text is rejected. Repeating an admission does not repeat matching
sentences. Cancellation and ownership changes prevent a late rewrite from playing.

Echo the playback match misses still reaches the classifier with the overlapping
playback text as context. Overlapping speech remains in the transcript. Echo
Cancellation (off by default, see below) removes voice's own speech from the
audio before transcription. Recognition and response decisions can be
imperfect; use `/voice off` to close the microphone immediately.

If inference fails, the transcript remains pending instead of becoming an
automatic command, and voice retries on its own. Speech more than 30 seconds old
by the time voice can decide is kept as context for what comes next and is never
sent. Once a request is admitted, its progress and final spoken replies can
arrive after that deadline while the same conversation still owns voice.
An ambiguous admission is reconciled with the durable host record rather than
blindly submitted again. Restarting or changing the owner starts at the current
transcript position and does not replay old speech.

Attached chats keep audio on the device running the TUI. Transcript observation
uses the local Laya (or Qwen) worker by default, or the attached agent for the provider
option. Request admission runs through the attached agent; spoken replies come back
to the local speaker queue. No audio model or microphone starts in the remote
agent process.

## What voice says itself

Voice speaks before the agent does, so speech never meets silence:

- **Speech for the agent:** a short acknowledgement ("Let me check on that.",
  "On it.") at once, sent with the words as `[you already said: ...]`, so the
  agent answers from there instead of repeating it.
- **A mic check, thanks or a bare name:** voice answers itself ("Yes, I can hear
  you.", "You're welcome.", "Hi, I'm listening.") and still sends the words to the
  agent with that answer. The agent may add more, or hold off with `.`. After a
  bare name, the next 15 seconds of speech go to the agent too.
- **Unsure speech:** "Sorry, was that for me?" A yes sends the held words. A no
  ("no, it's the TV") stops these questions for two minutes. No answer within 12
  seconds after voice stops talking stops them for 30 seconds. Calling the agent
  by name always gets through, and unsure thanks are not asked about.
- **While the agent works:** still-working lines ("Still working on it.") after 8
  and 25 seconds, unless the agent has spoken or voice already answered the turn
  itself.

Each line is said once per batch of words, even when admission is retried. Its
audio is kept in `cache/speech/`, so a line voice has said before plays at once.

## Echo cancellation and the speaker gate

Two filters sit in front of transcription. Both are off until you turn them on
under Echo and Speaker in `/voice config` (terminal) or Settings → Voice (web).

**Echo Cancellation** removes voice's own speech from the microphone. The speaker
queue records what it plays and when; WebRTC's echo canceller (AEC3) subtracts
it from each microphone frame. Only voice's playback is removed: a TV, other
people and other apps keep their sound. With it on, voice's replies stop showing
up as heard speech. Talking over voice gets harder, though: while voice speaks,
the canceller also turns down your words, so an interruption is heard less
reliably. See the [measurements](../../packages/kollabor-voice/bench/README.md#echo-cancellation).

**Speaker Gate** passes only your voice to the classifier. Turn voice on, run
`/voice enroll` and talk for 30 seconds, alone in a quiet room; it prints a
passage you can read. Lines heard while voice speaks, and lines unlike the rest,
are left out of the voiceprint. Nothing you say during enrollment reaches the
agent; the status row counts the seconds. Then turn on Speaker Gate. Each line
of a second or more is compared with your voiceprint; a line below Speaker
Threshold (default 0.45) is marked another speaker. It stays in the transcript
as context for later decisions and never reaches the agent. Lines under a
second are not checked and pass. `/voice classifier` lists gated lines as `not
you` and enrollment lines as `enroll`, with each line's score; `/voice status`
shows both filters.

The speaker model (3D-Speaker ERes2Net, Apache-2.0, 26.5 MB) downloads once,
pinned and checked like the other models, the first time the gate or enrollment
needs it. On recorded speech it rejected 1.6% of a speaker's own lines and let
through 0.5% of other people's and 0.01% of a TV film's
([measurements](../../packages/kollabor-voice/bench/README.md#speaker-gate)).
Raising the threshold keeps out more of the TV and more of you.
`/voice enroll forget` deletes the voiceprint; a new speaker model needs a new
enrollment.

## Recovery

Voice recovers on its own; nothing waits for `/voice retry`:

- When the voice service dies, the connection lease expires, setup fails or
  capture stops with an error, the client starts again after 5 seconds, doubling
  up to 2 minutes. A run that stays healthy for a minute resets the wait. The
  status row shows `starting` until the microphone is live again.
- A failed speech engine (a VibeVoice worker that timed out or exited) is
  prepared again on the next sentence, and on the next start.
- A failed or timed-out classifier keeps speech pending and retries with the
  same growing wait; a failed Laya or Qwen worker is prepared again.
- A voice service call that answers late (models loading, a worker starting) is
  waited for, not treated as a stop.
- Another chat taking the microphone is not a failure: this chat shows Active
  Elsewhere and stays off.

`/voice retry` skips the wait and `/voice off` cancels it.

## Agent integration

The owner can issue an output-only capability with the local `delegate` request.
Pass that capability explicitly to a background producer. It cannot read the
transcript, open the microphone, transfer ownership or extend the owner's lease.

```python
from kollabor_voice.client import VoiceClient

await VoiceClient().voice_out(
    text="The build finished. All checks passed.",
    reply_id="build-42-result",
    owner_epoch=capability["epoch"],
    producer_id=capability["producer_id"],
    token=capability["token"],
)
```

Queue overflow is an explicit rejection. Cancelled synthesis results cannot play
later, and cancelled playback is stopped. Speech interrupted by a service restart
is recorded as cancelled and is never automatically replayed.

Implementation and measured platform/acceptance coverage are recorded in
[`implementation-status.md`](../../specs/voice-mode/implementation-status.md).


## Select a classifier

Laya is the default. On an Apple silicon Mac, Classifier in `/voice config` (or
`/voice classifier qwen`) also offers **Fine-Tuned Qwen3 0.6B**: Qwen3-0.6B
(Apache-2.0) with a LoRA adapter trained on synthetic fixtures and generated echo
and talk-over cases, no household speech. One model makes all three local
decisions, each as one constrained token: whether speech is for the agent, what
the reply to "Sorry, was that for me?" means (yes, no, or not an answer) and
whether a spoken reply reads as natural speech. With Laya or the provider, word
rules read that reply; Qwen reads it beside the held words and the question.
Other platforms are not offered it, and saving it there is refused.

It runs as MLX 8-bit in its own worker process and pinned runtime (`mlx` 0.32.4,
`mlx-lm` 0.32.2), loaded once, with 645 MB of weights checked against the
manifest's SHA-256 before loading. Intent and style decide at a probability of
0.8 or more and ask or defer below it, as Laya does; it judges each utterance
once, with its history. Non-English speech is asked about. Switching closes the
old worker; speech stays pending until the new one is ready. Classifier
Readiness and `/voice status` show its state.

On one day of real household speech (M2 Max):

| Measure | Qwen | Laya |
| --- | --- | --- |
| Intent, right / wrong / unsure | 70 / 18 / 13 | 45 / 20 / 36 |
| Replies to "was that for me?" | 59/60 synthetic, 11/12 real | 44/60 synthetic (word rules) |
| Spoken style, right / wrong / unsure | 71 / 22 / 26 | 65 / 21 / 33 |
| Intent latency p50 / p95 | 96 / 118 ms | 102 / 130 ms |
| Peak memory | 1.75 GB | 3.87 GB |

Its mistakes lean the other way from Laya's: 12 of its 18 are false wakes, mostly
room or TV fragments; most of Laya's are misses
([benchmark](../../packages/kollabor-voice/bench/README.md#fine-tuned-qwen3-06b)).

The weights have no download source yet. Until they do, selecting Qwen reports
the folder they belong in: put the seven files of the fused 8-bit model in
`~/.kollab/voice/models/qwen/voice-qwen-v1/`. They load only when every size and
SHA-256 matches the manifest.

## Select a transcription engine

Open `/voice config` (terminal) or Settings → Voice (web), choose Transcription
Engine and save for this device. **Whisper Base** is the default. **Parakeet v3**
is NVIDIA's Parakeet TDT 0.6B v3 (CC-BY-4.0, the `mlx-community` MLX conversion)
running on the GPU through `parakeet-mlx`; it needs an Apple silicon Mac and the
setting refuses it elsewhere. The choice applies without restarting voice.

Selecting Parakeet installs its own pinned runtime into `~/.kollab/voice/runtimes/`
and downloads 2.5 GB of weights once, at a pinned revision checked against the
manifest's SHA-256 before loading. It runs in its own worker process, loaded once.
Whisper stays loaded: it transcribes while Parakeet installs, downloads and warms,
and whenever Parakeet stops; Parakeet is prepared again five minutes after a
failure. Transcription Readiness and `/voice status` show which engine is live and
any download progress or error. The same voice activity detector cuts speech into
utterances for both.

On an M2 Max, Parakeet took a median 69 ms per utterance against Whisper Base's
562 ms, with half the word errors on clean speech and the same share with a
second voice talking
([benchmark](../../packages/kollabor-voice/bench/README.md#speech-to-text-whisper-base-vs-parakeet-v3)).
Differences to know:

- Whisper is told the agent names; Parakeet has no way to be. In the benchmark it
  still wrote aquamarine, lapis and zircon right, but never "Kollab" ("Column",
  "Colab", "Colib").
- On a Mac short of memory the first utterance after a pause can stall while
  macOS brings the idle worker back: one run saw 14–113 s. A line that takes over
  30 s is transcribed by Whisper instead, and Parakeet is prepared again five
  minutes later.
- Parakeet transcribes 25 European languages but does not report which one, so
  its text reaches the local classifier as English. Whisper's non-English speech is
  deferred to the agent.

## Select a speech engine

Open `/voice config` (terminal) or Settings → Voice (web). Choose `vibevoice`
under Speech Engine and save for this device. This prepares an isolated runtime
from a pinned Microsoft VibeVoice source archive, verifies the selected voice
preset by hash, and warms the model without opening the microphone. Read Speech
Readiness or `/voice status`. A setup error retries on its own; `/voice retry`
retries now.

VibeVoice uses Microsoft VibeVoice-Realtime-0.5B. Presets are Carter, Davis, Emma,
Frank, Grace, Mike and Samuel. A trusted cached `.pt` file in Custom VibeVoice
Prompt overrides the preset. Model accepts the default Microsoft model name or
an existing absolute model directory; an installed model in the usual local
VibeVoice directory is detected. Auto device chooses MPS, then CUDA, then CPU.
Diffusion Steps (1–20) and Guidance (1.0–3.0) control generation.

Kokoro remains selectable with its English voice presets and Speed (0.5–2.0).
Inactive engine settings are retained. A change applies at the next sentence;
failed VibeVoice inference is shown as an error and never silently becomes
Kokoro. Both engines return waveform samples to the same serialized speaker
queue. Cancellation and ownership changes still suppress late synthesis. The
VibeVoice worker never plays audio itself or changes the Codex speech hook.

Dependencies, voice presets and default weights download on first use and are
cached. Heavy libraries stay out of the main Kollab process. Custom model
directories and custom voice prompts are user-owned files and are preserved.
