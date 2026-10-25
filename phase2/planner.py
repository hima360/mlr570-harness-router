"""Planner: one frontier-model call turns the task into a validated DAG of steps."""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

PLANNER_PROMPT = """You are planning work for a team of coding agents that share one Linux machine.
Split the task into a small number of steps (at most {max_steps}). Use one step if the task is small.

For each step give:
- id: short unique id like "s1"
- goal: what this step must achieve, self-contained, specific enough to act on alone
- depends_on: ids of steps that must finish first
- writes: file paths or globs this step may create or modify ([] if it only reads)
- outputs: what later steps need from this step, each {{"name": "seed", "kind": "fact", "about": "DGA seed
  as 8 hex chars"}} or {{"name": "solver", "kind": "file", "path": "/app/solve.py", "about": "..."}}; [] if none
- check: one shell command that exits 0 only if the step succeeded

Before chaining steps, ask what each step needs to know. If another step finds it (a key, a seed, a
host, a decoded file), depend on that step and name it as one of that step's outputs. Steps that need
nothing from each other get no dependency, so they run at the same time.

Each step writes its facts as name=value lines to /tmp/steps/<id>/outputs.txt (that folder is always
writable; do not list it in writes). Every step needs a real check: the verifier runs it to decide
whether the step is done, and a failed check moves the step to a stronger model. A step that only
investigates declares what it finds as fact outputs, and its check greps outputs.txt for the concrete
value (for example grep -Eq '^seed=[0-9a-f]{{8}}$' /tmp/steps/s1/outputs.txt), not just that it exists.

Keep each step small enough for a modest model to finish in about 10 shell commands; split long
investigations into separate steps. Steps that write overlapping files must depend on each other.
Do not invent files you have not seen.

Task:
{instruction}

Current directory listing:
{env_summary}

Reply with only JSON: {{"steps": [{{"id": "...", "goal": "...", "depends_on": [], "writes": [], "outputs": [], "check": ""}}]}}"""

REPAIR_PROMPT = """Your plan was rejected: {error}
Reply with only the corrected JSON plan in the same format."""


class Output(BaseModel):
    """A result a step promises: a fact (name=value line in /tmp/steps/<id>/outputs.txt) or a file."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    kind: Literal["fact", "file"] = "fact"
    about: str = ""
    path: str = ""


class Step(BaseModel):
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    depends_on: list[str] = []
    writes: list[str] = []
    outputs: list[Output] = []
    check: str = ""
    # Set by the harness only (the short repair turn for missing outputs); not part of the plan.
    worker_step_limit: int | None = Field(default=None, exclude=True)


class Plan(BaseModel):
    steps: list[Step]


class PlanError(ValueError):
    pass


def parse_plan(text: str, max_steps: int) -> Plan:
    """Parse and validate planner output: JSON shape, unique ids, known deps, no cycles."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise PlanError("no JSON object found")
    try:
        plan = Plan.model_validate(json.loads(m.group(0)))
    except (json.JSONDecodeError, ValidationError) as e:
        raise PlanError(f"invalid JSON plan: {e}") from e
    if not 1 <= len(plan.steps) <= max_steps:
        raise PlanError(f"plan has {len(plan.steps)} steps; allowed 1..{max_steps}")
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate step ids")
    for s in plan.steps:
        unknown = set(s.depends_on) - set(ids)
        if unknown:
            raise PlanError(f"step {s.id} depends on unknown steps {sorted(unknown)}")
        names = [o.name for o in s.outputs]
        if len(set(names)) != len(names):
            raise PlanError(f"step {s.id} has duplicate output names")
        nopath = [o.name for o in s.outputs if o.kind == "file" and not o.path.strip()]
        if nopath:
            raise PlanError(f"step {s.id} file outputs need a path: {nopath}")
    topo_order(plan)
    return plan


def topo_order(plan: Plan) -> list[str]:
    deps = {s.id: set(s.depends_on) for s in plan.steps}
    order, done = [], set()
    while deps:
        ready = [i for i in deps if deps[i] <= done]
        if not ready:
            raise PlanError(f"dependency cycle among {sorted(deps)}")
        for i in sorted(ready, key=[s.id for s in plan.steps].index):
            order.append(i)
            done.add(i)
            del deps[i]
    return order


def single_step_plan(instruction: str) -> Plan:
    return Plan(steps=[Step(id="s1", goal=instruction, writes=["**"])])


def load_pinned_plan(path, max_steps: int) -> Plan:
    """The plan logged by an earlier run, so every routing policy runs the same DAG.
    `path` is a trace.jsonl, or a trial / agent directory that holds one."""
    from pathlib import Path

    p = Path(path)
    if p.is_dir():
        p = next((c for c in (p / "trace.jsonl", p / "agent" / "trace.jsonl") if c.is_file()), p / "trace.jsonl")
    try:
        lines = p.read_text().splitlines()
    except OSError as e:
        raise PlanError(f"cannot read pinned plan {p}: {e}") from e
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "plan":
            return parse_plan(json.dumps({"steps": event.get("steps", [])}), max_steps)
    raise PlanError(f"no plan event in {p}")


def make_plan(instruction: str, env_summary: str, ask, max_steps: int) -> tuple[Plan, dict]:
    """`ask(messages) -> str` is the model call. One repair attempt, then a single-step fallback."""
    messages = [{"role": "user", "content": PLANNER_PROMPT.format(
        max_steps=max_steps, instruction=instruction, env_summary=env_summary[:4000])}]
    info = {"attempts": 0, "fallback": False, "errors": []}
    for _ in range(2):
        info["attempts"] += 1
        reply = ask(messages)
        try:
            return parse_plan(reply, max_steps), info
        except PlanError as e:
            info["errors"].append(str(e))
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": REPAIR_PROMPT.format(error=e)}]
    info["fallback"] = True
    return single_step_plan(instruction), info
