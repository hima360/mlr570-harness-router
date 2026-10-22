"""Append-only JSONL trace of planning, routing, worker and verifier events for one trial."""
import json
import threading
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path | None):
        self.path = path
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._t0 = time.time()

    def log(self, event: str, **data) -> dict:
        row = {"t": round(time.time() - self._t0, 3), "event": event, **data}
        with self._lock:
            self.events.append(row)
            if self.path:
                with self.path.open("a") as f:
                    f.write(json.dumps(row, default=str) + "\n")
        return row
