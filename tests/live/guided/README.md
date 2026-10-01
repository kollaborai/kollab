# guided: live proof for the guided network setup (#121, 0.11.0)

Drives the first-launch notice and the guided setup (Story 1 of
`docs/specs/agent-network-simple-flow.md`) on INSTALLED builds, through tmux like a
user, on this Mac (`gs-mac`) and `server` (`gs-srv`): 120 columns, default (daemon)
launch, fresh workspaces. Same plumbing as `m1/` (`tmuxtype.py`, `scan.py`).

## Order

```
M1_VERSION=<v> M1_ROOT_NAME=kollab-gs M1_MAC_SESSION=gs-mac M1_SRV_SESSION=gs-srv \
  M1_MAC_WS=$HOME/kollab-gs-mac M1_SRV_WS_NAME=kollab-gs-srv bash m1/install_both.sh <wheeldir>
bash guided/proof.sh        # GS_EXPECT_VERSION=<v> pins the version; exit 0 only on a full PASS
bash guided/teardown.sh     # only gs-* sessions and processes from ~/kollab-gs(-pypi)/venv
```

Preconditions are m1's: relay lookup route live, ChatGPT login on both hosts (`--llm openai-oauth`),
`ssh server` without a password, nobody attached to the `gs-*` sessions. Never run with `bash -x`.

## Rows

| Row | Proves |
|---|---|
| `pre-*` | relay route, fresh workspaces (no network state, no marker), logins, same build on both hosts |
| `g1` | the Mac's first launch shows the two notice lines, exactly |
| `g2` | Enter shows "Start a new network on kollabor.ai" and "Join with a code" |
| `g3` | choosing the first one: Connect screen with a join code and "On your other computer" with its 3 steps |
| `g4` | server: notice, Enter, Down, Enter, private code form, code typed (masked field) |
| `g5` | Mac accepts "wants to join"; server prints `joined <network> as <device>. trust: <level>` |
| `g6` | server's post-join line names the Mac's device and says to run /login |
| `g7` | relaunching the Mac client shows no notice; both marker files exist |
| `g8-*` | one message each way: reply content, far shell ran (log count), one `hub_msg`, no warnings |
| `g9-clean-*` | every captured pane and kollab.log from both hosts: no code, 64-hex, `relay:`, error text |
| `z-real-marker-absent` | `~/.kollab/connect-guide-seen` exists on neither host |

The join code lives only in a shell variable, is typed through a pipe and redacted in
`evidence/` (gitignored, one `.txt` per capture, `pane-findings.txt`, `logscan-*.txt`).

## Isolation

Own venv root (`kollab-gs`, `kollab-gs-pypi`), workspaces, tmux sessions and ssh master
(`/tmp/kollab-gs-*`; `m1/teardown.sh` closes the shared `/tmp/kollab-m1-*` master, ours never does).
`KOLLAB_CONNECT_GUIDE_MARKER` points at `<workspace>/.connect-guide-seen` on both hosts. Shared and
unavoidable: both hosts' `~/.kollab/config.json`, `oauth/`, `agents/`, and the hub presence directory.
A workspace keeps its network state: for another run use new `M1_MAC_WS` / `M1_SRV_WS_NAME`.

## GS_INSTALL=pypi-upgrade (release check, after 0.11.0 is on PyPI and GitHub Releases)

`GS_INSTALL=pypi-upgrade bash guided/proof.sh` installs `kollab==0.10.7` from PyPI into
`~/kollab-gs-pypi/venv` on both hosts, launches it, expects `Update available ... 0.11.0` (`p1-update-notice-*`),
stops it, runs `kollab --upgrade` in the venv, expects `kollab --version` to say 0.11.0 (`p2-upgrade-*`), then
runs g1-g9 on that venv. Fresh workspaces: `GS_PYPI_MAC_WS`, `GS_PYPI_SRV_WS_NAME`.
`GS_STOP_AFTER=launch` stops after the 0.10.7 install and launch (what can run before the release).

0.10.7's update check: GitHub Releases API (`kollaborai/kollab` `releases/latest`, 5 s timeout, not PyPI).
Cache: `kollabor.updates.{last_check_timestamp,cached_latest_version,cached_release_name,cached_release_url}`,
saved to the workspace-local `.kollab/config.json` when one exists, else `~/.kollab/config.json`. TTL
`check_interval_hours` (24), capped at 1 hour while the cached version equals the running one. The driver
seeds a local config with the timestamp at 0, so the check is live and the real cache is never written.
`kollab --upgrade` picks pip for a plain venv (`sys.executable -m pip`), so it only changes that venv.

## Not covered

80 columns (unit tests and `tests/tmux/specs/network-guided-setup-*.json`), Esc at the notice, the
web UI, knock, trust levels, sealed config sync.
