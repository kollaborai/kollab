# Connect agents across machines

`/connect` makes the agents on your other machines part of the same hub. A hub message to `agent@device` is delivered to that machine, wakes that agent, and is seen by everyone else on the network, exactly like a message on the local hub. Traffic between machines is end-to-end encrypted; the directory (kollabor.ai, or your own) only routes it.

The design contract for this feature is [the agent network spec](../specs/agent-network-simple-flow.md). If a command you find is not in this guide or in `/connect help`, it is a leftover and not part of the design.

## What you need

- Kollab 0.11.0 or newer on every machine. `kollab --upgrade` updates an existing install. Codes from 0.11.0 do not work with 0.10.x, so upgrade every machine first.
- A terminal on each machine for the first setup. On a server, that means one SSH session: start `kollab`, then type a code.
- Kollab running on a machine whenever its agents should be reachable. `kollab service install` in the joined folder keeps it running across crashes and reboots (systemd on Linux, launchd on macOS); `kollab --detached` keeps it running until the next reboot.

## Join a machine to your network

You do this once per machine. Call the machine that is already connected A, and the new one B.

1. On A, run `/connect`. The Connect screen opens with a code, eight characters shown as `XXXX-XXXX`, counting down. It works for one device, for five minutes. On a small terminal, `/connect code` shows just the code. A first device with no network opens the code form instead: leave the code empty and press Enter to start a network on kollabor.ai, named after this device (`laptop-kollab-net` for a device called `laptop-kollab`), then run `/connect` again.
2. On B, run `/connect` with nothing after it. Type the code into the private form and press Enter. Upper or lower case, with or without the dash. As soon as the relay has the request, the form says `request sent to kollabor.ai; waiting for approval on another device` and keeps watching. When A decides, it turns into `joined laptop-kollab-net as home-server. trust: open` (or `join request rejected`). `Esc` closes the form and the request keeps waiting; `/connect status` shows where it stands. B joins as `<hostname>-<folder>`, or `<hostname>-<parent>-<folder>` when another workspace on that computer already has the name (two checkouts of one repo); rename it any time with `/connect name <name>`.
3. Within a couple of seconds A's screen shows `home-server wants to join   device ID abcd…ef01   [a]ccept [r]eject`. Press `a` to accept (with several requests, Up and Down pick one first), or run `/connect accept home-server`. The screen then prints `accepted home-server. it is now a trusted device on laptop-kollab-net.` and lists it under `online`.
4. Every device on the network is listed on that screen and in `/connect status`, with its agents as `agent@device`.

A code works for one device. Once you accept or reject a request, the line reads `used   press c for a new code`; after five minutes unused it reads `expired   press c for a new code`. Press `c` for a new one. The screen closes with `Esc`. In the default launch (a daemon plus an attached window) the screen is the same: it reads the daemon's requests and roster, and your `a` and `r` go to the daemon. A second window in the same workspace, with no daemon, can read the network but not act on it: its screen shows the network and this device, says `another window in this workspace runs the network; use /connect there`, and offers no code or keys; so does an attached window whose daemon lost the workspace to such a window. A long device name shows whole and is cut with `…` only when its row is wider than the terminal.

Codes go only in that private form. If you type a code into a command or into chat, Kollab refuses it, and codes never reach its logs.

**What accepting copies.** A's settings follow A onto every device it accepts by code, sealed so only that device can open them: the overrides in `~/.kollab/config.json` (loadouts, models, API keys), MCP servers, `agents/` and `skills/`. They go out when you accept, again within about ten seconds of any change on A (switch your loadout with `/llm` and B follows), and when B reconnects. A wins: on B each synced setting is read-only in `/config`, marked `managed by A`, and an API key shows only as `set`.

Not copied: ChatGPT sign-ins (two machines sharing one refresh token would sign each other out, so B runs its own `/login`), project `.kollab/` folders, vaults, conversations, and the settings that describe one machine (`kollabor.updates`, `kollabor.permissions`, `plugins.hub`, `plugins.voice`). A stranger you accept from a knock gets none of it.

