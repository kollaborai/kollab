#!/bin/sh
# Agents in a throwaway HOME for specs/hub_arrival_wake*.json. ws/ and ws2/ are two project
# folders (FOLDER=ws2 picks the second one; the default is ws).
#
#   hub_arrival_probe.sh setup [v] [g]        a new HOME with a fake model and the global arrival_wake
#                                             g (default all); ws/ gets the folder value v (default
#                                             none, "-" for no folder file); its path goes to the
#                                             .path file
#   hub_arrival_probe.sh <home> join <name>   start a detached agent, wait until it is online
#   hub_arrival_probe.sh <home> pongs <name>  model replies <name> has shown (the fake model says "pong: ...")
#   hub_arrival_probe.sh <home> folder <v>    rewrite the folder's plugins.hub.arrival_wake
#   hub_arrival_probe.sh <home> set <where>   print the arrival_wake a config file holds: global, ws or ws2
#   hub_arrival_probe.sh <home> stop          stop every agent and the fake model
R=$(cd "$(dirname "$0")/../../.." && pwd)
PATH_FILE="${TMPDIR:-/tmp}/kollab-spec-arrival-wake.path"

if [ "$1" = setup ]; then
  [ -s "$PATH_FILE" ] && [ -d "$(cat "$PATH_FILE")/ws" ] && sh "$0" "$(cat "$PATH_FILE")" stop >/dev/null 2>&1
  H=$(mktemp -d "${TMPDIR:-/tmp}/kollab-spec-arrival-wake.XXXXXX")
  printf %s "$H" > "$PATH_FILE"
  mkdir -p "$H/.kollab" "$H/ws/.kollab" "$H/ws2"
  touch "$H/.kollab/connect-guide-seen"
  ("$R/.venv/bin/python" "$R/tests/tmux/lib/fake_llm.py" "$H/fake.port" >/dev/null 2>&1 & echo $! > "$H/fake.pid")
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$H/fake.port" ] && break; sleep 0.3; done
  printf '{"application":{"setup_completed":true},"plugins":{"hub":{"arrival_wake":"%s"}},"kollabor":{"llm":{"active_profile":"fake","profiles":{"fake":{"provider":"custom","model":"fake-echo","base_url":"http://127.0.0.1:%s/v1","api_key":"not-a-real-key"}}}}}' "${3:-all}" "$(cat "$H/fake.port")" > "$H/.kollab/config.json"
  [ "${2:-none}" = - ] || sh "$0" "$H" folder "${2:-none}"
  echo "throwaway HOME $H, fake model on port $(cat "$H/fake.port")"
  exit 0
fi

H=$1
cmd=$2
arg=$3
FOLDER=${FOLDER:-ws}
kollab() { (cd "$H/$FOLDER" && HOME="$H" KOLLAB_NO_KEYRING=1 "$R/.venv/bin/python" "$R/main.py" "$@"); }

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
  folder)
    mkdir -p "$H/$FOLDER/.kollab"
    printf '{"plugins":{"hub":{"arrival_wake":"%s"}}}' "$arg" > "$H/$FOLDER/.kollab/config.json"
    ;;
  set)
    file="$H/$arg/.kollab/config.json"
    [ "$arg" = global ] && file="$H/.kollab/config.json"
    echo "$arg: $(tr -d ' \n' < "$file" 2>/dev/null | grep -o '"arrival_wake":"[a-z]*"' || echo 'no arrival_wake')"
    exit 0
    ;;
  stop)
    for FOLDER in ws ws2; do kollab --hub stop all 2>&1 | tail -1; done
    kill "$(cat "$H/fake.pid")" 2>/dev/null
    exit 0
    ;;
esac
