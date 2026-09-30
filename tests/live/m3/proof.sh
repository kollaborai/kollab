#!/usr/bin/env bash
# proof.sh: drive section 10 of docs/specs/agent-network-simple-flow.md (the mesh) on the
# INSTALLED m1 builds. A is the Mac (tmux m1-mac), B is the server device (tmux m1-srv, on
# alzan-prod), C is a second device on alzan-prod (tmux m3-c, own workspace, own hub identity)
# with NO relay: it is reachable only through B's loopback TLS endpoint. A must reach C through B.
#
# Order: build_wheels.sh, install_both.sh, m1/proof.sh, (m2/proof.sh), THIS, m3/teardown.sh,
# m1/teardown.sh. Approvals are NOT seeded here: B and C approve each other because both joined
# A's network (step c3-members fails, on purpose, on a build without that). Do not run with
# `bash -x`: the join code lives in the shell variable CODE.
#
# Exit code 0 only if every row of the final table is PASS.
set -euo pipefail
M3_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$M3_DIR/env.sh"
EVID=$M3_DIR/evidence
mkdir -p "$EVID"
: > "$EVID/pane-findings.txt"     # this run only
POLL=3
SCAN=$M1_DIR/scan.py
PROBE=$M3_DIR/probe.py
CODE=""
LAST=""
TOKEN='[A-Za-z0-9_-]+@[A-Za-z0-9_.-]+'
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  printf '\n%-24s %-6s %s\n' STEP RESULT EVIDENCE
  printf '%-24s %-6s %s\n' ---- ------ --------
  for ((i = 0; i < n; i++)); do
    printf '%-24s %-6s %s\n' "${REC_STEP[$i]}" "${REC_STATUS[$i]}" "${REC_EVID[$i]}"
    [ "${REC_STATUS[$i]}" = PASS ] || { fails=$((fails + 1)); printf '    -> %s\n' "${REC_NOTE[$i]}"; }
  done
  if [ "$code" -ne 0 ] && [ "$fails" -eq 0 ]; then
    printf '\nproof.sh stopped early (exit %s) before every step ran. Not a pass.\n' "$code"
    exit "$code"
  fi
  if [ "$fails" -gt 0 ]; then printf '\nFAIL: %d step(s) failed. Not a pass. Run m3/teardown.sh before retrying.\n' "$fails"; exit 1; fi
  printf '\nPASS: %d steps, transcript clean.\n' "$n"
}
trap finish EXIT
abort() { rec "$1" FAIL "${3:--}" "$2"; exit 1; }

