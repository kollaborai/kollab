#!/usr/bin/env bash
# edge_vhost.sh [show|apply|restore]: point the edge nginx vhost for selfhost.kollabor.ai at the one command.
#
# Today the vhost forwards to three things on alzan-prod (10.0.0.5): the relay workers (9178, 9179), the
# supervisor's health port (9180) and a static server for the key file (9177). `kollab relay serve` answers
# all five routes on one port, so the vhost must name that port instead. serve_up.sh wrote it to
# ~/kollab-m4/serve.env on alzan-prod. This changes ONLY the upstream server lines and the direct proxy_pass
# lines (repoint_vhost.py refuses anything it does not understand). It runs on alzan-edge over ssh with sudo -n.
#
#   show     (default) read-only: the vhost as it is, and the diff apply would make.
#   apply    back the vhost up to /etc/nginx/m4-backups/, write the change, `nginx -t`, reload, then check
#            the public health route. Any failure puts the backup back and reloads.
#   restore  put the most recent backup back, `nginx -t`, reload.
#
# The public domain is down from `serve_up.sh up` until `apply` has run (or until the old stack is restored).
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
MODE=${1:-show}
case "$MODE" in show | apply | restore) ;; *) die "usage: edge_vhost.sh [show|apply|restore]" ;; esac
EVID=$M4_DIR/evidence
mkdir -p "$EVID"
m4_srv_paths
BACKUPS=/etc/nginx/m4-backups

find_vhost() {
  local found
  found=$(m4_edge "sudo -n grep -l -E 'server_name[[:space:]]+${M4_DOMAIN}[[:space:];]' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf 2>/dev/null" || true)
  [ -n "$found" ] || die "no nginx vhost with server_name $M4_DOMAIN on $M4_EDGE_HOST"
  [ "$(wc -l <<<"$found")" -eq 1 ] || die "more than one vhost names $M4_DOMAIN on $M4_EDGE_HOST: $(tr '\n' ' ' <<<"$found")"
  printf '%s' "$found"
}
public_health() { curl -sS -m 10 "https://$M4_DOMAIN/relay/v1/health" 2>&1 || true; }
reload_edge() { m4_edge "sudo -n nginx -t" && m4_edge "sudo -n systemctl reload nginx"; }
put_back() { # put_back <backup path on the edge>
  m4_edge "sudo -n cp -p '$1' '$VHOST'" && reload_edge
}

VHOST=$(find_vhost)
say "vhost on $M4_EDGE_HOST: $VHOST"

if [ "$MODE" = restore ]; then
  backup=$(m4_edge "sudo -n cat '$BACKUPS/LATEST'") || die "no backup recorded in $BACKUPS/LATEST on $M4_EDGE_HOST"
  say "restoring $backup"
  put_back "$backup" || die "restore failed; the backup is $backup on $M4_EDGE_HOST"
  say "restored. public health now: $(public_health)"
  exit 0
fi

serve_env=$(m1_ssh "cat '$M4_SRV_ROOT/serve.env'") || die "no serve.env on $M1_HOST: run serve_up.sh up first"
BIND=$(sed -n 's/^BIND=//p' <<<"$serve_env")
PORT=$(sed -n 's/^PORT=//p' <<<"$serve_env")
[ -n "$BIND" ] && [ -n "$PORT" ] || die "serve.env on $M1_HOST has no BIND/PORT"
TARGET=$BIND:$PORT

m4_edge "sudo -n cat '$VHOST'" >"$EVID/vhost-before.conf" || die "could not read $VHOST"
python3 "$M4_DIR/repoint_vhost.py" "$TARGET" <"$EVID/vhost-before.conf" >"$EVID/vhost-after.conf" || die "repoint_vhost.py refused this vhost; nothing was changed. Edit it by hand: every route goes to http://$TARGET"
if cmp -s "$EVID/vhost-before.conf" "$EVID/vhost-after.conf"; then
  say "the vhost already points at $TARGET; nothing to do. public health: $(public_health)"
  exit 0
fi
say "diff (before -> after), all routes now go to $TARGET:"
diff -u "$EVID/vhost-before.conf" "$EVID/vhost-after.conf" || true
[ "$MODE" = apply ] || { say "show only. Run: edge_vhost.sh apply"; exit 0; }

stamp=$(date +%Y%m%d-%H%M%S)
backup=$BACKUPS/$M4_DOMAIN.$stamp
m4_edge "sudo -n mkdir -p '$BACKUPS' && sudo -n cp -p '$VHOST' '$backup' && printf '%s\n' '$backup' | sudo -n tee '$BACKUPS/LATEST' >/dev/null" || die "could not back up $VHOST; nothing was changed"
say "backup: $backup (edge_vhost.sh restore puts it back)"
m4_edge "sudo -n tee '$VHOST' >/dev/null" <"$EVID/vhost-after.conf" || { put_back "$backup" || true; die "could not write $VHOST"; }
if ! reload_edge; then
  put_back "$backup" || true
  die "nginx rejected the new vhost; the backup is back in place"
fi
sleep 2
health=$(public_health)
if ! grep -q '"status": "ok"' <<<"$health"; then
  put_back "$backup" || true
  die "public health after the change: $health -- put the backup back"
fi
say "applied. public health: $health"
