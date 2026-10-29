"""The binary smoke test must verify embedded, not inherited, provenance."""

from __future__ import annotations

import importlib.util
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_binary.py"
_spec = importlib.util.spec_from_file_location("kollab_build_binary", SCRIPT)
assert _spec is not None and _spec.loader is not None
build_binary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_binary)


def test_wheel_build_line_uses_embedded_full_sha(tmp_path: Path) -> None:
    wheel = tmp_path / "kollab-0.18.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "kollabor/_build_info.py",
            "BUILD_BRANCH = 'live/0.18.0'\nBUILD_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n",
        )

    assert build_binary.kollab_build_line([wheel]) == "Build: live/0.18.0 @ aaaaaaaa"


def test_wheel_build_line_rejects_missing_embedded_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "kollab-0.18.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w"):
        pass

    with pytest.raises(SystemExit, match="valid embedded build provenance"):
        build_binary.kollab_build_line([wheel])


def test_smoke_strips_runtime_build_env_and_requires_final_version_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOLLAB_BUILD_BRANCH", "live/0.18.0")
    monkeypatch.setenv("KOLLAB_BUILD_SHA", "a" * 40)
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Build: live/0.18.0 @ aaaaaaaa\nkollab 0.18.0\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(build_binary.subprocess, "run", run)
    build_binary.smoke(tmp_path / "kollab", "0.18.0", "Build: live/0.18.0 @ aaaaaaaa")

    assert len(calls) == 2
    for call in calls:
        assert "KOLLAB_BUILD_BRANCH" not in call["env"]
        assert "KOLLAB_BUILD_SHA" not in call["env"]
    assert calls[0]["env"]["KOLLAB_NO_KEYRING"] == "1"
    assert calls[1]["env"]["PEX_INTERPRETER"] == "1"


def test_smoke_rejects_missing_provenance_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = [str(tmp_path / "kollab")]
    result = subprocess.CompletedProcess(
        command, 0, stdout="kollab 0.18.0\n", stderr=""
    )
    expected = "Build: live/0.18.0 @ aaaaaaaa"
    with patch.object(build_binary.subprocess, "run", return_value=result):
        with pytest.raises(SystemExit, match="expected Build: live/0.18.0"):
            build_binary.smoke(tmp_path / "kollab", "0.18.0", expected)


@pytest.mark.parametrize(
    ("system", "machine", "name", "filename"),
    [
        ("Linux", "x86_64", "linux-x86_64", "kollab-linux-x86_64"),
        ("Darwin", "arm64", "macos-aarch64", "kollab-macos-aarch64"),
        ("Windows", "AMD64", "windows-x86_64", "kollab-windows-x86_64.exe"),
    ],
)
def test_binary_is_named_as_pex_names_it_on_each_platform(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, name: str, filename: str
) -> None:
    monkeypatch.setattr(build_binary.platform, "system", lambda: system)
    monkeypatch.setattr(build_binary.platform, "machine", lambda: machine)
    assert build_binary.platform_name() == name
    assert build_binary.binary_filename() == filename


def test_unknown_platform_still_has_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_binary.platform, "system", lambda: "FreeBSD")
    with pytest.raises(SystemExit, match="no kollab binary for FreeBSD"):
        build_binary.platform_name()


STATUS_ONLINE = "1 agent(s) online:\n\n  lapis *  pid=4242  idle\n\nnetwork: not connected\n"
STATUS_NONE = "no agents online\n\nnetwork: not connected\n"


def fake_kollab(monkeypatch: pytest.MonkeyPatch, statuses: list[str], stop_calls: list[list[str]]) -> None:
    """A stand-in kollab: `--detached` starts nothing, `--hub status` answers from `statuses` in order."""
    monkeypatch.setattr(build_binary.time, "sleep", lambda _: None)

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        args = command[1:]
        if args[:2] == ["--hub", "stop"]:
            stop_calls.append(args)
        stdout = statuses.pop(0) if args[:2] == ["--hub", "status"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(build_binary.subprocess, "run", run)


def test_agent_smoke_passes_once_the_agent_is_online_and_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stops: list[list[str]] = []
    fake_kollab(monkeypatch, [STATUS_NONE, STATUS_ONLINE, STATUS_NONE], stops)
    build_binary.agent_smoke(tmp_path / "kollab.exe")
    assert stops == [["--hub", "stop", "lapis"]]


def test_agent_smoke_names_what_a_runtime_without_daemon_start_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stops: list[list[str]] = []
    fake_kollab(monkeypatch, [STATUS_NONE] * 50, stops)
    with pytest.raises(SystemExit, match="started no agent that answers `--hub status`"):
        build_binary.agent_smoke(tmp_path / "kollab.exe", timeout=0.0)
    assert stops == []


def test_agent_smoke_fails_when_stop_leaves_the_agent_online(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_kollab(monkeypatch, [STATUS_ONLINE] * 40, [])
    clock = iter(range(0, 10_000, 40))
    monkeypatch.setattr(build_binary.time, "monotonic", lambda: next(clock))
    with pytest.raises(SystemExit, match="left the agent online"):
        build_binary.agent_smoke(tmp_path / "kollab.exe")
