"""Planner: one frontier-model call turns the task into a validated DAG of steps."""
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

PLANNER_PROMPT = """You are planning work for a team of coding agents that share one Linux machine.
Split the task into a small number of steps (at most {max_steps}). Use one step if the task is small.

Think in two passes. First list the task's independent WORKSTREAMS: parts that touch different files,
components, inputs or questions and can be done without each other's results (separate modules or bugs,
separate analyses of different inputs, an implementation and its tests against an agreed interface).
Each workstream becomes its own step(s) with no dependency on the other workstreams, so they run at the
same time; when two parallel steps build pieces that must fit together, state the shared interface
(function names, file formats, paths) in both goals. A final step depends on them only if it consumes
their results. Splitting is worth it only when steps can run at the same time, or when a clearly easy
part (writing up or formatting results already found) can go to a cheaper model: then mark that later
step "continues": "<id of the step whose work it carries on>", and it keeps that step's full working
memory instead of starting fresh. If there is no parallel work and no easy part, use ONE step.

For each step give:
- id: short unique id like "s1"
- goal: what this step must achieve, self-contained, specific enough to act on alone
- depends_on: ids of steps that must finish first
- writes: file paths or globs this step may create or modify ([] if it only reads)
- outputs: what later steps need from this step, each {{"name": "seed", "kind": "fact", "about": "DGA seed
  as 8 hex chars"}} or {{"name": "solver", "kind": "file", "path": "/app/solve.py", "about": "..."}}; [] if none
- check: one shell command that exits 0 only if the step succeeded
- effort: "low" only for steps that just assemble or write results other steps already found; omit otherwise
- continues: optional id of one step in depends_on whose line of work this step carries on (it then
  continues that step's working memory); omit otherwise

Before chaining steps, ask what each step needs to know. If another step finds it (a key, a seed, a
host, a decoded file), depend on that step and name it as one of that step's outputs. Steps that need
nothing from each other get no dependency, so they run at the same time.

Each step writes its facts as name=value lines to /tmp/steps/<id>/outputs.txt (that folder is always
writable; do not list it in writes). Every step needs a real check: the verifier runs it to decide
whether the step is done, and a failed check moves the step to a stronger model. A step that only
investigates declares what it finds as fact outputs, and its check greps outputs.txt for the concrete
value (for example grep -Eq '^seed=[0-9a-f]{{8}}$' /tmp/steps/s1/outputs.txt), not just that it exists.
A check must cover the scenarios the task names (failure and restart cases, edge cases, limits under load),
not only the normal path. A check must fail for a plausible WRONG answer, not only a malformed one: recompute or cross-check the
value against the source data (for example regenerate the observed domains from the seed, or decrypt a
known sample with the key). A format-only check lets a wrong value flow into every later step.
If the steps would only form a chain with no parallel work, prefer fewer, larger steps (2 or 3).
A check runs in the task container with only what is installed there: use shell tools, the Python
standard library, or the same interpreter and packages the task itself uses (for example its virtualenv);
never import a package the task does not already provide (2026-10-27: a check that imported pandas with the
system python3 failed although the step's work was right).

Keep each step small enough for a modest model to finish in about 10 shell commands; split long
investigations into separate steps. Steps that write overlapping files must depend on each other.
Do not invent files you have not seen.

Task:
{instruction}

Current directory listing:
{env_summary}

Reply with only JSON: {{"workstreams": ["..."], "steps": [{{"id": "...", "goal": "...", "depends_on": [], "writes": [],
"outputs": [], "check": ""}}]}}"""

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
    effort: str = ""  # "low": the worker runs at low reasoning effort (assembling known results)
    continues: str = ""  # a step in depends_on whose CLI session this step forks (keeps its working memory)
    # Set by the harness only (repair turns, merged chains); not part of the plan.
    worker_step_limit: int | None = Field(default=None, exclude=True)
    wall_time_sec: int | None = Field(default=None, exclude=True)
    fork_from: dict | None = Field(default=None, exclude=True)  # the continued step's session (set by the harness)
    team_feed: Any = Field(default=None, exclude=True)  # () -> "TEAM UPDATE: ..." since the last call


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
        if s.continues and s.continues not in s.depends_on:
            raise PlanError(f"step {s.id} continues {s.continues!r}, which is not in its depends_on")
        names = [o.name for o in s.outputs]
        if len(set(names)) != len(names):
            raise PlanError(f"step {s.id} has duplicate output names")
        nopath = [o.name for o in s.outputs if o.kind == "file" and not o.path.strip()]
        if nopath:
            raise PlanError(f"step {s.id} file outputs need a path: {nopath}")
    topo_order(plan)
    for s in plan.steps:  # Droid: "exit 0 means finished, not succeeded"; never trust the worker's word
        if not s.check.strip():
            raise PlanError(f"step {s.id} has no check: give it a shell command that fails on a wrong result")
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


