#!/usr/bin/env python3
"""Validate the model registry (bundles/data/models.json).

A gate to keep the registry well-formed and current. Run in CI / pre-commit;
exits non-zero with a clear report on any problem. Two kinds of checks:

  * schema   — every entry has the fields compaction, the tool-output budget and pricing rely on
  * freshness — the registry must have been reviewed within MAX_AGE_DAYS, so new
                frontier models don't silently rot out of the list

To clear a freshness failure: review current model releases, add/refresh
entries, and bump ``_meta.updated`` to today.
"""

import json
import math
import sys
from datetime import date
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "bundles" / "data" / "models.json"
MAX_AGE_DAYS = 120
REQUIRED_FIELDS = ("provider", "context_window")
BOOL_FIELDS = (
    "supports_tools",
    "supports_thinking",
    "supports_streaming",
    "supports_vision",
    "supports_sampling",
    "retired",
)
PRICE_FIELDS = ("pricing_in", "pricing_out", "pricing_cached_in")


def _describe(value: object) -> str:
    """Render a rejected value for an error message without raising.

    repr() raises ValueError for an int past Python's int-to-str digit limit,
    and a list or dict can hide one. So only None, bool, float, str and ints
    under 10**300 are printed; any other value is named by its type.
    """
    if value is None or isinstance(value, (bool, float, str)):
        return repr(value)
    if isinstance(value, int) and abs(value) < 10**300:
        return repr(value)
    return f"<{type(value).__name__}>"


def _parse_json_int(text: str) -> int | float:
    """Read a JSON integer literal without raising past Python's digit limit.

    int() refuses a literal longer than sys.get_int_max_str_digits() with a
    plain ValueError, which json.loads does not wrap. Such a value is
    unrepresentable, so it reads as the float it overflows to (inf or -inf);
    the checks in validate() reject that as a field error.
    """
    try:
        return int(text)
    except ValueError:
        return float(text)


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
        if isinstance(cw, bool) or not isinstance(cw, int) or cw <= 0:
            errors.append(
                f"{name}: context_window must be a positive int, got {_describe(cw)}"
            )
        out = info.get("max_output")
        if out is not None and (
            isinstance(out, bool) or not isinstance(out, int) or out <= 0
        ):
            errors.append(
                f"{name}: max_output must be a positive int or null, got {_describe(out)}"
            )
        for field in PRICE_FIELDS:
            price = info.get(field)
            if price is None:
                continue
            valid_price = (
                not isinstance(price, bool)
                and isinstance(price, (int, float))
                and price >= 0
            )
            if valid_price:
                try:
                    valid_price = math.isfinite(float(price))
                except OverflowError, TypeError, ValueError:
                    valid_price = False
            if not valid_price:
                errors.append(
                    f"{name}: {field} must be a finite non-negative number or null, got {_describe(price)}"
                )
        for field in BOOL_FIELDS:
            if field in info and not isinstance(info[field], bool):
                errors.append(f"{name}: {field} must be true/false")
        levels = info.get("effort_levels")
        if levels is not None and not (isinstance(levels, list) and all(isinstance(x, str) for x in levels)):
            errors.append(f"{name}: effort_levels must be a list of level names")

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
        except TypeError, ValueError:
            errors.append(f"_meta.updated is not an ISO date: {updated!r}")

    return errors


def main() -> int:
    try:
        data = json.loads(REGISTRY.read_text(), parse_int=_parse_json_int)
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
