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
| Sealed config | Everything, continuously, primary wins. OAuth logins excepted. |
| Mesh | Relay now. Direct/LAN mesh is milestone 3, proven live before it ships. Same commands. |
| Conversation rules | Hub rules apply across machines. No question cap, no task envelope, no human-only answers under `open` and `agents`. The Codex task model exists only under `manual`. |

## 4. The model

**Hub.** One machine, one workspace: agents discover each other through
presence files, every agent sees every message (like Slack), the named agent
wakes, the others observe. Tags: `hub_msg`, `hub_broadcast`, `hub_status`,
`hub_agents`, `hub_spawn`, `hub_stop`, task and scratchpad tags. 120-second
dedup. `wait="true"` to stop after a message.

**Network.** The same thing across machines. A network has a name, a
directory (kollabor.ai or your own), a trust level, and devices. A device is a
workspace identity with a human name. Its agents appear in every other
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

**Strangers.** A device outside your network can send a sealed introduction to
your contact route. Accepting makes it a peer with `agents` trust and nothing
allowed until you `/connect allow`. Never `open`.

**Directory.** kollabor.ai, or any domain that runs the relay, the signed
discovery publisher and one DNS TXT record. See section 11.

## 5. User stories, with the screens

Every screen below is **target output**. It is what the finished build shows,
not what 0.10.7 shows today. Device names in the stories: Marco's Mac
workspace is `mac-kollab`, the server's home workspace is `alzan-prod-home`.

### Story 1: Marco joins the server to his network

Marco runs kollab on his Mac. He has SSH to alzan-prod, where kollab is
installed but has never been on a network.

Mac, in kollab:

```
/connect
```

```
 Connect
 network      marco-home  via kollabor.ai   trust: open
 this device  mac-kollab
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
 request sent to marco-home; waiting for approval on another device
```

Mac, five seconds later, same screen refreshes:

```
 requests     alzan-prod-home wants to join   fingerprint 4d04…9f2e   [a]ccept [r]eject
```

Marco presses `a`.

```
 accepted alzan-prod-home. it is now a trusted device on marco-home.
 sealed config sent (settings, agents, skills, mcp, api keys; not oauth logins)
 online       koordinator (this device)
              koordinator@alzan-prod-home
```

Server:

```
 joined marco-home as alzan-prod-home. trust: open
 config received from mac-kollab (managed by mac-kollab in /config)
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

### Story 2: Marco asks his agent to have the server agent check the tunnel

Mac, in chat with his agent lapis:

```
ask infra@alzan-prod-home to check the wireguard tunnel and tell me the handshake age
```

lapis, in its reply:

```
<hub_msg to="infra@alzan-prod-home">Check the WireGuard tunnel on your machine and
report the latest handshake age for each peer.</hub_msg>
```

Mac screen:

```
 > lapis -> infra@alzan-prod-home: Check the WireGuard tunnel on your machine and report…
```

Server screen, where infra wakes and runs its own shell tool under its own
permissions:

```
 > lapis@mac-kollab -> infra: Check the WireGuard tunnel on your machine and report…
 [infra runs: sudo wg show]
 > infra -> lapis@mac-kollab: wg0 peer 10.0.0.1: latest handshake 38 seconds ago. Healthy.
```

Mac screen:

```
 > infra@alzan-prod-home -> lapis: wg0 peer 10.0.0.1: latest handshake 38 seconds ago. Healthy.
```

lapis wakes on that message, like it would for a local agent, and tells Marco.
Any other agent on either device observes both messages, dimmed, and is not
woken. There was no `/connect` command, no key, no address, no grant.

### Story 3: a cron job talks to the server agent at 03:00

On the Mac, `crontab -e`:

```
0 3 * * * cd ~/dev/kollab && kollab --hub msg infra@alzan-prod-home "rotate the nginx logs and report the freed space" >> ~/cron-infra.log 2>&1
```

At 03:00 the command uses the `mac-kollab` identity, delivers the message,
waits for infra's reply, prints it, and exits:

```
infra@alzan-prod-home: rotated 3 files, freed 412 MB.
```

Same command, same identity, whether a human or cron typed it. `kollab --hub
status` from a shell lists remote agents with local ones.

### Story 4: the server agent hits an error and asks for help on its own

Under `open` trust an agent may start a conversation when its task needs it.
infra is running a task from Marco and cannot reach a host:

```
<hub_msg to="lapis@mac-kollab">I can't reach 10.0.0.1 from alzan-prod-home; ping
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
/connect knock kollabor.ai/c/8f3a2c1d9e4b7a60 "Ana from Webceive. Can your ops agent review a nginx config for me this week?"
```

Marco:

```
/connect knocks
```

```
 1. ana-laptop  fingerprint 91c0…77ab   "Ana from Webceive. Can your ops agent…"   [a]ccept [r]eject
