#!/usr/bin/env bash
# proof.sh: live proof of Story 7, manual trust (issue #121, release 0.11.0).
# INSTALLED builds, driven through tmux like a user on this Mac (s7-mac) and on alzan-prod
# (s7-srv), 120x40, default (daemon) launch, fresh workspaces. Setup rows first (the guided
# g1-g5 flow: network started on the Mac, server joined), then:
#   s1 /connect trust manual (Mac)   s2 first message blocked, the screen says how to authorize
#   s3 /connect authorize            s4 /connect send queues the request
#   s5 /connect task                 s6 the reply returns through the task envelope
#   s7 /connect withdraw             s8 /connect cancel stops a running request
#   s9 /connect answer (SKIP when the model never asks)   s10 clean transcript, both hosts
# The join code lives only in the shell variable CODE: read from a pane capture, typed into the
# other pane through a pipe, replaced with "[join code]" in every evidence file.
# Never run with bash -x. Exit 0 when no row FAILs; a SKIP row is listed and is never a PASS.
set -euo pipefail
S7_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
M1_ROOT_NAME=${M1_ROOT_NAME:-kollab-s7}; M1_MAC_WS=${M1_MAC_WS:-$HOME/kollab-s7-mac}; M1_SRV_WS_NAME=${M1_SRV_WS_NAME:-kollab-s7-srv}
M1_MAC_SESSION=${M1_MAC_SESSION:-s7-mac}; M1_SRV_SESSION=${M1_SRV_SESSION:-s7-srv}
GS_INSTALL=wheels; GS_PYPI_TO=; GS_STOP_AFTER=   # names the copied guided setup rows still read
GS_EXPECT_VERSION=${S7_EXPECT_VERSION:-0.11.0.dev7}
. "$S7_DIR/../m1/env.sh"
# Own ssh master: the m1 one is shared with other proofs and must never be closed from here.
M1_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-s7-%C" -o ControlPersist=300)
case "$M1_ROOT_NAME" in kollab-s7*) ;; *) die "M1_ROOT_NAME=$M1_ROOT_NAME: this proof only runs from a kollab-s7* venv root" ;; esac
case "$M1_MAC_SESSION|$M1_SRV_SESSION" in s7-*"|s7-"*) ;; *) die "tmux sessions must be s7-*, got $M1_MAC_SESSION / $M1_SRV_SESSION" ;; esac

EVID=$S7_DIR/evidence
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

rec() { # rec <step> <PASS|FAIL|SKIP> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 skips=0 n=${#REC_STEP[@]}
  printf '\n%-30s %-6s %s\n' STEP RESULT EVIDENCE
  printf '%-30s %-6s %s\n' ---- ------ --------
  for ((i = 0; i < n; i++)); do
    printf '%-30s %-6s %s\n' "${REC_STEP[$i]}" "${REC_STATUS[$i]}" "${REC_EVID[$i]}"
    case "${REC_STATUS[$i]}" in
      PASS) ;;
      SKIP) skips=$((skips + 1)); printf '    -> %s\n' "${REC_NOTE[$i]}" ;;
      *) fails=$((fails + 1)); printf '    -> %s\n' "${REC_NOTE[$i]}" ;;
    esac
  done
  if [ "$code" -ne 0 ] && [ "$fails" -eq 0 ]; then
    printf '\nproof.sh stopped early (exit %s) before every step ran. Not a pass.\n' "$code"
    exit "$code"
  fi
  if [ "$fails" -gt 0 ]; then printf '\nFAIL: %d step(s) failed. Not a pass. Evidence: %s\n' "$fails" "$EVID"; exit 1; fi
  printf '\nPASS: %d rows, %d skipped (a skip is not a pass), transcript clean. Evidence: %s\n' "$n" "$skips" "$EVID"
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
  abort setup-g1-notice-text "the first launch on the Mac never showed the notice (both lines, exactly)" g1-mac-no-notice.txt
