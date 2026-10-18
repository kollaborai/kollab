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
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
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

# --- HOME isolation ----------------------------------------------------------
#
# A spec must never touch the developer's real ~/.kollab: the app would join the
# live hub (attach to a running daemon, wake agents that spend paid model calls)
# and write conversation logs and vault entries. So the app command and every
# shell step get a throwaway HOME, seeded as a configured machine (setup done,
# connect guide seen) so specs do not land on the first-run wizard. The env is
# applied as `env HOME=... <command>`, so a spec that sets its own HOME inside
# its command (the throwaway-HOME specs) keeps it: the inner assignment wins.
#
# The hub's unix sockets live in /tmp/kollabor-hub/<hash of the project root>/,
# outside HOME, and agents delete socket files they have no presence record
# for. A spec that runs in this checkout (the owner's live folder) would share
# that directory with the live agents, so such a spec also gets its own project
# root (KOLLAB_PROJECT_ROOT, the knob behind `--project`).
#
# Opt-outs, in config: "real_home": true runs against the real HOME (loud
# warning; only for a spec that needs the developer's providers or data),
# "first_run": true leaves the throwaway HOME unseeded (the first-run wizard).
REAL_HOME = CONFIG.get("real_home") is True
FIRST_RUN = CONFIG.get("first_run") is True
HOME_ENV = re.compile(r"(?<![A-Za-z0-9_])HOME=")
SOCKET_DIR_ROOT = "/tmp/kollabor-hub/"
TEST_HOME: Path | None = None
RUN_ENV: dict[str, str] = {"KOLLAB_NO_KEYRING": "1"}


def _start_commands() -> list[str]:
    commands = [str(CONFIG.get("command") or "python main.py")]
    for key in ("setup", "steps", "teardown"):
        for step in SPEC.get(key) or []:
            if step.get("action") == "start_app" and step.get("command"):
                commands.append(str(step["command"]))
    return commands


def _setup_home() -> None:
    """Make the throwaway HOME and the env every spawned command runs with."""
    global TEST_HOME
    if REAL_HOME:
        return
    TEST_HOME = Path(tempfile.mkdtemp(prefix="kollab-tmux-home-"))
    (TEST_HOME / "project").mkdir()
    if not FIRST_RUN:
        kollab_dir = TEST_HOME / ".kollab"
        kollab_dir.mkdir()
        (kollab_dir / "config.json").write_text(
            json.dumps({"application": {"setup_completed": True}}), encoding="utf-8"
        )
        (kollab_dir / "connect-guide-seen").touch()
    RUN_ENV["HOME"] = str(TEST_HOME)
    if not any(HOME_ENV.search(command) for command in _start_commands()):
        RUN_ENV["KOLLAB_PROJECT_ROOT"] = str(TEST_HOME / "project")


def _env_prefix() -> str:
    return "env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in RUN_ENV.items())


