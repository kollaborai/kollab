#!/usr/bin/env bash
# ============================================================================
# Kollab Docker Runtime UI Smoke
# ============================================================================
# Drives the installed Docker runtime through tmux, the same surface a user
# sees. This is intentionally a custom tmux script instead of a JSON spec:
# it needs Docker volume seeding, optional credential checks, raw JSONL
# inspection, and multi-agent hub sessions.
#
# Usage:
#   tests/tmux/runtime_docker_ui_smoke.sh
#   KOLLAB_RUNTIME_SMOKE_CASES=clean,login-flow tests/tmux/runtime_docker_ui_smoke.sh
#   ANTHROPIC_AUTH_TOKEN=... tests/tmux/runtime_docker_ui_smoke.sh
#
# Optional env:
#   KOLLAB_DOCKER_IMAGE              Image tag, default kollab:local-runtime
#   KOLLAB_RUNTIME_SMOKE_CASES       all, or comma list:
#                                      clean,openai-env,login-flow,oauth-cache,
#                                      zai-profile,tools,hub
#   KOLLAB_RUNTIME_SMOKE_KEEP        1 keeps tmux sessions, containers, volumes
#   KOLLAB_RUNTIME_SMOKE_OAUTH_DIR   default ~/.kollab/oauth
#   ANTHROPIC_AUTH_TOKEN               enables Z.AI/Anthropic-compatible cases
#   ANTHROPIC_BASE_URL                 default https://api.z.ai/api/anthropic
#   ANTHROPIC_DEFAULT_OPUS_MODEL       default glm-5.1
# ============================================================================

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

IMAGE="${KOLLAB_DOCKER_IMAGE:-kollab:local-runtime}"
PREFIX="${KOLLAB_RUNTIME_SMOKE_PREFIX:-kollab-runtime-smoke-$$}"
SOCKET_NAME="${KOLLAB_RUNTIME_SMOKE_SOCKET:-$PREFIX}"
CASE_FILTER="${KOLLAB_RUNTIME_SMOKE_CASES:-all}"
KEEP="${KOLLAB_RUNTIME_SMOKE_KEEP:-0}"
OAUTH_DIR="${KOLLAB_RUNTIME_SMOKE_OAUTH_DIR:-$HOME/.kollab/oauth}"

TERM_WIDTH="${TERM_WIDTH:-120}"
TERM_HEIGHT="${TERM_HEIGHT:-35}"
APP_INIT_SLEEP="${APP_INIT_SLEEP:-5}"
KEY_DELAY="${KEY_DELAY:-0.04}"
SLASH_DELAY="${SLASH_DELAY:-0.7}"
WAIT_INTERVAL="${WAIT_INTERVAL:-2}"

TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0
LAST_CAPTURE=""

CONTAINERS=()
VOLUMES=()
SESSIONS=()

