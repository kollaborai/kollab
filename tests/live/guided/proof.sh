#!/usr/bin/env bash
# proof.sh: live proof of the guided network setup (issue #121, release 0.11.0).
# INSTALLED builds, driven through tmux like a user on this Mac (gs-mac) and on
# alzan-prod (gs-srv), 120x40, default (daemon) launch, fresh workspaces.
#   g1 Mac notice            g2 Enter shows the two choices
#   g3 new network on kollabor.ai: join code + "On your other computer" box
#   g4 server: notice, Join with a code, private form, code typed
#   g5 the join completes   g6 post-join line names the Mac and says /login
#   g7 relaunch on the Mac shows no notice   g8 one message each way
#   g9 clean transcript on both hosts
# GS_INSTALL=pypi-upgrade first installs kollab==$GS_PYPI_FROM from PyPI into
# ~/kollab-gs-pypi/venv on both hosts, launches it and expects its "Update available"
# notice, runs `kollab --upgrade`, expects `kollab --version` to say $GS_PYPI_TO, then
# runs g1-g9 on that upgraded venv. GS_STOP_AFTER=launch stops once 0.10.7 is installed
# and launched (the part that can run before 0.11.0 is on PyPI).
#
# The join code lives only in the shell variable CODE: read from a pane capture, typed
# into the other pane through a pipe, replaced with "[join code]" in every evidence
# file. Never run with bash -x. Exit 0 only if every row of the table is PASS.
set -euo pipefail
GS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GS_INSTALL=${GS_INSTALL:-wheels}   # wheels | pypi-upgrade
GS_PYPI_FROM=${GS_PYPI_FROM:-0.10.7}
GS_PYPI_TO=${GS_PYPI_TO:-0.11.0}
GS_STOP_AFTER=${GS_STOP_AFTER:-}
GS_EXPECT_VERSION=${GS_EXPECT_VERSION:-}   # wheels mode: both hosts must report this (default: just equal)
if [ "$GS_INSTALL" = pypi-upgrade ]; then
  # own venv root, workspaces and sessions: a wheels run and a pypi run never meet
  # (a workspace keeps its network state: pass GS_PYPI_MAC_WS / GS_PYPI_SRV_WS_NAME to run again on fresh ones)
  M1_ROOT_NAME=kollab-gs-pypi; M1_MAC_WS=${GS_PYPI_MAC_WS:-$HOME/kollab-gs-pypi-mac}; M1_SRV_WS_NAME=${GS_PYPI_SRV_WS_NAME:-kollab-gs-pypi-srv}
  M1_MAC_SESSION=gs-pypi-mac; M1_SRV_SESSION=gs-pypi-srv
else
  M1_ROOT_NAME=${M1_ROOT_NAME:-kollab-gs}; M1_MAC_WS=${M1_MAC_WS:-$HOME/kollab-gs-mac}; M1_SRV_WS_NAME=${M1_SRV_WS_NAME:-kollab-gs-srv}
  M1_MAC_SESSION=${M1_MAC_SESSION:-gs-mac}; M1_SRV_SESSION=${M1_SRV_SESSION:-gs-srv}
fi
. "$GS_DIR/../m1/env.sh"
# Own ssh master: the m1 one is shared with other proofs and must never be closed from here.
M1_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-gs-%C" -o ControlPersist=300)
case "$M1_ROOT_NAME" in kollab-gs*) ;; *) die "M1_ROOT_NAME=$M1_ROOT_NAME: this proof only runs from a kollab-gs* venv root" ;; esac
case "$M1_MAC_SESSION|$M1_SRV_SESSION" in gs-*"|gs-"*) ;; *) die "tmux sessions must be gs-*, got $M1_MAC_SESSION / $M1_SRV_SESSION" ;; esac

EVID=$GS_DIR/evidence
mkdir -p "$EVID"
printf '*\n' > "$EVID/.gitignore"   # evidence stays out of git
: > "$EVID/pane-findings.txt"        # this run only
POLL=3
SCAN=$M1_DIR/scan.py
CODE=""
LAST=""
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()
N1='New: connect your agents across computers.'
N2='Enter sets it up now; Esc for later (/connect any time).'

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  printf '\n%-30s %-6s %s\n' STEP RESULT EVIDENCE
  printf '%-30s %-6s %s\n' ---- ------ --------
  for ((i = 0; i < n; i++)); do
    printf '%-30s %-6s %s\n' "${REC_STEP[$i]}" "${REC_STATUS[$i]}" "${REC_EVID[$i]}"
    [ "${REC_STATUS[$i]}" = PASS ] || { fails=$((fails + 1)); printf '    -> %s\n' "${REC_NOTE[$i]}"; }
  done
  if [ "$code" -ne 0 ] && [ "$fails" -eq 0 ]; then
    printf '\nproof.sh stopped early (exit %s) before every step ran. Not a pass.\n' "$code"
    exit "$code"
  fi
  if [ "$fails" -gt 0 ]; then printf '\nFAIL: %d step(s) failed. Not a pass. Evidence: %s\n' "$fails" "$EVID"; exit 1; fi
  if [ -n "$GS_STOP_AFTER" ]; then printf '\nPARTIAL (GS_STOP_AFTER=%s): the install part passed; this is not a full pass.\n' "$GS_STOP_AFTER"; exit 0; fi
  printf '\nPASS: %d steps, transcript clean. Evidence: %s\n' "$n" "$EVID"
}
trap finish EXIT
abort() { rec "$1" FAIL "${3:--}" "$2"; exit 1; }

