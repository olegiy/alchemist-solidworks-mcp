import importlib.util
from pathlib import Path

import pytest


def oracle():
    path = Path(__file__).resolve().parents[3] / "tools" / "cad_oracle.py"
    spec = importlib.util.spec_from_file_location("cad_oracle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_numeric_tolerance():
    expected = {"volume": {"value": 6000, "unit": "mm3", "relative_tolerance": 0.0001}}
    assert oracle().evaluate(expected, {"volume": {"value": 6000.5, "unit": "mm3"}})["status"] == "passed"
    assert oracle().evaluate(expected, {"volume": {"value": 6100, "unit": "mm3"}})["status"] == "failed"


@pytest.mark.parametrize("value", [True, "6000", float("nan"), float("inf"), None])
def test_invalid_actual_is_unknown(value):
    expected = {"volume": {"value": 6000, "unit": "mm3"}}
    assert oracle().evaluate(expected, {"volume": {"value": value, "unit": "mm3"}})["status"] == "unknown"


def test_missing_geometry_cannot_be_proved_by_volume():
    expected = {"volume": {"value": 6000, "unit": "mm3"}, "holes": {"value": 4, "unit": "count"}}
    assert oracle().evaluate(expected, {"volume": {"value": 6000, "unit": "mm3"}})["status"] == "unknown"


def test_wrong_units_and_readback_error():
    expected = {"height": {"value": 10, "unit": "mm"}}
    for actual in ({"value": 10, "unit": "m"}, {"value": 10, "unit": "mm", "error": "readback"}):
        assert oracle().evaluate(expected, {"height": actual})["status"] == "unknown"


def test_boolean_is_not_truthiness():
    expected = {"rebuild_ok": {"value": True}}
    assert oracle().evaluate(expected, {"rebuild_ok": {"value": "false"}})["status"] == "unknown"
    assert oracle().evaluate(expected, {"rebuild_ok": {"value": False}})["status"] == "failed"


def test_relation_mismatch_and_empty_expectations():
    assert oracle().evaluate({"relation": {"value": "coincident"}}, {"relation": {"value": "fixed"}})["status"] == "failed"
    assert oracle().evaluate({}, {})["status"] == "unknown"


def test_decimal_comma_boundary_parser():
    assert oracle().parse_cad_number("4,22503E-06") == 4.22503e-6
    for value in (True, "NaN", "1,234.5", "false", float("inf")):
        assert oracle().parse_cad_number(value) is None


def test_analytic_csg_geometry():
    import math
    values = oracle().boss_plate_geometry(15)
    assert values["volume"] == pytest.approx(60000 + 1140 * math.pi)
    assert values["area"] == pytest.approx(15200 + 468 * math.pi)
    changed = oracle().boss_plate_geometry(16.5)
    assert changed["volume"] == pytest.approx(60000 + 1290 * math.pi)
    assert changed["area"] == pytest.approx(15200 + 498 * math.pi)


def test_parametric_audit_requires_return_and_dependencies():
    expected = {"height": {"value": 10, "unit": "mm"}}
    changed = {"height": {"value": 11, "unit": "mm"}}
    observations = {
        "baseline": expected, "changed": changed, "restored": expected,
        "dependencies_preserved": True, "separate_copy": True,
    }
    assert oracle().evaluate_parametric(expected, changed, observations)["status"] == "passed"
    observations["dependencies_preserved"] = "true"
    assert oracle().evaluate_parametric(expected, changed, observations)["status"] == "unknown"
    observations["dependencies_preserved"] = True
    del observations["restored"]
    assert oracle().evaluate_parametric(expected, changed, observations)["status"] == "unknown"


def test_invalid_expectation_never_passes():
    for value in (float("nan"), float("inf"), None, [], {}):
        assert oracle().evaluate({"x": {"value": value}}, {"x": {"value": value}})["status"] == "unknown"


def test_empty_changed_audit_never_passes():
    observed = {"baseline": {}, "changed": {}, "restored": {},
                "separate_copy": True, "dependencies_preserved": True}
    assert oracle().evaluate_parametric({}, {}, observed)["status"] == "unknown"


@pytest.mark.parametrize("tolerance", [True, -1, "0.01", float("nan"), float("inf")])
def test_invalid_tolerances_never_pass(tolerance):
    expected = {"x": {"value": 10, "unit": "mm", "absolute_tolerance": tolerance}}
    assert oracle().evaluate(expected, {"x": {"value": 10, "unit": "mm"}})["status"] == "unknown"


def test_readback_error_prevents_equal_value_acceptance():
    expected = {"x": {"value": 10, "unit": "mm"}}
    observed = {"x": {"value": 10, "unit": "mm", "error": "COM read failed"}}
    assert oracle().evaluate(expected, observed)["status"] == "unknown"
