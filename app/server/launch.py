"""Start runs: an ad-hoc chat task, or a rerun of a recorded benchmark task. Both go through
phase2.jobs.start_run with a setup from phase2.setups.SETUPS, so the harness, its config and prices are untouched."""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from phase2.setups import SETUPS

from .models import LIVE_SETUPS
from .runs import read_json, safe_name

LIVE_IDS = [s["id"] for s in LIVE_SETUPS]

TASK_TOML = """schema_version = "1.4"
artifacts = []

[metadata]

[verifier]
timeout_sec = 120.0
collect = []

[verifier.env]

[agent]
timeout_sec = 3600.0

[environment]
network_mode = "public"
build_timeout_sec = 900.0
os = "linux"
mcp_servers = []

[environment.env]

[solution.env]
"""

DOCKERFILE = """FROM python:3.12-slim

# Ad-hoc task from the Relay app: a small Debian + Python workspace with common tools.
RUN apt-get update \\
 && apt-get install -y --no-install-recommends git curl ca-certificates jq less procps file unzip \\
    build-essential sqlite3 \\
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
"""

# No benchmark grader: the verifier passes whenever it runs. The app labels these runs "no benchmark grader".
TEST_SH = """#!/bin/bash
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt
"""


def setup_args(setup: str) -> tuple[str, str, dict]:
    """(config path, policy, caps) for a setup, exactly as scripts/run_compare.py uses them."""
    if setup not in SETUPS:
        raise ValueError(f"unknown setup {setup!r}")
    policy, caps, *cfg = SETUPS[setup]
    config = cfg[0] if cfg else "configs/phase2-ocx-base.json"
    return config, policy, dict(caps)


def create_adhoc_task(root: Path, text: str, cid: str | None = None) -> tuple[str, Path]:
    """jobs/_adhoc/<id>/ shaped like tests/harbor_smoke/hello-file. Returns (id, task dir)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty task")
    if len(text) > 20000:
        raise ValueError("task text too long (20,000 characters at most)")
    cid = cid or time.strftime("%m%d%H%M%S") + secrets.token_hex(2)
    task = root / "jobs" / "_adhoc" / cid
    (task / "environment").mkdir(parents=True, exist_ok=False)
    (task / "tests").mkdir()
    (task / "task.toml").write_text(TASK_TOML)
    (task / "instruction.md").write_text(text + "\n")
    (task / "environment" / "Dockerfile").write_text(DOCKERFILE)
    sh = task / "tests" / "test.sh"
    sh.write_text(TEST_SH)
    sh.chmod(0o755)
    return cid, task


def start_chat(root: Path, text: str, setup: str, launcher) -> dict:
    if setup not in LIVE_IDS:
        raise ValueError(f"setup must be one of {', '.join(LIVE_IDS)}")
    config, policy, caps = setup_args(setup)
    cid, task = create_adhoc_task(root, text)
    job = f"chat-{setup}-{cid}"
    (root / "jobs" / "_adhoc" / f"{cid}.chat.json").write_text(json.dumps(
        {"id": cid, "text": text.strip(), "setup": setup, "job": job, "created": time.time()}, indent=2))
    pid = launcher(root, str(task.relative_to(root)), config, policy, caps, job)
    return {"job": job, "pid": pid, "id": cid}


def rerun_task(root: Path, source_job: str) -> str:
    """The Harbor task a recorded job ran: 'terminal-bench/<name>' or a local task path."""
    safe_name(source_job)
    td = root / "jobs" / source_job
    trials = [p for p in td.iterdir() if p.is_dir() and "__" in p.name] if td.is_dir() else []
    for t in trials:
        r = read_json(t / "result.json")
        if r.get("task_name", "").startswith("terminal-bench/"):
            return r["task_name"]
        path = ((r.get("config") or {}).get("task") or {}).get("path")
        if path:
            return path
    cfg = read_json(td / "config.json")
    ds = cfg.get("datasets") or []
    names = (ds[0].get("task_names") or []) if ds else []
    if names:
        n = names[0]
        return n if n.startswith("terminal-bench/") else f"terminal-bench/{n}"
    tasks = cfg.get("tasks") or []
    if tasks and tasks[0].get("path"):
        return tasks[0]["path"]
    raise ValueError(f"cannot tell which task {source_job!r} ran")


def start_rerun(root: Path, source_job: str, setup: str, launcher) -> dict:
    if setup not in LIVE_IDS:
        raise ValueError(f"setup must be one of {', '.join(LIVE_IDS)}")
    task = rerun_task(root, source_job)
    config, policy, caps = setup_args(setup)
    short = re.sub(r"[^a-z0-9-]+", "-", task.rstrip("/").split("/")[-1].lower()).strip("-") or "task"
    n = 1
    while (root / "jobs" / f"app-{setup}-{short}-r{n}").exists() or \
            (root / "jobs" / f"app-{setup}-{short}-r{n}.run.json").exists():
        n += 1
    job = f"app-{setup}-{short}-r{n}"
    pid = launcher(root, task, config, policy, caps, job)
    return {"job": job, "pid": pid, "task": task}
