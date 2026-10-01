#!/usr/bin/env bash
# proof.sh: drive Story 8 of docs/specs/agent-network-simple-flow.md (the sealed
# config follows Marco) on the INSTALLED m1 builds, in the m1-mac / m1-srv tmux
# sessions that m1/proof.sh left joined. The Mac issued the join code, so it is
# the primary. Evidence lands in m2/evidence/.
#
# Order: build_wheels.sh, install_both.sh, m1/proof.sh, THIS, m1/teardown.sh.
# The fake api key exists only in the shell variable KEY: it reaches the Mac's
# config.json through a pipe, and is looked for (never printed) in every pane and
# kollab log. Do not run with `bash -x`.
#
# Exit code 0 only if every row of the final table is PASS.
set -euo pipefail
M2_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$M2_DIR/../m1/env.sh"          # M1_DIR is set there; the settings and m1_ssh are shared
EVID=$M2_DIR/evidence
mkdir -p "$EVID"
: > "$EVID/pane-findings.txt"     # this run only
POLL=3
SCAN=$M1_DIR/scan.py
PROBE=$M2_DIR/probe.py
CODE=""                           # no join code in this proof; scan.py still wants the argument
LAST=""
DIRTY=0
MAC_ORIG="-"
REC_STEP=(); REC_STATUS=(); REC_EVID=(); REC_NOTE=()
PANE_FINDINGS=()

