"""Orchestrator: plan -> route -> work -> verify -> escalate, with every decision traced.

All model-facing parts are injected so the whole flow runs under tests with fakes:
  ask_planner(messages) -> str
  work(step, model_id, blackboard_text, output_path) -> WorkerResult   (blocking; run in a thread)
  router.route(step_id, text) -> RouteDecision
"""
import asyncio
from pathlib import Path

from .blackboard import Blackboard
from .planner import load_pinned_plan, make_plan, topo_order
from .scheduler import run_dag
from .verifier import Verdict, read_outputs, steps_dir, verify


REPAIR_STEP_LIMIT = 4  # model calls for the missing-outputs repair turn: write the file, submit


class Orchestrator:
    def __init__(self, cfg, ledger, trace, router, ask_planner, work, logs_dir: Path | None = None):
        self.cfg, self.ledger, self.trace, self.router = cfg, ledger, trace, router
        self.ask_planner, self.work = ask_planner, work
        self.logs_dir = logs_dir
        self.on_progress = lambda: None  # agent.py refreshes the Harbor context here

    async def run(self, instruction: str, environment) -> dict:
        if self.cfg.plan_from:  # same DAG as an earlier run: only the routing policy differs
            plan = load_pinned_plan(self.cfg.plan_from, self.cfg.max_plan_steps)
            plan_info = {"attempts": 0, "fallback": False, "errors": [], "pinned_from": str(self.cfg.plan_from)}
        else:
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
        handoff = ""
        try:
            await environment.exec(f"mkdir -p {steps_dir(step.id)}", timeout_sec=30)
        except Exception:
            pass  # the worker can still create it; read_outputs copes with a missing file
        for attempt in range(2):  # first try + one escalated retry
            model_id = self.cfg.tiers[tier]
            bb.update(step.id, status="running", tier=tier, model=model_id, attempts=attempt + 1)
            out_path = (self.logs_dir / f"worker_{step.id}_a{attempt + 1}.json") if self.logs_dir else None
            team = bb.summary_for(step.id, step.depends_on)
            result = await asyncio.to_thread(self.work, step, model_id, team + handoff, out_path)
            outputs, missing = await read_outputs(environment, step)
            if missing and result.exit_status == "Submitted":
                result, outputs, missing = await self._repair_outputs(
                    step, model_id, team, environment, result, missing, out_path)
            verdict = await verify(environment, step, result.exit_status, self.cfg.check_timeout)
            if missing:  # promised results that later steps rely on are part of passing
                verdict = Verdict(False, f"missing declared outputs: {', '.join(missing)}\n{verdict.output}")
            self.trace.log("attempt", step=step.id, attempt=attempt + 1, tier=tier, model=model_id,
                           exit_status=result.exit_status, n_calls=result.n_calls, passed=verdict.passed,
                           check=step.check, check_output=verdict.output[-500:], summary=result.summary[:300],
                           outputs={k: v[:200] for k, v in outputs.items()}, missing_outputs=missing)
            record["attempts"].append({"tier": tier, "model": model_id, "exit_status": result.exit_status,
                                       "passed": verdict.passed, "n_calls": result.n_calls})
            bb.update(step.id, summary=result.summary, check_output=verdict.output[-500:], outputs=outputs)
            self.on_progress()
            if verdict.passed:
                record["passed"] = True
                break
            if attempt == 0 and tier < len(self.cfg.tiers) - 1:
                record["escalations"] += 1
                self.trace.log("escalate", step=step.id, from_tier=tier, to_tier=tier + 1, router_error=True)
                handoff = self._handoff(out_path, model_id, result.exit_status, verdict.output)
                if handoff:
                    self.trace.log("handoff", step=step.id, from_model=model_id, to_model=self.cfg.tiers[tier + 1],
                                   chars=len(handoff))
                tier += 1
            else:
                break
        bb.update(step.id, status="done" if record["passed"] else "failed")
        return record

    async def _repair_outputs(self, step, model_id, team, environment, result, missing, out_path):
        """The worker submitted without publishing what the plan promised: one short turn on the same
        model to write them, so a format slip is not counted as a routing error."""
        self.trace.log("outputs_repair", step=step.id, model=model_id, missing=missing)
        fix = step.model_copy(update={"worker_step_limit": REPAIR_STEP_LIMIT, "goal": (
            f"Your step is already done ({step.goal[:600]}). It has NOT yet published these results: "
            f"{', '.join(missing)}. Write them now as described below (find the values in your earlier work, "
            f"the files you wrote and {steps_dir(step.id)}/), then submit. Do not redo the step.")})
        path = out_path.with_name(out_path.stem + "_repair.json") if out_path else None
        fixed = await asyncio.to_thread(self.work, fix, model_id, team, path)
        outputs, still = await read_outputs(environment, step)
        self.trace.log("outputs_repaired", step=step.id, model=model_id, missing=still, n_calls=fixed.n_calls)
        merged = type(result)(result.exit_status, result.summary, result.n_calls + fixed.n_calls)
        return merged, outputs, still

    @staticmethod
    def _handoff(out_path: Path | None, model_id: str, exit_status: str, check_output: str) -> str:
        """The failed attempt's findings, so the escalated attempt continues instead of starting over."""
        import json

        from .worker import attempt_digest

        try:
            messages = json.loads(out_path.read_text()).get("messages", []) if out_path else []
        except (OSError, ValueError):
            messages = []
        digest = attempt_digest(messages)
        if not digest:
            return ""
        check = f"\nThe step's check said: {check_output.strip()[-600:]}" if check_output.strip() else ""
        return (f"\n\nA previous attempt at YOUR step by {model_id} did not pass (ended: {exit_status}).{check}\n"
                f"Build on what it found instead of starting over; verify anything you rely on.\n{digest}")
