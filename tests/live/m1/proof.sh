#!/usr/bin/env bash
# proof.sh: drive Stories 1, 2 and 3 of docs/specs/agent-network-simple-flow.md
# the way a user would, on the INSTALLED m1 builds, in tmux on this Mac
# (m1-mac) and on alzan-prod (m1-srv), 120x40. Evidence lands in m1/evidence/.
#
# The join code lives only in the shell variable CODE: it is read out of a pane
# capture, typed into the other pane through a pipe, compared inside python, and
# replaced with "[join code]" in every evidence file. It is never echoed,
# written to disk, or placed on a command line (tmux only ever sees one
# character per argv).
#
# Exit code 0 only if every row of the final table is PASS.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
EVID=$M1_DIR/evidence
mkdir -p "$EVID"
: > "$EVID/pane-findings.txt"   # this run only
POLL=3
SCAN=$M1_DIR/scan.py
CODE=""
LAST=""
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()
SCREEN_MODE=0

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  printf '\n%-34s %-6s %s\n' STEP RESULT EVIDENCE
  printf '%-34s %-6s %s\n' ---- ------ --------
  for ((i = 0; i < n; i++)); do
    printf '%-34s %-6s %s\n' "${REC_STEP[$i]}" "${REC_STATUS[$i]}" "${REC_EVID[$i]}"
    [ "${REC_STATUS[$i]}" = PASS ] || { fails=$((fails + 1)); printf '    -> %s\n' "${REC_NOTE[$i]}"; }
  done
  if [ "$code" -ne 0 ] && [ "$fails" -eq 0 ]; then
    printf '\nproof.sh stopped early (exit %s) before every step ran. Not a pass.\n' "$code"
    exit "$code"
  fi
  if [ "$fails" -gt 0 ]; then printf '\nFAIL: %d step(s) failed. Not a pass.\n' "$fails"; exit 1; fi
  printf '\nPASS: %d steps, transcript clean.\n' "$n"
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
cmd() { typ "$1" "$2"; sleep 1.2; key "$1" Enter; }   # type a slash command and press Enter
count_pat() { raw "$1" 500 | grep -Ec -- "$2" || true; }
wait_for() { # wait_for <host> <ERE> <timeout-s> [baseline]: until count(ERE) > baseline
  local h=$1 re=$2 end n base=${4:-0}
  end=$(( $(date +%s) + $3 ))
  while :; do
    n=$(count_pat "$h" "$re")
    [ "$n" -gt "$base" ] && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}
wait_screen() { # wait_screen <host> <ERE> <timeout-s>: until the visible screen matches
  local h=$1 re=$2 end
  end=$(( $(date +%s) + $3 ))
  while :; do
    screen "$h" | grep -Eq -- "$re" && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}
wait_ready() { # a TUI is up when its pane has content; then give it time to settle
  local h=$1 end n
  end=$(( $(date +%s) + 90 ))
  while :; do
    n=$(screen "$h" | grep -c '[^[:space:]]' || true)
    [ "$n" -ge 5 ] && break
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep 2
  done
  sleep 10
}

# ------------------------------------------------------ evidence and scanning ----
# record <label> <evidence-name> <text> [allow-code]: redact into evidence/<name>.txt,
# scan the raw text for leaks and errors, remember findings for the final row.
record() {
  local label=$1 name=$2 text=$3 allow=${4:-0} out flag=""
  [ "$allow" = 1 ] && flag="--allow-code"
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text $flag 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
cap() { LAST=$(raw "$1" "${4:-500}"); record "$1" "$2" "$LAST" "${3:-0}"; }   # cap <host> <name> [allow-code] [history]
capscreen() { LAST=$(screen "$1"); record "$1" "$2" "$LAST" "${3:-0}"; }

log_path() { # <mac|srv>: kollab.log for that workspace, with a glob fallback
  local ws home p
  if [ "$1" = mac ]; then ws=$M1_MAC_WS; home=$HOME; else ws=$M1_SRV_WS; home=$M1_SRV_HOME; fi
  p=$(m1_project_dir "$ws" "$home")/logs/kollab.log
  printf %s "$p"
}
log_size() { if [ "$1" = mac ]; then wc -c < "$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path srv)' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <mac|srv> <offset>: counters on stdout, code from CODE via stdin
  if [ "$1" = mac ]; then printf %s "$CODE" | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf %s "$CODE" | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path srv)" "$2"; fi
}
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }   # kv <scan-output> <key>
shell_ok() { kv "$(log_scan "$1" "$2")" shell_ok; }

