"""Harbor jobs for the TUI: list them, read what happened, start and stop runs.

A run is started detached (own process group, under `caffeinate -i`) so it survives the TUI closing;
its pid goes to `jobs/<job>.run.json`. Stopping kills that process group and removes the trial's
Docker containers and network, which Harbor leaves behind when it is killed.
"""
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TB_DATASET = "terminal-bench/terminal-bench@4.0.0"
SMOKE_TASK = "tests/harbor_smoke/hello-file"
# Text-only TB 4.0 tasks without GPUs (CLI models cannot see images).
TASKS = [
    ("smoke: hello-file (seconds)", SMOKE_TASK),
    ("tb4: shadow-relay (forensics)", "terminal-bench/shadow-relay"),
    ("tb4: interleaved-vigenere (crypto)", "terminal-bench/interleaved-vigenere"),
    ("tb4: session-window-debug (debugging)", "terminal-bench/session-window-debug"),
    ("tb4: html-js-filter (web)", "terminal-bench/html-js-filter"),
    ("tb4: data-anonymization (data)", "terminal-bench/data-anonymization"),
    ("tb4: heat-pump-warranty (rules + services)", "terminal-bench/heat-pump-warranty"),
]
POLICIES = ["laya", "cascade", "random", "arch"]


@dataclass
class Job:
    name: str
    path: Path
    status: str = "incomplete"  # running | passed | failed | error | stopped | incomplete
    reward: float | None = None
    task: str = ""
    config: str = ""
    policy: str = ""
    caps: dict = field(default_factory=dict)
    trial_dir: Path | None = None
    exception: str = ""
    pid: int | None = None
    started: float = 0.0

    @property
    def agent_dir(self) -> Path | None:
        return self.trial_dir / "agent" if self.trial_dir else None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:  # reap it if it is our child (a run started from this TUI), else a zombie looks alive
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_job(path: Path) -> Job:
    job = Job(path.name, path)
    run = _read_json(path.parent / f"{path.name}.run.json")
    job.pid = run.get("pid")
    job.started = run.get("started", 0.0) or (path.stat().st_mtime if path.exists() else 0.0)
    if not path.is_dir():  # started from the TUI, Harbor has not created the job dir yet
        job.task = run.get("task", "")
        cmd = run.get("cmd", [])
        job.config = next((a.split("=", 1)[1] for a in cmd if a.startswith("config=")), "")
        job.policy = next((a.split("=", 1)[1] for a in cmd if a.startswith("policy=")), "")
        if pid_alive(job.pid):
            job.status = "running"
        else:
            job.status, job.exception = "error", "Harbor exited before creating the job (see the Harbor log tab)"
        return job
    cfg = _read_json(path / "config.json")
    kwargs = ((cfg.get("agents") or [{}])[0].get("kwargs") or {})
    job.config, job.policy = kwargs.get("config", ""), kwargs.get("policy", "")
    job.caps = {k: v for k, v in kwargs.items() if k not in ("config", "policy")}
    tasks = cfg.get("tasks") or []
    datasets = cfg.get("datasets") or []
    job.task = (tasks[0].get("path", "") if tasks else
                ",".join((datasets[0].get("task_names") or [])) if datasets else run.get("task", ""))
    trials = sorted((p for p in path.iterdir() if p.is_dir() and "__" in p.name), key=lambda p: p.stat().st_mtime) \
        if path.is_dir() else []
    job.trial_dir = trials[-1] if trials else None
    result = _read_json(job.trial_dir / "result.json") if job.trial_dir else {}
    if result:
        job.task = result.get("task_name") or job.task
        exc = result.get("exception_info") or {}
        job.reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if exc and job.reward is None:
            stopped = exc.get("exception_type") == "CancelledError"  # killed mid-run
            job.status = "stopped" if stopped else "error"
            job.exception = f"{exc.get('exception_type')}: {exc.get('exception_message', '')[:300]}"
        else:
            job.status = "passed" if job.reward == 1 else "failed"
    elif pid_alive(job.pid):
        job.status = "running"
    elif (path.parent / f"{path.name}.stopped").exists():
        job.status = "stopped"
    elif job.trial_dir:
        job.status, job.exception = "stopped", "trial never finished (its process is gone, e.g. killed)"
    elif _read_json(path / "result.json"):
        job.status = "error"
        job.exception = "Harbor finished without a trial (see the Harbor log tab)"
    return job


