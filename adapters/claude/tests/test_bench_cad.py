import importlib.util
from pathlib import Path


def bench():
    path = Path(__file__).resolve().parents[3] / "tools" / "bench_cad.py"
    spec = importlib.util.spec_from_file_location("bench_cad", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_has_six_independent_tasks():
    corpus = bench().load_corpus()
    assert corpus["version"] == 1
    assert len(corpus["scenarios"]) == 6
    assert {(s["task"], s["part"]) for s in corpus["scenarios"]} == {
        (task, part) for task in ("build", "edit", "repair") for part in ("plate", "boss_plate")
    }
    for scenario in corpus["scenarios"]:
        assert scenario["initial_state"]
        assert scenario["expected"]
        assert scenario["dependencies"]


def test_paired_analysis_is_seeded_and_not_confirmation_with_small_sample():
    result = bench().analyze_pairs([10] * 12, [8] * 12, seed=42)
    assert result == bench().analyze_pairs([10] * 12, [8] * 12, seed=42)
    assert result["verdict"] == "no evidence"
    assert abs(result["ratio"] - 0.8) < 1e-10


def test_unknown_outcome_blocks_speed_claim():
    result = bench().analyze_pairs([10] * 30, [8] * 30, seed=42, quality_ok=False)
    assert result["verdict"] == "quality regression"


def test_holm_adjustment():
    assert bench().holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def test_all_attempts_count_in_correct_result_cost():
    result = bench().summarize_runs([
        {"elapsed": 10, "status": "passed"},
        {"elapsed": 20, "status": "unknown"},
        {"elapsed": 30, "status": "failed"},
    ])
    assert result["cost_per_correct"] == 60
    assert result["successes"] == 1
    assert result["p95_descriptive_only"] is True
    assert bench().summarize_runs([{"elapsed": 4, "status": "unknown"}])["cost_per_correct"] is None


def test_schedule_balanced_and_reproducible():
    schedule = bench().paired_schedule(30, seed=19)
    assert schedule == bench().paired_schedule(30, seed=19)
    assert schedule.count("AB") == schedule.count("BA") == 15


def test_replay_unknown_stops_without_resending():
    import asyncio
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {"running": True, "job_id": "owned-job"}

    result = asyncio.run(bench().replay_steps(call, [{"tool": "modify_dimension", "arguments": {}},
                                                   {"tool": "extrude", "arguments": {}}]))
    assert calls == ["modify_dimension"]
    assert result["status"] == "unknown"
    assert result["unexecuted"] == [1]


def test_replay_quality_failure_stops_tail():
    import asyncio

    async def call(name, arguments):
        return {"verification": {"status": "failed"}}

    result = asyncio.run(bench().replay_steps(call, [{"tool": "verify_part_result", "arguments": {}},
                                                   {"tool": "extrude", "arguments": {}}]))
    assert result["status"] == "failed"
    assert result["unexecuted"] == [1]


def test_replay_preflights_entire_sequence_before_mutation():
    import asyncio
    import pytest
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {}

    with pytest.raises(ValueError):
        asyncio.run(bench().replay_steps(call, [{"tool": "modify_dimension", "arguments": {}},
                                              {"tool": "close_document", "arguments": {"close_all": True}}]))
    assert calls == []


def test_replay_api_success_is_never_independent_success():
    import asyncio

    async def call(name, arguments):
        return {"status": "COMPLETED", "verified": True}

    result = asyncio.run(bench().replay_steps(call, [{"tool": "modify_dimension", "arguments": {}}]))
    assert result["status"] == "unverified"


def test_replay_budget_does_not_cancel_or_retry_mutation():
    import asyncio
    calls = []
    ticks = iter([0, 0, 601, 601])

    async def call(name, arguments):
        calls.append(name)
        return {"status": "COMPLETED"}

    result = asyncio.run(bench().replay_steps(call, [{"tool": "modify_dimension", "arguments": {}},
                                                   {"tool": "extrude", "arguments": {}}],
                                            budget_seconds=600, clock=lambda: next(ticks)))
    assert calls == ["modify_dimension"]
    assert result["status"] == "timeout"
    assert result["unexecuted"] == [1]


def test_mcp_result_decoding_is_fail_closed():
    from types import SimpleNamespace
    module = bench()
    assert module.decode_mcp_result(SimpleNamespace(isError=False, structuredContent={"status": "COMPLETED"})) == {"status": "COMPLETED"}
    failed = SimpleNamespace(isError=True, structuredContent={"status": "COMPLETED"})
    assert module.decode_mcp_result(failed)["status"] == "FAILED"
    invalid = SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text="not json")])
    assert module.decode_mcp_result(invalid) is None


def test_manifest_identity_changes_with_bytes():
    module = bench()
    identity = module.corpus_identity()
    assert len(identity["sha256"]) == 64
    assert identity["version"] == 1
    assert identity == module.corpus_identity()