fi
sleep 2
capscreen mac g1-mac-notice
MAC_PROCS=$(pgrep -f -- "[${M1_MAC_VENV:0:1}]${M1_MAC_VENV:1}/bin/" | wc -l | tr -d ' ' || true)
rec setup-g1-notice-text PASS g1-mac-notice.txt "both lines of the notice on the Mac's first launch; $MAC_PROCS processes run from the venv (client + daemon)"

# ================================================================== g2 ====
key mac Enter
if wait_screen mac 'Join with a code' 40 && shows mac 'Start a new network on kollabor.ai'; then
  sleep 2
  capscreen mac g2-mac-choices
  rec setup-g2-two-choices PASS g2-mac-choices.txt "Enter shows 'Start a new network on kollabor.ai' and 'Join with a code'"
else
  cap mac g2-mac-no-choices 1
  abort setup-g2-two-choices "Enter on the notice did not show both choices" g2-mac-no-choices.txt
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
  abort setup-g3-new-network-box "no join code appeared on the Mac after choosing 'Start a new network on kollabor.ai'" g3-mac-no-code.txt
fi
sleep 2
capscreen mac g3-mac-connect-screen 1
S=$LAST
missing=""
for t in "On your other computer" "1) kollab --upgrade (or pip install -U kollab)" "2) run kollab and press Enter on the same notice" "3) choose Join with a code and type the code"; do
  grep -Fq -- "$t" <<<"$S" || missing="$missing [$t]"
done
if [ -z "$missing" ]; then
  rec setup-g3-new-network-box PASS g3-mac-connect-screen.txt "the Connect screen shows a join code and the 'On your other computer' box with its three steps"
else
  rec setup-g3-new-network-box FAIL g3-mac-connect-screen.txt "the Connect screen shows a join code but is missing:$missing"
fi

# ================================================================== g4 ====
if ! wait_notice srv 150; then
  cap srv g4-srv-no-notice
  abort setup-g4-srv-join-form "the first launch on the server never showed the notice" g4-srv-no-notice.txt
fi
capscreen srv g4-srv-notice
key srv Enter
wait_screen srv 'Join with a code' 40 || { cap srv g4-srv-no-choices; abort setup-g4-srv-join-form "Enter on the server's notice did not show the choices" g4-srv-no-choices.txt; }
sleep 2
key srv Down; sleep 1
capscreen srv g4-srv-choices
grep -Eq '> +Join with a code' <<<"$LAST" || abort setup-g4-srv-join-form "Down did not move the selection to 'Join with a code'" g4-srv-choices.txt
key srv Enter
wait_screen srv 'enter privately|first device' 40 || { cap srv g4-srv-no-form; abort setup-g4-srv-join-form "'Join with a code' did not open the private code form" g4-srv-no-form.txt; }
sleep 2
capscreen srv g4-srv-form
if grep -Eqi '> *domain' <<<"$LAST"; then key srv Tab; sleep 1; fi   # an older form focuses Domain first
typ srv "$CODE" 0.08
sleep 1
capscreen srv g4-srv-code-typed   # must show the masked field, never the code
if shows srv '********'; then
  rec setup-g4-srv-join-form PASS g4-srv-code-typed.txt "server: notice, Enter, 'Join with a code', private form, code typed (masked)"
else
  rec setup-g4-srv-join-form FAIL g4-srv-code-typed.txt "the server form does not show the masked code field after typing"
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
  rec setup-g5-join-completes PASS g5-srv-joined.txt "accept on the Mac: $([ "$accepted" = 1 ] && echo "yes ($SRV_DEVICE)" || echo "not asked"); server printed: $JOINED_LINE; $SUBMIT_NOTE"
else
  abort setup-g5-join-completes "the server never printed 'joined <network> as <device>. trust: <level>' within 240s (Mac asked to accept: $accepted; $SUBMIT_NOTE)" g5-srv-joined.txt
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
[ -n "$REMOTE" ] && [ -n "$BACK" ] && [ -n "$MAC_DEVICE" ] || abort setup-g5-status-both-sides "mac sees '${REMOTE:-nothing}', server sees '${BACK:-nothing}', mac device '${MAC_DEVICE:-unknown}'" g5-mac-status.txt


