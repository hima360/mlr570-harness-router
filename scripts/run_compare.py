"""Run the Phase 2 setup comparison: every task under every setup, N Harbor runs at a time.

  python scripts/run_compare.py --config configs/phase2-droid-claude-ladder.json --prefix cmpd \
      --parallel 3 payments-pipeline-fix kv-live-surgery ...
  python scripts/run_compare.py --summary --prefix cmpd

Setups: haiku / sonnet = one step on a fixed tier (0 / 1), no planner, no escalation, no handler;
routed = Laya routing, up to 5 steps, chains merged, escalation and handler on; split = routed without
merging chains. Jobs are named <prefix>-<setup>-<task> under jobs/; an existing job is skipped.
"""
import argparse
import collections
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = {"max_plan_steps": "1", "worker_step_limit": "80", "step_wall_time_sec": "2700",
        "max_interventions": "0", "max_escalations": "0"}
ROUTED = {"max_plan_steps": "5", "worker_step_limit": "40", "step_wall_time_sec": "1800"}
# --long: Terminal-Bench allows hours per task; give the agents room to verify (2026-10-27: agents submitted
# early, sure they were done)
LONG = {"worker_step_limit": "80", "step_wall_time_sec": "3600", "submit_checks": "3"}
SETUPS = {  # name: (policy, caps[, config overriding --config])
    "haiku": ("fixed:0", BASE),
    "sonnet": ("fixed:1", BASE),
    "opus": ("fixed:2", BASE),
    "routed": ("laya", ROUTED),
    "split": ("laya", {**ROUTED, "merge_chains": "false"}),
    "planner": ("planner", ROUTED),
    "levers": ("planner", {**ROUTED, **{"worker_step_limit": "80", "step_wall_time_sec": "3600", "submit_checks": "3"}},
               "configs/phase2-droid-levers.json"),
}


def run(config: str, prefix: str, parallel: int, tasks: list[str], setups=None, long=False, repeats: int = 1) -> None:
    from phase2 import jobs as J

    chosen = setups or list(SETUPS)
    queue = [(f"{prefix}-{s}-{t}" + (f"-r{r}" if repeats > 1 else ""), t, SETUPS[s][0],
              {**SETUPS[s][1], **(LONG if long and SETUPS[s][1] is not BASE else {})},
              SETUPS[s][2] if len(SETUPS[s]) > 2 else config)
             for r in range(1, repeats + 1) for t in tasks for s in chosen]
    log = open(ROOT / "jobs" / f"{prefix}.queue.log", "a", buffering=1)
    running = {}
    while queue or running:
        for name, pid in list(running.items()):
            try:  # our own children: reap them, or they stay as zombies and look alive
                done = os.waitpid(pid, os.WNOHANG)[0] == pid
            except ChildProcessError:
                done = True
            if done:
                log.write(f"{time.strftime('%H:%M:%S')} done {name}\n")
                del running[name]
        while queue and len(running) < parallel:
            name, task, policy, caps, cfg_path = queue.pop(0)
            if (ROOT / "jobs" / name).exists():
                log.write(f"skip {name} (exists)\n")
                continue
            running[name] = J.start_run(ROOT, f"terminal-bench/{task}", cfg_path, policy, caps, name)
            log.write(f"{time.strftime('%H:%M:%S')} start {name} pid={running[name]}\n")
        time.sleep(20)
    log.write("ALL DONE\n")


def tests_fraction(trial: str) -> float | None:
    """Share of the task's own tests that passed (ctrf.json), or its partial score (reward.json)."""
    for f in glob.glob(trial + "/verifier/ctrf.json"):
        s = json.load(open(f))["results"]["summary"]
        return s["passed"] / s["tests"] if s.get("tests") else None
    for f in glob.glob(trial + "/verifier/reward.json"):
        d = json.load(open(f))
        return d.get("partial_score", d.get("reward"))
    return None


def summary(prefix: str) -> None:
    rows, agg = [], collections.defaultdict(lambda: collections.Counter())
    for d in sorted(glob.glob(f"{ROOT}/jobs/{prefix}-*/")):  # pathlib would drop the trailing slash
        if not os.path.isdir(d):
            continue
        _, setup, task = os.path.basename(d.rstrip("/")).split("-", 2)
        task = task.rsplit("-r", 1)[0] if task.rsplit("-r", 1)[-1].isdigit() else task
        trials = [t for t in glob.glob(d + "*/") if os.path.exists(t + "result.json")]
        if not trials:
            rows.append((task, setup, "running")); continue
        t = trials[0]
        r = json.load(open(t + "result.json"))
        ar, exc = r.get("agent_result") or {}, (r.get("exception_info") or {}).get("exception_type")
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        ev = [json.loads(l) for l in open(t + "agent/trace.jsonl")] if os.path.exists(t + "agent/trace.jsonl") else []
        credits = sum(e.get("factory_credits") or 0 for e in ev if e["event"] == "llm_call")
        frac, mins = tests_fraction(t), (ev[-1]["t"] / 60 if ev else 0)
        models = ",".join(sorted({e["model"].split("/")[-1] for e in ev if e["event"] == "attempt"}))
        rows.append((task, setup, f"reward={reward} tests={'-' if frac is None else f'{frac:.2f}'} "
                                  f"${ar.get('cost_usd') or 0:.2f} {credits:,.0f}cr {mins:.0f}min {models} {exc or ''}"))
        a = agg[setup]
        a["runs"] += 1; a["valid"] += exc is None; a["passed"] += reward == 1.0
        a["usd"] += ar.get("cost_usd") or 0; a["credits"] += credits; a["minutes"] += mins
        a["frac"] += frac or 0
    for row in rows:
        print(f"{row[0]:28s} {row[1]:7s} {row[2]}")
    print()
    for s, a in agg.items():
        per = lambda k: a[k] / a["passed"] if a["passed"] else float("nan")
        print(f"{s:7s} runs={a['runs']} valid={a['valid']} passed={a['passed']} mean_tests={a["frac"] / max(a["valid"], 1):.2f} "
              f"usd={a['usd']:.2f} credits={a['credits']:,.0f} minutes={a['minutes']:.0f} "
              f"credits/solve={per('credits'):,.0f} usd/solve={per('usd'):.2f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("tasks", nargs="*")
    p.add_argument("--config", default="configs/phase2-droid-claude-ladder.json")
    p.add_argument("--prefix", default="cmpd")
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--summary", action="store_true")
    p.add_argument("--setups", help="comma list of " + ",".join(SETUPS))
    p.add_argument("--long", action="store_true", help="longer worker budgets for routed / split")
    p.add_argument("--repeats", type=int, default=1, help="runs per task and setup (job names end in -r<n>)")
    a = p.parse_args()
    if a.summary:
        summary(a.prefix)
    else:
        run(a.config, a.prefix, a.parallel, a.tasks, a.setups.split(",") if a.setups else None, a.long, a.repeats)
