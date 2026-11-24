"""Tests for scripts/ci_changed_lanes.py."""

import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci_changed_lanes.py"

spec = importlib.util.spec_from_file_location("ci_changed_lanes", SCRIPT)
ci_changed_lanes = importlib.util.module_from_spec(spec)
# Register before exec, matching the other script loaders in tests/unit.
sys.modules["ci_changed_lanes"] = ci_changed_lanes
spec.loader.exec_module(ci_changed_lanes)

BOTH = {"webui": True, "voice": True}
NEITHER = {"webui": False, "voice": False}


def test_webui_prefix_turns_on_only_webui():
    paths = ["packages/kollabor-webui/src/app.ts"]
    assert ci_changed_lanes.lanes_for(paths) == {"webui": True, "voice": False}


def test_voice_prefix_turns_on_only_voice():
    paths = ["packages/kollabor-voice/agent/run.py"]
    assert ci_changed_lanes.lanes_for(paths) == {"webui": False, "voice": True}


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "uv.lock", "pyproject.toml"])
def test_shared_files_turn_on_both_lanes(path):
    assert ci_changed_lanes.lanes_for([path]) == BOTH


def test_github_actions_directory_turns_on_both_lanes():
    paths = [".github/actions/setup-env/action.yml"]
    assert ci_changed_lanes.lanes_for(paths) == BOTH


def test_empty_path_list_turns_on_both_lanes():
    assert ci_changed_lanes.lanes_for([]) == BOTH


def test_unrelated_paths_turn_on_neither_lane():
    # The near-miss package name must not match the webui prefix.
    paths = ["kollab/core.py", "docs/guide.md", "packages/kollabor-webui-old/x.ts"]
    assert ci_changed_lanes.lanes_for(paths) == NEITHER


def test_main_prints_exactly_two_lane_lines(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("packages/kollabor-webui/a.ts\n"))
    assert ci_changed_lanes.main([]) == 0
    out = capsys.readouterr().out
    assert out == "webui=true\nvoice=false\n"
    assert len(out.splitlines()) == 2


def test_all_flag_prints_both_lanes_without_reading_stdin(monkeypatch, capsys):
    class ExplodingStdin:
        def __iter__(self):
            raise AssertionError("--all must not read stdin")

    monkeypatch.setattr(sys, "stdin", ExplodingStdin())
    assert ci_changed_lanes.main(["--all"]) == 0
    assert capsys.readouterr().out == "webui=true\nvoice=true\n"
