"""Replay a finished run so the TUI shows it as if it were live: no Docker, no models, no credits.

  .venv/bin/python scripts/demo_replay.py --job jobs/<job> --speed 10 --root demo
  .venv/bin/python -m phase2.tui --root demo

The job is copied into <root>/jobs/<job>-replay and its trace.jsonl is written back event by event at
`speed` times the recorded pace; each worker's trajectory grows turn by turn with its model calls. While the
replay runs, this process is the job's "pid", so the TUI shows it as running; at the end the Harbor result is
copied in and the job shows passed or failed, as recorded.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def trial_of(job: Path) -> Path:
    trials = [p for p in job.iterdir() if p.is_dir() and "__" in p.name]
    if not trials:
        sys.exit(f"{job} has no trial folder")
    return trials[0]


def prefix(messages: list[dict], turns: int) -> list[dict]:
    """The trajectory as it stood after `turns` model replies: the prompt, then each reply and its output."""
    seen = 0
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and i >= 2:
            seen += 1
            if seen > turns:
                return messages[:i]
    return messages


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, help="a finished job folder, e.g. jobs/ev-sonnet-...")
    ap.add_argument("--speed", type=float, default=10.0, help="times faster than recorded")
    ap.add_argument("--root", default=str(ROOT / "demo"), help="the TUI's --root")
    a = ap.parse_args(argv)

    src = Path(a.job).resolve()
    src_trial = trial_of(src)
    events = [json.loads(l) for l in (src_trial / "agent" / "trace.jsonl").read_text().splitlines() if l.strip()]
    name = f"{src.name}-replay"
    jobs = Path(a.root) / "jobs"
    dest = jobs / name
    shutil.rmtree(dest, ignore_errors=True)
    agent = dest / src_trial.name / "agent"
    agent.mkdir(parents=True)
    for f in ("config.json",):
        if (src / f).exists():
            shutil.copy(src / f, dest / f)
    (jobs / f"{name}.run.json").write_text(json.dumps({"pid": os.getpid(), "started": time.time(),
                                                       "task": "", "cmd": []}))
    workers = {p.stem: json.loads(p.read_text()).get("messages", [])
               for p in (src_trial / "agent").glob("worker_*.json") if not p.stem.endswith("_repair")}
    attempt_no, turns = {}, {}

    def file_for(step: str) -> str:
        if f"worker_{step}" in workers:  # a lead's helper: one file per helper
            return f"worker_{step}"
        return f"worker_{step}_a{attempt_no.get(step, 1)}"

    def write_worker(key: str, n: int | None = None) -> None:
        if key in workers:
            msgs = workers[key] if n is None else prefix(workers[key], n)
            (agent / f"{key}.json").write_text(json.dumps({"messages": msgs}))

    trace = (agent / "trace.jsonl").open("a", buffering=1)
    t0 = time.monotonic()
    for e in events:
        wait = e.get("t", 0) / a.speed - (time.monotonic() - t0)
        if wait > 0:
            time.sleep(wait)
        ev, step = e.get("event"), e.get("step")
        if ev == "llm_call" and e.get("role") in ("worker", "helper") and step:
            key = file_for(step)
            turns[key] = turns.get(key, 0) + 1
            write_worker(key, turns[key])
        elif ev == "attempt" and step:
            write_worker(file_for(step))
            attempt_no[step] = attempt_no.get(step, 1) + 1
        elif ev == "helper_end":
            write_worker(f"worker_{e.get('helper')}")
        trace.write(json.dumps(e) + "\n")
    trace.close()
    for key in workers:
        write_worker(key)
    for f in ("result.json",):
        if (src_trial / f).exists():
            shutil.copy(src_trial / f, dest / src_trial.name / f)
    if (src_trial / "verifier").is_dir():
        shutil.copytree(src_trial / "verifier", dest / src_trial.name / "verifier", dirs_exist_ok=True)
    print(f"replayed {len(events)} events of {src.name} at {a.speed}x")


if __name__ == "__main__":
    main()