# ------------------------------------------------------------ tmux plumbing ----
sess() { if [ "$1" = mac ]; then printf %s "$M1_MAC_SESSION"; else printf %s "$M1_SRV_SESSION"; fi; }
tm() { local h=$1; shift; if [ "$h" = mac ]; then tmux "$@"; else m1_ssh "$(printf '%q ' tmux "$@")" </dev/null; fi; }   # %q: '#{...}' must survive the remote shell
raw() { tm "$1" capture-pane -t "$(sess "$1")" -p -J -S "-${2:-500}" || true; }   # screen + history
screen() { tm "$1" capture-pane -t "$(sess "$1")" -p -J || true; }               # visible screen only
key() { local h=$1; shift; tm "$h" send-keys -t "$(sess "$h")" "$@"; }
typ() { # typ <mac|srv> <text> [delay]: one character at a time, text on stdin
  local h=$1 delay=${3:-0.06}
  if [ "$h" = mac ]; then
    printf '%s' "$2" | python3 "$M1_DIR/tmuxtype.py" "$(sess mac)" "$delay" >/dev/null
  else
    printf '%s' "$2" | m1_ssh python3 "$M1_SRV_ROOT/bin/tmuxtype.py" "$(sess srv)" "$delay" >/dev/null
  fi
}
cmd() { typ "$1" "$2"; sleep 1.2; key "$1" Enter; }   # type a line and press Enter
count_pat() { raw "$1" 500 | grep -Ec -- "$2" || true; }
wait_for() { # wait_for <host> <ERE> <timeout-s> [baseline]: until count(ERE) > baseline
  local h=$1 re=$2 end n base=${4:-0}
  end=$(( $(date +%s) + $3 ))
  while :; do
    n=$(count_pat "$h" "$re")
    if [ "$n" -gt "$base" ]; then return 0; fi
    if [ "$(date +%s)" -ge "$end" ]; then return 1; fi
    sleep "$POLL"
  done
}
wait_screen() { # wait_screen <host> <ERE> <timeout-s>: until the visible screen matches
  local h=$1 re=$2 end
  end=$(( $(date +%s) + $3 ))
  while :; do
    if grep -Eq -- "$re" <<<"$(screen "$h")"; then return 0; fi
    if [ "$(date +%s)" -ge "$end" ]; then return 1; fi
    sleep "$POLL"
  done
}
wait_ready() { # a TUI is up when its pane has content; then give it time to settle
  local h=$1 end n
  end=$(( $(date +%s) + 90 ))
  while :; do
    n=$(screen "$h" | grep -c '[^[:space:]]' || true)
    if [ "$n" -ge 5 ]; then break; fi
    if [ "$(date +%s)" -ge "$end" ]; then return 1; fi
    sleep 2
  done
  sleep 10
}
shows() { grep -Fq -- "$2" <<<"$(screen "$1")"; }     # shows <host> <fixed text>
flat() { tr '\n' ' ' | tr -s ' '; }
notice_on() { local s; s=$(screen "$1"); grep -Fq -- "$N1" <<<"$s" && grep -Fq -- "$N2" <<<"$s"; }
wait_notice() { # wait_notice <host> <timeout-s>
  local end; end=$(( $(date +%s) + $2 ))
  while :; do
    if notice_on "$1"; then return 0; fi
    if [ "$(date +%s)" -ge "$end" ]; then return 1; fi
    sleep 2
  done
}

