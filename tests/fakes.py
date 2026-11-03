"""Test doubles: an in-memory 'container' and scripted models. No network, no Docker."""
import asyncio
from dataclasses import dataclass


@dataclass
class FakeExecResult:
    stdout: str | None
    stderr: str | None
    return_code: int


class FakeEnvironment:
    """Mimics Harbor's BaseEnvironment.exec. `handler(command) -> (stdout, rc)` decides output.
    Tracks how many commands run at once so tests can check the scheduler's parallelism."""

    def __init__(self, handler=None, delay: float = 0.0):
        self.handler = handler or (lambda cmd: ("", 0))
        self.delay = delay
        self.commands: list[str] = []
        self.active = 0
        self.max_active = 0

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append(command)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            out, rc = self.handler(command)
            return FakeExecResult(out, "", rc)
        finally:
            self.active -= 1


class FakeScorer:
    """Stands in for Laya (returns (score, confidence)) or Arch-Router (returns score)."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        return self.value
