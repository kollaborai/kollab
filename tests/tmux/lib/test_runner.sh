#!/usr/bin/env bash
# JSON-driven tmux test runner for Kollab terminal UI tests.
#
# Usage:
#   tests/tmux/lib/test_runner.sh tests/tmux/specs/test_tmux_simple.json
#   SHOW_CAPTURES=true tests/tmux/lib/test_runner.sh tests/tmux/specs/test_tmux_simple.json

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <spec.json>" >&2
  exit 2
fi

SPEC_PATH="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

python3 - "$SPEC_PATH" "$REPO_ROOT" <<'PY'
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SPEC_PATH = Path(sys.argv[1]).resolve()
REPO_ROOT = Path(sys.argv[2]).resolve()


def _load_spec(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[FAIL] unable to read spec {path}: {exc}", file=sys.stderr)
        sys.exit(2)


SPEC = _load_spec(SPEC_PATH)
CONFIG = SPEC.get("config") or {}
NAME = str(SPEC.get("name") or SPEC_PATH.stem)
SAFE_NAME = re.sub(r"[^A-Za-z0-9_.-]+", "-", NAME).strip("-") or "kollab-test"
SOCKET_NAME = f"kollab-test-{os.getpid()}-{SAFE_NAME}"[:90]
SESSION_NAME = f"{SAFE_NAME}-{os.getpid()}"[:90]

TERM_WIDTH = int(CONFIG.get("term_width", 120))
TERM_HEIGHT = int(CONFIG.get("term_height", 35))
APP_INIT_SLEEP = float(CONFIG.get("app_init_sleep", 3))
KEY_DELAY = float(CONFIG.get("key_delay", 0.3))
MENU_DELAY = float(CONFIG.get("menu_delay", 0.5))
SHOW_CAPTURES = str(
    os.environ.get("SHOW_CAPTURES", CONFIG.get("show_captures", False))
).lower() in {"1", "true", "yes", "on"}
KEEP_SESSION = str(os.environ.get("KOLLAB_TMUX_KEEP", "")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

LAST_OUTPUT = ""
FAILURES = 0
STARTED = False


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["tmux", "-L", SOCKET_NAME, *args]
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"tmux {' '.join(args)} failed: {detail}")
    return result


def _shell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        shell=True,
        executable=os.environ.get("SHELL", "/bin/bash"),
        text=True,
        capture_output=True,
        check=False,
    )


def _cleanup() -> None:
    if KEEP_SESSION:
        print(f"[INFO] keeping tmux socket/session: {SOCKET_NAME}/{SESSION_NAME}")
        return
    _tmux("kill-server", check=False)


def _handle_signal(signum: int, _frame: Any) -> None:
    print(f"\n[INFO] signal {signum}; cleaning up")
    _cleanup()
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _print_header() -> None:
    print("==============================================")
    print(f"Tmux spec: {NAME}")
    print("==============================================")
    description = SPEC.get("description")
    if description:
        print(description)
    print(f"spec:    {SPEC_PATH}")
    print(f"socket:  {SOCKET_NAME}")
    print(f"session: {SESSION_NAME}")
    print("")


def _section(title: str) -> None:
    print("")
    print(f"--- {title} ---")


def _info(message: str) -> None:
    print(f"[INFO] {message}")


SPECIAL_KEYS = {
    "Enter": "C-m",
    "Return": "C-m",
    "Escape": "Escape",
    "Esc": "Escape",
    "Tab": "Tab",
    "Backspace": "BSpace",
    "BSpace": "BSpace",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "Space": "Space",
}


def _send_special(key: str) -> None:
    _tmux("send-keys", "-t", SESSION_NAME, SPECIAL_KEYS.get(key, key))


def _send_literal(text: str) -> None:
    if text:
        _tmux("send-keys", "-t", SESSION_NAME, "-l", text)


def _send_keys(keys: str) -> None:
    if keys in SPECIAL_KEYS:
        _send_special(keys)
    elif re.fullmatch(r"C-[A-Za-z]", keys):
        _send_special(keys)
    else:
        _send_literal(keys)
    time.sleep(KEY_DELAY)


def _start_app(step: dict[str, Any]) -> None:
    global STARTED
    command = str(step.get("command") or CONFIG.get("command") or "python main.py")
    if STARTED:
        _info("start_app requested after app already started; ignoring")
        return

    _tmux(
        "new-session",
        "-d",
        "-s",
        SESSION_NAME,
        "-x",
        str(TERM_WIDTH),
        "-y",
        str(TERM_HEIGHT),
    )
    STARTED = True
    _send_literal(command)
    _send_special("Enter")
    time.sleep(float(step.get("sleep", APP_INIT_SLEEP)))


def _capture(step: dict[str, Any] | None = None) -> str:
    global LAST_OUTPUT
    if not STARTED:
        LAST_OUTPUT = ""
        return LAST_OUTPUT
    result = _tmux("capture-pane", "-t", SESSION_NAME, "-p", check=False)
    LAST_OUTPUT = result.stdout or ""
    show = bool((step or {}).get("show", False)) or SHOW_CAPTURES
    if show:
        print("")
        print("----- capture start -----")
        print(LAST_OUTPUT.rstrip())
        print("----- capture end -----")
    return LAST_OUTPUT


