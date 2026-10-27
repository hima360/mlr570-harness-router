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
    # the step's role in the team, from the plan
    writes: list[str] = field(default_factory=list)
    promises: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Blackboard:
    task: str
    entries: dict[str, Entry] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)  # finished / failed / published, for live team updates
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, step_id: str, goal: str, writes=(), promises=(), depends_on=()) -> None:
        self.entries[step_id] = Entry(goal=goal, writes=list(writes), promises=list(promises),
                                      depends_on=list(depends_on))

    def update(self, step_id: str, **fields) -> None:
        with self._lock:
            e = self.entries[step_id]
            if fields.get("outputs") and fields["outputs"] != e.outputs:
                new = {k: v for k, v in fields["outputs"].items() if e.outputs.get(k) != v}
                self.events.append({"step": step_id, "kind": "published", "facts": new})
            if fields.get("status") in ("done", "failed") and fields["status"] != e.status:
                self.events.append({"step": step_id, "kind": fields["status"]})
            for k, v in fields.items():
                setattr(e, k, v)

    def feed_for(self, step_id: str, depends_on=(), cap: int = 500):
        """A callable that returns what the team did since its last call, as one short TEAM UPDATE line
        ("" when nothing new): dependencies first. A running worker gets it before its next model call."""
        seen = [len(self.events)]

        def updates() -> str:
            with self._lock:
                new, seen[0] = self.events[seen[0]:], len(self.events)
            new = [ev for ev in new if ev["step"] != step_id]
            new.sort(key=lambda ev: ev["step"] not in depends_on)
            parts = []
            for ev in new:
                if ev["kind"] == "published":
                    parts.append(f"{ev['step']} published {facts_text(ev['step'], ev['facts'], 200)}")
                else:
                    parts.append(f"{ev['step']} {'finished' if ev['kind'] == 'done' else 'FAILED'}"
                                 f"{' (your step needs it)' if ev['step'] in depends_on else ''}")
            text = "; ".join(parts)
            return f"TEAM UPDATE: {text[:cap]}" if text else ""

        return updates

    def summary_for(self, step_id: str, depends_on=(), roster: bool = True) -> str:
        """What a worker sees about the other steps (its own history stays private). Finished steps
        show their published facts (up to 2,000 chars for a dependency, 400 otherwise) rather than a
        cut summary; a failed dependency says so and what to do about it."""
        lines = []
        with self._lock:
            me = self.entries.get(step_id)
            waiting = [f"{sid} (needs: {', '.join(e.promises) or 'your work'})" if me and me.promises else sid
                       for sid, e in self.entries.items() if step_id in e.depends_on]
            if roster and len(self.entries) > 1:
                lines.append(f"TEAM: {len(self.entries)} agents share this task; each owns the files listed and "
                             f"publishes the results listed. YOU are {step_id}."
                             + (f" You build on: {', '.join(depends_on)}." if depends_on else "")
                             + (f" Waiting on you: {', '.join(waiting)}." if waiting else "")
                             + " Do not edit files another agent owns; if you need a change there, publish "
                               "blocker=<that agent's id>: <what you need>.")
            for sid, e in self.entries.items():
                if sid == step_id:
                    continue
                dep = sid in depends_on
                role = "" if not roster else (f"\n  owns: {', '.join(e.writes) or 'nothing (reads only)'}; publishes: "
                        f"{', '.join(e.promises) or '-'}" + (f"; builds on: {', '.join(e.depends_on)}" if e.depends_on else ""))
                head = (f"- {sid} [{e.status}{', ' + e.model.split('/')[-1] if e.model else ''}]"
                        f"{' (your step needs this)' if dep else ''} {e.goal[:200]}{role}")
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
