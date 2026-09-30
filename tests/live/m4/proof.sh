#!/usr/bin/env bash
# proof.sh: Story 6 of docs/specs/agent-network-simple-flow.md on the INSTALLED m1 builds, in tmux on this Mac
# (m4-mac) and on alzan-prod (m4-srv), 120x40, against the self-hosted directory https://selfhost.kollabor.ai
# that `kollab relay serve` runs (serve_up.sh up, edge_vhost.sh apply and verify_serve.sh come first).
#
#   1. the Mac starts a network on the company directory:      /connect selfhost.kollabor.ai
#   2. the Mac shows a join code; the server types that domain and the code into the private form
#   3. the Mac accepts; both sides list the other's agent@device
#   4. the two exchange a message:                             kollab --hub msg agent@device "..."
#   5. nothing touched kollabor.ai (screens and logs)
#   6. the one command is stopped and started again: same publisher key, both devices come back, and they
#      exchange another message
#
# The join code lives only in the shell variable CODE: read out of a pane capture, typed into the other pane
# through a pipe, compared inside python, replaced by "[join code]" in every evidence file. Never echoed,
# written to disk or placed on a command line (tmux only ever sees one character per argv). Do not run with
# `bash -x`. Exit code 0 only if every row of the final table is PASS. Evidence: m4/evidence/.
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
EVID=$M4_DIR/evidence
mkdir -p "$EVID"
: >"$EVID/pane-findings.txt"
POLL=3
SCAN=$M1_DIR/scan.py
CODE=""
LAST=""
REC_STEP=()
REC_STATUS=()
REC_EVID=()
REC_NOTE=()
PANE_FINDINGS=()

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1")
  REC_STATUS+=("$2")
  REC_EVID+=("$3")
  REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  printf '\n%-34s %-6s %s\n' STEP RESULT EVIDENCE
  printf '%-34s %-6s %s\n' ---- ------ --------
  for ((i = 0; i < n; i++)); do
    printf '%-34s %-6s %s\n' "${REC_STEP[$i]}" "${REC_STATUS[$i]}" "${REC_EVID[$i]}"
    [ "${REC_STATUS[$i]}" = PASS ] || {
      fails=$((fails + 1))
      printf '    -> %s\n' "${REC_NOTE[$i]}"
    }
  done
  if [ "$code" -ne 0 ] && [ "$fails" -eq 0 ]; then
    printf '\nproof.sh stopped early (exit %s) before every step ran. Not a pass.\n' "$code"
    exit "$code"
  fi
  if [ "$fails" -gt 0 ]; then
    printf '\nFAIL: %d step(s) failed. Not a pass.\n' "$fails"
    exit 1
  fi
  printf '\nPASS: %d steps, transcript clean.\n' "$n"
}
trap finish EXIT
abort() { rec "$1" FAIL "${3:--}" "$2"; exit 1; }

# ------------------------------------------------------------ tmux plumbing ----
sess() { if [ "$1" = mac ]; then printf %s "$M4_MAC_SESSION"; else printf %s "$M4_SRV_SESSION"; fi; }
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
  end=$(($(date +%s) + $3))
  while :; do
    n=$(count_pat "$h" "$re")
    [ "$n" -gt "$base" ] && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}
wait_ready() { # a TUI is up when its pane has content; then give it time to settle
  local h=$1 end n
  end=$(($(date +%s) + 90))
  while :; do
    n=$(screen "$h" | grep -c '[^[:space:]]' || true)
    [ "$n" -ge 5 ] && break
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep 2
  done
  sleep 10
}

# ------------------------------------------------------ evidence and scanning ----
# record <label> <evidence-name> <text> [allow-code]: redact into evidence/<name>.txt, scan the raw text for
# leaks and errors, remember findings for the final row.
record() {
  local label=$1 name=$2 text=$3 allow=${4:-0} out flag=""
  [ "$allow" = 1 ] && flag="--allow-code"
  printf '%s\n' "$text" | python3 "$SCAN" redact 3< <(printf %s "$CODE") >"$EVID/$name.txt"
  # shellcheck disable=SC2086
  out=$(printf '%s\n' "$text" | python3 "$SCAN" text $flag 3< <(printf %s "$CODE"))
  if ! grep -qx 'code=0' <<<"$out" || ! grep -qx 'hex64=0' <<<"$out" || ! grep -qx 'relay=0' <<<"$out" || ! grep -qx 'errhits=0' <<<"$out"; then
    PANE_FINDINGS+=("$name")
    {
      printf '== %s (%s)\n' "$name" "$label"
      printf '%s\n' "$out"
    } >>"$EVID/pane-findings.txt"
  fi
}
cap() { LAST=$(raw "$1" "${4:-500}"); record "$1" "$2" "$LAST" "${3:-0}"; }   # cap <host> <name> [allow-code] [history]
capscreen() { LAST=$(screen "$1"); record "$1" "$2" "$LAST" "${3:-0}"; }

