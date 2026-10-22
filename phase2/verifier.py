"""Verifier: run a step's acceptance check in the container."""
from dataclasses import dataclass


@dataclass
class Verdict:
    passed: bool
    output: str


async def verify(environment, step, worker_exit_status: str, timeout: int) -> Verdict:
    """No check -> trust the worker only if it submitted. Otherwise the check must exit 0."""
    if not step.check.strip():
        ok = worker_exit_status == "Submitted"
        return Verdict(ok, "no check; worker " + worker_exit_status)
    try:
        r = await environment.exec(step.check, timeout_sec=timeout)
    except Exception as e:
        return Verdict(False, f"check errored: {e}")
    out = ((r.stdout or "") + (r.stderr or ""))[-2000:]
    return Verdict(r.return_code == 0, out)
