"""DAG scheduler: a step starts once its dependencies finish; ready steps run together only when
the files they write cannot overlap, so parallel workers never edit the same file."""
import asyncio
from fnmatch import fnmatch


def _prefix(pattern: str) -> str:
    """Literal directory part before the first wildcard, e.g. 'src/app/*.py' -> 'src/app/'."""
    cut = min([i for i, c in enumerate(pattern) if c in "*?["] or [len(pattern)])
    return pattern[:cut]


def writes_overlap(a: list[str], b: list[str]) -> bool:
    """Conservative: True unless the two write sets are clearly disjoint. Empty = read-only."""
    for pa in a:
        for pb in b:
            pa_, pb_ = pa.strip("./") or "**", pb.strip("./") or "**"
            if pa_ in ("*", "**") or pb_ in ("*", "**") or pa_ == pb_:
                return True
            if fnmatch(pa_, pb_) or fnmatch(pb_, pa_):
                return True
            xa, xb = _prefix(pa_), _prefix(pb_)
            if (xa != pa_ or xb != pb_) and (xa.startswith(xb) or xb.startswith(xa)):
                return True
    return False


async def run_dag(steps, run_step, max_parallel: int = 3) -> dict:
    """steps: objects with .id, .depends_on, .writes. run_step: async (step) -> result.
    Dependents of a failed step still run (best effort); results map step id -> result."""
    pending = list(steps)
    running: dict[asyncio.Task, object] = {}
    results: dict = {}
    while pending or running:
        for step in list(pending):
            if len(running) >= max_parallel:
                break
            if not all(d in results for d in step.depends_on):
                continue
            if any(writes_overlap(step.writes, other.writes) for other in running.values()):
                continue
            pending.remove(step)
            running[asyncio.create_task(run_step(step))] = step
        if not running:
            raise RuntimeError(f"scheduler stuck with pending steps {[s.id for s in pending]}")
        done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            step = running.pop(task)
            results[step.id] = task.result()
    return results