log_path() { # <mac|srv>: kollab.log for that workspace
  local ws home
  if [ "$1" = mac ]; then ws=$M4_MAC_WS; home=$HOME; else ws=$M4_SRV_WS; home=$M1_SRV_HOME; fi
  printf '%s/logs/kollab.log' "$(m1_project_dir "$ws" "$home")"
}
log_size() { if [ "$1" = mac ]; then wc -c <"$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path srv)' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <mac|srv> <offset>: counters on stdout, code from CODE via stdin
  if [ "$1" = mac ]; then
    printf %s "$CODE" | python3 "$SCAN" file "$(log_path mac)" "$2"
  else
    printf %s "$CODE" | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path srv)" "$2"
  fi
}
log_window_scan() { # log_window_scan <mac|srv> <start> <end>: scan.py counters for the log bytes [start, end)
  local tmp
  if [ "$1" = mac ]; then
    tmp=$(mktemp)
    tail -c +$(($2 + 1)) "$(log_path mac)" 2>/dev/null | head -c $(($3 - $2)) >"$tmp" || true   # tail may get SIGPIPE
    printf %s "$CODE" | python3 "$SCAN" file "$tmp" 0
    rm -f "$tmp"
  else
    printf %s "$CODE" | m1_ssh "T=\$(mktemp) && tail -c +$(($2 + 1)) '$(log_path srv)' 2>/dev/null | head -c $(($3 - $2)) >\"\$T\" && python3 '$M1_SRV_ROOT/bin/scan.py' file \"\$T\" 0; rc=\$?; rm -f \"\$T\"; exit \$rc"
  fi
}
log_count() { # log_count <mac|srv> <offset> <ERE>: matching lines in the log after the offset
  if [ "$1" = mac ]; then
    tail -c +$(($2 + 1)) "$(log_path mac)" 2>/dev/null | grep -Ec -- "$3" || true
  else
    m1_ssh "tail -c +$(($2 + 1)) '$(log_path srv)' 2>/dev/null | grep -Ec -- '$3' || true"
  fi
}
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }   # kv <scan-output> <key>
shell_ok() { kv "$(log_scan "$1" "$2")" shell_ok; }
run_limited() { local secs=$1; shift; perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"; }
newest_with() { raw "$1" 500 | grep -E -- "$2" | tail -1 || true; }

# What the directory's key file says right now: "<publisher key> <revision>". Retries: the edge answers 502
# for a few seconds while the command behind it restarts.
manifest_key() {
  local i out
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if out=$(curl -sS -m 20 "https://$M4_DOMAIN/.well-known/agent-keys.json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["coordinator"]["public_key"], d["revision"])' 2>/dev/null); then
      printf '%s' "$out"
      return 0
    fi
    sleep 3
  done
  return 1
}
serve_pane() { m1_ssh "tmux capture-pane -p -J -S -300 -t $M4_SERVE_SESSION" </dev/null || true; }
# The [r] keeps pgrep from matching the remote shell that carries this very pattern.
serve_pid() { m1_ssh "pgrep -f '[r]elay serve --domain $M4_DOMAIN' | head -1" </dev/null || true; }

# =============================================================== preflight ====
say "preflight"
m4_srv_paths
[ -x "$M1_MAC_VENV/bin/kollab" ] || abort pre-installed-build "mac venv missing: run m1/install_both.sh"
MAC_V=$(cd "$HOME" && "$M1_MAC_VENV/bin/kollab" --version 2>&1 | tail -1)
SRV_V=$(m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/kollab' --version 2>&1 | tail -1" || true)
case "$MAC_V|$SRV_V" in
  *"$M1_VERSION|"*"$M1_VERSION") rec pre-installed-build PASS - "mac: $MAC_V / srv: $SRV_V" ;;
  *) abort pre-installed-build "expected $M1_VERSION on both, got mac='$MAC_V' srv='$SRV_V'" ;;
esac

SERVE_PID0=$(serve_pid)
[ -n "$SERVE_PID0" ] || abort pre-one-command "no 'kollab relay serve --domain $M4_DOMAIN' process on $M1_HOST: run serve_up.sh up"
# The same finder serve_up.sh used: the manual relay, publisher and static server, by what they are configured to do.
manual_left=$(m1_ssh python3 - "$M4_DOMAIN" <"$M4_DIR/inspect_old.py" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(k for k in ("relay", "publisher", "static") if d[k]))') || abort pre-one-command "could not look for the manual stack on $M1_HOST"
[ -z "$manual_left" ] || abort pre-one-command "the manual stack still runs next to the one command ($manual_left): run serve_up.sh up"
rec pre-one-command PASS - "pid $SERVE_PID0 on $M1_HOST; none of the three manual processes runs"

if bash "$M4_DIR/verify_serve.sh" >"$EVID/pre-verify.log" 2>&1; then
  rec pre-public-directory PASS pre-verify.log "$(grep -c '^PASS' "$EVID/pre-verify.log") checks passed on https://$M4_DOMAIN"
else
  abort pre-public-directory "verify_serve.sh failed; see evidence/pre-verify.log" pre-verify.log
fi

for h in mac srv; do
  if [ "$h" = mac ]; then ws=$M4_MAC_WS; else ws=$M4_SRV_WS; fi
  py='import hashlib, pathlib, sys; print(hashlib.sha256(str(pathlib.Path(sys.argv[1]).resolve()).encode()).hexdigest())'
  if [ "$h" = mac ]; then
    dg=$(python3 -c "$py" "$ws")
    state=$HOME/.kollab/network/$dg
    present=$([ -e "$state" ] && echo yes || echo no)
  else
    dg=$(m1_ssh "python3 -c '$py' '$ws'")
    state=$M1_SRV_HOME/.kollab/network/$dg
    present=$(m1_ssh "[ -e '$state' ] && echo yes || echo no")
  fi
  [ "$present" = no ] || abort pre-fresh-workspaces "$h workspace $ws already has network state (a previous run). Use a new workspace: M4_MAC_WS=... M4_SRV_WS_NAME=..."
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

MAC_LOG0=$(log_size mac)
SRV_LOG0=$(log_size srv)

# ================================================================ launch ====
say "launching both TUIs (120x40) in fresh workspaces"
mkdir -p "$M4_MAC_WS"
tmux kill-session -t "$M4_MAC_SESSION" 2>/dev/null || true
tmux new-session -d -s "$M4_MAC_SESSION" -x 120 -y 40 "cd '$M4_MAC_WS' && env KOLLAB_NO_KEYRING=1 '$M1_MAC_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh"
m1_ssh "mkdir -p '$M4_SRV_WS'; tmux kill-session -t $M4_SRV_SESSION 2>/dev/null; tmux new-session -d -s $M4_SRV_SESSION -x 120 -y 40 \"cd '$M4_SRV_WS' && env KOLLAB_NO_KEYRING=1 '$M1_SRV_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} --llm openai-oauth; exec zsh\""
wait_ready mac || abort s0-launch-mac "no output from the Mac TUI after 90s"
wait_ready srv || abort s0-launch-srv "no output from the server TUI after 90s"
cap mac s0-01-mac-launch
cap srv s0-02-srv-launch
for h in mac srv; do
  att=$(tm "$h" display -p -t "$(sess "$h")" '#{session_attached}' || echo 0)
  [ "$att" = 0 ] || say "WARNING: someone is attached to $(sess "$h"); keys can vanish while a human is attached"
done
if [ "$MAC_APPROVAL" != trust_all ]; then say "mac approval mode is '${MAC_APPROVAL:-default}': typing /permissions trust"; cmd mac "/permissions trust"; sleep 2; fi
if [ "$SRV_APPROVAL" != trust_all ]; then say "srv approval mode is '${SRV_APPROVAL:-default}': typing /permissions trust"; cmd srv "/permissions trust"; sleep 2; fi
rec s0-launch PASS s0-01-mac-launch.txt "both TUIs up; see s0-02-srv-launch.txt"

# ============================================ Story 6: the first device ====
say "Story 6: the Mac starts a network on $M4_DOMAIN"
ANSWER='trust: |connect: |beacon: '   # the status text, or the reason /connect gave up (key_changed, dns_unavailable, http_error ...)
b=$(count_pat mac "$ANSWER")
cmd mac "/connect $M4_DOMAIN"
if wait_for mac "$ANSWER" 150 "$b"; then
  sleep 2
  cap mac s6-01-mac-first-device 1
  status_line=$(newest_with mac 'network .*trust: ')
  if grep -Eq "selfhost\.kollabor\.ai" <<<"$status_line"; then
    rec s6-01-first-device PASS s6-01-mac-first-device.txt "the Mac is on: ${status_line# }"
  else
    abort s6-01-first-device "no network status naming $M4_DOMAIN; /connect answered: '$(newest_with mac 'connect: |beacon: ' | sed -E 's/^[[:space:]]+//')'" s6-01-mac-first-device.txt
  fi
else
  cap mac s6-01-mac-first-device-failed 1
  abort s6-01-first-device "/connect $M4_DOMAIN printed no status within 150s (discovery, TLS or the edge?)" s6-01-mac-first-device-failed.txt
fi

# ----------------------------------------------- the Mac shows a join code ----
MAC_BASE=$(raw mac 500)
cmd mac "/connect"
found=0
for _ in $(seq 1 30); do
  sleep 3
  CODE=$(screen mac | python3 "$SCAN" findcode 4< <(printf %s "$MAC_BASE")) || true
  if [ -n "$CODE" ]; then found=1; break; fi
done
[ "$found" = 1 ] || { cap mac s6-02-mac-no-code 1; abort s6-02-mac-join-code "the Mac's Connect screen showed no join code" s6-02-mac-no-code.txt; }
capscreen mac s6-02-mac-connect-screen 1
rec s6-02-mac-join-code PASS s6-02-mac-connect-screen.txt "the Connect screen shows a join code for $M4_DOMAIN"

# --------------------------- the server types the company domain and the code ----
cmd srv "/connect"
sleep 5
capscreen srv s6-03-srv-connect-form
grep -Eqi 'domain' <<<"$(screen srv)" || abort s6-03-srv-form "the server did not open the private code form" s6-03-srv-connect-form.txt
key srv Tab                       # focus the domain field (it starts on the code field, domain prefilled)
sleep 0.5
for _ in $(seq 1 30); do key srv BSpace; sleep 0.1; done   # clear whatever the field was prefilled with
typ srv "$M4_DOMAIN" 0.06
sleep 0.5
key srv Tab                       # back to the code field
sleep 0.5
typ srv "$CODE" 0.08
sleep 1
capscreen srv s6-04-srv-form-filled   # the masked code, and the company domain in the domain field
if grep -Eq "domain .*selfhost\.kollabor\.ai" <<<"$LAST"; then
  rec s6-03-srv-domain-typed PASS s6-04-srv-form-filled.txt "the server's form names $M4_DOMAIN and masks the code"
else
  abort s6-03-srv-domain-typed "the form's domain field does not read $M4_DOMAIN" s6-04-srv-form-filled.txt
fi
key srv Enter
sleep 4
cap srv s6-05-srv-after-enter
if grep -Eqi 'could not submit|rejected' <<<"$(screen srv)"; then
  abort s6-04-srv-submits-code "the server form reported a failure right after Enter" s6-05-srv-after-enter.txt
elif grep -Eqi 'request sent to selfhost\.kollabor\.ai|waiting for approval' <<<"$(screen srv)"; then
  rec s6-04-srv-submits-code PASS s6-05-srv-after-enter.txt "the server shows 'request sent to $M4_DOMAIN ... waiting for approval'"
else
  rec s6-04-srv-submits-code FAIL s6-05-srv-after-enter.txt "no 'request sent to $M4_DOMAIN ... waiting for approval' four seconds after Enter"
fi

# ------------------------------------------------------- the Mac accepts ----
wait_for mac 'wants to join' 120 || { cap mac s6-06-mac-no-request 1; abort s6-05-mac-sees-request "the Mac never showed '<device> wants to join'" s6-06-mac-no-request.txt; }
capscreen mac s6-06-mac-wants-to-join 1
SRV_DEVICE=$(newest_with mac '[^[:space:]]+[[:space:]]+wants to join' | sed -E 's/.*[[:space:]]([^[:space:]]+)[[:space:]]+wants to join.*/\1/')
[ -n "$SRV_DEVICE" ] || SRV_DEVICE=unknown
rec s6-05-mac-sees-request PASS s6-06-mac-wants-to-join.txt "the server device is named '$SRV_DEVICE'"
b=$(count_pat mac 'accepted|trusted device')
key mac a
if wait_for mac 'accepted|trusted device' 60 "$b"; then
  cap mac s6-07-mac-accepted 1
  rec s6-06-mac-accepts PASS s6-07-mac-accepted.txt "accepted $SRV_DEVICE"
else
  cap mac s6-07-mac-accept-failed 1
  abort s6-06-mac-accepts "no 'accepted' / 'trusted device' after pressing a" s6-07-mac-accept-failed.txt
fi
if wait_for srv 'joined ' 60; then
  cap srv s6-08-srv-joined
  JOINED_LINE=$(raw srv 200 | grep -E 'joined ' | tail -1 | sed -E 's/^[[:space:]]+//')
  if grep -Eq 'joined .+ as .+trust: ' <<<"$JOINED_LINE"; then
    rec s6-07-srv-joined PASS s6-08-srv-joined.txt "the server printed: $JOINED_LINE"
  else
    rec s6-07-srv-joined FAIL s6-08-srv-joined.txt "the server printed '$JOINED_LINE' instead of 'joined <network> as <device>. trust: <level>'"
  fi
else
  cap srv s6-08-srv-not-joined
  rec s6-07-srv-joined FAIL s6-08-srv-not-joined.txt "the server never printed a joined line within 60s (continuing: /connect status decides whether the join happened)"
fi

# -------------------- both sides list the other's agents as agent@device ----
key mac Escape
key srv Escape
sleep 2
TOKEN='[A-Za-z0-9_-]+@[A-Za-z0-9_.-]+'
REMOTE=""
BACK=""
MAC_DEVICE=""
for _ in $(seq 1 8); do
  cmd mac "/connect status"
  cmd srv "/connect status"
  sleep 7
  MAC_STATUS=$(raw mac 200)
  SRV_STATUS=$(raw srv 200)
  MAC_DEVICE=$(sed -nE 's/.*this device[[:space:]]+([^[:space:]]+).*/\1/p' <<<"$MAC_STATUS" | tail -1)
  REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -F "@$SRV_DEVICE" | head -1 || true)
  [ -n "$REMOTE" ] || REMOTE=$(grep -oE "$TOKEN" <<<"$MAC_STATUS" | grep -vF "@${MAC_DEVICE:-__none__}" | head -1 || true)
  BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -F "@${MAC_DEVICE:-__none__}" | head -1 || true)
  [ -n "$BACK" ] || BACK=$(grep -oE "$TOKEN" <<<"$SRV_STATUS" | grep -vF "@$SRV_DEVICE" | head -1 || true)
  [ -n "$REMOTE" ] && [ -n "$BACK" ] && break
  sleep 8