def test_stdio_transport_uses_session_and_preserves_errors():
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    events = []

    @asynccontextmanager
    async def transport(parameters):
        events.append("spawn")
        yield ("reader", "writer")

    class Session:
        def __init__(self, read, write):
            assert (read, write) == ("reader", "writer")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("close")

        async def initialize(self):
            events.append("initialize")

        async def call_tool(self, name, arguments):
            events.append(name)
            return SimpleNamespace(isError=True)

    result = asyncio.run(bench().replay_transport(
        [{"tool": "modify_dimension", "arguments": {}}], object(),
        transport=transport, session_factory=Session))
    assert result["status"] == "failed"
    assert events == ["spawn", "initialize", "modify_dimension", "close"]


def test_transport_preflight_before_spawn():
    import asyncio
    import pytest
    from contextlib import asynccontextmanager
    events = []

    @asynccontextmanager
    async def transport(parameters):
        events.append("spawn")
        yield None, None

    with pytest.raises(ValueError):
        asyncio.run(bench().replay_transport(
            [{"tool": "close_document", "arguments": {"close_all": True}}], None,
            transport=transport, session_factory=None))
    assert events == []


def test_stdio_requires_explicit_gate():
    import asyncio
    import pytest
    with pytest.raises(ValueError, match="live"):
        asyncio.run(bench().replay_stdio([], live=False))


def test_unknown_inspection_cannot_unlock_dependent_step():
    import asyncio
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {"verification": {"status": "unknown"}}

    result = asyncio.run(bench().replay_steps(call, [
        {"tool": "verify_part_result", "arguments": {}},
        {"tool": "modify_dimension", "arguments": {}},
    ]))
    assert result["status"] == "unknown"
    assert calls == ["verify_part_result"]


def test_unknown_response_shape_cannot_unlock_dependent_step():
    import asyncio
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {}

    result = asyncio.run(bench().replay_steps(call, [
        {"tool": "modify_dimension", "arguments": {}},
        {"tool": "extrude", "arguments": {}},
    ]))
    assert result["status"] == "unknown"
    assert calls == ["modify_dimension"]


def test_corpus_expectations_are_machine_readable():
    for scenario in bench().load_corpus()["scenarios"]:
        assert isinstance(scenario["expected"], dict)
        assert scenario["expected"]["volume"]["unit"] == "mm3"
        assert scenario["expected"]["area"]["unit"] == "mm2"
        assert scenario["expected"]["rebuild_errors"]["value"] == 0


def test_corpus_identity_binds_expanded_criteria():
    import hashlib
    import json
    module = bench()
    canonical = json.dumps(module.load_corpus(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert module.corpus_identity()["expanded_sha256"] == hashlib.sha256(canonical).hexdigest()


def test_audit_missing_observations_is_unknown():
    result = bench().audit_scenario("plate-build", {})
    assert result["status"] == "unknown"
    assert result["scenario"] == "plate-build"
    assert result["corpus_identity"]["expanded_sha256"]


def test_audit_never_uses_api_verified_flag():
    result = bench().audit_scenario("boss-edit", {"verified": True})
    assert result["status"] == "unknown"


def test_matching_caller_values_are_not_independent_proof():
    module = bench()
    scenario = module.load_corpus()["scenarios"][0]
    observed = {key: {"value": criterion["value"], **({"unit": criterion["unit"]} if "unit" in criterion else {})}
                for key, criterion in scenario["expected"].items()}
    result = module.audit_scenario(scenario["id"], observed)
    assert all(check["status"] == "passed" for check in result["checks"])
    assert result["status"] == "unverified"


def test_document_ledger_rejects_preexisting_and_outside_root(tmp_path):
    import pytest
    ledger = bench().DocumentLedger(tmp_path / "owned", preexisting_titles={"UserPart"})
    with pytest.raises(ValueError):
        ledger.register("UserPart", tmp_path / "owned" / "user.sldprt")
    with pytest.raises(ValueError):
        ledger.register("TestPart", tmp_path / "outside.sldprt")
    assert ledger.cleanup_titles() == []


def test_document_ledger_unknown_outcome_blocks_cleanup(tmp_path):
    import pytest
    ledger = bench().DocumentLedger(tmp_path / "owned", preexisting_titles={"UserPart"})
    ledger.register("TestPart", tmp_path / "owned" / "test.sldprt")
    assert ledger.cleanup_titles() == ["TestPart"]
    ledger.mark_unknown()
    with pytest.raises(ValueError, match="unknown"):
        ledger.cleanup_titles()


def test_document_ledger_does_not_create_directories(tmp_path):
    root = tmp_path / "owned"
    bench().DocumentLedger(root, preexisting_titles=set())
    assert not root.exists()