# ============================================================ Story 7 rows ====
# Intended product lines the leak/error scan skips (evidence keeps them): the screen hint of
# s2 may carry a "warning:" prefix, and the s7 line says "cannot start work here" on purpose.
PANE_ALLOW='communication grant is required|request [0-9]+ withdrawn; late replies cannot start work here'
record() { # record <label> <evidence-name> <text> [allow-code]
  local label=$1 name=$2 text=$3 allow=${4:-0} out flag="" kept
  if [ "$allow" = 1 ]; then flag="--allow-code"; fi
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  kept=$(grep -Ev -- "$PANE_ALLOW" <<<"$text" || true)
  out=$(printf '%s\n' "$kept" | python3 "$SCAN" text $flag 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$label:$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
fl() { raw "$1" "${2:-500}" | flat; }
num_in() { { grep -oE 'request [0-9]+' <<<"$1" | head -1 | grep -oE '[0-9]+'; } || true; }
state_of() { { grep -oE ': [a-z_]+' <<<"$1" | tail -1 | tr -d ': '; } || true; }
block_after() { raw "$1" 500 | awk -v re="$2" '$0 ~ re {n=NR} {l[NR]=$0} END {if (n) for (i=n; i<=n+8 && i<=NR; i++) print l[i]}'; }
log_grep() { # log_grep <mac|srv> <fixed text>: this run's occurrences in kollab.log
  local off; off=$(off_of "$1")
  if [ "$1" = mac ]; then { tail -c +"$((off + 1))" "$(log_path mac)" 2>/dev/null | grep -Fc -- "$2"; } || true
  else m1_ssh "tail -c +$((off + 1)) '$(log_path srv)' 2>/dev/null | grep -Fc -- '$2'" || true; fi
}
run_cmd() { # run_cmd <host> <command line> <ERE> <timeout-s>: type it, wait for a NEW line matching ERE; OUT = the newest one
  local h=$1 line=$2 re=$3 t=${4:-60} b
  b=$(count_pat "$h" "$re")
  cmd "$h" "$line"
  if wait_for "$h" "$re" "$t" "$b"; then
    OUT=$(raw "$h" 500 | grep -E -- "$re" | tail -1 | sed -E 's/^[[:space:]]+//' || true)
    return 0
  fi
  OUT=""; return 1
}
N=""; M=""; K=""; OUT=""
RE_REPLY="${REMOTE}[[:space:]]*(->|→)"

if [ "$MAC_APPROVAL" != trust_all ]; then say "mac approval mode is '${MAC_APPROVAL:-default}': /permissions trust"; cmd mac "/permissions trust"; sleep 2; fi
if [ "$SRV_APPROVAL" != trust_all ]; then say "srv approval mode is '${SRV_APPROVAL:-default}': /permissions trust"; cmd srv "/permissions trust"; sleep 2; fi
sleep 10

# ================================================================== s1 ====
say "s1: /connect trust manual on the Mac"
if run_cmd mac "/connect trust manual" 'trust for .* is now manual' 60; then
  sleep 2; cap mac s1-mac-trust-manual
  if grep -Fq 'messages now need /connect authorize or /connect send' <<<"$(fl mac 200)"; then
    rec s1-trust-manual PASS s1-mac-trust-manual.txt "trust line plus the hint: messages now need /connect authorize or /connect send"
  else
    rec s1-trust-manual FAIL s1-mac-trust-manual.txt "trust line shown, but not the 'messages now need /connect authorize or /connect send' hint"
  fi
else
  cap mac s1-mac-no-trust-line
  rec s1-trust-manual FAIL s1-mac-no-trust-line.txt "no 'trust for ... is now manual' line within 60s of /connect trust manual"
fi

# ================================================================== s2 ====
say "s2: the Mac agent's first message must not go out"
GRANT_RE='communication grant is required|use /connect authorize'
SRV_IN_RE="${BACK}[[:space:]]*(->|→)"
SRV_SH0=$(shell_ok srv "$(off_of srv)"); SRV_IN0=$(count_pat srv "$SRV_IN_RE"); GB=$(count_pat mac "$GRANT_RE")
cmd mac "$(printf 'ask %s to run `uname -n` and report back what it prints' "$REMOTE")"
if wait_for mac "$GRANT_RE" 300 "$GB"; then
  sleep 30   # the Mac agent finishes its turn
  cap mac s2-mac-blocked; cap srv s2-srv-quiet
  SRV_SH1=$(shell_ok srv "$(off_of srv)"); SRV_IN1=$(count_pat srv "$SRV_IN_RE"); GATE=$(log_grep mac 'communication grant is required')
  if [ "${SRV_SH1:-0}" -gt "${SRV_SH0:-0}" ] || [ "${SRV_IN1:-0}" -gt "${SRV_IN0:-0}" ]; then
    rec s2-first-msg-blocked FAIL s2-srv-quiet.txt "the Mac screen showed the hint but the server saw the message (shell runs $SRV_SH0 -> $SRV_SH1, inbound lines $SRV_IN0 -> $SRV_IN1)"
  else
    rec s2-first-msg-blocked PASS s2-mac-blocked.txt "Mac screen says how to authorize; the server saw nothing (shell runs $SRV_SH0 -> $SRV_SH1, inbound lines $SRV_IN0 -> $SRV_IN1); gate lines in the Mac log: ${GATE:-0}"
  fi
else
  cap mac s2-mac-no-hint; cap srv s2-srv-quiet
  rec s2-first-msg-blocked FAIL s2-mac-no-hint.txt "no 'grant is required' / 'use /connect authorize' line on the Mac within 300s of the ask"
fi

# ================================================================== s3 ====
say "s3: /connect authorize"
if run_cmd mac "/connect authorize $REMOTE \"check the tunnel\"" "communication authorized: request [0-9]+; expires at [0-9]{2}:[0-9]{2}; recipient" 60; then
  N=$(num_in "$OUT"); cap mac s3-mac-authorize
  if grep -Fq "recipient $REMOTE" <<<"$(fl mac 200)"; then rec s3-authorize PASS s3-mac-authorize.txt "$OUT"; else rec s3-authorize FAIL s3-mac-authorize.txt "the authorized line did not name recipient $REMOTE"; fi
else
  cap mac s3-mac-no-authorize
  rec s3-authorize FAIL s3-mac-no-authorize.txt "no 'communication authorized: request N; expires at HH:MM; recipient $REMOTE' line within 60s"
fi

# ================================================================== s4 ====
say "s4: /connect send"
RB=$(count_pat mac "$RE_REPLY"); SH0=$(shell_ok srv "$(off_of srv)")
SEND_TXT='run uname -n and uptime in your shell and reply with what they print'
if run_cmd mac "/connect send $REMOTE \"$SEND_TXT\"" "request [0-9]+ to ${REMOTE}: [a-z_]+" 120; then
  M=$(num_in "$OUT"); ST=$(state_of "$OUT"); cap mac s4-mac-send
  case "$ST" in
    rejected|failed|cancelled|interrupted) rec s4-send-queued FAIL s4-mac-send.txt "$OUT" ;;
    *) rec s4-send-queued PASS s4-mac-send.txt "$OUT" ;;
  esac
