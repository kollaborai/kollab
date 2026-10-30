# Changelog

All notable changes to Kollab will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Windows: the attached window, the hub client, the MCP hub calls, presence
  checks and Deep Thought's child streams connect over local IPC instead of Unix
  sockets, and a Windows CI job runs their tests (#268).
- Windows: the engine behind the web UI and the desktop app opens a chat. It
  starts the session's agent in its own process group, connects to it over
  local IPC and checks that it runs without sending it Ctrl+C. Closing the chat
  asks the agent to shut down, then ends anything it left running. A Windows CI
  job opens a session, gets its state and closes it, leaving no process behind
  (#268).
- Windows: `kollab --detached` starts an agent with no fork, and the hub's
  agent sockets run over local IPC there, so `kollab --hub status` shows the
  agent online and `kollab --hub stop` stops it. A Windows CI job runs this
  agent smoke on pull requests that change the code, and imports every module
  whose optional packages are installed (#268).
- Windows groundwork: `kollabor_config` gains `private_fs`, `proc_ctl` and
  `local_ipc`, cross-platform helpers for owner-only files, process control and
  local sockets. On Windows they use DACL checks, Job Objects and loopback TCP with
  an HMAC handshake. Nothing uses them yet; call sites move in later changes (#268).
- Phone app link screen: the Kollab phone app (iOS and Android) opens on a link
  screen, where Scan QR Code reads the code from Settings → PhoneLink on
  your computer and Paste Link Instead takes the copied link. It opens only a
  fresh PhoneLink invite and remembers the directory, so later launches go
  straight to your computer. Inside the app, the PhoneLink error screen adds
  Scan QR Code to link again (#369).
- Voice Prompt settings: Settings → Voice → Voice Prompt (and `/voice config`
  in the terminal) edits the instructions added to every spoken reply, and
  picks a Personality (Max Gen Z, Neutral or Professional) and a Reasoning
  level (Off or Low). Every agent without a voice prompt of its own speaks
  with these; clearing Instructions goes back to the default. Settings panels
  gain a multi-line `textarea` field (#295).
- `KOLLAB_WEBUI_NO_BROWSER=1` starts the web UI without opening a browser tab.
  The desktop app sets it in place of `BROWSER=true`, which does nothing on
  Windows (#268).
- Voice: words said while the agent is working get a spoken answer within
  2.5 s from the Quick Model, read from what the agent's tools are doing (a
  tool's name and how long it has run, never its command or paths), and the
  running turn keeps going. Asking it to do something says one line at once
  and queues the words for their own turn. With no Quick Model, or none that
  answers in time, the newest tool's state is said instead (#388).
- Voice: a reply that goes quiet for 3 s while the agent works hands the floor
  back. The turn keeps running, and what it says next is spoken once nobody
  is talking. Words said in that quiet get the Quick Model's answer and never
  stop the work. Talking over the agent's voice still stops its turn, and
  "stop" or "cancel" stops it with no model call (#388).
- Voice Quick Model: Settings → Voice → Quick Model (`/voice config` in the
  terminal) picks the model that answers out loud from any configured
  provider's models — the `/llm` list, so a fast OpenRouter model works while
  the chat stays on its own provider, using that provider's connection. Same
  as Chat keeps the chat's model; typed messages always use the chat's model
  (#260).
- Network kill switch: `kollab --hub halt` stops every agent on every device
  of your network; resume and network-wide revoke need two machines that count
  (`kollab --hub resume --confirm`). Also `/connect halt|resume` and Settings →
  Network → Halt and Resume. Agents and skills are never touched
  (`docs/features/network-kill-switch.md`, #349).
- Web UI Settings: long forms (Configuration, Voice) are a split view on wide
  screens, the section list and search on the left and the picked section on
  the right; the desktop dialog has a liquid-metal border and the Prompts
  tab's panes are rounded with margins (#371).
- Gems: births draw from six friendly eye styles (pill, kawaii and the new
  gleam, bean, smile and drowsy; the other nine stay pickable), plus 10 new
  hats (sailor, chef, cowboy, bucket, boater, graduate, aviator, flower, halo,
  propeller), 10 hair styles and four fur lengths; the curly styles coil into
  locks. A browser that cannot draw gems (no WebGL) shows each gem's saved
  headshot, a PNG taken by a browser that can, instead of a plain blob (#380).

### Changed
- Gems are born furry 85% of the time (hair 10%, bare 5%), the same on every
  device, and fur color is drawn too. Gems recorded bare are dressed once at
  the new odds; your own picks never change (#373).

### Security
- PhoneLink: a phone's link is sealed when the browser can save it (WebCrypto
  Ed25519 and X25519, and IndexedDB): its private keys become non-extractable
  WebCrypto keys and its seed leaves browser storage, so script on the page
  can no longer copy them out. A link saved with its seed is sealed at the
  first launch that can save it, with no new pairing; until then, and in
  other browsers, the seed stays, as before (#368).
- PhoneLink: an approved phone reaches only the engine roots its app calls;
  `models`, which no route served, is gone from the list. The phone guide
  lists what a phone can reach and the parts only the computer has (#368).
- PhoneLink: the phone app's version is now the release number, so an older
  build with fresh file times no longer passes the phone's downgrade check;
  expired, unapproved invitations are pruned from the saved phones instead
  of counting toward the eight-phone limit. A phone that loaded this release
  refuses an older one, so roll back by upgrading, not downgrading (#368).
- Hub capture (`/hub capture`, `kollab --hub capture`, the `hub_capture` tool)
  redacts private keys, `NAME=secret` lines and auth headers; only `--raw`
  shows them. The `hub_capture` tool asks permission (#340).
- Capture works across devices: `agent@device` on one of this network's
  devices is captured through the network attach, as that device's trust
  allows. A contact's agents (a knock) are never capturable.
  `plugins.hub.disallow_cross_device_capture` (off by default) turns
  cross-device capture off on this device (#340).
- A capture is a read: the device running the captured agent no longer posts
  "opened ... from the network" and "closed ..." lines for it (it logs the
  capture instead). Opening an agent as a chat still posts them (#340).

### Fixed
- Windows: stdio MCP servers start when their command is a `.cmd` launcher such as
  `npx`, and backslashes in a Windows path in an MCP command are kept (#428).
- Settings → Voice: the path under **Save for This Device** names the file each
  changed row is saved to, so Quick Model, Personality, Reasoning and
  Instructions no longer show `preferences.json` (#412).
- Voice: a voice model that runs out of memory now shows Low On Resources with the reason, instead of a general error, refs #295
- Goals: `/resume` keeps a conversation's goal. The resumed chat used to get a new conversation id, so `/goal` answered "no active goal" and the goal was never driven or paused again, refs #400
- Goals: a goal whose last turn was cut off by an agent that stopped mid-turn is paused with the reason, instead of staying active with nothing driving it; `/goal resume` continues it, refs #397
- Context compaction: a reply that hit the length limit and was continued no longer counts its prompt twice. The doubled count could trip the hard cap and cut a long reply to 8,000 characters, refs #396
- Voice: a spoken yes can no longer run a recursive or forced `git rm` in any spelling, abbreviations such as `--f` included (`--cached` before any `--` still works), or `kollab --hub halt`, and `gh -R <repo> pr merge`, `kollab --hub=stop` and `--restart=...` are refused like their plain forms, refs #295
- Goals: an active `/goal` whose loop stopped (the provider was down, or a `/resume` into its conversation) is picked up again within a minute once no turn is running, refs #389
- Context compaction: a round keeps fewer recent messages verbatim, down to 2, when they would fill more than a quarter of the compaction threshold, so a few large messages or tool results are summarized instead of kept, refs #390
- Context compaction: an agent over its context budget now compacts once it has
  older messages to summarize, even before 6 human turns, when the provider
  reports its token count, so a hub agent that works from one brief no longer
  rides over budget until the hard cap (#390).
- Web UI: a message that reaches a busy Claude Code (ACP) agent shows in its
  chat when it arrives, as it does for other agents, instead of only after
  the running turn ends; the agent still reads the batch once the turn is
  done (#266).
- PhoneLink: a phone diagnostic the computer can never accept is answered
  instead of failed, so the phone drops it rather than resending it ahead of
  every later connection report; revoke and an unknown-phone revoke are tested
  with their actor and cause (#366).
- Save paths under the Settings save buttons no longer break mid-word: the
  home folder reads as `~` and a long path shows its last folders, with the
  full path on hover (#371).
- Web UI help is a hover, not a link: the "Learn about profiles", "Learn more
  about workspaces" and PhoneLink "How It Works" links are gone. Hovering
  Manage Profiles, the workspace picker's Use button or PhoneLink's connection
  line shows a small info icon, and after a moment a card says what it does.
  On a touch screen the icon is always shown and a tap on it opens the card.
  The cards open below their control so they never cover the text they explain.
  The sidebar's Learn button still opens the Learn topics.
- Voice in the web UI with no speech model yet no longer says to look in
  Settings: it says "Voice needs a speech model on this computer." as a plain
  notice, with an "Install speech model (2.5 GB, one time)" button that selects
  Parakeet v3 for this device and starts voice again, which downloads it. On a
  Mac without Apple silicon it says voice needs an Apple silicon Mac.
- With an ACP profile, input from agent and skill generation and other
  internal callers goes through ACP admission instead of the native queue; an
  unavailable ACP is refused. Agent and skill generation, which need the
  native tools, say so and keep the form open (#340).

## [0.18.5] - 2026-10-26

### Fixed
- A Web UI voice room whose page was closed now ends after a minute without
  it: its worker stops and gives the voice service back. A reload within that
  minute rejoins the same room (#336).
- A voice start replaces an idle voice service that still runs an older
  Kollab's code, so a release that changes voice takes effect without
  stopping the service by hand. `/voice status` shows the service's code
  (#336).
- The Engine asks its own LiveKit server who is in a room directly, never
  through `HTTP_PROXY` or the system proxy, which could keep a closed room
  alive or end one still in use (#336).

## [0.18.4] - 2026-10-26

### Fixed
- Voice in the Web UI connects after the chat's agent restarts. It stayed on
  "Starting" until the Web UI was restarted: an earlier voice room still held
  the voice service, which then reported itself as starting, so the new room
  waited for 20 minutes and gave up (#333).

## [0.18.3] - 2026-10-26

### Fixed
- Voice in Trust All runs tools as a typed turn does. It asked for a spoken yes
  before each command and refused anything off the voice list, though Trust All
  means no approvals (#331).
- Voice: a yes in your own words counts, such as "Yes, run it." or "Okay, you
  don't have to ask, just do it". Only a bare "yes" did, so the agent kept asking.
  "Thanks" is still no yes, and a yes with a new request is a normal turn (#331).
- Voice: a recursive `grep` inside the project (`grep -rn`, `--include=`) runs
  as a read-only command instead of being refused (#331).

## [0.18.2] - 2026-10-25

### Fixed
- Kollab.app opened from the Dock or Finder runs with your login shell's PATH.
  Voice in the Web UI said `livekit-server` was not installed when Homebrew had
  it, and agents' shell commands missed your tools (#329).

## [0.18.1] - 2026-10-25

### Changed
- Voice: Parakeet v3 is the default transcription engine on Apple silicon Macs,
  since voice in the Web UI transcribes only with it. Whisper Base stays the
  default elsewhere and transcribes while Parakeet downloads (2.5 GB, once). A
  saved choice is kept.

### Fixed
- Voice: turning voice on in the Web UI now sets voice up when it is not installed
  yet, as `/voice on` does in the terminal. The first start installs voice support
  and starts the voice service, and the voice bar shows each step. It used to stop
  with "Voice support is not set up yet", pointing to a Settings control that did
  not exist. A voice service left running by an older Kollab is replaced unless a
  chat is using it, Stop works while voice starts, and Settings > Voice opens on
  whether voice is installed.
- Voice: when voice cannot start in the Web UI, the voice bar gives the reason,
  such as Whisper being the chosen transcription engine, instead of "Voice worker
  exited before joining."

## [0.18.0] - 2026-10-25

### Added
- Web: PhoneLink has its own Settings tab, expiring QR invitations, local phone
  approval and per-phone revocation over the existing `/connect` transport.
  The directory now includes a same-origin browser portal; its deployment is
  checked before the app offers a QR.
- Web: Learn opens as a compact modal with ten reference topics, plain navigation, and an X to close. What's New has its own page and sidebar action.
- Engine: Web UI-created agents survive engine and Web UI restarts, restoring the same conversation; use `/quit` or Web UI Delete to stop them. A Delete that stops an agent but cannot update its saved record keeps the agent listed with a retry hint instead of vanishing or stopping it twice; unreadable approval mode reports as unknown with a warning instead of guessing, and both the session panel and the delete error say what to do.
- Profiles can be copied with `/model copy` or from the Web UI; ACP command and runtime settings persist. Profile cards show provider and a sanitized endpoint or ACP adapter.
- Invalid profiles are reported individually without blocking valid profiles; missing active/default selections warn at startup.
- Docs: add a product-ready checklist and PR template requiring evidence or a
  reasoned N/A for every change.
- Web: Profiles has a Reasoning Effort picker; effort appears in each row and
  Model Default clears it. Unsupported values are rejected before a copied
  profile is saved. Copy uses the
  stored source profile server-side, preserving saved credentials and hidden
  settings (including ACP runtime fields). Environment-backed keys are not
  copied; set the target profile's key variable or `KOLLAB_API_KEY` before
  copying. System-keyring references remain shared. OAuth and provisioned
  profiles cannot be copied; the form explains these rules before Create.
- Web: Add a System Prompts settings tab to browse prompt bundles, edit per-user
  section overrides, and preview the assembled prompt.
- Web: Confirm before removing an override and show progress while saving or
  removing it.
- Web: Sidebar agents are grouped by workspace and can be manually ordered within local and remote workspace groups.
- PhoneLink: the connection service no longer supplies the phone app. A small
  loader fetches it from your computer over the encrypted session and runs it
  only after the computer's signature on the app files checks out against the
  host key in the QR; a tampered or mismatched app is refused. Both screens show
  the computer's fingerprint beside a longer 12-character pairing code, each
  phone shows when it was last seen, and Revoke removes a phone in one tap.
  The loader is installable to the Home Screen and opens its verified copy.
- Build: `kollab --version` shows the source branch and short SHA before the
  version line. The Web UI and signed PhoneLink app show the same identity;
  Python wheels and binaries embed the full SHA.
- Web: PhoneLink shows Open on This Phone and Copy Link under the QR code, so a phone can link itself with the same invitation (five minutes, still approved on the computer).
- Hub: the idle supervisor now proves its wakes work. Wakes that produce no
  completed coordinator turn are counted; once enough land while the
  coordinator is stalled, an alert names the blocker (including the last turn
  error) in `/hub status`, the log, attached web clients, and the configured
  notifier channel. Undelivered worker nudges surface there too instead of
  vanishing. Spec: `docs/specs/hub-idle-supervisor.md`.
- Hub: a task handed to an agent with a task id stays on the ledger as
  "handoff awaiting ack" in `/hub status` until the agent acknowledges it;
  unacknowledged after two minutes it is resent once, then alerted. Failed
  sends are listed in `/hub status`, and a message that fell back to the
  mailbox reports "queued for" instead of "delivered to".
- Hub: email wake. New mail in one IMAP inbox becomes a direct hub message
  that wakes the configured agent (read-only, IDLE). Guide:
  `docs/guides/email-wake-setup.md`.
- Web: hub supervisor alerts show as a dismissible banner above the chat.

### Changed
- Hub: Dream Model selects a provider-supported model for dreaming. A blank
  override uses the shared fast model, then the active session model.
- Hub capture commands show a bounded readable transcript by default; `--raw`
  keeps the previous terminal output.
- Voice: the voice package no longer imports `kollabor_ai`, which its Python 3.12 runtime cannot
  install. The host-side helper that shows stored voice turns as screens draw them
  (`turn_display_texts`) now lives in `kollabor_ai.response_channels`, and a voice-package
  test fails if a service module imports a host package. Fixes #301.
- Web: PhoneLink starts with the newest 50 conversation messages, caches bounded
  history pages locally, loads older messages on scroll, and fetches full tool
  output only after Show more. Negotiated compressed PhoneLink frames carry the
  150 KB first-page budget within the three-round-trip synthetic relay check.
- Web: Sidebar agents use smaller avatars, clearer names and status text, consistent group spacing, and neutral selection colors shared with the rest of the app.

### Fixed
- Web: disconnecting or revoking a phone closes buffered assistant streams without leaving event subscriptions running. Explicit Stop still cancels the agent turn.
- Build: one selected frontend is reused by CLI, desktop, service and Python packages. Invalid selections and failed source builds stop instead of silently serving older UI.
- Network: attached clients show the owning connection's Knocks address.
- ACP profile switching now uses the ACP harness instead of native provider construction. Missing native providers raise a profile-specific actionable error.
- Environment-backed API keys are not copied into config; configure the target profile's environment variable first. Keyring references remain shared.
- Profile API responses, state snapshots, and CLI summaries omit URL userinfo/query/fragment, credential-like path segments, header values, ACP command arguments, and local runtime paths; unchanged redacted endpoint edits preserve server-side configuration.
- Updating a model-less ACP profile to a native provider now requires a native model; invalid combinations return an actionable validation error without mutating the profile.
- Web: a long profile name or model no longer widens the Profiles dialog past a phone screen and hides its buttons.
- Web: Sidebar order is engine-persisted and revision-checked across clients. Unreadable order data is preserved with a recovery warning; a missing first-run file is normal.
- Web: agent guidance covers sidebar workspace grouping and drag ordering. Learn no longer tracks completion or shows a progress counter. The Voice topic documents the Hub Dream Model control: it sets a cheaper model for background agent dreaming only and does not change voice replies.
- Web: the main pane shares the sidebar's rounded inset frame; footer actions stay on one row or form two balanced rows. Wrapped changelog entries retain their full text.
- Engine: model-requested context compaction stages the new history before
  resuming the same turn, rather than ending before the follow-up response.
- Bound automatic compaction to recent live hub tasks, expose its pause/recovery state in the TUI and Web UI (terminal states are re-announced at the start of the next turn so a banner is never missed), add a Learn topic, and use OAuth model context-window metadata when available instead of an unrelated generic model limit.
- Web: the open chat shows messages from other agents or windows while a turn
  is running and re-syncs after every reconnect; a tool card shown mid-turn
  picks up its result when the turn finishes.
- Web: a stopped turn no longer leaves the agent's gem acting out a dead tool
  call.
- Web: a window watching an ACP agent's turn that started elsewhere streams its
  reply and tools live, keeps a stopped reply on screen until the stored one
  replaces it, and streams a turn queued right behind it. Prose the agent sends
  after a cancel is neither shown nor stored. The reply shows no stray "2 / 2"
  branch picker once it is stored, and it no longer blanks out and retypes, or
  shows twice, when rows land before it.
- Hub: the Telegram bridge wakes its target agent, and disconnecting the email
  bridge no longer waits out an IMAP IDLE.
- Context-compaction emergency recovery prioritizes the latest human request,
  excludes non-intended, group, and other-agent hub notices plus standalone HUD
  and stale assignments, preserves direct-to-agent requests without TaskLedger
  IDs, and caps the wrapped recovery message at 8,000 characters.
- Web: PhoneLink QR codes scale to fit their panel without cutting off the corner markers, so phone cameras can recognize the complete code.
- Voice: a spoken reply no longer pauses about a second between sentences; each
  sentence's silent lead-in is cut to a short breath.
- Chat: a reply you stop keeps the text shown so far, also when you stop it
  while its tools run.
- Voice: talking over a reply keeps only the part that was played, marked
  interrupted, and the voice room stays up while the chat reloads to show it.
  A reply that ran tools is cut where you stopped it and the unheard rest is
  dropped, a reply you talked over before it was heard goes, the saved
  conversation keeps the cut, and a typed reply is never touched.
- Voice: talking over a reply while its tool waits on your approval or runs a
  long command no longer stalls the agent's connection: the stop, the approval
  answer and the next turn all still get through.
- Chat: a resumed conversation sends a saved reply that only ran tools as
  nothing, not as an empty message, which the Anthropic API rejects.
- Voice: a reply after a round of hub messages alone is spoken again; with a
  provider that does not stream it stayed silent.
- Voice: every reply speaks its last sentence, which was dropped, so a short
  reply such as the answer after a talk-over is heard. The latency line keeps
  Hear, Think and Speak when the reply's Total arrives.
- Voice: while the voice room is on, the chat follows the conversation to the
  latest turn. The greeting shows as a message of its own instead of running
  into the reply before it, and the latency line wraps rather than being cut
  off on a phone or a narrow window.

### Removed
- ACP: the retired Gemini CLI is gone: the built-in `gemini-cli` profile and
  its Apps catalog entry are removed.

### Security
- Web UI: its API and engine routes answer only clients on the private
  subnets of the interfaces it binds, from the same origin; a request with an
  unknown Host or any forwarded-client header is refused.

## [0.17.0] - 2026-10-19

### Added
- Web: Settings has a Versions section: the kollab, engine, web UI server and agent bundle versions this computer runs.
- Web: Random Look in the Gem Studio rolls hair or fur too, at the odds a gem is born with them.
- Voice: Fine-Tuned Qwen3 0.6B is a second local classifier on Apple silicon Macs. Pick it under Classifier in `/voice config` or with `/voice classifier qwen`; Laya stays the default and nothing changes until you do. One model decides whether speech is for the agent, what your reply to "Sorry, was that for me?" means and whether a reply reads as natural speech. On a day of real household speech it decided 70 lines right, 18 wrong and asked about 13, against Laya's 45, 20 and 36, read 59 of 60 synthetic replies to the question right (word rules: 44) and 11 of 12 real ones, and peaked at 1.75 GB of memory against Laya's 3.87 GB. Its mistakes are mostly the TV or the room waking it, where Laya's are mostly misses. It runs in its own process from 645 MB of weights checked against a pinned SHA-256; they have no download source yet, so selecting it names the folder they belong in.
- Voice: Parakeet v3 is a second transcription engine on Apple silicon Macs. Pick it under Transcription Engine in `/voice config`; Whisper Base stays the default. It downloads 2.5 GB once, checked against a pinned SHA-256, and runs on the GPU in its own process: on an M2 Max it transcribed an utterance in a median 69 ms against Whisper Base's 562 ms. Whisper transcribes while Parakeet prepares and whenever it stops, and `/voice status` names the engine that is live. Parakeet cannot be told the agent names, so "Kollab" comes out as "Colab".
- Voice: the lines voice says itself (acknowledgements, "Sorry, was that for me?", still-working lines) are kept on disk in `~/.kollab/voice/cache/speech/`, so a line it has said before plays at once instead of being synthesized again. A file is deleted three days after it last played; agent replies are never kept.
- Voice: two filters in front of transcription, both off until you turn them on under Echo and Speaker in `/voice config`. Echo Cancellation removes voice's own speech from the microphone with WebRTC's echo canceller, fed what the speaker plays: in a simulated room voice's words vanished from the transcript, but talking over voice is heard less reliably. Speaker Gate passes only your voice to the classifier: `/voice enroll` saves a voiceprint from 30 seconds of your speech (`/voice enroll forget` deletes it), and other voices stay in the transcript as context and never reach the agent. On recorded speech it rejected 1.6% of a speaker's own lines and let through 0.5% of other people's and 0.01% of a TV film's. The speaker model (3D-Speaker ERes2Net, 26.5 MB) downloads once, checked against a pinned SHA-256. The voice runtime gains two small packages, so the first `/voice on` after updating prepares it again.

### Changed
- Kollab is licensed under Apache 2.0 (was MIT).
- Claude Code and other ACP agents speak their replies to mic turns, like a native agent.
- ACP: the model or effort you pick for a harness is kept for its profile and set again whenever the harness starts. The status bar shows the harness's own model.
- Gems born before hair existed get one hair or fur roll; a gem born bare stays bare for good.
- Terminal: assistant replies start with ◆.
- Kollab needs Python 3.14 or later. CI no longer tests 3.12. The voice service keeps its own Python 3.12 or 3.13 runtime for its speech models.
- Web: Session Controls is a panel at the chat's right edge, open by default, instead of a popover. It holds the session's name, the Chat and Trajectory views and every control that sat in the chat header, so on a computer the chat has no header bar and the sidebar no hide button. Hide the panel to a slim rail; the browser remembers. On a phone the Session button opens it as a sheet.
- Web: the sidebar's footer (Hidden Agents, Apps, Agents, Gem Studio, Settings) is one row of icons.
- Web: the sidebar and Session Controls float as rounded cards with a margin around them, and Session Controls ends at its last control instead of running to the bottom of the window. The sidebar's edge shows a line on hover: drag it, or focus it and use the arrow keys, to resize the sidebar.
- Voice: a mic check ("can you hear me?"), thanks or a bare name is answered by voice at once and still reaches the agent with that answer, so the agent can add more or hold off with a lone period. No still-working lines follow a turn voice already answered.
- Voice: after "Sorry, was that for me?", a no stops these questions for two minutes (was ten) and no answer stops them for 30 seconds (was two minutes).
- Voice: a spoken reply is no longer rewritten for its length. One or two sentences are preferred, but a longer reply is still spoken, and a reply still unsuitable after its one rewrite is spoken with code, links and paths taken out instead of skipped.
- Voice: the classifier reads the chat's last three exchanges (was the last two messages) and drops the oldest text first when that runs over Laya's context. Whisper is told the agent's names, so it writes "Aquamarine" instead of a near miss.
- Voice: speech more than 30 seconds old by the time voice can decide is kept as context and never sent; `/voice retry` no longer sends it.

### Fixed
- Web: a chat reconnects on its own when its agent's socket restarts, instead of failing every save with a 502.
- Hub: the startup socket sweep no longer deletes a socket a live agent still answers on, so a test or tmux spec with its own HOME cannot cut off running agents.
- ACP: tool calls show as tool rows, not "completed" info lines, and the voice rules sent with a mic turn no longer show as your message.
- ACP: a chat with the hub off (pipe mode, `KOLLAB_NO_HUB`) starts its harness instead of failing with "Hub startup was not scheduled".
- Web: messages between agents were drawn in larger type than the chat's own messages on a computer.
- Voice: every voice turn left the previous input box and status rows on the terminal screen, and the next message printed under them.
- Voice recovers on its own instead of waiting for `/voice retry`. When the voice service dies, its lease expires, setup fails or capture stops, the chat starts voice again after 5 seconds, waiting longer each time up to two minutes; the status row reads `starting` until the microphone is live. A late answer from a busy voice service no longer turns voice off, and no message tells you to run `/voice retry`.
- Voice: a VibeVoice worker that timed out or exited left every later reply silent. The speech engine is prepared again on the next sentence.
- Voice: talking over voice's own speech merged both into one line. The words voice was saying are cut, and only yours are judged and sent.
- Voice: replies no longer go quiet between sentences. VibeVoice streams audio as it generates, and the next sentence is synthesized while one plays.
- Voice: packaged builds (wheel, release binary, Kollab.app) shipped without Laya's classifier heads, so `/voice` stopped with "Laya setup failed".
- Voice: stopping the microphone no longer deadlocks CoreAudio, a recorder start or stop that hangs restarts the voice service instead of freezing every later `/voice`, and a slow first bind right after the daemon starts no longer turns voice off.
- Voice: `/voice on` right after a session starts no longer stops with "voice.bind timed out after 2s". The first bind gets more time and retries, and a failed bind keeps voice running on this computer. A stuck microphone start or stop no longer wedges the voice service, and a stop now says why.
- Two sessions started in the same minute could draw the same id, and resuming or branching one then overwrote the other's saved transcript. Session ids now end in a short random token (2610180756-quantum-spark-7k2x); the status bar, /resume, the conversation browsers and the web UI still show only the two words.

## [0.16.8] - 2026-10-18

### Added
- Web: gems can grow hair or fur. Properties and the Gem Studio have a Hair tab: Short, Long, Spiky or Mohawk hair on the head and Short or Shaggy fur all over, alone or together, each in the gem's own color, a natural shade or any color you pick. They are thousands of 3D strands that sway as the gem moves and still read as a coat in the sidebar. Every knob of a style (length, how flat it lies, fall, clump, sway, bangs, hairline, sheen and the rest) can be set for each gem. A hat sits snug on the head over the hair: one that covers the crown presses it flat and the rest hangs from under the band. Some gems are born with hair or fur, and a Person shape gives hair a head to grow on.
- Web: an Aura tab dresses a gem in a glow, flames, orbiting orbs and sparkles, mixed as you like, in one color you pick. Auras are quiet while the agent idles, stronger while it works and gone while it is offline. Sparkles moved here, and can now be turned off or up.

### Fixed
- Web: a shape picked in the Gem Studio shows on its gem right away.

## [0.16.7] - 2026-10-18

### Added
- Web: every agent's Properties has an Avatar tab: keep what its roster drew, or make it a gem, its initials, an emoji or a picture you upload (cropped square and shrunk in the browser). Roster personas such as Omar Haddad can now be dressed like gems: pick Gem and the Shape, Eyes, Hat and Color tabs appear. The pick shows wherever the agent does, in every browser.
- Web: a Shape tab picks a gem's body (Brilliant, Cushion, Heart, Hex, Cloud and ten more) over the one its name draws, in Properties and the Gem Studio. Reset puts the shape, avatar and colors back.

### Fixed
- Kollab.app: its web UI and engine no longer load packages from `~/.local` (the user site), so a `pip install --user` of another kollab can't replace the app's own code, and the agent shells it starts don't inherit the setting. (#232)

## [0.16.6] - 2026-10-18

### Added
- Rosters and teams: an agent's name is no longer tied to gems. A roster is a set of personas (a name, a picture, an agent bundle, a role, a prompt, skills) and a team puts personas in a reporting chain; both are plain JSON in `bundles/`, `~/.kollab/` or a project's `.kollab/`. `kollab --as maya` starts a persona from any roster (`roster/name` when two share a name), `plugins.hub.roster` (the Roster dropdown in `/config`) picks the roster a folder draws its names from, and `kollab --hub org marketing` or `/hub org marketing` launches a team. A persona's skills attach on every launch path, and the engine serves it all at `GET /rosters`, `GET /teams` and `POST /teams/<name>/launch`. See `docs/features/rosters-and-teams.md`.
- Web: New Session's Agent field lists the folder's roster first, then every other roster's personas, each with its picture and job title, and fills in its Type (the field called Agent before, now Type here and in Session settings). The sidebar's Agents button shows every roster and team, with the files that failed to load, and launches a team into a folder. Rows, the chat header, Who Is Online and Session settings call a persona by its display name, an offline row keeps its persona's picture, and a persona's Properties show that picture with the Hub switch and no gem to dress.
- Hub: `plugins.hub.arrival_wake` says who wakes when an agent comes online: `all` (default), `koordinator` (only the folder's coordinator) or `none`. Set it with Wake on Agent Arrival (All, Koordinator or None) in `/config` or the web Settings; saved to a project, it applies to that folder only and overrides the global file.

### Fixed
- Web: hiding an agent in the sidebar hides it in Kollab.app, every browser and the phone, not only the window you clicked in. The engine keeps the list. An agent you launch later under the same name shows even when an older one in that folder was hidden. Agents hidden earlier in one browser are shown again.
- Web: an agent is in use only in the folder it runs in. New Session in a project starts a koordinator while another one runs in the home folder, and lists what the chosen folder can start; starting an agent that already runs in that folder says where it runs. With project-scoped hubs turned off, every folder shares one hub and one set of names.
- Web: an agent started from the terminal and opened in the web UI no longer shows its system prompt as the first chat message, and its sidebar row reads model and folder like one the web UI started. Agents publish their running model in presence and keep it current on every heartbeat.
- Web: a chat started with Workspace left on Default reports the folder its agent runs in, so its row reads model and folder like the rest.
- Web: a sidebar row's working state is its own agent's. A koordinator busy in one folder no longer shows the koordinators of other folders as working.
- Web: opening a running agent shows its saved conversation at once and switches to the live one as soon as the agent answers, and a window that opens it while it answers shows the message being answered, whichever window or terminal sent it. Before, the chat could stay on an older copy until the answer landed.
- Web: New Session's Type list is the chosen folder's, so a project's own agents are offered there and not in folders that cannot start them. Agent types read in Title Case (Coder, Creative Writer) wherever they show; the engine still gets the id.
- A chat started in a project uses that project's own agents (`.kollab/agents`) wherever the engine runs; Kollab.app's engine runs in the home folder, so a project-local agent answered "not found". On a computer where the terminal app has never run, the web UI starts the built-in agents (coder, research, default and the rest) and teams, and the default prompt, hub protocol and session-context prompt parts fall back to the built-in copies. A folder that holds only `agent.json` no longer hides the built-in agent of that name.
- A chat or team member that names no profile starts on the active profile, and Launch Team in the Agents view uses the Model New Session has chosen. Before, team members started on the auto-detect `default` profile (coder seats always did) and answered "LLM provider not available".
- `kollab --hub org <team>` and `/hub org <team>` no longer freeze the window while a team starts. Each member comes online before the next starts, members already online are skipped and named instead of reported as launched, and a member that never comes online stops the launch and says which one (`kollab --hub org` exits 1); the same command again launches the rest. A web team launch no longer fails a member, about 2 times in 100, when two members draw the same session name.
- The engine, `/hub org` and the agent orchestrators start the kollab they run from, never the first `kollab` on PATH, so a development engine no longer starts an installed release's agents.
- Settings and `/config` show dropdown options by name: Roster reads Gems and Marketing Crew, Wake on Agent Arrival reads All, Koordinator and None; the saved value is still the id. `/config` reopened after a save shows the saved values, not the ones it first opened with. Scrolled settings no longer show between the Settings header and the search box.
- Web: the @ list, Who Is Online and the sidebar name an agent's state the same way (Idle, Working, Online), and a persona's details show the handle people type (@maya).
- The engine's `/prompts` API lists the default prompt once, reads agents from the project, the home folder and the built-in set, and previews them with their Available Agents list instead of "[No agent manager available]". `/agent` marks a built-in agent `B`, and `/skills` shows a built-in agent's default skills.
- The web UI no longer warns about a duplicate operation id when it starts, and agents no longer warn about a missing AltView on every start.

## [0.16.5] - 2026-10-17

### Removed
- The unused Mentiko spawn adapter (`kollabor_agent.mentiko_adapter`). The engine's daemon pool starts agents. (#225)

### Fixed
- The web UI opens a new chat on the agent in its own folder. When the same gem ran in another folder (each project runs its own koordinator), the engine could attach the chat to that folder's agent, and a chat that failed to start could stop it. (#229)
- Errors that were silently ignored are logged: a tool grant, tool revoke or MCP connect notice that cannot reach the agent logs a warning, and process-kill and fallback paths log at debug level. (#226)

## [0.16.4] - 2026-10-17

### Fixed
- `kollab --version` and `kollab --help` name the program `kollab` again. On Python 3.14 they showed `python /path/to/kollab`.

## [0.16.3] - 2026-10-17

### Added
- Hub: work no longer sits idle. The koordinator keeps a durable queue of unassigned tasks that survives restarts, agents claim them first-come, and an idle supervisor wakes idle agents for open work and requeues stalled tasks. (#215)
- Prompt overrides: replace any section of an agent's system prompt with your own file in `~/.kollab/prompt-overrides/<agent>/<section>.md`, and the engine has an API to list, read and edit them. Overrides apply in the real prompt and survive upgrades. (#209)

### Changed
- Kollab runs on Python 3.14. The release binaries and the macOS app carry CPython 3.14.8, CI tests 3.14, and the Docker image is built on 3.14 (3.12 is still the minimum for pip installs). The new Python asks for Keychain access once on each Mac. (#221)
- The system prompt is stable from turn to turn, so providers can cache it. The project tree, AGENTS.md and skill aliases are sent only when they change, and MCP tool summaries come last. (#216)
- `kollab --hub capture` and the `hub_capture` tool return plain text by default; raw terminal output is opt-in. (#208)

### Fixed
- Hub agents keep running when the engine restarts, instead of shutting down with it. (#219)
- A tool loaded mid-session can be called right away, and tools removed from an agent's scope stop being callable when the agent or session changes. (#212)
- `kollab --update` upgrades a user-site pip install (`pip --user`), so the running engine actually gets the new version, and it names any other kollab copies it left untouched. (#218)
- An approval-mode change that cannot reach agents is logged instead of failing silently. (#213)
- The web UI bundle reports the same version as the rest of the release. (#217)

## [0.16.2] - 2026-10-16

### Added
- Desktop: Kollab.app updates itself on macOS. It asks when a newer release is out (and from Check for Updates… in the menu bar), checks the download's SHA-256 and signature, swaps the app in one step and reopens it. The web UI's update banner inside the app hands the update to the app.

### Changed
- Desktop: the title bar blends into the web UI's dark background, so it reads as part of the app while keeping native dragging.
- Web: the gems read as a cast. Each one has its own silhouette, finish and temperament, hats sit on each gem's crown, and faces stay clear at 28px. Idle gems breathe and blink on their own rhythm, each work state has its own pose and prop, transitions ease, and a long turn ends with a short dance: four bounces, a sway, one spin and a jigging hat. Gem Studio's Dance preview loops it, and a booting gem whirls and winds round to face front. A page of many gems stays smooth, and reduced-motion and offline gems hold still.
- Web: Gem Studio, Settings, the chat header and Trajectory use underline tabs and toggles instead of pills.
- Web: no lines anywhere. Sections separate by shade instead of borders (sidebar, headers, Trajectory, dialogs, popovers, the composer palette, code blocks and tables), badges are plain colored labels, and text fields show focus by shade instead of an outline.
- Terminal: Apps moved into `/mcp`. `/mcp browse`, `/mcp search`, `/mcp add` and `/mcp remove` replace `/apps`, and the `/mcp` manager screen lists the Apps catalog's MCP servers, so `a` adds Context7, Linear, Sentry and the rest from there.

### Fixed
- Web: gems in messages and online notices wear the same seasonal costume as everywhere else.
- Web: "came online" and "going offline" notices fold into small chips that stack together, including ones that arrive inside an agent's HUD, instead of full message cards.
- Web: the version-mismatch banner no longer shows twice.
- Web: the update check no longer fails with a server error, and it reports a newer release when there is one.
- `/upgrade` in a binary install no longer fails with "signal only works in main thread", and its download progress no longer prints over the app's screen.
- Web: `kollab --web-ui` in a source checkout rebuilds the web UI when its source changed since the last build, even with the UI already running, and the browser always revalidates the page, so a change no longer looks reverted.
- Web: `kollab --web-ui` no longer attaches to an engine that runs from another kollab install, such as the desktop app's or a test build's. It leaves that engine running and starts its own on the next port.
- A delegation to Claude Code or Codex over ACP no longer echoes back into the sender's chat as "<you> → Koordinator". An agent that takes a peer's message as its input stopped rebroadcasting it to every agent as if you had typed it.

## [0.16.1] - 2026-10-15

### Added
- Web: each agent and session shows a badge for the CLI it runs on (Claude, Codex or Gemini), and the session UI leads with the agent's name. (#188)
- Web: the desktop sidebar is resizable. Drag its edge or use the keyboard, and the browser remembers the width. (#188)
- Web: hide agents from the sidebar. The choice is kept in this browser. (#189)
- Web: Hub and ACP messages between agents render as compact cards with the sender's name and device, instead of looking like your own messages. Online notices fold into small chips you can expand, and an agent's reply shows as "Replied to <agent>". (#189)

### Changed
- Web: the chat header folds its session controls into one menu, the active chat gem docks beside the input, and the README uses the new dark screenshots and product description. (#188, #189)
- Web: when a harness diff already shows an edit, the raw tool arguments are hidden instead of shown twice, ACP context token counts are compact, and ACP peer observations are no longer shown twice. (#188)

### Fixed
- Web: opening a busy agent shows its history right away and never times out. History loads from disk without waiting on the agent, refreshes in the background, and the client open timeout is gone. Signature checks no longer block the event loop. (#183, #190)
- Hub: a remote sender's name and device resolve from the local and remote rosters, and thread ids and reply instructions stay out of the visible message while the model still sees them. (#189)
- Resumed sessions keep their initialization provenance. (#189)

### macOS desktop app
- The Kollab desktop app (`desktop/`, built with Tauri) is not code-signed or notarized yet, so macOS blocks it the first time with a message that it cannot be opened or verified. To open it, right-click (or Control-click) Kollab.app in Finder and choose Open, then choose Open again. Or try to open it once, then go to System Settings > Privacy & Security, scroll to Security, and click Open Anyway next to the Kollab message. You only need to do this once.

## [0.16.0] - 2026-10-14

### Added
- Expose Kollab to ACP clients with `kollab acp`, with owned and attached sessions, shared permissions, tool notifications and cancellation.
- Add native macOS desktop-wrapper source with a window, menu-bar controls, launch at login and owned runtime startup/shutdown.

### Fixed
- ACP harnesses now finish startup before accepting work and preserve provider refusal details instead of reporting a generic unavailable error.
- Hub requests to ACP agents retain authenticated ownership, queue independently and deliver correlated replies and final outcomes to their requester.
- Permission choices retain the vendor's exact option IDs across terminal and web clients. Stop remains available while an owned permission is pending, including after reload.
- Cancellation, timeout and shutdown settle their owned work without consuming another turn's output; attached sessions retain their external runtime.
- Saved conversations retain merged tool cards, file changes, metadata and original timestamps through resume. Harness identity, usage and cost stay visible in the roster and session header.
- Terminal ACP output no longer overwrites active input or diagnostic rows. Long permission choices wrap inside their cards.
- Web gem hats and seasonal effects fit inside avatar rows, and harness costs use the terminal's currency formatting.

### Qualification limits
- Gemini's positive model/tool/permission/cancel checks still require supported authentication. Consumer Google OAuth is not a supported route; no Gemini API key or ready Vertex setup was configured in the checked environment.
- The user-runtime restore retained local conversation data, but Claude vendor IDs rotated; exact vendor-session continuity remains unverified. See `docs/specs/acp-bridge-phase1.md` for the recorded provider and client evidence.

## [0.15.7] - 2026-10-13

### Added
- Apps: /apps marketplace to browse and install apps; bundled skill seed copies are retired on startup (moved, never deleted).
- ACP: phase-one outbound Agent Client Protocol bridge with orchestrator, cancellation lifecycle, and profile integration.
- Voice: selectable VibeVoice speech backend; unified voice settings shared across the terminal and the web UI.

### Changed
- Web: composer input typed during an active turn is queued instead of dropped.
- Hub: capture output is normalized and structured with bounded pagination.

### Fixed
- Opening a busy agent no longer times out: history refresh is bounded, session open has a 2-second timeout, and permission reads no longer wait on history. (#179, #180)



## [0.15.6] - 2026-10-13

### Added
- Web: fold a sidebar group away by clicking its name (This Computer, each network, Contacts). A folded group keeps its count, and the browser remembers what you folded. (#173)

### Fixed
- Web: agents on your other computers fill the sidebar's width again. The network section nested its rows two levels deep, which left each name a few letters wide and clipped the device name and its count. (#173)
- Web: the sidebar shows every network this computer is on. With folders on different networks it asked only the first agent that answered, so the other networks' agents were missing, and the ones it did show could sit under another network's name. Every agent here is asked now, all at once, and each network lists its own computers and keeps its own offline roster; opening an agent on a network no agent here carries starts one in that network's folder first.
- Web: opening a busy agent, or one on another computer, no longer fails with "rpc call 'state.get_permission_state' timed out". Its replies queue behind its live events, so the engine stops asking it on the way in: pending approvals come from the prompts the agent already pushed, and its profile and approval mode are read in the background.

### Changed
- Web: on a mouse-and-keyboard screen the UI is a step denser (90%), the chat text one size smaller, and tool calls start collapsed ("2 tool calls"; click to open). Touch screens keep their size. (#173)

## [0.15.5] - 2026-10-12

### Fixed
- The update check no longer treats a version cache holding the version you are already running as fresh for a full day: a cache that shows the current or an older version is refetched within the hour, so a release you just installed is announced instead of staying hidden until the next day. (#169, #170)
- `kollab --update` now shows download progress (bytes and percent) on stderr, fails loudly after 30 seconds of no data and retries once — resuming from where the server allows, and restarting cleanly when it does not — sweeps stale `.kollab-upgrade-*` temp files older than an hour at startup, and unlinks its in-flight temp file on Ctrl-C or SIGTERM instead of leaving it behind. (#171, #172)


## [0.15.4] - 2026-10-12

### Added
- A device can hold several network memberships at once. Each folder keeps its own membership keys while moving to the new device record, so peer approvals and conversations keep validating without re-joins, and the device record's primary choice owns the managed-config entry: joins never clobber it, and a stale entry is cleared only when no live workspace on the machine still claims it. (#158, #162, #164)
- `/connect status` lists every membership with the primary marked, `/connect join` no longer leaves your current network first, `/connect leave` asks which network when you hold several, and the new `/connect primary <network>` sets the device's choice. (#166)
- The relay runs one membership runtime per folder and network (up to eight), each with its own inbound approval checks. (#162)
- The web UI sidebar groups agents Networks > Devices > Workspaces > Agents across every network the device is on, plus a contacts section built from knocks you accepted. (#163)

### Fixed
- `/connect start-network` no longer dies with a generic "could not submit the join request" when the domain resolves to a non-global address (a DNS or egress proxy like 198.18.0.0/15): the reason travels end to end, `address_denied` names the host, the IP and the config line to change, and the Connect screen offers to approve the range and retry, with a caution to only approve a DNS or proxy you trust. (#155, #159)
- The join code field shows one `*` per character typed and backspace removes one, instead of filling every star on the first keypress. (#159)
- The web UI folds a live network answer into the right network block: an exact-name match now runs before the substring fallback, so an answer for "acme" no longer lands in "acme-lab-net". (#165)
- The device record tolerates and normalizes the flat `primary_network` spelling, so a record written by an older layout no longer breaks the validator. (#167)

### Changed
- Web: the gems are soft now. Each is a smooth, breathing blob in its own color that changes shape, with pill eyes and a mouth that morph too, and its face turns with the body toward your cursor. Every agent is born with one of three motions that sticks: Mood Morph (its shape shows what it is doing), Jelly (it wobbles as it moves) or Shape Cycle (it cycles through shapes while idle).

## [0.15.3] - 2026-10-11

### Added
- The web UI sidebar keeps every agent it knows. Agents that ran in this folder and are not running show as offline with when they last ran; a click starts one as itself. Agents on your other computers stay listed when no agent here is on the network (the last roster seen, kept a month) or after they leave it (offline, with when they were last seen); opening one with no agent here on the network starts this folder's koordinator to carry it.
- The New Session gem list says when each gem last ran in this folder ("Ran 3d Ago").
- New Session's workspace is a folder picker: the engine's folder, recent folders with their live agents, a folder browser, or a typed path.

### Fixed
- The web UI's New Session offers koordinator first, as a terminal does for the first agent in a folder. It was missing from the gem list, so a first web session started as lapis.
- Starting several kollab windows at once could wipe `config.json`, profiles and API keys included: a window that read the file while another was writing it started from a blank config and saved that. Config files are now replaced whole (mode 0600), and one that cannot be read is copied to `config.json.corrupt` before a fresh one takes its place.
- A first launch with no LLM provider opens the setup wizard by itself, as intended. The check looked for a fullscreen `setup` plugin that no longer exists, so a new install landed on a provider error instead. It now opens what `/setup` opens; Esc leaves it.
- With the keyring off (`KOLLAB_NO_KEYRING`, the tmux test harness), a provider whose key is kept in the keyring no longer opens the setup wizard at launch: the key just can't be read there.
- The `/conversations` preview shows an assistant turn's tool calls (`[tool: name] {input}`) instead of dropping them, including turns that were only tool calls. A message of several lines now gets a row per line there; the rows after the first were drawn over by the row below.
- `/resume` brings back every text block of an assistant turn; it kept only the first, so a turn that opened with a tool call came back empty.
- `/matrix` runs the AltView version, which pauses the render loop so it does not flicker, and any key leaves it. `/example` stays the fullscreen showcase; the AltView plugin reference moved to `/altview-example`.
- Provider errors in the log name the exception. A timeout or a cancelled call logged a bare `Anthropic call failed:` with no reason.

### Changed
- The log warns when a fullscreen plugin hides an AltView with the same command name. It used to skip it silently at debug level.
- The clean and modern renderers share one copy of their tool-row helpers (`kollabor_tui/_tool_detail.py`). Output is unchanged.

## [0.15.2] - 2026-10-10

### Fixed
- Opening another chat in the web UI while an agent was answering cancelled that agent's turn (its terminal showed "Request cancelled"). Leaving a chat, closing the tab or losing the connection now leaves the turn running; only Stop cancels it.
- Cancelling a turn (Esc, or Stop in the web UI) closed every MCP server, even ones the turn never called. It now stops only a server that is starting or still answering a call.

### Changed
- Web UI frontend packages updated: Vite 8, TypeScript 7, React 19.3, assistant-ui 0.15.27, Radix Dialog 1.2 and lucide-react 1.55.

## [0.15.1] - 2026-10-09

### Added
- Kollab ships as a single-file binary for macOS (11 and later) and Linux (glibc 2.28 and later) on x86_64 and arm64, attached to every GitHub release. The install script installs it, with no Python needed, and falls back to uv, pipx or pip elsewhere. In the binary, `/upgrade` and `kollab --update` download the latest release's binary, check its SHA-256 and that it starts, then swap it in and restart.
- With automatic updates on (`/config` → Auto Update Kollab), launching kollab installs a newer release before anything starts, then restarts as the new version with the same arguments.

### Fixed
- With automatic updates on, a launch installed the same update twice at once, in the window and in its agent's daemon. Only the window you launch installs now; daemons report the release.
- The install script reported success when uv, pipx or pip failed to install Kollab.
- The web UI showed a voice reply's raw `<display_text>` and `<spoken_text>` fields, and the voice rules ahead of what you said. It now shows what you said, the reply's display text, and nothing for a reply that stayed silent (a lone "."), as the terminal does. `/resume` shows them the same way.

## [0.15.0] - 2026-10-09

### Added
- `kollab --web-ui --restart` stops the web UI running on its port, and the engine its launcher started, then starts both again (after an update, say). A second plain `kollab --web-ui` no longer crashes with "Address already in use": it prints the running UI's address.
- `/voicemode classifier` opens a log of voice classifier decisions. Each call is on the left; on the right, what was heard, what the classifier read (transcript history, recent conversation, the agent's own speech), each pass's answer, the decision and what reached the agent. It updates while open. `/voicemode classifier laya|provider` still switches classifiers.
- The status bar's mic shows what the classifier did with your last words: `laya sent`, `ignored`, `unsure`, `deciding` or `stopped`, and how many lines wait.

### Changed
- The voice rules reach the model once per voice session instead of with every voice message (about 290 words each time). Later voice messages carry `[voice mode on: the user said this aloud]`, plus `[intent unsure]` when the classifier was unsure. The rules are sent again when voice mode is turned back on or compaction removes them.
- Laya decides on whichever pass is sure: the words alone, or the words with recent transcript history. History no longer turns clear speech into "unsure" ("Hello, can you hear me?" scored 0.71 with history and 0.99 alone, and the agent answered "."). The agent decides only when no pass is sure or the sure passes disagree. A Laya worker started before this update keeps the old rule until it restarts.

### Fixed
- A bare `kollab` could exit with "agent 'koordinator' not found" while listing koordinator as online: the window looked the new agent up before its presence record listed its socket. The agent now lists its socket before it reports ready, and the window attaches to the socket it was handed.
- The web UI's engine stored the ChatGPT login token in the OS keyring (Keychain on macOS) as if it were an API key, every few seconds. Login tokens never go to the keyring, and a plaintext API key is moved there once per process.
- The microphone's recording of the agent's own spoken reply came back as your words ("Yes, I can hear you."). A line that repeats the agent's overlapping speech is now dropped before the classifier and never reaches the agent.
- After one slow Laya call, `/voicemode retry` left voice stuck on "Laya request stopped" with speech pending; only a second retry woke it.

## [0.14.2] - 2026-10-09

### Changed
- Opening an agent on another computer follows that computer's trust, as messages do: under `open` (the default) every computer of your network opens its agents, and can approve their tools, with no command; under `agents` only the agents it may message, under `manual` none. `/connect attach allow|deny` is gone, and so is its list; `/connect revoke <device>` cuts one computer off. On a network shared with other people, use `trust agents` there.

### Fixed
- In the web UI every koordinator wore the same look, and dressing one dressed them all: an agent's look was keyed by its name alone. Each agent now has its own. One in another folder or on another computer is born with its own eyes, hat and color (a pool gem keeps the pool's color), and its chat's Properties dress it alone. Agents in the folder the web UI runs in keep the looks they had.
- Opening an agent on another computer from the web UI sat on "Opening…" for a minute, then blamed the other computer, when the agent running this folder's network had started before network attach existed. It now names that agent and says to restart it. An agent asked for anything it does not know answers at once instead of leaving the asker to wait out its timeout.
- After the web UI restarted, or the agent it was chatting with did, the agents on your other computers were missing from the sidebar until a chat was opened here, and opening one failed with "open one first". The web UI now asks any agent on this computer that is on the network, this folder's first, read-only over its hub socket, for both the list and the opening. While the relay connection is down, neither the web UI nor `kollab --hub status` lists those agents: the directory then is the cached one, not who is online.

## [0.14.1] - 2026-10-09

### Added
- The web UI opens the agents on your other computers as full chats, through your network: history, live turns, sending and permission prompts. The computer running the agent decides: run `/connect attach allow <this computer>` there (`/connect attach` lists who may; `deny` closes what is open). Never under trust manual, and under trust agents only the agents that computer may message. Until it allows yours, a click shows its answer and the command to run. An open agent closes as soon as that computer's rules no longer allow it.

### Removed
- `kollabor_agent.tool_timeline`: a tool timeline module that nothing called; only its own test used it (#83).

### Fixed
- `/resume` and `/branch`: Esc, or a chat that failed to load, left the terminal without its input box and status bar.
- The engine's `GET /status` answered 500 whenever a chat was open.
- In a folder where another window runs the network (a second terminal, or a web chat beside a terminal), New Join Code failed with "connect offer could not be created", and joining with a code failed too.
- Web UI errors read as sentences: no leading HTTP status ("503: could not open this agent" is now "Could not open this agent").
- The web sidebar greyed out every agent the web UI had not started (a `kollab` in a terminal, a detached daemon, another folder's agent): each one now opens as a full chat, with its history, live turns and permission prompts. The web UI attaches to it and only detaches when it closes, so it offers no Delete and never stops it. Agents with the same name in different folders are separate rows, named after the conversation their terminal shows.
- A permission prompt answered in one window (the web UI, the terminal, a second `kollab --attach`) stayed open in the others: the terminal sat on it, still "Working". Every attached window now closes it. A `kollab --no-daemon` session answers its own prompts in its terminal, even while the web UI watches it.
- The web UI showed an agent's XML tool calls as raw text (`<terminal>…</terminal>`), live and after a reload. They show as tool cards with their results, like native tool calls; the model still gets its own calls back as it wrote them.
- A web chat still starting showed for a moment as a second agent in the sidebar, and opening that row attached a second session to the same agent.
- After New Session, or a click on another chat, the open chat's message box stayed live for the seconds the switch took: a message typed there went to the chat being left, or was lost. The box now waits, and says what it is waiting for.
- A message from an open-trust device to a manual-trust device that allowed it (`/connect allow`) gets its answer back. Before, the request ran but the answer never left. The answer goes only to that message's thread, and only while the allow stands.
- A tool call the model wrote into its reply as text instead of making it (`<functions.hub_msg>{...}</functions.hub_msg>`, seen from gpt-5.6-luna) did nothing, while the reply said it was done. It now runs as the tool it names, through the same permission check, and shows as a tool card. So does a `<tool_call>{"name": ...}` naming a built-in or plugin tool (it went to MCP and failed) or holding backticks in its arguments (misread). A call that cannot be read answers with what failed. A block written beside the model's native calls only echoes them, so it never runs and nothing runs twice; a `<tool_call>` that names no tool says so instead of running as a tool named "unknown".
- An agent answering another device's request with `hub_msg` was told only "relay answer requires its exact target, thread_id, and question reply_to", tried again, and stopped on "Stuck loop detected". It is now told before it starts that its normal reply goes back to the sender.
- A message to an agent on another computer (`koordinator@box`) could fail as "unknown agent@device" for up to 15 seconds after that agent came online. The name now asks the other computers again before it gives up.
- A reply in which one generated image failed was lost whole, with "image generation completed without image data". The failed image now shows as a note and the rest of the reply stays.
- An agent answering another device's message showed "unknown agent@device" when its `hub_msg` reply misspelled the asker's long device name, or "relay answer requires its exact target…" when it marked the reply `kind="answer"`. Both now reach the asker as the reply.

### Security
- Another agent's message could end the block of agent notes the model reads before your turn: a `</agent_hud>` in its text closed the block early, so the words after it read as yours, to the model and in the web UI, where they showed as a message you sent. That covered any peer, including one on another computer. Agent tags inside a note are now escaped, and the web UI still shows the message as written.
- A relay takes up to 64 connections from one address (was 16), so a team behind one office IP fits, and up to 4,096 per worker (was 512), so a flood needs far more addresses. The nginx config from `--print nginx` caps connections per address too; Caddy has no such limit and says so.
- A relay on the same computer as nginx can listen on a unix socket (`--unix-socket`), so no other program on that computer can pose as the proxy.
- When two of your computers claim the same direct-connection name, the one you approved first keeps it, even while it is offline; the name never moves to the other computer.
- Direct connections never dial loopback or link-local addresses.
- An agent you accepted from outside your network can no longer carry or forward traffic inside it.
- Removing a device also drops its direct connection for good; approved again, it connects through the relay.
- Member lists are dated, so an old copy replayed later cannot remove devices you added since. Devices on 0.13 and 0.14 keep working; their lists stay undated until they update.
- A new device refuses a settings bundle older than its join, and settings sync never writes through a symlink. A `config.json` or `mcp_settings.json` that is itself a symlink is no longer synced (it was replaced by a plain file).
- Discovery keeps at most 8 addresses per computer, so one computer cannot crowd out the rest.
- A remote agent's failure text prints as its own message, with control characters removed, instead of as your command's error. Agent messages with C1 control characters are refused.

## [0.14.0] - 2026-10-08

### Changed
- The @ menu (terminal and web) has three broadcasts: `@broadcast` reaches every agent in this project, `@local-broadcast` every agent on this computer, in every project folder (it reached only this folder), and `@global-broadcast` every agent on every computer in your network. Broadcast rows in the web menu no longer show an "online" dot.

### Fixed
- `@broadcast` in the terminal answered "agent 'broadcast' is not online"; it reaches every agent in this project, as in the web.
- The web sidebar writes computer names as they are (`laptop-a-1`, not "Laptop A 1"), the way an `agent@device` address spells them.
- `kollab --hub status` names the network (`network: <name> via <directory> (trust: ...)`) and, on its own line, this device. It printed this device's name as the network.
- Web gem avatars no longer show a thin line at the edge of the gem: each gem is copied out of the shared renderer on whole pixels, so the copy stops blending in a sliver of another gem.
- `scripts/relay/autoupdate.sh` waits up to 10 minutes for PyPI's index to serve a new release; right after a release it stopped with "No matching distribution" and tried again only on the next timer run.
- Web Settings → Network was read-only, with no actions, in every chat but the one whose agent runs the network. Every chat in the folder now shows the full tab, Knocks and New Join Code included.

## [0.13.1] - 2026-10-08

### Added
- The @ menu, in the terminal and the web, opens with @local-broadcast (every agent in this folder) and @global-broadcast (those plus every agent on your network), and lists agents on your other computers as agent@device. A broadcast's reply says when no other computer was reached, and why.
- A relay run with `kollab relay run` can keep itself on the newest release: `scripts/relay/autoupdate.sh` (with `kollab-relay-update.timer`) installs a newer kollab from PyPI beside the running one, switches to it and rolls back if health fails. kollabor.ai runs it, so a release reaches the directory within about 20 minutes.

### Fixed
- The web @ menu offers only the live agents in the chat's project folder; an agent in another folder never heard a message sent from it. A message from another computer keeps its agent@device address in the model's context, so a reply reaches it, and the web shows the sender as "Koordinator on devbox".
- A self-hosted or kollabor.ai relay's public `/relay/v1/health` lists the protocol versions it serves under `protocols`; only each worker's own health did.

### Removed

- The eight bundled skills copied from Anthropic's skills repo: docx, pdf, pptx, xlsx, frontend-design, mcp-builder, skill-creator and webapp-testing. Four are proprietary and the rest shipped without their license. Put any skill you want in ~/.kollab/skills/ or .kollab/skills/.

## [0.13.0] - 2026-10-08

### Added
- Open an agent that runs on another computer right from your terminal: `kollab --attach lapis@devbox`. It works with any computer you can already log in to with ssh and that has kollab installed. Detaching leaves the agent running there.
- Web UI: the Kollabor K logo heads the sidebar and is the browser tab icon.
- Web UI: `KOLLAB_WEBUI_HOSTS` (comma-separated) serves the web UI on more addresses than 127.0.0.1, such as a VPN address, so a phone on the VPN can open it. A page opened from another machine reaches the engine through the web UI's own `/engine` proxy, since the engine stays on 127.0.0.1; an address that cannot bind is skipped. Anyone who can open the page gets the engine token, so list private addresses only.
- Web: agents are born with a random look that sticks. The first time the engine sees a gem alive it rolls its eyes and hat and keeps them for good (a seasonal costume covers the hat while the season lasts; the gem keeps its own color). Gem Studio (sidebar footer) dresses any gem over that look (eyes, hat, color) and sets the season for all (Auto, None, Halloween, Christmas) on a live stage; Random Look rolls a new outfit and Reset goes back to the born look. The engine keeps the looks (`GET`/`PUT /agents/appearance`, stored in `~/.kollab/hub/appearance.json`), so every browser shows the same gems.
- Web: messages between agents show in the chat, as in the terminal. Another agent's hub message appears as a message from that gem ("Aquamarine → Lapis") before the reply it wakes; one the agent only overheard is dimmed. What an agent sends shows in its own chat the same way, with its own gem, instead of a "delivered to lapis" tool row. A turn the page did not run (a hub message that woke the agent, a turn typed in the terminal) appears in the open thread without a reload, and the composer keeps what you are typing.
- Web: a session's Properties. Right-click its row in the sidebar (long-press on a phone) for Properties or Delete Session, or double-click its gem (in the row or greeting on an empty chat): Gem Studio opens on that one gem to dress it in place. Its Chat tab takes the agent off the hub, so your other agents stop seeing and messaging it while your own chat keeps working, or puts it back, with no restart; `/hub leave` and `/hub join` do the same in the terminal.
- Web: the sidebar lists every agent under the computer it runs on. This computer comes first, with its chats and the agents running in a terminal; then each of your other computers with its agents, shown while a chat runs here and this computer is connected to your network (open those from their own computer).

### Changed
- Plain words replace insider terms on the screens people use: the web UI says kollab instead of engine and daemon, the setup wizard asks for a server address instead of an endpoint, and `kollab --help` explains --attach, --web-ui and --daemon in everyday terms.
- The Connect and Knocks screens in the terminal stand out: a branded title bar that says what the screen is for, the join code highlighted so it is easy to read out, quiet labels with bright values, and the keys you can press in color.
- Joining a computer with a code is much faster. The request shows up on your other computer in about 10 seconds (it could take over a minute), and the join finishes a few seconds after you press Accept. Making a new code also cancels the old ones nobody used.
- Knocks are calls (#123). A knock rings the device behind a contact route for 5 minutes and the directory keeps nothing: the seven-day contact mailbox and its `/relay/v1/contact/*` routes are gone, so a flood of knocks costs the directory frames, never storage. Only an accept is answered; a reject, a block or a full missed list rings out like an unanswered knock, and every outcome but accept reads `unavailable` to the knocker, whose device redials for an hour (`plugins.hub.knock_redial_minutes`). `/connect knocks` is now the knock screen (ringing knocks, the knocks you placed, missed knocks kept on this device, blocked routes, contacts, who may knock), the same under Settings → Network → Knocks; `/connect knocks everyone|contacts|nobody [for 2h]`, `/connect expect`, `block` and `unblock` set who may knock. Older kollab versions are never rung, and cannot knock until they update.
- Web UI: the session header is one slim row on desktop (two on phones) with borderless controls: the model, the approval mode (an amber open shield for Trust All), MCP and online counts, and Settings and Clear as icons with tooltips. The sidebar header is one row (the logo, then New Session and its options as icon buttons), rows show bigger gems with smaller text and drop the message count, and the session count sits beside "Sessions".
- Web: with no session open, the next free gem greets you with the model it will run and a Start Session button; inside a session the composer reads "Message Lapis…". Shell tool rows say what ran ("List Files", "Run · make build" instead of "Run ls" / "Run make"). The model picker dims the profile beside the model and shows the model alone below 1280px, so the header stays one row on a laptop with the sidebar open. Trajectory's header is a single row of counts and controls (the tab above already names it), and on phones its controls share one row. Its labels are in Title Case ("Collapse Tools", "Record Details", "Load Earlier").
- Web: Who Is Online shows each agent's gem and its name in Title Case; model lists read like the header (the profile dimmed, cut with an ellipsis instead of mid-word); MCP Servers drops its "No description" filler; every confirmation (delete session, delete profile, delete MCP server, clear history) has a Title Case question and a red action button; Settings' status lines are cased ("Engine connected", "Hub Sapphire · Idle").
- Web: the agents' terminal checklists ("[x] done", "[ ] next", also "todo: [x] item" on one line) render as checklists, and a turn with a single tool call shows just that call's row (no "1 tool call" header above it).
- Web: the page sets `writingsuggestions="false"`, the switch browsers give pages to turn off system typing suggestions in its inputs (macOS 27 floats a Siri button at the cursor that covered the composer's attach button).
- Web: the gems move between states smoothly. The thought bubble grows and fades instead of popping, the hunch, typing bounce and hands follow the turn to the laptop, and a gem thinking between two tool calls stays at its laptop instead of flipping to a thought bubble and back.
- Updates no longer break the computers already out there. The relay and every device ignore fields and messages a newer version adds, the relay connection names its protocol version, and a mismatch says which side to update (`this version of kollab is too old for kollabor.ai; run kollab --upgrade`) instead of failing to connect. The rules are in `docs/specs/agent-public-beacon.md#versioning`.
- Join requests and knocks show the other computer's "device ID" instead of a "fingerprint", and Settings → Network says Connection: Connected instead of Relay: Online.

### Fixed
- The web sidebar lists every running agent, including the koordinator and numbered gems like lapis-2; before, only agents with a built-in gem name showed.
- Apps that start a chat through kollab's engine with their own API key get a working agent; it used to wait 45 seconds and fail because the agent never received the key.
- The bundled `default` agent's description said it fixes linting errors; it now describes the general assistant it is.
- Shell commands report exit code 0 when they succeed; every success was reported as exit 1 next to `success: true`.
- Web: New Session's Agent field shows the agent the session will run (the gem's pool agent until you pick one) and pins the one you pick; it said "default" while the hub swapped the gem to `coder`. The agent list no longer appends each bundle's unused profile ("coder · default").
- Web: a session reloaded in a background tab shows in about a second instead of up to 20 (the history gate polled with timers the browser throttles).
- Web: Trajectory keeps a tool's name readable beside long output ("Run ls" showed "R…"); a tool result's multi-line output no longer ends in a stray comma; the sidebar shows gem names in Title Case.
- Web: sidebar rows no longer clip the gems' hats and props.
- Web: on a phone, opening a session, starting one or opening Settings from the sidebar closes the sidebar; it stayed over the chat.
- Web: a session deleted elsewhere (another tab, the CLI, a daemon that died) closes within a few seconds; its event streams retried every second for as long as the tab stayed open.
- Web: opening the sidebar on a phone no longer pops the New Session tooltip; the first Escape closed that tooltip instead of the sidebar.
- Web: a reply's time ("14.0s" under it) stays after a reload. The live clock lives in the tab, so a reloaded turn is timed by the history's own timestamps, from your message to the final reply.
- `kollab --web-ui` no longer reuses an engine of another version that is still running on port 7433 (a 0.12 web UI was served by a 0.10 engine left over from an older install). It leaves that engine running for whoever uses it and starts its own on the next free port.
- Web: a turn that fails says why. With a broken provider the reply stayed blank; the error now shows in full under your message (it was cut to two lines) and stays after a reload (`GET /sessions/{id}/history` returns `last_turn_error`).
- Web: a send the agent refuses because a turn is already running no longer cancels that turn.
- An agent past the 24th on one computer (lapis-2, sapphire-2 and on) runs `coder` like every other gem; it ran `default`.
- Two folders with the same name on one computer (two checkouts of one repo) no longer get the same device name: the second adds its parent folder, like `laptop-feature-app` next to `laptop-app`. The name is saved the first time, so it stays put; a device already on a network keeps its name.

## [0.12.0] - 2026-10-07

### Changed
- A paste shorter than `input.paste_min_chars` (default 500, in `/config` under Input Settings) goes into the input as text, line breaks kept and not submitted. Only longer pastes collapse to `[Pasted #N ...]`; before, anything over 10 characters did. Ctrl+V clipboard text follows the same rule instead of always going in flattened to one line. Text with a character the input cannot hold still collapses, so nothing is lost.
- Sliders with a whole-number step show whole numbers in `/config` (`History Limit: 100`, not `100.0`).
- The first Ctrl+C in a window that started its agent says what each key does next: "Press Ctrl+C again to stop koordinator, or Ctrl+Z to detach". The hint wraps on narrow terminals.
- After Ctrl+Z on the coordinator, the notice says `reattach: kollab`: a bare `kollab` in the same folder picks it back up. Other agents still show `kollab --attach <name>`.
- Tool durations no longer count the time spent waiting on a permission prompt, in the terminal and the web UI.
- The web UI's Computer panel is gone; `GET /hub/agents/{id}/output` now returns the agent's output lines.

### Added
- `kollab service install` keeps the folder's agent running: a systemd unit on Linux that runs as you and starts at boot (sudo writes it), or a LaunchAgent on macOS that starts at login, restarted 5 s after it stops either way. `kollab` in the folder attaches to it and closing that window leaves it running; `kollab --hub stop` says it will come back. `kollab service status` shows the unit, its pid and whether the agent answers; `kollab service uninstall` removes it. `install` refuses while another agent runs in the folder.
- `kollab relay serve --domain <domain> --install` installs the directory as the systemd unit `--print systemd` shows, after creating its state directory, then enables and starts it; `--uninstall` removes it.
- The README and `docs/architecture/agent-network.md` show the agent network: who connects to whom, the join step by step, the tunnel's layers, how `/connect` uses a domain's DNS record, staying online and running your own directory. `scripts/build_network_diagrams.py` draws them.
- `kollab --web-ui` has a new look. The sidebar shows each session's gem as a live 3D avatar that acts out what its agent is doing (thinking, typing, searching, messaging, an error), and an empty chat greets you as that gem. New Session starts a session in one click; the options button next to it picks the agent, gem, model and workspace.
- The web chat composer is a pill with attachments, a `/voicemode` mic and a send/stop button. The mic reads the daemon's voice state, so it is right after a reload or when voice mode was started in the terminal. Attachments are hidden for models that cannot read images.
- Replies in the web chat stream in as the model writes them, and a turn that goes text, tool, text keeps that order. Tool calls show while they run, then their real duration; a group of calls shows its total, and a failed call is red with its error line.
- The web Trajectory tab has a Waterfall view next to the Table: one block per turn, each tool call under the model request that made it, timed with the daemon's measured run times.
- The README opens with a recording of kollab running.

### Fixed
- Agents no longer call `task_complete` with a task id they made up. Its description and example now ask for the ledger id from the work queue (the example used a slug, `phase-b`), and calling it for an unknown task says so: "no open task <id> on your ledger".
- A native tool call whose first argument holds the rest of the documented XML tag (GLM 5.3 sends `{"name": "coder\" task=\"..."}`) no longer fails with "task required". kollab splits out the parameters the tool declares, so `hub_spawn`, `hub_cron_add` and the other tag tools run on the first try.
- `kollab --hub stop` run from another shell no longer reports an agent that stopped as "pid survived SIGKILL". The agent had exited, but its window had not reaped it yet, and the check counted that as still running.
- A joined computer no longer lists an MCP server the other computer switched off as "Skipped MCP servers not installed here". It is still skipped when its command is missing, but nothing was missed.
- `python main.py` exits with the command's status. It always exited 0, so a failed `kollab relay serve` or `kollab service status` looked like success when run from a checkout.
- A second `kollab` in the same workspace starts the next agent again instead of joining the session another terminal still has open. A bare relaunch only reattaches to a daemon with no window (one left by Ctrl+Z or a closed terminal); the agent's status now reports how many windows it has.
- Pasting an image no longer closes `kollab`. The daemon read each client message with a 64 KB cap, so an image dropped the connection and the window exited as if the agent had died.
- The hub coordinator now saves work it takes back from a dead agent, so the work is reassigned. It used to log the same reassignment every 5 seconds and never save it.
- `KeyPress` hooks, plugin or config, run once per key instead of twice, and a hook that returns `prevent_default` now stops the key's normal handling. Ctrl+Z printed its detach notice twice because of it.
- An agent answers a message from another machine, a greeting included. A new `agent@device` message got the hub rule "if it is only an acknowledgement, do not respond", so a "hello" woke the agent, it replied with nothing, and the sender saw silence. The agent is now told the sender is waiting and to reply once.
- Agents on one machine no longer spend the same ChatGPT refresh token twice: every write of the login file holds a lock and replaces the file atomically, and a refresh reuses a login another process just wrote.
- Approving a permission prompt in the web UI finishes the tool's row (result and duration) in the same turn, and the row no longer shows a second Allow/Deny bar that the engine rejected.
- The web UI's Clear history, and a typed `/clear`, `/new` or `/restart`, reset the open thread instead of leaving old turns on screen, and the Trajectory tab stays open.
- Spaces typed in the web composer are kept, and the web UI no longer starts with a 401 before it has loaded its engine token.
- After a reload, web chat messages no longer show the agent status (vault, hub) the daemon adds to a turn, and the Trajectory names such a turn by what you typed.
- Saving an edited profile from the web UI works again (a 500 that showed as "Failed to fetch"). The editor keeps a profile's streaming, tools, temperature and base URL, lists every provider the engine runs, and asks before deleting.
- Web Session Settings shows the live effort after Apply, and lists the agent and the gem separately.
- Web MCP rows offer Edit and Delete only for servers in the global MCP config, Delete asks first, and the dialog fits a phone screen. An MCP result flagged `isError` counts as a failure instead of a green check, and MCP file tools name the file they touch.
- Creating a web session right after deleting one or restarting the engine no longer fails with "daemon failed to start: Connection refused", and an agent whose process has exited drops out of the Online count at once instead of after a minute.
- Engine errors reach the browser as JSON with CORS headers, so the web UI shows the real error instead of "Failed to fetch".
- Web session names read as words, the header shows the session's live model, the `@` menu lists only online agents, and links in replies open in a new tab.

## [0.11.3] - 2026-10-05

### Changed
- `--llm` with a name that is neither a profile nor a loadout stops with exit status 2 and the closest names, instead of running the default model.
- Claude models that think by default (Opus and Sonnet 5.x, Fable, Mythos) are asked for summarized thinking, so their reasoning shows in the thinking display instead of arriving empty.

### Added
- Agent bundles can set `"hub": false`. The engine then runs that session's daemon solo: the engine still attaches to it, but it never sees, messages, broadcasts to or receives from other agents, never becomes coordinator or joins the network, and its prompt has no hub instructions. Embedded assistants (one web session per user) use it so one user's input is not broadcast into every other session.
- A hub agent's online announcement names its active profile, provider, model and configured reasoning effort (`default` when none is set).

### Fixed
- Engine sessions pass `MENTIKO_SESSION_ID` and the caller's `MENTIKO_SESSION_TOKEN` to their daemon, and through it to MCP servers. Without them a Mentiko MCP server could not authenticate as the user and sent UI actions (such as page navigation) to no session.
- A process with the hub disabled by environment (`KOLLAB_HUB_DISABLED`, `KOLLAB_NO_HUB`) no longer gets the hub collaboration instructions in its system prompt.
- The prompt cache holds across new user and hub messages. Context blocks (session, hub status, `[env]` events) stay on the message they were first sent with; moving them to each new message rewrote history, so ChatGPT/Codex sessions fell back to the cached system prompt at every message.
- ChatGPT/Codex requests send session headers and pass encrypted reasoning back, so the conversation stays cached and reasoning carries across turns and `/resume`.
- Anthropic, OpenRouter (Anthropic routes) and Gemini requests keep their prompt cache across turns and send signed thinking or thought signatures back. Gemini tool calls and results are no longer dropped.
- OpenAI-compatible providers report cache reads for DeepSeek, Kimi, Qwen, xAI and Z.AI, Azure streams report usage, and DeepSeek V4, GLM and Kimi get their reasoning back during tool loops.
- Cost estimates no longer charge Anthropic and Gemini cache reads at full price on top of the cache discount.

## [0.11.2] - 2026-10-03

### Added
- The web UI has a Settings dialog with six tabs: Session, Configuration, Loadouts, Model, Setup and Network. They cover what the terminal's `/config`, `/llm`, `/model`, `/setup` and `/connect` screens do, and typing one of those commands in the web chat opens its tab. Secret values are not sent back to the browser, and join codes are redacted from logs and saved conversations. See `docs/features/web-settings-panels.md`.
- The model registry adds GPT-6, Claude 5.5/Fable 5.1, Gemini 3.7/3.8 Flash, and Grok 4.6/4.7 entries with context, capability, and pricing metadata.
- Context compaction can ask the model what to preserve before summarizing; `/compact now`, `/compact status`, and `/compact preview` provide manual controls. Automatic compaction thresholds are capped at 272K tokens.

- A model with no entry in the model registry logs one warning naming the default context window it falls back to.

### Changed
- Requests are no longer trimmed to fit the context window. A pre-send guard silently dropped the oldest messages from every request without telling the model, so agents could lose their place mid-task. Large tool output is already capped where it is produced, compaction shrinks the history, and a real overflow now shows a visible error instead.
- The `kollabor.llm.max_history` setting is gone. Every request sends the whole conversation instead of the last N messages, and compaction is what keeps it inside the model's window. The Max History slider is removed from `/config`.
- The question gate is removed. A `<question>` tag no longer pauses a reply's tool calls until you answer, and agents are no longer told to use it. Tools in a reply are no longer paused by the gate, though normal permission checks still apply.
- `kollabor.llm.context_overhead_tokens` defaults to 48,000 instead of 60,000. Measured on 169 real first turns, the system prompt and tool schemas take 34K tokens at the median and 45K at most, so the old guess took room from tool output.

### Fixed
- Typing `/config`, `/llm`, `/model`, `/setup`, `/connect` or `/matrix` in the web chat no longer hangs the turn: the web opens the matching Settings tab (or says the command needs the terminal) and the turn ends. Fullscreen views now refuse to open in a process with no terminal instead of waiting for keys nobody can send.
- A running daemon sees config saves made by another process. It polls the config files once a second; the file watcher it used before was not installed there.
- Context compaction no longer re-fires every turn. The trigger compared a rough character estimate (about 2.3x too high, and counting text compaction can't remove) instead of the prompt size the API reported, so one compaction never got under the threshold and every turn summarized the summary again. It now uses the reported count, and the estimate only when no usage was reported.
- Truncated replies now auto-continue on Anthropic, Gemini, ChatGPT/Codex (OpenAI Responses) and custom-provider streams: each reports a max-output-tokens stop as `length`. Responses streams no longer drop `response.incomplete` (it carried the final usage), and custom streams keep `finish_reason` past a trailing usage-only chunk or a server that omits usage.
- Auto-compaction pauses with a visible warning when a round can't get the prompt under the threshold, and retries once the prompt has grown, instead of re-summarizing the summary every turn. A compaction deferred for in-flight hub coordination now runs after 3 deferrals, only the newest task reminder survives a round, and the summarizer retries rate limits and server errors.
- A restarted hub agent's rebirth context lists its active TaskLedger cards, or says it has none, so it no longer asks its peers for its own assignments.
- Hub wake chains stop after 3 identical tool errors in a row instead of running up to 500 turns. They share the queue drain's breaker, which now trips at 3 as its log line always said.
- The hub's background (dreaming) LLM call retries rate limits and server errors.
- ChatGPT sign-in sessions survive an expired access token: a 401 refreshes the token, rebuilds the provider and replays the request once.
- Responses streams report the server's own error on `response.failed`, `error` and malformed final events instead of "Stream ended without response.completed".
- Tool-call stops are reported as `tool_calls` on Anthropic and Responses streams, including the ChatGPT/Codex backend, which sends its calls only as stream items, so the inconsistent-stop warning can fire. Codex calls made through the non-streaming path keep their tool calls.
- OpenAI chat and OpenRouter streams keep each tool call's real id and read every call in a chunk; tool calls with no arguments are no longer dropped; Gemini and Responses requests keep every system message.
- A reply that is still cut off after auto-continue, or whose continuation comes back empty, now says so instead of ending mid-word.
- A direct hub message addressed to an agent is no longer dropped while the user is typing or the ESC cooldown is active; it waits and wakes the agent once it can run.
- Starting an agent with a launch task no longer broadcasts that private prompt to every peer as a user message.
- Hub tool results retain a token budget even when stored conversation history fills the budget, so agents receive results instead of repeatedly rereading spill pointers.
- In voice mode, a response consisting only of `.` stays silent in the conversation display, matching the voice instruction.

- The raw request log rotates to a new file at `raw_log_max_file_mb` instead of dropping interactions.
- A plugin that initializes twice (startups with a plugin CLI argument such as `kollab --hub status`) no longer logs "Command name conflict" for its own commands.
- The API-format error points at the profile's real `provider` and `base_url` settings and `/setup` instead of a `tool_format` setting that doesn't exist.

### Removed
- Unused code: the `kollabor_ai.adapters` package, `ProfileManager.get_adapter_for_profile`, `LLMService._call_llm`, `AgentLifecycle.BLOCKED`, the never-set `force_continue` flag on `LLM_RESPONSE`, the Responses and Gemini `_format_tool_result` helpers, and the `previous_response_id` / `prompt_cache_*` pass-through.

## [0.11.1] - 2026-10-01

### Added
- The status bar shows the microphone: `mic off`, `mic starting`, `mic listening` or `mic error`, and while it listens, the last thing it heard, cut to fit. New layouts have it on row 2 after the status; an existing layout gets it once, on row 2 or its first visible row, without moving anything else.

### Fixed
- A workspace that issued join codes before 0.11 can join a network again. Its private directory file was still pinned to its own key, so the join failed right after the other device accepted it: the form said only "the join request did not complete" and nothing was logged. Once the join may go ahead, the old file is set aside as `private-directory.<time>.replaced` (kept, not deleted) and a fresh one is pinned to the new network. A join that does fail now says why on the form and in one log line, never with the code or a key.
- `/hub stop all` typed in the attached window no longer says "stop timed out (pid survived SIGKILL)" for an agent that did stop. The window is the daemon's parent and now reaps it before checking whether it is still alive.

## [0.11.0] - 2026-10-01

### Added
- Guided setup on the first launch of 0.11.0. Once per machine kollab shows "New: connect your agents across computers. Enter sets it up now; Esc for later (/connect any time)." Enter on a computer with no network offers "Start a new network on kollabor.ai" (it creates the network, then opens the Connect screen with the join code and an "On your other computer" box: upgrade, run kollab and press Enter on the same notice, choose Join with a code) or "Join with a code"; on a computer already on a network Enter opens the Connect screen. After a join one line says settings arrive sealed from the computer that issued the code and that a ChatGPT login does not travel: run `/login`. Enter and Esc both answer and write the marker `~/.kollab/connect-guide-seen`; pipe mode, detached and spawned agents and launches without a terminal never see it.
- The guided setup counts a computer alone on a network as not set up. kollab 0.10.7 made a network of one on every launch, so every upgraded computer went straight to the Connect screen and never saw Join with a code; now Enter offers the two choices there. Start a new network keeps the computer's own network (named if it has none) and opens the Connect screen with the code and the steps for the other computer; Join with a code replaces it. A network with another computer on it is never replaced, and Enter on one still opens the Connect screen.
- The line after a join names the computer that issued the code as soon as this computer knows its name (the generic wording only after about 30 seconds without one), and it shows in the main pane as well as on the Connect form, so closing the form early no longer loses it. The "Skipped MCP servers not installed here" and "Settings sync is off in this workspace" lines are said once per change instead of once per launch: the network state keeps what was last said.
- Every device on a network approves every other, so the mesh routes in networks of three or more devices. After a join by code each device sends the members it approved a signed list of the devices a person on it accepted (by code, or by joining through them) and of the ones it revoked; a member approves a device that a member it already approves names, on the same network, and drops a revoked one along with any device only that one named. Accepted strangers never vouch and are never named, and approval grants nothing: under `agents` and `manual` trust the per-message grants stay as they are. A designation claimed by two members goes to the first one approved, so a later claim cannot block its direct link.
- The mesh is on. Devices of one network now reach each other directly first (a signed locator names a device's TLS endpoint and the session it runs under; approved devices dial it and fall back to the directory when the dial fails) and forward for each other: a device with no relay connection, such as a second device on the same host, is reachable through any network member that can reach it, and the message stays sealed end to end so the forwarding device never reads it. Forwarding is bounded at 120 frames a minute per peer, 600 in total and 16 at once. `plugins.hub.peer_direct_enabled` and `plugins.hub.peer_forward_enabled` default to `true` and are the two off switches; neither opens a socket, because the TLS endpoint, LAN discovery and private addresses stay opt-in. Roster names and commands are unchanged. Proof: `tests/live/m3/`.
- An accepted knock now reaches its agents. The stranger stays in its own network; the relay routes between the two device keys across rooms (`POST /relay/v1/contact/links`) only while each key has signed a declaration naming the other: the knocking device declares when it knocks, yours when you accept, and `/connect revoke` or `/connect leave` withdraws it. The stranger sees only the agents you `/connect allow`, as `agent@device`, and those agents can answer the agent that knocked; `/connect deny` refuses at once. A stranger gets no mesh records and is left out of `hub_broadcast scope="network"`. The relay must be upgraded for delivery: an older relay still takes the knock and the accept but delivers nothing between the two networks. The wire contract is in `docs/specs/agent-public-beacon.md`.
- `kollab relay serve --domain <domain>` runs a whole directory in one process: the relay, the signed discovery document (renewed every minute, naming the relay only while the relay is ready) and the key file at `/.well-known/agent-keys.json`, on one local port (`--bind`, `--port`, default `127.0.0.1:9078`). It creates or loads its signing key under `~/.kollab/relay/<domain>` (`--state-dir`), so a restart publishes the same identity and joined devices reconnect on their own, and it prints what is left to do: the one `_agent.<domain>` TXT record and the five routes a TLS proxy forwards. `--print nginx`, `--print caddy` and `--print systemd` print the proxy config or a service unit for the same settings and exit; nothing is installed for you. A state directory that already runs the standalone publisher is adopted as it is, and an office behind one address raises `--max-connections-per-source`. `kollab relay run --config` (several workers, shared backend) and the bare worker `kollab relay serve --origin` are unchanged.
- Sealed config sync. The device that issued a join code keeps every device it accepted in step with it: the overrides in `~/.kollab/config.json` (loadouts, models, API keys), MCP servers, `agents/` and `skills/` travel signed by the primary and sealed to each device's key over the network's secure conversation path, so the directory only carries ciphertext. They go out on accept, within about ten seconds of a change (a loadout switch is one small request) and on reconnect. OAuth logins, project `.kollab/`, vaults, conversations and machine-local settings (`kollabor.updates`, `kollabor.permissions`, `plugins.hub`, `plugins.voice`) never travel. On the receiving device each synced setting is read-only in `/config` with `managed by <primary>` (a secret shows `set`), a Loadout and a Model row lead LLM Settings on every device, the accept line says `sealed config queued: settings, agents, skills, mcp, api keys; not oauth logins`, and the Connect screen gets a `config` row. `/connect leave` keeps the received values as the device's own; `/connect revoke` and `/connect rotate` stop the updates. A network joined before this build has no recipients, so join once more. Joining still copies the issuer's active API-key profile once as `kollab-…`, so a duplicate loadout can show.

### Changed
- `/connect` is thirteen commands: `code` shows an eight-character `XXXX-XXXX` join code on a private screen (never in scrollback or logs), `accept`/`reject` take a device name, `status` lists devices and agents as `agent@device`, plus `name`, `trust open|agents|manual`, `knock`/`knocks`, `allow`/`deny`/`revoke`, `leave` and `help`. The task-envelope commands (`authorize`, `send`, `withdraw`, `answer`, `task`, `cancel`) work only under `manual` trust and appear in `/connect help all`. Old names print where to go.
- Hub messages reach remote agents: `<hub_msg to="agent@device">`, `<hub_broadcast scope="network">`, `kollab --hub msg agent@device "text"` (waits for the reply), and a network section in `kollab --hub status`. Every device has a human name (`<hostname>-<folder>` by default) and one trust level per network; `open` is the default and needs no approvals.
- Bare `/connect` on a device that is on a network opens the Connect screen: network, this device, a join code that counts down (`c` makes a new one), join requests you accept or reject with `a` and `r`, waiting knocks and the online agents, refreshed every two seconds. `/connect code` shows just the code, on a private screen. With no network, `/connect` still opens the private code form, now titled Connect with `network      none` above the code field, and a pending request no longer prints a receipt id.
- A network is named after the device that starts it, `<first device name>-net` (for example `laptop-kollab-net`). A device that joins takes the name from the signed decision that admits it, so the Connect screen, `/connect status`, the accept line, the joined line and the hub context show it instead of the directory's name. `/connect leave` forgets it. No command renames it yet.
- Join codes are looked up by a keyed tag (`POST /relay/v1/enrollment/lookup`) and an offer burns after five failed proofs. The joining device sends its name with the request, and the accept line shows the name and key fingerprint.
- Config sync skips MCP servers whose command is not installed on the receiving device (a bare name that is not on `PATH`, or a path that is missing or not executable) instead of writing one that cannot start. A local server of the same name is never removed because of it, URL servers always sync, and the device shows one line, `Skipped MCP servers not installed here: a, b`, once per distinct set, refs #121

### Removed
- The stale Codex live-acceptance script under `scripts/relay/` and its unit test are gone; manual trust (Story 7) gets a short live proof instead, refs #121

### Fixed
- A request that runs as a task no longer writes its sender's relay address, which holds a device key, to the log: the wake line names the sender as agent@device.
- The guided setup's Start on a half-set-up network of one (a 0.10.7 room with no domain and networking off) leaves it and starts fresh on kollabor.ai, as Join already did, instead of saying it could not start a network.
- A request from a device on manual trust now runs as a task on a device set to open. The sender marks its first message as a task (`task` on the wire) and the receiver runs it as a remote task turn, answers it as the task's result, and takes `/connect task` and `/connect cancel` for it, with no receiving grant while it stays open; before, the open device ran it as a plain hub turn whose reply the manual device refused, so no result ever came back and cancel had nothing to stop.
- A message queued under open trust no longer goes out after `/connect trust manual`: raising the trust level revokes it, so the outbox retry cannot deliver it with no human grant.
- A relaunch in a workspace that already has a live daemon now attaches to it instead of starting a second one next to it. The second daemon took another agent name (lapis instead of koordinator) while the first kept running with no window, so what was sent to the first agent was never seen. A launch that names an agent (`--agent`, `--as`, `--project`) or carries a first message still starts its own daemon.
- A `hub_msg` to an `agent@device` that the peer reports as unavailable now fails with "that agent is not online: run /connect status to see who is" instead of saying it was sent and that its reply arrives by itself.
- A keyring that unlocks after launch is picked up by sealed config sync: the primary reads the keyring again for the API keys it could not read, once each time a device reconnects (a peer's new relay session, or this device's own relay link coming back), instead of leaving them out until `config.json` changes. The keys already read are not read again, so a missing macOS Keychain entry still prompts at most once per reconnect, refs #121
- A remote request whose chain dies no longer stays open: a queue drain, hub continuation or goal turn that raises, is cancelled or is healed by the watchdog now ends the request with a failed end frame at once, and a request no turn ever handled gets the failed frame at the 600 s ceiling instead of being dropped, so `kollab --hub msg` exits 1 instead of waiting for nothing, refs #121
- A restart no longer announces every pending join request and knock again: the ids already announced are kept in the network state, pruned to what is still pending
- A knock nobody answers no longer leaves its approval, trust, link and reply grant behind: the knocking device records when it knocked and drops them after seven days, unless the other device accepted (its link is live, or it already reached this device)
- A knock the other device rejects now clears its approval, trust, link and reply grant at once instead of after seven days: the directory answers `POST /relay/v1/contact/status` only to the device that sent the knock (an unknown id and someone else's id read the same, for the knock's 24 hours), and the knocking device asks once a minute while it is online; an older directory without the route leaves the seven-day expiry in charge, silently
- `kollab --hub msg` no longer exits with "finished without a reply" while the remote agent is still answering: a remote request's turn now ends when the queue processor finishes the whole chain (no tool result left to go back to the model, nothing queued, no other turn running) instead of after the model looked idle for a second, and the end frame still follows every reply and the forwarded plain-text answer, refs #121
- A remote request whose turn answers in plain text instead of `hub_msg` now gets that text back: when the turn sent no reply on the request's thread, the runtime sends its final assistant text as the reply (hub XML and thinking stripped), so `kollab --hub msg` and cron jobs always get their answer.
- `/connect knock` and `/connect knocks` work in the default launch (a daemon plus an attached window), so a stranger can knock. The daemon owns the relay: the window hands it the knock and prints its answer (`knock sent to <route>` or its refusal), and the knock review lists the daemon's waiting knocks with `a` and `r` deciding on the daemon, then `/connect allow <device> <agent>` as before. Both used to print `attached daemon does not support private contact requests`. A daemon older than this build still gets that plain refusal.
- Two network members that each run a direct endpoint reach each other again after their secure sessions lapse: the handshake no longer rides a peer link that outlived its sessions, and a dropped responder error now logs its class.
- A join request or a knock that arrives while no Connect screen is open now shows one line in the main pane, `<device> wants to join <network>. /connect to review` or `<device> knocked. /connect knocks to review`, once per request and never while the Connect or knock screen is open. It names the device only, never a code, key, fingerprint or `relay:` address, and reaches an attached window through the daemon's usual system-message path.
- Under manual trust, `/connect authorize`, `send`, `withdraw`, `answer`, `task` and `cancel` print and take short per-network numbers (`request 3`, `question 4`) instead of 32-hex ids, a remote question ends with `(answer with /connect answer 4 <text>)`, and a manual-trust relay event shows `agent@device` as its sender instead of a `relay:` address.
- The Connect screen opens in the default launch (daemon plus attached window): its requests and roster come from the daemon and `a`/`r` go to it, instead of printing status text. The joining device's code form now says `request sent to <network>; waiting for approval on another device` as soon as the relay has the request, then shows `joined <network> as <device>. trust: <level>` or the rejected line when the other device decides, and never reports a slow approval as a failed submission. A join code that a decision has used no longer stays on the accepting device's screen: the line reads `used   press c for a new code`.
- Trust, the device name and peer names no longer revert when the client saves stale state on `/connect leave`, `/connect rotate` or a reconnect.
- `/connect leave` forgets the network, so the device can join another by code; `/connect leave <domain>` refuses a domain it is not on. `/connect knocks` reads the joined directory.
- Bare `/connect` in a second window of the same workspace (no daemon) opens the Connect screen instead of printing status text. Only the first window can make a code, list requests or decide them, so the second shows the network and this device, says `another window in this workspace runs the network; use /connect there`, and offers no code or keys. `/connect code` there says the same line, and a workspace with no network shows `network none` instead of a code form whose submit could only fail. An attached window whose daemon lost the workspace to a single-process window gets the same screen from the daemon instead of the daemon's status text.
- `/connect accept` and `/connect reject` name the device (`accepted ana-laptop. it is now a trusted device on home-net.`) and never print a receipt; two requests with one name are told apart by the start of the fingerprint. `/connect allow` and `deny` print device names, and say they have no effect under `open` trust.
- The knock review has a selection (up/down) and `a`/`r` act on the marked row; a held key cannot decide the next knock or request unseen. One malformed knock no longer hides the others.
- The code form starts a network on the directory when the code is left empty (first device), prints `joined <network> as <device>. trust: <level>` after an approved join, and keeps a pasted hyphenated or eight-letter domain out of the masked code field. Accepting a join no longer offers an OAuth login; each device runs its own `/login`.
- Offline devices are not listed while the relay is unreachable or for peers the relay shows online; an accepted stranger is listed like any device once it has a name. `hub_capture`, `hub_spawn` and `hub_stop` refuse an `agent@device` target with a hint to message it.
- Accepting a knock or a join from a key that already has a name is refused (`could not accept <name>: this device is already on your network as <old-name>`) instead of renaming it, and a name held by this device, a peer or any device in the live roster is refused. A failed accept puts approvals, names and trust back exactly as they were. The joining device now records the issuer's name too.
- Knock and join-request rows fit 60, 80 and 120 columns whatever name or introduction a sender chooses: a device name (up to 63 characters) shows whole and is cut with `…` only when its row is wider than the terminal, keeping `wants to join`; tabs are dropped, wide characters are measured by their width and the `[a]ccept [r]eject` hint is never cut. The joined line on the joining device is cut by width with `…` too, not silently.
- `/connect authorize`, `send`, `task` and `cancel` take and print `agent@device`, not a relay address. A full approval table says so instead of "try again". The relay's contact route index sets its expiry atomically and no longer unlists a device that just reconnected. `repr()` of a request, offer or outcome never shows a key, receipt id or join code.
- A `hub_msg` to an `agent@device` on the roster reports `sent to <agent@device>` instead of `warning: ... is not online`, so the agent no longer tells the human the peer is offline or resends. A target off the roster reports `unknown agent@device: run /connect status to see who is online`, a refusal by the receiving device is reported as a refusal, and a repeated identical send says it was not sent again. `/hub msg`, `@agent@device` and `/hub wake` follow the same rules.
- One `<hub_msg>` tag in a model response runs once. The LLM core registered every registry tool's tag with the response parser before plugins started, so a plugin's own tag for the same name stacked on the generic one: one tag parsed into two tools and `hub_msg` ran twice (`sent to ...`, then `not sent again: ...`, then a warning from the model). A tag name registered again now replaces the earlier entry. `hub_broadcast`, `vault_write` and the other hub tags shared the same doubling.
- A `hub_msg` that starts a request to an `agent@device` now says in its result that the reply arrives by itself as a hub message and that the agent should end its turn unless it has other local work, with no status check, capture or second message. An answer on a request the agent received still reports `sent to <agent@device>`. The asking agent no longer polls and resends, which made the answering device run the task twice.
- `kollab --hub msg agent@device "text"` prints the answer to its own request. The request's thread id travels in the relay payload and the answering agent echoes it, so an older or duplicate answer from the same agent is never printed as the reply to a newer request.
- The `ContactReviewAltView: missing 3 required positional arguments` error at every launch is gone; the knock review builds without callbacks like the Connect screen does.
- Hub XML tags take their attributes in any order and either quote style: `<hub_msg wait="true" to="lapis">` runs and is hidden instead of staying on screen as raw text, and `<hub_broadcast scope="network">` parses.
- `wait="true"` on `hub_msg` / `hub_reply` (and the idle phrases that set it) ends the sender's turn once the send succeeds; a rejected send, a send to nobody, or a failed tool in the same reply keeps the turn going.
- `hub_cron_add` can target another machine: `to="agent@device"` on the tag, `to` on the native tool, sent through the same network path as `hub_msg` (leaving `to` out still means the sender itself, and the tag's old `target` attribute, which nothing read, now counts as `to`). A malformed target is refused when the job is added, an offline device is accepted, a fire that is not delivered is logged with its reason and shows as `last fire failed` in `hub_cron_list`, and a job whose device is not on the network is dropped with a logged reason instead of failing on every interval.
- A reply belongs to the request whose turn sent it, not the oldest request from that agent. When a remote request starts an agent turn, every `hub_msg` from that turn to the requester goes on that request's thread, so an interim "on it" and the answer both land there and two overlapping requests never cross, whatever order the agent handles them in; the relay hands the model one request at a time, and when the turn ends the runtime (never the model) sends the requester an end-of-turn frame that no screen shows. `kollab --hub msg agent@device "text"` prints every reply on its own thread as it arrives and exits 0 when that turn ends (1 with the error if the turn failed, or after its 600 s wait), and a reply to a shell request no longer wakes the asking agent's model. Both devices need this build.
- `hub_cron_add` reports `bad interval: ...` and `usage: ...` as a failure with that wording instead of a success. A `hub_msg` sent to a relay address draws `sapphire -> infra@home-server` in the outgoing box (`a remote agent` when the roster has no such agent), never the address. `/connect authorize` prints `expires at 14:05` in local time instead of a unix epoch.
- Agent network config sync and knocks: leaving after a room rotate now ends the old primary's hold on this device's settings, a knock from a device that already joined with a code is refused instead of rebinding it, keyring keys the primary cannot read are kept on secondaries instead of deleted, and a file whose only change is its exec bit now syncs.
- Mesh links between network members stay valid across refreshes and renewals. One device of a pair, the one with the lower peer id, writes the link on the secure session it opened, with a fresh timestamp at each renewal and revisions that only move forward; the other accepts it on the session it arrived on, and every device checks a link by whether the session it names is still open. A gossiped or forwarded record or link older than the one a device holds is skipped instead of failing the exchange, a transit hop forwards the links it was handed, and a request whose mesh route fails falls back to the relay when the peer is on it. Links used to break about a minute after they formed ("peer link proposal does not match the live session", "invalid lifetime", "revision rollback", "peer route delivery failed").
- A reply always goes on the thread of the request its turn is handling, and a send to an agent that is no longer listed says `unknown agent@device`.
- A cron job aimed at `agent@device` now draws one dim line per fire (`cron <id> -> infra@home-server`) instead of a message box every time it runs, refs #121
- A workspace whose settings sync was taken over by another workspace on the same machine (the latest join owns the machine's managed-config record) now says so once, `Settings sync is off in this workspace: another workspace on this machine joined a different network last.`, instead of refusing every bundle silently, refs #121

## [0.10.7] - 2026-09-28

### Fixed

- Relay conversations no longer fill the transcript with errors and filler. A
  receiving agent that tries to send its answer with `hub_msg` gets an
  actionable error (its final reply returns automatically; only one
  `kind='question'` is allowed). Progress events are shown to the human without
  starting a sender model turn, and turns started by relay events no longer
  offer tools they would refuse.

## [0.10.6] - 2026-09-28

### Fixed

- An attach client that cannot reach its daemon at startup (a stale socket
  after the daemon died) now explains how to start the agent and exits, instead
  of leaving a window with no agent behind it.
## [0.10.5] - 2026-09-28

### Fixed

- `/upgrade` in the default daemon mode now stops and reaps its daemon and
  relaunches the command you started Kollab with, so the restarted window gets
  a fresh daemon on the new version. Before, it re-attached to the daemon it
  had just stopped and left a window that could not reach its agent. `/connect`
  in such a window now says it has no daemon connection.

## [0.10.4] - 2026-09-28

### Added

- Added `/quit` (alias `/exit`), which exits Kollab the same way a second
  Ctrl+C does.

### Fixed

- The session prompt reads the host name with `uname -n`, so hosts without a
  `hostname` binary (Arch Linux) no longer log an error on every prompt render.

## [0.10.3] - 2026-09-28

### Fixed

- Enrollment codes are redacted from every log line, and a code typed into any
  `/connect` subcommand is refused with a pointer to the private form. Unknown
  `/connect` words report "unknown subcommand" (with a suggestion) instead of a
  DNS error, the private code form says how to get a code, `/connect <domain>`
  points to `/connect offer`, and `/connect help` lists every subcommand with
  its description.
- `/upgrade`, `kollab --upgrade` and `kollab --update` now upgrade the install
  that is running (uv tool, pipx, Homebrew, or the current environment's pip,
  falling back to `uv pip` when pip is missing) instead of whichever installer
  is on PATH. `/upgrade` no longer crashes after updating, and says "already
  up to date" instead of restarting when nothing changed.
- The command menu now lists every `/connect` subcommand with its arguments;
  typing `/conn` previously showed only the bare command. `/connect answer`
  and `/connect networks <extra>` are routed instead of being rejected with the
  enrollment-form message.

## [0.10.2] - 2026-09-28

### Added

- Added voice mode (`/voicemode`, aliases `/vm` and `/voice`) on macOS: local
  transcription, spoken and display response channels, and a local classifier
  deciding what to speak. Voice starts only on an explicit command; the audio
  runtime and models install on demand and add nothing to the base install.
  Echo cancellation is not implemented and the compact status row is still
  being repaired.
- Web UI: pick an agent bundle and workspace when starting a session, manage
  profiles, and manage MCP servers from the toolbar. The engine applies the MCP
  servers requested for a new session.

### Fixed

- `/connect accept` explains when a relay restart or reconnect ended the relay
  session a code was issued under, instead of reporting "unauthorized". The
  relay keeps no durable state, so a restart ends in-flight enrollments; create
  a new code (#94).
- `kollab relay run` workers stop themselves when their supervisor dies without
  a clean stop, and startup waits out a previous owner's lease instead of giving
  up after 45 seconds (#97).
- Goals: pasted text expands in command mode, `goal_report` accepts multi-line
  evidence references and reports detail when no turn is in flight, and pausing
  discards held completions.
- Tool output budgets never return an empty result, input typed during startup
  is re-queued instead of dropped, compaction keeps coordination state across a
  restart, and the command executor shows handler errors instead of only
  logging them.

### Documentation

- Exact walkthroughs for hosting kollabor.ai, self-hosting, enrollment by code,
  discovering agents, approving contact, conversing and revoking access.

## [0.10.1] - 2026-09-28

### Fixed

- Any agent connected to a relay can create enrollment codes for its own private
  network. The new device verifies the issuer's key through the code exchange
  instead of trusting only the discovery domain's publisher, so codes are no
  longer limited to the kollabor.ai operator (#89).
- The device-code screen says what to check when a code cannot be created.
- A code typed or pasted into the domain field of the private `/connect` form
  moves to the masked code field instead of showing in clear text.

## [0.10.0] - 2026-09-28

### Added

- Added agent conversations over the relay. `/connect send` delivers one exact,
  human-authorized request to a connected agent in another workspace. The
  receiving agent runs it through its normal model and tool pipeline in its own
  workspace and replies on the same thread. Follow-ups, questions and answers,
  progress, deadlines and `/connect cancel` are supported.
- Added `/connect allow` and `/connect deny` for incoming authority per local
  agent, `/connect grants`, `/connect authorize` and `/connect withdraw` for human
  sending grants, and `/connect agents` and `/connect task` for inspection.
- Added private device enrollment by code. `/connect offer` creates a one-device,
  five-minute code; the new device enters it with bare `/connect`; the issuer
  decides with `/connect requests`, `/connect accept` or `/connect reject`. An
  accepted device receives a scoped conversation credential, a room invitation
  and, when a supported provider profile is active, its settings and one provider
  credential in a device-sealed bundle.
- Added an opt-in peer mesh runtime: LAN locator discovery, direct TLS peer links
  and signed, bounded forwarding with per-hop consent. It is off by default.
- Added `scripts/relay/verify_agent_conversation.py`, a live two-host acceptance
  harness for relay conversations.

### Security

- Receivers reject unauthorized, revoked, replayed and wrong-workspace requests.
- Turns started by an incoming relay reply, result or question cannot run tools.
  A remote agent's question waits for the human instead of being answered by the
  model.
- Enrollment grants no workspace or tool permission. Network revocation does not
  revoke a copied provider credential at its provider.

### Networking scope

- A live two-host run (macOS and Linux through kollabor.ai) passed conversations,
  follow-ups, questions and answers, cancel, reconnect and the receiver
  rejections. Live code enrollment and the peer mesh's direct, LAN and multi-hop
  paths are not yet proven across hosts.
- Only the agent holding the discovery domain's coordinator key can create
  enrollment codes. On kollabor.ai that is the beacon operator. Issuing codes from
  any trusted agent for its own private network is not implemented yet.

## [0.9.0] - 2026-09-27

### Added

- Added `/connect` with signed domain discovery, persistent publisher pins, private
  invitation files, explicit peer approval, and encrypted cross-network ping/pong.
  Attached clients use the daemon's connection and identity.
- Added `kollab relay run` and `kollab relay serve` to the app. The managed runtime
  supervises workers and an optional Valkey sidecar, with shared admission limits,
  expiring presence, ciphertext routing, bounded queues, and readiness reporting.
  Client and relay Python dependencies are included in `pip install kollab`.
- Added optional A2A workspace read/create receivers with signed device membership,
  scoped grants, local permission checks, and authenticated task results.
- Added the Kollab development skill and relay maintenance guide to the bundled
  maintainer agent.

- Added the `/goal` command: durable session goals with bounded continuation,
  a SQLite-backed goal store with crash recovery, model-declared completion
  gated on runtime-recorded evidence via the `goal_report` tool, and
  conversation identity that survives `/resume`.
- Made context compaction observable: the compacted-history summary carries a
  durable metadata marker, and the terminal transcript and Web UI history and
  trajectory views render compaction events with round and message counts.

### Fixed

- Explain invitation file, ownership/permission, publisher verification, and
  self-invitation failures without displaying invitation contents. Quoted paths
  work, and joining your own invitation no longer closes the existing connection.
- Keep public discovery identities separate from workspace coordinator elections
  and local message-routing records. Discovery does not grant workspace access.
- Use exclusive file creation so a concurrent writer cannot be overwritten after
  the existing-file check.

- Opened the goal HUD drain gate on fresh conversations so goal turns render
  when no prior turn has completed.

### Networking scope

- Relay clients exchange approved encrypted presence responses. General agent
  conversations and remote model/tool execution over the relay are not implemented.
- Private invitations must be transferred to the receiving computer. The receiving
  file must be owned by its user and have private permissions. Device keys and
  existing discovery pins are preserved across app updates.

### Documentation

- Documented the release flow and tag/publish consistency guard in `CLAUDE.md`.

## [0.8.1] - 2026-09-18

### Fixed

- Backported the OAuth login fixes missed in v0.8.0: OpenAI device-code polling
  now respects its configured timeout and reports progress while authorization
  is pending.
- Default OpenAI API and ChatGPT OAuth profiles to `gpt-5.6-luna` (v0.8.0
  shipped with the older Sol default).
- Keep missing-model profile lookups quiet during repeated status-bar renders.

## [0.8.0] - 2026-09-18

### Added

- Added multimodal image input for the terminal and Web UI, with explicit model
  capability checks and provider-aware request translation.
- Added hosted image generation for supported models, including streamed image
  reconciliation, persisted session-scoped artifacts, and `/artifact open
  <media_id>` actions that open generated images through daemon-owned state.
- Added Hub model lifecycle announcements and startup status messages for clearer
  agent coordination.

### Changed

- Routed pasted slash commands locally and expanded attach-mode artifact actions
  through daemon state.
- Changed the default permission mode to `trust_all`; use `/permissions default`
  or `/permissions confirm_all` for approval-gated execution.

### Fixed

- Hardened generated-image streaming with bounded SSE payloads, raw image-content
  redaction, and success reporting only after an artifact is persisted.
- Fixed Web UI live session refresh/discovery, polling timer cleanup, default
  loadout startup overrides, and session deletion fallback.

### Documentation

- Documented the provider-neutral goal command/harness contract and the separate
  capability boundaries for image input and generation.

## [0.7.3] - 2026-08-25

### Added

- Added a Web UI trajectory view and richer session workflows, including model-aware
  session navigation, command palettes, and improved tool-call presentation.
- Added Hub agent mentions in terminal and Web UI composers, routing operator messages
  directly to selected agents through the daemon-owned state service.

### Changed

- Made the system-prompt prefix byte-stable while moving per-turn volatile context to a
  separate rail, improving prompt-cache reuse without losing live context.

### Fixed

- Fixed setup onboarding to run when providers are unavailable, attach-mode MCP widgets
  to read daemon state, provider-facing error messages, custom-provider empty choices,
  and OpenAI OAuth origin handling.

## [0.7.2] - 2026-08-22

### Fixed

- Corrected token usage accounting for streamed responses, covering OpenRouter
  usage chunks, the API communication service tally, and provider transformers.
- Fixed remote widget state synchronization in attach mode so status widgets
  reflect the daemon's live state (state refresher, snapshots, and local state).

## [0.7.1] - 2026-08-21

### Changed

- Aligned root and workspace package metadata and minimum dependency constraints
  before tagging the release.
- Updated the default status layout to show stats, deep-thought, and MCP widgets
  in the fourth row.

### Added

- Added `--model` and `--effort` launch flags, which layer over whatever `--llm`
  selected and also work on their own against the already-active profile. Both
  apply in memory only and cross the client-daemon boundary in attach mode.

### Changed

- **Breaking:** replaced `--profile` with `--llm`. The old flag is removed, not
  aliased; using it now names its replacement instead of failing obscurely as an
  unknown CLI command.
- Merged live provider catalogs into `/llm`, so models a provider only reports
  at runtime (OpenRouter, any OpenAI-compatible endpoint) appear in the picker.
  Providers with no rows now say why instead of vanishing from the list.
- Added aggregate tool-output budgeting with lossless managed `.output` artifacts,
  bounded previews, history packing, and non-empty request recovery.
- Normalized legacy `git` tool requests to the supported `terminal` execution
  path and documented wildcard tool access for bundled agent profiles.
- Corrected ChatGPT OAuth Responses handling so unsupported output-token
  overrides are omitted while reasoning effort remains available.

### Fixed

- Fixed the Gemini provider rejecting every tool-calling request. Tool schemas
  were sent as raw JSON Schema; Gemini validates a restricted OpenAPI 3.0
  subset and 400'd on `additionalProperties`.
- Fixed streamed Gemini tool calls being dropped before execution — they were
  emitted without an id and with a Python dict repr in place of JSON arguments.
- Fixed Gemini token counts and cost always reading zero, caused by usage being
  read only after the branch that returns on the chunk carrying it.
- Fixed provider error bodies being discarded, which reduced every 4xx to a
  bare status line and left the context-length check unreachable.

## [0.6.1] - 2026-08-02

### Added

- Added `kollab --web-ui`, which launches or reuses the local engine and serves
  the assistant-ui browser client on port 8080.
- Added the assistant transport, session-owned engine state, profile/MCP/hub
  routes, and browser permission flow for the local web runtime.
- Added the model registry with context, pricing, capability, sampling, and
  reasoning-effort metadata, plus `/llm` loadouts.
- Added on-demand `tool-search` and `tool-load` discovery for built-in and MCP
  tools, web search/fetch tools, and workspace/MCP reload tools.
- Added actionable task checkpoint nudges and `task_snooze` for quieting
  reminders without hiding active work.

### Changed

- Attach mode now routes state through the daemon-owned state service and supports
  the current multi-context/engine workflow.
- Bundled hub coder identities expose the full registered tool set by default.

### Fixed

- Preserved assistant transport state across web UI turns and cleaned up child
  engine/UI processes on exit.
- Fixed profile timeout units, orphaned tool results, retry cancellation, and hub
  wake-cache ordering.

## [0.5.22] - 2026-07-21

### Added

- Added the `/setup` onboarding wizard, with `/onboard` and `/wizard` aliases,
  to guide new users through provider and profile configuration.
- Added engineering-discipline guidance to the bundled agent base prompt.

### Changed

- Updated README, FAQ, and profile documentation to describe wizard-created
  profiles and the current built-in profile behavior.

## [0.5.20] - 2026-07-03

### Added

- Added remote hub endpoint hardening, trust/DNS documentation, and test
  coverage for off-box hub message delivery.
- Added a stability review audit covering startup, shutdown, plugin lifecycle,
  event hooks, terminal cleanup, MCP policy, config reload, hub background work,
  and subprocess cleanup.

### Changed

- Refined bundled agent operating guidance for code review, dirty worktrees,
  tool workflow, resource use, final reminders, and session-log handling.
- Timer-gated scratchpad injection to reduce repeated background context
  overhead during long hub sessions.

### Fixed

- Fixed hub endpoint advertising so a failed remote listener is not published,
  and co-located peers prefer the local socket path.
- Fixed stale pending hub replies so they expire automatically and show age in
  the hub display.
- Fixed attach-mode session/status display so the status bar prefers the
  daemon's real session instead of the local client shadow session.
- Fixed `/compact` display and preview in attach mode so message and token
  counts come from daemon-backed state instead of empty local history.

## [0.5.16] - 2026-06-21

### Added

- Durable per-agent message inbox: messages for an offline agent are persisted and
  replayed as a single bounded catch-up block when it reconnects, so agents no longer
  lose project context after a crash or restart.
- Hub spawn guard: refuses to start a second live process for an identity that is
  already running, preventing duplicate agents that double-execute actions.

### Fixed

- `kollab --hub stop` (and `/hub stop`) now escalate to SIGKILL, so a wedged or
  unresponsive coordinator is reliably reaped instead of squatting its slot.
- Fixed a lost-wakeup race where user messages enqueued while the hub was mid-continue
  were never drained, leaving the agent idle with the message unprocessed.
- Addressed, actionable hub messages now always wake an idle recipient; the wake dedup
  no longer suppresses a deliberate re-send (only true redelivery and broadcasts dedup).
- AltView panel backgrounds now paint fully to the panel edges instead of leaking the
  terminal default background.
- AltView exit-state cleanup always restores the main UI on every exit and failure path,
  and no longer crashes on a view without metadata.

### Security

- Spawned subprocesses no longer inherit the full parent environment by default;
  sensitive variables (API keys, tokens, secrets) are filtered unless explicitly passed.

## [0.5.15] - 2026-06-20

### Added

- Added a `/updates` AltView with version numbers on the left and release notes
  on the right, backed by the local changelog so installed and source users can
  browse recent changes in-terminal.

### Fixed

- Fixed kollab crashing on launch with a raw, confusing traceback when obsolete
  Python-2-era backport packages (`typing`, `dataclasses`, `asyncio`, `pathlib`,
  `uuid`, ...) are installed in the same interpreter and shadow the standard
  library. A startup preflight now detects the offending packages before any
  kollab import and prints an actionable message naming them and the exact
  `pip uninstall` fix, then exits cleanly instead of dumping a traceback.

### Changed

- Recommend `pipx install kollab` as the primary install method in the README so
  kollab lands in its own isolated virtualenv and avoids polluted-interpreter
  startup failures; `pip install kollab` remains supported as a secondary option.

## [0.5.14] - 2026-05-28

### Fixed

- Fixed provider requests so local-only agent HUD metadata stays in local logs
  but is stripped before payloads are sent to OpenAI, Anthropic, OpenRouter, and
  custom provider APIs.
- Fixed OpenAI Responses tool output handling so oversized function outputs are
  capped before hitting the API's per-string payload limit.
- Fixed installed `kollab` environments that use keyring-backed profile secrets
  by declaring the `keyring` dependency in `kollabor-ai`.

## [0.5.13] - 2026-05-27

### Added

- Added a full-screen `/mcp` manager for browsing configured and available MCP
  servers, toggling servers, adding template servers, deleting configured
  servers, testing, reloading, filtering, and inspecting tools/env status.
- Added a global MCP on/off toggle inside the `/mcp` manager and wired startup,
  reload, and native tool loading to respect the global setting.

### Fixed

- Fixed AltView flicker by making fullscreen views render on input, data, or
  resize by default while live/animated views explicitly opt into timer redraws.
- Fixed slash command submenu contrast so dim rows remain readable over the
  terminal background.
- Fixed long `/config` menus so the selected setting stays visible while
  scrolling through off-screen items.

## [0.5.12] - 2026-05-26

### Fixed

- Fixed installed-package plugin discovery so packaged plugins and bundled
  assets resolve correctly outside a source checkout.
- Fixed permission SSE metadata so terminal command details are preserved for
  approval prompts and tool-start events.
- Fixed terminal command risk assessment so command-specific checks run before
  generic terminal tool defaults.
- Fixed 256-color terminal rendering so graphite input backgrounds do not fall
  back to navy blue.

### Changed

- **Agent Skills:** `Skill` loading now strictly enforces the published directory
  contract (`SKILL.md` frontmatter; `name` matches folder; required `description`;
  validated `metadata`, `allowed-tools`, and `compatibility`). Bundled caches bump
  to `CACHE_VERSION` 3 — delete `~/.kollab/agent_metadata.cache` only if stale
  agent metadata causes issues after upgrade.

## [1.0.1] - 2026-05-05

### Fixed

- Fixed first-run installed environments so bundled agents and skills are seeded
  from packaged wheel data, preventing fallback system-prompt mode.
- Fixed the installer `uv` path to install a persistent `kollab` tool instead
  of only running the command ephemerally.
- Added Docker smoke coverage for first-run bundled agent prompt seeding.

## [1.0.0] - 2026-05-05

### Added
- Added an installed-user Docker runtime validation guide and helper script/smoke tests
  for reproducible UI checks.

### Changed
- Expanded session and agent launch plumbing so agent profiles and MCP context pass
  through consistently for spawned agents and per-session tool execution.
- Hardened workspace handling for file operations and session/profile paths to reduce
  invalid or unsafe path usage in stricter trust modes.

### Fixed
- Improved conversation persistence by tracking file interactions and improving tool
  result metadata (`tool_result` + `tool_use_id`) in transcripts.
- Fixed `wait_for_user` parking behavior so bookkeeping tools can park cleanly without
  forcing unnecessary follow-up turns.
- Improved `/save` robustness with a fallback path when state services are not yet
  available during startup or attach mode.
- Fixed development entrypoint imports so `python main.py` prefers the current
  checkout's workspace packages instead of stale editable installs.

## [0.5.7] - 2026-04-24

### Added
- `kollabor-engine` README now documents the local-daemon service model, current
  API surface, known gaps, and service-hardening roadmap.
- Package READMEs now use the same current-role, architecture, known-gaps, and
  roadmap structure across `kollabor-ai`, `kollabor-agent`, `kollabor-config`,
  `kollabor-events`, `kollabor-plugins`, `kollabor-rpc`, `kollabor-tui`, and
  `kollabor-webui`.
- `kollabor-rpc` is now included in root package dependencies and the
  tag-publish workflow so attach-mode RPC installs and releases with the rest
  of the workspace.
- Pre-compaction checkpoints for auditability.
- Web attach protocol spec for structured event streams for programmatic clients.

### Changed
- Architecture docs reorganized into canonical taxonomy: archive, decisions,
  records, reference, and RFCs.
- CLI docs now expose the `--as` flag, document environment variables, and remove
  stale `-d` references.

### Fixed
- Session widget state refresher now populates `session_id` in daemon/attach mode.
- Dynamic prompt rendering skips `bd` commands when `.beads/` is absent, avoiding
  roughly 10 seconds of daemon startup delay.

## [0.5.6] - 2026-04-21

### Added
- **Phase 4.5 Daemon Transparency**: Unified state_service abstraction for local and attach mode
  - StateService protocol (LocalStateService, RemoteStateService) for daemon state access
  - Multi-context daemon architecture with `--context NAME` launch flag
  - ConversationContext + ContextRegistry with snapshot-and-swap (preserves list identity)
  - RPC methods: state.set_agent, state.activate_skill, state.deactivate_skill,
    state.set_system_prompt, state.clear_agent, state.restart_session,
    state.resume_conversation, state.enable/disable_mcp_server, state.test_mcp_server,
    state.get_mcp_tools, state.clear_session_approvals, state.clear_project_approvals,
    state.list_project_approvals, state.list_contexts, state.get_active_context,
    state.create_context, state.attach_to_context, state.archive_context,
    state.get_hub_status_text, state.get_hub_whoami_text, state.get_hub_work_text
  - CLI launch-flag routing: `--profile`, `--agent`, `--skill`, `--system-prompt`,
    `--save`, `--context` cross attach-client boundary
  - Thin-client attach path: RemoteStateService registered first in attach mode
  - 4 JSON tmux specs for local-mode smoke testing (18/18 assertions pass)

- **Tool Permission System**: Comprehensive approval and permission system for controlling tool execution
  - 4 approval modes: CONFIRM_ALL (default), DEFAULT, AUTO_APPROVE_EDITS, TRUST_ALL
  - Risk-based assessment with pattern matching for dangerous commands
  - Inline permission prompts in thinking/executing area (no modal interruption)
  - Single keypress responses (a/s/d/c/t/A) for quick approval/denial
  - Session-scoped approvals with "remember this session" option
  - `/permissions` command for runtime mode switching and statistics
  - Color-coded risk levels (HIGH=red, MEDIUM=yellow, LOW=green)
  - Event bus integration at SECURITY priority (900)
  - Statistics tracking (auto-approved, user-approved, denied, blocked)
  - Configuration via `kollabor.permissions.*` with defaults
  - 8 core files: manager, risk_assessor, hook, models, config, response_handler, UI component
  - Complete specification in `docs/specs/tool-permission-system-spec.md`
- **Hub socket authentication**: peer credentials, Ed25519 handshake support, and
  coordinator gatekeeper permissions.
- **Hub console and memory**: live socket feed, project/global crystal memory
  stores, project-scoped hub config, and auto-grant/revoke environment queue
  producers.
- **Context and notification services**: context-service hub bridge MVP,
  file-read deduplication metadata, and agent notification queue/render/tag/wake
  phases.
- **Parser and daemon defaults**: daemon mode is now the default for interactive
  sessions, with parser protections that strip code/backtick blocks before XML
  tag scanning.

### Changed
- /agent, /skills, /restart, /mcp, /permissions, /resume, /status all route through state_service
- Status widgets prefer remote_state when available (attach mode shows daemon state)
- Legacy fallbacks removed from step 7 commands (state_service is the only path)
- HubStatusView migrated to read-only state.get_hub_status_text RPC
- Hub auth defaults off by default for raw-client compatibility.
- Hub loop thresholds, coordinator breakthrough behavior, and default configs now
  wire to actual runtime behavior.

### Fixed
- `--profile openai-oauth` silently fell back to default (ProfileManager init race condition)
- MCP plugin passed app=None to MCPCommandHandler (masked by pre-step-7 fallback)
- /resume broke conversation_history list identity (clear+extend preserves cached references)
- /hub -h / --help / no-args now prints hub help instead of generic help
- Config defaults that were missing or wired to the wrong keys.
- Slash command handler smoke coverage for all 21 command handlers.
- Parser code-span preservation for file operations and tool outputs.
- Attach-mode tool-call boxes when assistant content is empty.
- Context-service event-loop capture and file_path propagation in tool metadata.
- Hub bridge initialization races, waiting-state scheduling, vault scoping, and
  crystal tag parsing edge cases.
- Text utility regression by restoring `generate_ngrams` and relevance scoring.

### Deferred to Phase 4.6
- /login OAuth browser split (client runs browser, daemon stores token)
- /hub msg, broadcast, stop, spawn, org cross-process messaging from attach client
- /terminal view, attach streaming transport
- /sub completion notification (MessageInjector deprecation)
- /resume modal, search, branch, filter paths
- MCP hot-reload on config change
- Full thin-client refactor (skip ProfileManager/AgentManager in attach)
- 176-reference hot-path rewrite (option A of multi-context)

## [0.5.5] - 2026-04-16

### Added
- OpenRouter model metadata fetcher with dynamic max_tokens capping
  - Fetches /api/v1/models on provider init, caches with 1h TTL
  - Caps max_tokens to fit within model context_length (prevents 400 errors)
  - Graceful fallback if metadata fetch fails
- Spawn identity resolution from pool — three modes
  - By identity: `name="lapis"` uses pool's agent_type
  - By agent_type: `name="coder"` picks next available gem from pool
  - Explicit: `name="lapis" type="research"` overrides type
  - Returns resolved identity immediately (no discovery/polling)
  - Returns "already online" if identity is running
- Pool identity schema now supports agent_type and skills fields on all 24 gems

### Fixed
- hub_capture returns entries newest-first (was oldest-first)
- hub_capture truncation raised from 2k to 10k chars (old entries consumed entire budget)
- Slash command /hub spawn now parses identity=X and type=X kwargs
- Removed dead code from openrouter_model_info.py
- Fire-and-forget warm_cache() task now properly handles exceptions

## [0.4.18] - 2026-01-19

### Added
- **Interactive Widget Status System**: Complete rewrite of status bar with interactive, customizable widgets
  - Tab-based navigation mode with widget selection and activation
  - Edit mode for adding/removing/configuring widgets
  - Inline editors (slider, text, dropdown) for real-time value adjustment
  - Script-based widget support for zero-code customization
  - Widget picker modal for easy widget discovery and selection
  - Visual effects (shimmer, pulse, ultra-shimmer) for enhanced UX
  - Widget background color customization (5 color options)
  - 18+ built-in widgets (cwd, profile, model, git, tmux, skills, tasks, etc.)
  - Undo/redo support (Ctrl+Z) for widget layout changes
  - Quick jump to widgets with digit keys (1-9)
  - Comprehensive documentation (user guide, technical reference, developer guide)

- **Parallel Agent Spawning**: Non-blocking agent execution for improved performance
  - Agent spawning now runs in background tasks
  - Reduced spawn time from 50s to 14s for 5 agents (3.6x speedup)
  - Message injection when background agents complete
  - Pipe mode waits for full initialization

- **Widget Documentation**: Three comprehensive documentation files
  - User guide with quick start and examples
  - Technical reference with architecture and API
  - Developer guide with design system integration

### Fixed
- **Widget background color rendering**: Fixed alignment issues where colored widgets showed visible gaps
- **Toggle widget persistence**: Toggle states now properly persist across application restarts
- **Label widget inline editing**: Fixed context attribute and row indexing bugs
- **Widget color toggle**: Fixed row background painting over widget colors
- **Edit mode selection state**: Fixed selection state when exiting edit mode to status focus
- **Script widget registration**: Script widgets now properly discovered and registered in widget picker
- **Agent orchestrator blocking**: Fixed blocking `asyncio.sleep()` calls that froze UI during agent spawning
- **Modal controller state**: Improved state management in modal controller

### Changed
- Replaced fixed 3-area status system with flexible multi-row widget layout (up to 6 rows)
- Widget layout configuration now persisted to config file
- Navigation mode has three modes: INPUT, STATUS_FOCUS, EDIT
- Modal system integrated with widget navigation

### Technical Details
- New files: `kollabor/io/status/widget_registry.py`, `layout_manager.py`, `navigation_manager.py`, etc.
- Script widget manager with metadata parsing and execution engine
- Inline editor service with consistent API across all editor types
- Widget interaction handler for keyboard routing and modal display
- Tmux verification tests for all widget features
- Known issues tracking system with templates

## [0.4.11] - 2025-12-27

### Added
- **Terminal color capability detection**: Automatic detection of terminal color support with intelligent fallbacks
  - Detects TRUE_COLOR (24-bit), EXTENDED (256-color), BASIC (16-color), and NONE modes
  - Checks `COLORTERM`, `TERM_PROGRAM`, and `TERM` environment variables
  - Apple Terminal.app correctly detected as 256-color only
  - `KOLLAB_COLOR_MODE` environment variable for manual override
  - Programmatic control via `set_color_support()` and `reset_color_support()`

### Fixed
- **Color rendering on non-true-color terminals**: Gradients and colors now display correctly on terminals that don't support 24-bit true color
  - `ColorPalette` now uses dynamic color code generation based on terminal capabilities
  - `GradientRenderer` automatically uses 256-color fallback
  - Plugin `ColorEngine` updated to use color support detection
  - Fixes broken gradient display in macOS Terminal.app and other 256-color terminals

### Technical Details
- Refactored `ColorPalette` class to use metaclass for dynamic color generation
- Added `ColorSupport` enum, `get_color_support()`, `rgb_to_256()`, and `color_code()` utilities
- Updated `status_renderer.py` and `message_display_service.py` to use dynamic colors
- Updated `plugins/enhanced_input/color_engine.py` with fallback support

## [0.4.10] - 2025-12-10

### Fixed
- **Slash command menu filtering prioritization**: Command menu now correctly prioritizes commands whose names start with the typed query over alias matches
  - When typing `/t`, `/terminal` now appears first (name match) instead of `/save` (alias "transcript" match)
  - Improved user experience with more intuitive command filtering and selection
  - Filtering now uses three-tier priority: name prefix → alias prefix → substring matches
  - Cursor selection automatically focuses on the most relevant match

### Technical Details
- Enhanced `SlashCommandRegistry.search_commands()` to separate matches into priority tiers
- Name matches have highest priority, followed by alias matches, then substring matches
- Each tier only returns results if no higher priority matches exist

## [0.4.9] - 2025-12-10

### Fixed
- **Tmux plugin keyboard shortcuts**: Fixed Option+Left/Right (Alt+Arrow) key handling in tmux plugin
  - Previously these shortcuts were not properly captured in tmux sessions
  - Now correctly handles Alt+Left/Right for word-level navigation
  - Improved tmux session viewing experience with proper keyboard support

### Added
- Alt+Arrow keyboard shortcuts support in tmux plugin for enhanced navigation

## [0.4.8] - 2025-12-08

### Added
- **Windows compatibility**: Full support for Windows operating systems
  - Platform-specific abstractions for terminal operations
  - Inline platform checks for cross-platform compatibility
  - Windows-specific terminal handling and input processing
  - Tested on Windows, macOS, and Linux

## [0.4.7] - 2025-12-08

### Fixed
- **Save command payload preservation**: `/save` command now preserves exact API payload structure
  - Conversation history saved with original message format
  - Tool calls and function results properly serialized
  - Metadata preserved across save/load cycles

## [0.4.6] - 2025-12-08

### Changed
- Dynamic version display system
- Improved OS classifier metadata in package configuration

## [0.4.5] - 2025-12-08

### Added
- Render cache optimization with smart invalidation
  - Cache automatically invalidates when clearing active display areas
  - Improved terminal rendering performance
  - Reduced screen flicker during updates

### Fixed
- Duplicate plugin instance initialization prevented
- Task concurrency increased for better performance
- Default timeout removed for more flexible operation

## [0.4.0] - 2025-12-08

### Added
- **Dynamic system prompt rendering with `<trender>` tags**
  - System prompts can now include dynamic content that renders at runtime
  - `<trender type="project_tree">` - Include project directory structure
  - `<trender type="file_list" pattern="**/*.py">` - Include filtered file lists
  - `<trender type="file_content" path="README.md">` - Include file contents
  - `<trender type="timestamp">` - Include current timestamp
  - Comprehensive documentation in `docs/features/dynamic-system-prompts.md`
  - Test suite for prompt rendering functionality

- **Environment variable configuration system**
  - Complete configuration via environment variables (see `ENV_VARS.md`)
  - API configuration: `KOLLAB_API_ENDPOINT`, `KOLLAB_API_TOKEN`, `KOLLAB_API_MODEL`, etc.
  - System prompt configuration: `KOLLAB_SYSTEM_PROMPT`, `KOLLAB_SYSTEM_PROMPT_FILE`
  - Environment variables take precedence over config files
  - Support for `.env` file loading

- **Save conversation plugin** (`/save` command)
  - Save conversations to file or clipboard
  - Multiple format support (JSON, markdown, text)
  - Preserves full conversation context and metadata

- **Tmux plugin with live modal viewing**
  - `/terminal` (aliases: `/tmux`, `/term`, `/t`) command for tmux session management
  - Create, view, list, and kill tmux sessions
  - Live modal viewing with real-time session output
  - Interactive session selection and management

### Changed
- Enhanced system prompt with comprehensive project context
- Improved modal UI layout and spacing
- Optimized status view performance
- Startup banner functionality removed for cleaner startup

### Fixed
- System prompt rendering now processes tags before storing in history
- Plugin configuration merging improved
- Modal rendering with better coordinate-based restoration

## [0.3.2] - 2025-01-15

### Fixed
- **System prompt now actually loads from file**: Fixed critical bug where `system_prompt/default.md` was bundled but never loaded into config
- Config generation now includes `kollabor.llm.system_prompt` section with:
  - `base_prompt`: Full content from `~/.kollab/system_prompt/default.md` (15k+ chars)
  - `include_project_structure`: Controls project tree inclusion (default: true)
  - `attachment_files`: List of files to attach to system prompt (default: [])
  - `custom_prompt_files`: Additional custom prompt files (default: [])
- LLM service now uses loaded system prompt instead of hardcoded fallback
- Both global and local configs properly generate with full system prompt content

### Technical Details
- Added `_load_system_prompt()` method to `ConfigLoader`
- System prompt resolution: local `.kollab/system_prompt/default.md` overrides global `~/.kollab/system_prompt/default.md`
- Fallback to "You are Kollab, an intelligent coding assistant." only if file read fails

## [0.3.1] - 2025-01-15

### Changed
- Updated repository URLs to point to https://github.com/kollaborai/kollab
- Updated all package metadata and documentation links

## [0.3.0] - 2025-01-15

### Changed
- Standardized the pre-release configuration directory on `.kollab`
  - Global default: `~/.kollab/`
  - Local override: `./.kollab/`
  - No public migration is required for this pre-release cleanup

### Added
- System prompt customization support
  - Bundled system prompt content included in package
  - Global and local prompt resolution supported
  - Local system prompt overrides global (follows same resolution as config.json)
  - Users can edit system prompts to customize LLM behavior
- New utility functions in `kollabor/utils/config_utils.py`:
  - `get_system_prompt_path()`: Get active system prompt path
  - `initialize_system_prompt()`: Seed default prompt assets into config directories

### Fixed
- Consistent use of configuration directory utility functions across all components
- Application initialization now uses `ensure_config_directory()` instead of hardcoded paths

## [0.2.1] - 2025-01-15

### Changed
- Enhanced input plugin default colors now use darker gradient theme
  - Border: dim (was default)
  - Text: dim (was default)
  - Gradient mode: enabled by default (was disabled)
  - Gradient colors: darker theme ["#333333", "#999999", "#222222"] (was blue theme)
  - Text gradient: enabled by default (was disabled)
  - Provides better visual consistency and reduced visual noise

## [0.2.0] - 2025-01-15

### Changed
- Configuration directory now defaults to global `~/.kollab/`
  - Global default: `~/.kollab/` (configuration shared across all directories)
  - Local override: Create `./.kollab/` in project directory for project-specific config
  - All data (config, conversations, logs, state) now stored globally by default
  - This matches standard CLI tool behavior (like git, docker, etc.)

### Added
- Configuration directory utility functions in `kollabor/utils/config_utils.py`

### Fixed
- Consistent configuration directory resolution across all components
- LLM service, conversation manager, and logging now use unified config directory logic

## [0.1.3] - 2025-01-15

### Added
- Version flag support: `kollab -v` or `kollab --version` now displays version number

## [0.1.2] - 2025-01-15

### Fixed
- **Critical bug**: Fixed fullscreen command discovery (missed in 0.1.1)
  - `/matrix` and other fullscreen commands now work when installed via pip
  - Fullscreen plugins now use same directory resolution as regular plugins
  - Fixes second instance of plugin directory bug

## [0.1.1] - 2025-01-15

### Fixed
- **Critical bug**: Plugin discovery now works correctly when installed via pip
  - Previously plugins were only discovered in current working directory
  - Now searches package installation directory first, falls back to cwd for development
  - Fixes missing plugin features (enhanced input styling, /matrix command, etc.)
  - Users who installed v0.1.0 should upgrade to get full plugin functionality

## [0.1.0] - 2025-01-15

### Added
- Initial PyPI release
- Core LLM chat functionality with streaming support
- Event-driven plugin system
- Terminal UI with status indicators
- Enhanced input box with gradient borders
- Hook system for extensibility
- Configuration management with hot reload
- Conversation logging and persistence
- MCP (Model Context Protocol) integration
- File operations with automatic backups
- Command system with modal UI
- Visual effects (matrix rain, shimmer animations)

### Security
- Fixed command injection vulnerability in MCP integration
- Added input validation for tool names
- Changed subprocess execution from shell=True to shell=False
- Updated aiohttp dependency to >=3.10.11 (patches CVE vulnerabilities)

[0.1.1]: https://github.com/kollaborai/kollab/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kollaborai/kollab/releases/tag/v0.1.0

## [0.5.0] - 2026-01-16

### Added
- **Modern Design System (V2 UI) - Complete UI Overhaul**
  - Comprehensive redesign using unified design system (TagBox, Box, T, S, C)
  - Themed rendering with gradient support (lime, ocean, sunset, mono themes)
  - Consistent 76-character width across all UI elements
  - Segmented colored sections for status bars and containers
  - Proper visual hierarchy with modern styling

- **Modern Message Renderer**
  - New `ModernMessageRenderer` class with TagBox-based message display
  - `user_message()` - themed user messages with arrow icon (❯)
  - `assistant_message()` - single-line AI responses with diamond icon (◆)
  - `response_block()` - multi-line AI responses with continuous styling

- **Text Wrapping Utility**
  - `wrap_text()` function in `kollabor.ui.design_system.components`
  - Preserves ANSI color codes during text wrapping
  - Word-boundary and character-boundary wrapping modes
  - Helper functions: `_visible_len()`, `_wrap_words()`, `_wrap_chars()`

- **Modern Status Rendering**
  - `render_horizontal_layout_v2()` method with segmented design
  - Hint bars with solid backgrounds and proper color theming
  - Cycling hint for multi-view status (Opt/Alt+Left/Right)
  - Provider styles using design system colors

- **Modern Thinking Animation**
  - `get_display_lines_modern()` with TagBox styling
  - Timer display with token count support
  - Consistent with other UI elements

### Changed
- **Command Menu Renderer**
  - Migrated from legacy Agnoster/Gradient to TagBox design system
  - Refactored `_make_empty_state()`, `_make_scroll_indicator()`, `_make_footer()`
  - Removed `apply_bg_gradient()`, `make_bg_color()`, `make_fg_color()`
  - Updated imports to use design system components

- **Core Status Views**
  - Updated `PROVIDER_STYLES` to use design system color tuples
  - `_format_agent_skills_line()` now uses segmented status_v2 style
  - Changed from single-line to multi-line agent/skills display
  - Application now calls `register_all_views_v2()` instead of `register_all_views()`

- **Terminal Renderer**
  - Added `use_modern_ui` flag for new vs legacy rendering
  - `_render_input_modern()` method for TagBox input rendering
  - Shell command display handling (! prefix stripped from display)
  - Thinking animation switched to modern renderer

- **Message Coordinator**
  - Updated to use `ModernMessageRenderer` for message display
  - Improved cursor handling after atomic message display

- **All UI Widgets**
  - Migrated from `ColorPalette` to design system (T, S, TagBox)
  - Updated: `CheckboxWidget`, `DropdownWidget`, `LabelWidget`, `SliderWidget`, `TextInputWidget`
  - Removed `ColorPalette` imports from all widgets

- **Modal Renderers**
  - `LiveModalRenderer` and `ModalRenderer` now use `Box` component
  - Modernized title and footer rendering with half-block style
  - Removed legacy border character rendering

- **Banner Renderer**
  - Removed legacy `KOLLAB_ASCII` constant
  - `create_kollabor_banner()` now uses TagBox with primary gradient
  - Matches `banner_v2` design from preview-modern-ui.py

- **Enhanced Input Plugin**
  - `BoxRenderer` completely refactored to use TagBox
  - Removed legacy top/bottom border and content line rendering
  - `GeometryCalculator` box width now capped at 76 chars for consistency
  - Diamond icon (◆) in tag for first line

### Technical Details
- Design system exports: `wrap_text` added to `kollabor.ui.design_system.__all__`
- Theme colors accessed via `T()` - e.g., `T().ai_tag`, `T().user_tag`, `T().primary`
- Style codes via `S` - e.g., `S.BOLD`, `S.DIM`, `S.RESET`
- Gradient functions: `gradient()`, `gradient_fg()`, `solid()`, `solid_fg()`
- TagBox pattern for consistent tag + content layout across all components
