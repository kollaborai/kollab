#!/usr/bin/env bash
# proof.sh: drive Story 5 of docs/specs/agent-network-simple-flow.md (a stranger
# knocks, is accepted, and reaches only the agent it was allowed) the way a user
# would, on the INSTALLED m1 builds, in tmux on this Mac (s5-mac, plus s5-mac2 for a
# second agent) and on server (s5-srv), 120x40. Evidence lands in
# story5/evidence/.
#
# Two devices, two networks, one directory: the Mac and the server each start a
# network of their own on kollabor.ai (nobody joins anybody). The server knocks the
# Mac's contact route, the Mac accepts and allows ONE of its two agents, and the
# server talks to it with `kollab --hub msg agent@device` from a plain shell. The
# relay on kollabor.ai must already serve POST /relay/v1/contact/links.
#
# Reuses ../m1/env.sh, scan.py, tmuxtype.py (and the wheels/venvs that
# m1/build_wheels.sh and m1/install_both.sh made). The tmux and evidence helpers
# below are the same as m1/proof.sh's; keep them in step.
#
# Exit code 0 only if every row of the final table is PASS. Do not run with
# `bash -x`, and nobody may be attached to the s5-* sessions while it runs.
set -euo pipefail
export KOLLAB_NO_KEYRING=1   # no macOS Keychain dialog from any kollab this script runs
S5_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
M1_DIR_REL=$S5_DIR/../m1
export M1_MAC_SESSION=${M1_MAC_SESSION:-s5-mac} M1_SRV_SESSION=${M1_SRV_SESSION:-s5-srv}
M1_MAC_WS=${S5_MAC_WS:-$HOME/kollab-s5-mac}
M1_SRV_WS_NAME=${S5_SRV_WS_NAME:-kollab-s5-server}
# shellcheck source=../m1/env.sh
. "$M1_DIR_REL/env.sh"
S5_MAC2_SESSION=${S5_MAC2_SESSION:-s5-mac2}
SECOND_AGENT=${S5_SECOND_AGENT:-peridot}   # hub identity of the Mac's second agent
EVID=$S5_DIR/evidence
mkdir -p "$EVID"
: > "$EVID/pane-findings.txt"   # this run only
POLL=3
SCAN=$M1_DIR/scan.py
CODE=""     # Story 5 has no join code; scan.py still wants the (empty) fd 3
LAST=""
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()

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
sess() { case "$1" in mac) printf %s "$M1_MAC_SESSION" ;; mac2) printf %s "$S5_MAC2_SESSION" ;; *) printf %s "$M1_SRV_SESSION" ;; esac; }
tm() { local h=$1; shift; if [ "$h" = srv ]; then m1_ssh "$(printf '%q ' tmux "$@")" </dev/null; else tmux "$@"; fi; }   # %q: '#{...}' must survive the remote shell
raw() { tm "$1" capture-pane -t "$(sess "$1")" -p -J -S "-${2:-500}" || true; }   # screen + history
screen() { tm "$1" capture-pane -t "$(sess "$1")" -p -J || true; }               # visible screen only
key() { local h=$1; shift; tm "$h" send-keys -t "$(sess "$h")" "$@"; }
typ() { # typ <mac|srv> <text> [delay]: one character at a time, text on stdin
  local h=$1 delay=${3:-0.06}
  if [ "$h" = srv ]; then
    printf '%s' "$2" | m1_ssh python3 "$M1_SRV_ROOT/bin/tmuxtype.py" "$(sess srv)" "$delay" >/dev/null
  else
    printf '%s' "$2" | python3 "$M1_DIR/tmuxtype.py" "$(sess "$h")" "$delay" >/dev/null
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
# record <label> <evidence-name> <text>: redact into evidence/<name>.txt, scan the
# raw text for leaks and errors, remember findings for the final row.
record() {
  local label=$1 name=$2 text=$3 out
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
# record_refusal: the text is an EXPECTED refusal, so only leaks (key, relay: address) count.
record_refusal() {
  local label=$1 name=$2 text=$3 out
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text --allow-code 3< <(printf %s "$CODE"))
  if ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out"; then
    PANE_FINDINGS+=("$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
cap() { LAST=$(raw "$1" "${3:-500}"); record "$1" "$2" "$LAST"; }   # cap <host> <name> [history]
capscreen() { LAST=$(screen "$1"); record "$1" "$2" "$LAST"; }

log_path() { # <mac|srv>: kollab.log for that workspace
  local ws home
  if [ "$1" = mac ]; then ws=$M1_MAC_WS; home=$HOME; else ws=$M1_SRV_WS; home=$M1_SRV_HOME; fi
  printf '%s/logs/kollab.log' "$(m1_project_dir "$ws" "$home")"
}
log_size() { if [ "$1" = mac ]; then wc -c < "$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path srv)' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <mac|srv> <offset>: counters on stdout
  if [ "$1" = mac ]; then printf '' | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf '' | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path srv)" "$2"; fi
}
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }   # kv <scan-output> <key>
shell_ok() { kv "$(log_scan "$1" "$2")" shell_ok; }
run_limited() { local secs=$1; shift; perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"; }

# ------------------------------------------------------------ shell-side kollab ----
# Every state check reads `kollab --hub status` / `--hub msg` from a plain shell in
# the workspace: no scrollback, no screen, the same as cron would see.
mac_hub() { (cd "$M1_MAC_WS" && run_limited "${S5_CLI_SECS:-300}" "$M1_MAC_VENV/bin/kollab" --hub "$@" 2>&1); }
srv_hub() { # srv_hub <args...>
  m1_ssh "cd $(printf %q "$M1_SRV_WS") && env KOLLAB_NO_KEYRING=1 perl -e 'alarm shift; exec @ARGV or die' ${S5_CLI_SECS:-300} $(printf %q "$M1_SRV_VENV/bin/kollab") --hub $(printf '%q ' "$@")" 2>&1
}
hub_device() { sed -nE 's/^network: +([^ ]+) +trust:.*/\1/p' <<<"$1" | head -1; }
hub_local() { sed -nE 's/^  ([A-Za-z0-9_-]+)( \*)?  pid=.*/\1/p' <<<"$1" | sort -u; }
hub_remote_names() { sed -nE "s/^  ([A-Za-z0-9_-]+)@$2 - .*/\\1/p" <<<"$1" | sort -u; }
status_line() { # the newest `contact route <domain>/c/<16 hex>` on a pane
  raw "$1" 500 | grep -Eo 'contact route +[A-Za-z0-9.:-]+/c/[0-9a-f]{16}' | tail -1 | awk '{print $3}'
}
approval() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("kollabor",{}).get("permissions",{}).get("approval_mode",""))' "$1" 2>/dev/null || true; }

# =============================================================== preflight ====
say "preflight"
m1_srv_paths
MAC_HOSTNAME=$(uname -n)
[ -x "$M1_MAC_VENV/bin/kollab" ] || abort pre-installed-build "mac venv missing: run m1/install_both.sh"
MAC_V=$(cd "$HOME" && "$M1_MAC_VENV/bin/kollab" --version 2>&1 | tail -1)
SRV_V=$(m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/kollab' --version 2>&1 | tail -1" || true)
case "$MAC_V|$SRV_V" in
  *"$M1_VERSION|"*"$M1_VERSION") rec pre-installed-build PASS - "mac: $MAC_V / srv: $SRV_V" ;;
  *) abort pre-installed-build "expected $M1_VERSION on both, got mac='$MAC_V' srv='$SRV_V'" ;;
