import argparse
import hashlib
import importlib.util
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path


class DocumentLedger:
    def __init__(self, output_root, *, preexisting_titles):
        root = Path(output_root)
        if not root.is_absolute():
            raise ValueError("absolute output root required")
        self.root = root.resolve()
        self.preexisting = {title.casefold() for title in preexisting_titles}
        self.documents = {}
        self.unknown = False

    def register(self, title, path):
        if not isinstance(title, str) or not title.strip() or title.casefold() in self.preexisting:
            raise ValueError("document ownership not established")
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("absolute document path required")
        candidate = candidate.resolve()
        if self.root not in candidate.parents or candidate.suffix.lower() != ".sldprt":
            raise ValueError("document outside owned root or not a part")
        key = title.casefold()
        if key in self.documents or any(candidate == registered for _, registered in self.documents.values()):
            raise ValueError("duplicate document identity")
        self.documents[key] = (title, candidate)

    def mark_unknown(self):
        self.unknown = True

    def cleanup_titles(self):
        if self.unknown:
            raise ValueError("unknown outcome prohibits cleanup")
        return [title for title, path in self.documents.values()]


async def replay_stdio(steps, *, live=False, budget_seconds=600):
    if live is not True:
        raise ValueError("explicit live permission required")
    validate_replay(steps, budget_seconds)
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(command=sys.executable,
                                      args=[str(root / "adapters" / "claude" / "server.py")],
                                      cwd=str(root))
    return await replay_transport(steps, parameters, transport=stdio_client,
                                  session_factory=ClientSession, budget_seconds=budget_seconds)


async def replay_transport(steps, parameters, *, transport, session_factory, budget_seconds=600):
    validate_replay(steps, budget_seconds)
    async with transport(parameters) as (reader, writer):
        async with session_factory(reader, writer) as session:
            await session.initialize()

            async def call(name, arguments):
                return decode_mcp_result(await session.call_tool(name, arguments))

            return await replay_steps(call, steps, budget_seconds=budget_seconds)


def decode_mcp_result(result):
    if getattr(result, "isError", False):
        return {"status": "FAILED"}
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    texts = [item.text for item in getattr(result, "content", []) if isinstance(getattr(item, "text", None), str)]
    if len(texts) != 1:
        return None
    try:
        value = json.loads(texts[0])
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def validate_replay(steps, budget_seconds):
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("positive finite budget required")
    if not isinstance(steps, list):
        raise ValueError("step list required")
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str) or not isinstance(step.get("arguments"), dict):
            raise ValueError("invalid replay step")
        if step["tool"] == "close_document":
            raise ValueError("replay cleanup requires separate ownership-checked protocol")
        if step["tool"] in {"execute_python", "execute_solidworks_script", "execute_batch"}:
            raise ValueError("unrestricted or nested execution is not permitted in replay")


async def replay_steps(call, steps, *, budget_seconds=600, clock=time.perf_counter):
    validate_replay(steps, budget_seconds)
    records = []
    outcome = "unverified"
    task_started = clock()
    for index, step in enumerate(steps):
        started = clock()
        if started - task_started >= budget_seconds:
            outcome = "timeout"
            break
        try:
            response = await call(step["tool"], step["arguments"])
        except Exception:
            response = None
        completed = clock()
        elapsed = completed - started
        status = "unverified"
        if not isinstance(response, dict) or response.get("running") is True:
            status = "unknown"
        elif response.get("status") in ("FAILED", "DUPLICATE"):
            status = "failed" if response["status"] == "FAILED" else "unknown"
        elif "verification" in response:
            verification = response["verification"]
            status = verification.get("status", "unknown") if isinstance(verification, dict) else "unknown"
            if status not in ("passed", "failed", "unknown"):
                status = "unknown"
        elif response.get("status") != "COMPLETED":
            status = "unknown"
        records.append({"step": index, "elapsed": elapsed, "status": status})
        if status in ("failed", "unknown"):
            outcome = status
            break
        if completed - task_started >= budget_seconds:
            outcome = "timeout"
            break
    return {"status": outcome, "steps": records, "unexecuted": list(range(len(records), len(steps))),
            "limitations": ["No independent audit; API completion alone is not task success."]}


