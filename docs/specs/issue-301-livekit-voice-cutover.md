# Issue #301: LiveKit voice, classifier out of the voice path

Status: built on `issue-301-livekit-cutover` (PR #318), proven live in a scratch
web UI on 2026-10-23. PhoneLink voice and the before-latency baseline are open.

## Scope

Voice runs on the LiveKit Agents stack the livekit-demo uses. The intent and
style classifiers stay in Kollab for other features; voice never starts or calls
them, and no voice screen shows classifier controls, status or decisions.

## Pipeline

- The Engine owns one local `livekit-server` (loopback, per-run key) and one
  agent worker per voice session (`kollabor_engine/voice_livekit.py`). The
  server gets free ports for signaling and for ICE over TCP (`--rtc.tcp_port`):
  its ICE/TCP default, 7881, is one port per machine, so a second server
  (another Engine, LiveKit's own demo) exited at startup.
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
  Engine revokes the turn's admission and cancels the session turn while it
  is still the one running (`cancel_current_request(voice_turn_id)`: a typed
  turn that started after it runs on). No words
  within 2 s (a cough) and the voice resumes where it paused.
  `kind: "greeting"` is a hidden instruction turn that opens the room.
- The worker reports `listening`, `hearing`, `thinking` and `speaking` from
  LiveKit's own state changes; the Engine never guesses them.
- The room ends with its worker. A worker that exits by itself (a crash) turns
  its session to error, "The voice worker stopped." (retryable): the Engine
  waits on its exit (`LiveKitRuntime.wait_exited`), and only an exit it did not
  stop counts. The browser leaves the room and frees the mic on that state, and
  the next start makes a fresh session. The Engine waits up to 1260 s for a
  worker to join, past the worker's own 1200 s wait for its models.
- A reply stream that breaks mid-turn ends the turn on both sides: the worker
  reports `turn_incomplete` (retryable), and a fault in the Engine's stream
  still cancels the session turn (only that turn, as a talk-over does) and
  revokes its admission.
- A voice turn's reply stream takes only events stamped with its id
  (`request_id`). A hub wake or a typed turn that runs while the voice turn
  waits in the queue streams to every subscriber with no id, and was said as
  the voice reply. A voice batch that fails closed (merged with typed input, or
  cutting off an unfinished voice turn) keeps the id of the voice turn its
  reply belongs to: no tool runs, but the reply is said and the stream ends. A
  reply shown without the id (an ACP agent's) is said at `turn_complete`.
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
- A stop before the session exists (create still in flight) ends the session
  create makes, and the mic does not start again until it has; a stop while
  the room is being built never connects it.
- `livekit-server` takes signaling on loopback only, so only a page served from
  this computer gets the room. Its ICE ports listen on every interface, but
  answer only peers that joined through signaling. Elsewhere the mic is the
  `/voice` toggle (the computer running the agent listens), as before.

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
- A talk-over or a Stop while a tool waits for approval ends the turn at once,
  so the cut lands before the next turn, and leaves the prompt open
  (`PermissionManager.park_confirmations`, called from
  `LLMService.cancel_current_request`; see "Approvals by voice"). ACP's Stop
  answers its prompts cancelled (`cancel_confirmations`).
- A Stop or a talk-over ends the voice turn's grant with it
  (`QueueProcessor._drain_queue`): the next turn, spoken or typed, runs on its own,
  so its tools run and a talk-over can cut its reply. Input that cuts in while a
  voice turn still has a round to run gets no grant (fail closed).
- Known limits, accepted for 0.18: a daemon that dies with a cut still queued
  loses it (the history and the log keep the uncut reply), and a typed message
  that reaches the daemon in the moment between a talk-over and its cut (the
  worker's post and the Engine's call, right after the audio stops) goes to the
  model before the cut, which lands after that round.

## Approvals by voice

Like the demo: the agent says what it wants to run and asks, and a spoken yes
runs it.

- A voice turn never waits on an approval prompt. A call it may not run
  unasked comes back "Not run yet: this needs the user's OK", and the agent
  says in its reply what it wants to run and why and asks for a yes. That is
  the voice guard's `confirmation_required` (a command on the voice list that is
  not read-only, a read-only command whose tool is not in `/bin` or `/usr/bin`,
  such as Homebrew's `rg`, and the `hub_msg` tool), in every permission mode,
  and in a Confirm mode the permission prompt too (no card opens).
- The user's next message, spoken or typed, decides
  (`kollabor_agent/yes_approval.py`, fed by `LLMService.process_user_input`): a
  short yes ("yes", "yeah, go ahead", "do it", "okay"; "ok thanks" is not one)
  lets each call the agent's last reply asked for run once, past both checks. A
  one-shot note on the yes, seen only by the model, names them; the agent runs
  exactly those and answers with what it found. A short no drops them.
- A yes answers only the reply just before it, once that reply is finished. Its
  asks open when its chain ends (`QueueProcessor.is_processing` falling,
  `YesApprovals.on_reply_done`); a message sent before then answers none of
  them, a reply that asks nothing leaves nothing to answer, and any other
  message is a normal turn that drops them (a parked card stays open). A voice
  ask stops counting after 5 minutes (`ASK_OPEN_SECONDS`), with nothing said. A
  later ask replaces them, and `/resume` or a new chat drops them.
- Words said over the agent never count. The worker marks a turn whose speech
  started while the agent was thinking or speaking (`talk_over` on the turn,
  `voice_talk_over` in its context), also in the AEC warm-up, when LiveKit lets
  the reply play on; a talk-over's cut (`record_interrupted_voice_reply`,
  `YesApprovals.reply_cut`) drops what the cut reply asked.
- Only the user's own words count. `process_user_input` is the human entry
  (terminal, web UI, voice); hub and relay messages, steering and the model's
  own text never answer. A native hub message wakes the agent through
  `TRIGGER_LLM_CONTINUE`; one that goes through `submit_human_input` as another
  agent (its "agent" source: a network-attached agent, a hub message whose
  profile could not be read) leaves the asks alone, as every source outside
  `YES_SOURCES` does (`kollabor/user_input_source.py`: the terminal, an
  attached window, state RPC for the web composer and the room, the `/voice`
  plugin): `process_user_input`'s `may_answer` is closed by default, so a
  `/agent` or `/skill` generation prompt never answers, and `MessageHandler`
  opens it only for those sources. `kollab --hub msg` and the Telegram and
  email bridges are hub messages, so their "yes" never answers either; to an
  ACP agent `kollab --hub msg` goes in as `hub_message`, outside
  `YES_SOURCES`. The goal driver's steering is a HUD context item, and ACP
  steering goes to the ACP agent. A hidden voice turn (the greeting) asks for
  nothing: a call that needs the OK comes back refused, never "Not run yet",
  and its words never answer. Asks come only from the voice guard's decision and the permission
  manager's (`PermissionDecision.needs_yes`), never from a tool's text, and one
  is keyed by the tool's own name and the exact command, quotes kept.
- The yes runs the call as the guard plans it again, approved
  (`VoiceToolExecutionGuard.evaluate(approved=True)`): argv with no shell, the
  executable found on PATH but never in the project or under a directory others
  can write. Hard blocks and unsafe syntax stay refused, and a yes never runs
  them: `sudo`, `rm -r`/`-f`, interpreters and wrappers (`sh`, `bash`, `env`,
  `xargs`, `timeout`, `nice`, `nohup`, `python*`, `node`, `perl`, `ruby`,
  `osascript`, `awk`, `npx`, ...), `find -exec`/`-delete`, git's config
  options (`-c`, `--config-env`, `--exec-path`), git sent outside the project
  (`-C`, `--git-dir`, `--work-tree`; the cwd must stay inside it too), and any
  git subcommand outside `_VOICE_GIT_COMMANDS` (so no push, reset, stash, pull
  or config, and no alias or `git-*` helper).
- A yes runs only what is on the voice list (`_voice_may_run`), since the user
  heard only the agent's words for the call: read-only commands, git, file
  changes inside the project and outside `.git` (`mkdir` `-p`/`-v`, `touch`,
  `cp` `-R`/`-r`/`-p`/`-n`/`-v`, `mv` `-n`/`-v`, `rm` `-d`/`-v`, no other
  option), the project's own scripts (`make` with targets only, `-C` and `-f`
  inside the project, never `VAR=value` or another option; `npm run <script>`,
  `test`, `start`, `install` and `ci` with no other argument, so never a package
  or `npm exec`), read-only `gh` (`--version`, `status`, `pr list/view/status/checks/diff`,
  `issue list/view/status`, `repo view`, `run list/view`) and `kollab --hub`
  `status`/`capture`/`where`/`help`. Anything else comes back `not_by_voice`
  before it is asked for: the agent says it cannot run that by voice and offers
  to run it from a typed message, where the user sees the exact command. So no
  tool that runs code from its arguments (`pip`, `ex`/`vi`, `sqlite3`, `sed`,
  `tar`, `find`, `curl`, `ssh`, `gh api`, `kollab --hub msg`...); the `hub_msg`
  tool still asks. Marco chose `make` and `npm` (2026-10-24): they run any code
  the project's own scripts contain. A tool other than the terminal and the hub
  tools (`file_create`, `file_edit`, MCP...) comes back `command_blocked` with
  the same list: real models reach for `file_create` first, and without it the
  agent said it could not make files by voice, when the terminal's `touch` does
  on a yes.

A typed turn keeps its card, and a Stop or a wait no longer refuses the tool:

- A Stop while the card waits ends the turn and the card stays open: it
  "parks" (`PermissionManager.park_confirmations`). So does a card nobody
  answers for 290 s (`PARK_AFTER_SECONDS`, under the permission hook's 5 min):
  the tool comes back "Not run yet", the agent writes that it needs the OK, and
  the card stays. A prompt asked while its turn is stopping shows as a card and
  parks at once.
- Allow on a parked card runs that call, and the agent goes on: the card's
  answer approves it once (`YesApprovals.answered_on_card`), an agent HUD note
  names it and `TRIGGER_LLM_CONTINUE` starts the turn that runs it. Deny on it
  drops the ask and the agent stays quiet; the web answer's stream ends with no
  turn (the resolution event carries `parked`).
- The user's typed yes runs a parked call like a spoken one, and closes its
  card approved; a typed no closes it denied. A new chat or `/resume` closes
  the old one's cards.
- Terminals never keep a parked prompt: its keys would answer it in the middle
  of the next message. The daemon publishes `permission_parked` and attached
  terminals close theirs without answering; a `--no-daemon` terminal closes its
  own. The card (web UI) stays open, and the typed yes works everywhere.
- The engine marks a parked prompt (`permission_parked` sets the stored
  prompt's `details.parked`), so a page that loads the chat knows its turn
  ended. A page whose own run showed the card reloads whole when it parks
  (`promptParkedSinceShown`): that run ended at the card, so only a reload shows
  the call's "Asked for your OK" and the agent's ask. A message that answers a
  card an earlier run showed (a typed yes or no) closes that card at once.

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
- [x] No orphaned worker or server after stop (process-group kill, tests), or
      after the Engine dies without stopping them (crash, SIGKILL): the
      watchdog SIGKILLs livekit-server (on SIGTERM it drains, waiting for rooms
      only the Engine could end) and the worker stops when it is reparented
      (`Worker._stop_when_orphaned`). Live: none left 8 s after a SIGKILL. A
      clean Engine shutdown SIGKILLs the server too, for the same reason.
- [ ] PhoneLink voice.
- [x] Before/after latency (#301, "Measurement").