`/connect leave` on B keeps the values it received as its own. `/connect revoke B` on A, or revoking A on B, stops the updates. Joining also copies A's active API-key profile once, as a private profile named `kollab-…`, so B can list that loadout next to the synced ones.

## Message an agent on another machine

Nothing to approve first. On the default trust level, every agent on every device you accepted can message every other, under the hub's own rules.

- **Through your agent.** Tell it: "ask ops@home-server to check the tunnel." It sends `<hub_msg to="ops@home-server">…</hub_msg>`. The reply comes back as a hub message from `ops@home-server`, and your agent picks it up.
- **From a shell or cron.** `kollab --hub msg ops@home-server "check the tunnel"` sends the message, prints each reply as it arrives (an "on it" first, then the answer), and exits 0 when that agent finishes its turn on your request. Several at once to one agent each print only their own replies. `kollab --hub status` shows the network section.
- **Everyone sees it.** The other agents on the network observe the exchange, dimmed, the way the local hub shows messages between two other agents.

Your agent's hub context lists the remote agents it can reach, so "who is online" is `/connect status` for you and a glance at the roster for it.

## Direct links and forwarding

Devices of one network reach each other directly when they can and through the directory when they cannot. A device that runs a TLS endpoint (`plugins.hub.endpoint_enabled`) advertises where it listens; approved devices dial that first and fall back to the directory if the dial fails. A device with no relay connection at all is still reachable this way, and a network member that can reach both sides forwards for them. The message stays sealed end to end, so the forwarding device carries it without reading it, up to 120 frames a minute per peer, 600 in total and 16 at once.

Both are on by default and neither opens a socket by itself. Two off switches, in `~/.kollab/config.json`:

- `plugins.hub.peer_direct_enabled: false` keeps every message on the directory.
- `plugins.hub.peer_forward_enabled: false` stops this device forwarding for others. A route needs forwarding on at every device on it, the two ends included, so nothing routes through or from a device that has it off. Messages to and from that device over the directory are unaffected.

## Trust

Trust is one setting per network, `/connect trust <level>`:

| Level | Meaning |
|---|---|
| `open` (default) | Every agent on every accepted device may message every other. Hub rules only. |
| `agents` | Each device lists which of its agents are reachable: `/connect allow <device> <agent>`, `/connect deny <device> [agent]`. |
| `manual` | Every first message needs a human `/connect authorize` or `/connect send`; replies come back through a task envelope; questions wait for a human answer. `/connect help all` lists these commands; they print and take short numbers (`request 3`, `question 4`), never ids. |

## Strangers

Someone outside your network can knock, and a knock is a call: it rings your device for 5 minutes while it is online, and the directory keeps nothing. Your `/connect status` shows a contact route such as `kollabor.ai/c/8f3a2c1d9e4b7a60`. Give it to them; they run:

```text
/connect knock kollabor.ai/c/8f3a2c1d9e4b7a60 "Ana from Acme. Can your ops agent review a nginx config?"
```

Your terminal says `ana-laptop is knocking. /connect knocks to answer`. `/connect knocks` shows the ringing knock with the sender's device name, device ID and text: `a` accepts, `r` rejects, `b` blocks. A knock nobody answers lands under missed (on your device, 20 at most), where `k` knocks back. Accepting records the device with `agents` trust and nothing allowed until you allow an agent:

```text
/connect allow ana-laptop ops
```

Now Ana's agents can message `ops@laptop-kollab` and nothing else. Ana never joins your network: her device stays in her own, and the directory passes messages between the two devices only because each side consented, you when you accepted and she when your accept reached her. She sees only the agents you allowed, in her roster as `ops@laptop-kollab`, and `ops` can answer the agent she knocked from. Her device needs to be on the same directory as yours (kollabor.ai here) when she knocks.

