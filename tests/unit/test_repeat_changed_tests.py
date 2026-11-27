from __future__ import annotations

import subprocess

from scripts import repeat_changed_tests as repeat


def test_select_test_files_keeps_only_nested_python_test_files() -> None:
    assert repeat.select_test_files(
        [
            "README.md",
            "tests/unit/test_alpha.py",
            "tests/test_beta.py",
            "tests/unit/helper.py",
            "tests/unit/test_gamma.txt",
            "packages/tests/test_delta.py",
        ]
    ) == ["tests/test_beta.py", "tests/unit/test_alpha.py"]


def test_repeat_file_stops_at_first_failure_and_names_the_run(capsys) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if len(calls) == 2 else 0)

    assert repeat.run_test_file("tests/unit/test_alpha.py", 5, runner=runner) == 1

    assert len(calls) == 2
    assert "-p" in calls[0] and calls[0][calls[0].index("-p") + 1] == "no:cacheprovider"
    marker_option = len(calls[0]) - 1 - calls[0][::-1].index("-m")
    assert calls[0][marker_option + 1] == "not quarantine"
    assert "run 2/5" in capsys.readouterr().err


def test_cli_defaults_to_twenty_runs() -> None:
    assert repeat.build_parser().get_default("runs") == 20


def test_main_exits_cleanly_when_no_test_file_changed(monkeypatch, capsys) -> None:
    monkeypatch.setattr(repeat, "changed_test_files", lambda _base, **_kwargs: [])

    assert repeat.main(["--base", "origin/main"]) == 0
    assert "No changed test files" in capsys.readouterr().out


def test_select_test_files_includes_the_package_suites_ci_runs() -> None:
    assert repeat.select_test_files(
        [
            "packages/kollabor-engine/tests/test_x.py",
            "packages/kollabor-rpc/tests/sub/test_y.py",
            "packages/kollabor-voice/tests/test_z.py",
        ]
    ) == ["packages/kollabor-engine/tests/test_x.py", "packages/kollabor-rpc/tests/sub/test_y.py"]


def test_changed_test_files_diffs_the_package_suites_too() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    repeat.changed_test_files("origin/main", runner=runner)

    assert "packages/kollabor-engine/tests" in commands[0]
    assert "packages/kollabor-rpc/tests" in commands[0]


def test_package_test_files_repeat_with_the_asyncio_mode_ci_uses() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    assert repeat.run_test_file("packages/kollabor-engine/tests/test_x.py", 1, runner=runner) == 0
    assert calls[0][calls[0].index("-o") + 1] == "asyncio_mode=auto"


def test_the_voice_suite_can_be_repeated_in_its_own_environment() -> None:
    voice = "packages/kollabor-voice/tests/test_z.py"

    assert repeat.select_test_files([voice], suites=("packages/kollabor-voice/tests",)) == [voice]
    args = repeat.build_parser().parse_args(["--base", "x", "--suites", "packages/kollabor-voice/tests"])
    assert args.suites == ("packages/kollabor-voice/tests",)
    assert repeat.build_parser().parse_args(["--base", "x"]).suites == repeat.DEFAULT_SUITES
