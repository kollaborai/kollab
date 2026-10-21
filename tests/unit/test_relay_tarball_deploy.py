"""Exercise the real deployment shell with local SSH/service/package stubs."""

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/relay/deploy_relay_tarball.sh"
STUB = r'''
import json, os, pathlib, shutil, subprocess, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["RELAY_TEST_LOG"], "a") as log:
    log.write(json.dumps({"program": name, "args": args}) + "\n")
if name == "ssh":
    if args[0] == "--":
        args.pop(0)
    env = dict(os.environ, HOME=os.environ["RELAY_TEST_REMOTE_HOME"])
    sys.exit(subprocess.run(["/bin/bash", "-c", args[1]], env=env).returncode)
if name == "scp":
    source, target = args[-2:]
    target = pathlib.Path(target.split(":", 1)[1])
    shutil.copyfile(source, target)
    if os.environ.get("RELAY_TEST_CORRUPT_UPLOAD"):
        target.write_bytes(target.read_bytes() + b"corrupted upload")
    sys.exit(0)
if name == "sudo":
    if args[0] == "-n":
        args.pop(0)
    sys.exit(subprocess.run(args).returncode)
if name == "systemctl":
    if args[0] == "restart":
        counter = pathlib.Path(os.environ["RELAY_TEST_REMOTE_HOME"]) / "restarts"
        count = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(count))
        if count == 1 and os.environ.get("RELAY_TEST_FAIL_RESTART"):
            sys.exit(1)
    sys.exit(0)
if name == "python3":
    if args[:2] == ["-m", "venv"]:
        binary = pathlib.Path(args[2]) / "bin"
        binary.mkdir(parents=True)
        for tool in ("python", "pip"):
            (binary / tool).write_text("#!/bin/sh\nexit 0\n")
            (binary / tool).chmod(0o755)
        sys.exit(0)
    os.execv(sys.executable, [sys.executable, *args])
if name == "flock":
    sys.exit(1 if os.environ.get("RELAY_TEST_LOCK_BUSY") else 0)
if name == "sleep":
    sys.exit(0)
if name == "curl":
    candidate = "WorkingDirectory=" in pathlib.Path(os.environ["DROPIN"]).read_text()
    mode = os.environ.get("RELAY_TEST_HEALTH", "compact") if candidate else "compact"
    if mode == "malformed":
        print("not-json")
    else:
        print(json.dumps({"status": "ok", "degraded": mode == "degraded"}, separators=(",", ":")))
    sys.exit(0)
raise SystemExit("unhandled stub " + name)
'''


@pytest.fixture
def deployment(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    for name in ("ssh", "scp", "sudo", "systemctl", "python3", "flock", "sleep", "curl"):
        tool = binary / name
        tool.write_text(f"#!{sys.executable}\n" + STUB)
        tool.chmod(0o755)
    remote_home = tmp_path / "remote"
    relay_home = remote_home / ".local/share/kollab-relay"
    relay_home.mkdir(parents=True)
    config = remote_home / ".config/kollab-relay/runtime.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"bind_host": "127.0.0.1", "health_port": 18000}))
    dropin = tmp_path / "systemd/scoped-relay.service.d/source-v2.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_bytes(b"[Service]\nExecStart=old exact service command\n")
    dropin.chmod(0o640)
    (relay_home / "version").write_bytes(b"0.17.0\n")
    (relay_home / "version").chmod(0o640)
    # The fixture needs no account credentials or inherited application state.
    # Keep failure representations and every child environment bounded.
    env = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(
        PATH=str(binary) + os.pathsep + os.environ["PATH"], RELAY_HOST="relay-alias",
        SERVICE="scoped-relay.service", DROPIN=str(dropin), RELAY_HEALTH_ATTEMPTS="2",
        RELAY_HEALTH_INTERVAL="0", RELAY_TEST_REMOTE_HOME=str(remote_home),
        RELAY_TEST_LOG=str(tmp_path / "commands.jsonl"),
    )
    return tmp_path, relay_home, config, dropin, env


def artifact(directory, *, bad_manifest=False, unsafe_member=False):
    sources = {"kollabor_cli_main.py": b"", "scripts/relay/requirements.txt": b""}
    if unsafe_member:
        sources["../escaped"] = b"must never extract"
    manifest = {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()}
    if bad_manifest:
        manifest["kollabor_cli_main.py"] = "0" * 64
    sources["source-sha256.json"] = json.dumps(manifest).encode()
    path = directory / "selected-service.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, body in sources.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return path


def deploy(state, **settings):
    directory, _, _, _, env = state
    selected = settings.pop("artifact", None) or artifact(directory)
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), str(selected), "abc1234", "0.18.0"],
        env={**env, **settings}, capture_output=True, text=True, timeout=30,
    )


