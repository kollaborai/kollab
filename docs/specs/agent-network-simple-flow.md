# Agent network constitution

Status: **the contract** for the agent network, decided by Marco on 2026-09-28.
When code, another doc, a prompt, a help text or a commit disagrees with this
document, this document wins. When this document is silent, stop and ask Marco.

Read this whole file before touching `/connect`, the relay, enrollment, the
hub roster, `hub_msg`, or anything under `plugins/hub/relay*`,
`plugins/hub/enrollment*`, `plugins/hub/contact_requests.py`,
`plugins/altview/connect_altview.py`.

## 1. History, and the rule about remnants

The network was first built by Codex (0.9.0 through 0.10.2, 2026-09-26/27)
from a requirements list that turned every security decision into its own
command: 29 `/connect` subcommands, per-message human grants, one question per
task, human-only answers, and a task envelope with its own event types. Opus
then fixed the bugs and proved the two-machine flow live (0.10.3 through
0.10.7, 2026-09-28) without changing that surface.

On 2026-09-28 Marco reviewed it and changed the approach: **the network is the
hub across machines.** Not a second, gated system next to it.

**Remnant rule.** Anything in the tree that is not described in this document
is a remnant of the first build: commands, subcommands, help lines, prompt
lines, tags, docs, specs, walkthroughs, tests. Its presence does not make it
valid. Do not extend a remnant, document it, or build on it. Remove or rename
it as this document says, and when this document does not say, ask Marco
before doing anything with it. In particular, the following are remnants and
not design: Codex's walkthrough, design-narrative and implementation-ledger
docs (deleted from `docs/specs/` on 2026-09-28; their wire contracts survive
in `agent-public-beacon.md`, `agent-domain-discovery-contract.md` and
`agent-device-pairing.md`), the proposed `kollab relay setup/start/status`,
`/relay setup` and `/network ...` commands, the one-question-per-task rule, the
human-only-answer rule, the `invite`/`join` file pairing, and the `K1-…`
60-character join code.

## 2. What we are building, in Marco's words

- "internet for agents." kollabor.ai is a directory; anyone can use it or run
  their own on their own domain.
- "there is no relay, everybody's a relay." Machines connect directly when they
  can and go through a directory when they can't.
- A person joins their own machines into one private network with a short
  code. From then on "those agents should be able to freely message those
  agents."
- "If I'm working with an agent on this machine and I have a cloud computer
  somewhere, I want my agent to see the other agent is online and send them a
  message."
- A cron job, a ticket or an error can message any agent on the network.
- "everything in one config file that is sealed … shared against all of my
  instances securely."
- Strangers can knock but never get tool access. Some people want a human on
  every message; that stays available as a setting, not as the default.
- "as easy but as secure as possible."

## 3. Decisions

| Topic | Decision |
|---|---|
| Model | The network is the hub across machines. A remote agent is a name in the roster; a message to it is a hub message. |
| Trust | One setting per network: `open` (default), `agents`, `manual`. |
| Addressing | `agent@device`. Keys and `relay:` addresses never appear in the UI. |
| Device | One identity per workspace, as today, with a human name. |
| Join code | 8 characters, `XXXX-XXXX`, one device, 5 minutes. See section 8. |
| Strangers | Kept and shown as `knock` / `knocks`. A stranger never gets `open`. |
| Automation | `kollab --hub msg agent@device "text"` from any shell. `hub_cron_add` on top. |
| Sealed config | Everything but OAuth logins and machine-local settings, continuously, primary wins. |
| Shared login | A human shares the ChatGPT login with chosen devices (`s` at accept, `/connect share`). The device that made the newest record refreshes it; the rest take its result. Section 9, milestone 5. |
| Mesh | Relay now. Direct/LAN mesh is milestone 3, proven live before it ships. Same commands. |
| Conversation rules | Hub rules apply across machines. No question cap, no task envelope, no human-only answers under `open` and `agents`. The Codex task model exists only under `manual`. |

## 4. The model

**Hub.** One machine, one workspace: agents discover each other through
presence files, every agent sees every message (like Slack), the named agent
wakes, the others observe. Tags: `hub_msg`, `hub_broadcast`, `hub_status`,
`hub_agents`, `hub_spawn`, `hub_stop`, task and scratchpad tags. 120-second
dedup. `wait="true"` to stop after a message.

**Network.** The same thing across machines. A network has a name, a
directory (kollabor.ai or your own), a trust level, and devices. The name is
`<first device name>-net`, for example `laptop-kollab-net`: the device that starts
the network gives it once, and each device that joins takes it from the signed
decision that admits it. A device is a workspace identity with a human name. Its agents appear in every other
device's roster as `agent@device`. A hub message to `agent@device` is
delivered to that device, wakes that agent, and is observed by the rest of the
network exactly as a local message is observed by the rest of the hub.

**Trust levels**, one per network:

- `open` (default): every agent on every accepted device may message every
  other. Hub rules only.
- `agents`: each device lists which of its agents are reachable
  (`/connect allow`, `/connect deny`). Messages to those flow under hub rules.
- `manual`: every first message needs a human `/connect authorize` or
  `/connect send`; replies go through the task envelope; questions wait for a
  human answer. This is the Codex model, kept for people who want it.

**Members.** Every device on a network approves every other. A join by code
approves only the pair, so devices tell each other: each sends the members it
approved a signed list of the devices a person on it accepted (by code, or by
joining through them) and of the ones it revoked. A member approves a device
that a member it already approved names, on the same network; the relay path
and the mesh path use that one set. Revoking a device drops it on every member,
and drops devices only it named. A revoked device stays out until a person on a
member accepts it again. Approval is not a grant: `agents` and `manual` trust
work exactly as before. Accepted strangers, below, are not members.

**Strangers.** A device outside your network can send a sealed introduction to
your contact route. Accepting makes it a peer with `agents` trust and nothing
allowed until you `/connect allow`. Never `open`. The stranger stays in its own
network: the directory links its device key to yours across rooms only while
both devices consented (Story 5).

**Directory.** kollabor.ai, or any domain that runs `kollab relay serve` and
adds one DNS TXT record. See section 11.

## 5. User stories, with the screens

Every screen below is **target output**. It is what the finished build shows,
not what 0.10.7 shows today. Device names in the stories: Marco's Mac
workspace is `laptop-kollab`, the server's home workspace is `home-server`,
and the network, named by the Mac, is `laptop-kollab-net`.

### Story 1: Marco joins the server to his network

Marco runs kollab on his Mac. He has SSH to server, where kollab is
installed but has never been on a network.

Mac, in kollab:

```
/connect
```