run_limited() { local secs=$1; shift; perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"; }
newest_with() { raw "$1" 500 | grep -E -- "$2" | tail -1 || true; }

# =============================================================== preflight ====
say "preflight"
m1_srv_paths
SRV_HOSTNAME=$(m1_ssh uname -n)
[ -x "$M1_MAC_VENV/bin/kollab" ] || abort pre-installed-build "mac venv missing: run install_both.sh"
MAC_V=$(cd "$HOME" && "$M1_MAC_VENV/bin/kollab" --version 2>&1 | tail -1)
SRV_V=$(m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/kollab' --version 2>&1 | tail -1" || true)
case "$MAC_V|$SRV_V" in
  *"$M1_VERSION|"*"$M1_VERSION") rec pre-installed-build PASS - "mac: $MAC_V / srv: $SRV_V" ;;
  *) abort pre-installed-build "expected $M1_VERSION on both, got mac='$MAC_V' srv='$SRV_V'" ;;
esac

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
  if [ "$h" = mac ]; then present=$([ -e "$state" ] && echo yes || echo no); else present=$(m1_ssh "[ -e '$state' ] && echo yes || echo no"); fi
  [ "$present" = no ] || abort pre-fresh-workspaces "$h workspace $ws already has network state (previous run). Use a new workspace: mkdir it and rerun with M1_MAC_WS=... / M1_SRV_WS_NAME=..."
done
rec pre-fresh-workspaces PASS - "no network state for either workspace yet"

[ -s "$HOME/.kollab/oauth/openai.json" ] || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on the Mac (kollab --login)"
m1_ssh "[ -s \"\$HOME/.kollab/oauth/openai.json\" ]" || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on $M1_HOST (kollab --login there)"
rec pre-llm-login PASS - "both hosts have an openai oauth login; both agents run --llm openai-oauth"

m1_ssh "mkdir -p '$M1_SRV_ROOT/bin'"
scp -q "${M1_SSH_OPTS[@]}" "$M1_DIR/scan.py" "$M1_DIR/tmuxtype.py" "$M1_HOST:$M1_SRV_ROOT/bin/"

approval() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("kollabor",{}).get("permissions",{}).get("approval_mode",""))' "$1" 2>/dev/null || true; }
MAC_APPROVAL=$(approval "$HOME/.kollab/config.json")
SRV_APPROVAL=$(m1_ssh "python3 -c 'import json,os; c=json.load(open(os.path.expanduser(\"~/.kollab/config.json\"))); print(c.get(\"kollabor\",{}).get(\"permissions\",{}).get(\"approval_mode\",\"\"))' 2>/dev/null" || true)

MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv)

# ================================================================ launch ====
say "launching both TUIs (120x40) in fresh workspaces"
mkdir -p "$M1_MAC_WS"
tmux kill-session -t "$M1_MAC_SESSION" 2>/dev/null || true
tmux new-session -d -s "$M1_MAC_SESSION" -x 120 -y 40 "cd '$M1_MAC_WS' && env KOLLAB_NO_KEYRING=1 '$M1_MAC_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh"
m1_ssh "mkdir -p '$M1_SRV_WS'; tmux kill-session -t $M1_SRV_SESSION 2>/dev/null; tmux new-session -d -s $M1_SRV_SESSION -x 120 -y 40 \"cd '$M1_SRV_WS' && env KOLLAB_NO_KEYRING=1 '$M1_SRV_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh\""
wait_ready mac || abort s0-launch-mac "no output from the Mac TUI after 90s"
wait_ready srv || abort s0-launch-srv "no output from the server TUI after 90s"
cap mac s0-01-mac-launch; cap srv s0-02-srv-launch
for h in mac srv; do
  att=$(tm "$h" display -p -t "$(sess "$h")" '#{session_attached}' || echo 0)
  [ "$att" = 0 ] || say "WARNING: someone is attached to $(sess "$h"); keys can vanish while a human is attached"
done
if [ "$MAC_APPROVAL" != trust_all ]; then say "mac approval mode is '${MAC_APPROVAL:-default}': typing /permissions trust"; cmd mac "/permissions trust"; sleep 2; fi
if [ "$SRV_APPROVAL" != trust_all ]; then say "srv approval mode is '${SRV_APPROVAL:-default}': typing /permissions trust"; cmd srv "/permissions trust"; sleep 2; fi
rec s0-launch PASS s0-01-mac-launch.txt "both TUIs up; see s0-02-srv-launch.txt"

