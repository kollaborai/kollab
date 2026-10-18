#!/bin/sh
# Agents in a throwaway HOME for specs/hub_arrival_wake.json: its ws/ folder is the project.
#
#   hub_arrival_probe.sh setup               a new HOME with a fake model, the global arrival_wake "all"
#                                            and the folder's "none"; its path goes to the .path file
#   hub_arrival_probe.sh <home> join <name>  start a detached agent, wait until it is online
#   hub_arrival_probe.sh <home> pongs <name> model replies <name> has shown (the fake model says "pong: ...")
#   hub_arrival_probe.sh <home> folder <v>   rewrite the folder's plugins.hub.arrival_wake
#   hub_arrival_probe.sh <home> stop         stop every agent and the fake model
R=$(cd "$(dirname "$0")/../../.." && pwd)
PATH_FILE="${TMPDIR:-/tmp}/kollab-spec-arrival-wake.path"

if [ "$1" = setup ]; then
  [ -s "$PATH_FILE" ] && [ -d "$(cat "$PATH_FILE")/ws" ] && sh "$0" "$(cat "$PATH_FILE")" stop >/dev/null 2>&1
  H=$(mktemp -d "${TMPDIR:-/tmp}/kollab-spec-arrival-wake.XXXXXX")
  printf %s "$H" > "$PATH_FILE"
  mkdir -p "$H/.kollab" "$H/ws/.kollab"
  touch "$H/.kollab/connect-guide-seen"
  ("$R/.venv/bin/python" "$R/tests/tmux/lib/fake_llm.py" "$H/fake.port" >/dev/null 2>&1 & echo $! > "$H/fake.pid")
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$H/fake.port" ] && break; sleep 0.3; done
  printf '{"application":{"setup_completed":true},"plugins":{"hub":{"arrival_wake":"all"}},"kollabor":{"llm":{"active_profile":"fake","profiles":{"fake":{"provider":"custom","model":"fake-echo","base_url":"http://127.0.0.1:%s/v1","api_key":"not-a-real-key"}}}}}' "$(cat "$H/fake.port")" > "$H/.kollab/config.json"
  sh "$0" "$H" folder none
  echo "throwaway HOME $H, fake model on port $(cat "$H/fake.port")"
  exit 0
fi

H=$1
cmd=$2
arg=$3
kollab() { (cd "$H/ws" && HOME="$H" KOLLAB_NO_KEYRING=1 "$R/.venv/bin/python" "$R/main.py" "$@"); }

case $cmd in
  join)
    kollab --detached --as "$arg" >/dev/null 2>&1
    for _ in $(seq 1 30); do
      kollab --hub status 2>&1 | grep -q "^ *$arg " && { echo "$arg online"; exit 0; }
      sleep 1
    done
    echo "$arg never came online"
    exit 1
    ;;
  pongs) kollab --hub capture "$arg" 60 2>&1 | grep -c 'pong:'; exit 0 ;;
  folder) printf '{"plugins":{"hub":{"arrival_wake":"%s"}}}' "$arg" > "$H/ws/.kollab/config.json" ;;
  stop)
    kollab --hub stop all 2>&1 | tail -1
    kill "$(cat "$H/fake.pid")" 2>/dev/null
    exit 0
    ;;
esac
