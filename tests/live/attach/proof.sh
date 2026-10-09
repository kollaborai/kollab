#!/usr/bin/env bash
# proof.sh: Story 10 of docs/specs/agent-network-simple-flow.md, opening an agent on another
# device from the web UI's engine, on the INSTALLED m1 builds. Run it after m1/proof.sh left the
# Mac (tmux m1-mac) and the server (tmux m1-srv) joined, before m1/teardown.sh. It starts an
# engine on the Mac in the Mac workspace, opens the server's koordinator through the network and
# runs one paid turn there. It saves ~/.kollab/engine.token and puts it back at the end.
#
# Exit code 0 only if every row of the final table is PASS.
set -euo pipefail
AT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$AT_DIR/../m1/env.sh"
m1_srv_paths
EVID=$AT_DIR/evidence
mkdir -p "$EVID"
: > "$EVID/pane-findings.txt"
POLL=3
SCAN=$M1_DIR/scan.py
CODE=""
LAST=""
PORT=${AT_ENGINE_PORT:-7439}
TOKEN_FILE=$HOME/.kollab/engine.token
TOKEN_SAVED=$(cat "$TOKEN_FILE" 2>/dev/null || true)
SID=""
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()

E() { python3 "$AT_DIR/engine.py" "$@"; }

cleanup() {
  [ -n "$SID" ] && E delete "$PORT" "$SID" >/dev/null 2>&1 || true
  pkill -f "kollabor_engine serve --port $PORT" 2>/dev/null || true
  if [ -n "$TOKEN_SAVED" ]; then printf '%s' "$TOKEN_SAVED" > "$TOKEN_FILE"; chmod 600 "$TOKEN_FILE"; fi
}

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  cleanup
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
  if [ "$fails" -gt 0 ]; then printf '\nFAIL: %d step(s) failed. Not a pass.\n' "$fails"; exit 1; fi
  printf '\nPASS: %d steps, transcript clean.\n' "$n"
}
trap finish EXIT
abort() { rec "$1" FAIL "${3:--}" "$2"; exit 1; }

# ------------------------------------------------------------ tmux plumbing ----
sess() { case $1 in mac) printf %s "$M1_MAC_SESSION" ;; srv) printf %s "$M1_SRV_SESSION" ;; esac; }
tm() { local h=$1; shift; if [ "$h" = mac ]; then tmux "$@"; else m1_ssh "$(printf '%q ' tmux "$@")" </dev/null; fi; }
raw() { tm "$1" capture-pane -t "$(sess "$1")" -p -J -S "-${2:-500}" || true; }
key() { local h=$1; shift; tm "$h" send-keys -t "$(sess "$h")" "$@"; }
typ() { # typ <host> <text> [delay]: one character at a time, text on stdin
  local h=$1 delay=${3:-0.06}
  if [ "$h" = mac ]; then
    printf '%s' "$2" | python3 "$M1_DIR/tmuxtype.py" "$(sess mac)" "$delay" >/dev/null
  else
    printf '%s' "$2" | m1_ssh python3 "$M1_SRV_ROOT/bin/tmuxtype.py" "$(sess "$h")" "$delay" >/dev/null
  fi
}
cmd() { typ "$1" "$2"; sleep 1.2; key "$1" Enter; }
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
cap() { LAST=$(raw "$1" "${3:-500}"); record "$1" "$2" "$LAST"; }
log_path() {
  case $1 in
    mac) printf %s "$(m1_project_dir "$M1_MAC_WS" "$HOME")/logs/kollab.log" ;;
    srv) printf %s "$(m1_project_dir "$M1_SRV_WS" "$M1_SRV_HOME")/logs/kollab.log" ;;
  esac
}
log_size() { if [ "$1" = mac ]; then wc -c < "$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path "$1")' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <host> <offset>
  if [ "$1" = mac ]; then printf '' | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf '' | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path "$1")" "$2"; fi
}

# =============================================================== preflight ====
say "preflight"
tmux has-session -t "$M1_MAC_SESSION" 2>/dev/null && m1_ssh tmux has-session -t "$M1_SRV_SESSION" 2>/dev/null \
  || abort pre "run m1/proof.sh first: both TUIs must be up and joined"
SRV_HOSTNAME=$(m1_ssh uname -n)
MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv)
rec pre PASS - "both m1 TUIs are up"

# ================================================================== engine ====
say "a0: an engine on the Mac, in the Mac workspace (port $PORT)"
(cd "$M1_MAC_WS" && nohup env KOLLAB_NO_KEYRING=1 "$M1_MAC_VENV/bin/python" -m kollabor_engine serve --port "$PORT" \
  > "$EVID/engine.log" 2>&1 &)