# ------------------------------------------------------ evidence and scanning ----
# record <label> <evidence-name> <text> [allow-code]: redact into evidence/<name>.txt,
# scan the raw text for leaks and errors, remember findings for the final rows.
record() {
  local label=$1 name=$2 text=$3 allow=${4:-0} out flag=""
  if [ "$allow" = 1 ]; then flag="--allow-code"; fi
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text $flag 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$label:$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
cap() { LAST=$(raw "$1" "${4:-500}"); record "$1" "$2" "$LAST" "${3:-0}"; }   # cap <host> <name> [allow-code] [history]
capscreen() { LAST=$(screen "$1"); record "$1" "$2" "$LAST" "${3:-0}"; }

log_path() { # <mac|srv>: kollab.log for that workspace
  local ws home
  if [ "$1" = mac ]; then ws=$M1_MAC_WS; home=$HOME; else ws=$M1_SRV_WS; home=$M1_SRV_HOME; fi
  printf %s "$(m1_project_dir "$ws" "$home")/logs/kollab.log"
}
log_size() { if [ "$1" = mac ]; then { wc -c < "$(log_path mac)"; } 2>/dev/null || echo 0; else m1_ssh "{ wc -c < '$(log_path srv)'; } 2>/dev/null || echo 0"; fi; }   # a log that is not there yet is size 0, quietly
log_scan() { # log_scan <mac|srv> <offset>: counters on stdout, code from CODE via stdin
  if [ "$1" = mac ]; then printf %s "$CODE" | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf %s "$CODE" | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path srv)" "$2"; fi
}
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }   # kv <scan-output> <key>
shell_ok() { kv "$(log_scan "$1" "$2")" shell_ok; }
off_of() { if [ "$1" = mac ]; then printf %s "$MAC_LOG0"; else printf %s "$SRV_LOG0"; fi; }
newest_with() { raw "$1" 500 | grep -E -- "$2" | tail -1 || true; }

launch() { # launch <mac|srv>: the default (daemon) launch, own marker file inside the workspace
  if [ "$1" = mac ]; then
    tmux kill-session -t "$M1_MAC_SESSION" 2>/dev/null || true
    tmux new-session -d -s "$M1_MAC_SESSION" -x 120 -y 40 "cd '$M1_MAC_WS' && env KOLLAB_NO_KEYRING=1 KOLLAB_CONNECT_GUIDE_MARKER='$M1_MAC_WS/.connect-guide-seen' '$M1_MAC_VENV/bin/kollab' --llm openai-oauth; exec zsh"
  else
    m1_ssh "tmux kill-session -t $M1_SRV_SESSION 2>/dev/null; tmux new-session -d -s $M1_SRV_SESSION -x 120 -y 40 \"cd '$M1_SRV_WS' && env KOLLAB_NO_KEYRING=1 KOLLAB_CONNECT_GUIDE_MARKER='$M1_SRV_WS/.connect-guide-seen' '$M1_SRV_VENV/bin/kollab' --llm openai-oauth; exec zsh\""
  fi
}
version_of() { # version_of <mac|srv>
  if [ "$1" = mac ]; then (cd "$HOME" && "$M1_MAC_VENV/bin/kollab" --version 2>&1 | tail -1) || true
  else m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/kollab' --version 2>&1 | tail -1" || true; fi
}
upd_cache() { # upd_cache <mac|srv> <config file>: its kollabor.updates keys, or "missing"
  local py='import json, os, sys
try:
    print(json.dumps(json.load(open(os.path.expanduser(sys.argv[1]))).get("kollabor", {}).get("updates", {}), sort_keys=True))
except Exception:
    print("missing")'
  if [ "$1" = mac ]; then python3 -c "$py" "$2"; else m1_ssh "python3 -c '$py' '$2'"; fi
}

# =============================================================== preflight ====
say "preflight ($GS_INSTALL)"
m1_srv_paths
SRV_HOSTNAME=$(m1_ssh uname -n)
MAC_HOSTNAME=$(uname -n)

code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' -X POST -H 'content-type: application/json' -d '{}' https://kollabor.ai/relay/v1/enrollment/lookup || echo 000)
case "$code" in
  404|405|000|5??) abort pre-relay-lookup "POST /relay/v1/enrollment/lookup on kollabor.ai answered HTTP $code. The relay must serve it before this proof means anything." ;;
  *) rec pre-relay-lookup PASS - "lookup route answered HTTP $code" ;;
esac

for h in mac srv; do
  if [ "$h" = mac ]; then ws=$M1_MAC_WS; else ws=$M1_SRV_WS; fi
  py='import hashlib, pathlib, sys; print(hashlib.sha256(str(pathlib.Path(sys.argv[1]).resolve()).encode()).hexdigest())'
  if [ "$h" = mac ]; then dg=$(python3 -c "$py" "$ws"); state=$HOME/.kollab/network/$dg
  else dg=$(m1_ssh "python3 -c '$py' '$ws'"); state=$M1_SRV_HOME/.kollab/network/$dg; fi
  if [ "$h" = mac ]; then present=$([ -e "$state" ] || [ -e "$ws/.connect-guide-seen" ] && echo yes || echo no)
  else present=$(m1_ssh "[ -e '$state' ] || [ -e '$ws/.connect-guide-seen' ] && echo yes || echo no"); fi
  [ "$present" = no ] || abort pre-fresh-workspaces "$h workspace $ws already has network state or a guide marker (previous run). Use new names: M1_MAC_WS=... M1_SRV_WS_NAME=..."
done
rec pre-fresh-workspaces PASS - "no network state and no guide marker for either workspace"

[ -s "$HOME/.kollab/oauth/openai.json" ] || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on the Mac (kollab --login)"
m1_ssh "[ -s \"\$HOME/.kollab/oauth/openai.json\" ]" || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on $M1_HOST (kollab --login there)"
rec pre-llm-login PASS - "both hosts have an openai oauth login; both agents run --llm openai-oauth"

