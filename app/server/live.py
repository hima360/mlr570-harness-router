"""What is running right now: every job whose process is alive, and progress per active evaluation prefix.

Cheap enough to poll every 10-15 s: only jobs with a live pid in jobs/<job>.run.json are opened, traces are
re-normalized only when their size changes, and evaluation progress is cached by the prefix's newest mtime.
No model calls.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

from phase2 import jobs as J
from phase2 import results as R

from .connections import PENDING, cached_bg
from phase2 import quota as Q
from .models import setup_label
from .normalize import Normalizer
from .runs import job_summary, read_rows, trial_dir

_lock = threading.Lock()
_trace_cache: dict[str, tuple[int, dict]] = {}
_progress_cache: dict[str, tuple[float, dict]] = {}


def running_names(root: Path, alive=J.pid_alive) -> list[str]:
    """Jobs whose launcher process is alive (start_run writes jobs/<job>.run.json with its pid)."""
    out = []
    for p in (root / "jobs").glob("*.run.json"):
        if p.name.startswith(("_", ".")):
            continue
        name = p.name.removesuffix(".run.json")
        job = J.load_job(root / "jobs" / name)
        if job.status == "running" and alive(job.pid):
            out.append(name)
    return out


def trace_state(trace: Path) -> dict:
    """Who's working, the current phase, tokens and cost so far (cached by trace size)."""
    try:
        size = trace.stat().st_size
    except OSError:
        return {"working": [], "phase": "", "tokens": 0, "cost_usd": 0.0, "calls": 0, "t": 0.0}
    key = str(trace)
    with _lock:
        hit = _trace_cache.get(key)
    if hit and hit[0] == size:
        return hit[1]
    norm = Normalizer()
    working, usage, phase = [], {}, ""
    for ev in norm.feed_all(read_rows(trace)):
        if ev["type"] == "working":
            working = ev["now"]
        elif ev["type"] == "usage":
            usage = ev["total"]
        elif ev["type"] == "phase_start":
            phase = ev["phase"]["title"]
    out = {"working": [{"label": w["label"], "model": w["model"], "verb": w["verb"], "kind": w["kind"],
                        "since": w["since"]} for w in working],
           "phase": working[0]["kind"] if working else phase.lower(),
           "tokens": (usage.get("input", 0) + usage.get("output", 0)) if usage else 0,
           "cost_usd": round(usage.get("cost_usd", 0.0), 6) if usage else 0.0,
           "calls": usage.get("calls", 0) if usage else 0, "t": norm.last_t}
    with _lock:
        _trace_cache[key] = (size, out)
    return out


def running_jobs(root: Path, alive=J.pid_alive, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    rows = []
    for name in running_names(root, alive):
        s = job_summary(root, name)
        td = trial_dir(root / "jobs" / name)
        st = trace_state(td / "agent" / "trace.jsonl") if td else None
        rows.append({"job": name, "kind": s["kind"], "prefix": s["prefix"], "setup": s["setup"],
                     "setup_label": s["setup_label"], "task": s["task"], "title": s["title"],
                     "started": s["started"], "elapsed": max(now - (s["started"] or now), 0),
                     "stage": "running" if st is not None else "starting",
                     "working": (st or {}).get("working", []), "phase": (st or {}).get("phase", ""),
                     "tokens": (st or {}).get("tokens", 0), "cost_usd": (st or {}).get("cost_usd", 0.0)})
    rows.sort(key=lambda r: r["started"] or 0, reverse=True)
    return rows


def _clean(v):
    return None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v


def batch_progress(root: Path, prefix: str) -> dict:
    stamp = max((p.stat().st_mtime for p in (root / "jobs").glob(f"{prefix}-*")), default=0)
    with _lock:
        hit = _progress_cache.get(prefix)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        p = R.progress(root, prefix)
    except (ValueError, KeyError, OSError) as e:
        return {"prefix": prefix, "error": f"{type(e).__name__}: {e}"[:200]}
    setups = [{"setup": s, "label": setup_label(s), "runs": a["runs"], "passed": a["passed"],
               "usd": round(a["usd"], 4)} for s, a in p["setups"].items()]
    out = {"prefix": prefix, "total": p["total"], "done": p["done"], "running": p["running"], "queued": p["queued"],
           "eta": _clean(p["eta"]), "setups": setups, "usd": round(sum(s["usd"] for s in setups), 4)}
    with _lock:
        _progress_cache[prefix] = (stamp, out)
    return out


def live(root: Path, alive=J.pid_alive, quota=None) -> dict:
    running = running_jobs(root, alive)
    prefixes = []
    for r in running:
        if r["kind"] != "chat" and r["prefix"] not in prefixes:
            prefixes.append(r["prefix"])
    batches = [batch_progress(root, p) for p in prefixes]
    q = quota if quota is not None else cached_bg("quota", 300, Q.read_zai_quota)
    zai = None if q is PENDING or q is None else {"five_hour": q[0], "weekly": q[1]}
    return {"running": running, "batches": batches, "quota": zai,
            "quota_state": "pending" if q is PENDING else "ok" if zai else "unreadable", "checked": time.time()}