def load_corpus():
    corpus = json.loads((Path(__file__).parent / "bench" / "scenarios.json").read_text(encoding="utf-8"))
    for scenario in corpus["scenarios"]:
        edited = scenario["task"] == "edit"
        if scenario["part"] == "plate":
            height = 11 if edited else 10
            volume = 20 * 30 * height
            area = 2 * (20 * 30 + 20 * height + 30 * height)
        else:
            height = 16.5 if edited else 15
            volume = 60000 + (100 * height - 360) * math.pi
            area = 15200 + (168 + 20 * height) * math.pi
        scenario["description"] = scenario["expected"]
        scenario["expected"] = {
            "volume": {"value": volume, "unit": "mm3", "relative_tolerance": 0.0001},
            "area": {"value": area, "unit": "mm2", "relative_tolerance": 0.0001},
            "driving_height": {"value": height, "unit": "mm", "absolute_tolerance": 0.01},
            "rebuild_errors": {"value": 0, "unit": "count"},
            "required_sketches_defined": {"value": True},
            "driving_relations_preserved": {"value": True},
            "essential_surfaces_match": {"value": True},
        }
        if scenario["part"] == "boss_plate":
            scenario["expected"]["through_holes"] = {"value": 4, "unit": "count"}
    return corpus


def corpus_identity():
    data = (Path(__file__).parent / "bench" / "scenarios.json").read_bytes()
    expanded = json.dumps(load_corpus(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"version": json.loads(data)["version"], "sha256": hashlib.sha256(data).hexdigest(),
            "expanded_sha256": hashlib.sha256(expanded).hexdigest()}


def audit_scenario(scenario_id, observed):
    matches = [scenario for scenario in load_corpus()["scenarios"] if scenario["id"] == scenario_id]
    if len(matches) != 1:
        raise ValueError("unknown scenario")
    spec = importlib.util.spec_from_file_location("cad_bench_oracle", Path(__file__).with_name("cad_oracle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.evaluate(matches[0]["expected"], observed)
    result.update(scenario=scenario_id, corpus_identity=corpus_identity(),
                  limitations=["Caller-supplied observations; independent probe provenance and parametric audit are not established."])
    if result["status"] == "passed":
        result["status"] = "unverified"
    return result


def paired_schedule(pairs, *, seed):
    if type(pairs) is not int or pairs <= 0 or pairs % 2:
        raise ValueError("positive even pair count required")
    schedule = ["AB", "BA"] * (pairs // 2)
    random.Random(seed).shuffle(schedule)
    return schedule


def summarize_runs(runs):
    if not runs:
        raise ValueError("nonempty runs required")
    statuses = {"passed", "failed", "unknown", "timeout", "unverified", "skip"}
    for run in runs:
        value = run.get("elapsed")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or run.get("status") not in statuses:
            raise ValueError("invalid run")
    times = sorted(run["elapsed"] for run in runs)
    successes = sum(run["status"] == "passed" for run in runs)
    return {"runs": len(runs), "successes": successes,
            "outcomes": {status: sum(run["status"] == status for run in runs) for status in sorted(statuses)},
            "p50": statistics.median(times), "p95": times[math.ceil(0.95 * len(times)) - 1],
            "p95_descriptive_only": len(times) < 100,
            "cost_per_correct": sum(times) / successes if successes else None}


def holm(p_values):
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in p_values):
        raise ValueError("invalid p-values")
    adjusted = [0.0] * len(p_values)
    previous = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        previous = max(previous, min(1, p_values[index] * (len(p_values) - rank)))
        adjusted[index] = previous
    return adjusted


def analyze_pairs(a, b, *, seed, quality_ok=None):
    if not a or len(a) != len(b):
        raise ValueError("paired nonempty samples required")
    if any(type(t) not in (int, float) or not math.isfinite(t) or t <= 0 for t in [*a, *b]):
        raise ValueError("finite positive timings required")
    logs = [math.log(y / x) for x, y in zip(a, b)]
    rng = random.Random(seed)
    samples = sorted(statistics.median(rng.choices(logs, k=len(logs))) for _ in range(10000))
    return {
        "pairs": len(logs), "seed": seed, "bootstrap_samples": 10000,
        "ratio": math.exp(statistics.median(logs)),
        "ci95": [math.exp(samples[249]), math.exp(samples[9749])],
        "median_delta": statistics.median(y - x for x, y in zip(a, b)),
        "verdict": "quality regression" if quality_ok is False else "no evidence",
        "limitations": ["Timing-only analysis; acceptance requires preregistration, independent quality, balanced AB/BA, Holm and p95 review."],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        parser.error("Live execution unavailable until ownership and independent CAD probes are implemented")
    if args.results:
        data = json.loads(args.results.read_text(encoding="utf-8"))
        result = analyze_pairs(data["a"], data["b"], seed=args.seed)
    else:
        result = {"mode": "dry-run", "corpus": load_corpus(), "corpus_identity": corpus_identity(), "live_available": False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
