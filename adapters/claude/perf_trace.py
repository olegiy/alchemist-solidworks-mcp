import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_lock = threading.Lock()
_spans = frozenset({"session", "transport", "queue", "attach", "before_snapshot", "body", "rebuild", "actual_readback", "after_tree", "sketch_status", "serialization", "logging"})


@contextmanager
def span(name, operation_id, *, sink=None, clock=time.perf_counter_ns):
    if os.environ.get("SWMCP_PERF") != "1" or name not in _spans or not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id):
        yield
        return
    started = clock()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        row = {"operation_id": operation_id, "span": name, "duration_ns": max(0, clock() - started), "outcome": outcome}
        try:
            target = Path(sink) if sink is not None else Path(__file__).resolve().parents[2] / "tools" / "out" / "bench" / "python-perf.jsonl"
            with _lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError:
            pass