REAL_MARK_MAC=$([ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no)
REAL_MARK_SRV=$(m1_ssh '[ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no')
m1_ssh "mkdir -p '$M1_SRV_ROOT/bin' '$M1_SRV_WS'"
mkdir -p "$M1_MAC_WS"
scp -q "${M1_SSH_OPTS[@]}" "$M1_DIR/scan.py" "$M1_DIR/tmuxtype.py" "$M1_HOST:$M1_SRV_ROOT/bin/"

# ============================================= pypi-upgrade: 0.10.7 -> 0.11.0 ====
pypi_phase() {
  local PY re cfg cache0_mac cache0_srv h up_ok
  say "pypi-upgrade: kollab==$GS_PYPI_FROM from PyPI into $M1_MAC_VENV and $M1_SRV_VENV"
  PY=$(m1_pick_python) || abort p0-install "no Python >= 3.12 on the Mac (set M1_PY_MAC)"
  mkdir -p "$M1_MAC_ROOT" "$M1_MAC_WS/.kollab"
  [ -x "$M1_MAC_VENV/bin/python" ] || "$PY" -m venv "$M1_MAC_VENV"
  "$M1_MAC_VENV/bin/python" -m pip install -q --disable-pip-version-check --no-cache-dir "kollab==$GS_PYPI_FROM" >"$EVID/p0-pip-mac.txt" 2>&1 \
    || abort p0-install "pip install kollab==$GS_PYPI_FROM failed on the Mac" p0-pip-mac.txt
  m1_ssh "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' && mkdir -p '$M1_SRV_ROOT' '$M1_SRV_WS/.kollab' && { [ -x '$M1_SRV_VENV/bin/python' ] || python3 -m venv '$M1_SRV_VENV'; } && '$M1_SRV_VENV/bin/python' -m pip install -q --disable-pip-version-check --no-cache-dir 'kollab==$GS_PYPI_FROM'" >"$EVID/p0-pip-srv.txt" 2>&1 \
    || abort p0-install "pip install kollab==$GS_PYPI_FROM failed on $M1_HOST" p0-pip-srv.txt
  MAC_V=$(version_of mac); SRV_V=$(version_of srv)
  case "$MAC_V|$SRV_V" in
    *"$GS_PYPI_FROM|"*"$GS_PYPI_FROM") rec p0-install PASS p0-pip-mac.txt "mac: $MAC_V / srv: $SRV_V" ;;
    *) abort p0-install "expected $GS_PYPI_FROM on both, got mac='$MAC_V' srv='$SRV_V'" ;;
  esac

  # Where 0.10.7 caches its update check: kollabor.updates.* via save_key(), into the
  # workspace-local .kollab/config.json when one exists, else ~/.kollab/config.json.
  # A local file with the cache zeroed forces a live GitHub check and keeps the cache off the real config.
  cfg='{"kollabor":{"updates":{"last_check_timestamp":0,"cached_latest_version":""}}}'
  cache0_mac=$(upd_cache mac '~/.kollab/config.json'); cache0_srv=$(upd_cache srv '~/.kollab/config.json')
  printf '%s\n' "$cfg" > "$M1_MAC_WS/.kollab/config.json"
  m1_ssh "printf '%s\n' '$cfg' > '$M1_SRV_WS/.kollab/config.json'"

  launch mac; launch srv
  wait_ready mac || abort p1-launch-mac "no output from the $GS_PYPI_FROM TUI on the Mac after 90s"
  wait_ready srv || abort p1-launch-srv "no output from the $GS_PYPI_FROM TUI on $M1_HOST after 90s"
  sleep 20   # the version check runs in the background (5s HTTP timeout)
  cap mac p1-mac-pypi-launch; cap srv p1-srv-pypi-launch
  rec p1-launch PASS p1-mac-pypi-launch.txt "$GS_PYPI_FROM launched on both hosts (p1-srv-pypi-launch.txt)"
  {
    printf 'global before  mac: %s\nglobal before  srv: %s\n' "$cache0_mac" "$cache0_srv"
    printf 'global after   mac: %s\nglobal after   srv: %s\n' "$(upd_cache mac '~/.kollab/config.json')" "$(upd_cache srv '~/.kollab/config.json')"
    printf 'local after    mac: %s\nlocal after    srv: %s\n' "$(upd_cache mac "$M1_MAC_WS/.kollab/config.json")" "$(upd_cache srv "$M1_SRV_WS/.kollab/config.json")"
  } > "$EVID/p1-update-cache.txt"
  cache_ok() { # cache_ok <mac|srv>: the check cached into the workspace config and left the real global cache alone
    grep -Eq "^local after +$1: .*\"last_check_timestamp\": [1-9]" "$EVID/p1-update-cache.txt" \
      && [ "$(sed -n "s/^global before  $1: //p" "$EVID/p1-update-cache.txt")" = "$(sed -n "s/^global after   $1: //p" "$EVID/p1-update-cache.txt")" ]
  }
  if cache_ok mac && cache_ok srv; then
    rec p1-cache-local PASS p1-update-cache.txt "the $GS_PYPI_FROM update check ran and cached into the workspace config; the real global cache is unchanged (both hosts)"
  else
    rec p1-cache-local FAIL p1-update-cache.txt "the update cache did not land in the workspace-local config, or the real global one changed; see the file"
  fi

  re="Update available.*${GS_PYPI_TO//./\\.}"
  up_ok=1
  for h in mac srv; do
    if wait_for "$h" "$re" "$( [ -n "$GS_STOP_AFTER" ] && echo 5 || echo 90 )"; then
      cap "$h" "p1-$h-update-notice"
      rec "p1-update-notice-$h" PASS "p1-$h-update-notice.txt" "$GS_PYPI_FROM showed 'Update available' for $GS_PYPI_TO"
    else
      up_ok=0
      if [ -z "$GS_STOP_AFTER" ]; then rec "p1-update-notice-$h" FAIL "p1-$h-pypi-launch.txt" "no 'Update available ... $GS_PYPI_TO' on the $h pane within 90s ($GS_PYPI_TO not released on GitHub yet, or a fresh cache blocks the check: see p1-update-cache.txt)"; fi
    fi
  done
  if [ -n "$GS_STOP_AFTER" ]; then
    say "GS_STOP_AFTER=$GS_STOP_AFTER: update notice for $GS_PYPI_TO seen on both hosts: $([ "$up_ok" = 1 ] && echo yes || echo no)"
    exit 0
  fi

  say "pypi-upgrade: stopping $GS_PYPI_FROM, running kollab --upgrade in the pypi venvs"
  bash "$GS_DIR/teardown.sh" >"$EVID/p2-teardown.txt" 2>&1 || true
  (cd "$M1_MAC_WS" && perl -e 'alarm shift; exec @ARGV or die "exec: $!"' 600 "$M1_MAC_VENV/bin/kollab" --upgrade) >"$EVID/p2-upgrade-mac.txt" 2>&1 || true
  m1_ssh "cd '$M1_SRV_WS' && '$M1_SRV_VENV/bin/kollab' --upgrade" >"$EVID/p2-upgrade-srv.txt" 2>&1 || true
  for h in mac srv; do
    v=$(version_of "$h")
    case "$v" in
      *"$GS_PYPI_TO"*) rec "p2-upgrade-$h" PASS "p2-upgrade-$h.txt" "kollab --version: $v" ;;
      *) rec "p2-upgrade-$h" FAIL "p2-upgrade-$h.txt" "after kollab --upgrade, kollab --version says '$v' (expected $GS_PYPI_TO)" ;;
    esac
  done
}
if [ "$GS_INSTALL" = pypi-upgrade ]; then pypi_phase; fi

