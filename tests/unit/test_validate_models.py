"""Focused validation tests for bundled model catalog prices."""

import importlib.util
import json
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
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(-1, id="negative"),
        pytest.param(-(10**400), id="large-negative"),
        pytest.param(math.inf, id="infinite"),
        pytest.param(math.nan, id="nan"),
        pytest.param(True, id="bool"),
        pytest.param("0.25", id="string"),
    ],
)
def test_rejects_invalid_catalog_prices(field, value):
    errors = validator.validate(_catalog_with_price(field, value))

    assert any(f"model-0: {field} must be" in error for error in errors)


@pytest.mark.parametrize("field", PRICE_FIELDS)
def test_F1_rejects_unrepresentable_integer_catalog_prices(field):
    errors = validator.validate(_catalog_with_price(field, 10**400))

    assert any(f"model-0: {field} must be" in error for error in errors)


@pytest.mark.parametrize("field", PRICE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(10**10000, id="huge"),
        pytest.param(-(10**10000), id="huge-negative"),
        pytest.param([10**10000], id="huge-in-list"),
    ],
)
def test_oversized_integer_catalog_prices_report_field_error(field, value):
    errors = validator.validate(_catalog_with_price(field, value))

    assert any(f"model-0: {field} must be" in error for error in errors)


@pytest.mark.parametrize("field", ["context_window", "max_output"])
def test_oversized_negative_limits_report_field_error(field):
    errors = validator.validate(_catalog_with_price(field, -(10**10000)))

    assert any(f"model-0: {field} must be" in error for error in errors)


@pytest.mark.parametrize("field", PRICE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [None, 0, 0.25, pytest.param(10**308, id="large-representable-integer")],
)
def test_accepts_unknown_and_nonnegative_catalog_prices(field, value):
    errors = validator.validate(_catalog_with_price(field, value))

    assert not errors


@pytest.mark.parametrize("field", PRICE_FIELDS)
@pytest.mark.parametrize(
    "literal",
    [
        pytest.param("9" * 5000, id="huge"),
        pytest.param("-" + "9" * 5000, id="huge-negative"),
    ],
)
def test_F1_json_oversized_integer_price_reports_field_error(
    field, literal, tmp_path, monkeypatch, capsys
):
    # json.loads raises ValueError for a literal past Python's int-from-str
    # digit limit, before validate() sees the field. main() must report it.
    text = json.dumps(_catalog_with_price(field, "PLACEHOLDER"))
    registry = tmp_path / "models.json"
    registry.write_text(text.replace('"PLACEHOLDER"', literal))
    monkeypatch.setattr(validator, "REGISTRY", registry)

    assert validator.main() == 1
    assert f"FAIL: model-0: {field} must be" in capsys.readouterr().out


@pytest.mark.parametrize(
    "literal",
    [
        pytest.param("9" * 5000, id="huge-integer"),
        pytest.param("1", id="small-integer"),
    ],
)
def test_non_string_registry_date_reports_field_error(
    literal, tmp_path, monkeypatch, capsys
):
    # A number in _meta.updated must be reported as a bad date, not raised
    # out of main() by date.fromisoformat.
    models = {
        f"model-{index}": {"provider": "openrouter", "context_window": 1000}
        for index in range(5)
    }
    text = json.dumps({"models": models, "_meta": {"updated": "PLACEHOLDER"}})
    registry = tmp_path / "models.json"
    registry.write_text(text.replace('"PLACEHOLDER"', literal))
    monkeypatch.setattr(validator, "REGISTRY", registry)

    assert validator.main() == 1
    assert "FAIL: _meta.updated is not an ISO date" in capsys.readouterr().out
