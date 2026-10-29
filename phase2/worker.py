"""Worker: mini-swe-agent runs one step inside the Harbor task container.

mini-swe-agent is synchronous, Harbor's environment is async: the worker runs in a thread and
each shell command is sent back to the event loop with run_coroutine_threadsafe.
"""
import asyncio
import os
import re
from dataclasses import dataclass

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
# mini-swe-agent retries a failed model call 10 times with 4-60 s waits (models/utils/retry.py); a CLI
# error after a model turn ("prompt too long", usage limit) does not get better with retries (Codex never
# retries those). 3 keeps one real second chance for transient errors.
os.environ.setdefault("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "3")

SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

INSTANCE_TEMPLATE = """You are one agent in a team working on this overall task:

{{task_overall}}

Your step ({{step_id}}): {{step_goal}}

Other steps and what they reported:
{{blackboard}}

Do only your step. Other agents may work on other files at the same time, so only edit files
your step needs{{writes_hint}}.{{outputs_hint}}

If a command fails, try a different approach; never end your step without either submitting or publishing a
blocker. The task's own tests run later, possibly as a non-root user in a fresh shell: make files you create
readable, do not rely on your shell's state or environment variables, and leave services, background jobs and
measurements in the state the task asks for (let a measurement finish before you submit). Every response must contain exactly one bash command. Each command runs in a fresh shell in the
task container, so prefix with `cd /path && ...` when needed.{{format_hint}}
When your step is done, run exactly:
printf '""" + SUBMIT + """\\n<one or two sentences on what you did and which files changed>\\n'
"""


def outputs_hint(step) -> str:
    """How to publish results: name=value lines in the step's outputs.txt, via a quoted heredoc (no
    printf quoting or % pitfalls), plus the facts and files the plan promised."""
    from .verifier import steps_dir

    d = steps_dir(step.id)
    facts = [o for o in step.outputs if o.kind == "fact"]
    files = [o for o in step.outputs if o.kind == "file"]
    text = (f"\n\nYour folder {d}/ is always writable: keep notes there. Publish results for the team as "
            f"name=value lines in {d}/outputs.txt, written with a quoted heredoc, for example:\n"
            f"cat >> {d}/outputs.txt <<'EOF'\nname=value\nEOF\n")
    if facts:
        text += "Your step must publish these facts:\n" + "".join(
            f"- {o.name}: {o.about or 'see your step'}\n" for o in facts)
    else:
        text += "Publish any fact a later step may need (a key, a value, a path you found).\n"
    text += ("If you cannot finish because an earlier step's result looks wrong, publish "
             "blocker=<that step's id>: <what is wrong and why> and submit, instead of working around it.\n")
    if files:
        text += "Your step must create these files:\n" + "".join(
            f"- {o.path}: {o.about or o.name}\n" for o in files)
    return text.rstrip("\n")


class SubmitGate:
    """Terminus-2 confirms completion twice; Claude Code's Stop hooks and Droid run the check when the agent
    tries to stop. Here: on submit, run the step's check (and look for its promised outputs); if it fails, the
    worker gets the check's output as its next observation and keeps its live context, instead of the
    attempt ending and a fresh, pricier attempt starting. The check command itself is not shown.
    At most `max_blocks` per attempt; each one costs a step of the worker's step limit."""

    def __init__(self, inner, step, check_timeout: int, max_blocks: int = 2, reviewer=None):
        self.inner, self.step, self.check_timeout, self.max_blocks = inner, step, check_timeout, max_blocks
        self.reviewer = reviewer  # (step, outputs) -> problem text or "": checks values against evidence
        self.senior = None  # (step, report) -> problem text or "": a stronger model reviews the work (phase2.review)
        self.senior_left = 0
        self.blocks = 0

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        from minisweagent.exceptions import Submitted

        try:
            return self.inner.execute(action, cwd, timeout=timeout)
        except Submitted as done:
            problem = ""
            if self.blocks < self.max_blocks and hasattr(self.inner, "gate"):
                problem = self.inner.gate(self.step, self.check_timeout)
                if not problem and self.reviewer:
                    problem = self.reviewer(self.step)
                if problem:
                    self.blocks += 1
                    left = self.max_blocks - self.blocks
                    return {"output": f"NOT DONE YET: {problem}\nFix it, then submit again"
                                      f"{f' ({left} more check before the step ends)' if left else ' (last check)'}.",
                            "returncode": 1, "exception_info": ""}
            # The senior review has its own rounds: other send-backs must not use them up (2026-10-29: a value
            # reviewer sent payments back three times on note-keeping and the senior review never ran).
            if self.senior and self.senior_left > 0:
                self.senior_left -= 1
                report = ((done.args[0] if done.args else {}) or {}).get("content", "")
                problem = self.senior(self.step, report)
                if problem:
                    return {"output": f"NOT DONE YET: {problem}\nFix them, then submit again.", "returncode": 1,
                            "exception_info": ""}
            raise

    def __getattr__(self, name):
        return getattr(self.inner, name)


@dataclass
class WorkerResult:
    exit_status: str  # Submitted | LimitsExceeded | error type
    summary: str
    n_calls: int
    session: dict | None = None  # the CLI session the attempt ended in, for a step that continues it


LINE_CAP = 1000  # oh-my-pi cuts lines at 768 bytes: one minified line must not fill the whole budget


def clip_output(text: str, limit: int) -> str:
    if any(len(line) > LINE_CAP for line in text.splitlines()):
        half = LINE_CAP // 2  # keep both ends of a long line, as the whole-output clip does
        text = "\n".join(line if len(line) <= LINE_CAP else
                         f"{line[:half]} ... [{len(line) - 2 * half} chars of this line elided] ... {line[-half:]}"
                         for line in text.splitlines())
    if limit <= 0 or len(text) <= limit:
        return text
    half = limit // 2
    return (f"{text[:half]}\n\n[... {len(text) - 2 * half} characters elided; print a narrower slice "
            f"(head, tail, grep, sed -n) to see more ...]\n\n{text[-half:]}")


SLOW_COMMAND_SEC = 15
MAX_COMMAND_SEC = 900  # ceiling for a "# timeout: N" request
# Pagers, editors and progress bars wait for a terminal and hang until the command timeout
# (mini-swe-agent's text config and oh-my-pi's bash tool set the same). CI=true is left out: it changes tests.
NONINTERACTIVE_ENV = {"PAGER": "cat", "GIT_PAGER": "cat", "MANPAGER": "cat", "EDITOR": "true", "VISUAL": "true",
                      "GIT_TERMINAL_PROMPT": "0", "TERM": "dumb", "NO_COLOR": "1", "TQDM_DISABLE": "1",
                      "PIP_PROGRESS_BAR": "off", "DEBIAN_FRONTEND": "noninteractive"}


INSTALL = re.compile(r"\b(apt-get|apt|dpkg|pip3?|python3? -m pip|uv pip|npm|yarn|pnpm|conda|cargo)\b[^\n|;&]*"
                     r"\b(install|add|i)\b")
INSTALL_LOCK = "/tmp/steps/.install.lock"


def bounded(command: str, seconds: int) -> str:
    """Run under coreutils `timeout` inside the container, a little before Harbor's own limit: Harbor
    discards all output when its limit fires (docker.py), `timeout` returns 124 with the partial output."""
    import shlex

    if INSTALL.search(command):  # parallel steps share one container: installs take turns (dpkg / pip locks)
        command = (f"mkdir -p /tmp/steps; exec 9>{INSTALL_LOCK}; command -v flock >/dev/null 2>&1 && flock 9; "
                   + command)
    inner = f"bash -c {shlex.quote(command)}"
    return (f"if command -v timeout >/dev/null 2>&1; then timeout -k 5 {max(seconds - 10, 5)} {inner}; "
            f"else {inner}; fi")


class HarborEnvAdapter:
    """mini-swe-agent Environment protocol on top of a Harbor BaseEnvironment."""

    def __init__(self, environment, loop: asyncio.AbstractEventLoop, timeout: int, max_output_chars: int = 10000):
        self.environment = environment
        self.loop = loop
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.config = {"timeout": timeout}

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        from minisweagent.exceptions import Submitted

        import time

        import re

        command = action.get("command", "")
        limit = timeout or self.timeout
        asked = re.match(r"\s*#\s*timeout:\s*(\d+)", command)  # Droid: the model can ask for a longer wait
        if asked:
            limit = max(10, min(int(asked.group(1)), MAX_COMMAND_SEC))
        t0 = time.monotonic()
        fut = asyncio.run_coroutine_threadsafe(
            self.environment.exec(bounded(command, limit), cwd=cwd or None, timeout_sec=limit,
                                  env=NONINTERACTIVE_ENV), self.loop)
        try:
            r = fut.result()
            text = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            out = {"output": text, "returncode": r.return_code, "exception_info": ""}
            if r.return_code == 124:
                out["output"] += (f"\n[timed out after about {limit - 10} s; output above is partial. Put "
                                  "'# timeout: N' as the first line to wait longer, or run it in the background]")
        except Exception as e:  # timeouts and transport errors are shown to the model
            out = {"output": "", "returncode": -1,
                   "exception_info": f"An error occurred while executing the command: {e}. If it was a "
                                     "timeout, run long jobs in the background (nohup ... > log 2>&1 &) and poll "
                                     "the log, work on a smaller slice, or put '# timeout: N' as the first line."}
        lines = out["output"].lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == SUBMIT and out["returncode"] == 0:
            summary = "".join(lines[1:]).strip()
            raise Submitted({"role": "exit", "content": summary,
                             "extra": {"exit_status": "Submitted", "submission": summary}})
        out["output"] = clip_output(out["output"], self.max_output_chars)
        took = time.monotonic() - t0
        if took >= SLOW_COMMAND_SEC:  # Droid: runtimes shown only above a threshold, so slow commands stand out
            out["output"] += f"\n[command took {took:.0f} s]"
        return out

    def gate(self, step, check_timeout: int) -> str:
        """What still keeps a submitted step from passing ("" = nothing): missing promised outputs, or
        its check failing. Runs in the worker thread; the check itself runs on the event loop."""
        return asyncio.run_coroutine_threadsafe(self._gate(step, check_timeout), self.loop).result()

    async def _gate(self, step, check_timeout: int) -> str:
        from .verifier import read_outputs, steps_dir, verify

        _, missing = await read_outputs(self.environment, step)
        if missing:
            return f"these promised results are not in {steps_dir(step.id)}/outputs.txt yet: {', '.join(missing)}"
        if step.check.strip():
            v = await verify(self.environment, step, "Submitted", check_timeout)
            if not v.passed:
                return "the step's check failed. Its output:\n" + (v.output.strip()[-800:] or "(no output)")
        return ""

    def get_template_vars(self, **kwargs) -> dict:
        return {"system": "Linux", "release": "", "version": "", "machine": "", **kwargs}

    def serialize(self) -> dict:
        return {"info": {"config": {"environment_type": "phase2.worker.HarborEnvAdapter"}}}


TEXT_FORMAT_HINT = """
Write a short THOUGHT, then put the command in a block exactly like this:

```mswea_bash_command
your_command_here
```"""


def _make_model(cfg, ledger, model_id: str, on_call=None, effort: str | None = None, reminder: str = "",
                step_limit: int = 0, fork_from: dict | None = None, team_feed=None):
    from .ledger import budget_exceeded

    import time

    from minisweagent.models.litellm_model import LitellmModel

    from .cli_models import IdleGuard, StepIdle, complete_fn_for, make_text_model
    from .llm import litellm_name, record_call

    def on_result(usage, text, ms):
        info = record_call(ledger, model_id, usage, ms, text)
        if on_call:
            on_call(info)

    if complete_fn_for(model_id) is not None:
        complete = complete_fn_for(model_id, cfg)
        if effort:  # the plan marked this step as light work
            import functools

            complete = functools.partial(complete_fn_for(model_id), effort=effort, timeout=cfg.cli_timeout_for(model_id))
        return make_text_model(model_id, on_result, complete_fn=complete, idle_sec=cfg.step_idle_sec,
                               context_budget_chars=cfg.context_budget_chars, reminder=reminder,
                               step_limit=step_limit, session_budget_chars=cfg.session_budget_chars,
                               fork_from=fork_from, team_feed=team_feed, output_guard=cfg.output_guard,
                               budget_fn=(lambda: budget_exceeded(cfg, ledger)) if ledger is not None else None)
    guard = IdleGuard(cfg.step_idle_sec)

    class LedgerModel(LitellmModel):
        abort_exceptions = [*LitellmModel.abort_exceptions, StepIdle]

        def _query(self, messages, **kwargs):
            guard.check()
            response = super()._query(messages, **kwargs)
            guard.progress()
            return response

        def query(self, messages, **kwargs):
            t0 = time.perf_counter()
            message = super().query(messages, **kwargs)
            usage = (message.get("extra", {}).get("response") or {}).get("usage")
            on_result(usage, message.get("content"), (time.perf_counter() - t0) * 1e3)
            return message

    m = LedgerModel(model_name=litellm_name(model_id), cost_tracking="ignore_errors",
                    model_kwargs={"api_base": cfg.base_url, "api_key": cfg.api_key})
    m.idle_guard = guard
    return m


def run_worker(cfg, ledger, model_id: str, env_adapter, *, task_overall: str, step, blackboard_text: str,
               model=None, output_path=None, on_call=None, run_helpers=None, senior=None) -> WorkerResult:
    """Blocking: call inside asyncio.to_thread. `model` is injectable for tests; `on_call(info)` gets
    one record per model call. With `run_helpers` (phase2.delegate) this worker leads: its `delegate`
    commands run helper models and their reports are the command output."""
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    from .cli_models import complete_fn_for

    from .verifier import steps_dir

    reminder = "" if not cfg.recency_reminder else (f"Your step ({step.id}): {' '.join(step.goal.split())[:300]} Reply with a short THOUGHT and exactly "
                f"one command block. Publish results in {steps_dir(step.id)}/outputs.txt; submit when done.")
    model = model or _make_model(cfg, ledger, model_id, on_call, effort="low" if step.effort == "low" else None,
                                 reminder=reminder, step_limit=step.worker_step_limit or cfg.worker_step_limit,
                                 fork_from=step.fork_from, team_feed=step.team_feed)
    writes_hint = f" (planned: {', '.join(step.writes)})" if step.writes else ""
    text_based = isinstance(model, LitellmTextbasedModel) or complete_fn_for(model_id) is not None
    format_hint = TEXT_FORMAT_HINT if text_based else ""
    env = env_adapter
    if run_helpers is not None:
        from .delegate import DelegatingEnv, lead_hint

        guard = getattr(model, "idle_guard", None)
        env = DelegatingEnv(env_adapter, cfg.helper_models, run_helpers, cfg.max_delegations,
                            on_return=guard.progress if guard else None)
        step = step.model_copy(update={"goal": step.goal + lead_hint(cfg.helper_models, cfg.max_delegations)})
    gate = SubmitGate(env, step, cfg.check_timeout, cfg.submit_checks)
    agent = DefaultAgent(
        model, gate,
        system_template="You are a careful software engineer operating a Linux shell.",
        instance_template=INSTANCE_TEMPLATE,
        step_limit=step.worker_step_limit or cfg.worker_step_limit, cost_limit=0, output_path=output_path,
        wall_time_limit_seconds=step.wall_time_sec or cfg.step_wall_time_sec,
    )
    if cfg.reviewer_model and model_id != cfg.reviewer_model:
        gate.reviewer = lambda s: review_values(cfg, ledger, s, env_adapter, agent.messages, on_call)
    if senior is not None and model_id != cfg.senior_reviewer_model:  # `senior(step, report, messages)`
        gate.senior, gate.senior_left = (lambda s, report: senior(s, report, agent.messages)), cfg.senior_review_rounds
    try:
        info = agent.run(task_overall=task_overall, step_id=step.id, step_goal=step.goal,
                         blackboard=blackboard_text, writes_hint=writes_hint,
                         outputs_hint=outputs_hint(step), format_hint=format_hint) or {}
    except Exception as e:
        return WorkerResult(type(e).__name__, str(e)[:500], agent.n_calls, _session_info(model, model_id))
    return WorkerResult(info.get("exit_status", "unknown"), (info.get("submission") or "").strip(),
                        agent.n_calls, _session_info(model, model_id))


def _session_info(model, model_id: str) -> dict | None:
    from .cli_models import backend_of

    s = getattr(model, "cli_session", None)
    if s is None or not s.id:
        return None
    return {"id": s.id, "backend": backend_of(model_id), "dir": s.dir, "chars": s.chars,
            "cost_total": s.totals.get("cost", 0.0)}


def attempt_digest(messages: list[dict], max_chars: int = 5000) -> str:
    """What a failed attempt found, for the next attempt: each turn's thought, command and the start of
    its output, newest turns kept first when it does not all fit."""
    import re

    turns = []
    for i, m in enumerate(messages):
        if i < 2 or m.get("role") != "assistant":
            continue
        text = str(m.get("content") or "")
        cmd = re.search(r"```mswea_bash_command\s*(.*?)```", text, re.S)
        thought = " ".join((text[: cmd.start()] if cmd else text).replace("THOUGHT:", "").split())[:400]
        nxt = messages[i + 1] if i + 1 < len(messages) else {}
        out = " ".join(str(nxt.get("content") or "").split())[:400] if nxt.get("role") != "assistant" else ""
        part = (f"- thought: {thought}\n" if thought else "") + \
               (f"  ran: {' '.join(cmd.group(1).split())[:300]}\n" if cmd else "") + \
               (f"  got: {out}\n" if out else "")
        if part:
            turns.append(part)
    kept, size = [], 0
    for part in reversed(turns):
        if size + len(part) > max_chars:
            kept.append("- (earlier turns omitted)\n")
            break
        kept.append(part)
        size += len(part)
    return "".join(reversed(kept))


PROGRESS_CHARS = 4000


def compact_messages(messages: list[dict], budget_chars: int) -> list[dict]:
    """The view a CLI model gets once the conversation outgrows `budget_chars`: system and first prompt
    pinned, older turns folded into one PROGRESS SO FAR block (command and start of its output), the
    newest turns verbatim. mini-swe-agent's own history (saved for the trace and the handoff) is untouched."""
    import re

    def size(m):
        return len(str(m.get("content") or ""))

    if budget_chars <= 0 or len(messages) <= 4 or sum(map(size, messages)) <= budget_chars:
        return messages
    head, rest = messages[:2], messages[2:]
    keep, room = 0, budget_chars - sum(map(size, head)) - PROGRESS_CHARS
    for m in reversed(rest):
        if keep and size(m) > room:
            break
        room -= size(m)
        keep += 1
    keep = min(max(keep, 2), len(rest))
    if keep < len(rest) and rest[-keep].get("role") != "assistant":
        keep += 1  # the verbatim part starts with a model turn
    if keep >= len(rest):
        return messages
    old, lines = rest[:-keep], []
    for i, m in enumerate(old):
        if m.get("role") != "assistant":
            continue
        text = str(m.get("content") or "")
        cmd = re.search(r"```mswea_bash_command\s*(.*?)```", text, re.S)
        nxt = old[i + 1] if i + 1 < len(old) else {}
        out = " ".join(str(nxt.get("content") or "").split())[:160] if nxt.get("role") != "assistant" else ""
        lines.append(f"- ran: {' '.join((cmd.group(1) if cmd else text).split())[:200]}\n  got: {out}")
    kept, total = [], 0
    for line in reversed(lines):
        if total + len(line) > PROGRESS_CHARS - 200:
            kept.append("- (earlier turns omitted)")
            break
        kept.append(line)
        total += len(line) + 1
    progress = ("\n\nPROGRESS SO FAR (your older turns, folded to save space; the files you wrote and "
                "/tmp/steps/ are unchanged):\n" + "\n".join(reversed(kept)))
    return [head[0], {**head[1], "content": str(head[1].get("content") or "") + progress}, *rest[-keep:]]


REVIEW_PROMPT = """You review a coding agent's claimed results. You do not run anything. For each published
value below, decide whether the command outputs in the transcript SHOW it (it was printed, computed or read
from the data), or whether it was guessed, assumed, copied from an example, or only has the right format.

Step goal: {goal}

Published values:
{facts}

Transcript (commands and the start of their outputs, newest last):
{digest}

Reply with only JSON: {{"unsupported": [{{"name": "<value name>", "why": "<what evidence is missing>"}}]}}
Use an empty list when every value is supported."""


def review_values(cfg, ledger, step, env_adapter, messages, on_call=None) -> str:
    """Droid's validators come from a different model family than the implementer; oh-my-pi's reviewer
    "challenges premature done". One cheap call on submit: a published fact with no evidence in the
    transcript sends the worker back (the 1025 runs passed a wrong seed and a wrong key on format-only
    checks). Returns the problem text, or "" when supported, when nothing was published, or on any error."""
    import json

    from .llm import chat
    from .verifier import read_outputs

    try:
        outputs, _ = asyncio.run_coroutine_threadsafe(read_outputs(env_adapter.environment, step),
                                                      env_adapter.loop).result()
        if not outputs:
            return ""
        prompt = REVIEW_PROMPT.format(goal=step.goal[:1500], digest=attempt_digest(messages, 6000),
                                      facts="\n".join(f"- {k}={v[:300]}" for k, v in outputs.items()))
        reply = chat(cfg, ledger, cfg.reviewer_model, [{"role": "user", "content": prompt}],
                     on_call=(lambda info: on_call({**info, "purpose": "review"})) if on_call else None)
        m = re.search(r"\{.*\}", reply or "", re.S)
        bad = [b for b in (json.loads(m.group(0)).get("unsupported") or []) if isinstance(b, dict)] if m else []
    except Exception:
        return ""
    if not bad:
        return ""
    return ("a reviewer found no evidence in your command outputs for: "
            + "; ".join(f"{b.get('name')} ({str(b.get('why', ''))[:200]})" for b in bad[:5])
            + ". Show the evidence (print or recompute it), or correct the value.")
