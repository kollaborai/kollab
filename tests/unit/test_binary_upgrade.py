"""The single-file kollab binary: detection, /upgrade swapping the file, and every restart path."""

import asyncio
import hashlib
import io
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kollabor.updates as updates
import kollabor.updates.auto_update as auto_update
from kollabor.binary import kollab_argv, running_binary

NAME = "kollab-macos-aarch64"
# A stand-in release binary: --version prints the file name it runs as, then the version.
NEW = b'#!/bin/sh\necho "$(basename "$0") 9.9.9"\n'


@pytest.fixture
def binary(tmp_path, monkeypatch):
    """This process runs from tmp_path/bin/kollab: the launcher's SCIE plus its pex venv."""
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "PEX-INFO").write_text("{}")
    path = tmp_path / "bin" / "kollab"
    path.parent.mkdir()
    path.write_bytes(b"old")
    path.chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setenv("SCIE", str(path))
    return path


def release(monkeypatch, *, tag="v9.9.9", body=NEW, digest=None, assets=(NAME, f"{NAME}.sha256")):
    """Serve a GitHub release for _open; returns the URLs requested."""
    base = "https://downloads.invalid/"
    digest = digest or hashlib.sha256(body).hexdigest()
    files = {base + NAME: body, f"{base}{NAME}.sha256": f"{digest} *{NAME}".encode()}
    files[auto_update.LATEST_RELEASE] = json.dumps(
        {"tag_name": tag, "assets": [{"name": name, "browser_download_url": base + name} for name in assets]}
    ).encode()
    opened = []

    def fake_open(url, timeout):
        opened.append(url)
        if url not in files:
            raise OSError(f"HTTP Error 404: {url}")
        return io.BytesIO(files[url])

    monkeypatch.setattr(auto_update, "_open", fake_open)
    monkeypatch.setattr(auto_update, "platform_name", lambda: "macos-aarch64")
    monkeypatch.setattr(auto_update, "_installed_version", lambda: "0.1.0")
    return opened


def leftovers(binary):
    return [path.name for path in binary.parent.iterdir() if path.name.startswith(".kollab-upgrade-")]


def test_the_binary_restarts_as_its_own_file(binary):
    assert running_binary() == binary
    assert kollab_argv() == [str(binary)]


def test_an_inherited_launcher_variable_is_not_the_binary(tmp_path, monkeypatch):
    # A dev checkout started from the binary's shell inherits SCIE but runs its own venv.
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setenv("SCIE", str(tmp_path / "kollab"))
    assert running_binary() is None
    assert kollab_argv() == [sys.executable, "-m", "kollabor_cli_main"]
    monkeypatch.delenv("SCIE")
    assert running_binary() is None


@pytest.mark.parametrize(
    ("system", "machine", "name"),
    [
        ("Darwin", "arm64", "macos-aarch64"),
        ("Darwin", "x86_64", "macos-x86_64"),
        ("Linux", "aarch64", "linux-aarch64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Windows", "AMD64", None),
        ("Linux", "riscv64", None),
    ],
)
def test_platform_names_match_the_release_assets(monkeypatch, system, machine, name):
    monkeypatch.setattr(auto_update.platform, "system", lambda: system)
    monkeypatch.setattr(auto_update.platform, "machine", lambda: machine)
    assert auto_update.platform_name() == name


def test_a_binary_upgrades_itself_not_a_package(binary, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_update, "_upgrade_binary", lambda path: calls.append(path) or "swapped")
    monkeypatch.setattr(auto_update, "_run_cmd", lambda *args: pytest.fail(f"ran {args}"))

    assert auto_update.run_auto_update() == "swapped"
    assert calls == [binary]


def test_upgrade_swaps_in_a_verified_binary_that_starts(binary, monkeypatch):
    release(monkeypatch)

    result = auto_update.run_auto_update()

    assert (result.success, result.changed, result.method) == (True, True, "binary")
    assert result.message == "Kollab upgraded: v0.1.0 -> v9.9.9 (via binary)."
    assert binary.read_bytes() == NEW and os.access(binary, os.X_OK)
    assert leftovers(binary) == []


def test_upgrade_reports_up_to_date_without_downloading(binary, monkeypatch):
    opened = release(monkeypatch, tag="v0.1.0")

    result = auto_update.run_auto_update()

    assert (result.success, result.changed) == (True, False)
    assert result.message == "Kollab is already up to date (v0.1.0)."
    assert opened == [auto_update.LATEST_RELEASE] and binary.read_bytes() == b"old"


def test_a_download_that_fails_its_checksum_changes_nothing(binary, monkeypatch):
    release(monkeypatch, digest="0" * 64)

    result = auto_update.run_auto_update()

    assert result.success is False and "did not match its SHA-256; kept v0.1.0" in result.message
    assert binary.read_bytes() == b"old" and leftovers(binary) == []