def _home_pids() -> list[int]:
    """Pids whose environment says HOME is this run's throwaway HOME.

    The unique mkdtemp path makes the match proof of ownership: only commands
    this run started carry it. The pane's shell keeps the real HOME, and so
    does this runner, so neither matches.
    """
    out = subprocess.run(
        ["/bin/ps", "axeww", "-o", "pid=,command="],
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    needle = f" HOME={TEST_HOME}"
    pids = []
    for line in out.splitlines():
        pid_text, _, rest = line.strip().partition(" ")
        at = rest.find(needle)
        while at != -1:
            end = at + len(needle)
            if end == len(rest) or rest[end].isspace():
                pids.append(int(pid_text))
                break
            at = rest.find(needle, end)
    return [pid for pid in pids if pid != os.getpid()]


def _stop_home_processes() -> None:
    """Stop what the spec left running (daemons outlive the tmux pane)."""
    if not _home_pids():
        return
    python = REPO_ROOT / ".venv" / "bin" / "python"
    if python.exists():
        # graceful first: the hub stop only sees presence records in this HOME
        try:
            subprocess.run(
                [str(python), str(REPO_ROOT / "main.py"), "--hub", "stop", "all"],
                cwd=str(REPO_ROOT),
                env={**os.environ, **RUN_ENV},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            pass
    for sig, grace in ((signal.SIGTERM, 6.0), (signal.SIGKILL, 2.0)):
        pids = _home_pids()
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.time() + grace
        while _home_pids() and time.time() < deadline:
            time.sleep(0.2)
    if pids := _home_pids():
        print(f"[WARN] still running under the throwaway HOME: {pids}")


def _remove_home() -> None:
    if TEST_HOME is None:
        return
    _stop_home_processes()
    # A daemon killed hard leaves its socket file behind. Its presence record
    # names it; drop that file and the (now empty) project directory.
    for record in TEST_HOME.glob(".kollab/**/presence/*.json"):
        try:
            sock = json.loads(record.read_text(encoding="utf-8")).get("socket_path")
            if isinstance(sock, str) and sock.startswith(SOCKET_DIR_ROOT):
                Path(sock).unlink(missing_ok=True)
                Path(sock).parent.rmdir()
        except (OSError, ValueError):
            pass
    shutil.rmtree(TEST_HOME, ignore_errors=True)


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
        # same reason as _start_app: a shell step that loads profiles must not
        # pop a Keychain dialog
        env={**os.environ, **RUN_ENV},
    )


def _cleanup() -> None:
    if KEEP_SESSION:
        print(f"[INFO] keeping tmux socket/session: {SOCKET_NAME}/{SESSION_NAME}")
        if TEST_HOME:
            print(f"[INFO] keeping throwaway HOME: {TEST_HOME}")
        return
    _tmux("kill-server", check=False)
    _remove_home()


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
    if REAL_HOME:
        print(f"[WARN] real_home: this spec runs against the real HOME ({Path.home()})")
        print("[WARN] it can join live agents and spend paid model calls")
    else:
        print(f"home:    {TEST_HOME} (throwaway)")
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
    # Never touch the developer's OS keyring or real ~/.kollab from a test: on
    # macOS every lookup/store of a missing keyring entry pops a Keychain
    # dialog, and the real HOME holds live agents (see "HOME isolation" above).
    #
    # Prefixed with `env` (an external binary) rather than typed as
    # `export VAR=1` or set via tmux's `-e`: `export` is bash/zsh syntax that a
    # fish/csh default shell would reject -- silently booting the app
    # unguarded -- and tmux's session environment does not reach the initial
    # pane's shell (verified: `show-environment` has it, the pane does not).
    _send_literal(f"{_env_prefix()} {command}")
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


def _wait_for(
    pattern: str, timeout: float, interval: float, description: str = ""
) -> None:
    """Poll the pane until *pattern* appears, then leave it in LAST_OUTPUT.

    Eliminates fixed-sleep races: an assert_contains after this sees the frame
    where the content actually rendered, not a snapshot taken too early.
    """
    global LAST_OUTPUT, FAILURES
    label = description or f"appeared: {pattern}"
    if not STARTED:
        FAILURES += 1
        print(f"[FAIL] {label}: app not started")
        return
    deadline = time.time() + timeout
    while True:
        result = _tmux("capture-pane", "-t", SESSION_NAME, "-p", check=False)
        LAST_OUTPUT = result.stdout or ""
        if re.search(pattern, LAST_OUTPUT, flags=re.IGNORECASE | re.MULTILINE):
            print(f"[PASS] {label}")
            return
        if time.time() >= deadline:
            FAILURES += 1
            print(f"[FAIL] {label}: '{pattern}' not seen within {timeout}s")
            print(_tail(LAST_OUTPUT))
            return
        time.sleep(interval)


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
    # Inline assertions on the command's own output. Without these the keys
    # were accepted and silently ignored, so a spec could assert nothing while
    # reporting PASS. A following assert_contains step still works too -- both
    # read LAST_OUTPUT.
    if step.get("assert_contains"):
        _assert_contains(str(step["assert_contains"]), desc)
    if step.get("assert_not_contains"):
        _assert_not_contains(str(step["assert_not_contains"]), desc)


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
    elif action == "wait_for":
        _wait_for(
            str(step.get("pattern", "")),
            float(step.get("timeout", 5)),
            float(step.get("interval", 0.25)),
            str(step.get("description") or step.get("name") or ""),
        )
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
    _setup_home()
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