redact() {
    perl -pe '
        s/[[:xdigit:]]{32}\.[A-Za-z0-9._-]+/[TOKEN]/g;
        s/(api[_-]?key[=\": ]+)[^,}\s]+/${1}[REDACTED]/ig;
        s/(auth[_-]?token[=\": ]+)[^,}\s]+/${1}[REDACTED]/ig;
        s/(Authorization: Bearer )[A-Za-z0-9._-]+/${1}[REDACTED]/g;
        s/(OPENAI_API_KEY=).+/${1}[REDACTED]/g;
        s/(ANTHROPIC_AUTH_TOKEN=).+/${1}[REDACTED]/g;
        s/(x-api-key[=\": ]+)[^,}\s]+/${1}[REDACTED]/ig;
        s/\b[A-Z0-9]{4}-[A-Z0-9]{5}\b/[DEVICE-CODE]/g;
    '
}

log() {
    printf '%s\n' "$*"
}

section() {
    log ""
    log "=== $* ==="
}

pass() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    PASSED_TESTS=$((PASSED_TESTS + 1))
    log "[PASS] $*"
}

fail() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    FAILED_TESTS=$((FAILED_TESTS + 1))
    log "[FAIL] $*"
}

skip() {
    SKIPPED_TESTS=$((SKIPPED_TESTS + 1))
    log "[SKIP] $*"
}

should_run() {
    local name="$1"
    if [[ "$CASE_FILTER" == "all" || -z "$CASE_FILTER" ]]; then
        return 0
    fi
    case ",$CASE_FILTER," in
        *,"$name",*) return 0 ;;
        *) return 1 ;;
    esac
}

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log "[FATAL] required command not found: $cmd"
        exit 2
    fi
}

cleanup() {
    if [[ "$KEEP" == "1" ]]; then
        log ""
        log "[INFO] Keeping tmux socket, containers, and volumes:"
        log "  tmux socket: $SOCKET_NAME"
        printf '  containers: %s\n' "${CONTAINERS[*]:-none}"
        printf '  volumes:    %s\n' "${VOLUMES[*]:-none}"
        return
    fi

    tmux -L "$SOCKET_NAME" kill-server 2>/dev/null || true

    local c
    for c in "${CONTAINERS[@]}"; do
        docker rm -f "$c" >/dev/null 2>&1 || true
    done

    local v
    for v in "${VOLUMES[@]}"; do
        docker volume rm "$v" >/dev/null 2>&1 || true
    done
}
trap cleanup EXIT

make_volume() {
    local suffix="$1"
    local volume="${PREFIX}-${suffix}"
    docker volume rm "$volume" >/dev/null 2>&1 || true
    if docker volume create "$volume" >/dev/null; then
        VOLUMES+=("$volume")
        printf '%s\n' "$volume"
        return 0
    fi
    return 1
}

shell_quote_command() {
    local out=""
    local part
    for part in "$@"; do
        printf -v part '%q' "$part"
        out+="$part "
    done
    printf '%s\n' "$out"
}

start_session() {
    local session="$1"
    local container="$2"
    local volume="$3"
    local env_mode="$4"
    shift 4

    local cmd=(
        docker run --rm -it
        --name "$container"
        --mount "type=volume,src=${volume},dst=/home/kollab/.kollab"
        --mount "type=bind,src=${REPO_ROOT},dst=/workspace"
        --workdir /workspace
        --env TERM=xterm-256color
    )

    if [[ "$env_mode" == "openai" ]]; then
        cmd+=(--env OPENAI_API_KEY)
    fi

    cmd+=("$IMAGE" "$@")

    local shell_cmd
    shell_cmd="$(shell_quote_command "${cmd[@]}")"

    log "[ACTION] start_app: $session -> $container"
    tmux -L "$SOCKET_NAME" new-session -d -s "$session" \
        -c "$REPO_ROOT" -x "$TERM_WIDTH" -y "$TERM_HEIGHT" "$shell_cmd"
    SESSIONS+=("$session")
    CONTAINERS+=("$container")
    sleep "$APP_INIT_SLEEP"

    if ! tmux -L "$SOCKET_NAME" list-sessions 2>/dev/null | grep -q "^${session}:"; then
        fail "$session failed to start tmux session"
        return 1
    fi
    return 0
}

stop_session() {
    local session="$1"
    tmux -L "$SOCKET_NAME" kill-session -t "$session" 2>/dev/null || true
    sleep 1
}

capture() {
    local session="$1"
    local lines="${2:-260}"
    tmux -L "$SOCKET_NAME" capture-pane -t "$session" -p -S "-$lines" 2>/dev/null | redact
}

show_tail() {
    local session="$1"
    capture "$session" 180 | tail -60 | sed 's/^/    /'
}

wait_for_capture() {
    local session="$1"
    local pattern="$2"
    local timeout="${3:-60}"
    local elapsed=0

    while (( elapsed < timeout )); do
        LAST_CAPTURE="$(capture "$session")"
        if printf '%s\n' "$LAST_CAPTURE" | grep -qiE "$pattern"; then
            return 0
        fi
        sleep "$WAIT_INTERVAL"
        elapsed=$((elapsed + WAIT_INTERVAL))
    done

    LAST_CAPTURE="$(capture "$session")"
    return 1
}

assert_last_contains() {
    local pattern="$1"
    local description="$2"
    if printf '%s\n' "$LAST_CAPTURE" | grep -qiE "$pattern"; then
        pass "$description"
    else
        fail "$description"
        log "  expected pattern: $pattern"
        printf '%s\n' "$LAST_CAPTURE" | tail -80 | sed 's/^/    /'
    fi
}

type_text() {
    local session="$1"
    local text="$2"
    local delay="${3:-$KEY_DELAY}"
    local i char
    for (( i = 0; i < ${#text}; i++ )); do
        char="${text:i:1}"
        if [[ "$char" == " " ]]; then
            tmux -L "$SOCKET_NAME" send-keys -t "$session" Space
        else
            tmux -L "$SOCKET_NAME" send-keys -t "$session" -l "$char"
        fi
        sleep "$delay"
    done
}

send_prompt() {
    local session="$1"
    local prompt="$2"
    tmux -L "$SOCKET_NAME" send-keys -t "$session" C-u
    sleep 0.2
    type_text "$session" "$prompt" 0.025
    tmux -L "$SOCKET_NAME" send-keys -t "$session" Enter
}

send_slash() {
    local session="$1"
    local command="$2"
    tmux -L "$SOCKET_NAME" send-keys -t "$session" C-u
    sleep 0.2
    tmux -L "$SOCKET_NAME" send-keys -t "$session" -l "/"
    sleep "$SLASH_DELAY"
    type_text "$session" "$command" 0.04
    tmux -L "$SOCKET_NAME" send-keys -t "$session" Enter
}

send_choice() {
    local session="$1"
    local choice="$2"
    tmux -L "$SOCKET_NAME" send-keys -t "$session" "$choice"
}

run_volume_root() {
    local volume="$1"
    shift
    docker run --rm --user root \
        --mount "type=volume,src=${volume},dst=/target" \
        "$IMAGE" "$@"
}

seed_oauth_volume() {
    local volume="$1"
    local oauth_file="$OAUTH_DIR/openai.json"
    if [[ ! -s "$oauth_file" ]]; then
        return 1
    fi

    docker run --rm --user root \
        --mount "type=volume,src=${volume},dst=/target" \
        --mount "type=bind,src=${OAUTH_DIR},dst=/hostoauth,readonly" \
        "$IMAGE" sh -lc '
            mkdir -p /target/oauth
            cp /hostoauth/openai.json /target/oauth/openai.json
            chown -R kollab:kollab /target
            test -s /target/oauth/openai.json
        '
}

seed_zai_volume() {
    local volume="$1"
    if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
        return 1
    fi

    local base_url="${ANTHROPIC_BASE_URL:-https://api.z.ai/api/anthropic}"
    local model="${ANTHROPIC_DEFAULT_OPUS_MODEL:-glm-5.1}"

    docker run --rm --user root \
        --env ANTHROPIC_AUTH_TOKEN \
        --env "ANTHROPIC_BASE_URL=$base_url" \
        --env "ANTHROPIC_DEFAULT_OPUS_MODEL=$model" \
        --mount "type=volume,src=${volume},dst=/target" \
        "$IMAGE" python - <<'PY'
import json
import os
from pathlib import Path

target = Path("/target")
target.mkdir(parents=True, exist_ok=True)
config = {
    "application": {
        "name": "Kollab",
        "description": "AI Edition",
    },
    "kollabor": {
        "llm": {
            "max_history": 999,
            "save_conversations": True,
            "conversation_format": "jsonl",
            "show_status": True,
            "use_provider_system": True,
            "active_profile": "default",
            "profiles": {
                "default": {
                    "provider": "auto",
                    "model": "",
                    "temperature": 0.7,
                    "description": "Auto-detect from env vars, fallback to local LLM",
                },
                "local": {
                    "provider": "custom",
                    "base_url": "http://localhost:1234/v1",
                    "model": "qwen3.5-4b",
                    "temperature": 0.7,
                    "description": "Local LLM via LM Studio / Ollama",
                },
                "zai-anthropic": {
                    "provider": "anthropic",
                    "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
                    "model": os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                    "api_key": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                    "temperature": 0.7,
                    "timeout": 0,
                    "description": "Z.ai Anthropic-compatible GLM",
                },
            },
        }
    },
}
(target / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
PY

    run_volume_root "$volume" sh -lc 'chown -R kollab:kollab /target && test -s /target/config.json'
}

prepare_model_volume() {
    local volume="$1"
    WORKING_PROFILE=""
    WORKING_PROVIDER=""
    WORKING_MODEL=""

    if seed_zai_volume "$volume"; then
        WORKING_PROFILE="zai-anthropic"
        WORKING_PROVIDER="anthropic"
        WORKING_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-glm-5.1}"
        return 0
    fi

    if seed_oauth_volume "$volume"; then
        WORKING_PROFILE="openai-oauth"
        WORKING_PROVIDER="openai"
        WORKING_MODEL="gpt-5.4"
        return 0
    fi

    return 1
}

print_raw_diag() {
    local container="$1"
    docker exec "$container" sh -lc 'python - <<'"'"'PY'"'"'
import glob
import json

paths = sorted(glob.glob("/home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl"))
print(paths[-1] if paths else "NO_RAW")
if paths:
    rows = [json.loads(line) for line in open(paths[-1], encoding="utf-8") if line.strip()]
    for i, obj in enumerate(rows[-3:], 1):
        req = obj.get("request") or {}
        resp = obj.get("response") or {}
        # v1 schema moved messages -> conversation_local and put
        # model/provider under .profile. Fall back to v0 paths so the
        # smoke test renders both shapes.
        profile = obj.get("profile") or {}
        messages = (
            req.get("conversation_local")
            if isinstance(req, dict) and req.get("conversation_local") is not None
            else (req.get("messages") if isinstance(req, dict) else None)
        ) or []
        chars = sum(
            len(str((m or {}).get("content", "")))
            for m in messages
            if isinstance(m, dict)
        )
        print(json.dumps({
            "turn": i,
            "model": profile.get("model") or obj.get("model"),
            "provider": profile.get("provider") or obj.get("provider"),
            "error": obj.get("error"),
            "request_message_chars": chars,
            "tool_calls": resp.get("tool_calls") if isinstance(resp, dict) else None,
            "content": (resp.get("content") or "")[:160] if isinstance(resp, dict) else "",
        }, indent=2))
PY' 2>&1 | redact
}

raw_response_contains() {
    local container="$1"
    local needle="$2"
    docker exec -i "$container" python - "$needle" <<'PY'
import glob
import json
import sys

needle = sys.argv[1]
paths = sorted(glob.glob("/home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl"))
if not paths:
    print("NO_RAW")
    sys.exit(1)

rows = [json.loads(line) for line in open(paths[-1], encoding="utf-8") if line.strip()]
for obj in rows:
    resp = obj.get("response") or {}
    content = resp.get("content") if isinstance(resp, dict) else ""
    if needle in (content or ""):
        print(f"raw response contains: {needle}")
        sys.exit(0)

if rows and rows[-1].get("error"):
    print(f"RAW_ERROR: {rows[-1].get('error')}")
    sys.exit(2)

print(f"raw response missing: {needle}")
sys.exit(1)
PY
}

wait_raw_response_contains() {
    local container="$1"
    local needle="$2"
    local timeout="${3:-120}"
    local elapsed=0
    local output rc

    while (( elapsed < timeout )); do
        output="$(raw_response_contains "$container" "$needle" 2>&1)"
        rc=$?
        if [[ "$rc" -eq 0 ]]; then
            log "$output" | redact
            return 0
        fi
        if [[ "$rc" -eq 2 ]]; then
            log "$output" | redact
            return 2
        fi
        sleep "$WAIT_INTERVAL"
        elapsed=$((elapsed + WAIT_INTERVAL))
    done

    output="$(raw_response_contains "$container" "$needle" 2>&1)"
    log "$output" | redact
    return 1
}

assert_native_tool_raw() {
    local container="$1"
    docker exec "$container" sh -lc 'python - <<'"'"'PY'"'"'
import glob
import json
import sys

paths = sorted(glob.glob("/home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl"))
if not paths:
    print("NO_RAW")
    sys.exit(1)
for line in open(paths[-1], encoding="utf-8"):
    obj = json.loads(line)
    resp = obj.get("response") or {}
    for tc in resp.get("tool_calls") or []:
        name = tc.get("name") or tc.get("function", {}).get("name")
        data = tc.get("input") or {}
        if name in {"file_read", "file-read"} and data.get("file") == "README.md":
            print("native file_read README.md recorded")
            sys.exit(0)
print("native file_read README.md not found")
sys.exit(1)
PY'
}

assert_xml_tool_raw() {
    local container="$1"
    docker exec "$container" sh -lc 'python - <<'"'"'PY'"'"'
import glob
import json
import sys

paths = sorted(glob.glob("/home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl"))
if not paths:
    print("NO_RAW")
    sys.exit(1)
for line in open(paths[-1], encoding="utf-8"):
    obj = json.loads(line)
    resp = obj.get("response") or {}
    content = resp.get("content") or ""
    calls = resp.get("tool_calls") or []
    if "<read><file>pyproject.toml" in content and not calls:
        print("xml read pyproject.toml recorded without native tool_calls")
        sys.exit(0)
print("xml read pyproject.toml turn not found")
sys.exit(1)
PY'
}

test_clean_first_run() {
    should_run clean || return 0
    section "clean first run"
    local volume session container
    volume="$(make_volume clean)" || { fail "create clean volume"; return; }
    session="${PREFIX}-clean"
    container="${PREFIX}-clean"
    start_session "$session" "$container" "$volume" none kollab --no-daemon || return

    if wait_for_capture "$session" "LLM Provider Configuration Error|No provider could be auto-detected" 25; then
        pass "clean runtime surfaces missing-provider UI"
    else
        fail "clean runtime surfaces missing-provider UI"
        show_tail "$session"
    fi
    stop_session "$session"
}

test_openai_env() {
    should_run openai-env || return 0
    section "openai env auto profile"
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        skip "OPENAI_API_KEY not set; openai-env case skipped"
        return
    fi

    local volume session container
    volume="$(make_volume openai)" || { fail "create OpenAI env volume"; return; }
    session="${PREFIX}-openai"
    container="${PREFIX}-openai"
    start_session "$session" "$container" "$volume" openai kollab --no-daemon || return

    if wait_for_capture "$session" "openai-auto.*gpt|model: gpt|gpt-5" 35; then
        pass "OPENAI_API_KEY auto-detects openai-auto profile"
    else
        fail "OPENAI_API_KEY auto-detects openai-auto profile"
        show_tail "$session"
    fi

    send_prompt "$session" "Reply exactly: openai api key path works."
    if wait_raw_response_contains "$container" "openai api key path works." 120; then
        pass "OpenAI env prompt returns expected response"
    else
        fail "OpenAI env prompt returns expected response"
        show_tail "$session"
        print_raw_diag "$container" | sed 's/^/    /'
    fi
    stop_session "$session"
}

test_login_flow() {
    should_run login-flow || return 0
    section "fresh /login openai flow"
    local volume session container
    volume="$(make_volume login)" || { fail "create login volume"; return; }
    session="${PREFIX}-login"
    container="${PREFIX}-login"
    start_session "$session" "$container" "$volume" none kollab --no-daemon || return

    send_slash "$session" "login openai"
    if wait_for_capture "$session" "OpenAI OAuth Login" 30; then
        pass "/login openai opens device-code login UI"
    else
        fail "/login openai opens device-code login UI"
        show_tail "$session"
    fi

    if wait_for_capture "$session" "auth.openai.com/codex/device" 45; then
        pass "/login openai shows device auth URL"
    else
        fail "/login openai shows device auth URL"
        show_tail "$session"
    fi

    tmux -L "$SOCKET_NAME" send-keys -t "$session" Escape
    if wait_for_capture "$session" "login cancelled" 15; then
        pass "/login openai can be cancelled cleanly"
    else
        fail "/login openai can be cancelled cleanly"
        show_tail "$session"
    fi
    stop_session "$session"
}

test_oauth_cache() {
    should_run oauth-cache || return 0
    section "oauth cache"
    if [[ ! -s "$OAUTH_DIR/openai.json" ]]; then
        skip "OpenAI OAuth token not found at $OAUTH_DIR/openai.json"
        return
    fi

    local volume session container
    volume="$(make_volume oauth)" || { fail "create OAuth cache volume"; return; }
    if ! seed_oauth_volume "$volume"; then
        fail "seed OAuth token into disposable volume"
        return
    fi
    session="${PREFIX}-oauth"
    container="${PREFIX}-oauth"
    start_session "$session" "$container" "$volume" none kollab --profile openai-oauth --no-daemon || return

    send_slash "$session" "login status"
    if wait_for_capture "$session" "openai: authenticated" 30; then
        pass "cached OAuth token authenticates without host bind mount"
    else
        fail "cached OAuth token authenticates without host bind mount"
        show_tail "$session"
    fi

    send_prompt "$session" "Reply exactly: oauth runtime smoke works."
    if wait_raw_response_contains "$container" "oauth runtime smoke works." 120; then
        pass "OAuth profile prompt returns expected response"
    else
        fail "OAuth profile prompt returns expected response"
        show_tail "$session"
        print_raw_diag "$container" | sed 's/^/    /'
    fi

    stop_session "$session"
    session="${PREFIX}-oauth-restart"
    container="${PREFIX}-oauth-restart"
    start_session "$session" "$container" "$volume" none kollab --profile openai-oauth --no-daemon || return
    send_slash "$session" "login status"
    if wait_for_capture "$session" "openai: authenticated" 30; then
        pass "cached OAuth token survives runtime restart"
    else
        fail "cached OAuth token survives runtime restart"
        show_tail "$session"
    fi

    LAST_CAPTURE="$(capture "$session")"
    if printf '%s\n' "$LAST_CAPTURE" | grep -qiE "kollabor -> kollabor|PERMISSION REQUIRED.*hub_msg|Tool\\(hub_msg\\)"; then
        fail "OAuth restart does not trigger stale hub self-message"
        show_tail "$session"
        if printf '%s\n' "$LAST_CAPTURE" | grep -qi "PERMISSION REQUIRED"; then
            send_choice "$session" d
        fi
    else
        pass "OAuth restart does not trigger stale hub self-message"
    fi
    stop_session "$session"
}

test_zai_profile() {
    should_run zai-profile || return 0
    section "zai anthropic-compatible profile"
    if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
        skip "ANTHROPIC_AUTH_TOKEN not set; zai-profile case skipped"
        return
    fi

    local volume session container model
    model="${ANTHROPIC_DEFAULT_OPUS_MODEL:-glm-5.1}"
    volume="$(make_volume zai)" || { fail "create Z.AI volume"; return; }
    if ! seed_zai_volume "$volume"; then
        fail "seed Z.AI profile into disposable volume"
        return
    fi
    session="${PREFIX}-zai"
    container="${PREFIX}-zai"
    start_session "$session" "$container" "$volume" none kollab --profile default --no-daemon || return

    send_slash "$session" "profile zai-anthropic"
    if wait_for_capture "$session" "Switched to profile: zai-anthropic|zai-anthropic.*${model}" 35; then
        pass "/profile switches to zai-anthropic"
        assert_last_contains "Provider: anthropic|zai-anthropic" "Z.AI profile shows anthropic provider"
        assert_last_contains "$model" "Z.AI profile shows configured model"
    else
        fail "/profile switches to zai-anthropic"
        show_tail "$session"
    fi

    send_prompt "$session" "Reply exactly: zai ui profile works."
    if wait_raw_response_contains "$container" "zai ui profile works." 120; then
        pass "Z.AI profile prompt returns expected response"
    else
        fail "Z.AI profile prompt returns expected response"
        show_tail "$session"
        print_raw_diag "$container" | sed 's/^/    /'
    fi
    stop_session "$session"
}

test_tools() {
    should_run tools || return 0
    section "native and xml tool calling"
    local volume session container
    volume="$(make_volume tools)" || { fail "create tools volume"; return; }
    if ! prepare_model_volume "$volume"; then
        skip "No working model credential found for tools case"
        return
    fi

    session="${PREFIX}-tools"
    container="${PREFIX}-tools"
    start_session "$session" "$container" "$volume" none kollab --profile "$WORKING_PROFILE" --no-daemon || return

    send_prompt "$session" "Use a native API tool call, not XML text, to read README.md. Then answer exactly: NATIVE_READ_OK: followed by the first markdown heading."
    if wait_for_capture "$session" "PERMISSION REQUIRED|Read\\(README.md\\)|file_read\\(README.md\\)" 120; then
        if printf '%s\n' "$LAST_CAPTURE" | grep -qiE "Read\\(README.md\\)|file_read\\(README.md\\)"; then
            pass "native tool call reaches README read permission/result"
            if printf '%s\n' "$LAST_CAPTURE" | grep -qi "PERMISSION REQUIRED"; then
                send_choice "$session" a
            fi
            if wait_raw_response_contains "$container" "NATIVE_READ_OK: # Kollab" 120; then
                pass "native tool call completes expected answer"
            else
                fail "native tool call completes expected answer"
                show_tail "$session"
                print_raw_diag "$container" | sed 's/^/    /'
            fi
        else
            fail "native tool call reaches README read permission/result"
            show_tail "$session"
        fi
    else
        fail "native tool call reaches README read permission/result"
        show_tail "$session"
    fi

    if assert_native_tool_raw "$container" >/tmp/kollabor-native-tool-$$.log 2>&1; then
        pass "raw JSONL records native file_read tool_call"
    else
        fail "raw JSONL records native file_read tool_call"
        sed 's/^/    /' /tmp/kollabor-native-tool-$$.log
        print_raw_diag "$container" | sed 's/^/    /'
    fi
    rm -f /tmp/kollabor-native-tool-$$.log

    send_prompt "$session" "Use XML tool syntax, not native tool calls. Emit a read tag for pyproject.toml with limit 5, then after the tool result answer exactly: XML_READ_OK: [build-system]"
    if wait_for_capture "$session" "PERMISSION REQUIRED|Read\\(pyproject.toml\\)|file_read\\(pyproject.toml\\)" 120; then
        if printf '%s\n' "$LAST_CAPTURE" | grep -qiE "Read\\(pyproject.toml\\)|file_read\\(pyproject.toml\\)"; then
            pass "XML read reaches pyproject permission/result"
            if printf '%s\n' "$LAST_CAPTURE" | grep -qi "PERMISSION REQUIRED"; then
                send_choice "$session" a
            fi
            if wait_raw_response_contains "$container" "XML_READ_OK: [build-system]" 120; then
                pass "XML tool call completes expected answer"
            else
                fail "XML tool call completes expected answer"
                show_tail "$session"
                print_raw_diag "$container" | sed 's/^/    /'
            fi
        else
            fail "XML read reaches pyproject permission/result"
            show_tail "$session"
        fi
    else
        fail "XML read reaches pyproject permission/result"
        show_tail "$session"
    fi

    if assert_xml_tool_raw "$container" >/tmp/kollabor-xml-tool-$$.log 2>&1; then
        pass "raw JSONL records XML read without native tool_calls"
    else
        fail "raw JSONL records XML read without native tool_calls"
        sed 's/^/    /' /tmp/kollabor-xml-tool-$$.log
        print_raw_diag "$container" | sed 's/^/    /'
    fi
    rm -f /tmp/kollabor-xml-tool-$$.log
    stop_session "$session"
}

test_hub() {
    should_run hub || return 0
    section "hub status and message"
    local volume primary peer primary_container peer_container
    volume="$(make_volume hub)" || { fail "create hub volume"; return; }
    if ! prepare_model_volume "$volume"; then
        skip "No working model credential found for hub case"
        return
    fi

    primary="${PREFIX}-hub-primary"
    peer="${PREFIX}-hub-peer"
    primary_container="${PREFIX}-hub-primary"
    peer_container="${PREFIX}-hub-peer"

    start_session "$primary" "$primary_container" "$volume" none kollab --profile "$WORKING_PROFILE" --no-daemon || return
    start_session "$peer" "$peer_container" "$volume" none kollab --agent coder --as lapis --profile "$WORKING_PROFILE" --no-daemon || return

    send_slash "$primary" "hub status"
    if wait_for_capture "$primary" "2 agent\\(s\\) online|lapis" 45; then
        pass "/hub status sees primary and lapis peer"
    else
        fail "/hub status sees primary and lapis peer"
        show_tail "$primary"
    fi

    send_slash "$primary" "hub msg lapis runtime smoke hub ping"
    if wait_for_capture "$primary" "sent to lapis|delivered to lapis|kollabor -> lapis" 45; then
        pass "/hub msg reports delivery to lapis"
    else
        fail "/hub msg reports delivery to lapis"
        show_tail "$primary"
    fi

    if wait_for_capture "$peer" "runtime smoke hub ping" 45; then
        pass "lapis peer displays incoming hub message"
    else
        fail "lapis peer displays incoming hub message"
        show_tail "$peer"
    fi

    if capture "$primary" | grep -qi "PERMISSION REQUIRED"; then
        send_choice "$primary" d
    fi
    if capture "$peer" | grep -qi "PERMISSION REQUIRED"; then
        send_choice "$peer" d
    fi
    stop_session "$peer"
    stop_session "$primary"
}

main() {
    log "=============================================="
    log "Kollab Docker Runtime UI Smoke"
    log "=============================================="
    log "Repo:    $REPO_ROOT"
    log "Image:   $IMAGE"
    log "Socket:  $SOCKET_NAME"
    log "Cases:   $CASE_FILTER"
    log ""

    require_cmd docker
    require_cmd tmux
    require_cmd perl

    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        log "[FATAL] Docker image not found: $IMAGE"
        log "        Build it with: scripts/docker-runtime.sh build"
        exit 2
    fi

    test_clean_first_run
    test_openai_env
    test_login_flow
    test_oauth_cache
    test_zai_profile
    test_tools
    test_hub

    log ""
    log "=============================================="
    log "Runtime UI Smoke Summary"
    log "=============================================="
    log "Assertions: $TOTAL_TESTS"
    log "Passed:     $PASSED_TESTS"
    log "Failed:     $FAILED_TESTS"
    log "Skipped:    $SKIPPED_TESTS"
    log ""

    if [[ "$FAILED_TESTS" -eq 0 ]]; then
        log "Result: PASS"
        exit 0
    fi

    log "Result: FAIL"
    exit 1
}

main "$@"
