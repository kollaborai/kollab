#!/usr/bin/env python3
"""Validate the model registry (bundles/data/models.json).

A gate to keep the registry well-formed and current. Run in CI / pre-commit;
exits non-zero with a clear report on any problem. Two kinds of checks:

  * schema   — every entry has the fields the budget guard and pricing rely on
  * freshness — the registry must have been reviewed within MAX_AGE_DAYS, so new
                frontier models don't silently rot out of the list

To clear a freshness failure: review current model releases, add/refresh
entries, and bump ``_meta.updated`` to today.
"""

import json
import sys
from datetime import date
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "bundles" / "data" / "models.json"
MAX_AGE_DAYS = 120
REQUIRED_FIELDS = ("provider", "context_window")
BOOL_FIELDS = ("supports_tools", "supports_thinking", "supports_streaming", "supports_vision")


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    models = data.get("models", {})

    if len(models) < 5:
        errors.append(f"only {len(models)} models — registry looks incomplete")

    for name, info in models.items():
        if not isinstance(info, dict):
            errors.append(f"{name}: entry must be an object")
            continue
        for field in REQUIRED_FIELDS:
            if field not in info:
                errors.append(f"{name}: missing required field '{field}'")
        cw = info.get("context_window")
        if not isinstance(cw, int) or cw <= 0:
            errors.append(f"{name}: context_window must be a positive int, got {cw!r}")
        out = info.get("max_output")
        if out is not None and (not isinstance(out, int) or out <= 0):
            errors.append(f"{name}: max_output must be a positive int or null, got {out!r}")
        for field in BOOL_FIELDS:
            if field in info and not isinstance(info[field], bool):
                errors.append(f"{name}: {field} must be true/false")

    updated = data.get("_meta", {}).get("updated")
    if not updated:
        errors.append("_meta.updated is missing")
    else:
        try:
            age = (date.today() - date.fromisoformat(updated)).days
            if age > MAX_AGE_DAYS:
                errors.append(
                    f"registry is {age} days stale (max {MAX_AGE_DAYS}). Review "
                    "current model releases, refresh entries, bump _meta.updated."
                )
        except ValueError:
            errors.append(f"_meta.updated is not an ISO date: {updated!r}")

    return errors


def main() -> int:
    try:
        data = json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read {REGISTRY}: {exc}")
        return 1

    errors = validate(data)
    for err in errors:
        print(f"FAIL: {err}")
    if errors:
        print(f"\n{len(errors)} problem(s) in {REGISTRY}")
        return 1

    updated = data["_meta"]["updated"]
    print(f"OK: {len(data['models'])} models, registry fresh ({updated})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
