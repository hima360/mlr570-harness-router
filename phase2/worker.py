"""Worker: mini-swe-agent runs one step inside the Harbor task container.

mini-swe-agent is synchronous, Harbor's environment is async: the worker runs in a thread and
each shell command is sent back to the event loop with run_coroutine_threadsafe.
"""
import asyncio
import os
from dataclasses import dataclass

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

INSTANCE_TEMPLATE = """You are one agent in a team working on this overall task:

{{task_overall}}

Your step ({{step_id}}): {{step_goal}}

Other steps and what they reported:
{{blackboard}}

Do only your step. Other agents may work on other files at the same time, so only edit files
your step needs{{writes_hint}}.

Every response must contain exactly one bash command. Each command runs in a fresh shell in the
task container, so prefix with `cd /path && ...` when needed.{{format_hint}}
When your step is done, run exactly:
printf '""" + SUBMIT + """\\n<one or two sentences on what you did and which files changed>\\n'
"""


@dataclass
class WorkerResult:
    exit_status: str  # Submitted | LimitsExceeded | error type
    summary: str
    n_calls: int


def clip_output(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    half = limit // 2
    return (f"{text[:half]}\n\n[... {len(text) - 2 * half} characters elided; print a narrower slice "
            f"(head, tail, grep, sed -n) to see more ...]\n\n{text[-half:]}")


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

        command = action.get("command", "")
        fut = asyncio.run_coroutine_threadsafe(
            self.environment.exec(command, cwd=cwd or None, timeout_sec=timeout or self.timeout), self.loop)
        try:
            r = fut.result()
            text = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            out = {"output": text, "returncode": r.return_code, "exception_info": ""}
        except Exception as e:  # timeouts and transport errors are shown to the model
            out = {"output": "", "returncode": -1,
                   "exception_info": f"An error occurred while executing the command: {e}"}
        lines = out["output"].lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == SUBMIT and out["returncode"] == 0:
            summary = "".join(lines[1:]).strip()
            raise Submitted({"role": "exit", "content": summary,
                             "extra": {"exit_status": "Submitted", "submission": summary}})
        out["output"] = clip_output(out["output"], self.max_output_chars)
        return out

    def get_template_vars(self, **kwargs) -> dict:
        return {"system": "Linux", "release": "", "version": "", "machine": "", **kwargs}

    def serialize(self) -> dict:
        return {"info": {"config": {"environment_type": "phase2.worker.HarborEnvAdapter"}}}


TEXT_FORMAT_HINT = """
Write a short THOUGHT, then put the command in a block exactly like this:

```mswea_bash_command
your_command_here
```"""


def _make_model(cfg, ledger, model_id: str, on_call=None):
    import time

    from minisweagent.models.litellm_model import LitellmModel

    from .cli_models import complete_fn_for, make_text_model
    from .llm import litellm_name, record_call

    def on_result(usage, text, ms):
        info = record_call(ledger, model_id, usage, ms, text)
        if on_call:
            on_call(info)

    if complete_fn_for(model_id) is not None:
        return make_text_model(model_id, on_result, complete_fn=complete_fn_for(model_id, cfg))

    class LedgerModel(LitellmModel):
        def query(self, messages, **kwargs):
            t0 = time.perf_counter()
            message = super().query(messages, **kwargs)
            usage = (message.get("extra", {}).get("response") or {}).get("usage")
            on_result(usage, message.get("content"), (time.perf_counter() - t0) * 1e3)
            return message

    return LedgerModel(model_name=litellm_name(model_id), cost_tracking="ignore_errors",
                       model_kwargs={"api_base": cfg.base_url, "api_key": cfg.api_key})


def run_worker(cfg, ledger, model_id: str, env_adapter, *, task_overall: str, step, blackboard_text: str,
               model=None, output_path=None, on_call=None) -> WorkerResult:
    """Blocking: call inside asyncio.to_thread. `model` is injectable for tests; `on_call(info)` gets
    one record per model call."""
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    from .cli_models import complete_fn_for

    model = model or _make_model(cfg, ledger, model_id, on_call)
    writes_hint = f" (planned: {', '.join(step.writes)})" if step.writes else ""
    text_based = isinstance(model, LitellmTextbasedModel) or complete_fn_for(model_id) is not None
    format_hint = TEXT_FORMAT_HINT if text_based else ""
    agent = DefaultAgent(
        model, env_adapter,
        system_template="You are a careful software engineer operating a Linux shell.",
        instance_template=INSTANCE_TEMPLATE,
        step_limit=cfg.worker_step_limit, cost_limit=0, output_path=output_path,
        wall_time_limit_seconds=cfg.step_wall_time_sec,
    )
    try:
        info = agent.run(task_overall=task_overall, step_id=step.id, step_goal=step.goal,
                         blackboard=blackboard_text, writes_hint=writes_hint,
                         format_hint=format_hint) or {}
    except Exception as e:
        return WorkerResult(type(e).__name__, str(e)[:500], agent.n_calls)
    return WorkerResult(info.get("exit_status", "unknown"), (info.get("submission") or "").strip(),
                        agent.n_calls)