def plan_shape(plan: Plan) -> dict:
    """How parallel the plan is, and dependencies whose goal and check never mention what they would
    consume (a soft lint: logged, never rejected, since a missing edge costs more than a fake one)."""
    from collections import Counter

    byid = {s.id: s for s in plan.steps}
    depth = {}
    for sid in topo_order(plan):
        depth[sid] = 1 + max((depth[d] for d in byid[sid].depends_on), default=0)
    unjustified = []
    for s in plan.steps:
        text = f"{s.goal} {s.check}"
        for d in s.depends_on:
            names = [o.name for o in byid[d].outputs] + [o.path for o in byid[d].outputs if o.path]
            if f"/tmp/steps/{d}" not in text and not any(n in text for n in names):
                unjustified.append(f"{d}->{s.id}")
    return {"parallelism": round(len(plan.steps) / max(depth.values()), 2),
            "widest_layer": max(Counter(depth.values()).values()), "unjustified_edges": unjustified}


def merge_chain(plan: Plan, step_limit: int, wall_time_sec: int, max_steps: int = 100) -> Plan:
    """A plan with no parallel work becomes ONE step for one agent: the planned steps become its checklist
    and all their checks must pass. 2026-10-26: one Sonnet agent passed shadow-relay 8/8 in 6 min for $0.45;
    the same work split into a 4-step chain scored 5/8 at best ($0.62, 14 min): every handoff lost context
    and no step could run in parallel. Each part still writes its facts to its own /tmp/steps/<id>/ folder,
    so the planned checks keep working."""
    parts = [next(s for s in plan.steps if s.id == i) for i in topo_order(plan)]
    lines = []
    for n, s in enumerate(parts, 1):
        facts = ", ".join(o.name for o in s.outputs if o.kind == "fact")
        files = ", ".join(o.path for o in s.outputs if o.kind == "file")
        lines.append(f"{n}. [{s.id}] {s.goal}" + (f"\n   Publish as name=value in /tmp/steps/{s.id}/outputs.txt: {facts}"
                                                   if facts else "") + (f"\n   Create: {files}" if files else ""))
    writes = sorted({w for s in parts for w in s.writes})
    merged = Step(id="all", goal="Do the whole task yourself, part by part, in this order:\n" + "\n".join(lines),
                  writes=["**"] if "**" in writes else writes,
                  check=" && ".join(f"( {s.check} )" for s in parts if s.check.strip()),
                  effort="" if any(s.effort != "low" for s in parts) else "low")
    merged.worker_step_limit = min(step_limit * len(parts), max_steps)
    merged.wall_time_sec = wall_time_sec * len(parts)
    return Plan(steps=[merged])


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
        try:
            reply = ask(messages)
        except Exception as e:  # a refused or failed planner call (2026-10-26: Claude's cyber classifier
            # refused Opus on legacy-utility-triage and the whole trial crashed): run the task as one step
            info["errors"].append(f"planner call failed: {type(e).__name__}: {str(e)[-300:]}")
            break
        try:
            return parse_plan(reply, max_steps), info
        except PlanError as e:
            info["errors"].append(str(e))
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": REPAIR_PROMPT.format(error=e)}]
    info["fallback"] = True
    return single_step_plan(instruction), info