E health "$PORT" 60 || abort a0-engine "the engine did not answer /health"
SID=$(E create "$PORT" openai-oauth "$M1_MAC_WS") || abort a0-engine "POST /sessions failed (engine.log)" engine.log
REMOTE=$(E remote "$PORT" koordinator 120) || abort a0-engine "no koordinator on another computer in GET /sessions network.remote"
MAC_DEVICE=$(E device "$PORT") || abort a0-engine "GET /sessions named no device"
rec a0-engine PASS engine.log "a chat runs here; the server's koordinator is listed"

# ===================================================== refused, then allowed ====
say "a1: the server has not allowed this computer yet"
OUT=$(E history "$PORT" "$REMOTE")
case $OUT in
  "503 "*"has not let $MAC_DEVICE open its agents"*"/connect attach allow $MAC_DEVICE"*)
    rec a1-refused PASS - "503 with the command to run on the server" ;;
  *) rec a1-refused FAIL - "expected 503 naming /connect attach allow $MAC_DEVICE, got: ${OUT:0:200}" ;;
esac

say "a2: /connect attach allow on the server"
BASE=$(count_pat srv 'may now open this computer')
cmd srv "/connect attach allow $MAC_DEVICE"
if wait_for srv 'may now open this computer' 30 "$BASE"; then rec a2-allow PASS - "the server allowed the Mac"
else cap srv a2-srv; rec a2-allow FAIL a2-srv.txt "no 'may now open' line on the server"; fi

# ============================================================ full history ====
say "a3: the server agent's own history, through the network"
OUT=$(E history "$PORT" "$REMOTE")
DUMP=$(E dump "$PORT" "$REMOTE")
record mac a3-history "$DUMP"
case $OUT in
  "200 "*)
    if [ "${OUT#200 }" -gt 0 ] && grep -qi 'uname' <<<"$DUMP"; then
      rec a3-history PASS a3-history.txt "${OUT#200 } messages, including m1 Story 2's uname request"
    else rec a3-history FAIL a3-history.txt "history came back without m1 Story 2's uname request"; fi ;;
  *) rec a3-history FAIL - "expected 200, got: ${OUT:0:200}" ;;
esac
if wait_for srv "$MAC_DEVICE opened koordinator from the network" 20; then rec a4-notice PASS - "the server's screen says who opened it"
else cap srv a4-srv; rec a4-notice FAIL a4-srv.txt "no 'opened koordinator from the network' line on the server"; fi

# ========================================================== one paid turn ====
say "a5: a turn on the server agent, typed on the Mac"
SENT=$(E send "$PORT" "$REMOTE" "Run uname -n in your shell and reply with only its output." || true)
if [ "$SENT" != 200 ] && [ "$SENT" != 202 ]; then rec a5-turn FAIL - "POST message answered ${SENT:-nothing}"
elif REPLY=$(E reply "$PORT" "$REMOTE" "$SRV_HOSTNAME" 300); then
  record mac a5-reply "$REPLY"; rec a5-turn PASS a5-reply.txt "the server agent answered with its hostname"
else rec a5-turn FAIL - "no assistant reply with the server's hostname in 300s"; fi

# ============================================================ deny closes ====
say "a6: /connect attach deny closes it"
BASE=$(count_pat srv 'may no longer open')
cmd srv "/connect attach deny $MAC_DEVICE"
if wait_for srv 'may no longer open this computer' 30 "$BASE" \
  && wait_for srv "$MAC_DEVICE closed koordinator" 30 \
  && E gone "$PORT" "$REMOTE" 60 >/dev/null; then
  rec a6-deny PASS - "the open agent closed on both computers"
else cap srv a6-srv; rec a6-deny FAIL a6-srv.txt "deny did not close the open agent"; fi
OUT=$(E history "$PORT" "$REMOTE")
case $OUT in
  "503 "*"has not let $MAC_DEVICE open its agents"*) rec a7-refused-again PASS - "refused again after deny" ;;
  *) rec a7-refused-again FAIL - "expected 503 after deny, got: ${OUT:0:200}" ;;
esac

# ============================================================== leak scans ====
say "scanning panes and logs"
cap mac z-mac; cap srv z-srv
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then rec z1-panes-clean PASS - "no join code, 64-hex, relay: address, receipt, or error text"
else rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"; fi
for h in mac srv; do
  if [ "$h" = mac ]; then off=$MAC_LOG0; else off=$SRV_LOG0; fi
  res=$(log_scan "$h" "$off")
  printf '%s\n' "$res" > "$EVID/logscan-$h.txt"
  if [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "z2-log-$h" PASS "logscan-$h.txt" "clean"
  else rec "z2-log-$h" FAIL "logscan-$h.txt" "code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"; fi
done
if grep -Eq 'Traceback|ERROR' "$EVID/engine.log"; then rec z3-engine-log FAIL engine.log "errors in the engine log"
else rec z3-engine-log PASS engine.log "clean"; fi
