"""Restate Droid-era costs as if the run had gone through opencodex (Droid ended 2026-11-03; v4 runs on opencodex).

Droid adds its own system prompt to every call, so Droid-era input tokens are inflated. We measure that overhead
from the first call of each role (system prompt + task only, no history) on the same task through both backends,
then take it off every Droid-era call: priced at the cache-read rate when the call read at least that many tokens
from cache, else at the uncached input rate. Output tokens are not changed. Agreed with Ibrahim 2026-11-18 instead
of rerunning v2.8 on the new backend.

  scripts/backend_adjust.py --droid dv8-takeover28 --ocx v4d1-ocx28 [--apply ho2-takeover28 ho4-takeover28 ...]
"""
import argparse
import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRICES = {"opus": (4.0, 0.20, 20.0), "glm": (0.15, 0.03, 0.50)}  # per M: uncached in, cache read, out


def fam(model: str) -> str:
    return "opus" if "opus" in model else "glm" if "glm" in model else ""


def calls(job_glob: str):
    """Yield (task, role, family, first_call_of_role, call event) for every llm_call in matching jobs."""
    for t in sorted(glob.glob(str(ROOT / "jobs" / job_glob / "*" / "agent" / "trace.jsonl"))):
        task = Path(t).parents[2].name.split("-", 2)[2].rsplit("-r", 1)[0]
        seen = set()
        for line in open(t):
            e = json.loads(line)
            if e.get("event") != "llm_call" or not fam(e.get("model", "")):
                continue
            key = (e.get("role"), fam(e["model"]))
            yield task, key[0], key[1], key not in seen, e
            seen.add(key)


def overhead(droid: str, ocx: str) -> dict:
    """Median first-call input tokens per (role, family): Droid minus opencodex, on tasks both have."""
    def firsts(g):
        out = {}
        for task, role, f, first, e in calls(g):
            if first:
                out.setdefault((role, f), {}).setdefault(task, []).append(e.get("input_tokens", 0))
        return out
    d, o = firsts(droid + "-*"), firsts(ocx + "-*")
    res = {}
    for key in d:
        common = sorted(set(d[key]) & set(o.get(key, {})))
        diffs = [statistics.median(d[key][t]) - statistics.median(o[key][t]) for t in common]
        if diffs:
            res[key] = {"tokens": statistics.median(diffs), "tasks": common}
    return res


def adjust(job_prefix: str, oh: dict) -> dict:
    """Recorded vs adjusted cost for every job matching the prefix."""
    flat = {f: statistics.median([v["tokens"] for (r, ff), v in oh.items() if ff == f] or [0]) for f in PRICES}
    rec = adj = 0.0
    n = 0
    for task, role, f, first, e in calls(job_prefix + "-*"):
        n += 1
        unc, cr, out = PRICES[f]
        inp, cached, o_t = e.get("input_tokens") or 0, e.get("cached_tokens") or 0, e.get("output_tokens") or 0
        cost = ((inp - cached) * unc + cached * cr + o_t * out) / 1e6  # list price (Droid traces logged 0 USD)
        rec += cost
        o = max(0.0, oh.get((role, f), {}).get("tokens", flat[f]))
        rate = cr if (e.get("cached_tokens") or 0) >= o else unc
        adj += max(0.0, cost - o * rate / 1e6)
    return {"calls": n, "recorded_usd": round(rec, 2), "adjusted_usd": round(adj, 2),
            "saving_pct": round(100 * (rec - adj) / rec, 1) if rec else 0.0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--droid", required=True)
    ap.add_argument("--ocx", required=True)
    ap.add_argument("--apply", nargs="*", default=[])
    a = ap.parse_args(argv)
    oh = overhead(a.droid, a.ocx)
    print("first-call input overhead, Droid minus opencodex (median over shared tasks):")
    for (role, f), v in sorted(oh.items()):
        print(f"  {role:10s} {f:5s} {v['tokens']:>8.0f} tokens  tasks={v['tasks']}")
    for p in [a.droid] + a.apply:
        print(p, adjust(p, oh))
    return 0


if __name__ == "__main__":
    sys.exit(main())