else
  cap mac s4-mac-no-send
  rec s4-send-queued FAIL s4-mac-no-send.txt "no 'request N to $REMOTE: <state>' line within 120s of /connect send"
fi

# ================================================================== s5 ====
say "s5: /connect task"
if [ -z "$M" ]; then
  rec s5-task-state FAIL - "no request number from s4"
elif run_cmd mac "/connect task $REMOTE $M" "request $M on ${REMOTE}: [a-z_]+" 90; then
  cap mac s5-mac-task
  rec s5-task-state PASS s5-mac-task.txt "$OUT"
else
  cap mac s5-mac-no-task
  rec s5-task-state FAIL s5-mac-no-task.txt "no 'request $M on $REMOTE: <state>' line within 90s of /connect task"
fi

# ================================================================== s6 ====
say "s6: the reply comes back through the task envelope"
if wait_for mac "$RE_REPLY" 300 "$RB"; then
  sleep 5; cap mac s6-mac-reply; cap srv s6-srv-ran
  BLK=$(block_after mac "$RE_REPLY"); SH1=$(shell_ok srv "$(off_of srv)")
  if [ "${SH1:-0}" -le "${SH0:-0}" ]; then
    rec s6-reply-via-envelope FAIL s6-srv-ran.txt "a reply arrived but the server log shows no new shell tool run (terminal: SUCCESS count $SH0 -> $SH1)"
  elif ! grep -Eqi -- "$SRV_HOSTNAME|load average|up [0-9]" <<<"$BLK"; then
    rec s6-reply-via-envelope FAIL s6-mac-reply.txt "a reply from $REMOTE arrived without the expected content ($SRV_HOSTNAME / load average)"
  else
    rec s6-reply-via-envelope PASS s6-mac-reply.txt "reply from $REMOTE with the server's output; server shell runs $SH0 -> $SH1"
  fi
