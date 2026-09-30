# m3 live proof: c2 debug (agent m3-live-debug)

Root cause of "the Mac never showed 'wants to join' for C": proof script bug, not product.
- `/connect code` opens a code-only screen (`code_only` in plugins/altview/connect_altview.py: no request rows, no accept key). Closing it prints nothing later; a request only shows in `/connect status` (or the full Connect screen). The proof waited passively for the text.
- The "/connect help list" in c2-02 was m1 leftover (Mac log 07:11:12); nothing was added to the Mac pane during c2. C's own pane said "request sent to kollabor.ai; waiting for approval on another device".

Fix commits (worktree branch, on top of bf63944):
- 5ce04af proof polls `/connect status` on the Mac (12 x 10s) for the request, like m1's fallback path.
- c2d67ba teardown now restarts B on its restored config. Before, B kept its loopback endpoint bound, so teardown then proof died at pre-ports (TCP 8801). stop_ws/launch_srv moved to env.sh.

Final proof table (run 2, after both fixes):
| step | result |
| pre | PASS |
| c1-endpoints | PASS |
| c2-c-joins | FAIL at the last check only: Mac showed "wants to join", accept worked, C printed "joined " |
| c3-members .. c7 | not run |

Why c2 still fails: it wants one NEW `~/.kollab/network/<digest>` after C joins. The digest is sha256(str(workspace path)) (plugins/hub/local_directory.py `_workspace_id`), C's path is fixed, and run 1 left that dir behind (teardown keeps it on purpose), so NET1 minus NET0 is empty.

Next step (small, script only): in m3/proof.sh set C_STATE_DIR from `printf %s "$M3_C_WS" | sha256sum` on the server and assert the dir exists after the join (drop NET0/NET1). In m3/teardown.sh delete that same dir on alzan-prod so every run starts C as a stranger. Then teardown, proof (about 15 min). c3-c7 have never run live.

Flag for main: the Mac prints nothing when a join request arrives; only `/connect status` shows it. Deliberate or a product gap?

State now: C torn down (teardown 07:42); m1-srv (B) restarted by teardown on its restored config, not re-checked as joined; m1-mac untouched. Evidence in this worktree's tests/live/m3/evidence/ is untracked.
