"""The kill switch, end to end, on a Windows CI runner.

A detached agent starts in a throwaway home and folder and shows online in `kollab --hub status`.
A halt run from this process stops it, and a second start waits instead of running. Resume lets
the waiter start it again. The halt stops every kollab of the install, so this runs only on CI.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The same agent line build_binary.py's agent smoke reads from `kollab --hub status`.
AGENT_ONLINE = re.compile(r"^\s+(?P<name>\S+).*\bpid=(?P<pid>\d+)", re.MULTILINE)


def smoke_env(**extra: str) -> dict[str, str]:
    """This environment minus any binary identity, which would make one kollab act as another."""
    build_env = {"KOLLAB_BUILD_BRANCH", "KOLLAB_BUILD_SHA"}
    env = {key: value for key, value in os.environ.items() if key not in {"SCIE", "SCIE_ARGV0", "PEX"} | build_env}
    env["KOLLAB_NO_KEYRING"] = "1"
    return {**env, **extra}


def status_online(kollab: str, project: Path, env: dict[str, str]) -> bool:
    """Print `kollab --hub status` and say whether an agent is online in it."""
    status = subprocess.run(
        [kollab, "--hub", "status"], cwd=project, env=env, capture_output=True, text=True, timeout=60
    )
    print(status.stdout.rstrip() or f"(no output, exit {status.returncode})")
    # A halted launch waits under a halted-<pid> name: it is no agent at work.
    return any(not m["name"].startswith("halted-") for m in AGENT_ONLINE.finditer(status.stdout))


def start_agent(kollab: str, project: Path, env: dict[str, str], log: Path) -> subprocess.Popen:
    """Launch `kollab --detached`: it starts a detached child and exits; under a halt that child is the waiter."""
    # A file, not a pipe: the detached agent may keep its inherited handles open for good.
    with log.open("ab") as stream:
        return subprocess.Popen(
            [kollab, "--detached"], cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream
        )


def wait_until(check, timeout: float, what: str) -> None:
    """Poll `check` every 2 s until it is true; past `timeout`, name what never happened."""
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise SystemExit(f"windows halt smoke: not within {timeout:.0f}s: {what}")
        time.sleep(2)


def halt_smoke(kollab: str, project: Path, env: dict[str, str], log: Path) -> None:
    """Start, halt, start again while halted, then resume. Runs after HOME and USERPROFILE are set."""
    from kollabor_config import proc_ctl
    from plugins.hub import halt_runtime, network_halt
    from plugins.hub.halt_context import HaltUnavailable
    from plugins.hub.presence import get_presence_dir

    def online() -> bool:
        return status_online(kollab, project, env)

    def halted() -> list[Path]:
        return list(get_presence_dir().glob("halted-*.json"))

    print(f"--detached: exit {start_agent(kollab, project, env, log).wait(timeout=60)}")
    wait_until(online, 90, "an agent online in `kollab --hub status`")

    network_halt.network_control_refusal = lambda *args, **kwargs: ""  # no console on a runner
    # A network refuses a halt with fewer than two counting machines, since a resume needs a second one to confirm
    # it: logic with no OS code, tested in tests/unit/test_network_halt.py.
    # This runner is one machine, so it cannot halt itself under that rule; the smoke proves the Windows halt,
    # the halted launch and the local resume, not the rule. An owner now runs on Windows too and would take
    # the halt with the real rule, so the bypass must cover the owner path as well: an owner's single-machine
    # refusal falls back to this folder's own record, the same copy _via_owner takes when no owner runs.
    # Test-only, this smoke's process; the rule itself never changes.
    network_halt.halt_allowed = lambda view: True

    def traced(call):  # cli_control prints only the error's message; a failed run needs its line
        async def run(*args, **kwargs):
            try:
                return await call(*args, **kwargs)
            except Exception:
                traceback.print_exc()
                raise

        return run

    real_via_owner = halt_runtime._via_owner

    async def via_owner_single_machine(folder, control, action, root, payload):
        """Test-only: an owner's one-machine refusal takes the halt on the folder's own record."""
        try:
            return await real_via_owner(folder, control, action, root, payload)
        except HaltUnavailable as error:
            if "one machine that can confirm a resume" not in str(error):
                raise
            return control.halt_act(action, payload)

    halt_runtime._via_owner = traced(via_owner_single_machine)
    halt_runtime._act = traced(halt_runtime._act)
    code = asyncio.run(halt_runtime.cli_control("halt", ["--here-only", "--reason", "windows smoke"]))
    if code != 0:  # the listener runs detached with no output: run one here so its error shows
        from kollabor.binary import kollab_argv

        try:
            ran = subprocess.run([*kollab_argv(), "--halt-listener"], capture_output=True, text=True,
                                 errors="replace", timeout=20, env=env)
            print(f"--- kollab --halt-listener exited {ran.returncode}:\n{(ran.stdout + ran.stderr)[-4000:]}")
        except subprocess.TimeoutExpired:
            print("--- kollab --halt-listener started and ran 20 s with no error, so it did not fail the halt")
    assert code == 0, f"halt exited {code}"
    wait_until(lambda: not online(), 60, "no agent online after the halt")
    assert halt_runtime.read_halt().get("scopes"), "the halt wrote no scopes"

    launch = start_agent(kollab, project, env, log)  # its detached child sees the halt and waits
    wait_until(lambda: bool(halted()), 60, "a halted-*.json marker in the presence folder")
    waiter = int(halted()[0].stem.split("-", 1)[1])  # halted-<pid>.json, written by the waiting child
    time.sleep(5)  # a waiter that saw no halt would restart the launch and drop its marker by now
    assert halted() and proc_ctl.pid_alive(waiter), f"the halted launch's waiter {waiter} did not stay"
    assert not online(), "an agent came online while the machine was halted"

    code = asyncio.run(halt_runtime.cli_control("resume", ["--local"]))
    assert code == 0, f"resume exited {code}"
    wait_until(lambda: online() and not halted(), 90, "the waiter restarted the agent and removed halted-*.json")
    print(f"launch: exit {launch.wait(timeout=60)}; waiter {waiter} restarted it after the resume")


def kill_home_processes(home: Path) -> None:
    """End every process whose environment names this throwaway home as USERPROFILE."""
    import psutil

    from kollabor_config import proc_ctl

    for proc in psutil.process_iter(["pid", "environ"]):
        try:
            # Skip this process: on Windows its own environment block can carry the same USERPROFILE.
            if proc.info["pid"] != os.getpid() and (proc.info.get("environ") or {}).get("USERPROFILE") == str(home):
                proc_ctl.kill_tree(proc.info["pid"], proc_ctl.HARD)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kollab", default=shutil.which("kollab"), help="the kollab to smoke (default: on PATH)")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("refusing: this smoke halts every kollab of this install on the machine; it runs only on a CI runner")
        return 2
    if not args.kollab:
        print("no kollab on PATH; pass --kollab PATH")
        return 2
    args.kollab = os.path.abspath(args.kollab)  # main() runs the smoke from the project folder

    home = Path(tempfile.mkdtemp(prefix="kollab-halt-smoke-"))
    project = home / "project"
    project.mkdir()
    (home / ".kollab").mkdir()
    (home / ".kollab" / "config.json").write_text(json.dumps({"application": {"setup_completed": True}}))
    log = home / "detached.log"
    env = smoke_env(HOME=str(home), USERPROFILE=str(home))
    # This process runs the halt itself, so it needs the same home before any kollab import.
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    original = os.getcwd()
    os.chdir(project)  # cli_control halts the folder it runs in
    try:
        halt_smoke(args.kollab, project, env, log)
    except BaseException:
        for path in [*sorted(home.rglob("kollab.log")), log]:
            if path.exists():
                print(f"--- last 60 lines of {path}")
                print("\n".join(path.read_text(errors="replace").splitlines()[-60:]))
        raise
    finally:
        try:
            subprocess.run([args.kollab, "--hub", "stop", "all"], cwd=project, env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        kill_home_processes(home)
        os.chdir(original)
        shutil.rmtree(home, ignore_errors=True)
    print("passed windows halt smoke: agent stopped, launch waited, resume restarted it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