esac

# The relay must serve the link route: 400 (bad request) on an empty body means it is
# routed and validating; 404/405/5xx means an older relay, and nothing below would mean anything.
code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' -X POST -H 'content-type: application/json' -d '{}' https://kollabor.ai/relay/v1/contact/links || echo 000)
case "$code" in
  400) rec pre-relay-links PASS - "POST /relay/v1/contact/links answered HTTP 400 (live)" ;;
  *) abort pre-relay-links "POST /relay/v1/contact/links on kollabor.ai answered HTTP $code (want 400). Deploy the relay with cross-room links first." ;;
esac

for h in mac srv; do
  if [ "$h" = mac ]; then ws=$M1_MAC_WS; else ws=$M1_SRV_WS; fi
  py='import hashlib, pathlib, sys; print(hashlib.sha256(str(pathlib.Path(sys.argv[1]).resolve()).encode()).hexdigest())'
  if [ "$h" = mac ]; then dg=$(python3 -c "$py" "$ws"); state=$HOME/.kollab/network/$dg
  else dg=$(m1_ssh "python3 -c '$py' '$ws'"); state=$M1_SRV_HOME/.kollab/network/$dg; fi
  if [ "$h" = mac ]; then present=$([ -e "$state" ] && echo yes || echo no); else present=$(m1_ssh "[ -e '$state' ] && echo yes || echo no"); fi
  [ "$present" = no ] || abort pre-fresh-workspaces "$h workspace $ws already has network state (previous run). Use new workspaces: S5_MAC_WS=... S5_SRV_WS_NAME=... (the script creates them)"
