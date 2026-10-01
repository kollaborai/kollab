# Story 7 live proof (manual trust)

Installed 0.11.0.devN builds on the Mac and server, driven through tmux (s7-mac / s7-srv, 120x40, own venv ~/kollab-s7, own ssh master).
Rows: setup (guided g1-g5: network started on the Mac, server joined), s1 trust manual, s2 first message blocked, s3 authorize, s4 send, s5 task, s6 reply via the task envelope, s7 withdraw, s8 cancel a running request, s9 answer (SKIP when the model asks nothing), s10 clean transcript.

Install: M1_VERSION=<version> bash tests/live/m1/install_both.sh <wheels-dir> with M1_ROOT_NAME=kollab-s7 M1_MAC_SESSION=s7-mac M1_SRV_SESSION=s7-srv M1_MAC_WS=$HOME/kollab-s7-mac M1_SRV_WS_NAME=kollab-s7-srv.
Run: bash tests/live/story7/proof.sh (about 25 minutes; fresh workspaces each run: set M1_MAC_WS and M1_SRV_WS_NAME to new names to rerun; S7_EXPECT_VERSION names the build). Never with bash -x.
Stop: bash tests/live/story7/teardown.sh (only s7-* sessions and processes from the kollab-s7 venv).
Evidence goes to tests/live/story7/evidence/ (gitignored, join code redacted). Needs a ChatGPT login on both hosts (--llm openai-oauth).
