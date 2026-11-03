"""Lead and helpers: the step's worker leads and hands sub-tasks to cheaper helper models while it works.

2026-10-28 evaluation: planning the whole task up front lost to one Sonnet agent (the planner guessed a design
before it understood the code, its checks were wrong, and nothing in these tasks could run in parallel). Here
the lead plans as it learns: it decides the approach, delegates reading, running and routine edits to helpers
(a whole command `delegate fast <<'TASK' ... TASK`), reads their reports and owns the final review. Its own
context grows by the reports, not by raw file dumps and logs, which is where a single agent's cost goes.

Model-agnostic: the lead is any tier, helpers are named in config (`helper_models`, cheapest first).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

DELEGATE_RE = re.compile(r"\A\s*delegate(?=\s|\Z)(?:\s+(?P<to>[A-Za-z_-]+))?\s*(?P<rest>.*)\Z", re.S)
HEREDOC_RE = re.compile(r"\A<<-?\s*(['\"]?)(?P<tag>\w+)\1[ \t]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*\Z", re.S)
REPORT_CHARS = 3000

LEAD_HINT = """

YOU LEAD THIS TASK, WITH HELPERS. You decide the approach and own the result; helpers do bulk work for you so the
task costs less. Your turns are the most expensive in the team, so spend them on understanding, decisions and
the final review, not on reading long files or logs.
{roster}
To delegate, make this your whole command:
delegate {first} <<'TASK'
<a self-contained sub-task: where to look, exactly what to do, what to report back>
TASK
Independent sub-tasks that touch different files run at the same time when you give a JSON list:
delegate <<'TASK'
[{{"to": "{first}", "task": "..."}}, {{"to": "{last}", "task": "..."}}]
TASK
Each helper's report comes back as the command output. Helpers see the task text and your sub-task, not your
reasoning: tell them everything they need. They can be wrong: check what you rely on (read the diff, rerun the
key command) before you submit. You can delegate {n} times in this step."""

HELPER_SUFFIX = """

You are a helper: do only this sub-task, then submit. Your submit message is your report to the lead, who relies
on it: say what you changed (file paths), what you found (exact values, failing test names and messages), and
anything you could not do. Do not change files the sub-task does not ask you to change."""


def parse_delegate(command: str, names) -> list[tuple[str, str]] | None:
    """The sub-tasks of a `delegate` command as (helper name, task), or None if it is not one.
    `delegate [name] <<'TAG' ... TAG` with a task text or a JSON list of {"to", "task"}; also
    `delegate [name] <task on one line>`. An unknown or missing name is "" (the router picks)."""
    m = DELEGATE_RE.match(command or "")
    if not m:
        return None
    to, rest = (m.group("to") or ""), m.group("rest").strip()
    if to and to not in names and not rest.startswith("<<"):
        rest, to = f"{to} {rest}".strip(), ""  # `delegate read the file ...`: no helper name given
    h = HEREDOC_RE.match(rest)
    body = h.group("body").strip() if h else rest.strip().strip("'\"")
    if not body:
        return None
    try:
        items = json.loads(body)
    except ValueError:
        items = None
    if isinstance(items, list) and items and all(isinstance(i, dict) and str(i.get("task", "")).strip() for i in items):
        picked = [str(i.get("to") or to) for i in items]
        return [(n if n in names else "", str(i["task"]).strip()) for n, i in zip(picked, items)]
    return [(to if to in names else "", body)]


def lead_hint(helpers: dict[str, str], max_delegations: int, pretty=lambda m: m) -> str:
    names = list(helpers)
    blurbs = ["cheapest; good for reading and summarizing files or logs, searching code, running tests and "
              "reporting failures with their messages, and edits you specify exactly",
              "mid-priced and capable; good for implementing a change you have specified, debugging a failing "
              "test, writing tests"]
    roster = "\n".join(f"- {n} = {pretty(m)}: {blurbs[0] if i == 0 else blurbs[-1]}"
                       for i, (n, m) in enumerate(helpers.items()))
    return LEAD_HINT.format(roster=roster, first=names[0], last=names[-1], n=max_delegations)


def helper_report(hid: str, name: str, model: str, result, outputs: dict, digest: str) -> str:
    facts = "".join(f"\n  {k}={v[:200]}" for k, v in list(outputs.items())[:10])
    text = (f"HELPER {hid} ({model}, {name}) ended: {result.exit_status} after {result.n_calls} model calls.\n"
            f"Its report: {result.summary.strip() or '(none)'}" + (f"\nIts published results:{facts}" if facts else "")
            + (f"\nIts last commands:\n{digest}" if digest and result.exit_status != "Submitted" else ""))
    return text[:REPORT_CHARS]


class DelegatingEnv:
    """Wraps the worker's environment: a `delegate` command runs helpers instead of a shell command, and the
    helpers' reports are its output. Everything else goes to the inner environment."""

    def __init__(self, inner, names, run_helpers, max_delegations: int, on_return=None):
        self.inner, self.names, self.run_helpers = inner, list(names), run_helpers
        self.left, self.on_return = max_delegations, on_return

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        tasks = parse_delegate(action.get("command", ""), self.names)
        if tasks is None:
            return self.inner.execute(action, cwd, timeout=timeout)
        if self.left <= 0:
            return {"output": "No delegations left in this step: do the rest yourself.", "returncode": 1,
                    "exception_info": ""}
        tasks = tasks[: self.left]
        self.left -= len(tasks)
        try:
            reports = self.run_helpers(tasks)
        finally:
            if self.on_return:
                self.on_return()  # the helpers' time is not the lead being idle
        return {"output": "\n\n".join(reports) + f"\n\n({self.left} delegations left)", "returncode": 0,
                "exception_info": ""}

    def __getattr__(self, name):
        return getattr(self.inner, name)