done
rec pre-fresh-workspaces PASS - "no network state for either workspace yet"

[ -s "$HOME/.kollab/oauth/openai.json" ] || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on the Mac (kollab --login)"
m1_ssh "[ -s \"\$HOME/.kollab/oauth/openai.json\" ]" || abort pre-llm-login "no ChatGPT login at ~/.kollab/oauth/openai.json on $M1_HOST (kollab --login there)"
rec pre-llm-login PASS - "both hosts have an openai oauth login; every agent runs --llm openai-oauth"

m1_ssh "mkdir -p '$M1_SRV_ROOT/bin'"
scp -q "${M1_SSH_OPTS[@]}" "$M1_DIR/scan.py" "$M1_DIR/tmuxtype.py" "$M1_HOST:$M1_SRV_ROOT/bin/"
MAC_APPROVAL=$(approval "$HOME/.kollab/config.json")
SRV_APPROVAL=$(m1_ssh "python3 -c 'import json,os; c=json.load(open(os.path.expanduser(\"~/.kollab/config.json\"))); print(c.get(\"kollabor\",{}).get(\"permissions\",{}).get(\"approval_mode\",\"\"))' 2>/dev/null" || true)
MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv)

# ================================================================ launch ====
say "launching the Mac and server TUIs (120x40) in fresh workspaces"
mkdir -p "$M1_MAC_WS"
for s in "$M1_MAC_SESSION" "$S5_MAC2_SESSION"; do tmux kill-session -t "$s" 2>/dev/null || true; done
tmux new-session -d -s "$M1_MAC_SESSION" -x 120 -y 40 "cd '$M1_MAC_WS' && env KOLLAB_NO_KEYRING=1 '$M1_MAC_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh"
m1_ssh "mkdir -p '$M1_SRV_WS'; tmux kill-session -t $M1_SRV_SESSION 2>/dev/null; tmux new-session -d -s $M1_SRV_SESSION -x 120 -y 40 \"cd '$M1_SRV_WS' && env KOLLAB_NO_KEYRING=1 '$M1_SRV_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh\""
wait_ready mac || abort s0-launch "no output from the Mac TUI after 90s"
wait_ready srv || abort s0-launch "no output from the server TUI after 90s"
if [ "$MAC_APPROVAL" != trust_all ]; then say "mac approval mode is '${MAC_APPROVAL:-default}': typing /permissions trust"; cmd mac "/permissions trust"; sleep 2; fi
if [ "$SRV_APPROVAL" != trust_all ]; then say "srv approval mode is '${SRV_APPROVAL:-default}': typing /permissions trust"; cmd srv "/permissions trust"; sleep 2; fi
cap mac s0-01-mac-launch; cap srv s0-02-srv-launch
rec s0-launch PASS s0-01-mac-launch.txt "both TUIs up; see s0-02-srv-launch.txt"

