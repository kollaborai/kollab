# Kill switch proof harness (Docker only)

Every halt, resume, revoke, sweep and listener runs inside these containers.
Never run `kollab --hub halt|resume` or `--halt-listener` on a real machine:
a halt is machine-wide across every HOME.

Pieces:

- `Dockerfile`: this checkout installed into `/opt/kollab` (a dev install;
  console script under `sys.prefix`), plus procps, lsof, tmux.
- `tls_proxy.py`: throwaway CA in `/certs`, TLS on :443 in front of
  `kollab relay serve` (the relay itself speaks plain HTTP).
- `seed_home.sh <home> <name>`: a machine HOME that trusts `/certs/ca.pem`,
  may dial the docker subnet, skips the wizard and the connect guide.
- `ks.sh`: in-container helpers (`daemon`, `tui`, `type`, `screen`,
  `proxy <connect_proxy op>`, `proxy rpc <state.method>`, `procs`).
- `proxy.py`: one `connect_proxy` op or handshake-phase state RPC on the
  folder's live agent (`offer`, `decide`, `snapshot`, `halt_snapshot`,
  `halt_act`, `rpc state.hub_enroll`).

Bring it up (from the repo root; `S` is any scratch dir for the CA):

```sh
docker build -f tests/docker/kill_switch/Dockerfile -t kollab-killswitch:dev .
docker network create --subnet 10.231.77.0/24 ks-net
H=$PWD/tests/docker/kill_switch
docker run -d --rm --init --name ks-relay --network ks-net --network-alias relay.ks.test \
  -v $H:/harness:ro -v $S/certs:/certs kollab-killswitch:dev \
  sh -c 'python /harness/tls_proxy.py relay.ks.test 8078 /certs & exec kollab relay serve --domain relay.ks.test --port 8078'
for m in m1 m2 m3; do
  docker run -d --rm --init --name ks-$m --hostname $m --network ks-net \
    -v $H:/harness:ro -v $S/certs:/certs:ro kollab-killswitch:dev sleep infinity
  docker exec ks-$m sh /harness/seed_home.sh /root $m
done
# founder
docker exec ks-m1 sh -c 'sh /harness/ks.sh daemon && sh /harness/ks.sh tui && sh /harness/ks.sh type "/connect relay.ks.test" 12'
# join code on the founder, joiner submits it, founder accepts
docker exec ks-m1 sh /harness/ks.sh proxy offer '{"domain": "relay.ks.test"}'          # -> code
docker exec ks-m3 sh -c 'sh /harness/ks.sh daemon; sh /harness/ks.sh proxy rpc state.hub_enroll "{\"domain\": \"relay.ks.test\", \"code\": \"<code>\"}"'
docker exec ks-m1 sh /harness/ks.sh proxy snapshot                                      # -> requests[0].enrollment_id
docker exec ks-m1 sh /harness/ks.sh proxy decide '{"enrollment_id": "<id>", "decision": "accept"}'
docker exec ks-m3 sh /harness/ks.sh proxy halt_snapshot                                  # counts: true (bootstrap)
```

Codes never go in a typed command (`/connect <domain> <code>` is refused by
design), and the guided "Join with a code" form is fixed to kollabor.ai, so the
joiner submits through `state.hub_enroll` over its hub socket.

Tear down: `docker stop ks-relay ks-m1 ks-m2 ks-m3` (`--rm` removes them),
`docker network rm ks-net`, `docker rmi kollab-killswitch:dev`.
