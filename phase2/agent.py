"""Harbor entry point.

  harbor run -d terminal-bench/terminal-bench@4.0.0 -a phase2.agent:RoutedHarness \
      -i '<task glob>' -n 1 -o jobs --ak config=configs/phase2.json --ak policy=cascade

`policy` (and any other config key) can be overridden with --ak; baselines are
`policy=fixed:3` (always Opus), `policy=fixed:0` (always cheapest) and `policy=random`.
"""
import asyncio
import json
from pathlib import Path

from harbor.agents.base import BaseAgent

from . import __version__
from .cli_models import report_failures
from .config import load_config
from .delegate import make_helper_runner
from .ledger import Ledger
from .llm import chat
from .orchestrator import Orchestrator
from .router import Router
from .trace import Trace
from .verifier import read_outputs, steps_dir
from .worker import HarborEnvAdapter, attempt_digest, clip_output, run_worker

CONFIG_KEYS = ("policy", "planner_model", "confidence_threshold", "base_url", "max_parallel",
               "max_plan_steps", "worker_step_limit", "cli_timeout_sec", "step_wall_time_sec", "step_idle_sec",
               "max_observation_chars", "plan_from",
               "fixer_model", "max_interventions", "context_budget_chars", "max_output_tokens",
               "submit_checks", "session_budget_chars",
               "merge_chains", "max_escalations", "max_run_cost_usd", "max_run_credits", "top_tier_budget_frac",
               "reviewer_model", "team_roster", "recency_reminder", "output_guard", "env_bootstrap",
               "acceptance_model", "acceptance_step_limit", "best_of_models",
               "planner_explore", "lead_reviews", "laya_head", "planner_top_tier",
               "d1_python", "d1_model", "d1_revision", "single_step_tier", "planner_role",
               "helper_policy", "helper_step_limit", "max_delegations", "max_parallel_helpers",
               "senior_reviewer_model", "senior_review_rounds", "senior_review_explore")


class RoutedHarness(BaseAgent):
    def __init__(self, logs_dir: Path, model_name: str | None = None, logger=None, *args,
                 config: str | None = None, **kwargs):
        overrides = {k: kwargs.pop(k) for k in CONFIG_KEYS if k in kwargs}
        super().__init__(logs_dir, model_name, logger, *args, **kwargs)
        self.cfg = load_config(config, **overrides)

    @staticmethod
    def name() -> str:
        return "routed-harness"

    def version(self) -> str | None:
        return __version__

    async def setup(self, environment) -> None:
        return None

    async def run(self, instruction: str, environment, context) -> None:
        logs = Path(self.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(self.cfg.prices_per_million, credits_usd=self.cfg.credits_usd)
        trace = Trace(logs / "trace.jsonl")
        trace.log("config", **self.cfg.to_dict())
        loop = asyncio.get_running_loop()
        adapter = HarborEnvAdapter(environment, loop, self.cfg.worker_command_timeout,
                                   max_output_chars=self.cfg.max_observation_chars)

        def ask_planner(messages):
            with report_failures(lambda info: trace.log("llm_call_failed", role="planner", **info)):
                return chat(self.cfg, ledger, self.cfg.planner_model, messages,
                            on_call=lambda info: trace.log("llm_call", role="planner", **info))

        helper_route = [None]

        def route_helper(task):  # a sub-task the lead did not assign: the local router picks a helper
            if helper_route[0] is None:
                import dataclasses

                pool = list(self.cfg.helper_models.values())
                helper_route[0] = Router(dataclasses.replace(self.cfg, policy=self.cfg.helper_policy,
                                                             tiers=pool + pool[-1:]))
            return helper_route[0].route("helper", task).tier

        def run_helper(lead_step, model_id, step, team):
            out_path = Path(logs) / f"worker_{step.id}.json"
            asyncio.run_coroutine_threadsafe(environment.exec(f"mkdir -p {steps_dir(step.id)}", timeout_sec=30),
                                             loop).result()
            with report_failures(lambda info: trace.log("llm_call_failed", role="helper", step=step.id, **info)):
                result = run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=step,
                                    blackboard_text=team, output_path=out_path,
                                    on_call=lambda info: trace.log("llm_call", role="helper", step=step.id,
                                                                   lead=lead_step.id, **info))
            outputs, _ = asyncio.run_coroutine_threadsafe(read_outputs(environment, step), loop).result()
            try:
                digest = attempt_digest(json.loads(out_path.read_text()).get("messages", []), 1500)
            except (OSError, ValueError):
                digest = ""
            return result, outputs, digest

        def senior(step, report, messages):
            """The senior reviewer's verdict on a submitted step: problem text for the worker, or ""."""
            import shlex

            from .review import DIFF_SCRIPT, problems_text, senior_review

            diff = ""
            if orch.snapshot:
                script = DIFF_SCRIPT.format(snap=shlex.quote(orch.snapshot), work=shlex.quote(orch.workdir))
                try:
                    r = asyncio.run_coroutine_threadsafe(environment.exec(script, timeout_sec=60), loop).result()
                    diff = clip_output(r.stdout or "", 30000)
                except Exception as e:
                    diff = f"(could not compute the diff: {e})"
            model = self.cfg.senior_reviewer_model

            def ask(msgs):
                with report_failures(lambda info: trace.log("llm_call_failed", role="reviewer", step=step.id, **info)):
                    return chat(self.cfg, ledger, model, msgs,
                                on_call=lambda info: trace.log("llm_call", role="reviewer", step=step.id, **info))

            def explore(cmd):
                return asyncio.run_coroutine_threadsafe(orch._explore(environment, cmd), loop).result()

            try:
                verdict, probs, n = senior_review(ask, instruction, report, attempt_digest(messages, 6000), diff,
                                                  explore, self.cfg.senior_review_explore)
            except Exception as e:  # a failed review never blocks the work
                trace.log("senior_review", step=step.id, model=model, error=f"{type(e).__name__}: {e}"[:300])
                return ""
            trace.log("senior_review", step=step.id, model=model, verdict=verdict, problems=probs, explored=n,
                      diff_chars=len(diff))
            return problems_text(probs, model.split("/")[-1]) if probs else ""

        def work(step, model_id, blackboard_text, output_path):
            helpers = None
            if self.cfg.helper_models:
                helpers = make_helper_runner(self.cfg, ledger, adapter, trace, step, model_id, instruction,
                                             lambda m, s, t: run_helper(step, m, s, t), route=route_helper)
            with report_failures(lambda info: trace.log("llm_call_failed", role="worker", step=step.id, **info)):
                return run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=step,
                                  blackboard_text=blackboard_text, output_path=output_path,
                                  on_call=lambda info: trace.log("llm_call", role="worker", step=step.id, **info),
                                  run_helpers=helpers, senior=senior if self.cfg.senior_reviewer_model else None)

        def ask_fixer(messages):
            with report_failures(lambda info: trace.log("llm_call_failed", role="fixer", **info)):
                return chat(self.cfg, ledger, self.cfg.fixer_model, messages,
                            on_call=lambda info: trace.log("llm_call", role="fixer", **info))

        orch = Orchestrator(self.cfg, ledger, trace, Router(self.cfg), ask_planner, work, logs,
                            ask_fixer=ask_fixer if self.cfg.fixer_model else None)

        def refresh(summary=None):
            ledger.fill_context(context)
            context.metadata = {**(context.metadata or {}), "policy": self.cfg.policy,
                                **({"summary": summary} if summary else {})}

        orch.on_progress = refresh
        try:
            summary = await orch.run(instruction, environment)
            refresh(summary)
            (logs / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        finally:
            ledger.fill_context(context)