done
cap mac s6-09-mac-status
cap srv s6-10-srv-status
if [ -n "$REMOTE" ] && [ -n "$BACK" ]; then
  rec s6-08-status-both-sides PASS s6-09-mac-status.txt "the Mac sees $REMOTE, the server sees $BACK (see s6-10-srv-status.txt)"
else
  abort s6-08-status-both-sides "the Mac sees '${REMOTE:-nothing}', the server sees '${BACK:-nothing}' as agent@device" s6-09-mac-status.txt
fi

# ------------------------------------------------ the two exchange a message ----
KOLLAB="$M1_MAC_VENV/bin/kollab"
exchange() { # exchange <step> <evidence-name> <ask>: shell -> the server agent, its reply printed, exit 0
  local step=$1 name=$2 ask=$3 rc=0 out
  out=$(cd "$M4_MAC_WS" && run_limited 300 env KOLLAB_NO_KEYRING=1 "$KOLLAB" --hub msg "$REMOTE" "$ask" 2>&1) || rc=$?
  record mac "$name" "$(printf 'exit=%s\n%s' "$rc" "$out")"
  if [ "$rc" -ne 0 ]; then
    rec "$step" FAIL "$name.txt" "exit $rc (expected 0)"
    return 1
  elif [ -z "$out" ]; then
    rec "$step" FAIL "$name.txt" "no reply printed"
    return 1
  elif ! grep -Eqi 'avail|free|disk|used' <<<"$out" || ! grep -Eq '[0-9]' <<<"$out"; then
    rec "$step" FAIL "$name.txt" "the printed reply is not a disk report: $(head -c 160 <<<"$out" | tr '\n' ' ')"
    return 1
  fi
  rec "$step" PASS "$name.txt" "exit 0, disk report printed"
}
say "message exchange through $M4_DOMAIN"
SHELL0=$(shell_ok srv "$SRV_LOG0")
exchange s6-09-message-exchange s6-11-message-exchange "report the free disk space on / in one line" || true
SHELL1=$(shell_ok srv "$SRV_LOG0")
if [ "${SHELL1:-0}" -gt "${SHELL0:-0}" ]; then
  rec s6-10-server-ran-its-shell PASS s6-11-message-exchange.txt "server shell runs $SHELL0 -> $SHELL1"