# ================================================================ Story 1 ====
say "Story 1: join the server to the Mac's network"
MAC_BASE=$(raw mac 500)
cmd mac "/connect"; sleep 5
if grep -Eq 'network +none' <<<"$(screen mac)" && grep -Eq 'first device\?|enter privately' <<<"$(screen mac)"; then
  say "Mac is on no network: the form is up; empty code + Enter starts one on kollabor.ai"
  capscreen mac s1-00a-mac-form-no-network 1
  key mac Enter
  wait_screen mac 'connected to|joined |could not|enter/esc close|cancelled' 90 || true
  capscreen mac s1-00b-mac-network-started 1
  key mac Escape; sleep 3
  cmd mac "/connect"
fi
found=0
for _ in $(seq 1 30); do
  sleep 3
  CODE=$(screen mac | python3 "$SCAN" findcode 4< <(printf %s "$MAC_BASE")) || true
  if [ -n "$CODE" ]; then found=1; break; fi
  grep -Eq 'first device\?|enter privately' <<<"$(screen mac)" && break
  grep -Eq 'trust: |join code +run /connect code' <<<"$(screen mac)" && break   # status text, not the Connect screen
done
if [ "$found" = 1 ]; then
  SCREEN_MODE=1
  capscreen mac s1-01-mac-connect-screen 1
  rec s1-01-mac-connect-code PASS s1-01-mac-connect-screen.txt "the Connect screen shows a join code"
else
  say "NOTE: /connect on the Mac did not show a join code (bare /connect on a connected device printed status text: the Connect screen did not open). Falling back to /connect code and /connect accept <device>."
  capscreen mac s1-01a-mac-connect-noscreen 1
  key mac Escape; sleep 2
  b=$(count_pat mac 'network[: ]')
  cmd mac "/connect status"; wait_for mac 'network[: ]' 20 "$b" || true; sleep 2
  if grep -Eq 'network:? +none' <<<"$(raw mac 60)" && ! grep -Eq 'via [a-z]' <<<"$(raw mac 60)"; then
    say "NOTE: the Mac is on no network yet: joining kollabor.ai first (/connect kollabor.ai)"
    b=$(count_pat mac 'via kollabor\.ai')
    cmd mac "/connect kollabor.ai"
    wait_for mac 'via kollabor\.ai' 90 "$b" || { cap mac s1-01c-mac-join-network 1; abort s1-01-mac-connect-code "/connect kollabor.ai never showed 'via kollabor.ai'" s1-01c-mac-join-network.txt; }
    sleep 3
  fi
  cmd mac "/connect code"
  pressed=0
  for _ in $(seq 1 30); do
    sleep 3
    snap=$(screen mac)
    CODE=$(printf '%s\n' "$snap" | python3 "$SCAN" findcode 4< <(printf %s "$MAC_BASE")) || true
    [ -n "$CODE" ] && { found=1; break; }
    if [ "$pressed" = 0 ] && grep -Eq 'Enter: create code' <<<"$snap"; then key mac Enter; pressed=1; fi
  done
  [ "$found" = 1 ] || { cap mac s1-01b-mac-no-code 1; abort s1-01-mac-connect-code "no join code appeared on the Mac (screen or /connect code)" s1-01b-mac-no-code.txt; }
  capscreen mac s1-01-mac-code-fallback 1
  rec s1-01-mac-connect-code FAIL s1-01-mac-code-fallback.txt "the Connect screen did not open (bare /connect printed status text, see s1-01a-mac-connect-noscreen.txt); the code came from /connect code instead (FALLBACK path)"
fi

# --- server: /connect, type the code into the private form, Enter
cmd srv "/connect"; sleep 5
capscreen srv s1-02-srv-connect-form
snap=$(screen srv)
grep -Eqi '> *domain' <<<"$snap" && { key srv Tab; sleep 1; }   # older private form focuses Domain first
typ srv "$CODE" 0.08
sleep 1
capscreen srv s1-03-srv-code-typed          # must show the masked field, never the code
key srv Enter
SUBMIT_AT=$(date +%s)
sleep 4
cap srv s1-04-srv-after-enter
if grep -Eqi 'could not submit|rejected' <<<"$(screen srv)"; then
  abort s1-02-srv-submits-code "the server form reported a failure right after Enter" s1-04-srv-after-enter.txt
