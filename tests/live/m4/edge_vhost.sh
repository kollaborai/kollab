#!/usr/bin/env bash
# edge_vhost.sh [show|apply|restore|check HOST:PORT]: point the edge nginx vhost for selfhost.kollabor.ai at the one command.
#
# Today the vhost forwards to three things on alzan-prod (10.0.0.5): the relay workers (9178, 9179), the
# supervisor's health port (9180) and a static server for the key file (9177). `kollab relay serve` answers
# all five routes on one port, so the vhost must name that port instead. serve_up.sh wrote it to
# ~/kollab-m4/serve.env on alzan-prod. This changes ONLY the upstream server lines and the direct proxy_pass
# lines (repoint_vhost.py refuses anything it does not understand, and any file that serves another name than
# the domain). It runs on alzan-edge over ssh with sudo -n.
#
#   show     (default) read-only: the vhost as it is, and the diff apply would make.
#   apply    back the vhost up to /etc/nginx/m4-backups/, write the change, `nginx -t`, reload, then check
#            the public health route. Any failure puts the backup back, reloads, and says whether that worked.
#            Refuses while an earlier apply is unrestored, so the backup always holds the original vhost.
#   restore  put the backup named in m4-backups/LATEST back, `nginx -t`, reload; the pointer then moves to
#            LATEST.used, so a stale backup is never replayed. No pointer: nothing was changed, nothing to do.
#   check    read-only, run by serve_up.sh before it stops anything: sudo -n works, nginx -t passes today,
#            exactly one vhost names the domain, and it can be repointed to HOST:PORT.
#
# The public domain is down from `serve_up.sh up` until `apply` has run (or until the old stack is restored).
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
MODE=${1:-show}
case "$MODE" in show | apply | restore | check) ;; *) die "usage: edge_vhost.sh [show|apply|restore|check HOST:PORT]" ;; esac
EVID=$M4_DIR/evidence
mkdir -p "$EVID"
BACKUPS=/etc/nginx/m4-backups

find_vhost() {
  local found dom=${M4_DOMAIN//./[.]}
  found=$(m4_edge "sudo -n grep -l -E 'server_name[[:space:]]+${dom}[[:space:];]' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf 2>/dev/null" </dev/null || true)
  [ -n "$found" ] || die "no nginx vhost with server_name $M4_DOMAIN on $M4_EDGE_HOST"
  [ "$(wc -l <<<"$found")" -eq 1 ] || die "more than one vhost names $M4_DOMAIN on $M4_EDGE_HOST: $(tr '\n' ' ' <<<"$found")"
  printf '%s' "$found"
}
public_health() { curl -sS -m 10 "https://$M4_DOMAIN/relay/v1/health" 2>&1 || true; }
reload_edge() { m4_edge "sudo -n nginx -t" && m4_edge "sudo -n systemctl reload nginx"; }
put_back() { # put_back <backup path on the edge>: copy it over the vhost, test, reload, then retire the LATEST pointer
  m4_edge "sudo -n cp -p '$1' '$VHOST'" && reload_edge && m4_edge "sudo -n mv -f '$BACKUPS/LATEST' '$BACKUPS/LATEST.used'"
}
rollback_die() { # rollback_die <backup> <why>: put the backup back and say truthfully whether that worked
  if put_back "$1"; then die "$2. The previous vhost is back in place and nginx is reloaded."; fi
  die "$2. AND PUTTING THE BACKUP BACK FAILED: run  bash $M4_DIR/edge_vhost.sh restore  (backup $1 on $M4_EDGE_HOST)"
}

m4_edge "sudo -n true" </dev/null || die "cannot ssh to $M4_EDGE_HOST, or sudo -n does not work there"
VHOST=$(find_vhost)
say "vhost on $M4_EDGE_HOST: $VHOST"

if [ "$MODE" = check ]; then
  TARGET=${2:?usage: edge_vhost.sh check HOST:PORT}
  m4_edge "sudo -n nginx -t" >/dev/null 2>&1 </dev/null || die "nginx -t already fails on $M4_EDGE_HOST, before any change of ours"
  m4_edge "sudo -n cat '$VHOST'" >"$EVID/vhost-before.conf" </dev/null || die "could not read $VHOST"
  python3 "$M4_DIR/repoint_vhost.py" "$TARGET" "$M4_DOMAIN" <"$EVID/vhost-before.conf" >/dev/null || die "repoint_vhost.py would refuse $VHOST (reason above); fix the vhost by hand first"
  say "edge check passed: sudo works, nginx -t passes, one vhost, and it can be repointed to $TARGET"
  exit 0
fi

if [ "$MODE" = restore ]; then
  rc=0
  m4_edge "sudo -n test -e '$BACKUPS/LATEST'" </dev/null || rc=$?
  case $rc in
    0) ;;
    1) say "no unrestored backup in $BACKUPS/LATEST on $M4_EDGE_HOST: these scripts have not changed the vhost, or it is already restored. Nothing to do. public health: $(public_health)"; exit 0 ;;
    *) die "could not look for $BACKUPS/LATEST on $M4_EDGE_HOST (ssh exit $rc)" ;;
  esac
  backup=$(m4_edge "sudo -n cat '$BACKUPS/LATEST'" </dev/null) || die "could not read $BACKUPS/LATEST on $M4_EDGE_HOST"
  say "restoring $backup"
  put_back "$backup" || die "restore failed; the backup is $backup on $M4_EDGE_HOST"
  say "restored. public health now: $(public_health)"
  exit 0
