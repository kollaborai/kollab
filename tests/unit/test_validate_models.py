"""Focused validation tests for bundled model catalog prices."""

import importlib.util
import math
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "validate_models.py"

_spec = importlib.util.spec_from_file_location("kollab_validate_models", SCRIPT)
assert _spec and _spec.loader
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)

PRICE_FIELDS = ("pricing_in", "pricing_out", "pricing_cached_in")


def _catalog_with_price(field: str, value) -> dict:
    models = {
        f"model-{index}": {"provider": "openrouter", "context_window": 1000}
        for index in range(5)
    }
    models["model-0"][field] = value
    return {"models": models, "_meta": {"updated": date.today().isoformat()}}


@pytest.mark.parametrize("field", PRICE_FIELDS)
@pytest.mark.parametrize("value", [-1, math.inf, math.nan, True, "0.25"])
def test_rejects_invalid_catalog_prices(field, value):
    errors = validator.validate(_catalog_with_price(field, value))

    assert any(f"model-0: {field} must be" in error for error in errors)


@pytest.mark.parametrize("field", PRICE_FIELDS)
@pytest.mark.parametrize("value", [None, 0, 0.25])
def test_accepts_unknown_and_nonnegative_catalog_prices(field, value):
    errors = validator.validate(_catalog_with_price(field, value))

    assert not errors