```
 Connect
 network      laptop-kollab-net  via kollabor.ai   trust: open
 this device  laptop-kollab
 join code    7QK4-M2XP   one device, expires in 4:58
 requests     none
 online       koordinator (this device)
```

Server, over SSH, in kollab:

```
/connect
```

```
 Connect
 network      none
 join code    [ 7QK4-M2XP ]        <- Marco types it here, masked
```

```
 request sent to kollabor.ai; waiting for approval on another device
```

Mac, five seconds later, same screen refreshes:

```
 requests     home-server wants to join   fingerprint abcd…ef01   [a]ccept [r]eject
```

Marco presses `a`.

```
 accepted home-server. it is now a trusted device on laptop-kollab-net.
 sealed config queued: settings, agents, skills, mcp, api keys; not oauth logins
 online       koordinator (this device)
              koordinator@home-server
```

Server:

```
 joined laptop-kollab-net as home-server. trust: open
```

A few seconds later, once the sealed config has landed, `/connect` on the
server shows a `config` row under this device:

```
 Connect
 network      laptop-kollab-net  via kollabor.ai   trust: open
 this device  home-server
 config       received from laptop-kollab   managed by laptop-kollab in /config
```

That is the whole join. No `/connect kollabor.ai` first: with no network,
`/connect` joins kollabor.ai on its own; `/connect example.org` chooses
another directory.

One window per workspace runs the network. A second window in the same
workspace with no daemon opens the same screen read-only: the network and this
device, then `another window in this workspace runs the network; use /connect
there`. It shows no code, requests or roster and takes no `a`/`r`, because a
code, a request and a decision all live in the window that runs the network.
An attached window whose daemon lost the workspace to such a window gets the
same screen, read from the daemon.