def make_helper_runner(cfg, ledger, env_adapter, trace, lead_step, lead_model: str, task_overall: str, run_one,
                       route=None):
    """`run_one(model_id, step, team_text) -> (WorkerResult, outputs, digest)` runs one helper. Returns the
    function DelegatingEnv calls with [(name, task), ...]: helpers run at the same time (up to
    cfg.max_parallel_helpers) and their reports come back in order."""
    from .planner import Step

    names, count = list(cfg.helper_models), [0]

    def pick(name: str, task: str) -> str:
        if name in cfg.helper_models:
            return name
        if route is not None:  # no helper named: the router picks among the helpers, cheapest first
            try:
                return names[max(0, min(route(task), len(names) - 1))]
            except Exception:
                pass
        return names[-1]

    def run(tasks):
        batch = []
        for name, task in tasks:
            count[0] += 1
            name = pick(name, task)
            batch.append((f"{lead_step.id}.h{count[0]}", name, cfg.helper_models[name], task))
        for hid, name, model, task in batch:
            trace.log("delegate", step=lead_step.id, helper=hid, model=model, to=name, task=task[:600],
                      parallel=len(batch))

        def one(item):
            hid, name, model, task = item
            others = [f"{h} ({m}): {t[:100]}" for h, _, m, t in batch if h != hid]
            team = (f"TEAM: you are helper {hid} ({model}). The lead ({lead_model}) gave you this sub-task of its "
                    f"step and will check your report." + (" Helpers working at the same time (different "
                                                            "files): " + "; ".join(others) if others else ""))
            step = Step(id=hid, goal=task + HELPER_SUFFIX, writes=["**"])
            step.worker_step_limit = cfg.helper_step_limit
            try:
                result, outputs, digest = run_one(model, step, team)
            except Exception as e:  # a helper that crashes is reported, not fatal to the lead
                from .worker import WorkerResult

                result, outputs, digest = WorkerResult(type(e).__name__, str(e)[:300], 0), {}, ""
            trace.log("helper_end", step=lead_step.id, helper=hid, model=model, exit_status=result.exit_status,
                      n_calls=result.n_calls, summary=result.summary[:400])
            return helper_report(hid, name, model, result, outputs, digest)

        with ThreadPoolExecutor(max_workers=max(1, cfg.max_parallel_helpers)) as pool:
            return list(pool.map(one, batch))

    return run
