"""Results of an evaluation (all jobs named <prefix>-<setup>-<task>[-r<n>] under jobs/), read once for every view:
the text summary (scripts/run_compare.py --summary), progress (scripts/progress.py), the HTML report
(scripts/report_html.py) and the TUI's Results screen. Pass = reward 1; cost = tokens x API list price."""
import collections
import glob
import json
import os
import time
from pathlib import Path

from .list_prices import trace_usd


def tests_fraction(trial: str) -> float | None:
    """Partial credit: the task's own partial score where it writes one, else the share of its tests that passed.

    Some tasks run one pytest test that always passes and score inside it against a pass mark (2026-11-01:
    freight-dispatch-shift writes points/total to trace_results.json, intrastat-meldung weighted checks to
    reward_details.json); counting their tests would read as 100% for a failed run."""
    v = trial + "/verifier/"
    rj = _load(v + "reward.json")
    if "partial_score" in rj:
        return rj["partial_score"]
    checks = _load(v + "reward_details.json").get("checks") or []
    if checks and all("weight" in c for c in checks):
        total = sum(c["weight"] for c in checks)
        return sum(c["weight"] for c in checks if c.get("passed")) / total if total else None
    tr = _load(v + "trace_results.json")
    if tr.get("total"):
        return tr.get("points", 0) / tr["total"]
    ctrf = _load(v + "ctrf.json")
    if ctrf:
        s = ctrf["results"]["summary"]
        return s["passed"] / s["tests"] if s.get("tests") else None
    return rj.get("reward")


def _load(path: str) -> dict:
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def split_name(name: str) -> tuple[str, str, str, int]:
    """'ho4-glmreview26-ctr-optimization-r2' -> ('ho4', 'glmreview26', 'ctr-optimization', 2)."""
    prefix, setup, task = name.split("-", 2)
    head, _, rep = task.rpartition("-r")
    return (prefix, setup, head, int(rep)) if head and rep.isdigit() else (prefix, setup, task, 1)


def collect(root: Path, prefix: str) -> list[dict]:
    """One row per job: status 'running' until Harbor writes result.json, then 'done'."""
    rows = []
    for d in sorted(glob.glob(f"{root}/jobs/{prefix}-*/")):  # pathlib would drop the trailing slash
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d.rstrip("/"))
        _, setup, task, rep = split_name(name)
        row = {"name": name, "task": task, "setup": setup, "repeat": rep, "status": "running"}
        trials = [t for t in glob.glob(d + "*/") if os.path.exists(t + "result.json")]
        if not trials:
            rows.append(row)
            continue
        t = trials[0]
        r = json.load(open(t + "result.json"))
        exc = (r.get("exception_info") or {}).get("exception_type")
        ev = [json.loads(l) for l in open(t + "agent/trace.jsonl")] if os.path.exists(t + "agent/trace.jsonl") else []
        calls = [e for e in ev if e["event"] == "llm_call"]
        attempts = [e for e in ev if e["event"] == "attempt"]
        row.update(
            status="done", exc=exc or "",
            reward=((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
            tests=tests_fraction(t), usd=trace_usd(ev),  # API list price of the same tokens (phase2.list_prices)
            credits=sum(e.get("factory_credits") or 0 for e in calls),
            tokens=sum((e.get("input_tokens") or 0) + (e.get("output_tokens") or 0) for e in calls),
            minutes=ev[-1]["t"] / 60 if ev else 0.0,
            models=",".join(sorted({e["model"].split("/")[-1] for e in ev if e["event"] in ("attempt", "helper_end")})),
            exit=attempts[-1].get("exit_status", "") if attempts else "",
            reviews=sum(e["event"] == "senior_review" for e in ev),
            finished=os.path.getmtime(t + "result.json"))
        rows.append(row)
    return rows


def by_setup(rows: list[dict]) -> dict[str, dict]:
    """Per setup, in first-seen order: runs, valid (no Harbor exception), passed, mean share of tests, totals."""
    agg = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["status"] != "done":
            continue
        a = agg[r["setup"]]
        a["runs"] += 1; a["valid"] += not r["exc"]; a["passed"] += r["reward"] == 1.0
        for k in ("usd", "credits", "minutes", "tokens"):
            a[k] += r[k]
        a["frac"] += r["tests"] or 0
        a["limit_hits"] += r["exit"] in ("TimeExceeded", "LimitsExceeded")  # v2.6 targets these: never reviewed
        a["reviewed"] += r["reviews"] > 0
    out = {}
    for s, a in agg.items():
        per = lambda k: a[k] / a["passed"] if a["passed"] else float("nan")
        out[s] = {**a, "mean_tests": a["frac"] / max(a["valid"], 1), "usd_per_solve": per("usd"),
                  "credits_per_solve": per("credits"), "minutes_per_run": a["minutes"] / max(a["runs"], 1)}
    return out


def plan(root: Path, prefix: str) -> dict:
    """What the supervisor was asked to run (jobs/<prefix>.plan.json), or {} for older evaluations."""
    try:
        return json.loads((root / "jobs" / f"{prefix}.plan.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def prefixes(root: Path) -> list[str]:
    """Evaluation prefixes under jobs/, newest first."""
    seen = {}
    for d in (root / "jobs").glob("*-*-*"):
        if d.is_dir() and not d.name.startswith("_"):
            p = d.name.split("-", 1)[0]
            seen[p] = max(seen.get(p, 0), d.stat().st_mtime)
    return sorted(seen, key=seen.get, reverse=True)


def progress(root: Path, prefix: str, now: float | None = None) -> dict:
    """Done / running / queued counts, solves so far per setup, and a rough finish time."""
    rows, p = collect(root, prefix), plan(root, prefix)
    done = [r for r in rows if r["status"] == "done"]
    total = len(p.get("tasks", [])) * len(p.get("setups", [])) * p.get("repeats", 1) if p else len(rows)
    running = len(rows) - len(done)
    left = max(total - len(done), 0)
    eta = None
    if done and left:
        mean_min = sum(r["minutes"] for r in done) / len(done)
        eta = (time.time() if now is None else now) + 60 * mean_min * left / max(p.get("parallel", running or 1), 1)
    return {"prefix": prefix, "total": total, "done": len(done), "running": running,
            "queued": max(total - len(rows), 0), "eta": eta, "setups": by_setup(rows), "rows": rows}