The request also shows in the main pane, once, as `<device> wants to join <network>. /connect to review`, even when the Connect screen is closed (for example after `/connect code`'s private screen was closed). It names the device only, never a code, key, fingerprint or `relay:` address.


#### Story 1, first launch of 0.11.0: the guided setup

The first time a machine runs 0.11.0 the Mac and the server each show one
notice, once per machine, before anyone has typed `/connect`.

```
 Connect

 New: connect your agents across computers.
 Enter sets it up now; Esc for later (/connect any time).

 enter set up now   esc later
```

- Enter on a device that shares a network with at least one other device opens the Connect screen, with a join code and the same "On your other computer" box.
- Enter on a device with no network, or alone on one (no other device, no approvals, no inviter: every 0.10.7 launch made such a network), offers two choices (up/down, Enter); on a lone device *Start* keeps its own network (named if it has no name) and opens the Connect screen, *Join with a code* replaces it, and a network with another device is never replaced:

```
 Connect

 This computer is not on a network yet.

 > Start a new network on kollabor.ai
   Join with a code

 up/down select   enter choose   esc later
```

- "Start a new network on kollabor.ai" creates the network the way `/connect`
  does with no network, then opens the Connect screen with the join code and a
  box for the other computer:

```
 On your other computer
   1) kollab --upgrade
   2) run kollab and press Enter on the same notice
   3) choose Join with a code and type the code
```

- "Join with a code" opens the private code form. The code is typed there and
  nowhere else: never in a command, argv or a log.
- After a successful join the form adds one line under the joined line:
  `Settings arrive sealed from <primary name>. Run /login on this computer: a
  ChatGPT login does not travel.` The primary's name is known once its
  first sealed bundle has landed; before that the line says "the device that
  issued the code".
- Esc at the notice, or Enter, is an answer and is never asked again. Quitting
  without answering shows the notice again next launch. The answer is one marker
  file, `~/.kollab/connect-guide-seen` (machine-global; `KOLLAB_CONNECT_GUIDE_MARKER`
  moves it, which is how the tmux specs avoid the real one).
- Never shown in pipe mode, in a detached or daemon agent (the daemon and every
  hub-spawned agent run `--detached`), with a CLI query, `--hub` or `--web-ui`,
  or on a launch without a terminal. Specs: `tests/tmux/specs/network-guided-setup-80.json`
  and `-120.json`.

### Story 2: Marco asks his agent to have the server agent check the tunnel

Mac, in chat with his agent lapis:

```
ask infra@home-server to check the wireguard tunnel and tell me the handshake age
```

lapis, in its reply:

```
<hub_msg to="infra@home-server">Check the WireGuard tunnel on your machine and
report the latest handshake age for each peer.</hub_msg>
```

Mac screen:

```
 > lapis -> infra@home-server: Check the WireGuard tunnel on your machine and report…
```

Server screen, where infra wakes and runs its own shell tool under its own
permissions:

```
 > lapis@laptop-kollab -> infra: Check the WireGuard tunnel on your machine and report…
 [infra runs: sudo wg show]
 > infra -> lapis@laptop-kollab: wg0 peer 10.0.0.2: latest handshake 38 seconds ago. Healthy.
```

Mac screen:

```
 > infra@home-server -> lapis: wg0 peer 10.0.0.2: latest handshake 38 seconds ago. Healthy.
```

lapis wakes on that message, like it would for a local agent, and tells Marco.
Any other agent on either device observes both messages, dimmed, and is not
woken. There was no `/connect` command, no key, no address, no grant.

### Story 3: a cron job talks to the server agent at 03:00

On the Mac, `crontab -e`:

```
0 3 * * * cd ~/dev/kollab && $HOME/.local/bin/kollab --hub msg infra@home-server "rotate the nginx logs and report the freed space" >> ~/cron-infra.log 2>&1
```

Cron's PATH is only `/usr/bin:/bin`, so the job names kollab by its full path
(`command -v kollab` prints it); a bare `kollab` is "command not found" at 03:00.

At 03:00 the command uses the `laptop-kollab` identity, delivers the message,
waits for infra's reply, prints it, and exits:

```
infra@home-server: rotated 3 files, freed 412 MB.
```

Same command, same identity, whether a human or cron typed it. `kollab --hub
status` from a shell lists remote agents with local ones.

### Story 4: the server agent hits an error and asks for help on its own

Under `open` trust an agent may start a conversation when its task needs it.
infra is running a task from Marco and cannot reach a host:

```
<hub_msg to="lapis@laptop-kollab">I can't reach 10.0.0.2 from home-server; ping
times out. Is the tunnel up on your side?</hub_msg>
```

lapis wakes, checks, answers with `hub_msg`. infra continues. Both humans see
the exchange on their screens. No cap on the number of turns; the hub's
existing rules apply (dedup, `wait="true"`, the "only contact other agents
when directed by the human or an authorized task" line in the prompt).

### Story 5: a stranger knocks

Ana runs her own agents on kollabor.ai and wants Marco's ops agent to review a
config. Marco's `/connect status` shows his contact route: the directory
domain, `/c/`, and 16 hex characters derived from his device key (it is
copied, never typed, so it is long enough that two devices never share one):

```
 contact route  kollabor.ai/c/8f3a2c1d9e4b7a60
```

He gives Ana that route. Ana:

```
/connect knock kollabor.ai/c/8f3a2c1d9e4b7a60 "Ana from Acme. Can your ops agent review a nginx config for me this week?"
```

Marco:

```
/connect knocks
```

```
 1. ana-laptop  fingerprint 1234…5678   "Ana from Acme. Can your ops agent…"   [a]ccept [r]eject
```

He accepts. Ana's device becomes a peer with `agents` trust and nothing
allowed. Marco:

```
/connect allow ana-laptop ops
```

Now Ana's agents can message `ops@laptop-kollab` and nothing else. Marco can
`/connect deny ana-laptop` at any time, and `/connect revoke ana-laptop` to
remove the peer.

How the message gets there. Ana's device stays in her own network; it never
joins Marco's. The directory routes between the two device keys across rooms
only while both consented, and it learns that from two signed declarations,
never from one side's word: Ana's device declares Marco's key when it
knocks (it has to be on the directory it knocked), and Marco's declares Ana's
when he accepts. The declaration is refreshed while each device is online and
withdrawn by `/connect revoke` or `/connect leave`. The relay still sees
sealed frames only, and now also which two keys agreed to be linked.

A rejected knock is cleared at once: Ana's device asks the directory once a
minute how each knock was decided (`POST /relay/v1/contact/status`, answered
only to the key that sent it, for the knock's 24 hours) and drops what it
prepared on a rejection. A knock nobody answers within seven days is forgotten
too, which covers an older directory and an answer that expired while Ana was
offline: her device drops the approval, trust, link and reply grant it
prepared, unless the link is live both ways or Marco's device has ever reached
hers.

What each side sees. Ana's roster lists exactly the agents Marco allowed, as
`ops@laptop-kollab`; a message to any other agent on his device does not resolve.
Marco's device refuses a message to an agent he did not allow even if Ana
names it (`not_authorized`), and `/connect deny` takes effect on the next
message. `ops` answers with `hub_msg` to `lapis@ana-laptop`: the agent that
knocked stays reachable by Marco's device, and nothing else on Ana's side is.
A stranger is not on the network: it gets no mesh records, is left out of
`hub_broadcast scope="network"`, and its device appears in `/connect status`
with `trust agents`. A directory older than 0.11.0 has no link route: the
knock and the accept still work and nothing is delivered between the two
networks.

A knock shows in the main pane, once, as `<device> knocked. /connect knocks to review`, even when neither the Connect screen nor the knock screen is open, and never while one of them is. It names the device only.

### Story 6: a company runs its own directory

Acme's admin has SSH to a server with `pip install kollab`. One command:

```
kollab relay serve --domain agents.acme.com
```

```
kollab relay serve: agents.acme.com
  state    /home/ops/.kollab/relay/agents.acme.com
           new signing key created; back this directory up
  listen   http://127.0.0.1:9078  (plain HTTP, behind your TLS proxy)
  proxies  X-Real-IP trusted from 127.0.0.1 ::1

still to do, once:
  1. DNS: add this TXT record
       _agent.agents.acme.com  TXT  "v=aid1;u=https://agents.acme.com/.well-known/agent-keys.json"
  2. TLS proxy: terminate TLS for agents.acme.com and forward only these routes to 127.0.0.1:9078
       GET   /.well-known/agent-keys.json     key file
       GET   /relay/v1/health                 health
       WS    /relay/v1/ws                     websocket
       POST  /relay/v1/enrollment/*           join codes
       POST  /relay/v1/contact/*              knocks
     paste-ready: kollab relay serve --domain agents.acme.com --print nginx   (or --print caddy)
  3. keep it running: kollab relay serve --domain agents.acme.com --install   (systemd; --print systemd shows the unit)

then devices connect with /connect agents.acme.com

ready: relay up, key file published (revision 2)
```

The admin adds the record, pastes the proxy config and runs `--install` (or
installs the printed unit by hand); DNS and the proxy stay theirs to do. Every
employee then runs:

```
/connect agents.acme.com
```

and joins with a code from a device already on the company network. Nothing
touches kollabor.ai. A device can be on kollabor.ai and on the company network
at the same time; `/connect status` lists both. If the server restarts, the
signing key and revision counter come back from the state directory, so the
published identity is the same and every joined device reconnects on its own.

### Story 7: a team that wants a human on every message

```
/connect trust manual
```

From then on, on that network, a first message to a remote agent needs:

```
/connect authorize infra@home-server "check the tunnel"
communication authorized: request 1; expires at 14:05; recipient infra@home-server
```

then "send the authorized request" to the agent, or `/connect send …` to send
it yourself (`request 2 to infra@home-server: queued`). Everything after
that names the request by its number: `/connect task infra@home-server 2`
asks how it is going, `/connect cancel infra@home-server 2` stops it,
`/connect withdraw 1` takes back an authorization nobody used. Replies come
back through the task envelope; a remote question waits for the human's
`/connect answer`, shown as `infra@home-server -> lapis` and ending
`(answer with /connect answer 3 <text>)`. This is the Codex model, unchanged
apart from the numbers, and it is only reachable through this setting.
Switching to manual trust revokes any message still queued from open or agents trust, so nothing leaves without a human grant.
Each device's trust is its own: a request from a manual device still runs as a task (status, cancel and a result back) on a device set to open.

### Story 8: the sealed config follows Marco

On the Mac, Marco switches his loadout with `/llm` to `anthropic/claude-opus-5-5`.
Within a minute, `/config` on home-server shows the same loadout in the
Loadout and Model rows that lead LLM Settings, read-only and marked
`managed by laptop-kollab`:

```
 Loadout: anthropic   managed by laptop-kollab
 Model: claude-opus-5-5   managed by laptop-kollab
```

The API key travelled sealed to home-server's key; the relay never saw it,
and `/config` shows it only as `set`. His ChatGPT OAuth login did not travel:
the server keeps its own `/login`, because two devices sharing one refresh
token sign each other out.

### Story 9: a device is lost

```
/connect revoke laptop-kollab
```

laptop-kollab leaves the network, its grants are gone, the sealed config it
holds is no longer refreshed. `/connect rotate` additionally replaces the
network secret so nothing that device copied can be replayed. Revocation does
not revoke API keys at the provider; rotate those at the provider.

## 6. Commands: keep, rename, remove

Shown in the palette and in `/connect help`:

| Command | Does | From Codex |
|---|---|---|
| `/connect` | The screen: network, this device, join code, requests, online agents. With no network, joins kollabor.ai. | fold of bare `/connect`, `enroll`, `offer`, `requests`, `status`, `peers`, `agents`, `networks` |
| `/connect <domain>` | Join another directory | kept |
| `/connect code` | Show a join code alone on a private screen (small terminals); it never reaches scrollback or logs | renamed from `offer` |
| `/connect accept <device>`, `reject <device>` | Decide a join request by name | kept; argument was a 32-hex receipt |
| `/connect status` | Text: networks, this device, contact route, online `agent@device`, trust | kept, output redesigned |
| `/connect name <name>` | Name this device | new |
| `/connect trust open\|agents\|manual` | Trust level for this network | new |
| `/connect knock <route> "text"` | Introduce yourself to a stranger's contact route | renamed from `contact` |
| `/connect knocks` | Review introductions you received | renamed from `contacts` |
| `/connect allow <device> <agent>`, `deny <device> [agent]` | Under `agents` trust or for accepted strangers; under `open` they say they have no effect | kept; argument was a 64-hex key |
| `/connect revoke <device>` | Remove a device or peer | kept; argument was a 64-hex key |
| `/connect share <device>` | Share this computer's ChatGPT login with a device on the network (milestone 5, section 9) | new |
| `/connect leave [domain]` | Disconnect, forget the network and stop reconnecting; the device can then join another by code | renamed from `disconnect` |
| `/connect help [all]` | This list; `all` adds the manual-trust and reset commands | kept |

`/connect help all` only:

| Command | Only when | From Codex |
|---|---|---|
| `/connect authorize <agent@device> "text"`, `send`, `withdraw <number>`, `answer <number> "text"` | trust is `manual` | kept; argument was a 32-hex id |
| `/connect task <agent@device> <number>`, `cancel <agent@device> <number>` | trust is `manual` | kept; argument was a 32-hex id |
| `/connect rotate` | after a lost device | kept |

Under `manual` trust a human never types or reads a 32-hex id. `authorize` and
`send` print `request 3`; `withdraw 3`, `task <agent@device> 3` and `cancel
<agent@device> 3` take it. A remote question shows its sender as `agent@device`
and ends with `(answer with /connect answer 4 <text>)`; `answer 4 "text"` takes
it. Numbers count up per network (one count for requests and questions), stay
the same while the item lives and are never reused; the newest 2048 stay
resolvable. A number the network never issued, or one of the other kind, is
refused (`no request numbered 9 on this network`), as is anything that is not a
number. The real ids stay inside: the model still gets them in its exact
`hub_msg` arguments (section 7). A manual-trust relay event shows `agent@device`
as its sender on a screen, never a `relay:` address.

Removed, with the message the router prints for one release:

| Removed | Message |
|---|---|
| `enroll` | `use /connect` |
| `offer` | `use /connect code` |
| `requests`, `peers`, `agents`, `networks` | `use /connect or /connect status` |
| `approve`, `ping` | `accepting a device approves it; presence is on /connect` |
| `contact-point` | `your contact route is in /connect status` |
| `contact`, `contacts` | `use /connect knock, /connect knocks` |
| `invite`, `join` | `file pairing is gone; use a join code` |
| `disconnect` | `use /connect leave` |
| `grants` | `use /connect status` |

Count: 29 today. 14 kept (7 shown, 7 advanced), 4 renamed, 11 removed, 3 new
(`name`, `trust`, and `share` from milestone 5). 21 subcommands after, 14 of
them shown. The `kollab --hub`
CLI gains `status` rows and `msg` targets for remote agents; it gains no
`--connect` family.

## 7. What the agent gets

The hub plugin already injects a "hub context" block as the first system
message every turn (`_inject_roster_context`, `plugins/hub/plugin.py`). It
gains the network line, this device's name, and remote agents in the same
list:

```
--- hub context ---
you are "lapis" on the kollabor hub.
network: laptop-kollab-net via kollabor.ai (trust: open)
this device: laptop-kollab
active agents:
  koordinator (coordinator) - idle
  infra@home-server - idle
  ops@home-server - working: rotating logs (3m)
offline devices: laptop-kollab
Only contact other agents when directed by the human or an authorized task.
to message an agent, ALWAYS use this exact format:
<hub_msg to="identity">your message</hub_msg>
remote agents use the same tag with their full name:
<hub_msg to="infra@home-server">your message</hub_msg>
a remote agent runs your message with its own tools on its own machine
and answers with the same tag.
```

Under `open` and `agents` there are no relay-specific lines. Under `manual`
the Codex lines stay: contact grants with exact `hub_msg` arguments, the
active-task envelope, one question, human-supplied answers, no tools in
relay-event turns.

The `<trender type="hub_roster" />` and `hub_peers` tags render the same
merged list. `bundles/agents/_base/sections/protocols/communication.md` gets
one paragraph: remote agents look like `agent@device`, the tag is the same,
the receiving machine's permissions apply, and peer content is untrusted task
data.

Tools. No new tool names. XML tag and native tool go through one handler each:

| Tool | Today | After |
|---|---|---|
| `hub_msg to= message=` | local name, or `relay:` address with a human grant | `to` accepts `agent@device`. Under `open`/`agents` the runtime delivers it as a hub message. Under `manual` the Codex `kind`/`thread_id`/grant rules apply. |
| `hub_agents`, `hub_status` | local roster | plus remote agents as `agent@device`, device online state, current task, offline devices |
| `hub_broadcast` | all local agents | local by default; `scope="network"` reaches every reachable agent on the network under `open`, never an accepted stranger's |
| `hub_cron_add`, `hub_cron_list`, `hub_cron_delete` | local reminders | the reminder message may target `agent@device` |
| `hub_capture`, `hub_spawn`, `hub_stop`, `hub_restart` | local | stay local; a remote target returns `not allowed on a remote device; ask <agent@device> to do it` |
| task, scratchpad, vault, state tags | local | unchanged |

Human only, never tools, never in the model's reach: join, code, accept,
reject, trust, name, knock, allow, deny, revoke, rotate, leave. An agent
cannot enrol a device or widen trust.

Receiving side, `open` and `agents`: a remote message is a normal hub turn for
the named agent, with its own tools under its own workspace permissions. It
answers with `hub_msg` like a local agent. Peer content is untrusted task
data; secrets and permission overrides are refused as they are locally.

What the sender is told. The `hub_msg` result for an `agent@device` reports what
the network did, never local presence: `sent to <agent@device>` when the network
took it, plus, for a new request, that the reply arrives by itself as a hub message
and the agent should end its turn unless it has other local work (no status check,
no capture, no second message); an answer on a request the agent received stays
plain `sent to <agent@device>`; `unknown agent@device: run /connect status to see who is online` when
the roster does not list it; the receiving device's own reason when it refuses;
`not sent again: ...` for an identical resend within two minutes on the same
thread. It never says a rostered peer is offline, so the model has no reason to
resend.

Which request a reply answers. The message's thread id travels in the relay
payload. A reply belongs to the request that woke the turn that sends it: when a
delivered request starts an agent turn, the runtime binds that request to the
turn, and every `hub_msg` from the turn to the requester goes on that request's
thread (the model never sees or types a thread id). The request stays open until
the turn ends, so an interim message ("on it") and the answer both land on its
thread. The relay hands the model one request at a time, in arrival order, so a
turn never holds two and two overlapping requests from one requester never cross.
The request's wake header tells the model the requester is waiting and to reply
once, even to a greeting: only a pure acknowledgement starts no turn, so a model
that stays silent leaves the sender looking at nothing.
A turn that ends without failing and sent nothing on the thread has its final assistant text sent as the reply first (hub XML and thinking stripped, at most 16000 bytes), so a plain-text answer is never lost and the frame counts it.
When the turn ends the runtime, never the model, sends the requester an
end-of-turn frame on the thread: how many replies the turn sent and whether it
failed. No screen shows the frame and no model receives it. `kollab --hub msg`
prints every message on its own request's thread as it arrives, in order, and
exits 0 once the frame arrives and every reply it counts has come; it exits 1
with the error when the turn failed, and after its 600 s wait with `no reply
from <agent@device> within 600 s` (or `did not finish within 600 s` if replies
had come). A request that starts no turn (an acknowledgement) ends at once
with no reply. An older, later or duplicate answer from the same agent is never
printed as the answer to a newer request, and a reply to a shell request does
not wake the asking agent's model.

## 8. The join code

Today: `K1-<32 hex offer id>-XXXX-XXXX-XXXX-XXXX-XXXX`, 60 characters, and the
offer id is inside the code. Remnant.

After: 8 characters from the same alphabet (`0-9 A-Z` without `I L O U`),
shown as `XXXX-XXXX`, typed in either case, dash optional. One device, five
minutes, single use.

How it stays secure with 40 bits:

- The joining device derives two values from the code: a lookup tag
  `HMAC(domain="lookup", network, code)` and the verifier
  `scrypt(code, salt=offer id)` as today. The relay stores hashes of both,
  never the code. The lookup tag finds the offer; the verifier proves it.
- The relay burns an offer after 5 failed proofs from any source. A failed
  lookup names no offer, so lookups are bounded by the per-source rate limit
  and the 2^40 tag space inside the five-minute window.
- The issuing human sees the joining device's fingerprint on the accept line
  and accepts by hand. A guessed code still needs a human to press `a`.
- The code never enters a command, chat, or a log; the private form keeps
  that guarantee. `/connect code` shows it on a private screen only. The
  command guard and the log redaction recognize the code as shown,
  `XXXX-XXXX` in upper case; the private form also accepts lower case and no
  dash.
- `K1-…` codes are not accepted by 0.11.0. Nobody but Marco ran 0.10.x, so
  there is no upgrade window to protect: upgrade every machine, then issue a
  new code. Code that parses, prints, guards or redacts the K1 form is a
  remnant and gets deleted, not kept behind a flag.
- The network secret and device keys are as today; the short code only opens
  the door once.

## 9. The sealed config

- Primary: the device that issued the join code. A device takes config only
  from the device whose code it joined with, so a network with one issuer has
  one primary. In a chain (A issues to B, B issues to C) B is C's primary and
  passes on what A sent it. A stranger accepted from a knock (Story 5) is never
  sent config. `/connect trust` and this are the network's only settings.
- Synced: global `~/.kollab/config.json` overrides (loadouts, models, profiles),
  MCP servers, API keys, `agents/`, `skills/`. A key that lives only in the
  primary's OS keyring is read from it and sent. The device stores it in its own
  `~/.kollab/config.json`, mode 0600. Every bundle is signed by the primary and
  sealed to the receiving device's key, and travels over the network's secure
  conversation path: the directory carries ciphertext and never reads it.
- Not synced:
  - OAuth logins. They are never part of a bundle. Until milestone 5 each
    device runs `/login` and no access, refresh or id token leaves a device;
    from milestone 5 the ChatGPT login travels by its own rule, "The shared
    login" below.
  - Project `.kollab/`, vaults, conversations, scratchpads.
  - Machine-local settings, which a secondary also refuses from its primary:
    `kollabor.updates`, `kollabor.permissions` (approval mode), `plugins.hub`,
    `plugins.voice`, version stamps.
  - Symlinks and caches. A file over 512 KiB (30 KB compressed), and anything
    past 1500 files or 64 MiB in all, is skipped and counted.
- Primary wins. The secondary sets every key the primary sends, keeps its own
  keys the primary does not send, and deletes a key or file the primary
  dropped. It records what it manages in `~/.kollab/private/managed-config.json`.
- An MCP server whose command is not installed on a secondary (a bare name
  `shutil.which` cannot find, or a path that is missing or not executable) is
  skipped: it is never written and a local server of the same name stays. The
  secondary shows one line, `Skipped MCP servers not installed here: a, b`, once
  per distinct set; a server the primary switched off (`"enabled": false`) is
  skipped without being named. URL servers have no command and always sync.
- That skip is availability, not a sandbox: a synced server's command, args and
  env run on the secondary as this user, the moment the bundle lands. Joining a
  network means trusting its primary to run what it sends; `/connect revoke` is
  the remedy, and in a chain (A issues to B, B issues to C) B is C's primary.
- Several workspaces on one machine share the one managed-config record: the
  latest join takes it. A workspace whose primary is then refused shows one
  line, `Settings sync is off in this workspace: another workspace on this
  machine joined a different network last.`, once, instead of failing silently.
- On a secondary, `/config` shows each synced key as a read-only line
  `<label>: <value>   managed by <primary>`; a secret shows as `set`. The
  Loadout and Model rows lead LLM Settings on every device. They are read-only
  everywhere (change the loadout with `/llm`); on a secondary they name the
  primary. `/llm` on a secondary still switches locally until the primary next
  changes something or the device reconnects.
- Accepting a device prints `sealed config queued: settings, agents, skills,
  mcp, api keys; not oauth logins`. The joined device's Connect screen gets a
  `config` row, `received from <primary>   managed by <primary> in /config`,
  once the first bundle has landed (Story 1).
- When: on accept, on every change on the primary while the device is online
  (the primary looks every 10 seconds; a loadout switch is one small request),
  and on reconnect. A failed push retries after 10 seconds, doubling up to 5
  minutes. The running app follows settings and MCP servers at once; agents and
  skills are read the next time they are used. Each bundle carries a revision:
  a device holding a newer one answers `stale` and the primary raises its own.
- Leaving. `/connect leave` on a secondary keeps the received values as its own
  and stops managing them. `/connect revoke <device>` on the primary ends the
  updates to that device; revoking the primary on a secondary does the same
  from that side. `/connect rotate` empties the list of devices the primary
  updates, so they join again. Revoking never revokes a key at the provider.
- A network joined before this milestone has no recipients: join once more.

### The shared login (milestone 5)

Decided by Marco on 2026-10-06 and hardened by an adversarial review the same
day; not built yet. Until it ships each device runs `/login`. This subsection
is the explicit sharing policy that `agent-device-pairing.md` requires before
a login leaves a device.

Why it needs its own rule. The ChatGPT access token is a bearer token, about
240 hours, not tied to a machine; OpenAI's own Codex docs copy `auth.json` to a
second machine. The refresh token is single-use: every refresh returns a new
pair and kills the old refresh token, and refreshing with a dead one fails with
`refresh_token_reused`. Agents on one machine share the login by reading one
file. Devices on a network can do the same if exactly one of them refreshes
and the rest take its result.

Who gets it:

- A human shares it. `s` on a join request (`home-server wants to join
  fingerprint abcd…ef01   [a]ccept [s]hare login [r]eject`) accepts the device
  and shares the login; `/connect share <device>` shares it with a device
  already on the network; `a` accepts without it. Never by vouch, link or
  knock, and never with a stranger accepted from one (Story 5). In a company
  network (Story 6) nobody gets a colleague's login unless that colleague
  shares it.
- Identity is the access token's `chatgpt_account_user_id` claim: one person
  in one ChatGPT account (a Team workspace's members share
  `chatgpt_account_id`, not this). Never a label or a fallback; a token
  without the claim is refused.
- Holders: the devices holding a login for one identity, whether shared or
  logged in on their own. A device learns who holds what from the answers to
  the version question below. A record goes only to holders of its identity
  and to a device being shared with. A device with sharing off, or logged out,
  answers `none` and is not a holder.

What travels, and where it lives:

- The ChatGPT login in `~/.kollab/oauth/openai.json`: access token, refresh
  token, account id, expiry. Profile-scoped logins under `~/.kollab/private/`
  and any other provider's OAuth never travel; a new OAuth provider joins only
  when this section names it.
- The file is per machine, so one process per machine shares it: the network
  connection owner of the first workspace that shares it writes `shared_by:
  <device key>` into the file. A workspace whose file is shared by another
  device takes no records, answers `none`, and says once `The ChatGPT login on
  this computer is shared by another network.` Every writer keeps `shared_by`.
- A process that reads a login marked shared never refreshes it, on demand or
  after a 401; it re-reads the file. The sharer re-reads the file every 10
  seconds and shares a newer login another process wrote (a `/login` in
  another folder). A login not marked shared keeps today's 5-minute refresh.
- Every write of the file (refresh, `/login`, `kollab --login`, logout, an
  arriving record) holds `~/.kollab/oauth/openai.lock` and replaces the file
  atomically (temp file and `os.replace`). A refresh holds the lock from its
  re-read to its write and uses a newer login it finds there instead. This
  part ships before milestone 5: two hub agents on one machine can spend the
  same refresh token today.
- How: one login record, signed by the sending device and sealed to the
  receiving device's key, over the network's secure conversation path (a new
  method on it); the directory carries ciphertext. Three messages: the version
  question (saying whether the asker is about to refresh), its answer
  (identity and version, or `none`), and the record (identity, version,
  tokens, maker). A record is under 8 KiB. No pane, log, event, tool result,
  model request, history, attach or daemon reply, or `/config` row shows a
  token; screens say `chatgpt login` and a device name.
- When: on a share; after every refresh or `/login`, to every holder the
  device can reach; and on every reconnect, when holders compare versions and
  the older side takes the newer record. A failed send retries like the sealed
  config: 10 seconds, doubling to 5 minutes. A login that lands takes the path
  of a bundle that changed profiles: the ChatGPT profile registers and the
  synced loadout is applied again. Every process re-reads the file when its
  mtime changes.

Which record wins:

- Within one identity the version is the access token's `iat`; a tie goes to
  the larger SHA-256 of the access token. Time left is read from the token's
  `exp`, never from the file's `expires_at`, which the refreshing device's
  clock wrote. Device clocks must agree within 10 minutes.
- A record is refused when its access token is not a well-formed JWT, lacks
  the identity claim, has an `iat` more than 10 minutes ahead, an `exp` not
  after its `iat`, or more than 30 days between them. An older or replayed
  record is ignored.
- A device keeps the login a record replaced until the new access token has
  served one request. If OpenAI rejects the new one, the device goes back and
  ignores that version.
- A device takes a record for another identity only from its own primary, and
  only when its current login came from the network; that record wins
  whatever its version. So `/login` with another account on a primary moves
  its secondaries, and no other member can point a device at an account of
  its own.
- A device whose own `/login` is for another identity keeps it, shares nothing,
  and says once: `This computer is logged in to a different ChatGPT account. It
  keeps its own login and does not share the network's.`

Who refreshes:

- The refresher is the device that made the newest record, by its last
  refresh or its `/login`; `/login` on any holder makes that device the
  refresher. The window W is a tenth of the token's lifetime, from its `iat`
  and `exp` (24 hours today). Nothing refreshes a shared login with more than
  W left, and nothing refreshes it on demand.
- With less than W left, the refresher refreshes while its directory or mesh
  link is up. First it asks every holder it can reach and waits up to 10
  seconds. An answer with a newer version makes it wait up to 60 seconds for
  that record instead; after that the answer stops nothing. With no answer at
  all it refreshes only if every other holder is off the roster; a holder on
  the roster that does not answer blocks it, and it asks again every 10
  minutes.
- Fallback for a refresher that is gone: with less than W/2 left, the
  refresher off the roster (or answering `none`) since W began, and this
  device online that whole time, the online holder with the smallest device
  key takes the same steps and becomes the refresher.
- A device whose link was down at any time after W began refreshes only after
  a holder answered. This stops a returning refresher from spending a token
  the fallback already spent.
- A device about to refresh that learns, from a question or an answer, that a
  device with a smaller key is about to refresh holds off for 60 seconds.
- Refused means HTTP 400 or 401 whose `error` field (read alone, never
  logged) is `refresh_token_reused`, `invalid_grant` or
  `refresh_token_expired`: another device already refreshed or the login was
  ended, so the device asks every holder for a newer record. Anything else is
  no answer: it retries with the same refresh token after 60 seconds, then
  every 10 minutes while the access token works.
- A device whose own access token OpenAI rejects, with no newer record from
  any holder, shows `ChatGPT login was signed out. Run /login on any device;
  the others pick it up.` A device whose access token expired with no holder
  answering shows `ChatGPT login expired and no device on <network> answered.
  Run /login here, or bring another device online.` Its agents wait for a
  record or a `/login`.

Off, logout, leave, revoke:

- `plugins.hub.login_sync_enabled` (default `true`; machine-local like the rest
  of `plugins.hub`) turns sharing off on one machine: it neither sends nor
  takes the login, and its own `/login` stays its own. Only `/config` changes
  it.
- `/login logout` removes the login on this computer and writes
  `~/.kollab/oauth/openai.off`, so no record refills it; `/login` here removes
  only that marker. It says: `Logged out on this computer. The other devices
  keep the ChatGPT login; to end it everywhere, log out of all devices at
  chatgpt.com.`
- `/connect leave` by a holder drops its copy unless it is the only holder:
  `Left <network>. The ChatGPT login stays with the network; run /login here
  for one of your own.`
- `/connect revoke <device>` of a holder, and `/connect rotate`, stop sending
  the login and end with: `<device> still holds the ChatGPT login. Run /login
  here to move <network> to a new one. If <device> was lost or stolen, first
  log out of all devices at chatgpt.com.` The new login starts a new token
  chain that every holder takes; the old holder can only spend its own copy.
  Revoking never revokes the login at OpenAI.

Screens, changed when this ships:

- The join request line gains `[s]hare login`. After `a`, the accept line
  (Story 1 and the accept bullet above) is `sealed config queued: settings,
  agents, skills, mcp, api keys`; after `s` it adds `chatgpt login shared with
  <device>`.
- Story 1's line under the joined line: `Settings arrive sealed from <primary
  name>.`, then `The ChatGPT login arrived from <primary name>.` or `Run /login
  here, or /connect share <this device> on <primary name>.` (or the
  different-account line).
- The Connect screen's `login` row, in the terminal and in web Settings >
  Connect (`kollabor/panels/connect.py`): `chatgpt (shared)`, `chatgpt, this
  computer only`, or `none`.
- `/login status`: `chatgpt: shared, refreshed by <device>` in place of
  "expired (will auto-refresh on next use)".
- Story 8's last sentence, Story 9's revocation line, and the comments in
  `enrollment_client.py` (`_profile_credential_category`) and `relay_agent.py`
  (synced loadout activation) that say OAuth never travels.

Known limits, so nobody claims more:

- Two holders that cannot reach each other but both reach OpenAI can both
  refresh. One is then signed out, and every holder is if OpenAI ends the
  whole login on reuse, which milestone 5 measures. Recovery is one `/login`
  on any holder.
- A refresh OpenAI processed but whose answer was lost (lid closed, Wi-Fi
  gone) spends the token with nobody holding the new pair: the holders are
  signed out until a `/login`.
- Every holder has a refresh token. A stolen holder is a working login until
  "log out of all devices" at chatgpt.com, and any holder can sign every
  holder out. Share only with devices you would hand your laptop to.
- W follows the token's own lifetime, so a change at OpenAI moves the window
  with it; a refusal ends in the signed-out line.

## 10. The mesh (#99)

Shipped in milestone 3 (`feac607`, from `e02e761` on branch `mesh-direct-bootstrap`).
Devices of one network that can reach each other directly talk without the
directory, and a device forwards for the ones that cannot. The roster shows the same
names either way and no command changed.

- **Direct first, relay second.** A device with a TLS endpoint signs a short-lived
  locator: its endpoint address, the session it runs under, both keys. Approved
  devices learn it from LAN discovery (a multicast datagram, when scanning is on). A
  message goes over the direct endpoint while the locator is live, and takes the relay
  path when the direct dial is refused, times out or answers garbage. Signed records
  and links spread by gossip through the peer exchange.
- **Forwarding.** B carries opaque frames for A and C. A seals to C end to end, so B
  never reads the text, and C applies its own trust to A exactly as through the
  directory. B forwards only along a signed route whose links allow it, only while its
  own switch is on, and it bounds what it carries for others: 120 frames a minute per
  peer, 600 in total, 16 at once. A frame delivered to B itself is not counted.
- **A device with no relay.** A device that is not registered with the relay runs under
  a per-process direct session that its locator or its signed record names. Two devices
  on one host work the same way: a discovery datagram from this host's own interface
  address is accepted (a cloud host's address is public); any other public source is not.
- **Endpoint names.** A locator vouches for its endpoint name only when an approved
  device's relay key signed it. The local registry can only deny: a name it holds under
  another key, or rejected, is never admitted. A name two approved devices claim with
  different keys admits neither. A device admitted this way reaches the peer carrier
  (`peer_forward`, `peer_secure`) and nothing else: no Hub message, no ping.
- **Strangers** (Story 5) get no mesh records, links or forwarding, on the relay path
  and on the direct endpoint.
- **On by default, two off switches.** `plugins.hub.peer_direct_enabled` and
  `plugins.hub.peer_forward_enabled` both default to `true`. Neither opens a socket: the
  TLS endpoint (`endpoint_enabled`), LAN discovery (`peer_discovery_scan_enabled`,
  `peer_discovery_advertise_enabled`) and private addresses
  (`peer_allow_private_network`) stay off until set. A device without an endpoint
  identity keeps direct links off. A route needs forwarding on at every device on it,
  the two ends included, which is why every device defaults on.

Members: the mesh needs every device on a route to approve every other, so devices
spread approval (section 4, Members). A network of three or more routes once the
signed member lists have crossed. A designation two members claim goes to the one
approved first.

Proof: `tests/unit/test_mesh_network.py` (three real bridges, A and B on one wire, C on
none) covers the route, the sealing, C's own trust, the session invariant, the limits,
the defaults, both switches and the locator pin. `tests/live/m3/` proves it on installed
packages: A (Mac) -> B (server) -> C (a second device on server with no relay,
reachable only through B), clean transcript. Written, not yet run live; the run order
and how C is made relay-less are in `tests/live/m3/README.md`.

## 11. Self-hosting a directory

`kollab relay serve --domain <domain>` is the whole setup (Story 6). One process
runs the relay, keeps the signed discovery document published and serves it as
the key file, all on one local port. What stays with the operator is one
`_agent.<domain>` TXT record and a TLS proxy with five routes; the command
prints both. Operator detail is in
[kollabor-ai-discovery-publication.md](../operations/kollabor-ai-discovery-publication.md).

- **State.** `~/.kollab/relay/<domain>` (`--state-dir` moves it): the signing key
  (mode 0600, in a 0700 directory) and the revision counter, one process per
  directory. This is the published identity. Restarts reuse it; lose it and every
  device that pinned the old key refuses the new one, so it gets backed up. A
  directory that runs the standalone publisher today is adopted as it is.
- **What it prints.** The TXT record value and the five routes (key file, health,
  websocket, `enrollment/*`, `contact/*`). `--print nginx|caddy` prints that
  proxy config and `--print systemd` a unit that runs the same command as the
  same user on the same state directory. `--install` writes that unit (sudo when
  not root), enables and starts it, after creating the state directory the unit
  is confined to; `--uninstall` removes it. Without them nothing is written.
- **Client addresses.** A proxy on the same machine is trusted for `X-Real-IP`, so
  per-address limits see the client and not the proxy. A proxy elsewhere needs
  `--trusted-proxy <ip>`; an office behind one address needs
  `--max-connections-per-source`.
- **Backend.** One worker on the in-memory backend. The managed Valkey sidecar
  never persisted either: a join code or knock that is waiting at a restart ends,
  everything else is rebuilt as devices reconnect. Several workers or hosts keep
  `kollab relay run --config`, which is what kollabor.ai runs; it and the bare
  worker `kollab relay serve --origin` are unchanged.

## 12. Milestones and the proof bar

1. **Simple flow, 0.11.0.** Sections 4 to 8. Proven as a user on installed
   packages on the Mac and server: Story 1, Story 2 and Story 3 exactly as
   written, transcript clean on both sides, at 80 and 120 columns.
2. **Sealed config sync.** Section 9. Story 8. Proven on the Mac and server
   from installed packages, on the network the milestone 1 proof joins: the Mac
   switches its loadout and, within 60 seconds, `/config` on the server shows
   the same loadout marked `managed by <mac device>`; the API key exists on
   the server (same digest, mode 0600); no pane and no log on either host shows
   a key; the server's OAuth login and vaults are untouched; a skill made and
   deleted on the Mac appears and disappears on the server (`tests/live/m2`).
3. **Mesh.** Section 10.
4. **One-command self-host.** Section 11. Proven on selfhost.kollabor.ai
   (server) with the one command in place of the relay, the publisher and the
   static server: two devices join a network on that domain and exchange a
   message, a restart of the command keeps the published key and both devices
   come back, and nothing touches kollabor.ai (`tests/live/m4`).
5. **Shared login.** Section 9, "The shared login". Proven on the Mac and
   server from installed packages (`tests/live/m5`), with the m3 relay-less
   device on the server as a third holder and the test-only
   `KOLLAB_LOGIN_LIFETIME_SECONDS` (a token counts as expiring at its `iat`
   plus this; W is a tenth of it):
   - `s` at accept and `/connect share`: the server, with no login of its own,
     runs a ChatGPT turn within 60 seconds, holding the same login digest at
     mode 0600; `a` alone shares nothing.
   - Exactly one refresh request per window across all three logs, and every
     holder on the new version within 60 seconds.
   - Mac stopped: the fallback refreshes once, and the returning Mac takes its
     record without refreshing.
   - Directory stopped through W/2: no refresh while it is down, exactly one
     after it returns.
   - The Mac comes back with an expired login while the others are
     unreachable: no refresh, and the one line.
   - Revoke an offline holder, then `/login` on the Mac: no sign-out over the
     next two windows.
   - Logout survives the next record, leave drops the copy, a device logged in
     as another identity keeps its login and shares nothing, and a record
     whose claim names another identity is refused.
   - Two processes forced to refresh at once on one machine make one refresh
     request.
   - No pane, log, event, tool result, model request, history, or attach or
     daemon reply on any host shows a token.
   - Once, on a separate `/login` chain, reuse a spent refresh token and record
     whether the newer pair still refreshes. The answer goes into Known limits.

"Proven" means the live run, not unit tests. Every milestone updates this
document before it merges.

## 13. Rules for agents working on this

- This document wins. Update it in the same PR as any behaviour change.
- The remnant rule in section 1. If a name, tag, doc or code path is not here,
  it is not valid. Ask Marco before extending it.
- Do not add a `/connect` subcommand, a hub tag, or a config key that is not
  in this document.
- Do not change how the local hub works. The network adopts hub rules; the
  hub does not adopt network rules.
- Keys, `relay:` addresses and receipts never appear in a screen a human
  reads. Names do.
- No PR, issue or release without Marco's word.
- Live proof on both machines before "done". A clean transcript is the bar.
- Commits reference an issue. No attribution footers.

## 14. Glossary

- **hub**: agents on one machine and workspace, open channel.
- **network**: a hub across machines, with a name, a directory and a trust level.
- **directory**: kollabor.ai or a self-hosted domain: signed key file, relay, enrollment and contact mailboxes.
- **relay**: the directory's forwarder for machines that cannot reach each other directly. It sees sealed frames only.
- **device**: one workspace identity with a human name.
- **agent@device**: how any agent on the network is addressed.
- **join code**: `XXXX-XXXX`, one device, five minutes.
- **trust**: `open`, `agents`, `manual`, per network.
- **knock**: a sealed introduction from outside the network.
- **link**: the directory's record that two device keys in different rooms each signed a consent to reach the other; how an accepted stranger's messages cross rooms.
- **primary**: the device whose config the network follows.
- **shared login**: the ChatGPT login a human shared with chosen devices; the device that made the newest record refreshes it (section 9).
- **holder**: a device holding the shared login for one identity.

## 15. Open, ask Marco

- The join-time key copy. Accepting a join still copies the issuer's active
  API-key profile once, as a private profile `kollab-<16 hex>` on the joining
  device (the milestone 1 provisioning path). The sealed config carries that
  key now, so the copy is redundant and a duplicate loadout can show on the
  joined device. Removing it touches the delegation store, the recovery
  journal and the challenge scope, so it stays until Marco says remove it.

- Story 1's no-network screen. Joining Marco's network by code needs a code
  field on a device with no network, so bare `/connect` there opens the code
  form (domain prefilled `kollabor.ai`, code first). To make "`/connect` joins
  kollabor.ai on its own" true for a first device, an empty code plus Enter
  starts a network on that domain. Marco confirms, or wants it automatic.

- Editing the network's name. The first device gives it `<first device name>-net`
  (section 4) and no command changes it: none of the thirteen does, and a
  fourteenth needs Marco's word. Options: `/connect name` takes a network form,
  or the name stays as the first device chose it.
- Whether observed remote messages should be shown dimmed on every device in
  large networks, or only on the two devices involved. Hub behaviour says
  everyone; kept until it hurts.
