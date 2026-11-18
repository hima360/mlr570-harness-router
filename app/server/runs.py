"""Read Harbor jobs from jobs/: list them, find a job's trial, and turn its trace into UI events.

Everything here reads files the harness and Harbor already write; nothing is changed.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from phase2 import jobs as J
from phase2.results import split_name, tests_fraction

from .models import setup_label
from .normalize import Normalizer, result_event

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


def safe_name(name: str) -> str:
    if not NAME_RE.match(name or "") or ".." in name:
        raise ValueError(f"bad job name {name!r}")
    return name


def read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def trial_dir(job_dir: Path) -> Path | None:
    """Newest trial folder of a job (Harbor names them <task>__<id>)."""
    if not job_dir.is_dir():
        return None
    trials = [p for p in job_dir.iterdir() if p.is_dir() and "__" in p.name]
    return max(trials, key=lambda p: p.stat().st_mtime) if trials else None


def read_rows(trace: Path) -> list[dict]:
    rows = []
    try:
        for line in trace.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a line being written
    except OSError:
        pass
    return rows


def kind_of(name: str) -> str:
    if name.startswith("chat-"):
        return "chat"
    if name.startswith("app-"):
        return "rerun"
    return "benchmark"


def chat_meta(root: Path, name: str) -> dict:
    """The chat's own record (jobs/_adhoc/<id>.chat.json), written when the app started it."""
    if not name.startswith("chat-"):
        return {}
    cid = name.rsplit("-", 1)[-1]
    return read_json(root / "jobs" / "_adhoc" / f"{cid}.chat.json")


def job_summary(root: Path, name: str) -> dict:
    """One row for the history sidebar and run headers."""
    job = J.load_job(root / "jobs" / name)
    kind = kind_of(name)
    try:
        prefix, setup, task, rep = split_name(name)
    except ValueError:
        prefix, setup, task, rep = name, "", name, 1
    meta = chat_meta(root, name)
    title = meta.get("text") or (job.task or task).removeprefix("terminal-bench/")
    status = job.status
    if kind == "chat" and status in ("passed", "failed"):
        status = "finished"  # an ad-hoc task has no grader: reward 1 only means the run completed
    return {"job": name, "kind": kind, "title": title.strip()[:200], "setup": setup,
            "setup_label": setup_label(setup), "task": task, "prefix": prefix, "repeat": rep, "status": status,
            "reward": job.reward, "started": job.started, "exception": job.exception,
            "grader": kind != "chat", "policy": job.policy, "config": job.config}


def list_history(root: Path, limit: int = 40) -> list[dict]:
    """App-started runs (chat-*, app-*) first-class, plus the most recent other jobs."""
    jobs_dir = root / "jobs"
    if not jobs_dir.is_dir():
        return []
    entries = []
    for p in jobs_dir.iterdir():
        if p.name.startswith(("_", ".")):
            continue
        if p.is_dir():
            name = p.name
        elif p.name.endswith(".run.json"):
            name = p.name.removesuffix(".run.json")
        else:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        entries.append((name, mt))
    best: dict[str, float] = {}
    for name, mt in entries:
        best[name] = max(best.get(name, 0), mt)
    ordered = sorted(best, key=best.get, reverse=True)
    mine = [n for n in ordered if n.startswith(("chat-", "app-"))]
    others = [n for n in ordered if not n.startswith(("chat-", "app-"))][:limit]
    out = []
    for n in mine + others:
        try:
            out.append(job_summary(root, n))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: r["started"] or 0, reverse=True)
    return out


def trace_t0(trace: Path, rows: list[dict]) -> float | None:
    """Wall-clock time of the trace's t=0 (so the browser can run timers for open phases)."""
    try:
        st = trace.stat()
    except OSError:
        return None
    born = getattr(st, "st_birthtime", None)
    if born:
        return born
    return st.st_mtime - (float(rows[-1].get("t") or 0) if rows else 0)


