"""Tests for the retrospective page generator skeleton (issue #730, Slice A).

Slice A pins the script's shape before any collector has data: the config
schema (retro section, enabled default-off), the HTML renderer with all six
sections present, and missing-data labeling ("missing", never zero). All
GitHub access goes through an injected fake runner — no test touches the
network or the real `gh` binary.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "build_retro.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_retro", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


retro = _load()


def _write_config(tmp_path, body):
    path = tmp_path / "retro.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class TestConfig:
    def test_disabled_by_default(self):
        cfg = retro.parse_config({})
        assert cfg.enabled is False

    def test_repo_required_when_enabled(self, tmp_path):
        with pytest.raises(retro.ConfigError):
            retro.parse_config({"retro": {"enabled": True}})

    def test_full_config_parses(self, tmp_path):
        cfg = retro.parse_config(
            {
                "retro": {
                    "enabled": True,
                    "repo": "kollaborai/kollab",
                    "range_days": 14,
                    "output_dir": "outputs/retro",
                    "kpi_targets": {"throughput_per_day": 3.0},
                    "incident_label": "status/incident",
                    "incidents": [123, 456],
                    "agents": ["lapis", "bismuth"],
                }
            }
        )
        assert cfg.repo == "kollaborai/kollab"
        assert cfg.range_days == 14
        assert cfg.output_dir == "outputs/retro"
        assert cfg.kpi_targets == {"throughput_per_day": 3.0}
        assert cfg.incident_label == "status/incident"
        assert cfg.incidents == (123, 456)
        assert cfg.agents == ("lapis", "bismuth")

    def test_defaults(self):
        cfg = retro.parse_config({"retro": {"enabled": False, "repo": "o/r"}})
        assert cfg.range_days == 7
        assert cfg.output_dir == "outputs/retro"
        assert cfg.kpi_targets == {}
        assert cfg.incident_label == ""
        assert cfg.incidents == ()
        assert cfg.agents == ()

    def test_range_days_must_be_positive_int(self):
        with pytest.raises(retro.ConfigError):
            retro.parse_config(
                {"retro": {"enabled": False, "repo": "o/r", "range_days": 0}}
            )

    def test_repo_shape_enforced_even_when_disabled(self):
        with pytest.raises(retro.ConfigError):
            retro.parse_config({"retro": {"repo": "no-slash"}})

    def test_incidents_must_be_ints(self):
        with pytest.raises(retro.ConfigError):
            retro.parse_config(
                {"retro": {"repo": "o/r", "incidents": ["123"]}}
            )


class TestRenderer:
    def test_all_six_sections_present_with_placeholder_data(self):
        report = retro.build_report(
            config=retro.parse_config(
                {"retro": {"repo": "kollaborai/kollab"}}
            ),
            now=retro.parse_time("2026-11-04T12:00:00Z"),
            data={},
        )
        for heading in (
            "Shipped",
            "Dragging",
            "Incidents",
            "KPIs vs targets",
            "Agent activity",
            "Issues / plan status",
        ):
            assert heading in report, f"section missing: {heading}"

    def test_missing_data_labeled_missing_never_zero(self):
        report = retro.build_report(
            config=retro.parse_config(
                {"retro": {"repo": "kollaborai/kollab"}}
            ),
            now=retro.parse_time("2026-11-04T12:00:00Z"),
            data={},
        )
        assert "missing" in report.lower()
        # A zero would masquerade as measured data.
        assert "0 PRs merged" not in report

    def test_html_escapes_repo_derived_strings(self):
        report = retro.build_report(
            config=retro.parse_config(
                {"retro": {"repo": "o<r>/r"}}
            ),
            now=retro.parse_time("2026-11-04T12:00:00Z"),
            data={},
        )
        assert "o&lt;r&gt;/r" in report
        assert "o<r>/r" not in report

    def test_links_cite_source_numbers_when_present(self):
        report = retro.build_report(
            config=retro.parse_config(
                {"retro": {"repo": "kollaborai/kollab"}}
            ),
            now=retro.parse_time("2026-11-04T12:00:00Z"),
            data={
                "shipped": [
                    {"number": 772, "title": "capture cursor", "url": "u772"}
                ],
            },
        )
        assert "772" in report
        assert "u772" in report


class TestGolden:
    def test_skeleton_report_is_stable(self, tmp_path):
        """Byte-stability of the placeholder render: the skeleton's output
        must not drift between runs with identical inputs."""
        cfg = retro.parse_config({"retro": {"repo": "kollaborai/kollab"}})
        a = retro.build_report(
            config=cfg, now=retro.parse_time("2026-11-04T12:00:00Z"), data={}
        )
        b = retro.build_report(
            config=cfg, now=retro.parse_time("2026-11-04T12:00:00Z"), data={}
        )
        assert a == b

    def test_main_returns_clean_error_on_malformed_config(self, tmp_path, capsys):
        """A malformed config file is a clean CLI error (exit 2, one stderr
        line), never a traceback — matches the pr_throughput_kpi convention."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert (
            retro.main(["--config", str(bad)]) == 2
        )
        err = capsys.readouterr().err
        assert "config error" in err
        assert "Traceback" not in err

    def test_main_returns_zero_when_disabled(self, tmp_path, capsys):
        cfg = _write_config(tmp_path, {"retro": {"enabled": False, "repo": "o/r"}})
        assert retro.main(["--config", str(cfg)]) == 0
        assert "disabled" in capsys.readouterr().out

    def test_write_outputs_creates_html_file(self, tmp_path):
        cfg = retro.parse_config(
            {"retro": {"repo": "kollaborai/kollab", "output_dir": str(tmp_path)}}
        )
        out = retro.write_outputs(
            config=cfg,
            now=retro.parse_time("2026-11-04T12:00:00Z"),
            data={},
        )
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert text.startswith("<!DOCTYPE html>")
        assert "Shipped" in text

class TestNonObjectRoot:
    """zircon P2 on 8eaa2804: parse_config assumes a dict root. Valid JSON
    like [1,2] reaches data.get -> uncaught AttributeError -> traceback +
    exit 1, instead of the clean config-error stderr/exit 2 path."""

    def test_parse_config_rejects_list_root(self):
        with pytest.raises(retro.ConfigError):
            retro.parse_config([1, 2])

    def test_parse_config_rejects_string_root(self):
        with pytest.raises(retro.ConfigError):
            retro.parse_config("oops")

    def test_main_list_root_config_is_clean_error_not_traceback(
        self, tmp_path, capsys
    ):
        """CLI regression: --config pointing at a valid-JSON list must exit
        2 with one stderr line, never an AttributeError traceback."""
        bad = tmp_path / "list-root.json"
        bad.write_text("[1, 2]", encoding="utf-8")
        assert retro.main(["--config", str(bad)]) == 2
        err = capsys.readouterr().err
        assert "config error" in err
        assert "Traceback" not in err