# ============================================ each device starts its own network ====
start_network() { # start_network <mac|srv> <evidence-prefix>: bare /connect, empty code + Enter
  local h=$1 p=$2
  cmd "$h" "/connect"; sleep 5
  if grep -Eq 'network +none' <<<"$(screen "$h")" && grep -Eq 'first device\?|enter privately' <<<"$(screen "$h")"; then
    key "$h" Enter
    wait_screen "$h" 'connected to|joined |could not|enter/esc close|cancelled' 90 || true
    capscreen "$h" "$p"
    key "$h" Escape; sleep 3
  else
    capscreen "$h" "$p"
    key "$h" Escape; sleep 2
  fi
}
say "each device starts a network of its own on kollabor.ai"
start_network mac s1-01-mac-network
start_network srv s1-02-srv-network
sleep 5
MAC_STATUS=$(mac_hub status || true); SRV_STATUS=$(srv_hub status || true)
MAC_DEVICE=$(hub_device "$MAC_STATUS"); SRV_DEVICE=$(hub_device "$SRV_STATUS")
record_refusal mac s1-03-mac-hub-status "$MAC_STATUS"; record_refusal srv s1-04-srv-hub-status "$SRV_STATUS"
if [ -z "$MAC_DEVICE" ] || [ -z "$SRV_DEVICE" ] || [ "$MAC_DEVICE" = "$SRV_DEVICE" ]; then
  abort s1-two-networks "could not read two distinct device names from kollab --hub status (mac='$MAC_DEVICE' srv='$SRV_DEVICE')" s1-03-mac-hub-status.txt
fi
if grep -Eq '\(online\)' <<<"$MAC_STATUS$SRV_STATUS"; then
  abort s1-two-networks "a remote agent is already online on one side: the two devices are not in separate networks" s1-03-mac-hub-status.txt
fi
rec s1-two-networks PASS s1-03-mac-hub-status.txt "mac is '$MAC_DEVICE', server is '$SRV_DEVICE'; neither sees the other's agents"

# ------------------------------------------ a second agent on the Mac, then the agents
say "second agent '$SECOND_AGENT' on the Mac"
tmux new-session -d -s "$S5_MAC2_SESSION" -x 120 -y 40 "cd '$M1_MAC_WS' && env KOLLAB_NO_KEYRING=1 '$M1_MAC_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --as '$SECOND_AGENT' --llm openai-oauth; exec zsh"
wait_ready mac2 || say "second Mac agent printed nothing after 90s"
if [ "$MAC_APPROVAL" != trust_all ]; then cmd mac2 "/permissions trust"; sleep 2; fi
ALLOWED=""; OTHER=""; MAC_LOCALS=""
for _ in $(seq 1 20); do
  MAC_STATUS=$(mac_hub status || true)
  MAC_LOCALS=$(hub_local "$MAC_STATUS")
  if [ "$(grep -c . <<<"$MAC_LOCALS" || true)" -ge 2 ]; then
    for n in $MAC_LOCALS; do if [ "$n" = "$SECOND_AGENT" ]; then OTHER=$n; else ALLOWED=$n; fi; done
    [ -n "$ALLOWED" ] && [ -n "$OTHER" ] && break
  fi
  sleep 5
done
SRV_AGENT=$(hub_local "$SRV_STATUS" | head -1)
if [ -z "$ALLOWED" ] || [ -z "$OTHER" ]; then
  rec s1-mac-has-two-agents FAIL s1-03-mac-hub-status.txt "the Mac shows '$(tr '\n' ' ' <<<"$MAC_LOCALS")' as local agents (want the TUI's agent and '$SECOND_AGENT'), so 'a message to a non-allowed agent' cannot be proven"
  ALLOWED=${ALLOWED:-$(head -1 <<<"$MAC_LOCALS")}; OTHER=${OTHER:-nobody}
else
  rec s1-mac-has-two-agents PASS s1-03-mac-hub-status.txt "allowed candidate '$ALLOWED', non-allowed '$OTHER'; the server's agent is '${SRV_AGENT:-?}'"
fi

# ================================================================ Story 5 ====
say "Story 5: the server knocks the Mac's contact route"
cmd mac "/connect status"; sleep 6
ROUTE=$(status_line mac)
capscreen mac s2-01-mac-status
if [ -z "$ROUTE" ]; then abort s2-mac-contact-route "the Mac's /connect status shows no contact route" s2-01-mac-status.txt; fi
rec s2-mac-contact-route PASS s2-01-mac-status.txt "route $ROUTE"

INTRO="Ana from Acme. Can your ops agent run uname for me?"
b=$(count_pat srv 'knock sent to')
cmd srv "/connect knock $ROUTE \"$INTRO\""
if wait_for srv 'knock sent to' 45 "$b"; then
  cap srv s3-01-srv-knock
  rec s3-server-knocks PASS s3-01-srv-knock.txt "server printed 'knock sent to ...'"
