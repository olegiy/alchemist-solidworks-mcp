import json

import pytest


def test_disabled_does_not_write(tmp_path, monkeypatch):
    import perf_trace
    monkeypatch.delenv("SWMCP_PERF", raising=False)
    target = tmp_path / "perf.jsonl"
    with perf_trace.span("session", "op", sink=target):
        pass
    assert not target.exists()


def test_clock_and_exception_preserved(tmp_path, monkeypatch):
    import perf_trace
    monkeypatch.setenv("SWMCP_PERF", "1")
    ticks = iter([100, 150])
    target = tmp_path / "perf.jsonl"
    with pytest.raises(ValueError):
        with perf_trace.span("transport", "op", sink=target, clock=lambda: next(ticks)):
            raise ValueError("private model path")
    row = json.loads(target.read_text())
    assert row == {"operation_id": "op", "span": "transport", "duration_ns": 50, "outcome": "error"}
    assert "private" not in target.read_text()


def test_invalid_span_never_logs_payload(tmp_path, monkeypatch):
    import perf_trace
    monkeypatch.setenv("SWMCP_PERF", "1")
    target = tmp_path / "perf.jsonl"
    with perf_trace.span("C:/secret/model", "op", sink=target):
        pass
    assert not target.exists()


def test_sink_failure_does_not_change_operation(tmp_path, monkeypatch):
    import perf_trace
    monkeypatch.setenv("SWMCP_PERF", "1")
    with perf_trace.span("session", "op", sink=tmp_path):
        pass


def test_nested_spans_are_separate_not_summed(tmp_path, monkeypatch):
    import perf_trace
    monkeypatch.setenv("SWMCP_PERF", "1")
    ticks = iter([0, 10, 30, 50])
    target = tmp_path / "perf.jsonl"
    clock = lambda: next(ticks)
    with perf_trace.span("session", "op", sink=target, clock=clock):
        with perf_trace.span("transport", "op", sink=target, clock=clock):
            pass
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [(row["span"], row["duration_ns"]) for row in rows] == [("transport", 20), ("session", 50)]