# the build under test: equal on both hosts (and the one you named, when you named one)
[ -x "$M1_MAC_VENV/bin/kollab" ] || abort pre-installed-build "mac venv missing: run m1/install_both.sh under the gs variables"
MAC_V=$(version_of mac); SRV_V=$(version_of srv)
if [ "$GS_INSTALL" = pypi-upgrade ]; then want=$GS_PYPI_TO; else want=$GS_EXPECT_VERSION; fi
if [ -n "$MAC_V" ] && [ "$MAC_V" = "$SRV_V" ] && case "$MAC_V" in *"$want"*) true ;; *) false ;; esac; then
  rec pre-installed-build PASS - "mac: $MAC_V / srv: $SRV_V"
else
  abort pre-installed-build "versions differ or are not '$want': mac='$MAC_V' srv='$SRV_V'"
fi

approval() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("kollabor",{}).get("permissions",{}).get("approval_mode",""))' "$1" 2>/dev/null || true; }
MAC_APPROVAL=$(approval "$HOME/.kollab/config.json")
SRV_APPROVAL=$(m1_ssh "python3 -c 'import json,os; c=json.load(open(os.path.expanduser(\"~/.kollab/config.json\"))); print(c.get(\"kollabor\",{}).get(\"permissions\",{}).get(\"approval_mode\",\"\"))' 2>/dev/null" || true)
MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv)

# ================================================================== g1 ====
say "launching both TUIs (default daemon launch, 120x40) in fresh workspaces"
launch mac; launch srv
if ! wait_notice mac 150; then
  cap mac g1-mac-no-notice 1
  abort g1-notice-text "the first launch on the Mac never showed the notice (both lines, exactly)" g1-mac-no-notice.txt
fi
sleep 2
capscreen mac g1-mac-notice
MAC_PROCS=$(pgrep -f -- "[${M1_MAC_VENV:0:1}]${M1_MAC_VENV:1}/bin/" | wc -l | tr -d ' ' || true)
rec g1-notice-text PASS g1-mac-notice.txt "both lines of the notice on the Mac's first launch; $MAC_PROCS processes run from the venv (client + daemon)"

