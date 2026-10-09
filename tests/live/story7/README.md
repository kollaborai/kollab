# Story 7 live proof (manual trust)

Installed 0.11.0.devN builds on the Mac and server, driven through tmux (s7-mac / s7-srv, 120x40, own venv ~/kollab-s7, own ssh master).
Rows: setup (guided g1-g5: network started on the Mac, server joined), s1 trust manual, s2 first message blocked, s3 authorize, s4 send, s5 task, s6 reply via the task envelope, s7 withdraw, s8 cancel a running request, r1-r6 the mirror direction (below), s9 answer (SKIP when the model asks nothing), s10 clean transcript.

The mirror, an open sender to a manual receiver (issue #123), runs between s8 and s9. The server is the open sender, the Mac the manual receiver:
- r1 trust pair: `/connect trust manual` on the Mac and `/connect trust open` on the server, both confirmed on screen.
- r2 the server agent messages the Mac agent and is refused (`sender has no conversation grant for this agent` on the server's screen); the Mac shows nothing, because a manual receiver prints no notice for a refused sender.
- r3 `/connect allow <server device> <Mac agent>` on the Mac prints `conversation allowed: ... local tool permissions still apply`. It is the only way a manual receiver lets a non-manual device in.
- r4 the server agent sends again; the Mac shows the request from the server agent and runs it: the token shows on its screen, or its log shows a new shell run. The request asks for `cat r4-token.txt`, a random token the harness writes to the Mac's workspace before r2, so no agent can answer it from memory.
- r5 the Mac agent's reply reaches the server agent (a box from the Mac agent carrying that token).
- r6 `/connect deny` on the Mac revokes the grant. Trust is not changed anywhere in r1-r6: the Mac stays manual and the server stays open until s9 flips it.

Preconditions for r1-r6: the setup rows passed (both agents listed by `/connect status`); nothing else. s1 already left the Mac on manual and the server is open until s9, so r1 only confirms the pair. When r5 fails, its note gives the server log's `secure conversation packet was rejected` count before and after the resend: growth means the open sender's runtime refused something the Mac sent.

Install: M1_VERSION=<version> bash tests/live/m1/install_both.sh <wheels-dir> with M1_ROOT_NAME=kollab-s7 M1_MAC_SESSION=s7-mac M1_SRV_SESSION=s7-srv M1_MAC_WS=$HOME/kollab-s7-mac M1_SRV_WS_NAME=kollab-s7-srv.
Run: bash tests/live/story7/proof.sh (about 35 minutes: 25 for the original rows, 10 for r1-r6; fresh workspaces each run: set M1_MAC_WS and M1_SRV_WS_NAME to new names to rerun; S7_EXPECT_VERSION names the build). Never with bash -x.
Paid model turns: the original rows plus about 4 for r1-r6 (server: the refused ask, the resend, the reply; Mac: the request). r5 waits up to 5 minutes when no reply comes back.
Stop: bash tests/live/story7/teardown.sh (only s7-* sessions and processes from the kollab-s7 venv).
Evidence goes to tests/live/story7/evidence/ (gitignored, join code redacted). Needs a ChatGPT login on both hosts (--llm openai-oauth).
