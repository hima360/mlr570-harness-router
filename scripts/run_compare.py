"""Run the Phase 2 setup comparison: every task under every setup, N Harbor runs at a time.

  python scripts/run_compare.py --config configs/phase2-droid-claude-ladder.json --prefix cmpd \
      --parallel 3 payments-pipeline-fix kv-live-surgery ...
  python scripts/run_compare.py --summary --prefix cmpd

Setups: haiku / sonnet = one step on a fixed tier (0 / 1), no planner, no escalation, no handler;
routed = Laya routing, up to 5 steps, chains merged, escalation and handler on; split = routed without
merging chains. Jobs are named <prefix>-<setup>-<task> under jobs/; an existing job is skipped.
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2.setups import BASE, LONG, ROUTED, SETUPS  # noqa: E402  (re-exported for supervise.py)


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


def summary(prefix: str) -> None:
    from phase2.results import by_setup, collect

    rows = collect(ROOT, prefix)
    for r in rows:
        if r["status"] != "done":
            print(f"{r['task']:28s} {r['setup']:7s} running")
            continue
        tests = "-" if r["tests"] is None else f"{r['tests']:.2f}"
        print(f"{r['task']:28s} {r['setup']:7s} reward={r['reward']} tests={tests} ${r['usd']:.2f} "
              f"{r['tokens'] / 1e6:.2f}Mtok {r['credits']:,.0f}cr {r['minutes']:.0f}min {r['models']} {r['exc']}")
    print()
    for s, a in by_setup(rows).items():
        print(f"{s:7s} runs={a['runs']} valid={a['valid']} passed={a['passed']} mean_tests={a['mean_tests']:.2f} "
              f"usd={a['usd']:.2f} tokens={a['tokens'] / 1e6:.2f}M credits={a['credits']:,.0f} minutes={a['minutes']:.0f} "
              f"credits/solve={a['credits_per_solve']:,.0f} usd/solve={a['usd_per_solve']:.2f}")

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