# ================================================================== g2 ====
key mac Enter
if wait_screen mac 'Join with a code' 40 && shows mac 'Start a new network on kollabor.ai'; then
  sleep 2
  capscreen mac g2-mac-choices
  rec g2-two-choices PASS g2-mac-choices.txt "Enter shows 'Start a new network on kollabor.ai' and 'Join with a code'"
else
  cap mac g2-mac-no-choices 1
  abort g2-two-choices "Enter on the notice did not show both choices" g2-mac-no-choices.txt
fi

# ================================================================== g3 ====
MAC_BASE=$(raw mac 500)
key mac Enter   # the first choice is selected: Start a new network on kollabor.ai
found=0
for _ in $(seq 1 40); do
  sleep 3
  CODE=$(screen mac | python3 "$SCAN" findcode 4< <(printf %s "$MAC_BASE")) || true
  if [ -n "$CODE" ]; then found=1; break; fi
done
if [ "$found" != 1 ]; then
  cap mac g3-mac-no-code 1
  abort g3-new-network-box "no join code appeared on the Mac after choosing 'Start a new network on kollabor.ai'" g3-mac-no-code.txt
fi
sleep 2
capscreen mac g3-mac-connect-screen 1
S=$LAST
missing=""
for t in "On your other computer" "1) kollab --upgrade (or pip install -U kollab)" "2) run kollab and press Enter on the same notice" "3) choose Join with a code and type the code"; do
  grep -Fq -- "$t" <<<"$S" || missing="$missing [$t]"
done
if [ -z "$missing" ]; then
  rec g3-new-network-box PASS g3-mac-connect-screen.txt "the Connect screen shows a join code and the 'On your other computer' box with its three steps"
else
  rec g3-new-network-box FAIL g3-mac-connect-screen.txt "the Connect screen shows a join code but is missing:$missing"
fi

# ================================================================== g4 ====
if ! wait_notice srv 150; then
  cap srv g4-srv-no-notice
  abort g4-srv-join-form "the first launch on the server never showed the notice" g4-srv-no-notice.txt
fi
capscreen srv g4-srv-notice
key srv Enter
wait_screen srv 'Join with a code' 40 || { cap srv g4-srv-no-choices; abort g4-srv-join-form "Enter on the server's notice did not show the choices" g4-srv-no-choices.txt; }
sleep 2
key srv Down; sleep 1
capscreen srv g4-srv-choices
grep -Eq '> +Join with a code' <<<"$LAST" || abort g4-srv-join-form "Down did not move the selection to 'Join with a code'" g4-srv-choices.txt
key srv Enter
wait_screen srv 'enter privately|first device' 40 || { cap srv g4-srv-no-form; abort g4-srv-join-form "'Join with a code' did not open the private code form" g4-srv-no-form.txt; }
sleep 2
capscreen srv g4-srv-form
if grep -Eqi '> *domain' <<<"$LAST"; then key srv Tab; sleep 1; fi   # an older form focuses Domain first
typ srv "$CODE" 0.08
sleep 1
capscreen srv g4-srv-code-typed   # must show the masked field, never the code
if shows srv '********'; then
  rec g4-srv-join-form PASS g4-srv-code-typed.txt "server: notice, Enter, 'Join with a code', private form, code typed (masked)"
else
  rec g4-srv-join-form FAIL g4-srv-code-typed.txt "the server form does not show the masked code field after typing"
fi

# ================================================================== g5 ====
key srv Enter
sleep 4
cap srv g5-srv-after-enter
if grep -Eqi 'request sent|waiting for approval' <<<"$(screen srv)"; then SUBMIT_NOTE="server showed 'request sent ... waiting for approval'"; else SUBMIT_NOTE="no 'request sent' line 4s after Enter"; fi
accepted=0; joined=0; SRV_DEVICE=""
end=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$end" ]; do
  S=$(screen mac)
  if [ "$accepted" = 0 ] && grep -Fq -- 'wants to join' <<<"$S"; then
    capscreen mac g5-mac-wants-to-join 1
    SRV_DEVICE=$(grep -F 'wants to join' <<<"$S" | head -1 | sed -E 's/.*[[:space:]]([^[:space:]]+)[[:space:]]+wants to join.*/\1/')
    key mac a; accepted=1; sleep 3
    capscreen mac g5-mac-accepted 1
  fi
  if grep -Eq 'joined .+ as .+trust: ' <<<"$(raw srv 200)"; then joined=1; break; fi
  sleep 3
done
capscreen srv g5-srv-joined
SRV_FORM=$LAST
if [ "$joined" = 1 ]; then
  JOINED_LINE=$(raw srv 200 | grep -E 'joined ' | tail -1 | sed -E 's/^[[:space:]]+//' || true)
  rec g5-join-completes PASS g5-srv-joined.txt "accept on the Mac: $([ "$accepted" = 1 ] && echo "yes ($SRV_DEVICE)" || echo "not asked"); server printed: $JOINED_LINE; $SUBMIT_NOTE"