def list_jobs(jobs_dir: Path) -> list[Job]:
    if not jobs_dir.is_dir():
        return []
    paths = {p for p in jobs_dir.iterdir() if p.is_dir()}
    paths |= {jobs_dir / p.name.removesuffix(".run.json") for p in jobs_dir.glob("*.run.json")}
    return sorted((load_job(p) for p in paths), key=lambda j: j.started, reverse=True)


def read_events(agent_dir: Path | None) -> list[dict]:
    if not agent_dir:
        return []
    out = []
    try:
        for line in (agent_dir / "trace.jsonl").read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # line being written
    except OSError:
        pass
    return out


def worker_files(agent_dir: Path | None) -> list[Path]:
    if not agent_dir or not agent_dir.is_dir():
        return []
    return sorted(agent_dir.glob("worker_*.json"), key=lambda p: p.stat().st_mtime)


def read_worker(path: Path) -> list[dict]:
    return _read_json(path).get("messages", [])


def build_command(task: str, config: str, policy: str, caps: dict, job_name: str) -> list[str]:
    harbor = str(Path(sys.executable).parent / "harbor")
    where = ["-p", task] if not task.startswith("terminal-bench/") else ["-d", TB_DATASET, "-i", task]
    ak = ["--ak", f"config={config}", "--ak", f"policy={policy}"]
    for k, v in caps.items():
        if v not in (None, ""):
            ak += ["--ak", f"{k}={v}"]
    return ["caffeinate", "-i", harbor, "run", *where, "-a", "phase2.agent:RoutedHarness", *ak,
            "-n", "1", "-o", "jobs", "--job-name", job_name]


def start_run(root: Path, task: str, config: str, policy: str, caps: dict, job_name: str,
              popen=subprocess.Popen) -> int:
    jobs = root / "jobs"
    jobs.mkdir(exist_ok=True)
    if (jobs / job_name).exists():
        raise ValueError(f"job {job_name!r} already exists")
    cmd = build_command(task, config, policy, caps, job_name)
    env = {**os.environ, "PYTHONPATH": str(root)}
    with open(jobs / f"{job_name}.log", "w") as log:
        p = popen(cmd, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                  start_new_session=True)
    (jobs / f"{job_name}.run.json").write_text(json.dumps(
        {"pid": p.pid, "cmd": cmd, "task": task, "started": time.time()}, indent=2))
    return p.pid


def compose_project(trial_dir: Path) -> str:
    return trial_dir.name.lower()


def stop_run(job: Job, run=subprocess.run, grace_sec: float = 5.0) -> list[str]:
    """Kill the run's process group, then remove the trial's leftover containers and network."""
    done = []
    if pid_alive(job.pid):
        for sig in (signal.SIGINT, signal.SIGKILL):
            try:
                os.killpg(job.pid, sig)
            except (ProcessLookupError, PermissionError):  # group already gone (or only a zombie left)
                break
            deadline = time.time() + grace_sec
            while time.time() < deadline and pid_alive(job.pid):
                time.sleep(0.2)
            if not pid_alive(job.pid):
                break
        done.append(f"stopped process group {job.pid}")
    if job.trial_dir:
        name = compose_project(job.trial_dir)
        ids = run(["docker", "ps", "-aq", "--filter", f"name={name}"], capture_output=True, text=True).stdout.split()
        if ids:
            run(["docker", "rm", "-f", *ids], capture_output=True, text=True)
            done.append(f"removed {len(ids)} container(s)")
        nets = run(["docker", "network", "ls", "-q", "--filter", f"name={name}"],
                   capture_output=True, text=True).stdout.split()
        if nets:
            run(["docker", "network", "rm", *nets], capture_output=True, text=True)
            done.append(f"removed {len(nets)} network(s)")
    (job.path.parent / f"{job.name}.stopped").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
    return done