# ------------------------------------------------------------ tmux plumbing ----
# Hosts: mac (A), srv (B), c (C). srv and c are both on $M1_HOST.
sess() { case $1 in mac) printf %s "$M1_MAC_SESSION" ;; srv) printf %s "$M1_SRV_SESSION" ;; c) printf %s "$M3_C_SESSION" ;; esac; }
tm() { local h=$1; shift; if [ "$h" = mac ]; then tmux "$@"; else m1_ssh "$(printf '%q ' tmux "$@")" </dev/null; fi; }
raw() { tm "$1" capture-pane -t "$(sess "$1")" -p -J -S "-${2:-500}" || true; }   # screen + history
screen() { tm "$1" capture-pane -t "$(sess "$1")" -p -J || true; }               # visible screen only
key() { local h=$1; shift; tm "$h" send-keys -t "$(sess "$h")" "$@"; }
typ() { # typ <host> <text> [delay]: one character at a time, text on stdin
  local h=$1 delay=${3:-0.06}
  if [ "$h" = mac ]; then
    printf '%s' "$2" | python3 "$M1_DIR/tmuxtype.py" "$(sess mac)" "$delay" >/dev/null
  else
    printf '%s' "$2" | m1_ssh python3 "$M1_SRV_ROOT/bin/tmuxtype.py" "$(sess "$h")" "$delay" >/dev/null
  fi
}
cmd() { typ "$1" "$2"; sleep 1.2; key "$1" Enter; }   # type a slash command and press Enter
count_pat() { raw "$1" 500 | grep -Ec -- "$2" || true; }
wait_for() { # wait_for <host> <ERE> <timeout-s> [baseline]: until count(ERE) in the history exceeds baseline
  local h=$1 re=$2 end base=${4:-0}
  end=$(( $(date +%s) + $3 ))
  while :; do
    [ "$(count_pat "$h" "$re")" -gt "$base" ] && return 0
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
wait_ready() { wait_screen "$1" '[[:alnum:]]' 90 && sleep 15; }      # a TUI is up when its pane has content
newest_with() { raw "$1" 500 | grep -E -- "$2" | tail -1 || true; }
latest_status() { # the lines of the newest /connect status output on <host>
  raw "$1" 300 | awk '/this device/ {buf=""} {buf = buf $0 "\n"} END {printf "%s", buf}'
}
run_limited() { local secs=$1; shift; perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"; }
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }

# ------------------------------------------------------ evidence and scanning ----
record() { # record <label> <evidence-name> <text>: redact into evidence/<name>.txt, scan for leaks and errors
  local label=$1 name=$2 text=$3 out
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") > "$EVID/$name.txt"
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$name")
    { printf '== %s (%s)\n' "$name" "$label"; printf '%s\n' "$out"; } >> "$EVID/pane-findings.txt"
  fi
}
cap() { LAST=$(raw "$1" "${3:-500}"); record "$1" "$2" "$LAST"; }   # cap <host> <name> [history]
capscreen() { LAST=$(screen "$1"); record "$1" "$2" "$LAST"; }
log_path() { # <mac|srv|c>: kollab.log of that device's workspace
  case $1 in
    mac) printf %s "$(m1_project_dir "$M1_MAC_WS" "$HOME")/logs/kollab.log" ;;
    srv) printf %s "$(m1_project_dir "$M1_SRV_WS" "$M1_SRV_HOME")/logs/kollab.log" ;;
    c) printf %s "$(m1_project_dir "$M3_C_WS" "$M1_SRV_HOME")/logs/kollab.log" ;;
  esac
}
log_size() { if [ "$1" = mac ]; then wc -c < "$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path "$1")' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <host> <offset>
  if [ "$1" = mac ]; then printf '' | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf '' | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path "$1")" "$2"; fi
}
srv_probe() { m1_ssh python3 "$M1_SRV_ROOT/bin/m3probe.py" "$@"; }
listening() { # listening <tcp|udp> <port>: is anything bound to it on the server
  m1_ssh "ss -l$([ "$1" = tcp ] && echo t || echo u)nH" | grep -Eq "[:.]$2[[:space:]]"
}
wait_listening() { # wait_listening <tcp|udp> <port> <seconds>
  local end=$(( $(date +%s) + $3 ))
  while :; do
    listening "$1" "$2" && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}
stop_ws() { # stop_ws <session> <workspace on the server>: kill the window and every process whose cwd is the workspace
  m1_ssh bash -s -- "$1" "$2" <<'REMOTE'
session=$1; ws=$2
tmux kill-session -t "$session" 2>/dev/null || true
sleep 2
for _ in 1 2 3 4 5 6 7 8 9 10; do
  pids=""
  for d in /proc/[0-9]*; do
    [ "$(readlink "$d/cwd" 2>/dev/null)" = "$ws" ] && pids="$pids ${d#/proc/}"
  done
  [ -z "$pids" ] && { echo stopped; exit 0; }
  kill -TERM $pids 2>/dev/null || true
  sleep 1
done
kill -KILL $pids 2>/dev/null || true
sleep 1
echo killed
REMOTE
}
launch_srv() { # launch_srv <session> <workspace> [extra kollab flags]: the same launch line as m1/proof.sh
  m1_ssh "tmux kill-session -t $1 2>/dev/null; tmux new-session -d -s $1 -x 120 -y 40 \"cd '$2' && env KOLLAB_NO_KEYRING=1 '$M1_SRV_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} ${3:-} --llm openai-oauth; exec zsh\""
}

