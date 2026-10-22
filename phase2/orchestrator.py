"""Orchestrator: plan -> route -> work -> verify -> escalate, with every decision traced.

All model-facing parts are injected so the whole flow runs under tests with fakes:
  ask_planner(messages) -> str
  work(step, model_id, blackboard_text, output_path) -> WorkerResult   (blocking; run in a thread)
  router.route(step_id, text) -> RouteDecision
"""
import asyncio
from pathlib import Path

from .blackboard import Blackboard
from .planner import make_plan, topo_order
from .scheduler import run_dag
from .verifier import verify


class Orchestrator:
    def __init__(self, cfg, ledger, trace, router, ask_planner, work, logs_dir: Path | None = None):
        self.cfg, self.ledger, self.trace, self.router = cfg, ledger, trace, router
        self.ask_planner, self.work = ask_planner, work
        self.logs_dir = logs_dir
        self.on_progress = lambda: None  # agent.py refreshes the Harbor context here

    async def run(self, instruction: str, environment) -> dict:
        env_summary = await self._env_summary(environment)
        plan, plan_info = await asyncio.to_thread(
            make_plan, instruction, env_summary, self.ask_planner, self.cfg.max_plan_steps)
        self.trace.log("plan", steps=[s.model_dump() for s in plan.steps], order=topo_order(plan),
                       planner_model=self.cfg.planner_model, **plan_info)
        bb = Blackboard(task=instruction)
        for s in plan.steps:
            bb.add(s.id, s.goal)

        async def run_step(step):
            return await self._run_step(step, instruction, environment, bb)

        results = await run_dag(plan.steps, run_step, self.cfg.max_parallel)
        if self.logs_dir:
            bb.save(self.logs_dir / "blackboard.json")
        summary = {
            "plan": [s.model_dump() for s in plan.steps],
            "plan_info": plan_info,
            "steps": results,
            "n_escalations": sum(r["escalations"] for r in results.values()),
            "n_failed_steps": sum(1 for r in results.values() if not r["passed"]),
            "policy": self.cfg.policy,
        }
        self.trace.log("done", **{k: summary[k] for k in ("n_escalations", "n_failed_steps", "policy")})
        return summary

    async def _env_summary(self, environment) -> str:
        try:
            r = await environment.exec("pwd && ls -la | head -50", timeout_sec=30)
            return (r.stdout or "")[:4000]
        except Exception as e:
            return f"(listing failed: {e})"

    async def _run_step(self, step, instruction, environment, bb) -> dict:
        decision = self.router.route(step.id, step.goal)
        self.trace.log("route", step=step.id, model=self.cfg.tiers[decision.tier], **decision.to_dict())
        tier = decision.tier
        record = {"route": decision.to_dict(), "attempts": [], "escalations": 0, "passed": False}
        for attempt in range(2):  # first try + one escalated retry
            model_id = self.cfg.tiers[tier]
            bb.update(step.id, status="running", tier=tier, model=model_id, attempts=attempt + 1)
            out_path = (self.logs_dir / f"worker_{step.id}_a{attempt + 1}.json") if self.logs_dir else None
            result = await asyncio.to_thread(self.work, step, model_id, bb.summary_for(step.id), out_path)
            verdict = await verify(environment, step, result.exit_status, self.cfg.check_timeout)
            self.trace.log("attempt", step=step.id, attempt=attempt + 1, tier=tier, model=model_id,
                           exit_status=result.exit_status, n_calls=result.n_calls, passed=verdict.passed,
                           check=step.check, check_output=verdict.output[-500:], summary=result.summary[:300])
            record["attempts"].append({"tier": tier, "model": model_id, "exit_status": result.exit_status,
                                       "passed": verdict.passed, "n_calls": result.n_calls})
            bb.update(step.id, summary=result.summary, check_output=verdict.output[-500:])
            self.on_progress()
            if verdict.passed:
                record["passed"] = True
                break
            if attempt == 0 and tier < len(self.cfg.tiers) - 1:
                record["escalations"] += 1
                self.trace.log("escalate", step=step.id, from_tier=tier, to_tier=tier + 1, router_error=True)
                tier += 1
            else:
                break
        bb.update(step.id, status="done" if record["passed"] else "failed")
        return record
