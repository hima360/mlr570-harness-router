"""Recorded evaluations (jobs named <prefix>-<setup>-<task>[-r<n>]) via phase2.results, cached by folder mtime."""
from __future__ import annotations

import math
import threading
from pathlib import Path

from phase2 import results as R

from .models import setup_label

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
HIDDEN = {"chat"}  # app chat runs have no grader; they are not an evaluation


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def evaluations(root: Path) -> list[dict]:
    """Every evaluation prefix, newest first, with how many runs it holds (cheap: no trace reads)."""
    jobs = root / "jobs"
    out = []
    for p in R.prefixes(root):
        if p in HIDDEN:
            continue
        dirs = [d for d in jobs.glob(f"{p}-*") if d.is_dir()]
        setups = sorted({R.split_name(d.name)[1] for d in dirs if d.name.count("-") >= 2})
        out.append({"prefix": p, "runs": len(dirs), "setups": setups,
                    "setup_labels": [setup_label(s) for s in setups],
                    "updated": max((d.stat().st_mtime for d in dirs), default=0)})
    return out


def evaluation(root: Path, prefix: str) -> dict:
    jobs = root / "jobs"
    stamp = max((d.stat().st_mtime for d in jobs.glob(f"{prefix}-*")), default=0)
    with _lock:
        hit = _cache.get(prefix)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        rows = [r for r in R.collect(root, prefix) if r["name"].count("-") >= 2]
    except (ValueError, KeyError, OSError) as e:  # a job name or trace phase2.results cannot read
        return {"prefix": prefix, "setups": [], "tasks": [], "plan": {}, "error": f"{type(e).__name__}: {e}"[:300]}
    agg = R.by_setup(rows)
    setups = []
    for s, a in agg.items():
        setups.append({"setup": s, "label": setup_label(s), "runs": a["runs"], "valid": a["valid"],
                       "passed": a["passed"], "mean_tests": _clean(a["mean_tests"]), "usd": round(a["usd"], 4),
                       "usd_per_solve": _clean(a["usd_per_solve"]), "tokens": a["tokens"],
                       "minutes_per_run": _clean(a["minutes_per_run"])})
    tasks: dict[str, dict] = {}
    for r in rows:
        t = tasks.setdefault(r["task"], {"task": r["task"], "runs": []})
        t["runs"].append({"job": r["name"], "setup": r["setup"], "setup_label": setup_label(r["setup"]),
                          "repeat": r["repeat"], "status": r["status"], "reward": _clean(r.get("reward")),
                          "tests": _clean(r.get("tests")), "usd": round(r.get("usd") or 0, 4),
                          "tokens": r.get("tokens", 0), "minutes": round(r.get("minutes") or 0, 2),
                          "exception": r.get("exc", ""), "reviews": r.get("reviews", 0),
                          "models": r.get("models", "")})
    out = {"prefix": prefix, "setups": setups, "tasks": sorted(tasks.values(), key=lambda t: t["task"]),
           "plan": R.plan(root, prefix)}
    with _lock:
        _cache[prefix] = (stamp, out)
    return out
