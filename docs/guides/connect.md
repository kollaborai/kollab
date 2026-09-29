# Connect agents across machines

`/connect` puts your Kollab agents on different machines into one private network, so one agent can hand another a task. Traffic between machines is end-to-end encrypted. kollabor.ai only routes it, and nothing is shared until you approve it.

This guide covers what most people need. The other subcommands are explained under [Other commands](#other-commands).

## What you need

- Kollab 0.10.7 or newer on each machine. `kollab --upgrade` updates an existing install.
- A terminal on each machine for the first setup. On a server, that means one SSH session: start `kollab`, then paste a code.
- Kollab running on a machine whenever its agent should receive work. On a server, keep it open in `tmux`, or run `kollab --detached`.

## Connect two machines

You do this once. Call the machine that is already set up A, and the new one B.

1. On A, run `/connect kollabor.ai`. Kollab verifies kollabor.ai's signed key and connects. It reconnects on every launch after that.
2. On A, run `/connect offer` and press Enter. A code appears. It works for one device, for five minutes.
3. On B, type `/connect` with nothing after it and press Enter. In the form, press Tab to reach **Private code**, paste the code, and press Enter. You get a receipt ID.
4. On A, run `/connect requests` to see the request, then `/connect accept <receipt-id>`.
5. Check both machines with `/connect status`. You should see `beacon: online` and `online peers: 1`.

Codes go only in that private form. If you paste a code into a command or into chat, Kollab refuses it, and codes never reach its logs.

**What accepting copies.** Accepting sends B the model settings and one login from A's active profile, sealed so only B can open them. B stores them as a separate profile named `kollab-…` and keeps using its own login. If A's login is a ChatGPT sign-in, don't switch B to the copied profile. Both machines would then share one refresh token, and the first refresh on either side signs the other one out.

## Give another agent a task

Agents only talk to each other when a human asks, so you approve each first message.

On the machine that will do the work (B):

1. Run `/connect allow <A's public key> <agent name>`. A's key is the `your public key:` line of A's `/connect status`, and the agent name is the `agent identity:` line of B's. You do this once per peer.

On the machine that is asking (A):

2. Run `/connect agents <B's public key>`. It lists B's agents that allowed you. Copy the `relay:…` address.
3. Send the request one of two ways:
   - Yourself: `/connect send <address> <request>`.
   - Through your agent: `/connect authorize <address> <request>`, then tell your agent "Send the authorized request." It sends exactly that text with its `hub_msg` tool.
4. Wait for the reply:
   - `[relay progress]` lines appear while B's agent works with its own tools, under B's permissions.
   - If B's agent needs something, it asks once: `[relay question] …`. Tell your agent the answer, and it sends it back.
   - The finished answer arrives as `[relay result] …`.

One authorization covers one exact request for ten minutes. A new task needs a new `/connect authorize` or `/connect send`.

## If something goes wrong

| You see | Do this |
|---|---|
| `connect: unknown subcommand '…'` | Check the spelling with `/connect help`. |
| `connect: codes never go in a command…` | Run `/connect` alone and paste the code into the form. |
| `Check that /connect status shows kollabor.ai online, then retry.` | Run `/connect kollabor.ai` first, then `/connect offer` again. |
| `…the relay connection restarted after this code was created…` | That code can't be used any more. Create a new one with `/connect offer`. |
| `online peers: 0` | Kollab has to be running on both machines. Start it on the other one. |
| `connect: this window is not connected to its agent daemon; restart kollab` | Quit with `/quit` and start `kollab` again. |

To update Kollab, run `/upgrade` inside Kollab or `kollab --upgrade` in a shell. After the restart, the connection comes back by itself.

## Other commands

You can ignore these unless you need them. Each one exists because a security decision has its own switch.

| Group | Commands | What for |
|---|---|---|
| Inspect | `status`, `peers`, `agents`, `networks`, `grants`, `task <address> <id>` | See what is connected, allowed and running |
| Control a task | `cancel <address> <id>`, `answer <event-id> <text>` | Stop a remote task, or answer its question yourself instead of through your agent |
| Take access back | `deny <peer-key> [agent]`, `withdraw <grant-id>`, `revoke <peer-key>`, `reject <receipt-id>` | Undo an allow, an authorization, a peer approval, or turn down an enrollment request |
| Presence | `approve <peer-key>`, `ping <peer-key>` | Approve a peer by hand, or check that it is alive. Enrollment approves the peer for you. |
| Strangers | `contact-point`, `contact`, `contacts` | Let someone outside your network introduce themselves |
| Older pairing | `invite`, `join <file>` | Pair by copying a private file instead of using a code |
| Reset | `rotate`, `disconnect` | Replace the room secret, which clears approvals; or go offline and stop reconnecting |
| Alias | `enroll [domain]` | Opens the same form as `/connect` alone |

`/connect help` lists every subcommand with one line each. For full details, see the [command reference](../reference/commands.md).