# =============================================================== preflight ====
say "preflight"
m3_srv_paths
tmux has-session -t "$M1_MAC_SESSION" 2>/dev/null || abort pre-sessions "no tmux session $M1_MAC_SESSION on the Mac: run m1/proof.sh first and do not run m1/teardown.sh yet"
m1_ssh "tmux has-session -t $M1_SRV_SESSION" 2>/dev/null || abort pre-sessions "no tmux session $M1_SRV_SESSION on $M1_HOST: run m1/proof.sh first and do not run m1/teardown.sh yet"
m1_ssh "tmux has-session -t $M3_C_SESSION" 2>/dev/null && abort pre-clean "tmux session $M3_C_SESSION already exists on $M1_HOST: run m3/teardown.sh first"
m1_ssh "test ! -e '$M3_C_WS' && test ! -e '$M3_TLS'" || abort pre-clean "$M3_C_WS or $M3_TLS already exists on $M1_HOST (an earlier run?): run m3/teardown.sh first"
m1_ssh "ss -ltnH" | grep -Eq "[:.]($M3_B_PORT|$M3_C_PORT)[[:space:]]" && abort pre-ports "TCP $M3_B_PORT or $M3_C_PORT is already in use on $M1_HOST: set M3_B_PORT / M3_C_PORT"
listening udp "$M3_DISCOVERY_PORT" && abort pre-ports "UDP $M3_DISCOVERY_PORT (LAN discovery) is already bound on $M1_HOST"
m1_ssh "command -v openssl >/dev/null && '$M1_SRV_VENV/bin/python' -c 'import certifi'" 2>/dev/null || abort pre-tools "$M1_HOST needs the openssl CLI and certifi in $M1_SRV_VENV"
m1_ssh "mkdir -p '$M1_SRV_ROOT/bin'"
scp -q "${M1_SSH_OPTS[@]}" "$M1_DIR/scan.py" "$M1_DIR/tmuxtype.py" "$M1_HOST:$M1_SRV_ROOT/bin/"
scp -q "${M1_SSH_OPTS[@]}" "$PROBE" "$M1_HOST:$M1_SRV_ROOT/bin/m3probe.py"
key mac Escape; key srv Escape; sleep 1
MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv); C_LOG0=0
rec pre PASS - "sessions $M1_MAC_SESSION and $M1_SRV_SESSION up; ports $M3_B_PORT/$M3_C_PORT and udp $M3_DISCOVERY_PORT free; nothing of an earlier run left"

# ============================================ c1: B listens on its TLS endpoint ====
say "c1: TLS endpoint and LAN discovery on B"
m1_ssh bash -s -- "$M3_TLS" "$M1_SRV_VENV" "$M1_SRV_ROOT" <<'REMOTE' || abort c1-endpoints "could not create the loopback certificate on the server"
set -e
tls=$1; venv=$2; root=$3
umask 077; mkdir -p "$tls"
openssl req -x509 -newkey rsa:2048 -keyout "$tls/key.pem" -out "$tls/cert.pem" -days 1 -nodes -subj /CN=127.0.0.1 -addext subjectAltName=IP:127.0.0.1 2>/dev/null
"$venv/bin/python" "$root/bin/m3probe.py" ca "$tls/cert.pem" "$tls/ca.pem" "$("$venv/bin/python" -c 'import certifi; print(certifi.where())')"
REMOTE
srv_probe config "$M1_SRV_WS" "$M3_B_PORT" "$M3_TLS/cert.pem" "$M3_TLS/key.pem" "$M3_TLS/ca.pem" >/dev/null
stop_ws "$M1_SRV_SESSION" "$M1_SRV_WS" >/dev/null
launch_srv "$M1_SRV_SESSION" "$M1_SRV_WS"
wait_ready srv || abort c1-endpoints "B's TUI showed nothing 90s after the restart"
if wait_listening tcp "$M3_B_PORT" 90 && wait_listening udp "$M3_DISCOVERY_PORT" 30; then
  cap srv c1-01-b-restarted
  rec c1-endpoints PASS c1-01-b-restarted.txt "B restarted; TLS endpoint on 127.0.0.1:$M3_B_PORT and LAN discovery (udp $M3_DISCOVERY_PORT) are bound"
