#!/usr/bin/env python3
"""Retrospective page generator: rebuildable HTML retro from GitHub data.

Implements kollaborai/kollab#730 as a standalone script, matching the
`build_dedup_report.py` / `pr_throughput_kpi.py` form factor. Off by
default: nothing runs unless the config sets `retro.enabled: true`. The
script is repo-agnostic — the repo, date range, KPI targets, incident
list/label, and agents all come from the `retro` config section.

Sections (Slice A renders all six; collectors fill them in Slices B/C):

- Shipped — PRs merged inside the window.
- Dragging — open PRs aging past thresholds inside the window.
- Incidents — config-listed issues first-class, optional label filter.
- KPIs vs targets — explicit `retro.kpi_targets` mapping; absent targets
  render "no target configured". No magic sources.
- Agent activity — per-agent PR/review/comment counts (GitHub-side only;
  hub presence/logs are out of scope v1 by ruling).
- Issues / plan status — open issues and plan state in the window.

Every number links to its source (issue/PR URL). Missing data is rendered
as "missing", never zero — a metric whose collector did not run or whose
inputs failed is missing, while a real zero stays zero.

Data comes only from the GitHub REST API through `gh`. The script is a
pure reader: it never labels, comments, closes, or merges anything.

Slice A output: the HTML skeleton with all sections present and every
collector's data labeled missing until Slices B/C wire the collectors in.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_RANGE_DAYS = 7
DEFAULT_OUTPUT_DIR = "outputs/retro"

SECTIONS = (
    "Shipped",
    "Dragging",
    "Incidents",
    "KPIs vs targets",
    "Agent activity",
    "Issues / plan status",
)

COLLECTOR_KEYS = (
    "shipped",
    "dragging",
    "incidents",
    "kpis",
    "agent_activity",
    "issues",
)


class ConfigError(ValueError):
    """The `retro` config section is invalid."""


@dataclass(frozen=True)
class RetroConfig:
    enabled: bool = False
    repo: str | None = None
    range_days: int = DEFAULT_RANGE_DAYS
    output_dir: str = DEFAULT_OUTPUT_DIR
    kpi_targets: dict[str, float] = field(default_factory=dict)
    incident_label: str = ""
    incidents: tuple[int, ...] = ()
    agents: tuple[str, ...] = ()


def parse_config(data: dict[str, Any]) -> RetroConfig:
    """Build RetroConfig from a parsed config document (reads its `retro` key)."""
    if not isinstance(data, dict):
        # Valid JSON with a non-object root (e.g. a list) is a config
        # error, not a crash: the CLI's config-error path must catch it.
        raise ConfigError("config root must be a JSON object")
    section = data.get("retro") or {}
    if not isinstance(section, dict):
        raise ConfigError("retro must be an object")
    if not section:
        # No retro section at all: the feature is simply absent (disabled
        # defaults), not a config error.
        return RetroConfig()
    enabled = bool(section.get("enabled", False))
    repo = section.get("repo")
    # The repo shape is validated even when disabled: a config that would
    # fail on the first enabled run is a config error, not a silent default.
    if not isinstance(repo, str) or repo.count("/") != 1:
        raise ConfigError("retro.repo must be 'owner/name'")
    range_days = section.get("range_days", DEFAULT_RANGE_DAYS)
    if not isinstance(range_days, int) or isinstance(range_days, bool) or range_days <= 0:
        raise ConfigError("retro.range_days must be a positive integer (days)")
    output_dir = section.get("output_dir", DEFAULT_OUTPUT_DIR)
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError("retro.output_dir must be a non-empty string")
    targets = section.get("kpi_targets", {})
    if not isinstance(targets, dict) or any(
        not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool)
        for k, v in targets.items()
    ):
        raise ConfigError(
            "retro.kpi_targets must map metric names to numeric targets"
        )
    incident_label = section.get("incident_label", "")
    if not isinstance(incident_label, str):
        raise ConfigError("retro.incident_label must be a string")
    incidents = section.get("incidents", [])
    if not isinstance(incidents, list) or any(
        not isinstance(n, int) or isinstance(n, bool) for n in incidents
    ):
        raise ConfigError("retro.incidents must be a list of issue numbers")
    agents = section.get("agents", [])
    if not isinstance(agents, list) or any(not isinstance(a, str) for a in agents):
        raise ConfigError("retro.agents must be a list of login strings")
    return RetroConfig(
        enabled=enabled,
        repo=repo,
        range_days=range_days,
        output_dir=output_dir,
        kpi_targets={k: float(v) for k, v in targets.items()},
        incident_label=incident_label,
        incidents=tuple(incidents),
        agents=tuple(agents),
    )


def load_config(path: Path) -> RetroConfig:
    return parse_config(json.loads(path.read_text(encoding="utf-8")))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --------------------------------------------------------------- rendering


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _section_body(key: str, payload: Any) -> str:
    """One section's rows from collector data, or the missing-data notice.

    Slice A ships no collectors, so the default payload is None -> the
    notice. Slices B/C land collectors that pass real rows here.
    """
    if payload is None:
        return (
            '<p class="missing">missing — collector not wired in this '
            "slice; nothing measured, no zero reported</p>"
        )
    if key == "kpis":
        rows = []
        for metric, value in sorted(payload.items()):
            target = None
            if isinstance(value, dict):
                value, target = value.get("value"), value.get("target")
            if value is None:
                shown = "missing"
            else:
                shown = f"{_esc(value)}"
            target_note = (
                f" (target {_esc(target)})" if target is not None else (
                    " — no target configured" if target is None else ""
                )
            )
            rows.append(f"<li>{_esc(metric)}: {shown}{_esc(target_note)}</li>")
        return f"<ul>{''.join(rows)}</ul>" if rows else (
            '<p class="missing">missing — no metrics collected</p>'
        )
    items = []
    for entry in payload:
        label = _esc(entry.get("title") or entry.get("number"))
        link = entry.get("url")
        number = entry.get("number")
        text = f"#{number} " if number is not None else ""
        body = f'{text}<a href="{_esc(link)}">{label}</a>' if link else f"{text}{label}"
        items.append(f"<li>{body}</li>")
    return f"<ul>{''.join(items)}</ul>" if items else "<p>none</p>"


def build_report(
    *, config: RetroConfig, now: datetime | None, data: dict[str, Any]
) -> str:
    """Render the retro HTML document.

    All six sections are always present; a section whose collector did not
    produce data renders its missing-data notice. Identical inputs render
    byte-identical output (no timestamps in the body; `now` only derives
    the window shown in the header).
    """
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=config.range_days)
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head><meta charset='utf-8'>",
        f"<title>retro {_esc(config.repo)}"
        f" {window_start:%Y-%m-%d}..{window_end:%Y-%m-%d}</title>",
        "</head>",
        "<body>",
        f"<h1>retro — {_esc(config.repo)}</h1>",
        f"<p>window {window_start:%Y-%m-%d} .. {window_end:%Y-%m-%d}"
        f" ({config.range_days} days)</p>",
    ]
    for key, heading in zip(COLLECTOR_KEYS, SECTIONS):
        parts.append(f"<h2>{heading}</h2>")
        parts.append(_section_body(key, data.get(key)))
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def write_outputs(
    *, config: RetroConfig, now: datetime | None, data: dict[str, Any]
) -> Path:
    """Write the retro HTML under the configured output dir; return its path."""
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "retro.html"
    out.write_text(
        build_report(config=config, now=now, data=data), encoding="utf-8"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the retrospective HTML page (issue #730)."
    )
    parser.add_argument("--config", required=True, help="path to the JSON config")
    parser.add_argument(
        "--json-out",
        help="optional path for the collected raw data artifact (JSON)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except (OSError, json.JSONDecodeError, ConfigError) as exc:
        print(f"build_retro: config error: {exc}", file=sys.stderr)
        return 2
    if not config.enabled:
        print("retro is disabled (retro.enabled is false); nothing to do")
        return 0

    data: dict[str, Any] = {key: None for key in COLLECTOR_KEYS}
    out = write_outputs(config=config, now=datetime.now(timezone.utc), data=data)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(data), encoding="utf-8")
    print(f"retro written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
