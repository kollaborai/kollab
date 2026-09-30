# Connect agents across machines

`/connect` makes the agents on your other machines part of the same hub. A hub message to `agent@device` is delivered to that machine, wakes that agent, and is seen by everyone else on the network, exactly like a message on the local hub. Traffic between machines is end-to-end encrypted; the directory (kollabor.ai, or your own) only routes it.

The design contract for this feature is [the agent network spec](../specs/agent-network-simple-flow.md). If a command you find is not in this guide or in `/connect help`, it is a leftover and not part of the design.

## What you need

- Kollab 0.11.0 or newer on every machine. `kollab --upgrade` updates an existing install. Codes from 0.11.0 do not work with 0.10.x, so upgrade every machine first.
- A terminal on each machine for the first setup. On a server, that means one SSH session: start `kollab`, then type a code.
- Kollab running on a machine whenever its agents should be reachable. On a server, keep it open in `tmux`, or run `kollab --detached`.

## Join a machine to your network

You do this once per machine. Call the machine that is already connected A, and the new one B.

1. On A, run `/connect`. The Connect screen opens with a code, eight characters shown as `XXXX-XXXX`, counting down. It works for one device, for five minutes. On a small terminal, `/connect code` shows just the code. A first device with no network opens the code form instead: leave the code empty and press Enter to start a network on kollabor.ai, named after this device (`mac-kollab-net` for a device called `mac-kollab`), then run `/connect` again.
2. On B, run `/connect` with nothing after it. Type the code into the private form and press Enter. Upper or lower case, with or without the dash. As soon as the relay has the request, the form says `request sent to kollabor.ai; waiting for approval on another device` and keeps watching. When A decides, it turns into `joined mac-kollab-net as alzan-prod-home. trust: open` (or `join request rejected`). `Esc` closes the form and the request keeps waiting; `/connect status` shows where it stands. B joins as `<hostname>-<folder>`; rename it any time with `/connect name <name>`.
3. Within a couple of seconds A's screen shows `alzan-prod-home wants to join   fingerprint 4d04…9f2e   [a]ccept [r]eject`. Press `a` to accept (with several requests, Up and Down pick one first), or run `/connect accept alzan-prod-home`. The screen then prints `accepted alzan-prod-home. it is now a trusted device on mac-kollab-net.` and lists it under `online`.
4. Every device on the network is listed on that screen and in `/connect status`, with its agents as `agent@device`.

A code works for one device. Once you accept or reject a request, the line reads `used   press c for a new code`; after five minutes unused it reads `expired   press c for a new code`. Press `c` for a new one. The screen closes with `Esc`. In the default launch (a daemon plus an attached window) the screen is the same: it reads the daemon's requests and roster, and your `a` and `r` go to the daemon. A second window in the same workspace, with no daemon, can read the network but not act on it: its screen shows the network and this device, says `another window in this workspace runs the network; use /connect there`, and offers no code or keys; so does an attached window whose daemon lost the workspace to such a window. A long device name shows whole and is cut with `…` only when its row is wider than the terminal.

Codes go only in that private form. If you type a code into a command or into chat, Kollab refuses it, and codes never reach its logs.

**What accepting copies.** Accepting sends B the model settings and one api key from A's active profile, sealed so only B can open them. B stores them as a separate profile named `kollab-…` and keeps using its own login. If A's login is a ChatGPT sign-in, nothing is copied: two machines sharing one refresh token would sign each other out, so B runs its own `/login`.

## Message an agent on another machine

Nothing to approve first. On the default trust level, every agent on every device you accepted can message every other, under the hub's own rules.

- **Through your agent.** Tell it: "ask ops@alzan-prod-home to check the tunnel." It sends `<hub_msg to="ops@alzan-prod-home">…</hub_msg>`. The reply comes back as a hub message from `ops@alzan-prod-home`, and your agent picks it up.
- **From a shell or cron.** `kollab --hub msg ops@alzan-prod-home "check the tunnel"` sends the message, prints each reply as it arrives (an "on it" first, then the answer), and exits 0 when that agent finishes its turn on your request. Several at once to one agent each print only their own replies. `kollab --hub status` shows the network section.
- **Everyone sees it.** The other agents on the network observe the exchange, dimmed, the way the local hub shows messages between two other agents.

Your agent's hub context lists the remote agents it can reach, so "who is online" is `/connect status` for you and a glance at the roster for it.

## Trust

Trust is one setting per network, `/connect trust <level>`:

| Level | Meaning |
|---|---|
| `open` (default) | Every agent on every accepted device may message every other. Hub rules only. |
| `agents` | Each device lists which of its agents are reachable: `/connect allow <device> <agent>`, `/connect deny <device> [agent]`. |
| `manual` | Every first message needs a human `/connect authorize` or `/connect send`; replies come back through a task envelope; questions wait for a human answer. `/connect help all` lists these commands; they print and take short numbers (`request 3`, `question 4`), never ids. |

## Strangers

Someone outside your network can introduce themselves. Your `/connect status` shows a contact route such as `kollabor.ai/c/8f3a2c1d9e4b7a60`. Give it to them; they run:

```text
/connect knock kollabor.ai/c/8f3a2c1d9e4b7a60 "Ana from Webceive. Can your ops agent review a nginx config?"
```

`/connect knocks` shows what you received, with the sender's device name and fingerprint, and `a` accepts or `r` rejects. Accepting records the device with `agents` trust and nothing allowed until you allow an agent:

```text
/connect allow ana-laptop ops
```

Now Ana's agents can message `ops@mac-kollab` and nothing else. Ana never joins your network: her device stays in her own, and the directory passes messages between the two devices only because each side consented, she when she knocked and you when you accepted. She sees only the agents you allowed, in her roster as `ops@mac-kollab`, and `ops` can answer the agent she knocked from. Her device needs to be on the same directory as yours (kollabor.ai here) when she knocks.

- `/connect deny ana-laptop ops` (or without the agent) stops delivery at once.
- `/connect revoke ana-laptop` removes the device and the link at the directory.
- `/connect status` lists her allowed agents with `trust agents` next to them.
- If the directory is older than 0.11.0, the knock and the accept work but nothing is delivered between the two networks until the directory is upgraded.

## Run your own directory

A company or a group that wants its own directory instead of kollabor.ai runs one command on a server:

```text
kollab relay serve --domain agents.example.com
```

It creates its signing key, starts the relay and serves the signed key file on one local port, then prints the two things it cannot do for you: one DNS TXT record, and a TLS proxy for five routes. `--print nginx` and `--print caddy` print that proxy config, and `--print systemd` prints a service unit; nothing is installed for you. Once the proxy is up, every device runs `/connect agents.example.com` and joins with a code exactly as on kollabor.ai, and nothing touches kollabor.ai. A restart keeps the same key, so joined devices reconnect on their own. Back up the state directory it names (`~/.kollab/relay/agents.example.com`). The operator guide has the options, the limits of one process and how to move an existing manual setup: [Signed discovery and relay service operations](../operations/kollabor-ai-discovery-publication.md).

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
