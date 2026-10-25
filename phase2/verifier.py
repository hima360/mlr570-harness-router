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


def steps_dir(step_id: str) -> str:
    """Each step's scratch folder in the task container: notes and outputs.txt. Outside /app, so
    Terminal-Bench's data-intact tests are unaffected; always writable, never in `writes`."""
    return f"/tmp/steps/{step_id}"


def parse_outputs(text: str) -> dict[str, str]:
    """name=value lines; blank lines, comments and malformed lines are skipped; later lines win."""
    import re

    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
            out[name] = value.strip()
    return out


async def read_outputs(environment, step) -> tuple[dict[str, str], list[str]]:
    """The facts a step published, and which of its declared outputs are missing."""
    import shlex

    try:
        r = await environment.exec(f"cat {steps_dir(step.id)}/outputs.txt 2>/dev/null; true", timeout_sec=30)
        outputs = parse_outputs(r.stdout or "")
    except Exception:
        outputs = {}
    missing = [o.name for o in step.outputs if o.kind == "fact" and not outputs.get(o.name)]
    files = [o for o in step.outputs if o.kind == "file"]
    if files:
        probe = "; ".join(f"test -e {shlex.quote(o.path)} || echo {shlex.quote(o.name)}" for o in files)
        try:
            r = await environment.exec(probe + "; true", timeout_sec=30)
            absent = set((r.stdout or "").split())
        except Exception:
            absent = {o.name for o in files}
        missing += [o.name for o in files if o.name in absent]
    return outputs, missing