def snapshot(root: Path, name: str) -> dict:
    """The whole run as UI events (for replay and for catching up a live view)."""
    summary = job_summary(root, name)
    td = trial_dir(root / "jobs" / name)
    trace = td / "agent" / "trace.jsonl" if td else None
    rows = read_rows(trace) if trace else []
    norm = Normalizer()
    events = norm.feed_all(rows)
    result = read_json(td / "result.json") if td else {}
    if result:
        events.append(result_event(result, tests_fraction(str(td) + "/"), summary["grader"], norm.seq + 1,
                                   norm.last_t))
    return {"run": summary, "t0": trace_t0(trace, rows) if trace else None, "events": events,
            "trial": td.name if td else None, "instruction": instruction(root, name, td)}


def instruction(root: Path, name: str, td: Path | None) -> str:
    meta = chat_meta(root, name)
    if meta.get("text"):
        return meta["text"]
    if td:
        cfg = read_json(td / "config.json")
        path = ((cfg.get("task") or {}).get("path"))
        if path:
            p = (root / path) if not os.path.isabs(path) else Path(path)
            try:
                return (p / "instruction.md").read_text()[:6000]
            except OSError:
                pass
        rows = read_rows(td / "agent" / "trace.jsonl")
        plan = next((r for r in rows if r.get("event") == "plan"), None)
        if plan and plan.get("steps"):
            return str(plan["steps"][0].get("goal", ""))[:6000]
    return ""


def harbor_log_tail(root: Path, name: str, n: int = 3) -> str:
    try:
        lines = [l for l in (root / "jobs" / f"{name}.log").read_text(errors="replace").splitlines() if l.strip()]
    except OSError:
        return ""
    return "\n".join(lines[-n:])[-400:]


class LiveTail:
    """Follows one job as it runs: new trace rows -> UI events, then the verifier result."""

    def __init__(self, root: Path, name: str):
        self.root, self.name = root, name
        self.norm = Normalizer()
        self.offset = 0
        self.buf = ""
        self.td: Path | None = None
        self.finished = False
        self.last_status = None
        self.t0 = None

    def poll(self) -> list[dict]:
        out: list[dict] = []
        job_dir = self.root / "jobs" / self.name
        job = J.load_job(job_dir)
        if self.td is None:
            self.td = trial_dir(job_dir)
            if self.td and job.status == "running":
                self.last_status = "running"
                out.append({"type": "status", "status": "running", "detail": "The harness is running."})
        if self.td:
            trace = self.td / "agent" / "trace.jsonl"
            if trace.exists():
                if self.t0 is None:
                    self.t0 = trace_t0(trace, [])
                    out.append({"type": "clock", "t0": self.t0})
                with trace.open() as f:
                    f.seek(self.offset)
                    chunk = f.read()
                    self.offset = f.tell()
                self.buf += chunk
                *lines, self.buf = self.buf.split("\n")
                for line in lines:
                    if line.strip():
                        try:
                            out += self.norm.feed(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            result = read_json(self.td / "result.json")
            if result:
                summary = job_summary(self.root, self.name)
                out.append(result_event(result, tests_fraction(str(self.td) + "/"), summary["grader"],
                                        self.norm.seq + 1, self.norm.last_t))
                out.append({"type": "status", "status": summary["status"], "detail": "Finished."})
                self.finished = True
                return out
        if job.status in ("error", "stopped") and not (self.td and (self.td / "result.json").exists()):
            if job.status != self.last_status:
                out.append({"type": "status", "status": job.status,
                            "detail": job.exception or harbor_log_tail(self.root, self.name)})
            self.finished = True
        elif self.td is None and self.last_status != "starting":
            out.append({"type": "status", "status": "starting",
                        "detail": "Harbor is building the task container."})
            self.last_status = "starting"
        return out


def now() -> float:
    return time.time()