else
  rec s6-10-server-ran-its-shell FAIL s6-11-message-exchange.txt "the server log shows no new shell tool run ($SHELL0 -> $SHELL1)"
fi

# ---------------------------------------------- nothing touched kollabor.ai ----
BARE='(^|[^A-Za-z0-9.-])kollabor\.ai'
URLS='(wss?|https?)://kollabor\.ai'
touched=""
grep -Eq "$BARE" <<<"$MAC_STATUS" && touched="$touched mac-status"
grep -Eq "$BARE" <<<"$SRV_STATUS" && touched="$touched srv-status"
[ "$(log_count mac "$MAC_LOG0" "$URLS")" = 0 ] || touched="$touched mac-log"
[ "$(log_count srv "$SRV_LOG0" "$URLS")" = 0 ] || touched="$touched srv-log"
if [ -z "$touched" ]; then
  rec s6-11-nothing-touches-kollabor-ai PASS s6-09-mac-status.txt "neither /connect status nor either kollab.log names kollabor.ai (only $M4_DOMAIN)"
else
  rec s6-11-nothing-touches-kollabor-ai FAIL s6-09-mac-status.txt "kollabor.ai appears in:$touched"
fi
# The strict log window ends here: joining and the first message. A restart of the relay makes clients log
# reconnects, which the second window allows (it still forbids tracebacks and leaks).
MAC_LOG1=$(log_size mac)
SRV_LOG1=$(log_size srv)

