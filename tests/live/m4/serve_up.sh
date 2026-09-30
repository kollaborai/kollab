#!/usr/bin/env bash
# serve_up.sh [plan|up]: replace the manual selfhost.kollabor.ai stack on alzan-prod (relay supervisor,
# standalone publisher and static file server, tmux sh-relay / sh-pub / sh-static) with the one command,
# `kollab relay serve --domain selfhost.kollabor.ai`, run from the m1 venv.
#
#   plan (default)  read-only: what runs today, what would be stopped, the command that would start.
#   up              do it. Records the old stack first and writes, on alzan-prod, in ~/kollab-m4/:
#                     serve.env, serve-cmd.sh   how the one command is started (also used to restart it)
#                     restore-old-stack.sh      brings the three old tmux sessions back
#                   and here, in m4/evidence/: old-stack.json, pre-manifest.json, pre.env, serve-banner.txt.
#
# The one command adopts the old publisher's state directory, so the signing key and the revision counter
# carry over: same published identity. It never touches kollabor.ai. It does not change the edge: after
# `up` the public domain still points at the old ports; run edge_vhost.sh next.
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
MODE=${1:-plan}
case "$MODE" in plan | up) ;; *) die "usage: serve_up.sh [plan|up]" ;; esac
EVID=$M4_DIR/evidence
mkdir -p "$EVID"
m4_srv_paths
KOLLAB=$M1_SRV_VENV/bin/kollab
EDGE_WG_IP=${M4_EDGE_WG_IP:-10.0.0.1}   # alzan-edge on the WireGuard mesh; used only when the old config trusts no proxy

# ---- the installed build has the command --------------------------------------------------------
help_out=$(m1_ssh "cd \"\$HOME\" && '$KOLLAB' relay serve --help 2>&1" || true)
grep -q -- '--domain' <<<"$help_out" || die "the build installed on $M1_HOST has no 'kollab relay serve --domain'. Build the branch and install it: m1/build_wheels.sh <ref> <wheeldir>; m1/install_both.sh <wheeldir>"
say "installed build on $M1_HOST has relay serve --domain"

# ---- what runs today (read-only) ---------------------------------------------------------------
m1_ssh python3 - "$M4_DOMAIN" <"$M4_DIR/inspect_old.py" >"$EVID/old-stack.now.json" || die "could not inspect the old stack on $M1_HOST"
plan=$(python3 - "$EVID/old-stack.now.json" <<'PY'
import json, shlex, sys

d = json.load(open(sys.argv[1]))
out = {}
for key, label in (("relay", "RELAY"), ("publisher", "PUB"), ("static", "STATIC")):
    entry = d.get(key)
    out[f"OLD_{label}_PID"] = entry["pid"] if entry else ""
relay, pub = d.get("relay") or {}, d.get("publisher") or {}
settings = relay.get("settings") or {}
out["OLD_BIND"] = settings.get("bind_host") or ""
out["OLD_TRUSTED"] = " ".join(settings.get("trusted_proxies") or [])
out["OLD_PUB_STATE"] = pub.get("state_dir") or ""
for k, v in out.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
)
eval "$plan"
say "old stack on $M1_HOST for $M4_DOMAIN:"
printf '  relay supervisor pid %s   publisher pid %s   static server pid %s\n' "${OLD_RELAY_PID:-none}" "${OLD_PUB_PID:-none}" "${OLD_STATIC_PID:-none}"
printf '  relay bind %s   trusted proxies [%s]   publisher state dir %s\n' "${OLD_BIND:-?}" "${OLD_TRUSTED:-}" "${OLD_PUB_STATE:-?}"
[ -n "$OLD_RELAY_PID" ] && [ -n "$OLD_PUB_PID" ] && [ -n "$OLD_PUB_STATE" ] && [ -n "$OLD_BIND" ] ||
  die "did not find the relay, the publisher and their settings for $M4_DOMAIN (see $EVID/old-stack.now.json). Nothing was changed. If the stack runs differently, start the one command by hand: $KOLLAB relay serve --domain $M4_DOMAIN --state-dir <publisher state dir> --bind <address the edge reaches> --port $M4_PORT --trusted-proxy <edge address>"
