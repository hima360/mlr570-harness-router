"""Shared blackboard: status, owner model and outputs of every step, visible to all workers."""
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Entry:
    goal: str
    status: str = "pending"  # pending | running | done | failed
    tier: int | None = None
    model: str | None = None
    attempts: int = 0
    summary: str = ""
    check_output: str = ""


@dataclass
class Blackboard:
    task: str
    entries: dict[str, Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, step_id: str, goal: str) -> None:
        self.entries[step_id] = Entry(goal=goal)

    def update(self, step_id: str, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self.entries[step_id], k, v)

    def summary_for(self, step_id: str) -> str:
        """What a worker sees about the other steps (its own history stays private)."""
        lines = []
        with self._lock:
            for sid, e in self.entries.items():
                if sid == step_id:
                    continue
                note = f" -> {e.summary[:300]}" if e.summary else ""
                lines.append(f"- {sid} [{e.status}] {e.goal[:200]}{note}")
        return "\n".join(lines) or "(no other steps)"

    def to_dict(self) -> dict:
        with self._lock:
            return {"task": self.task, "entries": {k: asdict(v) for k, v in self.entries.items()}}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))