fi

m4_srv_paths
serve_env=$(m1_ssh "cat '$M4_SRV_ROOT/serve.env'") || die "no serve.env on $M1_HOST: run serve_up.sh up first"
BIND=$(sed -n 's/^BIND=//p' <<<"$serve_env")
PORT=$(sed -n 's/^PORT=//p' <<<"$serve_env")
[ -n "$BIND" ] && [ -n "$PORT" ] || die "serve.env on $M1_HOST has no BIND/PORT"
TARGET=$BIND:$PORT

# The edge has to be able to reach the one command through the firewall before the vhost points at it.
reach=$(m4_edge "curl -sS -m 5 http://$TARGET/relay/v1/health" 2>&1 </dev/null || true)
if grep -q '"status": "ok"' <<<"$reach"; then
  say "the edge reaches http://$TARGET: $reach"
elif [ "$MODE" = apply ]; then
  die "the edge cannot reach http://$TARGET (got: $reach). Open that port to $M4_EDGE_HOST on $M1_HOST (firewall), or run serve_up.sh with another M4_PORT the firewall allows. Nothing was changed."
else
  say "WARNING: the edge cannot reach http://$TARGET (got: $reach); apply would refuse"
fi

m4_edge "sudo -n cat '$VHOST'" >"$EVID/vhost-before.conf" </dev/null || die "could not read $VHOST"
python3 "$M4_DIR/repoint_vhost.py" "$TARGET" "$M4_DOMAIN" <"$EVID/vhost-before.conf" >"$EVID/vhost-after.conf" || die "repoint_vhost.py refused this vhost; nothing was changed. Edit it by hand: every route goes to http://$TARGET"
if cmp -s "$EVID/vhost-before.conf" "$EVID/vhost-after.conf"; then
  say "the vhost already points at $TARGET; nothing to do. public health: $(public_health)"
  exit 0
fi
say "diff (before -> after), all routes now go to $TARGET:"
diff -u "$EVID/vhost-before.conf" "$EVID/vhost-after.conf" || true
[ "$MODE" = apply ] || { say "show only. Run: edge_vhost.sh apply"; exit 0; }

# A pointer still in LATEST means an earlier apply is live. Backing up now would save the CHANGED vhost as
# the "original", and restore (and teardown --restore-old) would bring that back.
if m4_edge "sudo -n test -e '$BACKUPS/LATEST'" </dev/null; then
  die "an earlier apply is still in place ($BACKUPS/LATEST on $M4_EDGE_HOST). Run  bash $M4_DIR/edge_vhost.sh restore  first, then apply again. Nothing was changed."
fi
stamp=$(date +%Y%m%d-%H%M%S)
backup=$BACKUPS/$M4_DOMAIN.$stamp
m4_edge "sudo -n mkdir -p '$BACKUPS' && sudo -n cp -p '$VHOST' '$backup' && printf '%s\n' '$backup' | sudo -n tee '$BACKUPS/LATEST' >/dev/null" </dev/null || die "could not back up $VHOST; nothing was changed"
say "backup: $backup (edge_vhost.sh restore puts it back)"
m4_edge "sudo -n tee '$VHOST' >/dev/null" <"$EVID/vhost-after.conf" || rollback_die "$backup" "could not write $VHOST"
reload_edge || rollback_die "$backup" "nginx rejected the new vhost"
sleep 2
health=$(public_health)
grep -q '"status": "ok"' <<<"$health" || rollback_die "$backup" "public health after the change was: $health"
say "applied. public health: $health"