else
  abort g5-join-completes "the server never printed 'joined <network> as <device>. trust: <level>' within 240s (Mac asked to accept: $accepted; $SUBMIT_NOTE)" g5-srv-joined.txt
fi

# --- both /connect status list the other device's agents (names for g6 and g8)
key mac Escape; key srv Escape; sleep 2
TOKEN='[A-Za-z0-9_-]+@[A-Za-z0-9_.-]+'
REMOTE=""; BACK=""; MAC_DEVICE=""
for _ in $(seq 1 8); do
  cmd mac "/connect status"; cmd srv "/connect status"; sleep 7
  MAC_STATUS=$(raw mac 200); SRV_STATUS=$(raw srv 200)
  MAC_DEVICE=$(sed -nE 's/.*this device[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$MAC_STATUS" | tail -1)
  REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -F "@$SRV_DEVICE" | head -1 || true)
  if [ -z "$REMOTE" ]; then REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -vF "@${MAC_DEVICE:-__none__}" | head -1 || true); fi
  BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -F "@${MAC_DEVICE:-__none__}" | head -1 || true)
  if [ -z "$BACK" ]; then BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -vF "@$SRV_DEVICE" | head -1 || true); fi
  if [ -n "$REMOTE" ] && [ -n "$BACK" ] && [ -n "$MAC_DEVICE" ]; then break; fi
  sleep 8
done
cap mac g5-mac-status; cap srv g5-srv-status
[ -n "$REMOTE" ] && [ -n "$BACK" ] && [ -n "$MAC_DEVICE" ] || abort g5-status-both-sides "mac sees '${REMOTE:-nothing}', server sees '${BACK:-nothing}', mac device '${MAC_DEVICE:-unknown}'" g5-mac-status.txt

# ================================================================== g6 ====
g6=0; end=$(( $(date +%s) + 60 ))
while :; do
  ALL=$(printf '%s\n%s\n' "$SRV_FORM" "$(raw srv 500)" | flat)
  if grep -Fq -- "settings arrive sealed from $MAC_DEVICE" <<<"$ALL" && grep -Fq -- 'run /login' <<<"$ALL"; then g6=1; break; fi
  if [ "$(date +%s)" -ge "$end" ]; then break; fi
  sleep 3
done
cap srv g6-srv-pane
if [ "$g6" = 1 ]; then
  rec g6-post-join-line PASS g6-srv-pane.txt "server line names '$MAC_DEVICE' and says run /login"
else
  FOUND=$(grep -o 'settings arrive sealed from [^,]*' <<<"$ALL" | tail -1 || true)
  rec g6-post-join-line FAIL g6-srv-pane.txt "no 'settings arrive sealed from $MAC_DEVICE ... run /login' on the server within 60s of closing the form (saw: '${FOUND:-no post-join line at all}')"
fi

# ================================================================== g7 ====
say "relaunching the Mac client: the marker was written, so no notice"
MAC_MARKER=$([ -e "$M1_MAC_WS/.connect-guide-seen" ] && echo yes || echo no)
SRV_MARKER=$(m1_ssh "[ -e '$M1_SRV_WS/.connect-guide-seen' ] && echo yes || echo no")
tmux kill-session -t "$M1_MAC_SESSION" 2>/dev/null || true
sleep 3
launch mac
wait_ready mac || abort g7-relaunch-no-notice "no output from the relaunched Mac TUI after 90s"
for _ in $(seq 1 30); do if shows mac '❯'; then break; fi; sleep 2; done
sleep 15   # the notice, if it were coming, is pushed within ~10s of startup
cap mac g7-mac-relaunch
if ! shows mac '❯'; then
  rec g7-relaunch-no-notice FAIL g7-mac-relaunch.txt "the relaunched TUI never reached its prompt, so absence of the notice proves nothing"
elif grep -Fq -- "$N1" <<<"$LAST" || shows mac 'Start a new network on kollabor.ai'; then
  rec g7-relaunch-no-notice FAIL g7-mac-relaunch.txt "the notice (or the choices) showed again after a relaunch (marker on the Mac: $MAC_MARKER)"
elif [ "$MAC_MARKER" != yes ] || [ "$SRV_MARKER" != yes ]; then
  rec g7-relaunch-no-notice FAIL g7-mac-relaunch.txt "no notice, but a marker file is missing (mac: $MAC_MARKER, srv: $SRV_MARKER)"
else
  rec g7-relaunch-no-notice PASS g7-mac-relaunch.txt "relaunched Mac TUI at its prompt with no notice; marker files exist on both hosts"
fi

