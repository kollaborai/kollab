# Changelog

All notable changes to Kollab will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Web UI: the Kollabor K logo heads the sidebar and is the browser tab icon.
- Web: agents are born with a random look that sticks. The first time the engine sees a gem alive it rolls its eyes and hat and keeps them for good (a seasonal costume covers the hat while the season lasts; the gem keeps its own color). Gem Studio (sidebar footer) dresses any gem over that look (eyes, hat, color) and sets the season for all (Auto, None, Halloween, Christmas) on a live stage; Random Look rolls a new outfit and Reset goes back to the born look. The engine keeps the looks (`GET`/`PUT /agents/appearance`, stored in `~/.kollab/hub/appearance.json`), so every browser shows the same gems.

### Changed
- Web UI: the session header is one slim row on desktop (two on phones) with borderless controls: the model, the approval mode (an amber open shield for Trust All), MCP and online counts, and Settings and Clear as icons with tooltips. The sidebar header is one row (the logo, then New Session and its options as icon buttons), rows show bigger gems with smaller text and drop the message count, and the session count sits beside "Sessions".
- Web: with no session open, the next free gem greets you with the model it will run and a Start Session button; inside a session the composer reads "Message Lapis…". Shell tool rows say what ran ("List Files", "Run · make build" instead of "Run ls" / "Run make"). The model picker dims the profile beside the model and shows the model alone below 1280px, so the header stays one row on a laptop with the sidebar open. Trajectory's header is a single row of counts and controls (the tab above already names it), and on phones its controls share one row. Its labels are in Title Case ("Collapse Tools", "Record Details", "Load Earlier").
- Web: Who Is Online shows each agent's gem and its name in Title Case; model lists read like the header (the profile dimmed, cut with an ellipsis instead of mid-word); MCP Servers drops its "No description" filler; every confirmation (delete session, delete profile, delete MCP server, clear history) has a Title Case question and a red action button; Settings' status lines are cased ("Engine connected", "Hub Sapphire · Idle").
- Web: the agents' terminal checklists ("[x] done", "[ ] next", also "todo: [x] item" on one line) render as checklists, and a turn with a single tool call shows just that call's row (no "1 tool call" header above it).

### Fixed
- The bundled `default` agent's description said it fixes linting errors; it now describes the general assistant it is.
- Shell commands report exit code 0 when they succeed; every success was reported as exit 1 next to `success: true`.
- Web: New Session's Agent field shows the agent the session will run (the gem's pool agent until you pick one) and pins the one you pick; it said "default" while the hub swapped the gem to `coder`. The agent list no longer appends each bundle's unused profile ("coder · default").
- Web: a session reloaded in a background tab shows in about a second instead of up to 20 (the history gate polled with timers the browser throttles).
- Web: Trajectory keeps a tool's name readable beside long output ("Run ls" showed "R…"); a tool result's multi-line output no longer ends in a stray comma; the sidebar shows gem names in Title Case.
- Web: sidebar rows no longer clip the gems' hats and props.
- Web: opening the sidebar on a phone no longer pops the New Session tooltip; the first Escape closed that tooltip instead of the sidebar.
- Web: a reply's time ("14.0s" under it) stays after a reload. The live clock lives in the tab, so a reloaded turn is timed by the history's own timestamps, from your message to the final reply.
- `kollab --web-ui` no longer reuses an engine of another version that is still running on port 7433 (a 0.12 web UI was served by a 0.10 engine left over from an older install). It leaves that engine running for whoever uses it and starts its own on the next free port.

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
- `/connect accept` and `/connect reject` name the device (`accepted ana-laptop. it is now a trusted device on marco-home.`) and never print a receipt; two requests with one name are told apart by the start of the fingerprint. `/connect allow` and `deny` print device names, and say they have no effect under `open` trust.
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