- `/connect deny ana-laptop ops` (or without the agent) stops delivery at once.
- `/connect revoke ana-laptop` removes the device and the link at the directory.
- `/connect status` lists her allowed agents with `trust agents` next to them.
- Ana hears `unavailable` for anything but an accept: you were offline, nobody answered, you rejected or blocked her, or you let nobody knock. Her device redials for an hour.
- `/connect knocks contacts for 2h` lets only your contacts knock for two hours; `nobody` is do not disturb. `/connect expect <route>` accepts that route's first knock without asking.
- A directory that cannot carry knocks says `<domain> needs an update to carry knocks`.

## Run your own directory

A company or a group that wants its own directory instead of kollabor.ai runs one command on a server:

```text
kollab relay serve --domain agents.example.com
```

It creates its signing key, starts the relay and serves the signed key file on one local port, then prints the two things it cannot do for you: one DNS TXT record, and a TLS proxy for four routes. `--print nginx` and `--print caddy` print that proxy config (on a shared host, `--unix-socket` lets only the proxy reach the relay); `--install` installs and starts it as a systemd service (`--print systemd` shows the unit instead). Once the proxy is up, every device runs `/connect agents.example.com` and joins with a code exactly as on kollabor.ai, and nothing touches kollabor.ai. A restart keeps the same key, so joined devices reconnect on their own. Back up the state directory it names (`~/.kollab/relay/agents.example.com`). The operator guide has the options, the limits of one process and how to move an existing manual setup: [Signed discovery and relay service operations](../operations/kollabor-ai-discovery-publication.md).

## If something goes wrong

| You see | Do this |
|---|---|
| `connect: unknown subcommand '…'` | Check the spelling with `/connect help`. |
| `connect: codes never go in a command…` | Run `/connect` alone and type the code into the form. |
| `unknown agent@device: run /connect status to see who is online` | The device is offline or the name is wrong. `/connect status` lists what is reachable. |
| `…the relay connection restarted after this code was created…` | That code can't be used any more. Press `c` on the `/connect` screen, or run `/connect code`, for a new one. |
| A device is listed as offline | Kollab has to be running on it. Start it there. |
| `connect: this window is not connected to its agent daemon; restart kollab` | Quit with `/quit` and start `kollab` again. |

To update Kollab, run `/upgrade` inside Kollab or `kollab --upgrade` in a shell. After the restart, the connection comes back by itself.

## Other commands

- `/connect leave` goes offline and stops reconnecting. `/connect revoke <device>` removes a device from your network.
- `/connect help` lists the 13 commands above. `/connect help all` adds the `manual` trust commands and `rotate`.
- The old names from before this design (`offer`, `enroll`, `requests`, `peers`, `agents`, `contact`, `invite`, `join`, `disconnect`, and others) print where to go now.

For full details, see the [command reference](../reference/commands.md).

## First launch: the guided setup

The first time a machine runs 0.11.0, kollab shows one notice before you have
typed anything:

```
New: connect your agents across computers.
Enter sets it up now; Esc for later (/connect any time).
```

- **Enter, on a computer with no network, or alone on one.** Choose *Start a new
  network on kollabor.ai* or *Join with a code*. A computer alone on a network (no
  other device, no inviter; every 0.10.7 launch made one) counts as not set up:
  *Start* keeps its own network and *Join with a code* replaces it, and a network
  with another computer on it is never replaced. Starting a network opens the Connect screen
  with the join code and an "On your other computer" box: run
  `kollab --upgrade` (it knows how kollab was installed), run `kollab` and press Enter
  on the same notice, choose *Join with a code* and type the code. Joining opens
  the private code form, as `/connect` does.
- **Enter, on a computer that shares a network with another computer.** Opens the Connect screen, with a join code and the same "On your other computer" steps.
- **Esc.** Leaves it for later; `/connect` does the same thing at any time.
- **After a join.** One line says settings arrive sealed from the computer that
  issued the code, and that a ChatGPT login does not travel: run `/login` on this
  computer.

The notice is asked once per machine. Enter and Esc both count as an answer and
write one marker file, `~/.kollab/connect-guide-seen`; quitting without answering
shows it again next launch. Delete the file to see it again. It never appears in
pipe mode, in detached or daemon agents, with a query on the command line, or
without a terminal. The code is typed only in the private form, never in a
command, an argument or a log.
