#!/usr/bin/env python3
"""MLR570 Phase 1 runner: one person's model through every harness, around the clock.

Usage:  caffeinate -i python3 run_grid.py config.json
Safe to stop and restart at any time: runs already in the results log are skipped.

What it does
- Reads the shared plan (plan.tsv: benchmark, task_id, run_index) and runs every
  plan row once per harness in config.json, `parallel` at a time.
- When a harness prints a usage-cap message, every worker pauses, waits
  `cap_wait_minutes`, then retries. A run cut off by a cap is NOT logged, so it reruns.
- Appends one JSON line per finished run to results/<person>.jsonl (the shared log schema).
"""
import json, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

CAP_PATTERN = re.compile(
    r"usage limit|rate.?limit|limit reached|quota|too many requests|\b429\b|try again (at|in)",
    re.IGNORECASE)

cfg = json.loads(Path(sys.argv[1]).read_text())
person, model = cfg["person"], cfg["model"]
log_path = Path("results") / f"{person}.jsonl"
log_path.parent.mkdir(exist_ok=True)
log_lock, resume_at = threading.Lock(), [0.0]
RUNNER_COMMIT = subprocess.run("git rev-parse --short HEAD", shell=True,
                               capture_output=True, text=True).stdout.strip() or None


def done_keys():
    if not log_path.exists():
        return set()
    rows = (json.loads(l) for l in log_path.read_text().splitlines() if l.strip())
    return {(r["benchmark"], r["task"], r["run"], r["harness"]) for r in rows}


def parse_result(outdir):
    """Read the harness's result file. Adjust the key names after the smoke test."""
    f = Path(outdir) / "result.json"
    if not f.exists():
        return {}
    r = json.loads(f.read_text())
    return {k: r.get(k) for k in
            ("passed", "input_tokens", "output_tokens", "cached_tokens", "tool_calls")}


def cost_usd(res):
    p = cfg["prices_per_million"][model]
    return round(sum((res.get(k) or 0) * p[k] / 1e6
                     for k in ("input_tokens", "output_tokens", "cached_tokens")), 4)


def run_one(bench, task, run, harness):
    outdir = Path("runs") / person / harness / bench / f"{task}__r{run}"
    cmd = cfg["harnesses"][harness].format(
        dataset=cfg["datasets"][bench], task=task, model=model, outdir=outdir)
    for attempt in range(cfg.get("max_cap_retries", 24) + 1):
        while time.time() < resume_at[0]:          # another worker hit the cap
            time.sleep(min(30, max(1, resume_at[0] - time.time())))
        outdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=cfg.get("task_timeout_minutes", 180) * 60)
        except subprocess.TimeoutExpired:       # logged as a failed run, not retried
            p = subprocess.CompletedProcess(cmd, None, "", "timeout")
        # Only the tail is checked, so a task whose own text mentions "quota" is not a cap.
        if CAP_PATTERN.search((p.stdout + p.stderr)[-2000:]) and attempt < cfg.get("max_cap_retries", 24):
            resume_at[0] = time.time() + cfg.get("cap_wait_minutes", 30) * 60
            print(f"[cap] {harness} {task}: waiting until "
                  f"{datetime.fromtimestamp(resume_at[0]):%H:%M}", flush=True)
            continue
        res = parse_result(outdir)
        row = {"ts": datetime.now(timezone.utc).isoformat(), "person": person,
               "benchmark": bench, "task": task, "run": run, "harness": harness,
               "model": model, "passed": res.get("passed"), "exit_code": p.returncode,
               "duration_s": round(time.time() - t0),
               "input_tokens": res.get("input_tokens"), "output_tokens": res.get("output_tokens"),
               "cached_tokens": res.get("cached_tokens"), "tool_calls": res.get("tool_calls"),
               "cost_usd": cost_usd(res), "runner_commit": RUNNER_COMMIT,
               "outdir": str(outdir)}
        with log_lock, log_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[done] {harness} {bench} {task} r{run} passed={row['passed']}", flush=True)
        return


plan = [l.split("\t") for l in Path(cfg["plan"]).read_text().splitlines()
        if l.strip() and not l.startswith("#")]
skip = done_keys()
todo = [(b, t, int(r), h) for b, t, r in plan for h in cfg["harnesses"]
        if (b, t, int(r), h) not in skip]
print(f"{len(todo)} runs to do, {len(skip)} already logged", flush=True)
with ThreadPoolExecutor(cfg.get("parallel", 2)) as pool:
    list(pool.map(lambda job: run_one(*job), todo))
print("all planned runs logged", flush=True)
