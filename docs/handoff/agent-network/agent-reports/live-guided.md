# live-guided (issue #121, guided setup live proof)

Build 0.11.0.dev7 (wheels-dev7b, tip 936ebab) on the Mac and alzan-prod. No product bug found: no product change, no new unit test.

## Result: PASS 16/16 (`bash tests/live/guided/proof.sh`, about 4.5 minutes)
| Row | Result | Seen |
|---|---|---|
| pre-* | PASS | relay lookup route live (HTTP 400), fresh workspaces, logins, both hosts 0.11.0.dev7 |
| g1 notice | PASS | both lines exact on the first launch; 3 processes from the venv (client + daemon) |
| g2 choices | PASS | "Start a new network on kollabor.ai" and "Join with a code" |
| g3 new network | PASS | join code plus "On your other computer", steps 1) 2) 3) |
| g4 server form | PASS | notice, Enter, Down, Enter, private form, masked code typed |
| g5 join | PASS | Mac accepted "wants to join"; server printed "joined ... trust: open" |
| g6 post-join line | PASS | server: "settings arrive sealed from <Mac device> ... run /login" |
| g7 relaunch | PASS | Mac back at its prompt, no notice, marker files on both hosts |
| g8 Mac to server | PASS | reply from koordinator@<srv>; server shell runs 0 -> 1; one hub_msg |
| g8 server to Mac | PASS | reply from koordinator@<mac>; Mac shell runs 0 -> 1; one hub_msg |
| g9 clean, mac + srv | PASS | all panes and the logs (352 KB, 188 KB): no code, hex64, relay:, error text |
| z real marker | PASS | ~/.kollab/connect-guide-seen absent on both hosts, again after teardown |

Evidence: tests/live/guided/evidence/ (gitignored, redacted). Files: tests/live/guided/{proof.sh,teardown.sh,README.md}. One commit, refs #121, not pushed.

## pypi-upgrade (written; install part run)
- `GS_INSTALL=pypi-upgrade GS_STOP_AFTER=launch`: PASS. 0.10.7 installs from PyPI and launches on both hosts; its update check cached into the workspace-local config, the real global cache untouched on both. No "Update available 0.11.0" yet, as expected.
- 0.10.7 update check: GitHub Releases `kollaborai/kollab` latest (not PyPI), 5 s timeout. Cache keys `kollabor.updates.*` (timestamp, version, url, name), saved by `save_key`: workspace-local `.kollab/config.json` if one exists, else `~/.kollab/config.json`. TTL 24 h (`check_interval_hours`), capped at 1 h while the cached version equals the running one. The real caches on both hosts say 0.10.7 (about 13-14 h old), so a real 0.10.7 re-checks on its next launch. The driver seeds timestamp 0 locally: live check, no write to the real config.
- `kollab --upgrade` (0.10.7 code): a plain venv means method pip, `sys.executable -m pip install --upgrade kollab`, so it only changes that venv. Not run (needs 0.11.0 on PyPI).

## Flags
- g6 line wraps at 120 columns with a 21-character device name ("run" ends line 1, "/login on this computer." starts line 2); the check flattens wraps. Cosmetic, not changed.
- `m1/teardown.sh` closes the shared ssh master `/tmp/kollab-m1-%C`, which cuts any other proof running through it. The guided scripts use their own master. Not changed.
- Not driven here: 80 columns, Esc at the notice (unit tests and tmux specs cover them).

## Left
- Kept on disk, nothing deleted: venvs `~/kollab-gs`, `~/kollab-gs-pypi`; workspaces `kollab-gs-{mac,srv}`, `kollab-gs-pypi-{mac,srv}`, `kollab-gs-pypi-mac2` / `-srv2`; their network state; one test network on kollabor.ai. Teardown done: no gs-* sessions, no kollab-gs processes on either host, m4-serve untouched.
- The release check itself.

## Next step
After v0.11.0 is on PyPI and GitHub Releases: `GS_INSTALL=pypi-upgrade GS_PYPI_MAC_WS=<new> GS_PYPI_SRV_WS_NAME=<new> bash tests/live/guided/proof.sh`, then `bash tests/live/guided/teardown.sh`, then confirm `~/.kollab/connect-guide-seen` is absent on both hosts.