# ------------------------------------- stop the one command, start it again ----
say "restart: stopping 'kollab relay serve' on $M1_HOST, then starting it again"
KEYS_BEFORE=$(manifest_key) || abort s6-12-serve-stops-cleanly "could not read the key file before the restart"
READY0=$(serve_pane | grep -c 'ready: relay up' || true)
m1_ssh "tmux send-keys -t $M4_SERVE_SESSION C-c" </dev/null
gone=0
for _ in $(seq 1 30); do
  sleep 2
  [ -z "$(serve_pid)" ] && { gone=1; break; }
done
serve_pane >"$EVID/s6-12-serve-after-stop.txt"
if [ "$gone" = 1 ] && ! grep -Eq 'Traceback|Error' "$EVID/s6-12-serve-after-stop.txt"; then
  rec s6-12-serve-stops-cleanly PASS s6-12-serve-after-stop.txt "the process exited on Ctrl-C with no traceback"
else
  rec s6-12-serve-stops-cleanly FAIL s6-12-serve-after-stop.txt "still running after 60s, or the pane shows a traceback/error"
fi
sleep 5   # let the devices notice; they reconnect with backoff
m1_ssh "tmux send-keys -t $M4_SERVE_SESSION -l \"bash '$M4_SRV_ROOT/serve-cmd.sh'\"; tmux send-keys -t $M4_SERVE_SESSION Enter" </dev/null
up=0
for _ in $(seq 1 45); do
  sleep 2
  [ "$(serve_pane | grep -c 'ready: relay up' || true)" -gt "$READY0" ] && { up=1; break; }