else
  cap mac s6-mac-no-reply; cap srv s6-srv-no-reply
  rec s6-reply-via-envelope FAIL s6-mac-no-reply.txt "no reply from $REMOTE within 300s of the authorized send (the server pane: s6-srv-no-reply.txt)"
fi
sleep 25   # the Mac agent finishes reporting the reply

# ================================================================== s7 ====
say "s7: /connect withdraw"
if [ -z "$N" ]; then
  rec s7-withdraw FAIL - "no request number from s3"
elif run_cmd mac "/connect withdraw $N" "request $N withdrawn" 60; then
  cap mac s7-mac-withdraw
  rec s7-withdraw PASS s7-mac-withdraw.txt "$OUT"
else
  cap mac s7-mac-no-withdraw
  rec s7-withdraw FAIL s7-mac-no-withdraw.txt "no 'request $N withdrawn' line within 60s of /connect withdraw $N"
fi

# ================================================================== s8 ====
say "s8: /connect cancel on a request that takes a while"
LONG='run sleep 240 in your shell and wait for it to finish, then reply done'
if run_cmd mac "/connect send $REMOTE \"$LONG\"" "request [0-9]+ to ${REMOTE}: [a-z_]+" 120; then
  K=$(num_in "$OUT"); sleep 30   # the server agent starts the sleep
  if [ -z "$K" ] || [ "$K" = "$M" ]; then
    rec s8-cancel-running FAIL - "no new request number from the long /connect send (got '$K', s4 was '$M')"
  else
    run_cmd mac "/connect task $REMOTE $K" "request $K on ${REMOTE}: [a-z_]+" 90 || true
    BEFORE=$(state_of "$OUT")
    if run_cmd mac "/connect cancel $REMOTE $K" "request $K on ${REMOTE}: (cancelled|interrupted)" 90; then
      CANCEL_STATE=$(state_of "$OUT"); sleep 3
      if run_cmd mac "/connect task $REMOTE $K" "request $K on ${REMOTE}: (cancelled|interrupted)" 90; then
        cap mac s8-mac-cancel
        rec s8-cancel-running PASS s8-mac-cancel.txt "state before cancel: ${BEFORE:-unknown}; cancel: $CANCEL_STATE; /connect task after: $(state_of "$OUT")"
      else
        cap mac s8-mac-cancel
        rec s8-cancel-running FAIL s8-mac-cancel.txt "cancel said $CANCEL_STATE but a following /connect task does not show the request stopped"
      fi
    else
      cap mac s8-mac-no-cancel; cap srv s8-srv-no-cancel
      rec s8-cancel-running FAIL s8-mac-no-cancel.txt "no cancelled/interrupted state from /connect cancel (state before: ${BEFORE:-unknown}; the server pane: s8-srv-no-cancel.txt)"
    fi
  fi
else
  cap mac s8-mac-no-send
  rec s8-cancel-running FAIL s8-mac-no-send.txt "the long /connect send was not accepted within 120s"