def _tail(text: str, lines: int = 18) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _assert_contains(pattern: str, description: str = "") -> None:
    global FAILURES
    if re.search(pattern, LAST_OUTPUT, flags=re.IGNORECASE | re.MULTILINE):
        print(f"[PASS] {description or pattern}")
        return
    FAILURES += 1
    print(f"[FAIL] {description or pattern}")
    print(f"pattern: {pattern}")
    print("got (last lines):")
    print(_tail(LAST_OUTPUT))


def _assert_not_contains(pattern: str, description: str = "") -> None:
    global FAILURES
    if not re.search(pattern, LAST_OUTPUT, flags=re.IGNORECASE | re.MULTILINE):
        print(f"[PASS] {description or 'not ' + pattern}")
        return
    FAILURES += 1
    print(f"[FAIL] {description or 'not ' + pattern}")
    print(f"unexpected pattern: {pattern}")
    print("got (last lines):")
    print(_tail(LAST_OUTPUT))


def _type_text(text: str, delay: float) -> None:
    for char in text:
        _send_literal(char)
        time.sleep(delay)


def _run_shell_step(step: dict[str, Any]) -> None:
    global LAST_OUTPUT, FAILURES
    command = str(step.get("command") or "")
    if not command:
        FAILURES += 1
        print("[FAIL] shell step missing command")
        return
    desc = step.get("description") or command
    print(f"[SHELL] {desc}")
    result = _shell(command)
    LAST_OUTPUT = (result.stdout or "") + (result.stderr or "")
    if SHOW_CAPTURES or step.get("show", False):
        print(LAST_OUTPUT.rstrip())
    if result.returncode != 0 and not step.get("allow_failure", False):
        FAILURES += 1
        print(f"[FAIL] shell command exited {result.returncode}")
        print(_tail(LAST_OUTPUT))


def _execute_step(step: dict[str, Any]) -> None:
    action = str(step.get("action") or "")
    if not action:
        return

    if action == "start_app":
        _start_app(step)
    elif action == "sleep":
        time.sleep(float(step.get("seconds", 1)))
    elif action == "capture":
        _capture(step)
    elif action == "assert_contains":
        _assert_contains(
            str(step.get("pattern", "")),
            str(step.get("description") or step.get("name") or ""),
        )
    elif action == "assert_not_contains":
        _assert_not_contains(
            str(step.get("pattern", "")),
            str(step.get("description") or step.get("name") or ""),
        )
    elif action == "send_keys":
        _send_keys(str(step.get("keys") or ""))
    elif action == "type":
        _type_text(
            str(step.get("text") or ""),
            float(step.get("delay", 0.05)),
        )
        time.sleep(KEY_DELAY)
    elif action == "slash_command":
        command = str(step.get("command") or "").strip()
        subcommand = str(step.get("subcommand") or "").strip()
        text = command + (f" {subcommand}" if subcommand else "")
        _send_literal("/")
        time.sleep(float(step.get("palette_delay", MENU_DELAY)))
        _type_text(text, float(step.get("delay", 0.05)))
        _send_special("Enter")
        time.sleep(MENU_DELAY)
    elif action == "control":
        key = str(step.get("key") or "").strip()
        if key:
            _send_special(f"C-{key}")
            time.sleep(KEY_DELAY)
    elif action == "clear_input":
        _send_special("C-u")
        time.sleep(KEY_DELAY)
    elif action == "nav_mode":
        _send_special("Tab")
        time.sleep(MENU_DELAY)
    elif action == "edit_mode":
        _send_literal("e")
        time.sleep(MENU_DELAY)
    elif action == "escape":
        _send_special("Escape")
        time.sleep(KEY_DELAY)
    elif action == "enter":
        _send_special("Enter")
        time.sleep(KEY_DELAY)
    elif action == "arrow":
        direction = str(step.get("direction") or "Down").title()
        _send_special(direction)
        time.sleep(KEY_DELAY)
    elif action == "section":
        _section(str(step.get("title") or step.get("name") or "section"))
    elif action == "info":
        _info(str(step.get("message") or ""))
    elif action == "shell":
        _run_shell_step(step)
    else:
        global FAILURES
        FAILURES += 1
        print(f"[FAIL] unknown action: {action}")


def _run_steps(label: str, steps: list[dict[str, Any]]) -> None:
    if not steps:
        return
    if label:
        _section(label)
    for index, step in enumerate(steps, start=1):
        try:
            _execute_step(step)
        except Exception as exc:
            global FAILURES
            FAILURES += 1
            print(f"[FAIL] step {index} ({step.get('action')}): {exc}")
            if STARTED:
                _capture({"show": True})


def main() -> int:
    _print_header()
    try:
        _run_steps("setup", SPEC.get("setup") or [])
        _run_steps("", SPEC.get("steps") or [])
    finally:
        try:
            _run_steps("teardown", SPEC.get("teardown") or [])
        finally:
            _cleanup()

    print("")
    print("==============================================")
    if FAILURES:
        print(f"[FAIL] {NAME}: {FAILURES} failure(s)")
        return 1
    print(f"[PASS] {NAME}")
    return 0


sys.exit(main())
PY
