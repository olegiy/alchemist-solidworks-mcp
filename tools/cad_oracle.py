import math
import re


def parse_cad_number(value):
    if isinstance(value, str):
        if not re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?", value.strip()):
            return None
        value = float(value.strip().replace(",", "."))
    return value if _number(value) else None


def boss_plate_geometry(height):
    if not _number(height) or height <= 0:
        raise ValueError("positive finite boss height required")
    base_volume = 100 * 60 * 10
    hole_volume = 4 * math.pi * 3 ** 2 * 10
    base_area = 2 * (100 * 60 + 100 * 10 + 60 * 10)
    hole_area_delta = 4 * (2 * math.pi * 3 * 10 - 2 * math.pi * 3 ** 2)
    return {"volume": base_volume + math.pi * 10 ** 2 * height - hole_volume,
            "area": base_area + hole_area_delta + 2 * math.pi * 10 * height}


def evaluate_parametric(baseline, changed, observed):
    if not isinstance(observed, dict):
        return {"status": "unknown", "checks": []}
    checks = []
    for phase, expected in (("baseline", baseline), ("changed", changed), ("restored", baseline)):
        result = evaluate(expected, observed.get(phase))
        checks.extend(dict(check, criterion=phase + "." + check["criterion"]) for check in result["checks"])
        if not result["checks"]:
            checks.append({"criterion": phase, "status": "unknown"})
    for criterion in ("separate_copy", "dependencies_preserved"):
        value = observed.get(criterion)
        checks.append({"criterion": criterion, "status": "unknown" if type(value) is not bool else "passed" if value else "failed"})
    statuses = {check["status"] for check in checks}
    return {"status": "failed" if "failed" in statuses else "unknown" if "unknown" in statuses else "passed", "checks": checks}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def evaluate(expected, observed):
    checks = []
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return {"status": "unknown", "checks": [], "reason": "invalid observations"}
    for criterion, target in expected.items():
        actual = observed.get(criterion)
        check = {"criterion": criterion, "status": "unknown"}
        checks.append(check)
        if not isinstance(target, dict) or "value" not in target:
            check["reason"] = "invalid expectation"
            continue
        if not isinstance(actual, dict) or "value" not in actual or actual.get("error"):
            check["reason"] = "readback unavailable"
            continue
        if actual.get("unit") != target.get("unit"):
            check["reason"] = "unit mismatch"
            continue
        wanted, value = target["value"], actual["value"]
        if _number(wanted):
            absolute = target.get("absolute_tolerance", 0)
            relative = target.get("relative_tolerance", 0)
            if not _number(value) or not all(_number(t) and t >= 0 for t in (absolute, relative)):
                check["reason"] = "invalid numeric readback or tolerance"
                continue
            passed = abs(value - wanted) <= max(absolute, abs(wanted) * relative)
        elif type(wanted) in (str, bool) and type(value) is type(wanted):
            passed = value == wanted
        else:
            check["reason"] = "unsupported or mismatched type"
            continue
        check.update(status="passed" if passed else "failed", expected=wanted, actual=value)
        if "unit" in target:
            check["unit"] = target["unit"]
    statuses = {check["status"] for check in checks}
    status = "failed" if "failed" in statuses else "unknown" if not checks or "unknown" in statuses else "passed"
    return {"status": status, "checks": checks}