def assert_previous_state(relay_home, dropin):
    assert dropin.read_bytes() == b"[Service]\nExecStart=old exact service command\n"
    assert (relay_home / "version").read_bytes() == b"0.17.0\n"
    assert stat.S_IMODE(dropin.stat().st_mode) == 0o640
    assert stat.S_IMODE((relay_home / "version").stat().st_mode) == 0o640


def test_remote_home_defaults_and_compact_health_activate_only_the_named_unit(deployment):
    directory, relay_home, config, dropin, _ = deployment
    result = deploy(deployment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (relay_home / "update.lock").is_file()
    assert (relay_home / "version").read_text() == "0.18.0\n"
    assert f"--config {config}" in dropin.read_text()
    assert "/.venv/bin/python kollabor_cli_main.py relay run" in dropin.read_text()
    (backup,) = relay_home.glob("rollback-*")
    assert (backup / "dropin.conf").read_bytes() == b"[Service]\nExecStart=old exact service command\n"
    assert (backup / "version").read_bytes() == b"0.17.0\n"
    assert (backup / "RETENTION").is_file()
    calls = [json.loads(line) for line in (directory / "commands.jsonl").read_text().splitlines()]
    restarts = [call["args"] for call in calls if call["program"] == "systemctl" and call["args"][0] == "restart"]
    assert restarts == [["restart", "scoped-relay.service"]]


@pytest.mark.parametrize("health", ["degraded", "malformed"])
def test_unhealthy_candidate_restores_exact_dropin_and_version(deployment, health):
    _, relay_home, _, dropin, _ = deployment
    result = deploy(deployment, RELAY_TEST_HEALTH=health)
    assert result.returncode != 0
    assert_previous_state(relay_home, dropin)
    assert "rollback status=0" in result.stderr


def test_failed_restart_restores_exact_dropin_and_version(deployment):
    _, relay_home, _, dropin, _ = deployment
    result = deploy(deployment, RELAY_TEST_FAIL_RESTART="1")
    assert result.returncode != 0
    assert_previous_state(relay_home, dropin)
    assert "rollback status=0" in result.stderr


def test_an_originally_absent_version_marker_stays_absent_after_failure(deployment):
    _, relay_home, _, dropin, _ = deployment
    (relay_home / "version").unlink()
    result = deploy(deployment, RELAY_TEST_HEALTH="degraded")
    assert result.returncode != 0
    assert not (relay_home / "version").exists()
    assert dropin.read_bytes() == b"[Service]\nExecStart=old exact service command\n"
    (backup,) = relay_home.glob("rollback-*")
    assert (backup / "version.absent").is_file()


def test_autoupdate_lock_contention_never_activates_the_uploaded_artifact(deployment):
    _, relay_home, _, dropin, _ = deployment
    result = deploy(deployment, RELAY_TEST_LOCK_BUSY="1")
    assert result.returncode == 75
    assert_previous_state(relay_home, dropin)
    assert not list(relay_home.glob("rollback-*"))
    assert "not activated" in result.stderr


@pytest.mark.parametrize("problem", ["upload", "manifest", "unsafe_member"])
def test_bad_artifacts_fail_before_changing_the_running_service(deployment, problem):
    directory, relay_home, _, dropin, _ = deployment
    selected = artifact(directory, bad_manifest=problem == "manifest", unsafe_member=problem == "unsafe_member")
    settings = {"RELAY_TEST_CORRUPT_UPLOAD": "1"} if problem == "upload" else {}
    result = deploy(deployment, artifact=selected, **settings)
    assert result.returncode != 0
    assert_previous_state(relay_home, dropin)
    assert not list(relay_home.glob("rollback-*"))
    assert not (relay_home / "releases/escaped").exists()
