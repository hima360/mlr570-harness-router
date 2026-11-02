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
               "senior_reviewer_model", "senior_review_rounds", "senior_review_explore", "senior_verify",
               "senior_fix_model", "senior_fix_step_limit", "measure_targets", "clock",
               "wrap_up_frac", "strategy_model", "checkpoint_frac", "takeover_model", "takeover_first_step_limit",
               "takeover_first_wall_sec", "takeover_step_limit", "takeover_fix_first_max_problems",
               "takeover_fix_step_limit", "spec_model", "repo_map", "handoff")


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

        from .strategy import REVIEW_FOCUS

        reviews = {}  # step id -> the reviewer's conversation, its problems, what is still open

        def current_diff(limit):
            import shlex

            from .review import DIFF_SCRIPT

            if not orch.snapshot:
                return ""
            script = DIFF_SCRIPT.format(snap=shlex.quote(orch.snapshot), work=shlex.quote(orch.workdir))
            try:
                r = asyncio.run_coroutine_threadsafe(environment.exec(script, timeout_sec=60), loop).result()
                return clip_output(r.stdout or "", limit)
            except Exception as e:
                return f"(could not compute the diff: {e})"

        def senior(step, report, messages):
            """The senior reviewer on a submitted step: the first submit gets a full review (problem text sent back
            to the worker), the next one a second look at only those problems (recorded; nothing sent back). One
            cached CLI session per step, so the second look and the explore turns re-send nothing."""
            from .cli_models import CliSession, complete_fn_for
            from .review import problems_text, senior_review, senior_verify

            model = self.cfg.senior_reviewer_model
            st = reviews.setdefault(step.id, {"session": CliSession() if complete_fn_for(model) else None,
                                              "transcript": [], "problems": [], "open": [], "mark": 0})

            def ask(msgs):
                with report_failures(lambda info: trace.log("llm_call_failed", role="reviewer", step=step.id, **info)):
                    return chat(self.cfg, ledger, model, msgs, session=st["session"],
                                on_call=lambda info: trace.log("llm_call", role="reviewer", step=step.id, **info))

            def explore(cmd):
                return asyncio.run_coroutine_threadsafe(orch._explore(environment, cmd), loop).result()

            try:
                if not st["problems"]:  # first submit: the full review
                    diff = current_diff(20000)
                    verdict, probs, n = senior_review(ask, instruction, report, attempt_digest(messages, 5000),
                                                      diff, explore, self.cfg.senior_review_explore,
                                                      transcript=st["transcript"],
                                                      evidence=self.cfg.measure_targets or bool(self.cfg.strategy_model),
                                                      focus=REVIEW_FOCUS.get(strategies.get(step.id, {}).get("mode"), ""))
                    st["problems"], st["mark"] = probs, len(messages)
                    trace.log("senior_review", step=step.id, model=model, verdict=verdict, problems=probs,
                              explored=n, round=1, diff_chars=len(diff))
                    return problems_text(probs, model.split("/")[-1]) if probs else ""
                if self.cfg.senior_verify:  # after the fix: which of those problems are still open
                    still, n = senior_verify(ask, st["transcript"], st["problems"], report,
                                             attempt_digest(messages[st["mark"]:], 4000), current_diff(12000),
                                             explore, self.cfg.senior_review_explore)
                    st["open"] = still
                    trace.log("senior_review", step=step.id, model=model, verdict="open" if still else "fixed",
                              problems=still, explored=n, round=2)
            except Exception as e:  # a failed review never blocks the work
                trace.log("senior_review", step=step.id, model=model, error=f"{type(e).__name__}: {e}"[:300])
            return ""

        def senior_fix(step, result, output_path):
            """Problems the reviewer still finds open after the worker's fix go to the fix model (default: the
            reviewer's model), as one bounded attempt on exactly those problems. 2026-10-29: in all 4 failed v2
            runs the review had named the bug behind the failing test, and the worker's own fix fell short."""
            from .review import FIX_GOAL
            from .worker import WorkerResult

            st = reviews.get(step.id) or {}
            if not st.get("open") or orch.over_budget():
                return result
            model = self.cfg.senior_fix_model or self.cfg.senior_reviewer_model
            listed = "\n".join(f"{i}. {p['what']}" + (f"\n   fix: {p['fix']}" if p.get("fix") else "")
                               for i, p in enumerate(st["open"], 1))
            fix = step.model_copy(update={"goal": FIX_GOAL.format(problems=listed)})
            fix.worker_step_limit = self.cfg.senior_fix_step_limit
            try:
                notes = attempt_digest(json.loads(Path(output_path).read_text()).get("messages", []), 4000)
            except (OSError, ValueError, TypeError):
                notes = ""
            trace.log("senior_fix", step=step.id, model=model, problems=st["open"], phase="start")
            path = Path(output_path).with_name(Path(output_path).stem + "_fix.json") if output_path else None
            with report_failures(lambda info: trace.log("llm_call_failed", role="fixer", step=step.id, **info)):
                fixed = run_worker(self.cfg, ledger, model, adapter, task_overall=instruction, step=fix,
                                   blackboard_text="Notes of the engineer before you:\n" + (notes or "(none)"),
                                   output_path=path, on_call=lambda info: trace.log("llm_call", role="fixer",
                                                                                    step=step.id, **info))
            trace.log("senior_fix", step=step.id, model=model, phase="end", exit_status=fixed.exit_status,
                      n_calls=fixed.n_calls, summary=fixed.summary[:300])
            ok = fixed.exit_status == "Submitted"
            return WorkerResult(result.exit_status if ok else fixed.exit_status,
                                (result.summary + " | senior fix: " + fixed.summary).strip(" |"),
                                result.n_calls + fixed.n_calls, result.session)

        strategies = {}  # step id -> the work mode and brief (phase2.strategy)

        def plan_work(step):
            """v2.7: one call picks how to work on this step; the worker reads the mode's playbook and the brief."""
            from .strategy import choose_strategy, strategy_text

            if step.id not in strategies:
                model = self.cfg.strategy_model

                def ask(msgs):
                    with report_failures(lambda info: trace.log("llm_call_failed", role="strategist", step=step.id,
                                                                **info)):
                        return chat(self.cfg, ledger, model, msgs,
                                    on_call=lambda info: trace.log("llm_call", role="strategist", step=step.id, **info))

                strategies[step.id] = choose_strategy(ask, step.goal if len(step.goal) > len(instruction) // 2
                                                      else f"{instruction}\n\nYOUR PART: {step.goal}")
                trace.log("strategy", step=step.id, model=model, **strategies[step.id])
            limit = step.worker_step_limit or self.cfg.worker_step_limit
            minutes = (step.wall_time_sec or self.cfg.step_wall_time_sec) // 60
            return step.model_copy(update={"goal": step.goal + strategy_text(strategies[step.id], limit, minutes)})

        def takeover(step, model_id, blackboard_text, output_path):
            """v2.8: the builder works alone first (no in-loop review: it broke work GLM had right), the senior
            reviewer judges the result once, and on "fix" the takeover model continues in the same container."""
            from .cli_models import CliSession, complete_fn_for
            from .review import TAKEOVER_GOAL, senior_review
            from .worker import WorkerResult

            from .review import HANDOFF_NOTE, SPEC_PROMPT

            spec = ""
            if self.cfg.spec_model:  # v3.1: one checklist every role works from
                try:
                    with report_failures(lambda info: trace.log("llm_call_failed", role="spec", step=step.id, **info)):
                        spec = chat(self.cfg, ledger, self.cfg.spec_model,
                                    [{"role": "user", "content": SPEC_PROMPT.format(task=instruction[:8000])}],
                                    on_call=lambda info: trace.log("llm_call", role="spec", step=step.id, **info))
                    spec = (spec or "").strip()[:3000]
                except Exception as e:
                    spec = ""
                    trace.log("spec", step=step.id, error=f"{type(e).__name__}: {e}"[:300])
                trace.log("spec", step=step.id, model=self.cfg.spec_model, text=spec)
            card = (f"\n\nSPEC CARD (the harness's checklist of this task; the task text wins if they disagree):\n{spec}"
                    if spec else "")
            first_goal = step.goal + card + (HANDOFF_NOTE if self.cfg.handoff else "")
            first_step = step.model_copy(update={"goal": first_goal,
                                                 "worker_step_limit": self.cfg.takeover_first_step_limit,
                                                 "wall_time_sec": self.cfg.takeover_first_wall_sec})
            with report_failures(lambda info: trace.log("llm_call_failed", role="worker", step=step.id, **info)):
                first = run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=first_step,
                                   blackboard_text=blackboard_text, output_path=output_path,
                                   on_call=lambda info: trace.log("llm_call", role="worker", step=step.id, **info))
            try:
                messages = json.loads(Path(output_path).read_text()).get("messages", []) if output_path else []
            except (OSError, ValueError):
                messages = []
            judge = self.cfg.senior_reviewer_model
            session = CliSession() if complete_fn_for(judge) else None

            def ask(msgs):
                with report_failures(lambda info: trace.log("llm_call_failed", role="reviewer", step=step.id, **info)):
                    return chat(self.cfg, ledger, judge, msgs, session=session,
                                on_call=lambda info: trace.log("llm_call", role="reviewer", step=step.id, **info))

            def explore(cmd):
                return asyncio.run_coroutine_threadsafe(orch._explore(environment, cmd), loop).result()

            def judge_now(result, msgs, rnd):
                try:
                    verdict, probs, n = senior_review(ask, instruction + card, result.summary,
                                                      attempt_digest(msgs, 5000),
                                                      current_diff(20000), explore, self.cfg.senior_review_explore,
                                                      evidence=True)
                except Exception as e:  # no verdict: the stronger model takes over rather than ship unchecked work
                    verdict, probs, n = "fix", [], 0
                    trace.log("senior_review", step=step.id, model=judge, error=f"{type(e).__name__}: {e}"[:300])
                trace.log("senior_review", step=step.id, model=judge, verdict=verdict, problems=probs, explored=n,
                          round=rnd, builder_exit=result.exit_status)
                return verdict, probs

            verdict, probs = judge_now(first, messages, 1)
            if verdict == "ship" and first.exit_status == "Submitted":
                return first
            if (first.exit_status == "Submitted" and probs
                    and len(probs) <= self.cfg.takeover_fix_first_max_problems):
                from .review import FIX_GOAL

                listed = "\n".join(f"{i}. {p['what']}" + (f"\n   fix: {p['fix']}" if p.get("fix") else "")
                                    for i, p in enumerate(probs, 1))
                fix = step.model_copy(update={"goal": FIX_GOAL.format(problems=listed),
                                              "worker_step_limit": self.cfg.takeover_fix_step_limit})
                fpath = Path(output_path).with_name(Path(output_path).stem + "_fix.json") if output_path else None
                trace.log("builder_fix", step=step.id, model=model_id, problems=probs, phase="start")
                with report_failures(lambda info: trace.log("llm_call_failed", role="worker", step=step.id, **info)):
                    fixed = run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=fix,
                                       blackboard_text="Your notes so far:\n" + (attempt_digest(messages, 4000)
                                                                                  or "(none)"),
                                       output_path=fpath,
                                       on_call=lambda info: trace.log("llm_call", role="worker", step=step.id,
                                                                      **info))
                try:
                    fmsgs = json.loads(Path(fpath).read_text()).get("messages", []) if fpath else []
                except (OSError, ValueError):
                    fmsgs = []
                trace.log("builder_fix", step=step.id, model=model_id, phase="end", exit_status=fixed.exit_status,
                          n_calls=fixed.n_calls)
                first = WorkerResult(fixed.exit_status, (first.summary + " | fix: " + fixed.summary).strip(" |"),
                                     first.n_calls + fixed.n_calls, fixed.session)
                messages = messages + fmsgs
                verdict, probs = judge_now(first, fmsgs or messages, 2)
                if verdict == "ship" and first.exit_status == "Submitted":
                    return first
            listed = "\n".join(f"{i}. {p['what']}" + (f"\n   fix: {p['fix']}" if p.get("fix") else "")
                                for i, p in enumerate(probs, 1)) or "(the review gave no details; check everything)"
            nxt = step.model_copy(update={"goal": TAKEOVER_GOAL.format(goal=step.goal + card, problems=listed),
                                          "worker_step_limit": self.cfg.takeover_step_limit})
            notes = attempt_digest(messages, 4000) or "(none)"
            if self.cfg.handoff:  # v3.1: the builder's own handoff note, plus a short digest
                try:
                    r = asyncio.run_coroutine_threadsafe(environment.exec("cat /tmp/HANDOFF.md 2>/dev/null | head -c 4000",
                                                                          timeout_sec=30), loop).result()
                    note = (r.stdout or "").strip()
                except Exception:
                    note = ""
                trace.log("handoff", step=step.id, chars=len(note))
                if note:
                    notes = f"HANDOFF.md:\n{note}\n\nLast commands:\n{attempt_digest(messages, 1500) or '(none)'}"
            path = Path(output_path).with_name(Path(output_path).stem + "_takeover.json") if output_path else None
            trace.log("takeover", step=step.id, model=self.cfg.takeover_model, problems=probs, phase="start")
            with report_failures(lambda info: trace.log("llm_call_failed", role="takeover", step=step.id, **info)):
                second = run_worker(self.cfg, ledger, self.cfg.takeover_model, adapter, task_overall=instruction,
                                    step=nxt, blackboard_text="Notes of the engineer before you:\n" + notes,
                                    output_path=path,
                                    on_call=lambda info: trace.log("llm_call", role="takeover", step=step.id, **info))
            trace.log("takeover", step=step.id, model=self.cfg.takeover_model, phase="end",
                      exit_status=second.exit_status, n_calls=second.n_calls, summary=second.summary[:300])
            return WorkerResult(second.exit_status, (first.summary + " | takeover: " + second.summary).strip(" |"),
                                first.n_calls + second.n_calls, second.session)

        def work(step, model_id, blackboard_text, output_path):
            if self.cfg.takeover_model and model_id != self.cfg.takeover_model:
                return takeover(step, model_id, blackboard_text, output_path)
            if self.cfg.strategy_model:
                step = plan_work(step)
            helpers = None
            if self.cfg.helper_models:
                helpers = make_helper_runner(self.cfg, ledger, adapter, trace, step, model_id, instruction,
                                             lambda m, s, t: run_helper(step, m, s, t), route=route_helper)
            reviewed = bool(self.cfg.senior_reviewer_model) and model_id != self.cfg.senior_reviewer_model
            with report_failures(lambda info: trace.log("llm_call_failed", role="worker", step=step.id, **info)):
                result = run_worker(self.cfg, ledger, model_id, adapter, task_overall=instruction, step=step,
                                    blackboard_text=blackboard_text, output_path=output_path,
                                    on_call=lambda info: trace.log("llm_call", role="worker", step=step.id, **info),
                                    run_helpers=helpers, senior=senior if reviewed else None)
            return senior_fix(step, result, output_path) if reviewed else result

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
