"""Run the harness on your own task and folder, not a benchmark task.

The folder is copied into a sandbox container (Docker, through Harbor, exactly like the evaluations), the harness works
there, and what changed comes back as a diff. Your folder is not touched unless you say yes to applying the diff.
A check command (for example your tests) is optional; without one the run is not graded, only reported.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", ".DS_Store", ".env"}

TASK_TOML = """schema_version = "1.4"
artifacts = []

[metadata]
category = "local"

[verifier]
timeout_sec = 900.0
collect = []

[verifier.env]

[agent]
timeout_sec = {agent_sec}.0

[environment]
network_mode = "public"
build_timeout_sec = 900.0
os = "linux"
mcp_servers = []

[environment.env]

[solution.env]
"""

DOCKERFILE = """FROM {image}
RUN (command -v git >/dev/null || (apt-get update && apt-get install -y --no-install-recommends git ca-certificates \\
     python3 python3-pip python3-venv curl && rm -rf /var/lib/apt/lists/*))
COPY project/ /app/
WORKDIR /app
RUN git init -q && printf '__pycache__/\\n*.pyc\\nnode_modules/\\n.venv/\\n.pytest_cache/\\n.mypy_cache/\\n' >> .git/info/exclude \
    && git add -A && git -c user.email=harness@local -c user.name=harness commit -qm start --allow-empty
"""

# The verifier: record every change as a diff, then run the person's check (if any). Reward 1 = the check passed.
TEST_SH = """#!/bin/bash
mkdir -p /logs/verifier
cd /app && git add -A && git diff --cached --binary > /logs/verifier/changes.diff
git diff --cached --stat > /logs/verifier/changes.stat
{check_block}
"""
CHECK_BLOCK = """cd /app && ( {check} ) > /logs/verifier/check.txt 2>&1
code=$?; echo "exit $code" >> /logs/verifier/check.txt
if [ $code -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi"""
NO_CHECK = "echo 1 > /logs/verifier/reward.txt  # no check given: not graded"

NOTE = """

(The project is in /app, a git repository whose first commit is the starting state. Work only inside /app.)"""


def slug(text: str, n: int = 32) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "task"


def make_task(folder: Path, instruction: str, out: Path, check: str = "", image: str = "ubuntu:24.04",
              agent_sec: int = 3600) -> Path:
    """Write a Harbor task at `out` whose environment is a copy of `folder`."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    if out.exists():
        shutil.rmtree(out)
    (out / "environment").mkdir(parents=True)
    (out / "tests").mkdir()
    shutil.copytree(folder, out / "environment" / "project", ignore=lambda d, names: [n for n in names if n in SKIP])
    (out / "instruction.md").write_text(instruction.strip() + NOTE + "\n")
    (out / "task.toml").write_text(TASK_TOML.format(agent_sec=agent_sec))
    (out / "environment" / "Dockerfile").write_text(DOCKERFILE.format(image=image))
    test = TEST_SH.format(check_block=CHECK_BLOCK.format(check=check) if check else NO_CHECK)
    (out / "tests" / "test.sh").write_text(test)
    (out / "tests" / "test.sh").chmod(0o755)
    (out / "local.json").write_text(json.dumps({"folder": str(folder), "instruction": instruction, "check": check,
                                                "image": image}, indent=2))
    return out


def status_lines(events: list[dict], seen: int) -> list[str]:
    """What happened since event `seen`, in plain words, for the person watching."""
    from .strategy import MODES

    out = []
    for e in events[seen:]:
        ev = e.get("event")
        if ev == "strategy":
            out.append(f"Way of working: {MODES.get(e.get('mode'), (e.get('mode'),))[0]}. {e.get('why', '')}")
        elif ev == "route":
            out.append(f"Builder: {str(e.get('model', '')).split('/')[-1]}")
        elif ev == "senior_review" and e.get("round") == 1:
            probs = e.get("problems") or []
            out.append("Review: approved" if not probs else
                       f"Review: sent back {len(probs)} problem(s): " + "; ".join(p.get("what", "")[:90] for p in probs))
        elif ev == "attempt":
            out.append(f"Builder finished ({e.get('exit_status')}) after {e.get('n_calls')} commands")
    return out


def result(trial: Path) -> dict:
    """The outcome of a finished local run: the diff, its stat, the check, the builder's report, spend."""
    from .list_prices import trace_usd

    v, events = trial / "verifier", []
    tr = trial / "agent" / "trace.jsonl"
    if tr.exists():
        events = [json.loads(l) for l in tr.read_text().splitlines() if l.strip()]
    att = [e for e in events if e.get("event") == "attempt"]
    read = lambda name: (v / name).read_text(errors="replace") if (v / name).exists() else ""
    r = json.loads((trial / "result.json").read_text()) if (trial / "result.json").exists() else {}
    return {"diff": read("changes.diff"), "stat": read("changes.stat").strip(), "check": read("check.txt").strip(),
            "reward": ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
            "report": att[-1].get("summary", "") if att else "", "usd": trace_usd(events),
            "minutes": events[-1]["t"] / 60 if events else 0.0,
            "error": ((r.get("exception_info") or {}).get("exception_type") or "")}


def apply_diff(folder: Path, diff: str, run=subprocess.run) -> str:
    """Apply the run's changes to the person's folder (only when they said yes). Checks first, changes nothing on
    a conflict."""
    if not diff.strip():
        return "nothing to apply"
    tmp = folder / ".harness-changes.diff"
    tmp.write_text(diff)
    try:
        check = run(["git", "apply", "--check", str(tmp)], cwd=folder, capture_output=True, text=True)
        if check.returncode != 0:
            return f"not applied: {check.stderr.strip()[:300]}"
        run(["git", "apply", str(tmp)], cwd=folder, check=True, capture_output=True)
        return "applied"
    finally:
        tmp.unlink(missing_ok=True)


def wait_for_trial(job_dir: Path, pid_alive, pid: int, poll: float = 5.0, sleep=time.sleep):
    """Yield (trial dir or None, events) every `poll` seconds until the run ends."""
    from . import jobs as J

    while True:
        trials = [p for p in job_dir.glob("*__*") if p.is_dir()] if job_dir.exists() else []
        trial = trials[0] if trials else None
        yield trial, J.read_events(trial / "agent") if trial else []
        if trial and (trial / "result.json").exists():
            return
        if not pid_alive(pid):
            return
        sleep(poll)