elif grep -Eqi 'request sent|waiting for approval' <<<"$(screen srv)"; then
  rec s1-02-srv-submits-code PASS s1-04-srv-after-enter.txt "server showed 'request sent ... waiting for approval'"
else
  rec s1-02-srv-submits-code FAIL s1-04-srv-after-enter.txt "no 'request sent ... waiting for approval' line 4s after Enter; the form still shows the masked code field (continuing: the Mac must decide within the 90s RPC window)"
fi

# --- Mac: sees "<server-device> wants to join", accepts
if [ "$SCREEN_MODE" = 1 ]; then
  wait_for mac 'wants to join' 120 || { cap mac s1-05-mac-no-request 1; abort s1-03-mac-sees-request "Mac never showed '<device> wants to join'" s1-05-mac-no-request.txt; }
else
  key mac Escape; sleep 1
  got=0
  for _ in $(seq 1 12); do
    b=$(count_pat mac 'wants to join'); cmd mac "/connect status"
    wait_for mac 'wants to join' 10 "$b" && { got=1; break; }
  done
  [ "$got" = 1 ] || { cap mac s1-05-mac-no-request 1; abort s1-03-mac-sees-request "no 'wants to join' in /connect status after 2 minutes" s1-05-mac-no-request.txt; }
fi
capscreen mac s1-05-mac-wants-to-join 1
SRV_DEVICE=$(newest_with mac '[^[:space:]]+[[:space:]]+wants to join' | sed -E 's/.*[[:space:]]([^[:space:]]+)[[:space:]]+wants to join.*/\1/')
[ -n "$SRV_DEVICE" ] || SRV_DEVICE=unknown
rec s1-03-mac-sees-request PASS s1-05-mac-wants-to-join.txt "server device is named '$SRV_DEVICE'"

b=$(count_pat mac 'accepted|trusted device')
if [ "$SCREEN_MODE" = 1 ]; then key mac a; else cmd mac "/connect accept $SRV_DEVICE"; fi
if wait_for mac 'accepted|trusted device' 60 "$b"; then
  cap mac s1-06-mac-accepted 1
  rec s1-04-mac-accepts PASS s1-06-mac-accepted.txt "accepted $SRV_DEVICE"
else
  cap mac s1-06-mac-accept-failed 1; abort s1-04-mac-accepts "no 'accepted' / 'trusted device' after pressing accept" s1-06-mac-accept-failed.txt
fi
if wait_for srv 'joined ' 60; then
  cap srv s1-07-srv-joined
  JOINED_LINE=$(raw srv 200 | grep -E 'joined ' | tail -1 | sed -E 's/^[[:space:]]+//')
  if grep -Eq 'joined .+ as .+trust: ' <<<"$JOINED_LINE"; then
    rec s1-05-srv-joined PASS s1-07-srv-joined.txt "server printed: $JOINED_LINE"
  else
    rec s1-05-srv-joined FAIL s1-07-srv-joined.txt "server printed '$JOINED_LINE' instead of 'joined <network> as <device>. trust: <level>'"
  fi
else
  cap srv s1-07-srv-not-joined; rec s1-05-srv-joined FAIL s1-07-srv-not-joined.txt "server never printed a joined line within 60s (continuing: /connect status decides whether the join happened)"
fi

# --- both /connect status list the other device and its agents as agent@device
key mac Escape; key srv Escape; sleep 2
TOKEN='[A-Za-z0-9_-]+@[A-Za-z0-9_.-]+'
REMOTE=""; BACK=""; MAC_DEVICE=""
# The other side's agents can take a moment to show up as online: retry status.
for _ in $(seq 1 8); do
  cmd mac "/connect status"; cmd srv "/connect status"; sleep 7
  MAC_STATUS=$(raw mac 200); SRV_STATUS=$(raw srv 200)
  MAC_DEVICE=$(sed -nE 's/.*this device[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$MAC_STATUS" | tail -1)
  REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -F "@$SRV_DEVICE" | head -1 || true)
  [ -n "$REMOTE" ] || REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -vF "@${MAC_DEVICE:-__none__}" | head -1 || true)
  BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -F "@${MAC_DEVICE:-__none__}" | head -1 || true)
  [ -n "$BACK" ] || BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -vF "@$SRV_DEVICE" | head -1 || true)
  [ -n "$REMOTE" ] && [ -n "$BACK" ] && break
  sleep 8