else
  cap srv s3-01-srv-knock-failed
  abort s3-server-knocks "no 'knock sent to' on the server after 45s" s3-01-srv-knock-failed.txt
fi

say "the Mac reviews the knock"
got=0
for _ in $(seq 1 12); do
  cmd mac "/connect knocks"; sleep 6
  if screen mac | grep -Eq '[0-9]+\. +[A-Za-z0-9-]+ +fingerprint'; then got=1; break; fi
  key mac Escape; sleep 5
done
capscreen mac s4-01-mac-knock-review
[ "$got" = 1 ] || abort s4-mac-accepts-knock "the Mac never listed the knock" s4-01-mac-knock-review.txt
KNOCK_NAME=$(screen mac | sed -nE 's/^[ >]*[0-9]+\. +([A-Za-z0-9-]+) +fingerprint.*/\1/p' | head -1)
key mac a
if wait_screen mac 'accepted .*agents trust' 45; then
  capscreen mac s4-02-mac-accepted
  rec s4-mac-accepts-knock PASS s4-02-mac-accepted.txt "accepted '$KNOCK_NAME' (the server is '$SRV_DEVICE')"
else
  capscreen mac s4-02-mac-accept-failed
  abort s4-mac-accepts-knock "no 'accepted ... agents trust' after pressing a" s4-02-mac-accept-failed.txt
fi
key mac Escape; sleep 3

say "the Mac allows one agent"
b=$(count_pat mac 'conversation allowed')
cmd mac "/connect allow $SRV_DEVICE $ALLOWED"
if wait_for mac 'conversation allowed' 30 "$b"; then
  cap mac s5-01-mac-allow
  rec s5-mac-allows-one-agent PASS s5-01-mac-allow.txt "allowed $SRV_DEVICE -> $ALLOWED"
else
  cap mac s5-01-mac-allow-failed
  abort s5-mac-allows-one-agent "no 'conversation allowed' after /connect allow $SRV_DEVICE $ALLOWED" s5-01-mac-allow-failed.txt
fi

say "waiting for the link: each side lists the other's agents (up to 150s)"
seen=0
for _ in $(seq 1 30); do
  SRV_STATUS=$(srv_hub status || true); MAC_STATUS=$(mac_hub status || true)
  srv_sees=$(hub_remote_names "$SRV_STATUS" "$MAC_DEVICE"); mac_sees=$(hub_remote_names "$MAC_STATUS" "$SRV_DEVICE")
  if grep -qx "$ALLOWED" <<<"$srv_sees" && [ -n "$mac_sees" ]; then seen=1; break; fi
  sleep 5
done
record_refusal srv s6-01-srv-hub-status "$SRV_STATUS"; record_refusal mac s6-02-mac-hub-status "$MAC_STATUS"
if [ "$seen" != 1 ]; then
  abort s6-link-and-visibility "after 150s the server sees '${srv_sees:-nothing}' on $MAC_DEVICE and the Mac sees '${mac_sees:-nothing}' on $SRV_DEVICE" s6-01-srv-hub-status.txt
elif grep -qx "$OTHER" <<<"$srv_sees"; then
  rec s6-link-and-visibility FAIL s6-01-srv-hub-status.txt "the server can see '$OTHER@$MAC_DEVICE', which was never allowed"
elif [ "$(wc -l <<<"$srv_sees" | tr -d ' ')" != 1 ]; then
  rec s6-link-and-visibility FAIL s6-01-srv-hub-status.txt "the server sees more than the one allowed agent: $(tr '\n' ' ' <<<"$srv_sees")"
else
  rec s6-link-and-visibility PASS s6-01-srv-hub-status.txt "server sees only $ALLOWED@$MAC_DEVICE; mac sees $(tr '\n' ' ' <<<"$mac_sees")on $SRV_DEVICE"
fi

