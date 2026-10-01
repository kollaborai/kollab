# live-story7 (issue #121, Story 7 manual trust, first live run)

Build 0.11.0.dev7 (wheels-dev7b, tip 712165f), own venv ~/kollab-s7 on the Mac and alzan-prod, run 20:39 to 20:59. Result: FAIL. Product bug found, not fixed (30-call cap reached).

| Row | Result | Seen |
|---|---|---|
| pre-*, setup g1-g5 | PASS (8) | network started on the Mac, server joined (trust: open) |
| s1 trust manual | PASS | "trust for kollabor.ai is now manual" + "messages now need /connect authorize or /connect send" |
| s2 first message blocked | FAIL (product) | the agent's hub_msg went out: "remote request 1: queued; acceptance is not completion"; no gate text; the server agent received it |
| s3 authorize | FAIL (harness) | product line is right: "communication authorized: request 2; expires at 20:56; recipient" with the handle on the next line (the TUI hard-wraps it); my regex wanted one line |
| s4 send | PASS | "request 3 to <server agent>: queued" |
| s5 task | PASS | "request 3 on <server agent>: delivered" (an ordinary hub message's state, not a task's) |
| s6 reply via envelope | FAIL | no reply in 300s; the server agent wrote "next: authorize a conversation grant if you want me to send it" and "[warn] I couldn't relay it to the peer; the hub rejected the message" |
| s7 withdraw | FAIL (cascade) | no request number from s3; not exercised |
| s8 cancel | FAIL | state stayed "delivered"; /connect cancel gave no cancelled state |
| s9 answer | NOT RUN | I tore down while s9 waited; this is not a SKIP |
| s10 clean | NOT RUN | partial findings: s2 Mac pane relay=1 (tool line shows to="[relay:addr]"), s2 server pane errhits=1 (the "[warn] ... couldn't relay" line) |

Evidence: tests/live/story7/evidence/ (gitignored, redacted). Files: tests/live/story7/{proof.sh,teardown.sh,README.md}. Harness: scan.py now allows "cannot start work here" (the withdraw line); proof.sh skips the intended gate/withdraw lines in its pane scan; the s3 recipient is now checked on the flattened screen (edited after the run, not re-run). One commit, refs #121, not pushed.

## Product lead (not verified)
After /connect trust manual the Mac still sent as open: relay_agent.py:1454 reads trust_level() (:523, `self._state().state.trust`) and took the no-grant path, so s4-s8 ran as ordinary hub messages (state "delivered", no task, no envelope). set_trust_level (:526) writes and saves the same field. Suspects: the daemon's bridge keeps a stale state while the attached client set it (default launch is client plus daemon), or the server's view differs (its agent refused its own reply for lack of a grant while still "trust: open").

## Left
- Product: reproduce in a unit test (set trust through one bridge, send through another), fix, build M1_VERSION=0.11.0.dev8, reinstall under the s7 variables, rerun on fresh workspaces (M1_MAC_WS=$HOME/kollab-s7-mac2 M1_SRV_WS_NAME=kollab-s7-srv2).
- s9 never ran; s10 never completed; s2, s6, s8 need the fix first.
- On disk: venvs ~/kollab-s7 on both hosts, workspaces kollab-s7-{mac,srv}, their network state, one test network on kollabor.ai.
- Teardown done (script output): Mac s7-mac killed, 1 process stopped; srv s7-srv killed, 1 process stopped; Mac `tmux ls` shows only e2e-mac, `pgrep -fl kollab-s7` empty. m4-serve, ~/kollab-m1, ~/kollab-gs* untouched.

## Next step
Make the send path honor /connect trust manual; nothing after s2 means anything until a manual-trust first message is gated.