# The recording restore-old-stack.sh is built from. Only a run that found the stack replaces it: a later run, after
# the old stack is gone, would otherwise overwrite it with nulls.
cp "$EVID/old-stack.now.json" "$EVID/old-stack.json"
TRUSTED=${OLD_TRUSTED:-$EDGE_WG_IP}
BIND=$OLD_BIND
STATE=$OLD_PUB_STATE
SERVE=(relay serve --domain "$M4_DOMAIN" --state-dir "$STATE" --bind "$BIND" --port "$M4_PORT")
for proxy in $TRUSTED; do SERVE+=(--trusted-proxy "$proxy"); done
say "the one command would run:  kollab $(printf '%q ' "${SERVE[@]}")"
# The command refuses a state directory that others can read, and a missing or open key. Find out now, not after the old stack is stopped.
state_facts=$(m1_ssh "stat -c '%a %U' '$STATE' && stat -c '%a' '$STATE/service.key' && test -f '$STATE/publisher.json' && echo revision-state-present" </dev/null 2>&1 | tr '\n' ' ') || true
printf '  state dir (mode owner), service.key mode: %s\n' "$state_facts"
case "$state_facts" in
  "700 $(m1_ssh whoami </dev/null) 600 revision-state-present"*) ;;
  *) die "the publisher state directory $STATE is not ready for the one command (need mode 700 owned by the ssh user, service.key mode 600, publisher.json present; got: $state_facts). Nothing was changed." ;;
esac
say "checking the edge before anything is stopped"
bash "$M4_DIR/edge_vhost.sh" check "$BIND:$M4_PORT" || die "the edge is not ready for the change (reason above). Nothing was changed."
if [ "$MODE" = plan ]; then
  say "plan only. Stops the three old processes and their tmux sessions (${M4_OLD_SESSIONS[*]}), keeps their state, starts tmux $M4_SERVE_SESSION. Run: serve_up.sh up"
  exit 0
fi

# ---- up ---------------------------------------------------------------------------------------------
say "recording the public manifest before the change"
curl -sS -m 20 "https://$M4_DOMAIN/.well-known/agent-keys.json" >"$EVID/pre-manifest.json" || die "could not fetch the public manifest of $M4_DOMAIN"
python3 - "$EVID/pre-manifest.json" >"$EVID/pre.env" <<'PY'
import json, shlex, sys

d = json.load(open(sys.argv[1]))
# pre.env is sourced later: quote what came off the network so a value can never be code
for name, value in (("PRE_KEY", d["coordinator"]["public_key"]), ("PRE_REVISION", d["revision"]), ("PRE_ROLES", ",".join(d["discovery"]["roles"]))):
    print(f"{name}={shlex.quote(str(value))}")
PY
scrub <"$EVID/pre.env"

say "writing the restore script for the old stack on $M1_HOST (~/kollab-m4/restore-old-stack.sh)"
m1_ssh "mkdir -p '$M4_SRV_ROOT'"
python3 - "$EVID/old-stack.json" <<'PY' | m1_ssh "cat > '$M4_SRV_ROOT/restore-old-stack.sh' && chmod 700 '$M4_SRV_ROOT/restore-old-stack.sh'"
import json, shlex, sys

d = json.load(open(sys.argv[1]))
entries = [(session, d[key]) for key, session in (("static", "sh-static"), ("relay", "sh-relay"), ("publisher", "sh-pub")) if d.get(key)]
print("#!/usr/bin/env bash")
print("# Recreates the manual selfhost.kollabor.ai stack as serve_up.sh found it: each command line and working directory.")
print("# The environment of the original tmux shells was not recorded. Stop m4-serve first (teardown.sh --restore-old does).")
print("# Safe to run twice: a command that already runs is left alone. Exits non-zero if one is not running after 3 s.")
print("set -u")
print("bad=0")
print('running() { ps -ewwo args= | grep -Fxq -- "$1"; }')
for session, entry in entries:
    print(f"cmd_{session[3:]}={shlex.quote(' '.join(entry['args']))}")
for session, entry in entries:
    print(f'if running "$cmd_{session[3:]}"; then echo "{session}: already running"; else')
    print(f"  tmux kill-session -t '={session}' 2>/dev/null")
    print(f"  tmux new-session -d -s {session} -c {shlex.quote(entry['cwd'])} {shlex.quote(shlex.join(entry['args']) + '; exec bash')} || bad=1")
    print("fi")
print("sleep 3")
for session, entry in entries:
    print(f'running "$cmd_{session[3:]}" && echo "{session}: running" || {{ echo "{session}: NOT running; look at: tmux attach -t {session}" >&2; bad=1; }}')
print("exit $bad")
PY