else
  cap srv c1-01-b-not-listening
  abort c1-endpoints "B is not listening on tcp $M3_B_PORT / udp $M3_DISCOVERY_PORT 90s after the restart" c1-01-b-not-listening.txt
fi

# ============================================== c2: C joins A's network by code ====
say "c2: C joins A's network"
m1_ssh "mkdir -p '$M3_C_WS'"
srv_probe config "$M3_C_WS" "$M3_C_PORT" "$M3_TLS/cert.pem" "$M3_TLS/key.pem" "$M3_TLS/ca.pem" >/dev/null
NET0=$(srv_probe netdirs)
launch_srv "$M3_C_SESSION" "$M3_C_WS" "--as $M3_C_AS"
wait_ready c || abort c2-c-joins "C's TUI showed nothing 90s after launch"
MAC_BASE=$(raw mac 500)
cmd mac "/connect code"
found=0; pressed=0
for _ in $(seq 1 30); do
  sleep 3
  snap=$(screen mac)
  CODE=$(printf '%s\n' "$snap" | python3 "$SCAN" findcode 4< <(printf %s "$MAC_BASE")) || true
  [ -n "$CODE" ] && { found=1; break; }
  if [ "$pressed" = 0 ] && grep -Eq 'Enter: create code' <<<"$snap"; then key mac Enter; pressed=1; fi
done
[ "$found" = 1 ] || abort c2-c-joins "no join code appeared on the Mac after /connect code"
key mac Escape; sleep 1
b_acc=$(count_pat mac 'accepted|trusted device')
cmd c "/connect"; sleep 5
snap=$(screen c)
grep -Eqi '> *domain' <<<"$snap" && { key c Tab; sleep 1; }
typ c "$CODE" 0.08
sleep 1
capscreen c c2-01-c-code-typed        # must show the masked field, never the code
key c Enter
sleep 4
# /connect code is a code-only screen (no request rows, no accept key) and closing it prints nothing later: a request
# only shows up in `/connect status` (or the full Connect screen). Ask for it, the way m1's fallback does, for 2 minutes.
got=0
for _ in $(seq 1 12); do
  b_wants=$(count_pat mac 'wants to join'); cmd mac "/connect status"
  wait_for mac 'wants to join' 10 "$b_wants" && { got=1; break; }
done
[ "$got" = 1 ] || { cap mac c2-02-mac-no-request; abort c2-c-joins "no 'wants to join' in the Mac's /connect status 2 minutes after C submitted the code" c2-02-mac-no-request.txt; }
C_REQ=$(newest_with mac '[^[:space:]]+[[:space:]]+wants to join' | sed -E 's/.*[[:space:]]([^[:space:]]+)[[:space:]]+wants to join.*/\1/')
[ -n "$C_REQ" ] || abort c2-c-joins "could not read C's device name from the Mac's 'wants to join' line"
cmd mac "/connect accept $C_REQ"
wait_for mac 'accepted|trusted device' 60 "$b_acc" || { cap mac c2-03-mac-accept-failed; abort c2-c-joins "no 'accepted' after /connect accept $C_REQ" c2-03-mac-accept-failed.txt; }
wait_for c 'joined ' 90 || { cap c c2-04-c-not-joined; abort c2-c-joins "C never printed a 'joined ...' line" c2-04-c-not-joined.txt; }
tm mac clear-history -t "$M1_MAC_SESSION" || true      # the join code is off the Mac's screen and scrollback from here on
cap c c2-04-c-joined
NET1=$(srv_probe netdirs)
C_STATE_DIR=$(comm -13 <(printf '%s\n' "$NET0" | sort) <(printf '%s\n' "$NET1" | sort))
[ "$(printf '%s\n' "$C_STATE_DIR" | grep -c .)" = 1 ] || abort c2-c-joins "expected one new ~/.kollab/network/<digest> after C joined, saw: $(printf '%s' "$C_STATE_DIR" | tr '\n' ' ')" c2-04-c-joined.txt
rec c2-c-joins PASS c2-04-c-joined.txt "C ($C_REQ, hub identity $M3_C_AS) joined A's network; state dir $C_STATE_DIR"