done
serve_pane >"$EVID/s6-13-serve-after-restart.txt"
KEYS_AFTER=$(manifest_key || true)
if [ "$up" = 1 ] && [ -n "$KEYS_AFTER" ] && [ "${KEYS_AFTER%% *}" = "${KEYS_BEFORE%% *}" ] && [ "${KEYS_AFTER##* }" -gt "${KEYS_BEFORE##* }" ]; then
  rec s6-13-same-identity-after-restart PASS s6-13-serve-after-restart.txt "publisher key unchanged, revision ${KEYS_BEFORE##* } -> ${KEYS_AFTER##* }"
else
  rec s6-13-same-identity-after-restart FAIL s6-13-serve-after-restart.txt "ready=$up, before='${KEYS_BEFORE:-?}', after='${KEYS_AFTER:-?}' (same key, higher revision expected)"
fi

say "waiting for both devices to come back and answer (their reconnect backs off, up to a minute)"
SHELL2=$(shell_ok srv "$SRV_LOG0")
back=0
attempts=10
for attempt in $(seq 1 "$attempts"); do
  rc=0
  out=$(cd "$M4_MAC_WS" && run_limited 150 env KOLLAB_NO_KEYRING=1 "$KOLLAB" --hub msg "$REMOTE" "report the free disk space on /var in one line" 2>&1) || rc=$?
  if [ "$rc" -eq 0 ] && grep -Eqi 'avail|free|disk|used' <<<"$out" && grep -Eq '[0-9]' <<<"$out"; then
    back=1
    break
  fi
  say "attempt $attempt: no answer yet (exit $rc): $(head -c 100 <<<"$out" | tr '\n' ' ')"
  sleep 15
