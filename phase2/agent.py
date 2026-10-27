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
from .ledger import Ledger
from .llm import chat
from .orchestrator import Orchestrator
from .router import Router
from .trace import Trace
from .worker import HarborEnvAdapter, run_worker

CONFIG_KEYS = ("policy", "planner_model", "confidence_threshold", "base_url", "max_parallel",
               "max_plan_steps", "worker_step_limit", "cli_timeout_sec", "step_wall_time_sec", "step_idle_sec",
               "max_observation_chars", "plan_from",
               "fixer_model", "max_interventions", "context_budget_chars", "max_output_tokens",
               "submit_checks", "session_budget_chars",
               "merge_chains", "max_escalations", "max_run_cost_usd", "max_run_credits", "top_tier_budget_frac",
               "reviewer_model", "team_roster", "recency_reminder", "output_guard", "env_bootstrap",
               "acceptance_model", "acceptance_step_limit", "best_of_models")


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

        def work(step, model_id, blackboard_text, output_path):
            with report_failures(lambda info: trace.log("llm_call_failed", role="worker", step=step.id, **info)):
                return run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=step,
                                  blackboard_text=blackboard_text, output_path=output_path,
                                  on_call=lambda info: trace.log("llm_call", role="worker", step=step.id, **info))

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