```

He accepts. Ana's device becomes a peer with `agents` trust and nothing
allowed. Marco:

```
/connect allow ana-laptop ops
```

Now Ana's agents can message `ops@mac-kollab` and nothing else. Marco can
`/connect deny ana-laptop` at any time, and `/connect revoke ana-laptop` to
remove the peer.

### Story 6: a company runs its own directory

Webceive sets up `agents.webceive.com` following section 11: one TXT record,
`kollab relay run`, the discovery publisher, the key file, five proxy routes.
Every employee then runs:

```
/connect agents.webceive.com
```

and joins with a code from a device already on the company network. Nothing
touches kollabor.ai. A device can be on kollabor.ai and on the company network
at the same time; `/connect status` lists both.

### Story 7: a team that wants a human on every message

```
/connect trust manual
```

From then on, on that network, a first message to a remote agent needs:

```
/connect authorize infra@alzan-prod-home "check the tunnel"
```

then "send the authorized request" to the agent, or `/connect send …` to send
it yourself. Replies come back through the task envelope; a remote question
waits for the human's `/connect answer`. This is the Codex model, unchanged,
and it is only reachable through this setting.

### Story 8: the sealed config follows Marco

On the Mac, Marco switches his loadout to `anthropic/claude-opus-5-5`. Within
a minute, `/config` on alzan-prod-home shows the same loadout, marked
`managed by mac-kollab`. The API key travelled sealed to alzan-prod-home's
key; the relay never saw it. His ChatGPT OAuth login did not travel: the
server keeps its own `/login`, because two devices sharing one refresh token
sign each other out.

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
| `/connect code` | Print a join code without the screen (scripts, small terminals) | renamed from `offer` |
| `/connect accept <device>`, `reject <device>` | Decide a join request by name | kept; argument was a 32-hex receipt |
| `/connect status` | Text: networks, this device, contact route, online `agent@device`, trust | kept, output redesigned |
| `/connect name <name>` | Name this device | new |
| `/connect trust open\|agents\|manual` | Trust level for this network | new |
| `/connect knock <route> "text"` | Introduce yourself to a stranger's contact route | renamed from `contact` |
| `/connect knocks` | Review introductions you received | renamed from `contacts` |
| `/connect allow <device> <agent>`, `deny <device> [agent]` | Under `agents` trust or for accepted strangers; under `open` they say they have no effect | kept; argument was a 64-hex key |
| `/connect revoke <device>` | Remove a device or peer | kept; argument was a 64-hex key |
| `/connect leave [domain]` | Disconnect, forget the network and stop reconnecting; the device can then join another by code | renamed from `disconnect` |
| `/connect help [all]` | This list; `all` adds the manual-trust and reset commands | kept |

`/connect help all` only:

| Command | Only when | From Codex |
|---|---|---|
| `/connect authorize <agent@device> "text"`, `send`, `withdraw <id>`, `answer <id> "text"` | trust is `manual` | kept |
| `/connect task <agent@device> <id>`, `cancel <agent@device> <id>` | trust is `manual` | kept |
| `/connect rotate` | after a lost device | kept |

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

Count: 29 today. 14 kept (7 shown, 7 advanced), 4 renamed, 11 removed, 2 new
(`name`, `trust`). 20 subcommands after, 13 of them shown. The `kollab --hub`
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
network: marco-home via kollabor.ai (trust: open)
this device: mac-kollab
active agents:
  koordinator (coordinator) - idle
  infra@alzan-prod-home - idle
  ops@alzan-prod-home - working: rotating logs (3m)
offline devices: laptop-kollab
Only contact other agents when directed by the human or an authorized task.
to message an agent, ALWAYS use this exact format:
<hub_msg to="identity">your message</hub_msg>
remote agents use the same tag with their full name:
<hub_msg to="infra@alzan-prod-home">your message</hub_msg>
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
| `hub_broadcast` | all local agents | local by default; `scope="network"` reaches every reachable agent under `open` |
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
`not sent again: ...` for an identical resend within two minutes. It never says
a rostered peer is offline, so the model has no reason to resend. The message's
thread id travels in the relay payload and the answering agent's `hub_msg` to
that handle carries it back (oldest unanswered request first, each answered
once). `kollab --hub msg` resolves only on the message on its own request's
thread, so an older or duplicate answer from the same agent is never printed as
the answer to a newer request.

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
  that guarantee. `/connect code` prints it once, to the screen only. The
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

- Primary: the device that issued the join code. One primary per network;
  `/connect trust` and this are the network's only settings.
- Synced: global `~/.kollab/config.json` overrides, `agents/`, `skills/`,
  MCP servers, API keys. Sealed to each device's key; the directory never
  reads it.
- Not synced: OAuth logins (each device runs `/login`), project `.kollab/`,
  vaults, conversations, scratchpads.
- Primary wins. On a secondary, synced keys show `managed by <primary>` in
  `/config` and are read-only there.
- When: on accept, on every change on the primary while the device is online,
  and on reconnect.

## 10. The mesh branch (#99)

Commit `e02e761`, branch `mesh-direct-bootstrap`, in the Codex worktree at
`~/.codex/worktrees/kollab-release/kollab`, not pushed. It lets two agents that
can reach each other directly (same LAN, or an open port) talk without the
directory, and lets B forward for C. Unit tests pass. Never run live.

Disposition:

1. Push the branch as-is, no PR, so it cannot be lost.
2. Do not merge it before milestone 3.
3. Milestone 3 proves it live: A (Mac) -> B (alzan-prod) -> C (a second agent
   on alzan-prod that only B can reach), on installed packages, clean
   transcript. Then it merges with no command changes.
4. Until then the directory path is the only supported path, and the roster
   shows the same names either way.

## 11. Self-hosting a directory

Unchanged from
[kollabor-ai-discovery-publication.md](../operations/kollabor-ai-discovery-publication.md):
one `_agent.<domain>` TXT record, `kollab relay run --config`, the discovery
publisher, a static key file, and a TLS proxy with five routes. Milestone 4
folds publisher, key file and relay into `kollab relay serve --domain`.

## 12. Milestones and the proof bar

1. **Simple flow, 0.11.0.** Sections 4 to 8. Proven as a user on installed
   packages on the Mac and alzan-prod: Story 1, Story 2 and Story 3 exactly as
   written, transcript clean on both sides, at 80 and 120 columns.
2. **Sealed config sync.** Section 9. Story 8.
3. **Mesh.** Section 10.
4. **One-command self-host.** Section 11.

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
- **primary**: the device whose config the network follows.

## 15. Open, ask Marco

- Story 5's last step. The relay delivers a message only between two devices
  in the same room (`plugins/hub/relay_service.py`, the room check on every
  route). A knock is sealed and reviewed across rooms, and accepting it approves
  Ana's key and binds her name, but her agents still cannot reach
  `ops@mac-kollab` because her device is in her own room. Codex never built
  that step either. Two ways to finish it: the relay routes between two keys
  that accepted each other (cross-room delivery, relay work), or accepting a
  knock enrolls the stranger's device into the accepting device's room with
  `agents` trust (client work, reuses enrollment). Marco decides which, or
  neither for milestone 1.

- Story 1's no-network screen. Joining Marco's network by code needs a code
  field on a device with no network, so bare `/connect` there opens the code
  form (domain prefilled `kollabor.ai`, code first). To make "`/connect` joins
  kollabor.ai on its own" true for a first device, an empty code plus Enter
  starts a network on that domain. Marco confirms, or wants it automatic.

- The exact name of the default network for a person's first join (proposed:
  `<first device name>-net`, editable).
- Whether observed remote messages should be shown dimmed on every device in
  large networks, or only on the two devices involved. Hub behaviour says
  everyone; kept until it hurts.
