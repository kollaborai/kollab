# Issue #301: LiveKit voice, classifier out of the voice path

Status: built on `issue-301-livekit-cutover` (PR #318), proven live in a scratch
web UI on 2026-10-23. PhoneLink voice and the before-latency baseline are open.

## Scope

Voice runs on the LiveKit Agents stack the livekit-demo uses. The intent and
style classifiers stay in Kollab for other features; voice never starts or calls
them, and no voice screen shows classifier controls, status or decisions.

## Pipeline

- The Engine owns one local `livekit-server` (loopback, per-run key) and one
  agent worker per voice session (`kollabor_engine/voice_livekit.py`).
- The worker (`kollabor_voice/livekit_worker.py`) runs an `AgentSession`:
  Silero VAD, the v1-mini turn detector, VAD interruption. Parakeet does
  speech-to-text and VibeVoice Carter text-to-speech, both through the one voice
  service owner.
- A final transcript becomes a turn of the selected Kollab session: the worker's
  `llm_node` POSTs `/voice/sessions/{id}/turns` and reads NDJSON lines
  (`{"text"}` per sentence, then `{"done", "turn_id"}`). Replies are spoken
  sentence by sentence while the rest is still generating.
- Talking over the agent works like the demo (LiveKit's defaults, `mode: vad`):
  half a second of speech pauses the voice. Words in it commit a user turn,
  which interrupts the reply: the worker closes the `/turns` stream, and the
  Engine revokes the turn's admission and cancels the session turn. No words
  within 2 s (a cough) and the voice resumes where it paused.
  `kind: "greeting"` is a hidden instruction turn that opens the room.
- The worker reports `listening`, `hearing`, `thinking` and `speaking` from
  LiveKit's own state changes; the Engine never guesses them.
- Turn ids are canonical UUIDs. Thinking runs at the lowest level the model
  takes (`kollabor/llm/voice_turn.py`); the user's provider, key and model stay.
- Voice turns run through the same guarded turn path as typed ones, so the
  dangerous-command block list applies (no voice-specific test proves it yet).

## Web UI

- The composer mic starts the room in this browser, and the pulsing mic stops
  it. The strip above the composer shows the state, Interrupted, model
  downloads, latency (Hear, Think, Speak, Total) and a stop button.
- The browser joins only once the runtime is ready, so a cold start that is
  still loading or downloading models waits instead of failing.
- `livekit-server` listens on loopback, so only a page served from this computer
  gets the room. Elsewhere the mic is the `/voice` toggle (the computer running
  the agent listens), as before.

## Talk-over

- 0.5 s of voice pauses the reply. Words cancel it and become the next turn; a
  cough (no words within 2 s) resumes it.
- The talked-over reply keeps only the part that was played, marked
  interrupted: the worker posts `turns/{id}/interrupted`, the Engine calls
  `state.voice_interrupted_reply`. Attach RPCs answer in the order they came, so
  no handler waits on a turn: a cut whose turn still has a round to run is
  queued on it (`QueueProcessor.after_turn`), answered at once, and lands under
  the turn lock as soon as no round of that turn is left, always before the next
  model call. A cut that lands late publishes `voice_reply_cut` and open windows
  reload.
- The worker posts each cut on its own, apart from its states and metrics. The
  next turn waits, bounded at 5 s, for the talked-over reply's handle to finish
  and for its cut to be answered (the Engine answers once the daemon has it), and
  never for an event. LiveKit starts the next reply only after it has interrupted
  the current one, except in the few loop turns between a talked-over reply's
  generation ending and its handle finishing; the wait covers those.
- The cut rewrites the conversation log off the event loop, under the logger's
  append lock (on the loop it held the daemon 15 ms at 1.6 MB, ~0.1 s at 11 MB,
  ~0.7 s at 67 MB). An append whose parent the cut dropped threads under that
  record's parent. Records written past the logger while it runs (compaction's
  checkpoint and summary records) are copied into the new file at the swap.
- A cut still queued when `/resume` or a new session swaps the conversation is
  dropped: its turn is not in the new one.
- A talk-over or a Stop while a tool waits for approval answers the prompt
  cancelled (`PermissionManager.cancel_confirmations`, called from
  `LLMService.cancel_current_request`): the tool does not run, every window
  closes the prompt and the turn ends, so the cut lands before the next turn. A
  prompt asked while the turn is stopping is answered cancelled without showing.
- A Stop or a talk-over ends the voice turn's grant with it
  (`QueueProcessor._drain_queue`): the next turn, spoken or typed, runs on its own,
  so its tools run and a talk-over can cut its reply. Input that cuts in while a
  voice turn still has a round to run gets no grant (fail closed).
- Known limits, accepted for 0.18: a daemon that dies with a cut still queued
  loses it (the history and the log keep the uncut reply), and a typed message
  that reaches the daemon in the moment between a talk-over and its cut (the
  worker's post and the Engine's call, right after the audio stops) goes to the
  model before the cut, which lands after that round.

## Logs

`<voice root>/logs/livekit-server.log` and `livekit-worker.log` (the voice root
is `KOLLAB_VOICE_ROOT`, default `~/.kollab/voice`). Each starts over past 5 MB.

## Live evidence (2026-10-23, scratch stack, `say` audio as the browser mic)

- Greeting spoken, the question heard by Parakeet, the reply streamed sentence
  by sentence, at 1280, 820 and 390 px.
- Every talk-over stopped the audio within ~0.1 s and cancelled the turn (the
  model saw its client go away mid-stream), with no "Audio stream failed".
- Latency per turn: Hear 0.1-0.7 s, Think 0.9-1.0 s, Speak 1.1-1.8 s.

## Open

- PhoneLink: the phone reaches the Engine through the encrypted relay channel,
  which carries no WebRTC media. Phone voice needs the phone's audio bridged over
  that channel into the room (or a reachable TURN path).

## Checklist

- [x] One Engine-owned LiveKit flow; no second integration or model manager.
- [x] Turns stay on the selected session and thread, through admission and
      permissions; provider, key and model settings kept; thinking low.
- [x] Classifier modules, models, workers and manifest entries still present;
      the voice path never imports or calls them (tests).
- [x] No orphaned worker or server after stop (process-group kill, tests).
- [ ] PhoneLink voice.
- [x] Before/after latency (#301, "Measurement").
