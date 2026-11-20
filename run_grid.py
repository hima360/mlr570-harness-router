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
import json, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Only real refusals. Claude Code logs "rate_limit_info": {"status": "allowed"} on every turn, and token counts can
# be 429, so a bare "rate limit" or "429" matched healthy runs and threw them away (2026-11-02).
CAP_PATTERN = re.compile(
    r"usage limit reached|hit your (usage )?limit|limit reached\W*\|?\s*\d|\"status\"\s*:\s*\"rejected\"|"
    r"quota exceeded|too many requests|try again (at|in) |"
    r"rate-limited every one of \d+ attempts|usage limit resets in|exceed your account's rate limit",  # Hermes 0.21.4 (2026-11-04: 4 runs logged as fails)
    re.IGNORECASE)

# Logins for the harnesses (for example CLAUDE_CODE_OAUTH_TOKEN) live in a local, git-ignored .env.
for line in (Path(".env").read_text().splitlines() if Path(".env").exists() else []):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

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


def trial_result(outdir):
    """Harbor writes <outdir>/<job>/<trial>/result.json; the trial's is the one with agent_result."""
    for f in sorted(Path(outdir).glob("*/*/result.json")):
        r = json.loads(f.read_text())
        if "agent_result" in r or "verifier_result" in r:
            return f.parent, r
    return None, {}


def parse_result(outdir):
    """Pass = Harbor's verifier reward 1. Harbor's n_input_tokens includes cached ones, so input_tokens here is the
    uncached part (each is priced at its own rate in cost_usd)."""
    trial, r = trial_result(outdir)
    if not r:
        return {}
    a = r.get("agent_result") or {}
    tool_calls = (a.get("metadata") or {}).get("tool_calls")
    if tool_calls is None:  # e.g. Codex: count tool calls in Harbor's trajectory.json
        try:
            steps = json.loads((trial / "agent" / "trajectory.json").read_text()).get("steps", [])
            tool_calls = sum(len(s.get("tool_calls") or []) for s in steps)
        except (OSError, ValueError, AttributeError):
            tool_calls = None
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    cached = a.get("n_cache_tokens") or 0
    return {"passed": None if reward is None else reward == 1,
            "input_tokens": max((a.get("n_input_tokens") or 0) - cached, 0), "cached_tokens": cached,
            "output_tokens": a.get("n_output_tokens"), "tool_calls": tool_calls}


def capped_inside(outdir):
    """The harness hit the usage cap inside the sandbox: its own logs (not Harbor's console) end with the message."""
    trial, _ = trial_result(outdir)
    if trial is None:
        return False
    for f in list((trial / "agent").glob("*.txt")) + list((trial / "agent").glob("*.log")):
        try:
            tail = f.read_text(errors="replace")[-2000:]
            m = CAP_PATTERN.search(tail)
            if m:
                return f"{f.name}: {tail[max(0, m.start() - 120):m.end() + 120]!r}"
        except OSError:
            pass
    return False


def cost_usd(res):
    p = cfg["prices_per_million"][model]
    return round(sum((res.get(k) or 0) * p[k] / 1e6
                     for k in ("input_tokens", "output_tokens", "cached_tokens")), 4)


def run_one(bench, task, run, harness):
    outdir = Path("runs") / person / harness / bench / f"{task}__r{run}"
    cmd = cfg["harnesses"][harness].format(  # Harbor names TB tasks terminal-bench/<task>, SWE ones bare
        dataset=cfg["datasets"][bench], task=cfg.get("task_prefix", {}).get(bench, "") + task, model=model,
        outdir=outdir.resolve())  # absolute: a relative -o broke Harbor's artifact copy on Windows
    for attempt in range(cfg.get("max_cap_retries", 24) + 1):
        while time.time() < resume_at[0]:          # another worker hit the cap
            time.sleep(min(30, max(1, resume_at[0] - time.time())))
        while Path("runs/PAUSE").exists():          # runs/quota_supervisor.sh: 5-hour window nearly used up
            time.sleep(30)
        outdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=cfg.get("task_timeout_minutes", 180) * 60)
        except subprocess.TimeoutExpired:       # logged as a failed run, not retried
            p = subprocess.CompletedProcess(cmd, None, "", "timeout")
        # Only the tail is checked, so a task whose own text mentions "quota" is not a cap.
        capped = CAP_PATTERN.search((p.stdout + p.stderr)[-2000:]) or capped_inside(outdir)
        if capped:
            m = capped if hasattr(capped, "group") else None
            text = (p.stdout + p.stderr)[-2000:] if m else ""
            hit = text[max(0, m.start() - 120):m.end() + 120] if m else capped
            print(f"[cap-evidence] {harness} {task}: {hit!r}", flush=True)
        if capped and attempt < cfg.get("max_cap_retries", 24):
            resume_at[0] = time.time() + cfg.get("cap_wait_minutes", 30) * 60
            print(f"[cap] {harness} {task}: waiting until "
                  f"{datetime.fromtimestamp(resume_at[0]):%H:%M}", flush=True)
            import shutil
            keep = Path("runs/capped") / f"{Path(outdir).name}-{datetime.now():%H%M%S}"  # evidence, not data
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(outdir), keep) if Path(outdir).exists() else None
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


EXCLUDED = {tuple(l.split("\t")[:2]) for l in Path("tasks/excluded.tsv").read_text().splitlines()
            if l.strip() and not l.startswith("#")} if Path("tasks/excluded.tsv").exists() else set()
plan = [l.split("\t") for l in Path(cfg["plan"]).read_text().splitlines()
        if l.strip() and not l.startswith("#")]
plan = [p for p in plan if (p[0], p[1]) not in EXCLUDED]  # tasks/excluded.tsv: hardware we do not have
skip = done_keys()
todo = [(b, t, int(r), h) for b, t, r in plan for h in cfg["harnesses"]
        if (b, t, int(r), h) not in skip]
print(f"{len(todo)} runs to do, {len(skip)} already logged", flush=True)
with ThreadPoolExecutor(cfg.get("parallel", 2)) as pool:
    list(pool.map(lambda job: run_one(*job), todo))
print("all planned runs logged", flush=True)