done
cap mac s1-08-mac-status; cap srv s1-09-srv-status
if [ -n "$REMOTE" ] && [ -n "$BACK" ]; then
  rec s1-06-status-both-sides PASS s1-08-mac-status.txt "mac sees $REMOTE, srv sees $BACK (see s1-09-srv-status.txt)"
else
  abort s1-06-status-both-sides "mac sees '${REMOTE:-nothing}', server sees '${BACK:-nothing}' as agent@device" s1-08-mac-status.txt
fi
RA=${REMOTE%@*}; RD=${REMOTE#*@}

# ================================================================ Story 2 ====
say "Story 2: Mac agent asks $REMOTE to run uname -n and uptime"
SHELL0=$(shell_ok srv "$SRV_LOG0")
REPLY_RE="${RA}@${RD}[[:space:]]*(->|→)"
b=$(count_pat mac "$REPLY_RE")
PROMPT=$(printf 'ask %s to run `uname -n` and `uptime` and report back what they print' "$REMOTE")
cmd mac "$PROMPT"
if wait_for mac "$REPLY_RE" 300 "$b"; then
  sleep 5
  cap mac s2-01-mac-reply; cap srv s2-02-srv-after
  BLOCK=$(raw mac 500 | awk -v re="$REPLY_RE" '$0 ~ re {n=NR} {l[NR]=$0} END {if (n) for (i=n+1; i<=n+8 && i<=NR; i++) print l[i]}')
  SHELL1=$(shell_ok srv "$SRV_LOG0")
  if [ "${SHELL1:-0}" -le "${SHELL0:-0}" ]; then
    rec s2-agent-to-agent FAIL s2-02-srv-after.txt "reply arrived but the server log shows no new shell tool run (terminal: SUCCESS count $SHELL0 -> $SHELL1)"
  elif ! grep -Eqi "$SRV_HOSTNAME|load average|up [0-9]" <<<"$BLOCK"; then
    rec s2-agent-to-agent FAIL s2-01-mac-reply.txt "reply from $REMOTE arrived but carries neither the hostname '$SRV_HOSTNAME' nor uptime output"
  else
    SEG=$(raw mac 500 | awk -v k="ask ${REMOTE} to run" 'index($0,k){n=NR} {l[NR]=$0} END{ if(n) for(i=n;i<=NR;i++) print l[i]}')
    SENDS=$(grep -Ec "(->|→) ${REMOTE}[[:space:]]*$" <<<"$SEG" || true)
    DIRTY=$(grep -Eci 'not online|warning:|\[warn\]|could not be delivered' <<<"$SEG" || true)
    DIRTY_LINE=$(grep -Ei 'not online|warning:|\[warn\]|could not be delivered' <<<"$SEG" | head -1 | sed -E 's/^[[:space:]]+//' | cut -c1-120 || true)
    if [ "${SENDS:-0}" -gt 1 ] || [ "${DIRTY:-0}" -gt 0 ]; then
      rec s2-agent-to-agent FAIL s2-01-mac-reply.txt "reply from $REMOTE has the uname/uptime content and the server ran its shell ($SHELL0 -> $SHELL1), but the transcript is not clean: the Mac agent sent ${SENDS:-0} hub_msg(s) to $REMOTE and the screen shows ${DIRTY:-0} warning line(s), first: '$DIRTY_LINE'"
    else
      rec s2-agent-to-agent PASS s2-01-mac-reply.txt "reply from $REMOTE with uname/uptime content; server shell runs $SHELL0 -> $SHELL1; one hub_msg, no warnings"
    fi
  fi
else
  cap mac s2-01-mac-no-reply; cap srv s2-02-srv-no-reply
  rec s2-agent-to-agent FAIL s2-01-mac-no-reply.txt "no hub message from $REMOTE within 300s (server pane: s2-02-srv-no-reply.txt)"
fi

# ================================================================ Story 3 ====
say "Story 3: shell -> server agent, then the same with a cron-like environment"
KOLLAB="$M1_MAC_VENV/bin/kollab"
ASK1="report the free disk space on / in one line"
ASK2="report the free disk space on /var in one line"
story3() { # story3 <step> <evidence-name> <ask> <env-prefix...>; runs in the Mac workspace
  local step=$1 name=$2 ask=$3; shift 3
  local rc=0 out
  out=$(cd "$M1_MAC_WS" && run_limited 300 "$@" "$KOLLAB" --hub msg "$REMOTE" "$ask" 2>&1) || rc=$?
  record mac "$name" "$(printf 'exit=%s\n%s' "$rc" "$out")"
  if [ "$rc" -ne 0 ]; then rec "$step" FAIL "$name.txt" "exit $rc (expected 0)"
  elif [ -z "$out" ]; then rec "$step" FAIL "$name.txt" "no reply printed"
  elif ! grep -Eqi 'avail|free|disk|used' <<<"$out" || ! grep -Eq '[0-9]' <<<"$out"; then rec "$step" FAIL "$name.txt" "the printed reply is not a disk report (a stale or unrelated reply?): $(head -c 160 <<<"$out" | tr '\n' ' ')"
  else rec "$step" PASS "$name.txt" "exit 0, disk report printed"; fi
}
# Let the Story 2 conversation finish first so the runs below start from a quiet pair of agents. (A
# reply is matched to its own request by thread, so an overlapping earlier request can no longer
# answer Story 3's message; the s3-overlap step below runs exactly that overlap on purpose.)
say "waiting for both agents to go quiet before Story 3"
q_end=$(( $(date +%s) + 300 )); q_last=-1; q_stable=0
while [ "$(date +%s)" -lt "$q_end" ]; do
  q_n=$(m1_ssh "grep -c -E 'TRIGGER_LLM_CONTINUE: Received|Tool execution completed' '$(log_path srv)' 2>/dev/null || echo 0" || echo 0)
  if [ "$q_n" = "$q_last" ] && screen mac | grep -q 'Ready'; then q_stable=$((q_stable + 1)); else q_stable=0; q_last=$q_n; fi
  [ "$q_stable" -ge 4 ] && break
  sleep 10
done
SHELL2=$(shell_ok srv "$SRV_LOG0")
story3 s3-shell-msg s3-01-shell-msg "$ASK1" env KOLLAB_NO_KEYRING=1
story3 s3-cron-env-msg s3-02-cron-env-msg "$ASK2" env -i "HOME=$HOME" PATH=/usr/bin:/bin KOLLAB_NO_KEYRING=1
SHELL3=$(shell_ok srv "$SRV_LOG0")
sleep 5; cap mac s3-03-mac-pane; cap srv s3-04-srv-pane
if [ "${SHELL3:-0}" -ge $((${SHELL2:-0} + 2)) ]; then rec s3-server-ran-its-shell PASS s3-04-srv-pane.txt "server shell runs $SHELL2 -> $SHELL3"
else rec s3-server-ran-its-shell FAIL s3-04-srv-pane.txt "expected 2 new server shell runs, saw $SHELL2 -> $SHELL3"; fi

# Overlap: two shells at once to the same remote agent (two cron jobs, or a cron job and an agent).
# The far agent takes one request at a time; each shell must print only its own request's replies
# and exit 0 once that request's turn ends. The asks have different kinds of answer, so a crossed
# reply shows: A must print the hostname and nothing that reads like a disk report, B the reverse.
say "Story 3 overlap: two shells at once to $REMOTE"
ASK_HOST="run uname -n and reply with only the hostname"
ASK_DISK="report the free disk space on / in one line"
OV_A=$(mktemp); OV_B=$(mktemp)
overlap_shell() { # overlap_shell <outfile> <ask>; exit code lands in <outfile>.rc
  local out=$1 ask=$2 rc=0
  (cd "$M1_MAC_WS" && run_limited 300 env KOLLAB_NO_KEYRING=1 "$KOLLAB" --hub msg "$REMOTE" "$ask" >"$out" 2>&1) || rc=$?
  printf '%s' "$rc" >"$out.rc"
}
overlap_shell "$OV_A" "$ASK_HOST" & OV_PID_A=$!
overlap_shell "$OV_B" "$ASK_DISK" & OV_PID_B=$!
wait "$OV_PID_A" "$OV_PID_B" || true
OV_RC_A=$(cat "$OV_A.rc" 2>/dev/null || echo missing); OV_RC_B=$(cat "$OV_B.rc" 2>/dev/null || echo missing)
OV_OUT_A=$(cat "$OV_A"); OV_OUT_B=$(cat "$OV_B")
rm -f "$OV_A" "$OV_A.rc" "$OV_B" "$OV_B.rc"
record mac s3-05-overlap-host "$(printf 'exit=%s\n%s' "$OV_RC_A" "$OV_OUT_A")"
record mac s3-06-overlap-disk "$(printf 'exit=%s\n%s' "$OV_RC_B" "$OV_OUT_B")"
SHELL4=$(shell_ok srv "$SRV_LOG0")
DISKISH='avail|free|disk|used'
if [ "$OV_RC_A" != 0 ] || [ "$OV_RC_B" != 0 ]; then
  rec s3-overlap FAIL s3-05-overlap-host.txt "exit codes host=$OV_RC_A disk=$OV_RC_B (expected 0 and 0; a shell that never saw its turn end waits out its 300s limit)"
elif ! grep -Fqi -- "$SRV_HOSTNAME" <<<"$OV_OUT_A" || grep -Eqi -- "$DISKISH" <<<"$OV_OUT_A"; then
  rec s3-overlap FAIL s3-05-overlap-host.txt "the hostname shell did not print only its own answer (crossed with the disk request?): $(head -c 160 <<<"$OV_OUT_A" | tr '\n' ' ')"
elif ! grep -Eqi -- "$DISKISH" <<<"$OV_OUT_B" || ! grep -Eq '[0-9]' <<<"$OV_OUT_B"; then
  rec s3-overlap FAIL s3-06-overlap-disk.txt "the disk shell did not print its own answer (crossed with the hostname request?): $(head -c 160 <<<"$OV_OUT_B" | tr '\n' ' ')"
elif [ "${SHELL4:-0}" -lt $((${SHELL3:-0} + 2)) ]; then
  rec s3-overlap FAIL s3-06-overlap-disk.txt "both shells printed sane answers but the server ran its shell fewer than 2 more times ($SHELL3 -> $SHELL4)"
else
  rec s3-overlap PASS s3-06-overlap-disk.txt "both shells exit 0, each printed its own answer; server shell runs $SHELL3 -> $SHELL4"
fi

# ============================================================ 80 columns ====
say "80-column check of the Connect screen and /connect status on both hosts"
width_pass=1; width_note=""
for h in mac srv; do
  tm "$h" resize-window -t "$(sess "$h")" -x 80 -y 40; sleep 4
  before=$(raw "$h" 300)
  cmd "$h" "/connect"; sleep 6
  after=$(screen "$h"); record "$h" "s5-connect-$h-80" "$after" 1
  if ! out=$({ printf '%s\n' "$before"; printf '=====SPLIT=====\n'; printf '%s\n' "$after"; } | python3 "$M1_DIR/width_check.py" 80); then width_pass=0; width_note="$width_note $h Connect: $out;"; fi
  key "$h" Escape; sleep 3
  before=$(raw "$h" 300)
  cmd "$h" "/connect status"; sleep 6
  after=$(raw "$h" 300); record "$h" "s5-status-$h-80" "$after"
  if ! out=$({ printf '%s\n' "$before"; printf '=====SPLIT=====\n'; printf '%s\n' "$after"; } | python3 "$M1_DIR/width_check.py" 80); then width_pass=0; width_note="$width_note $h status: $out;"; fi
  before=$(raw "$h" 300)
  cmd "$h" "/connect help"; sleep 5
  after=$(raw "$h" 300); record "$h" "s5-help-$h-80" "$after"
  if ! out=$({ printf '%s\n' "$before"; printf '=====SPLIT=====\n'; printf '%s\n' "$after"; } | python3 "$M1_DIR/width_check.py" 80); then width_pass=0; width_note="$width_note $h help: $out;"; fi
  tm "$h" resize-window -t "$(sess "$h")" -x 120 -y 40; sleep 3
done
if [ "$width_pass" = 1 ]; then rec s5-width-80 PASS s5-connect-mac-80.txt "no line wider than 80 on either host (s5-*-80.txt)"
else rec s5-width-80 FAIL s5-connect-mac-80.txt "$width_note"; fi

# ============================================================== leak scans ====
say "scanning panes and logs"
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then rec z1-panes-clean PASS - "no join code, 64-hex, relay: address, receipt, or error text on any captured pane"
else rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"; fi
for h in mac srv; do
  if [ "$h" = mac ]; then off=$MAC_LOG0; else off=$SRV_LOG0; fi
  res=$(log_scan "$h" "$off")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "z2-log-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "z2-log-$h" PASS "logscan-$h.txt" "$(kv "$res" size) bytes scanned, clean"
  else
    rec "z2-log-$h" FAIL "logscan-$h.txt" "code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
done