# From here a failure leaves the old stack down: say how to put everything back (m4-serve holds the old port, so
# running restore-old-stack.sh by hand is not enough).
# shellcheck disable=SC2154
trap 'rc=$?; if [ "$rc" -ne 0 ]; then printf "[m4 FATAL] stopped partway; the old stack may be down. Put it back (stops m4-serve, restarts sh-*, restores the vhost): bash %s/teardown.sh --restore-old\n" "$M4_DIR" >&2; fi' EXIT
say "stopping the old stack (SIGTERM, supervisor first; state is kept)"
for pid in $OLD_RELAY_PID $OLD_PUB_PID $OLD_STATIC_PID; do
  # A zombie (state Z) has exited and only waits for its parent to reap it; kill -0 still
  # succeeds on it, so count it as stopped.
  m1_ssh "kill -TERM $pid 2>/dev/null; for _ in \$(seq 1 60); do case \$(ps -o stat= -p $pid 2>/dev/null) in ''|Z*) exit 0;; esac; sleep 1; done; echo 'pid $pid is still running after 60 s' >&2; exit 1" || die "pid $pid is still running after 60 s; the rest of the old stack was left as it is"
done
for session in "${M4_OLD_SESSIONS[@]}"; do m1_ssh "tmux kill-session -t '=$session' 2>/dev/null || true"; done
m1_ssh "! ss -ltn 2>/dev/null | awk '{print \$4}' | grep -q '[:.]$M4_PORT\$'" </dev/null || die "port $M4_PORT is still listening on $M1_HOST after the old stack stopped (set M4_PORT)"

say "starting the one command in tmux $M4_SERVE_SESSION"
{
  printf '#!/usr/bin/env bash\n'
  printf '# Started by m4/serve_up.sh. Run by tmux %s; proof.sh uses it again to restart the command.\n' "$M4_SERVE_SESSION"
  printf 'cd "$HOME" || exit 1\n'
  printf 'exec env KOLLAB_NO_KEYRING=1 %q' "$KOLLAB"
  printf ' %q' "${SERVE[@]}"
  printf '\n'
} | m1_ssh "cat > '$M4_SRV_ROOT/serve-cmd.sh' && chmod 700 '$M4_SRV_ROOT/serve-cmd.sh'"
printf 'BIND=%s\nPORT=%s\nTRUSTED=%s\nSTATE=%s\n' "$BIND" "$M4_PORT" "$TRUSTED" "$STATE" | m1_ssh "cat > '$M4_SRV_ROOT/serve.env'"
m1_ssh "tmux kill-session -t $M4_SERVE_SESSION 2>/dev/null || true; tmux new-session -d -s $M4_SERVE_SESSION -x 150 -y 50 \"bash '$M4_SRV_ROOT/serve-cmd.sh'; exec bash\""
ready=""
for _ in $(seq 1 45); do
  sleep 2
  pane=$(m1_ssh "tmux capture-pane -p -t $M4_SERVE_SESSION" </dev/null 2>/dev/null || true)
  if grep -q 'ready: relay up' <<<"$pane"; then ready=1; break; fi
done
m1_ssh "tmux capture-pane -p -J -t $M4_SERVE_SESSION" >"$EVID/serve-banner.txt" || true
[ -n "$ready" ] || die "the one command did not report ready within 90 s; see $EVID/serve-banner.txt"
scrub <"$EVID/serve-banner.txt"

say "local checks on $M1_HOST ($BIND:$M4_PORT)"
health=$(m1_ssh "curl -sS -m 5 http://$BIND:$M4_PORT/relay/v1/health") || die "health did not answer"
grep -q '"status": "ok"' <<<"$health" || die "health is not ok: $health"
echo "  health $(printf '%s' "$health" | scrub)"
key=$(m1_ssh "curl -sS -m 5 http://$BIND:$M4_PORT/.well-known/agent-keys.json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["coordinator"]["public_key"], d["revision"], ",".join(d["discovery"]["roles"]))')
echo "  key file: key/revision/roles = $(printf '%s' "$key" | scrub)"
# shellcheck disable=SC1091
. "$EVID/pre.env"
[ "${key%% *}" = "$PRE_KEY" ] || die "the key file now carries a different publisher key than before ($(printf '%s' "$PRE_KEY" | scrub)). Devices that pinned the old key would refuse it."
say "same publisher key as before the change. Next: edge_vhost.sh show, then apply"
trap - EXIT