# ================================================================== g8 ====
if [ "$MAC_APPROVAL" != trust_all ]; then say "mac approval mode is '${MAC_APPROVAL:-default}': /permissions trust"; cmd mac "/permissions trust"; sleep 2; fi
if [ "$SRV_APPROVAL" != trust_all ]; then say "srv approval mode is '${SRV_APPROVAL:-default}': /permissions trust"; cmd srv "/permissions trust"; sleep 2; fi
sleep 15   # the relaunched client re-attaches; give the agents a moment to be online

ask_over() { # ask_over <from> <to> <to-token> <prompt> <want-ERE> <step>: one request, its reply, the far shell
  local from=$1 to=$2 tok=$3 prompt=$4 want=$5 step=$6
  local ta=${tok%@*} td=${tok#*@} re s0 s1 b blk seg sends dirty
  re="${ta}@${td}[[:space:]]*(->|→)"
  s0=$(shell_ok "$to" "$(off_of "$to")")
  b=$(count_pat "$from" "$re")
  cmd "$from" "$prompt"
  if ! wait_for "$from" "$re" 300 "$b"; then
    cap "$from" "$step-from"; cap "$to" "$step-to"
    rec "$step" FAIL "$step-from.txt" "no hub message from $tok within 300s (the other pane: $step-to.txt)"
    return 0
  fi
  sleep 5
  cap "$from" "$step-from"; cap "$to" "$step-to"
  blk=$(raw "$from" 500 | awk -v re="$re" '$0 ~ re {n=NR} {l[NR]=$0} END {if (n) for (i=n+1; i<=n+8 && i<=NR; i++) print l[i]}')
  s1=$(shell_ok "$to" "$(off_of "$to")")
  if [ "${s1:-0}" -le "${s0:-0}" ]; then
    rec "$step" FAIL "$step-to.txt" "reply arrived but the $to log shows no new shell tool run (terminal: SUCCESS count $s0 -> $s1)"
  elif ! grep -Eqi -- "$want" <<<"$blk"; then
    rec "$step" FAIL "$step-from.txt" "reply from $tok arrived without the expected content ($want)"
  else
    seg=$(raw "$from" 500 | awk -v k="ask ${tok} to run" 'index($0,k){n=NR} {l[NR]=$0} END{ if(n) for(i=n;i<=NR;i++) print l[i]}')
    sends=$(grep -Ec "(->|→) ${tok}[[:space:]]*$" <<<"$seg" || true)
    dirty=$(grep -Eci 'not online|warning:|\[warn\]|could not be delivered' <<<"$seg" || true)
    if [ "${sends:-0}" -gt 1 ] || [ "${dirty:-0}" -gt 0 ]; then
      rec "$step" FAIL "$step-from.txt" "reply from $tok is right ($to shell runs $s0 -> $s1) but the transcript is not clean: ${sends:-0} hub_msg(s) sent, ${dirty:-0} warning line(s)"
    else
      rec "$step" PASS "$step-from.txt" "reply from $tok with the expected content; $to shell runs $s0 -> $s1; one hub_msg, no warnings"
    fi
  fi
}
say "g8: Mac agent asks $REMOTE, server agent asks $BACK"
ask_over mac srv "$REMOTE" "$(printf 'ask %s to run `uname -n` and `uptime` and report back what they print' "$REMOTE")" "$SRV_HOSTNAME|load average|up [0-9]" g8-mac-to-srv
ask_over srv mac "$BACK" "$(printf 'ask %s to run `uname -n` and report back what it prints' "$BACK")" "$MAC_HOSTNAME" g8-srv-to-mac

# ================================================================== g9 ====
say "scanning panes and logs"
for h in mac srv; do
  pf=""
  for f in ${PANE_FINDINGS[@]+"${PANE_FINDINGS[@]}"}; do case $f in "$h":*) pf="$pf ${f#*:}" ;; esac; done
  res=$(log_scan "$h" "$(off_of "$h")")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "g9-clean-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ -n "$pf" ]; then
    rec "g9-clean-$h" FAIL pane-findings.txt "join code, 64-hex, relay: address, receipt or error text on $h panes:$pf"
  elif [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "g9-clean-$h" PASS "logscan-$h.txt" "$h: every captured pane and $(kv "$res" size) bytes of kollab.log clean"
  else
    rec "g9-clean-$h" FAIL "logscan-$h.txt" "log findings on $h: code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
done

# The real marker must never be written by this run (the runs set KOLLAB_CONNECT_GUIDE_MARKER).
REAL_MARK_MAC_END=$([ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no)
REAL_MARK_SRV_END=$(m1_ssh '[ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no')
if [ "$REAL_MARK_MAC_END" = no ] && [ "$REAL_MARK_SRV_END" = no ]; then
  rec z-real-marker-absent PASS - "~/.kollab/connect-guide-seen does not exist on the Mac or $M1_HOST"
else
  rec z-real-marker-absent FAIL - "~/.kollab/connect-guide-seen exists: mac=$REAL_MARK_MAC_END srv=$REAL_MARK_SRV_END (before this run: mac=$REAL_MARK_MAC srv=$REAL_MARK_SRV)"
fi