# ---------------------------------------------------------------- results ----
rec() { # rec <step> <PASS|FAIL> <evidence-file-or-dash> <note>
  REC_STEP+=("$1"); REC_STATUS+=("$2"); REC_EVID+=("$3"); REC_NOTE+=("${4:-}")
  say "$2  $1${4:+  ($4)}"
}
restore_mac() { # put the Mac's config back; the server follows on its own
  python3 "$PROBE" restore "$MAC_ORIG" || return 1
  DIRTY=0
  python3 "$PROBE" skill del
}
finish() {
  local code=$? i fails=0 n=${#REC_STEP[@]}
  if [ "$DIRTY" = 1 ]; then
    restore_mac || say "WARNING: could not restore the Mac's config.json; run: python3 $PROBE restore '$MAC_ORIG'"
  fi
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
# The same helpers as m1/proof.sh (that file runs its steps at top level, so it cannot be sourced).
sess() { if [ "$1" = mac ]; then printf %s "$M1_MAC_SESSION"; else printf %s "$M1_SRV_SESSION"; fi; }
tm() { local h=$1; shift; if [ "$h" = mac ]; then tmux "$@"; else m1_ssh "$(printf '%q ' tmux "$@")" </dev/null; fi; }
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
wait_screen() { # wait_screen <host> <ERE> <timeout-s>: until the visible screen matches
  local h=$1 re=$2 end
  end=$(( $(date +%s) + $3 ))
  while :; do
    screen "$h" | grep -Eq -- "$re" && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}

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

log_path() { # <mac|srv>: kollab.log of that host's m1 workspace
  local ws home
  if [ "$1" = mac ]; then ws=$M1_MAC_WS; home=$HOME; else ws=$M1_SRV_WS; home=$M1_SRV_HOME; fi
  printf %s "$(m1_project_dir "$ws" "$home")/logs/kollab.log"
}
log_size() { if [ "$1" = mac ]; then wc -c < "$(log_path mac)" 2>/dev/null || echo 0; else m1_ssh "wc -c < '$(log_path srv)' 2>/dev/null || echo 0"; fi; }
log_scan() { # log_scan <mac|srv> <offset>
  if [ "$1" = mac ]; then printf '' | python3 "$SCAN" file "$(log_path mac)" "$2"
  else printf '' | m1_ssh python3 "$M1_SRV_ROOT/bin/scan.py" file "$(log_path srv)" "$2"; fi
}
kv() { sed -n "s/^$2=//p" <<<"$1" | head -1; }   # kv <key=value lines> <key>

mac_state() { python3 "$PROBE" state; }
srv_state() { m1_ssh python3 "$M1_SRV_ROOT/bin/probe.py" state; }
srv_until() { # srv_until <key> <value> <seconds>: poll the server until key=value; leaves the last state in SRV_NOW
  local end=$(( $(date +%s) + $3 ))
  while :; do
    SRV_NOW=$(srv_state) || true
    [ "$(kv "$SRV_NOW" "$1")" = "$2" ] && return 0
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep "$POLL"
  done
}

# =============================================================== preflight ====
say "preflight"
m1_srv_paths
tmux has-session -t "$M1_MAC_SESSION" 2>/dev/null || abort s8-pre-sessions "no tmux session $M1_MAC_SESSION on the Mac: run m1/proof.sh first and do not run m1/teardown.sh yet"
m1_ssh "tmux has-session -t $M1_SRV_SESSION" 2>/dev/null || abort s8-pre-sessions "no tmux session $M1_SRV_SESSION on $M1_HOST: run m1/proof.sh first and do not run m1/teardown.sh yet"
m1_ssh "mkdir -p '$M1_SRV_ROOT/bin'"
scp -q "${M1_SSH_OPTS[@]}" "$M1_DIR/scan.py" "$M1_DIR/tmuxtype.py" "$PROBE" "$M1_HOST:$M1_SRV_ROOT/bin/"

MAC0=$(mac_state); SRV0=$(srv_state)
[ "$(kv "$MAC0" keysha)" = "-" ] || abort s8-pre-clean "the Mac already has a m2-proof profile (an earlier run?). Run: python3 $PROBE restore <original active_profile, or - if it was unset>"
[ "$(kv "$SRV0" keysha)" = "-" ] || abort s8-pre-clean "the server already has a m2-proof profile (an earlier run?). Restore the Mac and wait a minute"
MAC_ORIG=$(kv "$MAC0" active)
PRIMARY=$(kv "$SRV0" primary)
[ "$PRIMARY" != "-" ] || abort s8-pre-primary "the server has no ~/.kollab/private/managed-config.json: it never received the Mac's sealed config. Use a build with milestone 2 for m1/proof.sh, then wait a minute and rerun"

key mac Escape; key srv Escape; sleep 1
{ echo "== mac before"; echo "$MAC0"; echo "== server before"; echo "$SRV0"; } > "$EVID/s8-00-state-before.txt"
rec s8-pre PASS s8-00-state-before.txt "both sessions up; the server recorded primary '$PRIMARY'; the Mac's active_profile was '$MAC_ORIG'"

MAC_LOG0=$(log_size mac); SRV_LOG0=$(log_size srv)
KEY="sk-m2-proof-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
KEYSHA=$(printf %s "$KEY" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')

# ============================================================== Story 8 ====
say "Story 8: switch the loadout on the Mac"
DIRTY=1
T0=$(date +%s)
printf %s "$KEY" | python3 "$PROBE" switch
if srv_until keysha "$KEYSHA" 60; then
  took=$(( $(date +%s) - T0 ))
  problems=""
  [ "$(kv "$SRV_NOW" active)" = m2-proof ] || problems="$problems active_profile is '$(kv "$SRV_NOW" active)';"
  [ "$(kv "$SRV_NOW" model)" = m2-proof-model ] || problems="$problems model is '$(kv "$SRV_NOW" model)';"
  [ "$(kv "$SRV_NOW" mode)" = 600 ] || problems="$problems config.json mode is '$(kv "$SRV_NOW" mode)', want 600;"
  printf '%s\n' "$SRV_NOW" > "$EVID/s8-01-state-server-after-switch.txt"
  [ -z "$problems" ] || abort s8-loadout "$problems" s8-01-state-server-after-switch.txt
  rec s8-loadout PASS s8-01-state-server-after-switch.txt "server followed in ${took}s: same active_profile and model, key digest equal, config.json 0600"
else
  printf '%s\n' "$SRV_NOW" > "$EVID/s8-01-state-server-after-switch.txt"
  abort s8-loadout "60s passed and the server still lacks the Mac's key (active_profile '$(kv "$SRV_NOW" active)', model '$(kv "$SRV_NOW" model)')" s8-01-state-server-after-switch.txt
fi

say "Story 8: /config on the server"
cmd srv "/config"
wait_screen srv 'System Configuration' 30 || abort s8-config-screen "the server's /config did not open within 30s"
typ srv "/"; typ srv "loadout" 0.08; key srv Enter; sleep 2
capscreen srv s8-02-srv-config-loadout
loadout_row=$(grep -E 'Loadout: +m2-proof +managed by ' <<<"$LAST" | head -1 || true)
model_row=$(grep -E 'Model: +m2-proof-model +managed by ' <<<"$LAST" | head -1 || true)
key srv Escape; sleep 1; key srv Escape; sleep 2
if [[ "$loadout_row" == *"managed by $PRIMARY"* && "$model_row" == *"managed by $PRIMARY"* ]]; then
  rec s8-config-screen PASS s8-02-srv-config-loadout.txt "Loadout and Model rows read m2-proof / m2-proof-model, managed by $PRIMARY"
else
  rec s8-config-screen FAIL s8-02-srv-config-loadout.txt "expected 'Loadout: m2-proof   managed by $PRIMARY' and 'Model: m2-proof-model   managed by $PRIMARY'; saw loadout row '${loadout_row:-none}', model row '${model_row:-none}'"
fi

say "Story 8: the key never shows"
hits=""
for h in mac srv; do
  n=$(raw "$h" 3000 | grep -cF -f <(printf %s "$KEY") || true)
  [ "$n" = 0 ] || hits="$hits $h-pane:$n"
done
n=$(grep -cF -f <(printf %s "$KEY") "$(log_path mac)" 2>/dev/null || true)
[ "${n:-0}" = 0 ] || hits="$hits mac-log:$n"
n=$(printf %s "$KEY" | m1_ssh "grep -cF -f - '$(log_path srv)' 2>/dev/null || true")
[ "${n:-0}" = 0 ] || hits="$hits srv-log:$n"
if [ -z "$hits" ]; then rec s8-sealed PASS - "the fake key is in no pane history (3000 lines) and no kollab.log on either host"
else rec s8-sealed FAIL - "the fake key appears in:$hits"; fi

say "Story 8: a skill follows"
python3 "$PROBE" skill add
SKILLSHA=$(kv "$(mac_state)" skill)
if srv_until skill "$SKILLSHA" 120; then rec s8-skill-add PASS - "skills/m2-proof-skill/SKILL.md reached the server with the same digest"
else rec s8-skill-add FAIL - "the skill did not reach the server within 120s (server skill digest '$(kv "$SRV_NOW" skill)')"; fi
python3 "$PROBE" skill del
if srv_until skill - 120; then rec s8-skill-del PASS - "deleting it on the Mac removed it from the server"
else rec s8-skill-del FAIL - "the skill was still on the server 120s after the Mac deleted it"; fi

say "Story 8: cleanup"
restore_mac
if srv_until keysha - 60 && srv_until active "$MAC_ORIG" 60; then
  printf '%s\n' "$SRV_NOW" > "$EVID/s8-03-state-server-after-restore.txt"
  rec s8-cleanup PASS s8-03-state-server-after-restore.txt "the Mac dropped m2-proof; the server dropped it too and its active_profile is '$MAC_ORIG' again"
else
  printf '%s\n' "$SRV_NOW" > "$EVID/s8-03-state-server-after-restore.txt"
  rec s8-cleanup FAIL s8-03-state-server-after-restore.txt "the server still has the profile or a different active_profile ('$(kv "$SRV_NOW" active)', want '$MAC_ORIG') 60s after the Mac restored"
fi

say "Story 8: what must not travel"
SRV9=$(srv_state)
if [ "$(kv "$SRV9" oauth)" = "$(kv "$SRV0" oauth)" ] && [ "$(kv "$SRV9" vaults)" = "$(kv "$SRV0" vaults)" ]; then
  rec s8-excluded PASS - "the server's oauth/openai.json digest and hub/vaults listing are unchanged from before the run"
else
  rec s8-excluded FAIL - "oauth digest or vault listing changed on the server (oauth $(kv "$SRV0" oauth) -> $(kv "$SRV9" oauth); vaults $(kv "$SRV0" vaults) -> $(kv "$SRV9" vaults))"
fi

# ============================================================== leak scans ====
say "scanning panes and logs"
cap mac z-mac-final 250; cap srv z-srv-final 250
if [ "${#PANE_FINDINGS[@]}" -eq 0 ]; then rec z1-panes-clean PASS - "no 64-hex string, relay: address, or error text on the panes captured in this run"
else rec z1-panes-clean FAIL pane-findings.txt "findings in: ${PANE_FINDINGS[*]}"; fi
for h in mac srv; do
  if [ "$h" = mac ]; then off=$MAC_LOG0; else off=$SRV_LOG0; fi
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