say "the server messages the allowed agent from a plain shell and waits for the answer"
SHELL0=$(shell_ok mac "$MAC_LOG0")
rc=0; out=$(srv_hub msg "$ALLOWED@$MAC_DEVICE" "run uname -n and reply with only what it prints") || rc=$?
record srv s7-01-allowed-reply "$(printf 'exit=%s\n%s' "$rc" "$out")"
SHELL1=$(shell_ok mac "$MAC_LOG0")
if [ "$rc" -ne 0 ]; then rec s7-delivered-and-answered FAIL s7-01-allowed-reply.txt "exit $rc (want 0): $(head -c 160 <<<"$out" | tr '\n' ' ')"
elif ! grep -Fqi "$MAC_HOSTNAME" <<<"$out"; then rec s7-delivered-and-answered FAIL s7-01-allowed-reply.txt "the reply does not carry the Mac's hostname '$MAC_HOSTNAME': $(head -c 160 <<<"$out" | tr '\n' ' ')"
elif [ "${SHELL1:-0}" -le "${SHELL0:-0}" ]; then rec s7-delivered-and-answered FAIL s7-01-allowed-reply.txt "reply arrived but the Mac log shows no new shell run ($SHELL0 -> $SHELL1)"
else rec s7-delivered-and-answered PASS s7-01-allowed-reply.txt "exit 0, reply carries '$MAC_HOSTNAME', Mac shell runs $SHELL0 -> $SHELL1"; fi

say "the server names the agent nobody allowed"
rc=0; out=$(S5_CLI_SECS=60 srv_hub msg "$OTHER@$MAC_DEVICE" "run uname -n") || rc=$?
record_refusal srv s8-01-other-agent "$(printf 'exit=%s\n%s' "$rc" "$out")"
SHELL2=$(shell_ok mac "$MAC_LOG0")
if [ "$rc" -eq 0 ]; then rec s8-other-agent-unreachable FAIL s8-01-other-agent.txt "exit 0: a message to '$OTHER@$MAC_DEVICE' went through"
elif ! grep -Fq 'unknown agent@device' <<<"$out"; then rec s8-other-agent-unreachable FAIL s8-01-other-agent.txt "exit $rc but the text is not 'unknown agent@device': $(head -c 160 <<<"$out" | tr '\n' ' ')"
elif [ "${SHELL2:-0}" -gt "${SHELL1:-0}" ]; then rec s8-other-agent-unreachable FAIL s8-01-other-agent.txt "the Mac ran a shell after the refused message ($SHELL1 -> $SHELL2)"
else rec s8-other-agent-unreachable PASS s8-01-other-agent.txt "exit $rc, 'unknown agent@device'; the Mac ran nothing"; fi

say "the Mac denies the allowed agent; the next message is refused at once"
b=$(count_pat mac 'grant revoked')
cmd mac "/connect deny $SRV_DEVICE $ALLOWED"
wait_for mac 'grant revoked' 30 "$b" || say "no 'grant revoked' line on the Mac after deny (continuing)"
rc=0; out=$(S5_CLI_SECS=90 srv_hub msg "$ALLOWED@$MAC_DEVICE" "run uname -n") || rc=$?
record_refusal srv s9-01-after-deny "$(printf 'exit=%s\n%s' "$rc" "$out")"
SHELL3=$(shell_ok mac "$MAC_LOG0")
if [ "$rc" -eq 0 ]; then rec s9-deny-refuses FAIL s9-01-after-deny.txt "exit 0 after deny: the message was answered"
elif ! grep -Eq 'no conversation grant|unknown agent@device|did not deliver' <<<"$out"; then rec s9-deny-refuses FAIL s9-01-after-deny.txt "exit $rc but no refusal text: $(head -c 160 <<<"$out" | tr '\n' ' ')"
elif [ "${SHELL3:-0}" -gt "${SHELL2:-0}" ]; then rec s9-deny-refuses FAIL s9-01-after-deny.txt "the Mac ran a shell after deny ($SHELL2 -> $SHELL3)"
else rec s9-deny-refuses PASS s9-01-after-deny.txt "exit $rc: $(head -c 100 <<<"$out" | tr '\n' ' ')"; fi

say "the Mac allows the agent again and it answers, then the Mac revokes the device"
cmd mac "/connect allow $SRV_DEVICE $ALLOWED"; sleep 5
back=0
for _ in $(seq 1 24); do   # the server's roster turns over within about 30s
  SRV_STATUS=$(srv_hub status || true)
  if grep -qx "$ALLOWED" <<<"$(hub_remote_names "$SRV_STATUS" "$MAC_DEVICE")"; then back=1; break; fi
  sleep 5