fi
sleep 30   # the server agent winds down the cancelled turn

# ================================================================== s9 ====
say "s9: /connect answer (the server agent must ask a question)"
if run_cmd srv "/connect trust manual" 'trust for .* is now manual' 60; then say "server is on trust manual too (a remote agent only gets the one-question line there)"; else say "server /connect trust manual: no confirmation line"; fi
QRE='answer with /connect answer [0-9]+'
QB=$(count_pat mac "$QRE")
ASK='before doing anything else, ask me one question: should the report be red or blue? wait for my answer, then reply with the word color and my answer'
run_cmd mac "/connect send $REMOTE \"$ASK\"" "request [0-9]+ to ${REMOTE}: [a-z_]+" 120 || true
if wait_for mac "$QRE" 240 "$QB"; then
  sleep 3; cap mac s9-mac-question
  QN=$(raw mac 500 | grep -oE 'answer with /connect answer [0-9]+' | tail -1 | grep -oE '[0-9]+$' || true)
  RB2=$(count_pat mac "$RE_REPLY")
  if [ -z "$QN" ]; then
    rec s9-answer FAIL s9-mac-question.txt "a question showed but its number could not be read"
  elif run_cmd mac "/connect answer $QN blue" "question $QN answered: [a-z_]+" 60; then
    if wait_for mac "$RE_REPLY" 240 "$RB2"; then
      sleep 5; cap mac s9-mac-final
      if grep -Eqi 'blue' <<<"$(block_after mac "$RE_REPLY")"; then
        rec s9-answer PASS s9-mac-final.txt "question $QN asked by $REMOTE; /connect answer: $OUT; the final reply carries the answer"
      else
        rec s9-answer FAIL s9-mac-final.txt "answered question $QN but the next reply from $REMOTE does not carry the answer"
      fi
    else
      cap mac s9-mac-no-final
      rec s9-answer FAIL s9-mac-no-final.txt "answered question $QN ($OUT) but no further reply from $REMOTE within 240s"
    fi
  else
    cap mac s9-mac-no-answered
    rec s9-answer FAIL s9-mac-no-answered.txt "no 'question $QN answered: <state>' line within 60s of /connect answer"
  fi
else
  cap mac s9-mac-no-question; cap srv s9-srv-no-question
  rec s9-answer SKIP "s9-mac-no-question.txt" "the server agent asked no question within 240s of a request that told it to ask one (model behavior); /connect answer not exercised"
fi

# ================================================================= s10 ====
say "s10: scanning panes and logs"
for h in mac srv; do
  pf=""
  for f in ${PANE_FINDINGS[@]+"${PANE_FINDINGS[@]}"}; do case $f in "$h":*) pf="$pf ${f#*:}" ;; esac; done
  res=$(log_scan "$h" "$(off_of "$h")")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "s10-clean-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ -n "$pf" ]; then
    rec "s10-clean-$h" FAIL pane-findings.txt "join code, 64-hex, relay: address, receipt or error text on $h panes:$pf"
  elif [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "s10-clean-$h" PASS "logscan-$h.txt" "$h: every captured pane and $(kv "$res" size) bytes of kollab.log clean"
  else
    rec "s10-clean-$h" FAIL "logscan-$h.txt" "log findings on $h: code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
done
REAL_MARK_MAC_END=$([ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no)
REAL_MARK_SRV_END=$(m1_ssh '[ -e "$HOME/.kollab/connect-guide-seen" ] && echo yes || echo no')
if [ "$REAL_MARK_MAC_END" = no ] && [ "$REAL_MARK_SRV_END" = no ]; then
  rec z-real-marker-absent PASS - "~/.kollab/connect-guide-seen does not exist on the Mac or $M1_HOST"
else
  rec z-real-marker-absent FAIL - "~/.kollab/connect-guide-seen exists: mac=$REAL_MARK_MAC_END srv=$REAL_MARK_SRV_END (before this run: mac=$REAL_MARK_MAC srv=$REAL_MARK_SRV)"
fi