done
record mac s6-14-message-after-restart "$(printf 'attempt=%s exit=%s\n%s' "$attempt" "$rc" "$out")"   # the last attempt
SHELL3=$(shell_ok srv "$SRV_LOG0")
if [ "$back" = 1 ] && [ "${SHELL3:-0}" -gt "${SHELL2:-0}" ]; then
  rec s6-14-devices-back-and-answering PASS s6-14-message-after-restart.txt "answered on attempt $attempt; server shell runs $SHELL2 -> $SHELL3"
else
  rec s6-14-devices-back-and-answering FAIL s6-14-message-after-restart.txt "back=$back after $attempts attempts, server shell runs $SHELL2 -> $SHELL3"
fi

# ============================================================== leak scans ====
say "scanning panes and logs"
cap mac s9-mac-final
cap srv s9-srv-final
serve_pane >"$EVID/s9-serve-final.txt"
if grep -Eq 'Traceback|ERROR|Error' "$EVID/s9-serve-final.txt"; then
  rec z0-serve-pane-clean FAIL s9-serve-final.txt "the one command's pane shows a traceback or error"
else
  rec z0-serve-pane-clean PASS s9-serve-final.txt "no traceback or error in the one command's output"
fi
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then
  rec z1-panes-clean PASS - "no join code, 64-hex, relay: address, receipt, or error text on any captured pane"
else
  rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"
fi
late=""
for h in mac srv; do
  if [ "$h" = mac ]; then off0=$MAC_LOG0; off1=$MAC_LOG1; else off0=$SRV_LOG0; off1=$SRV_LOG1; fi
  end=$(log_size "$h")
  res=$(log_window_scan "$h" "$off0" "$off1")
  printf '%s\n' "$res" >"$EVID/logscan-$h.txt"
  if grep -qx 'missing=1' <<<"$res"; then
    rec "z2-log-$h" FAIL "logscan-$h.txt" "no log at $(log_path "$h")"
  elif [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "$(kv "$res" errhits)" = 0 ]; then
    rec "z2-log-$h" PASS "logscan-$h.txt" "$(kv "$res" size) bytes from launch to the first message, clean"
  else
    rec "z2-log-$h" FAIL "logscan-$h.txt" "code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) errhits=$(kv "$res" errhits)"
  fi
  res=$(log_window_scan "$h" "$off1" "$end")
  printf '%s\n' "$res" >"$EVID/logscan-$h-restart.txt"
  traces=$(log_count "$h" "$off1" 'Traceback|Failed executing')
  if [ "$(kv "$res" code)" = 0 ] && [ "$(kv "$res" hex64)" = 0 ] && [ "$(kv "$res" relay)" = 0 ] && [ "${traces:-1}" = 0 ]; then
    rec "z3-log-$h-restart" PASS "logscan-$h-restart.txt" "no traceback or leak across the relay restart ($(kv "$res" errhits) error-level lines, reconnects expected)"
  else
    rec "z3-log-$h-restart" FAIL "logscan-$h-restart.txt" "code=$(kv "$res" code) hex64=$(kv "$res" hex64) relay=$(kv "$res" relay) tracebacks=${traces:-?}"
  fi
  [ "$(log_count "$h" "$off0" "$URLS")" = 0 ] || late="$late $h-log"
done
if [ -z "$late" ]; then
  rec z4-kollabor-ai-never-named PASS - "no log names kollabor.ai from launch to the end, restart included"
else
  rec z4-kollabor-ai-never-named FAIL - "kollabor.ai appears in:$late"
fi
