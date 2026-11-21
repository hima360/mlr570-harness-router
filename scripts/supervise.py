"""Run an evaluation unattended: health checks, the queue, automatic reruns of runs killed by infrastructure, a report.

  scripts/supervise.sh --config configs/phase2-droid-glm.json --prefix ho2 --parallel 5 \
      --setups glm,glmreview,glmreview25 --repeats 2 atrx-vep-crispr biped-contact-dynamics ...

Each round: check Docker (restart Colima if it is down), the network and every model in the configs (a model Droid
rejects is waited for, up to --wait-model-min); run the queue (scripts/run_compare.py, finished jobs are skipped);
then set aside every run that infrastructure ended (killed at start, lost connection, model timeout) in
jobs/_excluded_<prefix>/ so the next round reruns it. Stops when a round leaves none, or after --max-rounds.
Writes jobs/<prefix>.REPORT.txt (the summary, plus what was set aside and why) and posts a macOS notification.
Log: jobs/<prefix>.supervisor.log. Runs that ended on the model's own merits (a submit, a turn or time limit) are
never rerun: only runs the stated rule calls infrastructure (2026-10-29: a Wi-Fi switch and a stopped Mac).
"""
import argparse
import contextlib
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

INFRA_EXITS = ("ProviderUnavailable", "ModelTimedOut")


def log(prefix: str, msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(ROOT / "jobs" / f"{prefix}.supervisor.log", "a") as f:
        f.write(line + "\n")


def infra_reason(job_dir: Path) -> str:
    """Why infrastructure ended this finished job ("" = it ended on the model's own merits, or is unfinished)."""
    res = glob.glob(str(job_dir / "*" / "result.json"))
    if not res:
        return ""
    r = json.loads(Path(res[0]).read_text())
    exc = (r.get("exception_info") or {}).get("exception_type")
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    trace = Path(res[0]).parent / "agent" / "trace.jsonl"
    events = [json.loads(l) for l in trace.read_text().splitlines() if l.strip()] if trace.exists() else []
    calls = [e for e in events if e.get("event") == "llm_call"]
    if exc and reward is None and not calls:
        return f"killed at start ({exc})"
    attempts = [e for e in events if e.get("event") == "attempt"]
    if attempts and attempts[-1].get("exit_status") in INFRA_EXITS:
        return f"ended by {attempts[-1]['exit_status']}"
    if any(e.get("event") == "attempt" and str(e.get("summary", "")).startswith(("litellm.RateLimitError",
                                                                             "litellm.APIConnectionError", "no model reply for"))
           for e in events):  # 2026-11-18: the builder died on a provider error and Opus did the task alone
        return "builder ended by a provider error (rate limit or connection)"
    broken = [e for e in calls if not str(e.get("reply") or "").strip() and 0 < (e.get("output_tokens") or 0) < 31000]
    if broken and any(e.get("exit_status") == "RepeatedFormatError" for e in events):  # 2026-11-19, phase2/ocx.py
        return f"ended by {len(broken)} empty replies from broken provider streams"
    ar = r.get("agent_result")
    if isinstance(ar, dict) and "n_input_tokens" in ar and not (ar.get("n_input_tokens") or ar.get("n_output_tokens")):
        return "no model tokens (proxy, bridge or account down)"  # 2026-11-17: every call failed
    return ""


def unfinished(job_dir: Path) -> bool:
    return not glob.glob(str(job_dir / "*" / "result.json"))


def set_aside(prefix: str) -> list[tuple[str, str]]:
    """Move infrastructure-ended and unfinished jobs out of the way, so the next queue round reruns them."""
    from phase2 import jobs as J

    out = []
    dest = ROOT / "jobs" / f"_excluded_{prefix}"
    dest.mkdir(exist_ok=True)
    for d in sorted((ROOT / "jobs").glob(f"{prefix}-*")):
        if not d.is_dir():
            continue
        job = J.load_job(d)
        if J.pid_alive(job.pid):
            continue  # still running (another queue)
        why = "unfinished (process gone)" if unfinished(d) else infra_reason(d)
        if not why:
            continue
        target = dest / d.name
        if target.exists():
            target = dest / f"{d.name}.{int(time.time())}"
        shutil.move(str(d), str(target))
        for suf in (".run.json", ".log", ".stopped"):
            side = ROOT / "jobs" / f"{d.name}{suf}"
            if side.exists():
                shutil.move(str(side), str(dest / side.name))
        out.append((d.name, why))
    return out


def docker_ok() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


def ensure_docker(prefix: str) -> bool:
    if docker_ok():
        return True
    log(prefix, "Docker is down: starting Colima")
    subprocess.run(["colima", "start"], capture_output=True, timeout=600)
    return docker_ok()


def network_ok() -> bool:
    try:
        return subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "15", "https://api.z.ai/"],
                              timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def model_ok(model: str) -> bool:
    """One tiny call (Droid or opencodex models; a rejected model id costs nothing). For opencodex models this also
    proves the proxy and the provider account work (2026-11-17: a stopped proxy and Claude inbound switched off
    cost four Phase 1 runs that logged 0 tokens)."""
    if model.startswith("ocx/"):
        from phase2 import ocx

        try:
            text, usage = ocx.complete(model, [{"role": "user", "content": "Reply with exactly: ok"}], timeout=120,
                                       max_output_tokens=200)
        except Exception:
            return False
        return usage.get("prompt_tokens", 0) > 0
    if not model.startswith("droid/"):
        return True
    prompt = ROOT / "jobs" / ".ping.txt"
    prompt.write_text("Reply with exactly: ok")
    try:
        p = subprocess.run(["droid", "exec", "-m", model.split("/", 1)[1], "-o", "json", "--only-tools", "TodoWrite",
                            "-f", str(prompt)], capture_output=True, text=True, timeout=180, cwd="/tmp")
    except subprocess.TimeoutExpired:
        return False
    return '"is_error":false' in p.stdout.replace(" ", "")