done
rc=0; out=$(srv_hub msg "$ALLOWED@$MAC_DEVICE" "run uname -n and reply with only what it prints") || rc=$?
record srv s10-01-reallowed-reply "$(printf 'exit=%s\n%s' "$rc" "$out")"
SHELL4=$(shell_ok mac "$MAC_LOG0")
if [ "$back" != 1 ] || [ "$rc" -ne 0 ] || ! grep -Fqi "$MAC_HOSTNAME" <<<"$out" || [ "${SHELL4:-0}" -le "${SHELL3:-0}" ]; then
  rec s10-allow-again-works FAIL s10-01-reallowed-reply.txt "back=$back exit=$rc shell runs $SHELL3 -> $SHELL4: $(head -c 160 <<<"$out" | tr '\n' ' ')"
else
  rec s10-allow-again-works PASS s10-01-reallowed-reply.txt "the agent listed again and answered (shell runs $SHELL3 -> $SHELL4)"
fi

b=$(count_pat mac 'device revoked')
cmd mac "/connect revoke $SRV_DEVICE"
wait_for mac 'device revoked' 30 "$b" || say "no 'device revoked' line on the Mac (continuing)"
gone=0
for _ in $(seq 1 24); do
  SRV_STATUS=$(srv_hub status || true); MAC_STATUS=$(mac_hub status || true)
  srv_sees=$(hub_remote_names "$SRV_STATUS" "$MAC_DEVICE"); mac_sees=$(hub_remote_names "$MAC_STATUS" "$SRV_DEVICE")
  if [ -z "$srv_sees" ] && [ -z "$mac_sees" ]; then gone=1; break; fi
  sleep 5
done
rc=0; out=$(S5_CLI_SECS=60 srv_hub msg "$ALLOWED@$MAC_DEVICE" "run uname -n") || rc=$?
record_refusal srv s11-01-after-revoke "$(printf 'exit=%s\n%s' "$rc" "$out")"
record_refusal srv s11-02-srv-hub-status "$SRV_STATUS"; record_refusal mac s11-03-mac-hub-status "$MAC_STATUS"
SHELL5=$(shell_ok mac "$MAC_LOG0")
if [ "$gone" != 1 ]; then rec s11-revoke-cuts-the-link FAIL s11-02-srv-hub-status.txt "after 120s the server still sees '$srv_sees' or the Mac still sees '$mac_sees'"
elif [ "$rc" -eq 0 ]; then rec s11-revoke-cuts-the-link FAIL s11-01-after-revoke.txt "exit 0 after revoke: the message was answered"
elif ! grep -Fq 'unknown agent@device' <<<"$out"; then rec s11-revoke-cuts-the-link FAIL s11-01-after-revoke.txt "exit $rc but the text is not 'unknown agent@device': $(head -c 160 <<<"$out" | tr '\n' ' ')"
elif [ "${SHELL5:-0}" -gt "${SHELL4:-0}" ]; then rec s11-revoke-cuts-the-link FAIL s11-01-after-revoke.txt "the Mac ran a shell after revoke ($SHELL4 -> $SHELL5)"
else rec s11-revoke-cuts-the-link PASS s11-01-after-revoke.txt "neither side lists the other; the message got 'unknown agent@device'; the Mac ran nothing"; fi

# ============================================================== leak scans ====
say "scanning panes and logs"
cap mac s12-01-mac-pane; cap srv s12-02-srv-pane; cap mac2 s12-03-mac2-pane
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then rec z1-panes-clean PASS - "no 64-hex key, relay: address, join code or error text on any captured pane or CLI output"
else rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"; fi
for h in mac srv; do
  if [ "$h" = mac ]; then off=$MAC_LOG0; else off=$SRV_LOG0; fi
  res=$(log_scan "$h" "$off")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "z2-log-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "z2-log-$h" PASS "logscan-$h.txt" "$(kv "$res" size) bytes scanned, clean"
  else
    rec "z2-log-$h" FAIL "logscan-$h.txt" "hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
done
