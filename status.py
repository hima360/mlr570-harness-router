#!/usr/bin/env python3
"""How far your Phase 1 column is:  python3 status.py configs/<you>.json  (add -w to refresh every minute)."""
import json, subprocess, sys, time
from pathlib import Path


def render(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    plan = [l.split("\t") for l in Path(cfg["plan"]).read_text().splitlines() if l.strip() and not l.startswith("#")]
    total = len(plan) * len(cfg["harnesses"])
    log = Path("results") / f"{cfg['person']}.jsonl"
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    out = [f"{cfg['person']} / {cfg['model']}: {len(rows)} of {total} runs logged"]
    for h in cfg["harnesses"]:
        hr = [r for r in rows if r["harness"] == h]
        passed = sum(r["passed"] is True for r in hr)
        cost = sum(r.get("cost_usd") or 0 for r in hr)
        out.append(f"  {h:12s} {len(hr):3d}/{len(plan)} done  {passed} passed  ${cost:.2f} at list price")
    if rows:
        last = rows[-1]
        out.append(f"  last finished: {last['harness']} {last['task'][:40]} at {last['ts'][11:16]} UTC")
    running = subprocess.run(["pgrep", "-f", "run_grid.py"], capture_output=True, text=True).stdout.split()
    out.append("  runner: " + ("running" if running else "NOT running (start: caffeinate -i python3 run_grid.py "
                                                         f"{cfg_path} >> runner.log 2>&1 &)"))
    caps = [l for l in Path("runner.log").read_text().splitlines() if l.startswith("[cap]")] if Path("runner.log").exists() else []
    if caps:
        out.append(f"  usage cap hit {len(caps)} time(s); last: {caps[-1]}")
    return "\n".join(out)


if __name__ == "__main__":
    while True:
        print(("\033[2J\033[H" if "-w" in sys.argv else "") + render(next(a for a in sys.argv[1:] if a != "-w")), flush=True)
        if "-w" not in sys.argv:
            break
        time.sleep(60)
