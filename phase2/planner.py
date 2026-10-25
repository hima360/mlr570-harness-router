"""Planner: one frontier-model call turns the task into a validated DAG of steps."""
import json
import re

from pydantic import BaseModel, Field, ValidationError

PLANNER_PROMPT = """You are planning work for a team of coding agents that share one Linux machine.
Split the task into a small number of steps (at most {max_steps}). Use one step if the task is small.

For each step give:
- id: short unique id like "s1"
- goal: what this step must achieve, self-contained, specific enough to act on alone
- depends_on: ids of steps that must finish first
- writes: file paths or globs this step may create or modify ([] if it only reads)
- check: one shell command that exits 0 only if the step succeeded

Every step needs a real check: the verifier runs it to decide whether the step is done, and a failed
check moves the step to a stronger model. A step that only investigates must write its findings to
/tmp/steps/<id>.txt (list that path in writes), and its check confirms the file holds the concrete
result (for example a grep for the value found), not just that the file exists.

Keep each step small enough for a modest model to finish in about 10 shell commands; split long
investigations into separate steps. Steps that write overlapping files must depend on each other.
Do not invent files you have not seen.

Task:
{instruction}

Current directory listing:
{env_summary}

Reply with only JSON: {{"steps": [{{"id": "...", "goal": "...", "depends_on": [], "writes": [], "check": ""}}]}}"""

REPAIR_PROMPT = """Your plan was rejected: {error}
Reply with only the corrected JSON plan in the same format."""


class Step(BaseModel):
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    depends_on: list[str] = []
    writes: list[str] = []
    check: str = ""


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