def models_in(configs) -> list[str]:
    ms = set()
    for c in configs:
        d = json.loads((ROOT / c).read_text())
        ms |= set(d.get("tiers") or [])
        ms |= {d.get(k) for k in ("senior_reviewer_model", "planner_model", "takeover_model") if d.get(k)}
    return sorted(m for m in ms if m)


def wait_until_healthy(prefix: str, models: list[str], wait_min: int) -> bool:
    deadline = time.time() + wait_min * 60
    while True:
        bad = []
        if not ensure_docker(prefix):
            bad.append("docker")
        if not network_ok():
            bad.append("network")
        if not bad:
            bad += [m for m in models if not model_ok(m)]
        if not bad:
            return True
        if time.time() > deadline:
            log(prefix, f"still unhealthy after {wait_min} min: {bad}; giving up")
            return False
        log(prefix, f"waiting: {bad} not ready; retry in 5 min")
        time.sleep(300)


def other_queue_running(prefix: str) -> bool:
    p = subprocess.run(["pgrep", "-f", f"run_compare.py.*--prefix {prefix} "], capture_output=True, text=True)
    return bool(p.stdout.strip())


def notify(title: str, msg: str) -> None:
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'], capture_output=True)


def main(argv=None) -> int:
    import run_compare

    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--config", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--setups", required=True)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--wait-model-min", type=int, default=180)
    a = ap.parse_args(argv)
    setups = a.setups.split(",")
    configs = sorted({a.config} | {run_compare.SETUPS[s][2] for s in setups if len(run_compare.SETUPS[s]) > 2})
    models = models_in(configs)
    log(a.prefix, f"start: setups={setups} models={models} parallel={a.parallel} repeats={a.repeats}")
    (ROOT / "jobs" / f"{a.prefix}.plan.json").write_text(json.dumps(  # for scripts/progress.py and the TUI
        {"tasks": a.tasks, "setups": setups, "repeats": a.repeats, "parallel": a.parallel, "config": a.config}))
    while other_queue_running(a.prefix):  # an earlier queue for this prefix: let it finish first
        log(a.prefix, "another queue for this prefix is running; waiting for it")
        time.sleep(120)
    moved_all = []
    for rnd in range(1, a.max_rounds + 1):
        moved = set_aside(a.prefix)
        moved_all += moved
        for name, why in moved:
            log(a.prefix, f"set aside to rerun: {name} ({why})")
        if rnd > 1 and not moved:
            break
        if not wait_until_healthy(a.prefix, models, a.wait_model_min):
            break
        log(a.prefix, f"round {rnd}: queue starts")
        run_compare.run(a.config, a.prefix, a.parallel, a.tasks, setups, False, a.repeats)
        log(a.prefix, f"round {rnd}: queue done")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_compare.summary(a.prefix)
    still = [(d.name, infra_reason(d)) for d in sorted((ROOT / "jobs").glob(f"{a.prefix}-*")) if d.is_dir()
             and infra_reason(d)]
    report = (f"Report for {a.prefix}, {time.strftime('%Y-%m-%d %H:%M')}\n\n{buf.getvalue()}\n"
              f"Set aside and rerun ({len(moved_all)}): " + "; ".join(f"{n} ({w})" for n, w in moved_all) +
              f"\nStill infrastructure-ended after the last round ({len(still)}): " +
              "; ".join(f"{n} ({w})" for n, w in still) + "\n")
    (ROOT / "jobs" / f"{a.prefix}.REPORT.txt").write_text(report)
    log(a.prefix, f"done: report in jobs/{a.prefix}.REPORT.txt")
    notify("MLR570 evaluation done", f"{a.prefix}: report in jobs/{a.prefix}.REPORT.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
