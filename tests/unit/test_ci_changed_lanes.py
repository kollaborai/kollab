"""Tests for scripts/ci_changed_lanes.py."""

import importlib.util
import io
import sys
from pathlib import Path

import pytest
import yaml

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


def test_voice_prefix_turns_on_both_lanes():
    paths = ["packages/kollabor-voice/agent/run.py"]
    assert ci_changed_lanes.lanes_for(paths) == BOTH


@pytest.mark.parametrize(
    "path",
    [
        "packages/kollabor-engine/src/kollabor_engine/auth.py",
        "kollabor/application.py",
        "plugins/hub/plugin.py",
    ],
)
def test_webui_dependencies_turn_on_webui(path):
    assert ci_changed_lanes.lanes_for([path]) == {"webui": True, "voice": False}


def test_learn_docs_change_turns_on_webui():
    paths = ["docs/features/learn.md", "changes/1413.ci.md"]
    assert ci_changed_lanes.lanes_for(paths) == {"webui": True, "voice": False}


def test_voice_only_change_turns_on_only_voice():
    paths = ["packages/kollabor-voice/src/kollabor_voice/client.py"]
    assert ci_changed_lanes.lanes_for(paths) == BOTH


def test_webui_unit_test_turns_on_webui():
    assert ci_changed_lanes.lanes_for(["tests/unit/test_webui_server.py"]) == {
        "webui": True,
        "voice": False,
    }


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "uv.lock", "pyproject.toml"])
def test_shared_files_turn_on_both_lanes(path):
    assert ci_changed_lanes.lanes_for([path]) == BOTH


def test_github_actions_directory_turns_on_both_lanes():
    paths = [".github/actions/setup-env/action.yml"]
    assert ci_changed_lanes.lanes_for(paths) == BOTH


def test_empty_path_list_turns_on_both_lanes():
    assert ci_changed_lanes.lanes_for([]) == BOTH


def test_unclassified_paths_turn_on_webui():
    paths = ["kollab/core.py", "docs/guide.md", "packages/kollabor-webui-old/x.ts"]
    assert ci_changed_lanes.lanes_for(paths) == {"webui": True, "voice": False}


def test_changes_job_pick_step_uses_fail_closed_shell():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    pick_step = next(
        step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "pick"
    )

    assert pick_step.get("shell") == "bash"
    assert "set -euo pipefail" in pick_step["run"]
    assert 'git diff --no-renames --name-only "$BASE_SHA" "$HEAD_SHA"' in pick_step["run"]
    assert '| python scripts/ci_changed_lanes.py >> "$GITHUB_OUTPUT"' not in pick_step["run"]


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
