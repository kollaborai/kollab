#!/usr/bin/env bash
# verify_serve.sh: what an outsider sees at https://selfhost.kollabor.ai once the one command runs and the
# edge points at it (serve_up.sh up, then edge_vhost.sh apply). Read-only; runs on this Mac against the
# public internet, with the installed m1 venv's client code for the discovery and websocket checks.
#
# PASS/FAIL per check, exit 0 only if all pass. Evidence: m4/evidence/verify-*.txt.
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
EVID=$M4_DIR/evidence
mkdir -p "$EVID"
exec > >(tee "$EVID/verify.txt") 2>&1
URL=https://$M4_DOMAIN
FAILS=0
ok() { printf 'PASS  %s\n' "$*"; }
bad() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
code_of() { curl -sS -o /dev/null -m 15 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }
[ -x "$M1_MAC_VENV/bin/python" ] || die "no m1 venv on this Mac: run m1/install_both.sh"

# 1. DNS: the one TXT record
txt=$(dig +short TXT "_agent.$M4_DOMAIN" 2>/dev/null | tr -d '"' || true)
printf '%s\n' "$txt" | scrub >"$EVID/verify-dns.txt"
if grep -Eq "v=aid1;u=$URL/\.well-known/agent-keys(\.json)?" <<<"$txt"; then ok "dns: _agent.$M4_DOMAIN TXT selects $URL ($(scrub <<<"$txt"))"; else bad "dns: _agent.$M4_DOMAIN TXT is '$(scrub <<<"$txt")'"; fi

# 2. discovery exactly as a client does it (DNS, TLS, signature), and the identity did not change
disc=$(cd "$HOME" && "$M1_MAC_VENV/bin/python" - "$M4_DOMAIN" <<'PY' 2>&1
import asyncio, json, sys

from plugins.hub.dns.discovery import discover

result = asyncio.run(discover(sys.argv[1]))
m = result.manifest
print(json.dumps({"key": m["coordinator"]["public_key"], "revision": m["revision"], "roles": m["discovery"]["roles"],
                  "control": m["endpoints"].get("control"), "summary": result.summary().replace("\n", " | ")}))
PY
) || true
printf '%s\n' "$disc" | scrub >"$EVID/verify-discovery.txt"
if python3 - "$URL" "$disc" <<'PY'
import json, sys

d = json.loads(sys.argv[2].splitlines()[-1])
assert d["control"] == sys.argv[1] + "/relay/v1", d
assert "relay" in d["roles"] and "rendezvous" in d["roles"], d
PY
then ok "discovery: verified by the client code, relay advertised at $URL/relay/v1"; else bad "discovery: $(scrub <<<"$disc")"; fi
if [ -f "$EVID/pre.env" ]; then
  # shellcheck disable=SC1091
  . "$EVID/pre.env"
  if python3 - "$PRE_KEY" "$PRE_REVISION" "$disc" <<'PY'
import json, sys

d = json.loads(sys.argv[3].splitlines()[-1])
assert d["key"] == sys.argv[1], "publisher key changed"
assert d["revision"] > int(sys.argv[2]), "revision did not advance"
PY
  then ok "identity: same publisher key as before the change, revision advanced past $PRE_REVISION"; else bad "identity: the publisher key or revision differs from evidence/pre.env: $(scrub <<<"$disc")"; fi
else
  echo "SKIP  identity: no evidence/pre.env (serve_up.sh up has not run from this checkout)"
fi

# 3. the routes
health=$(curl -sS -m 15 "$URL/relay/v1/health" 2>&1 || true)
printf '%s\n' "$health" >"$EVID/verify-health.txt"
if grep -q '"status": "ok"' <<<"$health" && grep -q "\"origin\": \"$URL\"" <<<"$health"; then ok "health: $health"; else bad "health: $health"; fi
c=$(code_of "$URL/relay/v1/metrics"); if [ "$c" = 404 ] || [ "$c" = 403 ]; then ok "metrics are not public (HTTP $c)"; else bad "metrics answered HTTP $c"; fi
c=$(code_of "$URL/"); if [ "$c" = 404 ]; then ok "anything else is 404"; else bad "GET / answered HTTP $c"; fi
for route in enrollment contact; do
  c=$(code_of -X POST -H 'content-type: application/json' -d '{}' "$URL/relay/v1/$route/lookup")
  if [ "$c" = 400 ]; then ok "POST /relay/v1/$route/lookup reaches the relay (HTTP 400 on an empty body)"; else bad "POST /relay/v1/$route/lookup answered HTTP $c"; fi
done
c=$(code_of "$URL/relay/v1/enrollment/lookup"); if [ "$c" = 403 ] || [ "$c" = 405 ]; then ok "GET on the enrollment prefix is refused (HTTP $c)"; else bad "GET /relay/v1/enrollment/lookup answered HTTP $c"; fi

# 4. the websocket upgrades and the relay speaks first
ws=$(cd "$HOME" && "$M1_MAC_VENV/bin/python" - "$M4_DOMAIN" <<'PY' 2>&1
import asyncio, json, sys

import aiohttp


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"wss://{sys.argv[1]}/relay/v1/ws", timeout=10) as ws:
            frame = json.loads((await asyncio.wait_for(ws.receive(), 10)).data)
            print(frame.get("type"), frame.get("origin"))


asyncio.run(main())
PY
) || true
printf '%s\n' "$ws" >"$EVID/verify-ws.txt"
if [ "$ws" = "challenge $URL" ]; then ok "websocket upgrades and the relay sends its challenge for $URL"; else bad "websocket: $(scrub <<<"$ws")"; fi

if [ "$FAILS" -eq 0 ]; then say "all checks passed"; else say "$FAILS check(s) failed"; exit 1; fi