def test_a_binary_that_does_not_start_changes_nothing(binary, monkeypatch):
    release(monkeypatch, body=b"#!/bin/sh\necho broken >&2\nexit 3\n")

    result = auto_update.run_auto_update()

    assert result.success is False and "binary did not start (broken)" in result.message
    assert binary.read_bytes() == b"old" and leftovers(binary) == []


def test_the_new_binary_starts_without_this_ones_launcher_variables(binary, monkeypatch):
    # Its launcher would refuse a SCIE naming another file, or boot that file instead.
    monkeypatch.setenv("PEX", str(binary))
    seen = binary.parent / "seen"
    release(monkeypatch, body=f'#!/bin/sh\necho "${{SCIE:-none}} ${{PEX:-none}}" > {seen}\necho "k 9.9.9"\n'.encode())

    assert auto_update.run_auto_update().success
    assert seen.read_text().split() == ["none", "none"]


def test_a_release_without_this_machines_binary_is_reported(binary, monkeypatch):
    release(monkeypatch, assets=())

    result = auto_update.run_auto_update()

    assert result.success is False
    assert result.message == f"v9.9.9 has no {NAME} binary yet; try again in a few minutes."
    assert binary.read_bytes() == b"old"


def test_a_network_failure_is_reported(binary, monkeypatch):
    release(monkeypatch)
    monkeypatch.setattr(auto_update, "_open", lambda url, timeout: (_ for _ in ()).throw(OSError("offline")))

    result = auto_update.run_auto_update()

    assert result.success is False and result.message == "Could not upgrade the kollab binary: offline"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere")
def test_a_binary_in_a_folder_it_cannot_write_says_so(binary, monkeypatch):
    opened = release(monkeypatch)
    binary.parent.chmod(0o555)
    try:
        result = auto_update.run_auto_update()
    finally:
        binary.parent.chmod(0o755)

    assert result.success is False and result.message.startswith(f"Cannot write {binary.parent.resolve()}")
    assert opened == []


@pytest.mark.asyncio
async def test_upgrade_restarts_the_binary_not_its_python(binary, monkeypatch):
    import kollabor.commands.system_commands.handlers.system as system_module
    from kollabor.commands.system_commands.handlers.system import SystemCommandHandler

    handler = SimpleNamespace(
        logger=system_module.logging.getLogger("test"),
        event_bus=SimpleNamespace(get_service=lambda name: None),
    )
    execs = []
    upgraded = updates.AutoUpdateResult(True, "Kollab upgraded: v0.1.0 -> v9.9.9 (via binary).", "binary")
    monkeypatch.setattr(updates, "run_auto_update", lambda: upgraded)
    monkeypatch.setattr(os, "execv", lambda *args: execs.append(args))
    monkeypatch.setattr(sys, "argv", ["kollab", "--agent", "lapis"])
    monkeypatch.delenv("KOLLAB_DAEMON_PID", raising=False)

    await SystemCommandHandler.handle_upgrade(handler, None)

    assert execs == [(str(binary), [str(binary), "--agent", "lapis"])]


def test_a_service_unit_runs_the_binary(binary):
    from kollabor import service

    assert service.exec_argv() == [str(binary), "--detached"]


def test_a_relay_unit_runs_the_binary(binary, tmp_path):
    from plugins.hub import relay_selfhost as selfhost
    from plugins.hub.dns.discovery import normalize_target

    target = normalize_target("relay.example.com", document=False)
    unit = selfhost.systemd_unit(selfhost.Settings(target=target, state_dir=tmp_path / "state"))
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert exec_line.startswith(f"ExecStart={binary} relay serve ")


def test_engine_daemons_run_the_engines_own_binary(binary, monkeypatch):
    from kollabor_engine import daemon_pool

    monkeypatch.setattr(daemon_pool.shutil, "which", lambda name: "/elsewhere/kollab")
    assert daemon_pool._kollab_command() == [str(binary)]


@pytest.mark.asyncio
async def test_hub_restart_execs_the_binary(binary, monkeypatch):
    from plugins.hub.plugin import HubPlugin

    plugin = object.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="koordinator")
    plugin._perform_self_restart = AsyncMock()
    plugin._self_restart_watchdog = AsyncMock()
    monkeypatch.setattr(sys, "argv", ["kollab", "--agent", "koordinator", "--detached"])

    await plugin._handle_hub_restart_tool({"id": "t1"})
    await asyncio.sleep(0)

    assert plugin._self_restart_cmd == [str(binary), "--agent", "koordinator"]
