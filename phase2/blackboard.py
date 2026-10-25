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
    outputs: dict[str, str] = field(default_factory=dict)  # published facts, kept in full


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

    def summary_for(self, step_id: str, depends_on=()) -> str:
        """What a worker sees about the other steps (its own history stays private). Finished steps
        show their published facts (up to 2,000 chars for a dependency, 400 otherwise) rather than a
        cut summary; a failed dependency says so and what to do about it."""
        lines = []
        with self._lock:
            for sid, e in self.entries.items():
                if sid == step_id:
                    continue
                dep = sid in depends_on
                head = f"- {sid} [{e.status}]{' (your step needs this)' if dep else ''} {e.goal[:200]}"
                where = f"/tmp/steps/{sid}/"
                if dep and e.status == "failed":
                    facts = f"; partial results: {facts_text(sid, e.outputs, 2000)}" if e.outputs else ""
                    lines.append(f"{head}\n  It FAILED, so produce what your step needs from it yourself{facts}. "
                                 f"Its notes: {where} . Last report: {e.summary[:300] or '(none)'}")
                elif e.outputs:
                    lines.append(f"{head}\n  results: {facts_text(sid, e.outputs, 2000 if dep else 400)}")
                elif dep and e.status == "done":
                    note = f" Its report: {e.summary[:300]}" if e.summary else ""
                    lines.append(f"{head}\n  It published no facts; look in {where} and the files it wrote.{note}")
                else:
                    note = f" -> {e.summary[:300]}" if e.summary else ""
                    lines.append(f"{head}{note}")
        return "\n".join(lines) or "(no other steps)"

    def to_dict(self) -> dict:
        with self._lock:
            return {"task": self.task, "entries": {k: asdict(v) for k, v in self.entries.items()}}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))


def facts_text(step_id: str, outputs: dict, cap: int) -> str:
    """name=value pairs up to `cap` chars; the rest stays on disk and the text says where."""
    parts, size = [], 0
    for name, value in outputs.items():
        item = f"{name}={value}"
        if size + len(item) > cap:
            room = cap - size
            if room > 40:
                parts.append(item[:room] + "...")
            parts.append(f"(rest in /tmp/steps/{step_id}/outputs.txt)")
            break
        parts.append(item)
        size += len(item) + 2
    return "; ".join(parts)