# ======================== c3: B and C are members of each other's network without any seeding ====
say "c3: B and C list each other"
C_DEVICE=""; B_DEVICE=""; B_SEES=""; C_SEES=""
for _ in $(seq 1 10); do
  cmd srv "/connect status"; cmd c "/connect status"; sleep 7
  B_ST=$(latest_status srv); C_ST=$(latest_status c)
  B_DEVICE=$(sed -nE 's/.*this device[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$B_ST" | tail -1)
  C_DEVICE=$(sed -nE 's/.*this device[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$C_ST" | tail -1)
  if [ -n "$B_DEVICE" ] && [ -n "$C_DEVICE" ]; then
    B_SEES=$(grep -oE "$TOKEN" <<<"$B_ST" | grep -F "@$C_DEVICE" | head -1 || true)
    C_SEES=$(grep -oE "$TOKEN" <<<"$C_ST" | grep -F "@$B_DEVICE" | head -1 || true)
    [ -n "$B_SEES" ] && [ -n "$C_SEES" ] && break
  fi
  sleep 8
done
cap srv c3-01-b-status; cap c c3-02-c-status
if [ -n "$B_SEES" ] && [ -n "$C_SEES" ]; then
  rec c3-members PASS c3-01-b-status.txt "B lists $B_SEES and C lists $C_SEES with no hand-made approvals"
else
  abort c3-members "B sees '${B_SEES:-nothing}' and C sees '${C_SEES:-nothing}' as agent@device. B and C must approve each other on their own after joining the same network (every member approves every other member). Do NOT seed approvals by hand: fix the build" c3-01-b-status.txt
fi
C_HANDLE=$B_SEES

# ==================================================== c4: C runs without the relay ====
say "c4: take C off the relay"
stop_ws "$M3_C_SESSION" "$M3_C_WS" >/dev/null
gone=0
for _ in $(seq 1 12); do
  cmd mac "/connect status"; sleep 7
  if ! grep -qF "$C_HANDLE" <<<"$(latest_status mac)"; then gone=1; break; fi
done
[ "$gone" = 1 ] || abort c4-c-relayless "A still listed $C_HANDLE 84s after C was stopped, so a later listing would prove nothing"
srv_probe relayless "$M1_SRV_HOME/.kollab/network/$C_STATE_DIR" > "$EVID/c4-01-relayless.txt"
launch_srv "$M3_C_SESSION" "$M3_C_WS" "--as $M3_C_AS"
wait_ready c || abort c4-c-relayless "C's TUI showed nothing 90s after the restart"
cmd c "/connect status"; sleep 7
C_ST=$(latest_status c)
cap c c4-02-c-relayless
if grep -q 'via kollabor\.ai' <<<"$C_ST"; then
  abort c4-c-relayless "C still shows a relay connection ('via kollabor.ai') after enabled=false" c4-02-c-relayless.txt
elif wait_listening tcp "$M3_C_PORT" 60; then
  rec c4-c-relayless PASS c4-02-c-relayless.txt "C restarted with enabled=false: no relay connection on its status; TLS endpoint on 127.0.0.1:$M3_C_PORT"
else
  abort c4-c-relayless "C is not listening on tcp $M3_C_PORT after the restart" c4-02-c-relayless.txt
fi

# ================================== c5: A learns C through B (the success signal) ====
say "c5: A lists C through B"
T0=$(date +%s); seen=""
for _ in $(seq 1 24); do
  cmd mac "/connect status"; sleep 7
  A_ST=$(latest_status mac)
  seen=$(grep -oE "$TOKEN" <<<"$A_ST" | grep -F "@$C_DEVICE" | head -1 || true)
  [ -n "$seen" ] && break
  sleep 3
done
cap mac c5-01-a-status
if [ -n "$seen" ]; then rec c5-a-sees-c PASS c5-01-a-status.txt "A lists $seen $(( $(date +%s) - T0 ))s after C came back without a relay"
else abort c5-a-sees-c "A never listed an agent on $C_DEVICE within 4 minutes of C's restart" c5-01-a-status.txt; fi

# ================================================= c6: A -> B -> C, sealed end to end ====
say "c6: a message from A reaches C's agent through B"
MARK=$(python3 -c 'import secrets; print(secrets.token_hex(6))')
REQ="m3req$MARK"; REP="m3rep$MARK"
ASK="run uname -n and answer with your hostname and the exact word $REP. this request carries the marker $REQ"
rc=0
out=$(cd "$M1_MAC_WS" && run_limited 300 env KOLLAB_NO_KEYRING=1 "$M1_MAC_VENV/bin/kollab" --hub msg "$C_HANDLE" "$ASK" 2>&1) || rc=$?
record mac c6-01-hub-msg "$(printf 'exit=%s\n%s' "$rc" "$out")"
if [ "$rc" -ne 0 ]; then rec c6-message FAIL c6-01-hub-msg.txt "kollab --hub msg exited $rc (expected 0)"
elif ! grep -qF -- "$REP" <<<"$out"; then rec c6-message FAIL c6-01-hub-msg.txt "the printed reply does not carry the reply word (no answer from C, or a stale one): $(head -c 160 <<<"$out" | tr '\n' ' ')"
else rec c6-message PASS c6-01-hub-msg.txt "exit 0 and C's reply came back through B"; fi

say "c7: B carried it and never read it"
hits=""
for tok in "$REQ" "$REP"; do
  n=$(raw srv 3000 | grep -cF -- "$tok" || true); [ "${n:-0}" = 0 ] || hits="$hits b-pane:$n"
  n=$(m1_ssh "grep -cF -- '$tok' '$(log_path srv)' 2>/dev/null || true"); [ "${n:-0}" = 0 ] || hits="$hits b-log:$n"
done
delivered=$(raw c 3000 | grep -cF -- "$REQ" || true)
if [ -n "$hits" ]; then rec c7-sealed FAIL - "the markers appear on B:$hits"
elif [ "${delivered:-0}" -eq 0 ]; then rec c7-sealed FAIL - "the request marker never showed on C's pane, so B's silence proves nothing"
else rec c7-sealed PASS - "request and reply markers are in no line of B's pane history (3000) or kollab.log; the request showed on C"; fi

# ============================================ c8: the run used the shipped defaults ====
say "c8: neither peer key is set anywhere"
KEYS=$( { python3 "$PROBE" hubkeys "$HOME/.kollab/config.json" "$M1_MAC_WS/.kollab/config.json"
          srv_probe hubkeys "$M1_SRV_HOME/.kollab/config.json" "$M1_SRV_WS/.kollab/config.json" "$M3_C_WS/.kollab/config.json"; } )
printf '%s\n' "$KEYS" > "$EVID/c8-01-hubkeys.txt"
if [ "$(grep -c '=-$' <<<"$KEYS")" = "$(grep -c . <<<"$KEYS")" ]; then rec c8-defaults-on PASS c8-01-hubkeys.txt "peer_direct_enabled and peer_forward_enabled are unset in every config: A, B and C ran on the defaults, and the route worked"
else rec c8-defaults-on FAIL c8-01-hubkeys.txt "a config sets a peer key: $(grep -v '=-$' <<<"$KEYS" | head -2 | tr '\n' ' ')"; fi

# ============================================================== leak scans ====
say "scanning panes and logs"
cap mac z-mac-final 250; cap srv z-b-final 250; cap c z-c-final 250
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then rec z1-panes-clean PASS - "no join code, 64-hex string, relay: address or error text on the panes captured in this run"
else rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"; fi
for h in mac srv c; do
  case $h in mac) off=$MAC_LOG0 ;; srv) off=$SRV_LOG0 ;; c) off=$C_LOG0 ;; esac
  res=$(log_scan "$h" "$off")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "z2-log-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "z2-log-$h" PASS "logscan-$h.txt" "$(kv "$res" size) bytes scanned since the start of this run, clean"
  else
    rec "z2-log-$h" FAIL "logscan-$h.txt" "hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
done
